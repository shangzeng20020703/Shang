"""
人才画像服务模块 - Talent Profile Service
===========================================

**业务职责**：
    售后管理系统的人才画像核心服务层，负责：
    1. 构建员工完整人才画像（聚合12+张表的多维度数据）
    2. 生成胜任力雷达图数据（实际等级 vs 岗位要求）
    3. 构建员工成长时间线（入职 + 培训 + 绩效 + 证书等关键事件）
    4. 多维度人才搜索（关键词/技能/胜任力等级）
    5. 自动生成人才标签（如"高绩效"、"关键人才"、"新员工"）

**人才画像的10个维度**：
    1. 员工基本信息（姓名/岗位/部门/司龄）
    2. 绩效历史（最近4次考核的等级和得分）
    3. 胜任力雷达（实际评估等级 vs 岗位要求等级，可视化为雷达图）
    4. 培训概况（参训课程数/完成数/平均成绩）
    5. IDP 计划概况（计划数/进行中目标数/完成率）
    6. 证书列表（证书名称/到期日/状态）
    7. 技能列表（技能名/熟练度）
    8. 人才盘点结果（九宫格位置/是否关键人才/离职风险）
    9. 成长时间线（时间轴事件流）
    10. 自动标签（系统基于规则生成的人才标签）

**人才标签生成规则**（_generate_tags）：
    - "高绩效"：最新绩效等级=A
    - "持续高绩效"：近4次中≥2次A
    - "绩效待提升"：最新等级=D
    - "胜任力达标"：所有能力项均≥要求（平均差距≤0）
    - "能力差距较大"：平均差距≥2级
    - "学习积极"：培训完成率≥90%
    - "培训优秀"：平均培训成绩≥90分
    - "关键人才"：人才盘点标记为关键人才
    - "高离职风险"：人才盘点 flight_risk=high
    - "明星员工"：九宫格位置含"star"或"高绩效-高潜力"
    - "证书即将到期"：有证书到期日 <= 今天
    - "资深员工"：司龄≥5年
    - "新员工"：司龄<1年

**人才搜索（search_talents）支持3种维度**：
    1. keyword（关键词）：匹配姓名或岗位（ILIKE 模糊搜索）
    2. skill（技能名）：在 employee_skills 表中模糊匹配
    3. min_competency（最低胜任力均值）：按平均评估等级过滤（HAVING 子句）
    三种维度可组合使用，结果自动去重（seen_ids 集合）。

**容错设计**：
    部分辅助方法（_build_idp_summary、_build_certifications、_build_skills、
    _build_review_summary）使用原生 SQL（text()）查询，并包裹在 try/except 中，
    防止表不存在或字段变更时导致整个画像构建失败。

**技术说明**：
    FastAPI + SQLAlchemy 2.0 Async（asyncpg / PostgreSQL）+ Pydantic v2。
    画像构建是只读操作，不写入任何数据，所有结果直接返回 Pydantic Schema。
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import select, func, text, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.organization import Department
from app.models.performance import PerformanceEvaluation, PerformanceCycle
from app.models.competency import (
    CompetencyItem,
    PositionCompetency,
    EmployeeCompetency,
)
from app.models.training import (
    TrainingCourse,
    TrainingEnrollment,
)
from app.schemas.talent_profile import (
    TalentProfile,
    CompetencyRadarItem,
    TimelineEvent,
    PerformanceSummaryItem,
    TrainingSummary,
    IDPSummary,
    CertificationItem,
    SkillItem,
    ReviewSummary,
    TalentSearchResult,
)


class TalentProfileService:
    """
    人才画像服务 — 聚合多维数据构建完整人才画像。

    该服务是人才发展模块的核心聚合服务，通过并行查询12+张数据库表，
    将员工的绩效、胜任力、培训、IDP、证书、技能、人才盘点等数据
    整合为一个结构化的人才画像对象（TalentProfile）。

    所有方法均为只读查询，不写入任何数据。
    """

    # ------------------------------------------------------------------
    # 主方法: 构建完整人才画像
    # ------------------------------------------------------------------

    @staticmethod
    async def build_profile(
        db: AsyncSession, employee_id: int
    ) -> TalentProfile:
        """
        构建员工完整人才画像，聚合12+张表数据。

        该方法按顺序调用10个子方法，每个子方法负责一个维度的数据收集：
            1. 员工基本信息（姓名、岗位、部门、入职日期、司龄）
            2. 绩效历史（最近4次考核的等级和加权得分）
            3. 胜任力雷达（调用 get_radar_data）
            4. 培训概况（调用 _build_training_summary）
            5. IDP 计划概况（调用 _build_idp_summary）
            6. 证书列表（调用 _build_certifications）
            7. 技能列表（调用 _build_skills）
            8. 人才盘点结果（调用 _build_review_summary）
            9. 成长时间线（调用 get_timeline）
            10. 人才标签（调用 _generate_tags，基于以上数据自动生成）

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 要构建画像的员工 ID

        返回:
            TalentProfile: 包含所有10个维度数据的完整人才画像对象

        异常:
            ValueError: 员工不存在时抛出（f"员工{employee_id}不存在"）
        """

        # 1. 员工基本信息
        emp_result = await db.execute(
            select(Employee).where(Employee.id == employee_id)
        )
        emp = emp_result.scalar_one_or_none()
        if not emp:
            raise ValueError(f"员工{employee_id}不存在")

        # 查询部门名称（可选，员工可能无部门）
        dept_name = None
        if emp.department_id:
            dept_result = await db.execute(
                select(Department.name).where(Department.id == emp.department_id)
            )
            dept_name = dept_result.scalar()

        # 计算司龄（年），精确到小数点后1位
        hire_date_str = emp.hire_date.isoformat() if emp.hire_date else None
        tenure_years = 0.0
        if emp.hire_date:
            delta = date.today() - emp.hire_date
            tenure_years = round(delta.days / 365.25, 1)  # 365.25 考虑闰年平均

        # 2. 绩效历史（最近4次，按年份+季度倒序）
        perf_result = await db.execute(
            select(
                PerformanceEvaluation.grade,
                PerformanceEvaluation.weighted_score,
                PerformanceCycle.name.label("cycle_name"),
            )
            .join(PerformanceCycle, PerformanceEvaluation.cycle_id == PerformanceCycle.id)
            .where(PerformanceEvaluation.employee_id == employee_id)
            .order_by(PerformanceCycle.year.desc(), PerformanceCycle.quarter.desc())
            .limit(4)  # 只取最近4次，保持画像简洁
        )
        perf_rows = perf_result.all()
        performance_summary = [
            PerformanceSummaryItem(
                cycle_name=row.cycle_name,
                grade=row.grade,
                score=float(row.weighted_score) if row.weighted_score else None,
            )
            for row in perf_rows
        ]

        # 3. 胜任力雷达（实际等级 vs 岗位要求）
        competency_radar = await TalentProfileService.get_radar_data(db, employee_id)

        # 4. 培训概况（参训数/完成数/均分）
        training_summary = await TalentProfileService._build_training_summary(
            db, employee_id
        )

        # 5. IDP概况（计划数/目标数/完成率）
        idp_summary = await TalentProfileService._build_idp_summary(db, employee_id)

        # 6. 证书列表
        certifications = await TalentProfileService._build_certifications(
            db, employee_id
        )

        # 7. 技能列表（按熟练度倒序）
        skills = await TalentProfileService._build_skills(db, employee_id)

        # 8. 人才盘点概况（九宫格/关键人才/离职风险）
        review_summary = await TalentProfileService._build_review_summary(
            db, employee_id
        )

        # 9. 成长时间线（入职+培训+绩效+证书事件流）
        timeline = await TalentProfileService.get_timeline(db, employee_id)

        # 10. 人才标签（基于规则自动生成）
        overall_tags = TalentProfileService._generate_tags(
            performance_summary=performance_summary,
            competency_radar=competency_radar,
            training_summary=training_summary,
            review_summary=review_summary,
            certifications=certifications,
            tenure_years=tenure_years,
        )

        return TalentProfile(
            employee_id=employee_id,
            employee_name=emp.name,
            position=emp.position,
            department_name=dept_name,
            hire_date=hire_date_str,
            tenure_years=tenure_years,
            performance_summary=performance_summary,
            competency_radar=competency_radar,
            training_summary=training_summary,
            idp_summary=idp_summary,
            certifications=certifications,
            skills=skills,
            review_summary=review_summary,
            timeline=timeline,
            overall_tags=overall_tags,
        )

    # ------------------------------------------------------------------
    # 胜任力雷达数据
    # ------------------------------------------------------------------

    @staticmethod
    async def get_radar_data(
        db: AsyncSession, employee_id: int
    ) -> list[CompetencyRadarItem]:
        """
        获取员工胜任力雷达图数据：实际评估等级 vs 岗位要求等级。

        雷达图用于直观展示员工在各维度能力上的达标情况：
        - assessed_level（实际）通常绘制为实心线
        - required_level（要求）通常绘制为虚线
        - 差距越大表示该维度需要重点提升

        业务流程：
            1. 查询员工当前岗位（position）；
            2. 通过 PositionCompetency 查询岗位的所有能力要求；
            3. 查询员工评估历史，每项能力取最新一条评估；
            4. 组装成 CompetencyRadarItem 列表（含实际等级 + 要求等级）。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 员工 ID

        返回:
            list[CompetencyRadarItem]: 雷达图数据列表，每项包含：
                - competency_name: 能力项名称
                - category: 能力分类
                - assessed_level: 实际评估等级（0=未评估，1-5）
                - required_level: 岗位要求等级（1-5）
            若员工无岗位或岗位无胜任力配置，返回空列表。
        """

        # 获取员工岗位
        emp_result = await db.execute(
            select(Employee.position).where(Employee.id == employee_id)
        )
        position = emp_result.scalar()
        if not position:
            return []  # 员工无岗位，无法构建雷达图

        # 查询岗位的胜任力要求（含能力项名称和分类）
        req_result = await db.execute(
            select(
                PositionCompetency.competency_item_id,
                PositionCompetency.required_level,
                CompetencyItem.name.label("competency_name"),
                CompetencyItem.category,
            )
            .join(CompetencyItem, PositionCompetency.competency_item_id == CompetencyItem.id)
            .where(PositionCompetency.position_name == position)
        )
        requirements = req_result.all()

        if not requirements:
            return []  # 岗位无胜任力配置，返回空

        # 查询员工评估历史（按时间倒序，用于取最新值）
        assess_result = await db.execute(
            select(
                EmployeeCompetency.competency_item_id,
                EmployeeCompetency.assessed_level,
            )
            .where(EmployeeCompetency.employee_id == employee_id)
            .order_by(EmployeeCompetency.assessed_at.desc())
        )
        assess_rows = assess_result.all()

        # 每项能力取最新评估（首次出现即为最新，因为结果已按时间倒序）
        assessed_map: dict[int, int] = {}
        for row in assess_rows:
            if row.competency_item_id not in assessed_map:
                assessed_map[row.competency_item_id] = row.assessed_level

        # 组装雷达图数据（未评估项的实际等级默认为0）
        radar_items = []
        for req in requirements:
            radar_items.append(CompetencyRadarItem(
                competency_name=req.competency_name,
                category=req.category,
                assessed_level=assessed_map.get(req.competency_item_id, 0),  # 未评估默认0
                required_level=req.required_level,
            ))

        return radar_items

    # ------------------------------------------------------------------
    # 成长时间线
    # ------------------------------------------------------------------

    @staticmethod
    async def get_timeline(
        db: AsyncSession, employee_id: int
    ) -> list[TimelineEvent]:
        """
        构建员工成长时间线，聚合入职、培训、绩效、证书等关键事件。

        时间线事件类型（event_type）：
            - hire：入职公司（根据 hire_date 生成，每人只有一条）
            - training：完成培训课程（取最近20条已完成记录）
            - performance：绩效评定完成（取最近10条已完成绩效）
            - certification：获得证书（从 employee_certifications 表取最近10条）

        排序：
            所有事件按 event_date 字符串倒序排列（最近事件在最前），
            使前端时间线组件从上到下展示最新到最旧的顺序。

        容错：
            证书事件使用原生 SQL 查询，包裹在 try/except 中；
            若 employee_certifications 表不存在，静默跳过（pass）。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 员工 ID

        返回:
            list[TimelineEvent]: 时间线事件列表，按日期倒序排列，每项包含：
                - event_type: 事件类型
                - event_date: 事件日期（YYYY-MM-DD 字符串）
                - title: 事件标题
                - description: 事件描述
        """
        events: list[TimelineEvent] = []

        # 入职事件（每人一条，作为时间线的起始锚点）
        emp_result = await db.execute(
            select(Employee.hire_date, Employee.name, Employee.position)
            .where(Employee.id == employee_id)
        )
        emp = emp_result.one_or_none()
        if emp and emp.hire_date:
            events.append(TimelineEvent(
                event_type="hire",
                event_date=emp.hire_date.isoformat(),
                title="入职公司",
                description=f"入职岗位: {emp.position or '未指定'}",
            ))

        # 培训完成事件（取最近20条，展示学习轨迹）
        training_result = await db.execute(
            select(
                TrainingEnrollment.completed_at,
                TrainingEnrollment.score,
                TrainingCourse.name.label("course_name"),
            )
            .join(TrainingCourse, TrainingEnrollment.course_id == TrainingCourse.id)
            .where(
                TrainingEnrollment.employee_id == employee_id,
                TrainingEnrollment.status == "completed",
                TrainingEnrollment.completed_at.isnot(None),  # 必须有完成时间
            )
            .order_by(TrainingEnrollment.completed_at.desc())
            .limit(20)
        )
        for row in training_result.all():
            score_str = f"，成绩: {row.score}" if row.score else ""
            events.append(TimelineEvent(
                event_type="training",
                event_date=row.completed_at.strftime("%Y-%m-%d"),
                title=f"完成培训: {row.course_name}",
                description=f"培训完成{score_str}",
            ))

        # 绩效评定事件（取最近10条已完成的绩效考核）
        perf_result = await db.execute(
            select(
                PerformanceEvaluation.grade,
                PerformanceEvaluation.weighted_score,
                PerformanceEvaluation.updated_at,
                PerformanceCycle.name.label("cycle_name"),
            )
            .join(PerformanceCycle, PerformanceEvaluation.cycle_id == PerformanceCycle.id)
            .where(
                PerformanceEvaluation.employee_id == employee_id,
                PerformanceEvaluation.status == "completed",  # 只取已完成的绩效考核
            )
            .order_by(PerformanceEvaluation.updated_at.desc())
            .limit(10)
        )
        for row in perf_result.all():
            score_str = f"，得分: {row.weighted_score}" if row.weighted_score else ""
            events.append(TimelineEvent(
                event_type="performance",
                event_date=row.updated_at.strftime("%Y-%m-%d") if row.updated_at else "",
                title=f"绩效评定: {row.cycle_name}",
                description=f"等级: {row.grade or '未定'}{score_str}",
            ))

        # 证书事件（使用原生 SQL，防止表不存在时崩溃）
        try:
            cert_result = await db.execute(
                text("""
                    SELECT cert_name, expiry_date, status, created_at
                    FROM employee_certifications
                    WHERE employee_id = :eid
                    ORDER BY created_at DESC
                    LIMIT 10
                """),
                {"eid": employee_id},
            )
            for row in cert_result.all():
                event_date = row.created_at.strftime("%Y-%m-%d") if row.created_at else ""
                events.append(TimelineEvent(
                    event_type="certification",
                    event_date=event_date,
                    title=f"获得证书: {row.cert_name}",
                    description=f"状态: {row.status or '有效'}",
                ))
        except Exception:
            pass  # 表不存在时静默跳过，不影响其他维度数据

        # 按日期字符串倒序排列（最新事件在最前）
        events.sort(key=lambda e: e.event_date, reverse=True)
        return events

    # ------------------------------------------------------------------
    # 人才搜索
    # ------------------------------------------------------------------

    @staticmethod
    async def search_talents(
        db: AsyncSession,
        keyword: Optional[str] = None,
        skill: Optional[str] = None,
        min_competency: Optional[int] = None,
    ) -> list[TalentSearchResult]:
        """
        多维度人才搜索，支持关键词、技能、胜任力等级3种维度。

        搜索维度：
            1. keyword（关键词）：
               - 对员工姓名（name）和岗位（position）做 ILIKE 模糊搜索
               - 仅搜索在职（"在职"/"试用期"）员工
               - 记录匹配字段（match_fields：["姓名"] 或 ["岗位"] 或两者）

            2. skill（技能名）：
               - 在 employee_skills 表中按技能名做 ILIKE 模糊搜索
               - 使用原生 SQL（try/except 防表不存在）
               - 仅匹配在职员工

            3. min_competency（最低胜任力均值）：
               - 按员工所有胜任力评估的平均等级（AVG）过滤
               - 使用 HAVING 子句：AVG(assessed_level) >= min_competency
               - 仅匹配在职员工
               - 0 或 None 时跳过此维度

        去重：
            三种维度可组合使用，seen_ids 集合确保同一员工不重复出现在结果中。

        参数:
            db (AsyncSession): 异步数据库会话
            keyword (str | None): 按姓名/岗位关键词搜索
            skill (str | None): 按技能名称搜索（ILIKE 模糊匹配）
            min_competency (int | None): 按最低平均胜任力等级过滤（1-5，0 或 None 不过滤）

        返回:
            list[TalentSearchResult]: 匹配的员工列表，每项包含：
                - employee_id / employee_name / position / department_name: 基本信息
                - match_fields: 命中的搜索维度（如 ["姓名"]、["技能"]、["胜任力均值>=3"]）
        """
        results: list[TalentSearchResult] = []
        seen_ids: set[int] = set()  # 去重集合，防止同一员工出现多次

        # 维度1：按姓名/岗位关键词搜索
        if keyword:
            kw = f"%{keyword}%"
            emp_result = await db.execute(
                select(
                    Employee.id,
                    Employee.name,
                    Employee.position,
                    Employee.department_id,
                    Department.name.label("dept_name"),
                )
                .outerjoin(Department, Employee.department_id == Department.id)
                .where(
                    or_(
                        Employee.name.ilike(kw),       # 按姓名模糊匹配
                        Employee.position.ilike(kw),   # 按岗位模糊匹配
                    ),
                    Employee.status.in_(["在职", "试用期"]),  # 只搜索在职员工
                )
                .limit(50)
            )
            for row in emp_result.all():
                if row.id not in seen_ids:
                    dept_name = row.dept_name
                    # 记录具体命中的字段，便于前端高亮显示
                    match_fields = []
                    if keyword.lower() in (row.name or "").lower():
                        match_fields.append("姓名")
                    if keyword.lower() in (row.position or "").lower():
                        match_fields.append("岗位")
                    results.append(TalentSearchResult(
                        employee_id=row.id,
                        employee_name=row.name,
                        position=row.position,
                        department_name=dept_name,
                        match_fields=match_fields,
                    ))
                    seen_ids.add(row.id)

        # 维度2：按技能名搜索（employee_skills 表）
        if skill:
            try:
                skill_result = await db.execute(
                    text("""
                        SELECT es.employee_id, e.name, e.position, e.department_id,
                               d.name as dept_name
                        FROM employee_skills es
                        JOIN employees e ON es.employee_id = e.id
                        LEFT JOIN departments d ON e.department_id = d.id
                        WHERE es.skill_name ILIKE :skill_kw
                          AND e.status IN ('在职', '试用期')
                        LIMIT 50
                    """),
                    {"skill_kw": f"%{skill}%"},
                )
                for row in skill_result.all():
                    if row.employee_id not in seen_ids:
                        dept_name = row.dept_name
                        results.append(TalentSearchResult(
                            employee_id=row.employee_id,
                            employee_name=row.name,
                            position=row.position,
                            department_name=dept_name,
                            match_fields=["技能"],
                        ))
                        seen_ids.add(row.employee_id)
            except Exception:
                pass  # employee_skills 表不存在时静默跳过

        # 维度3：按最低胜任力均值过滤（HAVING 子句）
        if min_competency is not None and min_competency > 0:
            comp_result = await db.execute(
                select(
                    EmployeeCompetency.employee_id,
                    Employee.name,
                    Employee.position,
                    Employee.department_id,
                    Department.name.label("dept_name"),
                    func.avg(EmployeeCompetency.assessed_level).label("avg_level"),
                )
                .join(Employee, EmployeeCompetency.employee_id == Employee.id)
                .outerjoin(Department, Employee.department_id == Department.id)
                .where(Employee.status.in_(["在职", "试用期"]))
                .group_by(
                    EmployeeCompetency.employee_id,
                    Employee.name,
                    Employee.position,
                    Employee.department_id,
                    Department.name,
                )
                # HAVING 过滤：只返回平均胜任力等级 >= min_competency 的员工
                .having(func.avg(EmployeeCompetency.assessed_level) >= min_competency)
                .limit(50)
            )
            for row in comp_result.all():
                if row.employee_id not in seen_ids:
                    dept_name = row.dept_name
                    results.append(TalentSearchResult(
                        employee_id=row.employee_id,
                        employee_name=row.name,
                        position=row.position,
                        department_name=dept_name,
                        match_fields=[f"胜任力均值>={min_competency}"],
                    ))
                    seen_ids.add(row.employee_id)

        return results

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    async def _build_training_summary(
        db: AsyncSession, employee_id: int
    ) -> TrainingSummary:
        """
        构建员工培训概况（内部辅助方法，被 build_profile 调用）。

        统计指标：
            - total_courses: 该员工的总参训课程数（所有状态）
            - completed: 已完成的课程数（status="completed"）
            - avg_score: 已有成绩的课程平均分（score 不为 None 的记录均值）

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 员工 ID

        返回:
            TrainingSummary: 培训概况对象（total_courses/completed/avg_score）
        """
        # 总课程数（包含进行中、已取消等所有状态）
        total_result = await db.execute(
            select(func.count(TrainingEnrollment.id))
            .where(TrainingEnrollment.employee_id == employee_id)
        )
        total_courses = total_result.scalar() or 0

        # 已完成数
        completed_result = await db.execute(
            select(func.count(TrainingEnrollment.id))
            .where(
                TrainingEnrollment.employee_id == employee_id,
                TrainingEnrollment.status == "completed",
            )
        )
        completed = completed_result.scalar() or 0

        # 平均成绩（仅统计有成绩的记录，未评分的不纳入均值）
        avg_result = await db.execute(
            select(func.avg(TrainingEnrollment.score))
            .where(
                TrainingEnrollment.employee_id == employee_id,
                TrainingEnrollment.score.isnot(None),  # 排除无成绩记录
            )
        )
        avg_score = float(avg_result.scalar() or 0)

        return TrainingSummary(
            total_courses=total_courses,
            completed=completed,
            avg_score=round(avg_score, 1),
        )

    @staticmethod
    async def _build_idp_summary(
        db: AsyncSession, employee_id: int
    ) -> IDPSummary:
        """
        构建 IDP 发展计划概况（内部辅助方法，被 build_profile 调用）。

        统计指标：
            - plan_count: 该员工的 IDP 计划总数
            - active_goals: 进行中的目标数（status="in_progress"）
            - completion_rate: 目标完成率 = 已完成目标数 / 总目标数 * 100

        使用原生 SQL（text()）查询，因为 idp_plans/idp_goals 表通过 talent.py
        管理，此处直接 SQL 避免循环导入。包裹在 try/except 中防止表不存在时崩溃。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 员工 ID

        返回:
            IDPSummary: IDP 概况对象，表不存在时返回空对象（所有字段为0）
        """
        try:
            # 查询 IDP 计划数
            plan_result = await db.execute(
                text("""
                    SELECT COUNT(*) as plan_count
                    FROM idp_plans
                    WHERE employee_id = :eid
                """),
                {"eid": employee_id},
            )
            plan_count = plan_result.scalar() or 0

            # 查询目标统计：进行中数量、总数量、平均进度
            goals_result = await db.execute(
                text("""
                    SELECT
                        COUNT(*) FILTER (WHERE g.status = 'in_progress') as active_goals,
                        COUNT(*) as total_goals,
                        COALESCE(AVG(g.progress_pct), 0) as avg_progress
                    FROM idp_goals g
                    JOIN idp_plans p ON g.idp_id = p.id
                    WHERE p.employee_id = :eid
                """),
                {"eid": employee_id},
            )
            goals_row = goals_result.one_or_none()
            active_goals = goals_row.active_goals if goals_row else 0
            total_goals = goals_row.total_goals if goals_row else 0
            avg_progress = float(goals_row.avg_progress) if goals_row else 0

            # 单独查询已完成目标数（用于计算完成率）
            completed_result = await db.execute(
                text("""
                    SELECT COUNT(*) as completed
                    FROM idp_goals g
                    JOIN idp_plans p ON g.idp_id = p.id
                    WHERE p.employee_id = :eid AND g.status = 'completed'
                """),
                {"eid": employee_id},
            )
            completed = completed_result.scalar() or 0
            # 完成率：已完成目标数 / 总目标数，防止除零
            completion_rate = (completed / total_goals * 100) if total_goals > 0 else 0

            return IDPSummary(
                plan_count=plan_count,
                active_goals=active_goals,
                completion_rate=round(completion_rate, 1),
            )
        except Exception:
            return IDPSummary()  # 表不存在时返回空对象，不影响画像其他维度

    @staticmethod
    async def _build_certifications(
        db: AsyncSession, employee_id: int
    ) -> list[CertificationItem]:
        """
        获取员工证书列表（内部辅助方法，被 build_profile 调用）。

        使用原生 SQL 查询 employee_certifications 表，包裹在 try/except 中。
        结果按 expiry_date 倒序排列（最晚到期的排前，重要性更高）。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 员工 ID

        返回:
            list[CertificationItem]: 证书列表，每项含证书名/到期日/状态；
            表不存在时返回空列表。
        """
        try:
            result = await db.execute(
                text("""
                    SELECT cert_name, expiry_date, status
                    FROM employee_certifications
                    WHERE employee_id = :eid
                    ORDER BY expiry_date DESC  -- 按到期日倒序，最晚到期的排前面
                """),
                {"eid": employee_id},
            )
            return [
                CertificationItem(
                    cert_name=row.cert_name,
                    expiry_date=row.expiry_date.isoformat() if row.expiry_date else None,
                    status=row.status,
                )
                for row in result.all()
            ]
        except Exception:
            return []  # 表不存在时返回空列表

    @staticmethod
    async def _build_skills(
        db: AsyncSession, employee_id: int
    ) -> list[SkillItem]:
        """
        获取员工技能列表（内部辅助方法，被 build_profile 调用）。

        使用原生 SQL 查询 employee_skills 表，按熟练度倒序排列（最强技能排前）。
        包裹在 try/except 中防止表不存在时崩溃。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 员工 ID

        返回:
            list[SkillItem]: 技能列表，每项含技能名/熟练度等级；
            表不存在时返回空列表。
        """
        try:
            result = await db.execute(
                text("""
                    SELECT skill_name, proficiency_level
                    FROM employee_skills
                    WHERE employee_id = :eid
                    ORDER BY proficiency_level DESC  -- 熟练度高的排前面，突出核心技能
                """),
                {"eid": employee_id},
            )
            return [
                SkillItem(
                    skill_name=row.skill_name,
                    proficiency_level=row.proficiency_level,
                )
                for row in result.all()
            ]
        except Exception:
            return []  # 表不存在时返回空列表

    @staticmethod
    async def _build_review_summary(
        db: AsyncSession, employee_id: int
    ) -> ReviewSummary:
        """
        获取最新一次人才盘点结果（内部辅助方法，被 build_profile 调用）。

        从 talent_review_entries 表中取最新一次盘点的结果，包含：
        - nine_box_position: 九宫格位置（如"高绩效-高潜力"/"明星"）
        - is_key_talent: 是否被标注为关键人才
        - flight_risk: 离职风险（low/medium/high）

        使用原生 SQL + try/except，防止表不存在时崩溃。
        取最新一次盘点（ORDER BY session_id DESC LIMIT 1）。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 员工 ID

        返回:
            ReviewSummary: 最新盘点结果；表不存在或无记录时返回空对象（所有字段为 None）
        """
        try:
            result = await db.execute(
                text("""
                    SELECT nine_box_position, is_key_talent, flight_risk
                    FROM talent_review_entries
                    WHERE employee_id = :eid
                    ORDER BY session_id DESC  -- 取最新一次盘点会话的结果
                    LIMIT 1
                """),
                {"eid": employee_id},
            )
            row = result.one_or_none()
            if row:
                return ReviewSummary(
                    nine_box_position=row.nine_box_position,
                    is_key_talent=row.is_key_talent,
                    flight_risk=row.flight_risk,
                )
            return ReviewSummary()  # 无盘点记录时返回空对象
        except Exception:
            return ReviewSummary()  # 表不存在时返回空对象

    @staticmethod
    def _generate_tags(
        performance_summary: list[PerformanceSummaryItem],
        competency_radar: list[CompetencyRadarItem],
        training_summary: TrainingSummary,
        review_summary: ReviewSummary,
        certifications: list[CertificationItem],
        tenure_years: float,
    ) -> list[str]:
        """
        基于多维度数据自动生成人才标签列表（纯计算，无DB操作）。

        该方法为同步方法（无 async），被 build_profile 在异步上下文中直接调用。

        标签生成规则（按维度分组）：

        绩效维度：
            - "高绩效"：最新考核等级 = A
            - "绩效待提升"：最新考核等级 = D
            - "持续高绩效"：近4次考核中 >= 2次 A 等级

        胜任力维度：
            - "胜任力达标"：所有能力项平均差距（required - assessed）<= 0
            - "能力差距较大"：平均差距 >= 2 级

        培训维度：
            - "学习积极"：培训完成率（completed/total * 100）>= 90%
            - "培训优秀"：已有成绩的平均分 >= 90 分

        人才盘点维度：
            - "关键人才"：is_key_talent = True
            - "高离职风险"：flight_risk = "high"
            - "明星员工"：nine_box_position 含 "star" 或 "高绩效-高潜力"

        证书维度：
            - "证书即将到期"：任意证书的 expiry_date <= 今天（已到期但未更新状态）

        司龄维度：
            - "资深员工"：tenure_years >= 5 年
            - "新员工"：tenure_years < 1 年

        参数:
            performance_summary: 最近4次绩效考核结果
            competency_radar: 胜任力雷达数据（含实际等级和要求等级）
            training_summary: 培训参训概况
            review_summary: 最新人才盘点结果
            certifications: 证书列表
            tenure_years: 司龄（年）

        返回:
            list[str]: 人才标签字符串列表（可能为空列表）
        """
        tags: list[str] = []

        # 绩效标签
        if performance_summary:
            latest_grade = performance_summary[0].grade  # 最近一次绩效等级
            if latest_grade == "A":
                tags.append("高绩效")
            elif latest_grade == "D":
                tags.append("绩效待提升")

            # 统计近4次中A等级的次数
            a_count = sum(1 for p in performance_summary if p.grade == "A")
            if a_count >= 2:
                tags.append("持续高绩效")

        # 胜任力标签
        if competency_radar:
            # 计算每个能力项的差距（required - assessed），负值表示超出要求
            gaps = [r.required_level - r.assessed_level for r in competency_radar]
            avg_gap = sum(gaps) / len(gaps) if gaps else 0
            if avg_gap <= 0:
                tags.append("胜任力达标")  # 所有能力项均达标或超出
            elif avg_gap >= 2:
                tags.append("能力差距较大")  # 平均差距超过2级，需重点培训

        # 培训标签
        if training_summary.total_courses > 0:
            # 培训完成率 = 已完成 / 总参训 * 100
            completion_rate = (
                training_summary.completed / training_summary.total_courses * 100
            )
            if completion_rate >= 90:
                tags.append("学习积极")
            if training_summary.avg_score >= 90:
                tags.append("培训优秀")

        # 人才盘点标签
        if review_summary.is_key_talent:
            tags.append("关键人才")
        if review_summary.flight_risk == "high":
            tags.append("高离职风险")
        if review_summary.nine_box_position:
            nb = review_summary.nine_box_position.lower()
            # 兼容英文（"star"）和中文（"高绩效-高潜力"）两种表达方式
            if "star" in nb or "高绩效-高潜力" in nb:
                tags.append("明星员工")

        # 证书标签（检测已到期但可能状态未更新的证书）
        if certifications:
            today = date.today().isoformat()
            expiring = [
                c for c in certifications
                if c.expiry_date and c.expiry_date <= today  # 到期日 <= 今天
            ]
            if expiring:
                tags.append("证书即将到期")

        # 司龄标签
        if tenure_years >= 5:
            tags.append("资深员工")
        elif tenure_years < 1:
            tags.append("新员工")

        return tags
