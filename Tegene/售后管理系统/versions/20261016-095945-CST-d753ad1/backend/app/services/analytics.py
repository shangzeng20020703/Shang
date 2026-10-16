"""
HR 分析中心服务 - Analytics Service
=====================================

本模块提供售后管理系统的多维度人才数据分析能力，面向 CHRO 和 HR BP 使用。
所有分析均基于数据库实时计算，无缓存。

核心分析维度（6个）：

1. **人才总览（TalentOverview）**
   - 在职总人数（ACTIVE + ON_PROBATION 状态员工）
   - 平均司龄（入职至今年数，优先数据库计算，兜底 Python 计算）
   - 关键人才人数及占比（来自 talent_review_entries.is_key_talent）
   - 全员平均胜任力匹配率（加权计算，详见 _calc_avg_competency_match）
   - 培训完成率（已完成/全部报名）

2. **胜任力热力图（CompetencyHeatmap）**
   - 按「部门 × 胜任力项」统计员工平均评估等级
   - 对比全局岗位要求均值，计算差距（gap = required - actual）
   - 差距越大表示该部门在该胜任力项上越需要提升

3. **培训 ROI（TrainingROI）**
   - 总投入金额（活跃+已完成培训计划的预算之和）
   - 已完成培训总课时
   - 已完成培训的平均成绩
   - 全部报名的完成率

4. **九宫格分布（NineBoxDistribution）**
   - 基于 talent_review_entries 表的 nine_box_position 字段统计
   - 支持按盘点期（session_id）过滤，或查询全部期次汇总
   - 九个格子：绩效（高/中/低）× 潜力（高/中/低）
   - 表不存在时优雅降级，返回空分布

5. **离职风险看板（FlightRiskDashboard）**
   - 从 talent_review_entries 提取 flight_risk=medium/high 的在职员工
   - 高风险优先显示
   - 根据风险等级推断典型风险因素（规则引擎，非 ML）

6. **继任覆盖率（SuccessionCoverage）**
   - 统计 succession_plans 中已有候选人的关键岗位比例
   - 返回未覆盖岗位列表（按关键度倒序）

内部辅助方法：
    _calc_avg_competency_match() -- 全员加权平均胜任力匹配率计算
    _infer_risk_factors()        -- 基于风险等级推断风险因素

调用关系：
    api/v1/analytics.py → services/analytics.py（本文件）
    → models/employee.py, organization.py, competency.py, training.py
    → 原生 SQL（talent_review_entries, succession_plans 等 talent 模块表）

依赖框架：FastAPI + SQLAlchemy 2.0 Async + PostgreSQL + Pydantic v2
"""

from datetime import date
from typing import Optional

from sqlalchemy import select, func, text, and_, case, cast, DateTime
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee, EmployeeStatus
from app.models.organization import Department
from app.models.competency import (
    CompetencyItem,
    PositionCompetency,
    EmployeeCompetency,
)
from app.models.training import TrainingEnrollment, TrainingCourse, TrainingPlan
from app.schemas.analytics import (
    TalentOverview,
    CompetencyHeatmapRow,
    TrainingROI,
    NineBoxDistribution,
    NineBoxCell,
    FlightRiskItem,
    SuccessionCoverage,
)


class AnalyticsService:
    """
    HR 分析中心 — 提供多维度人才数据分析。

    设计为无状态类（所有方法均为 staticmethod），通过 db 参数获取数据库连接。
    所有方法在数据不足时均有优雅降级处理（返回 0 或空列表），不会抛出 500 错误。
    """

    # ------------------------------------------------------------------
    # 1. 人才总览
    # ------------------------------------------------------------------

    @staticmethod
    async def talent_overview(db: AsyncSession) -> TalentOverview:
        """
        聚合人才总览核心指标（仪表盘首屏数据）。

        计算逻辑：
            total_employees    -- COUNT(ACTIVE + ON_PROBATION 状态员工)
            avg_tenure         -- 优先用 PostgreSQL extract/epoch 计算；
                                  若数据库函数不兼容（如测试环境），回退到 Python 计算
            key_talent_count   -- 查询 talent_review_entries.is_key_talent=true 的去重员工数
                                  （表不存在时优雅降级为 0）
            key_talent_pct     -- key_talent_count / total_employees × 100%
            avg_competency_match_rate -- 调用 _calc_avg_competency_match()
            training_completion_rate  -- completed / total_enrollments × 100%

        参数：
            db -- 异步数据库会话

        返回：
            TalentOverview schema，包含以上所有指标
        """
        active_statuses = [EmployeeStatus.ACTIVE.value, EmployeeStatus.ON_PROBATION.value]

        # 统计在职员工总数（ACTIVE + ON_PROBATION）
        total_result = await db.execute(
            select(func.count(Employee.id))
            .where(Employee.status.in_(active_statuses))
        )
        total_employees = total_result.scalar() or 0

        # 计算平均司龄（年）：用数据库 epoch 计算提高性能
        avg_tenure_raw = None
        try:
            avg_tenure_result = await db.execute(
                select(
                    func.avg(
                        func.extract("epoch", func.now() - cast(Employee.hire_date, DateTime))
                    ) / (365.25 * 86400)  # epoch 秒数转换为年数
                )
                .where(
                    Employee.status.in_(active_statuses),
                    Employee.hire_date.isnot(None),
                )
            )
            avg_tenure_raw = avg_tenure_result.scalar()
        except Exception:
            avg_tenure_raw = None
        # 如果数据库函数不兼容，使用Python计算
        if avg_tenure_raw is None:
            # 回退方案: 查询所有hire_date后在Python侧计算
            hire_result = await db.execute(
                select(Employee.hire_date)
                .where(
                    Employee.status.in_(active_statuses),
                    Employee.hire_date.isnot(None),
                )
            )
            hire_dates = [row.hire_date for row in hire_result.all() if row.hire_date]
            if hire_dates:
                today = date.today()
                total_days = sum((today - hd).days for hd in hire_dates)
                avg_tenure = round(total_days / len(hire_dates) / 365.25, 1)
            else:
                avg_tenure = 0
        else:
            avg_tenure = round(float(avg_tenure_raw), 1)

        # 查询关键人才数（从 talent_review_entries 表，表不存在时优雅降级为 0）
        key_talent_count = 0
        try:
            kt_result = await db.execute(
                text("""
                    SELECT COUNT(DISTINCT employee_id) as cnt
                    FROM talent_review_entries
                    WHERE is_key_talent = true
                """)
            )
            key_talent_count = kt_result.scalar() or 0
        except Exception:
            pass  # talent_review_entries 表不存在时忽略，返回 0

        # 关键人才占比（防零除）
        key_talent_pct = round(
            key_talent_count / total_employees * 100, 1
        ) if total_employees > 0 else 0

        # 计算全员平均胜任力匹配率（内部方法，含完整加权逻辑）
        avg_match = await AnalyticsService._calc_avg_competency_match(db)

        # 统计培训完成率：已完成培训 / 全部培训报名
        training_total_result = await db.execute(
            select(func.count(TrainingEnrollment.id))
        )
        training_total = training_total_result.scalar() or 0

        training_completed_result = await db.execute(
            select(func.count(TrainingEnrollment.id))
            .where(TrainingEnrollment.status == "completed")
        )
        training_completed = training_completed_result.scalar() or 0

        # 计算完成率（防零除）
        training_completion_rate = round(
            training_completed / training_total * 100, 1
        ) if training_total > 0 else 0

        return TalentOverview(
            total_employees=total_employees,
            avg_tenure=avg_tenure,
            key_talent_count=key_talent_count,
            key_talent_pct=key_talent_pct,
            avg_competency_match_rate=avg_match,
            training_completion_rate=training_completion_rate,
        )

    # ------------------------------------------------------------------
    # 2. 胜任力热力图
    # ------------------------------------------------------------------

    @staticmethod
    async def competency_heatmap(db: AsyncSession) -> list[CompetencyHeatmapRow]:
        """
        生成胜任力热力图数据（部门 × 胜任力项 × 平均等级 × 与岗位要求的差距）。

        数据来源：
            - 员工实际评估等级：EmployeeCompetency.assessed_level
            - 岗位要求等级：PositionCompetency.required_level（全局均值作为基准）

        计算逻辑：
            1. 按「部门 × 胜任力项」GROUP BY，计算在职员工平均评估等级
            2. 查询各胜任力项的全局岗位要求均值（作为基准线）
            3. gap = required - avg_level（正值表示不足，负值表示超额）

        参数：
            db -- 异步数据库会话

        返回：
            CompetencyHeatmapRow 列表，按部门名称 + 胜任力项名称排序
            每行包含：department_name, competency_name, avg_level, gap
        """
        # 查询各部门各胜任力项的平均评估等级（仅在职员工）
        result = await db.execute(
            select(
                Department.name.label("dept_name"),
                CompetencyItem.name.label("comp_name"),
                func.avg(EmployeeCompetency.assessed_level).label("avg_level"),
            )
            .join(Employee, EmployeeCompetency.employee_id == Employee.id)
            .join(Department, Employee.department_id == Department.id)
            .join(CompetencyItem, EmployeeCompetency.competency_item_id == CompetencyItem.id)
            .where(Employee.status.in_([EmployeeStatus.ACTIVE.value, EmployeeStatus.ON_PROBATION.value]))
            .group_by(Department.name, CompetencyItem.name)
            .order_by(Department.name, CompetencyItem.name)
        )
        rows = result.all()

        # 查询各胜任力项的全局岗位要求平均值（作为基准，默认 3.0 级）
        req_result = await db.execute(
            select(
                CompetencyItem.name.label("comp_name"),
                func.avg(PositionCompetency.required_level).label("avg_required"),
            )
            .join(CompetencyItem, PositionCompetency.competency_item_id == CompetencyItem.id)
            .group_by(CompetencyItem.name)
        )
        req_map: dict[str, float] = {}
        for rr in req_result.all():
            req_map[rr.comp_name] = float(rr.avg_required)

        # 组装热力图数据：计算每个格子的 avg_level 和 gap
        heatmap = []
        for row in rows:
            avg_level = round(float(row.avg_level), 2)
            required = req_map.get(row.comp_name, 3.0)  # 无配置时默认要求 3.0 级
            gap = round(required - avg_level, 2)         # 正值=能力不足，负值=超额完成
            heatmap.append(CompetencyHeatmapRow(
                department_name=row.dept_name,
                competency_name=row.comp_name,
                avg_level=avg_level,
                gap=gap,
            ))

        return heatmap

    # ------------------------------------------------------------------
    # 3. 培训ROI
    # ------------------------------------------------------------------

    @staticmethod
    async def training_roi(db: AsyncSession) -> TrainingROI:
        """
        计算培训投资回报率相关指标。

        指标说明：
            total_investment     -- 活跃/已完成培训计划的总预算（元）
            total_hours          -- 已完成培训报名对应课程的总课时（小时）
            avg_score_improvement -- 已完成培训的平均分数（参考指标，非前后对比差值）
            completion_rate      -- 全部培训报名的完成率（%）

        参数：
            db -- 异步数据库会话

        返回：
            TrainingROI schema，包含以上 4 个指标
        """
        # 总投入：活跃或已完成的培训计划的预算之和（NULL 按 0 处理）
        budget_result = await db.execute(
            select(func.coalesce(func.sum(TrainingPlan.budget), 0))
            .where(TrainingPlan.status.in_(["active", "completed"]))
        )
        total_investment = float(budget_result.scalar() or 0)

        # 总课时：已完成的培训报名 JOIN 课程，累计课程时长
        hours_result = await db.execute(
            select(func.coalesce(func.sum(TrainingCourse.duration_hours), 0))
            .join(TrainingEnrollment, TrainingEnrollment.course_id == TrainingCourse.id)
            .where(TrainingEnrollment.status == "completed")
        )
        total_hours = float(hours_result.scalar() or 0)

        # 平均成绩：已完成且有分数记录的报名的平均分
        avg_score_result = await db.execute(
            select(func.avg(TrainingEnrollment.score))
            .where(
                TrainingEnrollment.status == "completed",
                TrainingEnrollment.score.isnot(None),  # 排除未评分的记录
            )
        )
        avg_score = float(avg_score_result.scalar() or 0)

        # 完成率分子：已完成的报名数
        total_enrollments_result = await db.execute(
            select(func.count(TrainingEnrollment.id))
        )
        total_enrollments = total_enrollments_result.scalar() or 0

        completed_result = await db.execute(
            select(func.count(TrainingEnrollment.id))
            .where(TrainingEnrollment.status == "completed")
        )
        completed = completed_result.scalar() or 0

        # 完成率（防零除）
        completion_rate = round(
            completed / total_enrollments * 100, 1
        ) if total_enrollments > 0 else 0

        return TrainingROI(
            total_investment=round(total_investment, 2),
            total_hours=round(total_hours, 1),
            avg_score_improvement=round(avg_score, 1),
            completion_rate=completion_rate,
        )

    # ------------------------------------------------------------------
    # 4. 九宫格分布
    # ------------------------------------------------------------------

    @staticmethod
    async def nine_box_distribution(
        db: AsyncSession, session_id: Optional[int] = None
    ) -> NineBoxDistribution:
        """
        查询人才盘点九宫格（9-Box Grid）分布数据。

        九宫格定义（绩效 × 潜力 3×3 矩阵，共 9 格）：
            高绩效-高潜力 | 高绩效-中潜力 | 高绩效-低潜力
            中绩效-高潜力 | 中绩效-中潜力 | 中绩效-低潜力
            低绩效-高潜力 | 低绩效-中潜力 | 低绩效-低潜力

        数据来源：talent_review_entries.nine_box_position 字段

        优雅降级：
            若 talent_review_entries 表不存在（如初始化阶段），
            返回空分布（所有格子 count=0, pct=0）而非抛出 500

        参数：
            db         -- 异步数据库会话
            session_id -- 可选，按人才盘点期次过滤；None 表示查询所有期次的汇总

        返回：
            NineBoxDistribution schema，包含：
            - period -- 期次名称（或"全部盘点"）
            - boxes  -- 9 个格子的 NineBoxCell 列表（position, count, pct）
        """
        # 定义九宫格固定位置标签（顺序与展示矩阵对应）
        nine_box_labels = [
            "高绩效-高潜力", "高绩效-中潜力", "高绩效-低潜力",
            "中绩效-高潜力", "中绩效-中潜力", "中绩效-低潜力",
            "低绩效-高潜力", "低绩效-中潜力", "低绩效-低潜力",
        ]

        try:
            # 构建动态 WHERE 子句（按 session_id 过滤）
            where_clause = ""
            params: dict = {}
            if session_id:
                where_clause = "WHERE session_id = :sid"
                params["sid"] = session_id

            # 查询符合条件的总人数（作为百分比计算的分母）
            total_result = await db.execute(
                text(f"SELECT COUNT(*) FROM talent_review_entries {where_clause}"),
                params,
            )
            total = total_result.scalar() or 0

            # 按九宫格位置分组统计各格人数
            dist_result = await db.execute(
                text(f"""
                    SELECT nine_box_position, COUNT(*) as cnt
                    FROM talent_review_entries
                    {where_clause}
                    GROUP BY nine_box_position
                """),
                params,
            )
            dist_map: dict[str, int] = {}
            for row in dist_result.all():
                if row.nine_box_position:
                    dist_map[row.nine_box_position] = row.cnt

            # 查询期次名称（用于展示标题）
            period = ""
            if session_id:
                try:
                    period_result = await db.execute(
                        text(
                            "SELECT name FROM talent_review_sessions WHERE id = :sid"
                        ),
                        {"sid": session_id},
                    )
                    period = period_result.scalar() or f"盘点#{session_id}"
                except Exception:
                    period = f"盘点#{session_id}"  # 查询失败时使用 ID 作为标题
            else:
                period = "全部盘点"

            # 组装 9 个格子数据（含百分比）
            boxes = []
            for label in nine_box_labels:
                count = dist_map.get(label, 0)  # 无数据的格子显示 0
                pct = round(count / total * 100, 1) if total > 0 else 0
                boxes.append(NineBoxCell(
                    position=label,
                    count=count,
                    pct=pct,
                ))

            return NineBoxDistribution(period=period, boxes=boxes)

        except Exception:
            # 表不存在时返回空分布（优雅降级，不影响其他模块）
            return NineBoxDistribution(
                period="暂无数据",
                boxes=[
                    NineBoxCell(position=label, count=0, pct=0)
                    for label in nine_box_labels
                ],
            )

    # ------------------------------------------------------------------
    # 5. 离职风险看板
    # ------------------------------------------------------------------

    @staticmethod
    async def flight_risk_dashboard(db: AsyncSession) -> list[FlightRiskItem]:
        """
        获取中高离职风险员工名单（用于 HR 主动干预）。

        数据来源：
            talent_review_entries.flight_risk IN ('medium', 'high')
            且员工当前仍在职（status IN ('在职', '试用期')）

        排序规则：高风险（high）优先显示，同等级内按员工 ID 排序

        优雅降级：
            若 talent_review_entries 表不存在，返回空列表

        参数：
            db -- 异步数据库会话

        返回：
            FlightRiskItem 列表，每项包含：
            - employee_id/employee_name/position/department_name
            - risk_level（medium/high）
            - risk_factors（根据风险等级推断的典型因素列表）
        """
        try:
            result = await db.execute(
                text("""
                    SELECT
                        tr.employee_id,
                        e.name as employee_name,
                        e.position,
                        d.name as department_name,
                        tr.flight_risk
                    FROM talent_review_entries tr
                    JOIN employees e ON tr.employee_id = e.id
                    LEFT JOIN departments d ON e.department_id = d.id
                    WHERE tr.flight_risk IN ('medium', 'high')
                      AND e.status IN ('在职', '试用期')
                    ORDER BY
                        CASE tr.flight_risk WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                        tr.employee_id
                """)
            )
            items = []
            for row in result.all():
                # 根据风险等级推断典型风险因素（规则引擎）
                risk_factors = AnalyticsService._infer_risk_factors(row.flight_risk)
                items.append(FlightRiskItem(
                    employee_id=row.employee_id,
                    employee_name=row.employee_name,
                    position=row.position,
                    department_name=row.department_name,
                    risk_level=row.flight_risk,
                    risk_factors=risk_factors,
                ))
            return items
        except Exception:
            return []  # 表不存在或查询失败时返回空列表

    # ------------------------------------------------------------------
    # 6. 继任覆盖率
    # ------------------------------------------------------------------

    @staticmethod
    async def succession_coverage(db: AsyncSession) -> SuccessionCoverage:
        """
        计算关键岗位继任覆盖率（评估人才梯队建设完整度）。

        计算逻辑：
            coverage_rate = 已有候选人的岗位数 / 关键岗位总数 × 100%

        数据来源：
            - succession_plans -- 关键岗位规划表
            - succession_candidates -- 继任候选人表

        优雅降级：
            若相关表不存在，返回空 SuccessionCoverage（所有字段默认值）

        参数：
            db -- 异步数据库会话

        返回：
            SuccessionCoverage schema，包含：
            - total_key_positions  -- 关键岗位总数
            - covered_positions    -- 已有继任候选人的岗位数
            - coverage_rate        -- 覆盖率（%）
            - uncovered_positions  -- 未覆盖关键岗位名称列表（按关键度倒序）
        """
        try:
            # 关键岗位总数
            total_result = await db.execute(
                text("SELECT COUNT(*) FROM succession_plans")
            )
            total_key_positions = total_result.scalar() or 0

            # 已有至少一名候选人的岗位数（DISTINCT 去重）
            covered_result = await db.execute(
                text("""
                    SELECT COUNT(DISTINCT sp.id)
                    FROM succession_plans sp
                    JOIN succession_candidates sc ON sc.plan_id = sp.id
                """)
            )
            covered_positions = covered_result.scalar() or 0

            # 覆盖率（防零除）
            coverage_rate = round(
                covered_positions / total_key_positions * 100, 1
            ) if total_key_positions > 0 else 0

            # 查询尚未配置继任候选人的岗位列表（按关键度 criticality 倒序）
            uncovered_result = await db.execute(
                text("""
                    SELECT sp.key_position
                    FROM succession_plans sp
                    WHERE sp.id NOT IN (
                        SELECT DISTINCT sc.plan_id FROM succession_candidates sc
                    )
                    ORDER BY sp.criticality DESC
                """)
            )
            uncovered_positions = [
                row.key_position for row in uncovered_result.all()
            ]

            return SuccessionCoverage(
                total_key_positions=total_key_positions,
                covered_positions=covered_positions,
                coverage_rate=coverage_rate,
                uncovered_positions=uncovered_positions,
            )

        except Exception:
            return SuccessionCoverage()  # 表不存在时返回默认空对象

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    async def _calc_avg_competency_match(db: AsyncSession) -> float:
        """
        计算全员平均胜任力匹配率（加权计算，内部方法）。

        算法逻辑（三步）：
            Step 1 - 获取各岗位的胜任力要求（PositionCompetency 表）
            Step 2 - 获取每位员工的最新评估等级（EmployeeCompetency 表，取最新一条）
            Step 3 - 对每位员工计算加权匹配率：
                     matched_weight = sum(weight for items where actual >= required)
                     match_rate = matched_weight / total_weight × 100%
                     返回所有员工 match_rate 的算术平均值

        评估等级判断：实际等级 >= 要求等级 即视为达标，该项全额计入加权分子。

        参数：
            db -- 异步数据库会话

        返回：
            全员平均胜任力匹配率（%），0.0 ~ 100.0，保留 1 位小数。
            若无在职员工或无岗位要求配置，返回 0。
        """
        active_statuses = [EmployeeStatus.ACTIVE.value, EmployeeStatus.ON_PROBATION.value]

        # Step 1: 获取有岗位信息的在职员工列表
        emp_result = await db.execute(
            select(Employee.id, Employee.position)
            .where(
                Employee.status.in_(active_statuses),
                Employee.position.isnot(None),  # 无岗位的员工无法匹配
            )
        )
        employees = emp_result.all()

        if not employees:
            return 0

        # Step 2a: 加载所有岗位的胜任力要求（按岗位名称分组）
        pos_req_result = await db.execute(
            select(
                PositionCompetency.position_name,
                PositionCompetency.competency_item_id,
                PositionCompetency.required_level,
                PositionCompetency.weight,
            )
        )
        pos_reqs = pos_req_result.all()

        # 按岗位名称建立索引，格式：{position_name: [PositionCompetency, ...]}
        req_by_position: dict[str, list] = {}
        for pr in pos_reqs:
            req_by_position.setdefault(pr.position_name, []).append(pr)

        # Step 2b: 加载所有员工的历史胜任力评估（按 assessed_at 倒序，优先取最新）
        assess_result = await db.execute(
            select(
                EmployeeCompetency.employee_id,
                EmployeeCompetency.competency_item_id,
                EmployeeCompetency.assessed_level,
            )
            .order_by(EmployeeCompetency.assessed_at.desc())
        )
        assess_rows = assess_result.all()

        # 按（员工ID, 胜任力项ID）建立最新评估等级的映射表
        assessed_map: dict[tuple[int, int], int] = {}
        for row in assess_rows:
            key = (row.employee_id, row.competency_item_id)
            if key not in assessed_map:  # 已存在说明有更新的记录，跳过旧记录
                assessed_map[key] = row.assessed_level

        # Step 3: 为每位员工计算加权胜任力匹配率
        match_rates: list[float] = []
        for emp in employees:
            reqs = req_by_position.get(emp.position, [])
            if not reqs:
                continue  # 该岗位无胜任力要求，跳过
            total_weight = sum(r.weight for r in reqs)
            if total_weight == 0:
                continue  # 所有权重为 0，无法计算，跳过
            matched_weight = 0
            for r in reqs:
                actual = assessed_map.get((emp.id, r.competency_item_id), 0)
                if actual >= r.required_level:  # 达标则计入匹配权重
                    matched_weight += r.weight
            match_rates.append(matched_weight / total_weight * 100)

        if not match_rates:
            return 0
        return round(sum(match_rates) / len(match_rates), 1)

    @staticmethod
    def _infer_risk_factors(flight_risk: str) -> list[str]:
        """
        基于风险等级推断典型离职风险因素（规则引擎，非机器学习）。

        当前规则：
            high（高风险）→ 3 个典型因素（绩效下降 + 继任空缺 + 薪酬竞争力）
            medium（中风险）→ 2 个典型因素（发展空间 + 团队氛围）
            其他 → 空列表

        参数：
            flight_risk -- 风险等级字符串（"high"/"medium"/"low"）

        返回：
            风险因素描述字符串列表（供前端展示）
        """
        factors = []
        if flight_risk == "high":
            factors.extend([
                "近期绩效下降",
                "关键岗位无继任者",
                "市场薪酬竞争力不足",
            ])
        elif flight_risk == "medium":
            factors.extend([
                "发展空间有限",
                "团队氛围待改善",
            ])
        return factors
