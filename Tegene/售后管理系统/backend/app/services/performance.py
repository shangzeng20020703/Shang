"""
绩效管理服务模块 - Performance Service
========================================

业务职责：
    负责售后管理系统的全流程绩效管理，包括：
    - 绩效模板（PerformanceTemplate）的增删改查：定义考核指标结构和权重
    - 绩效指标（PerformanceIndicator）的增删改查：模板下的具体考核项
    - 考核周期（PerformanceCycle）的增删改查及状态流转：管理考核时间范围
    - 绩效评估（PerformanceEvaluation）的全流程：自评 → 上级评分 → 系统加权 → 等级
    - 等级分布统计与强制分布校验（GradeDistributionService）
    - 目标管理（GoalService）：KPI/OKR 目标的创建、跟踪与汇总
    - 强制分布计算（ForceDistributionService）：按配置比例对员工排名分档

考核流程：
    1. HR 创建绩效模板（定义指标和权重）
    2. HR 创建考核周期，绑定模板，批量生成员工评估记录
    3. 员工提交自评（status: pending_self → pending_superior）
    4. 上级提交评分，系统自动计算加权得分和等级（status: pending_superior → completed）
    5. 可选：绩效面谈记录
    6. HR 进行等级校准（强制分布）

评分公式（默认）：
    weighted_score = self_score × 0.30 + superior_score × 0.70

等级映射（默认，可被模板覆盖）：
    A ≥ 90分 → 绩效系数 1.30
    B ≥ 75分 → 绩效系数 1.10
    C ≥ 60分 → 绩效系数 1.00
    D ≥  0分 → 绩效系数 0.80

强制分布规则（通过 cycle.forced_distribution_json 配置，允许5%容差）：
    A ≤ 10% / B ≤ 30% / C ≈ 50% / D ≥ 10%

技术栈：
    FastAPI + SQLAlchemy 2.0 Async + PostgreSQL + Pydantic v2
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import and_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.performance import (
    PerformanceTemplate,
    PerformanceCycle,
    PerformanceIndicator,
    PerformanceEvaluation,
    PerformanceFeedback360,
    PerformanceGoal,
)
from app.schemas.performance import (
    PerformanceTemplateCreate,
    PerformanceTemplateUpdate,
    PerformanceIndicatorCreate,
    PerformanceIndicatorUpdate,
    PerformanceCycleCreate,
    PerformanceCycleUpdate,
    PerformanceCycleStatusTransition,
    PerformanceEvaluationCreate,
    SelfEvalSubmit,
    SuperiorScoreSubmit,
    InterviewRecordSubmit,
    GradeDistribution,
    ForcedDistributionCheck,
    GoalCreate,
    GoalUpdate,
    GoalSummary,
    GoalSummaryItem,
    DistributionConfig,
    ForceDistributionResult,
    PerformanceFeedback360Create,
)

# ---------------------------------------------------------------------------
# 等级映射 & 绩效系数默认配置
# ---------------------------------------------------------------------------
DEFAULT_GRADE_MAP: dict[str, tuple[Decimal, Decimal]] = {
    # grade: (最低分线, 绩效系数)
    # A级：90分及以上，绩效系数1.30（超出基准30%）
    "A": (Decimal("90"), Decimal("1.30")),
    # B级：75~89分，绩效系数1.10（超出基准10%）
    "B": (Decimal("75"), Decimal("1.10")),
    # C级：60~74分，绩效系数1.00（达到基准）
    "C": (Decimal("60"), Decimal("1.00")),
    # D级：60分以下，绩效系数0.80（低于基准20%，适用于绩效改善计划）
    "D": (Decimal("0"), Decimal("0.80")),
}

# 考核周期状态流转规则
# not_started（未开始）→ self_eval（自评中）→ superior_eval（上级评分中）→ completed（已完成）
VALID_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "not_started": ["self_eval"],
    "self_eval": ["superior_eval"],
    "superior_eval": ["completed"],
    "completed": [],  # 终态，不允许任何状态转换
}


def _quantize(value: Decimal) -> Decimal:
    """将 Decimal 四舍五入保留2位小数（使用 ROUND_HALF_UP 规则）。

    Args:
        value: 需要格式化的 Decimal 数值

    Returns:
        保留2位小数的 Decimal，例如 99.999 → 100.00
    """
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ============================================================
# PerformanceTemplate CRUD
# 绩效模板服务 — 管理考核指标体系的模板定义
# ============================================================

class PerformanceTemplateService:
    """绩效模板服务。

    绩效模板是考核的"蓝图"，定义了：
    - 适用的岗位类型（position_type）
    - 自评权重和上级评分权重（默认 30%/70%）
    - 具体考核指标列表（PerformanceIndicator）

    同一模板可被多个考核周期复用。
    模板一经激活（is_active=True）并关联周期后，不建议修改权重配置。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: PerformanceTemplateCreate
    ) -> PerformanceTemplate:
        """创建绩效模板（同时批量创建关联指标）。

        执行步骤：
        1. 创建 PerformanceTemplate 主记录并 flush 获取 ID
        2. 遍历 data.indicators，依次创建 PerformanceIndicator 子记录
        3. 若 indicator 未指定 sort_order，按迭代顺序（0, 1, 2...）自动填充

        Args:
            db:   异步数据库会话
            data: 模板创建 Schema，包含模板基本信息和指标列表

        Returns:
            已创建并 refresh 的 PerformanceTemplate ORM 对象（含关联 indicators）
        """
        template = PerformanceTemplate(
            name=data.name,
            description=data.description,
            position_type=data.position_type,
            indicators_json=None,  # 结构化指标通过关联表存储，此字段暂保留为 None
            self_eval_weight=data.self_eval_weight,
            superior_eval_weight=data.superior_eval_weight,
            is_active=data.is_active,
        )
        db.add(template)
        await db.flush()  # 获取 template.id，供后续指标关联使用

        # 批量创建关联指标，sort_order 控制前端展示顺序
        for idx, indicator_data in enumerate(data.indicators):
            indicator = PerformanceIndicator(
                template_id=template.id,
                name=indicator_data.name,
                description=indicator_data.description,
                category=indicator_data.category,
                weight=indicator_data.weight,
                max_score=indicator_data.max_score,
                sort_order=indicator_data.sort_order if indicator_data.sort_order else idx,
            )
            db.add(indicator)

        await db.flush()
        await db.refresh(template)
        return template

    @staticmethod
    async def get(db: AsyncSession, template_id: int) -> Optional[PerformanceTemplate]:
        """按 ID 获取单个绩效模板。

        Args:
            db:          异步数据库会话
            template_id: 模板主键 ID

        Returns:
            PerformanceTemplate 对象，不存在则返回 None
        """
        result = await db.execute(
            select(PerformanceTemplate).where(PerformanceTemplate.id == template_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        position_type: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> list[PerformanceTemplate]:
        """查询绩效模板列表，支持按岗位类型和启用状态过滤。

        Args:
            db:            异步数据库会话
            position_type: 岗位类型过滤（如 "technical"/"management"），None 表示不过滤
            is_active:     启用状态过滤，None 表示不过滤

        Returns:
            按创建时间倒序排列的模板列表
        """
        query = select(PerformanceTemplate)
        if position_type:
            query = query.where(PerformanceTemplate.position_type == position_type)
        if is_active is not None:
            query = query.where(PerformanceTemplate.is_active == is_active)
        query = query.order_by(PerformanceTemplate.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, template_id: int, data: PerformanceTemplateUpdate
    ) -> Optional[PerformanceTemplate]:
        """更新绩效模板基本信息（仅更新 Schema 中显式传入的字段）。

        使用 exclude_unset=True 实现 PATCH 语义，未传入的字段不会被清空。

        Args:
            db:          异步数据库会话
            template_id: 待更新的模板 ID
            data:        包含待更新字段的 Schema（Pydantic partial update）

        Returns:
            更新后的模板对象，不存在则返回 None
        """
        template = await PerformanceTemplateService.get(db, template_id)
        if not template:
            return None
        update_data = data.model_dump(exclude_unset=True)  # 只取前端明确传入的字段
        for field, value in update_data.items():
            setattr(template, field, value)
        await db.flush()
        await db.refresh(template)
        return template

    @staticmethod
    async def delete(db: AsyncSession, template_id: int) -> bool:
        """删除绩效模板。

        注意：若模板已被考核周期引用（template_id FK），删除会触发数据库约束错误。
        建议先将 is_active 设为 False 而非直接删除。

        Args:
            db:          异步数据库会话
            template_id: 待删除的模板 ID

        Returns:
            True 表示删除成功，False 表示模板不存在
        """
        template = await PerformanceTemplateService.get(db, template_id)
        if not template:
            return False
        await db.delete(template)
        await db.flush()
        return True


# ============================================================
# PerformanceIndicator CRUD
# 绩效指标服务 — 管理模板下的具体考核指标
# ============================================================

class PerformanceIndicatorService:
    """绩效指标服务。

    每个指标属于一个模板，定义了：
    - 指标名称和描述（如"代码质量"、"团队协作"）
    - 指标分类（category，如"工作成果"/"工作能力"/"工作态度"）
    - 指标权重（weight，所有指标权重之和应为100）
    - 满分值（max_score，通常为100）
    - 展示顺序（sort_order）

    指标权重用于计算加权平均分：各指标得分 × 权重 / 总权重 × 100。
    """

    @staticmethod
    async def create(
        db: AsyncSession, template_id: int, data: PerformanceIndicatorCreate
    ) -> PerformanceIndicator:
        """在指定模板下创建单个绩效指标。

        Args:
            db:          异步数据库会话
            template_id: 所属模板 ID（作为外键）
            data:        指标创建 Schema（name/category/weight/max_score/sort_order）

        Returns:
            已创建的 PerformanceIndicator ORM 对象
        """
        indicator = PerformanceIndicator(
            template_id=template_id,
            **data.model_dump(),
        )
        db.add(indicator)
        await db.flush()
        await db.refresh(indicator)
        return indicator

    @staticmethod
    async def get(db: AsyncSession, indicator_id: int) -> Optional[PerformanceIndicator]:
        """按 ID 获取单个绩效指标。

        Args:
            db:           异步数据库会话
            indicator_id: 指标主键 ID

        Returns:
            PerformanceIndicator 对象，不存在则返回 None
        """
        result = await db.execute(
            select(PerformanceIndicator).where(PerformanceIndicator.id == indicator_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_template(
        db: AsyncSession, template_id: int
    ) -> list[PerformanceIndicator]:
        """获取指定模板下的所有指标（按 sort_order 升序排列）。

        Args:
            db:          异步数据库会话
            template_id: 所属模板 ID

        Returns:
            按展示顺序排列的指标列表
        """
        result = await db.execute(
            select(PerformanceIndicator)
            .where(PerformanceIndicator.template_id == template_id)
            .order_by(PerformanceIndicator.sort_order)
        )
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, indicator_id: int, data: PerformanceIndicatorUpdate
    ) -> Optional[PerformanceIndicator]:
        """更新绩效指标（PATCH 语义，仅更新传入字段）。

        Args:
            db:           异步数据库会话
            indicator_id: 待更新的指标 ID
            data:         包含待更新字段的 Schema

        Returns:
            更新后的指标对象，不存在则返回 None
        """
        indicator = await PerformanceIndicatorService.get(db, indicator_id)
        if not indicator:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(indicator, field, value)
        await db.flush()
        await db.refresh(indicator)
        return indicator

    @staticmethod
    async def delete(db: AsyncSession, indicator_id: int) -> bool:
        """删除绩效指标。

        Args:
            db:           异步数据库会话
            indicator_id: 待删除的指标 ID

        Returns:
            True 表示成功，False 表示指标不存在
        """
        indicator = await PerformanceIndicatorService.get(db, indicator_id)
        if not indicator:
            return False
        await db.delete(indicator)
        await db.flush()
        return True


# ============================================================
# PerformanceCycle CRUD
# 考核周期服务 — 管理每次绩效考核的时间范围和状态
# ============================================================

class PerformanceCycleService:
    """考核周期服务。

    考核周期是一次完整绩效考核的容器，包含：
    - 周期名称和类型（cycle_type：annual/半年/季度）
    - 时间范围（start_date / end_date）
    - 状态机（not_started → self_eval → superior_eval → completed）
    - 关联绩效模板（template_id）
    - 强制分布配置（forced_distribution_json：JSON格式的各等级目标占比）

    状态流转规则参见模块顶部的 VALID_STATUS_TRANSITIONS 字典。
    已完成的周期不允许修改；只有未开始的周期可以删除。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: PerformanceCycleCreate
    ) -> PerformanceCycle:
        """创建考核周期。

        Args:
            db:   异步数据库会话
            data: 周期创建 Schema（name/year/cycle_type/start_date/end_date/template_id 等）

        Returns:
            已创建的 PerformanceCycle ORM 对象，初始状态为 not_started
        """
        cycle = PerformanceCycle(**data.model_dump())
        db.add(cycle)
        await db.flush()
        await db.refresh(cycle)
        return cycle

    @staticmethod
    async def get(db: AsyncSession, cycle_id: int) -> Optional[PerformanceCycle]:
        """按 ID 获取单个考核周期。

        Args:
            db:       异步数据库会话
            cycle_id: 考核周期主键 ID

        Returns:
            PerformanceCycle 对象，不存在则返回 None
        """
        result = await db.execute(
            select(PerformanceCycle).where(PerformanceCycle.id == cycle_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        year: Optional[int] = None,
        cycle_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[PerformanceCycle]:
        """查询考核周期列表，支持多维度过滤。

        Args:
            db:         异步数据库会话
            year:       按年份过滤（如 2024），None 表示不过滤
            cycle_type: 按周期类型过滤（annual/半年/季度），None 表示不过滤
            status:     按状态过滤（not_started/self_eval/superior_eval/completed），None 表示不过滤

        Returns:
            按年份倒序、开始日期倒序排列的周期列表
        """
        query = select(PerformanceCycle)
        if year:
            query = query.where(PerformanceCycle.year == year)
        if cycle_type:
            query = query.where(PerformanceCycle.cycle_type == cycle_type)
        if status:
            query = query.where(PerformanceCycle.status == status)
        query = query.order_by(PerformanceCycle.year.desc(), PerformanceCycle.start_date.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, cycle_id: int, data: PerformanceCycleUpdate
    ) -> Optional[PerformanceCycle]:
        """更新考核周期基本信息（已完成周期不允许修改）。

        Args:
            db:       异步数据库会话
            cycle_id: 待更新的周期 ID
            data:     包含待更新字段的 Schema

        Returns:
            更新后的周期对象，不存在则返回 None

        Raises:
            ValueError: 当周期状态为 completed 时，拒绝修改
        """
        cycle = await PerformanceCycleService.get(db, cycle_id)
        if not cycle:
            return None
        if cycle.status == "completed":
            raise ValueError("已完成的考核周期不能修改")
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(cycle, field, value)
        await db.flush()
        await db.refresh(cycle)
        return cycle

    @staticmethod
    async def transition_status(
        db: AsyncSession, cycle_id: int, data: PerformanceCycleStatusTransition
    ) -> Optional[PerformanceCycle]:
        """执行考核周期状态流转（严格按照预定义的状态机规则）。

        状态机：not_started → self_eval → superior_eval → completed
        不允许跳跃状态（如直接从 not_started 跳到 completed）。

        Args:
            db:       异步数据库会话
            cycle_id: 目标周期 ID
            data:     包含 target_status 的 Schema

        Returns:
            状态更新后的周期对象，不存在则返回 None

        Raises:
            ValueError: 当目标状态不在当前状态的允许转换列表中时抛出
        """
        cycle = await PerformanceCycleService.get(db, cycle_id)
        if not cycle:
            return None

        # 查找当前状态允许跳转的目标状态列表
        allowed = VALID_STATUS_TRANSITIONS.get(cycle.status, [])
        if data.target_status not in allowed:
            raise ValueError(
                f"不允许从 '{cycle.status}' 转换到 '{data.target_status}'。"
                f"允许的目标状态: {allowed}"
            )

        cycle.status = data.target_status
        await db.flush()
        await db.refresh(cycle)
        return cycle

    @staticmethod
    async def delete(db: AsyncSession, cycle_id: int) -> bool:
        """删除考核周期（仅允许删除未开始的周期）。

        已开始的考核周期包含员工评估记录，不允许删除以保护数据完整性。

        Args:
            db:       异步数据库会话
            cycle_id: 待删除的周期 ID

        Returns:
            True 表示删除成功，False 表示周期不存在

        Raises:
            ValueError: 当周期状态不为 not_started 时拒绝删除
        """
        cycle = await PerformanceCycleService.get(db, cycle_id)
        if not cycle:
            return False
        if cycle.status != "not_started":
            raise ValueError("只能删除未开始的考核周期")
        await db.delete(cycle)
        await db.flush()
        return True


# ============================================================
# PerformanceEvaluation 绩效评估
# 评估记录服务 — 管理每位员工在特定考核周期内的评估全过程
# ============================================================

class PerformanceEvaluationService:
    """绩效评估服务。

    PerformanceEvaluation 是绩效考核的核心实体，记录：
    - 被考核员工（employee_id）
    - 所属考核周期（cycle_id）
    - 使用的绩效模板（template_id，可选）
    - 直接上级/评分人（evaluator_id，可选）
    - 自评分数 JSON（self_scores_json）
    - 上级评分 JSON（superior_scores_json）
    - 系统计算的加权总分（weighted_score）
    - 最终等级（grade：A/B/C/D）
    - 绩效系数（performance_coefficient，用于薪资计算）
    - 评估状态：pending_self → pending_superior → completed

    评分 JSON 格式：{"指标ID字符串": 分数值}，例如 {"1": 85, "2": 90}

    核心计算方法 _calc_weighted_score() 和 _map_grade() 是评分引擎的心脏。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: PerformanceEvaluationCreate
    ) -> PerformanceEvaluation:
        """创建单条评估记录（通常由 create_batch 批量调用）。

        Args:
            db:   异步数据库会话
            data: 评估创建 Schema

        Returns:
            已创建的 PerformanceEvaluation 对象
        """
        evaluation = PerformanceEvaluation(**data.model_dump())
        db.add(evaluation)
        await db.flush()
        await db.refresh(evaluation)
        return evaluation

    @staticmethod
    async def create_batch(
        db: AsyncSession,
        cycle_id: int,
        employee_evaluator_pairs: list[tuple[int, Optional[int]]],
        template_id: Optional[int] = None,
    ) -> list[PerformanceEvaluation]:
        """批量创建评估记录（通常在开启考核周期时调用）。

        HR 开启考核周期时，系统为所有参与员工自动生成评估记录，
        初始状态为 pending_self（等待员工自评）。

        Args:
            db:                          异步数据库会话
            cycle_id:                    所属考核周期 ID
            employee_evaluator_pairs:    员工-上级配对列表，格式 [(employee_id, evaluator_id), ...]
                                         evaluator_id 可为 None（上级未指定时）
            template_id:                 使用的绩效模板 ID（可选，为 None 时使用简单算术均值）

        Returns:
            批量创建并 refresh 的评估记录列表
        """
        evaluations: list[PerformanceEvaluation] = []
        for employee_id, evaluator_id in employee_evaluator_pairs:
            evaluation = PerformanceEvaluation(
                employee_id=employee_id,
                cycle_id=cycle_id,
                template_id=template_id,
                evaluator_id=evaluator_id,
                status="pending_self",  # 初始状态：等待员工提交自评
            )
            db.add(evaluation)
            evaluations.append(evaluation)

        await db.flush()
        for ev in evaluations:
            await db.refresh(ev)
        return evaluations

    @staticmethod
    async def get(db: AsyncSession, evaluation_id: int) -> Optional[PerformanceEvaluation]:
        """按 ID 获取单条评估记录。

        Args:
            db:            异步数据库会话
            evaluation_id: 评估记录主键 ID

        Returns:
            PerformanceEvaluation 对象，不存在则返回 None
        """
        result = await db.execute(
            select(PerformanceEvaluation).where(PerformanceEvaluation.id == evaluation_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_cycle(
        db: AsyncSession,
        cycle_id: int,
        status: Optional[str] = None,
    ) -> list[PerformanceEvaluation]:
        """获取某考核周期下的所有评估记录（支持按状态过滤）。

        Args:
            db:       异步数据库会话
            cycle_id: 考核周期 ID
            status:   状态过滤（pending_self/pending_superior/completed），None 表示全部

        Returns:
            按 employee_id 升序排列的评估记录列表
        """
        query = select(PerformanceEvaluation).where(
            PerformanceEvaluation.cycle_id == cycle_id
        )
        if status:
            query = query.where(PerformanceEvaluation.status == status)
        query = query.order_by(PerformanceEvaluation.employee_id)
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def list_by_employee(
        db: AsyncSession, employee_id: int, year: Optional[int] = None
    ) -> list[PerformanceEvaluation]:
        """获取某员工的所有历史评估记录（支持按年份过滤）。

        可用于员工个人绩效档案页面。

        Args:
            db:          异步数据库会话
            employee_id: 员工 ID
            year:        年份过滤（通过 JOIN PerformanceCycle），None 表示全部年份

        Returns:
            按创建时间倒序排列的评估记录列表
        """
        query = select(PerformanceEvaluation).where(
            PerformanceEvaluation.employee_id == employee_id
        )
        if year:
            # 通过 JOIN 考核周期表按年份过滤
            query = query.join(PerformanceCycle).where(PerformanceCycle.year == year)
        query = query.order_by(PerformanceEvaluation.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def list_by_evaluator(
        db: AsyncSession, evaluator_id: int, cycle_id: Optional[int] = None
    ) -> list[PerformanceEvaluation]:
        """获取某评估人（上级/项目经理）待打分的评估列表。

        用于上级审批视图，展示需要本人打分的下属评估记录。

        Args:
            db:           异步数据库会话
            evaluator_id: 评估人（上级）的员工 ID
            cycle_id:     考核周期 ID 过滤，None 表示全部周期

        Returns:
            按状态排序的评估记录列表（pending_superior 优先显示）
        """
        query = select(PerformanceEvaluation).where(
            PerformanceEvaluation.evaluator_id == evaluator_id
        )
        if cycle_id:
            query = query.where(PerformanceEvaluation.cycle_id == cycle_id)
        query = query.order_by(PerformanceEvaluation.status)
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def submit_self_eval(
        db: AsyncSession, evaluation_id: int, data: SelfEvalSubmit
    ) -> Optional[PerformanceEvaluation]:
        """员工提交自评分数。

        业务规则：
        - 仅允许在 pending_self 状态下提交
        - self_scores_json 必须是合法的 JSON 对象格式（非数组）
        - 提交成功后状态流转为 pending_superior（等待上级评分）

        Args:
            db:            异步数据库会话
            evaluation_id: 待提交自评的评估记录 ID
            data:          包含 self_scores_json 字段的 Schema
                           格式示例: {"1": 85, "2": 90}（key为指标ID字符串）

        Returns:
            更新后的评估记录，不存在则返回 None

        Raises:
            ValueError: 状态不允许提交或 JSON 格式错误
        """
        evaluation = await PerformanceEvaluationService.get(db, evaluation_id)
        if not evaluation:
            return None
        if evaluation.status != "pending_self":
            raise ValueError("当前状态不允许提交自评")

        # 验证 self_scores_json 是合法的 JSON 对象（字典格式）
        try:
            scores = json.loads(data.self_scores_json)
            if not isinstance(scores, dict):
                raise ValueError("自评数据必须是JSON对象")
        except json.JSONDecodeError:
            raise ValueError("自评数据JSON格式错误")

        evaluation.self_scores_json = data.self_scores_json
        evaluation.status = "pending_superior"  # 流转到上级评分阶段
        await db.flush()
        await db.refresh(evaluation)
        return evaluation

    @staticmethod
    async def submit_superior_score(
        db: AsyncSession, evaluation_id: int, data: SuperiorScoreSubmit
    ) -> Optional[PerformanceEvaluation]:
        """上级提交评分并自动触发加权得分计算和等级映射。

        业务规则：
        - 仅允许在 pending_superior 状态下提交
        - 提交后自动调用 _calc_weighted_score() 计算加权得分
        - 自动调用 _map_grade() 根据得分映射等级（A/B/C/D）和绩效系数
        - 提交成功后状态流转为 completed（评估完成）

        Args:
            db:            异步数据库会话
            evaluation_id: 待评分的评估记录 ID
            data:          包含 superior_scores_json 字段的 Schema
                           格式与 self_scores_json 相同

        Returns:
            更新后的评估记录（含 weighted_score/grade/performance_coefficient），不存在则返回 None

        Raises:
            ValueError: 状态不允许提交或 JSON 格式错误
        """
        evaluation = await PerformanceEvaluationService.get(db, evaluation_id)
        if not evaluation:
            return None
        if evaluation.status != "pending_superior":
            raise ValueError("当前状态不允许上级评分")

        # 验证评分 JSON 格式
        try:
            superior_scores = json.loads(data.superior_scores_json)
            if not isinstance(superior_scores, dict):
                raise ValueError("评分数据必须是JSON对象")
        except json.JSONDecodeError:
            raise ValueError("评分数据JSON格式错误")

        evaluation.superior_scores_json = data.superior_scores_json

        # 触发加权得分计算（核心评分引擎）
        weighted_score = await PerformanceEvaluationService._calc_weighted_score(
            db, evaluation
        )
        evaluation.weighted_score = weighted_score

        # 根据加权得分映射绩效等级和薪资系数
        grade, coefficient = PerformanceEvaluationService._map_grade(weighted_score)
        evaluation.grade = grade
        evaluation.performance_coefficient = coefficient
        evaluation.status = "completed"  # 评估流程完结

        await db.flush()
        await db.refresh(evaluation)
        return evaluation

    @staticmethod
    async def _calc_weighted_score(
        db: AsyncSession, evaluation: PerformanceEvaluation
    ) -> Decimal:
        """计算员工最终加权得分（核心评分引擎）。

        计算逻辑（两种模式）：

        模式一：模板有指标（有 PerformanceIndicator 关联）
            1. 对每个指标，分别计算自评和上级评分的百分制得分：
               score_pct = score / max_score
            2. 各指标按权重加权求和（类似加权平均）：
               self_total = Σ(self_score_i / max_score_i × weight_i)
            3. 归一化到100分制：
               self_avg = (self_total / total_weight) × 100
            4. 最终加权（使用模板定义的权重，默认自评30%/上级70%）：
               weighted = self_avg × self_weight + superior_avg × superior_weight

        模式二：无模板指标（或无模板）
            - 对自评和上级评分的所有分值各自求算术均值
            - 再按默认权重（30%/70%）加权

        Args:
            db:         异步数据库会话（用于加载模板和指标）
            evaluation: 已填写 self_scores_json 和 superior_scores_json 的评估记录

        Returns:
            四舍五入保留2位小数的 Decimal 加权总分（0~100）

        Raises:
            ValueError: 指标总权重为0或指标满分值为0（会导致除零错误）
        """
        # 尝试加载关联的绩效模板
        template = None
        if evaluation.template_id:
            template = await PerformanceTemplateService.get(db, evaluation.template_id)

        # 获取自评/上级评分权重（优先使用模板配置，其次用默认值）
        self_weight = Decimal("0.30")      # 默认自评权重：30%
        superior_weight = Decimal("0.70")  # 默认上级评分权重：70%
        if template:
            self_weight = template.self_eval_weight
            superior_weight = template.superior_eval_weight

        # 解析 JSON 评分数据
        self_scores = json.loads(evaluation.self_scores_json or "{}")
        superior_scores = json.loads(evaluation.superior_scores_json or "{}")

        # 模式一：有模板指标，按指标权重加权计算
        if template and template.indicators:
            indicators = template.indicators
            total_weight = sum(ind.weight for ind in indicators)
            if total_weight == 0:
                raise ValueError("绩效模板指标总权重不能为0，请先配置指标权重")

            self_total = Decimal("0")
            superior_total = Decimal("0")
            for ind in indicators:
                ind_id_str = str(ind.id)
                self_score = Decimal(str(self_scores.get(ind_id_str, 0)))
                sup_score = Decimal(str(superior_scores.get(ind_id_str, 0)))
                # 将实际得分归一化为百分比，再乘以指标权重（加权求和）
                # 公式：得分贡献 = (实际分 / 满分) × 指标权重
                max_score = Decimal(str(ind.max_score)) if ind.max_score else Decimal("0")
                if max_score == 0:
                    raise ValueError(f"指标 '{ind.name}' 的满分值不能为0")
                self_total += (self_score / max_score) * ind.weight
                superior_total += (sup_score / max_score) * ind.weight

            # 归一化到100分制：(加权总和 / 总权重) × 100
            self_avg = _quantize((self_total / total_weight) * Decimal("100"))
            superior_avg = _quantize((superior_total / total_weight) * Decimal("100"))
        else:
            # 模式二：无模板指标，对所有分值求算术均值
            self_vals = [Decimal(str(v)) for v in self_scores.values()] if self_scores else [Decimal("0")]
            superior_vals = [Decimal(str(v)) for v in superior_scores.values()] if superior_scores else [Decimal("0")]
            self_avg = _quantize(sum(self_vals) / max(len(self_vals), 1))
            superior_avg = _quantize(sum(superior_vals) / max(len(superior_vals), 1))

        # 最终加权公式：weighted = self_avg × 自评权重 + superior_avg × 上级权重
        # 默认：weighted = self_avg × 0.30 + superior_avg × 0.70
        weighted = _quantize(self_avg * self_weight + superior_avg * superior_weight)
        return weighted

    @staticmethod
    def _map_grade(score: Decimal) -> tuple[str, Decimal]:
        """根据加权得分映射绩效等级和绩效系数（同步方法，无 DB 访问）。

        等级映射规则（使用 DEFAULT_GRADE_MAP，按 A→B→C→D 顺序匹配第一个满足的等级）：
            score ≥ 90 → A（绩效系数 1.30）
            score ≥ 75 → B（绩效系数 1.10）
            score ≥ 60 → C（绩效系数 1.00）
            score ≥  0 → D（绩效系数 0.80）

        绩效系数用于薪资计算：绩效工资 = 基准绩效工资 × 绩效系数

        Args:
            score: 加权得分（Decimal，0~100）

        Returns:
            tuple(等级字符串, 绩效系数 Decimal)，例如 ("A", Decimal("1.30"))
        """
        for grade in ["A", "B", "C", "D"]:
            min_score, coefficient = DEFAULT_GRADE_MAP[grade]
            if score >= min_score:
                return grade, coefficient
        # 兜底返回 D 级（理论上 score >= 0 时已能匹配 D，此行为安全兜底）
        return "D", DEFAULT_GRADE_MAP["D"][1]

    @staticmethod
    async def record_interview(
        db: AsyncSession, evaluation_id: int, data: InterviewRecordSubmit
    ) -> Optional[PerformanceEvaluation]:
        """记录绩效面谈结果（可在评估 completed 后任意时间调用）。

        绩效面谈是考核流程的可选步骤，HR 或上级可在评估完成后
        填写面谈记录和面谈日期，存档于评估记录中。

        Args:
            db:            异步数据库会话
            evaluation_id: 目标评估记录 ID
            data:          包含 interview_record（文本）和 interview_date（日期）的 Schema

        Returns:
            更新后的评估记录，不存在则返回 None
        """
        evaluation = await PerformanceEvaluationService.get(db, evaluation_id)
        if not evaluation:
            return None
        evaluation.interview_record = data.interview_record
        evaluation.interview_date = data.interview_date
        await db.flush()
        await db.refresh(evaluation)
        return evaluation


# ============================================================
# 360 Feedback
# ============================================================

class PerformanceFeedback360Service:
    """360反馈服务。"""

    @staticmethod
    async def create(
        db: AsyncSession,
        giver_employee_id: int,
        data: PerformanceFeedback360Create,
    ) -> PerformanceFeedback360:
        if giver_employee_id == data.target_employee_id:
            raise ValueError("不能给自己提交360反馈")

        from app.models.employee import Employee

        employee_result = await db.execute(
            select(Employee).where(
                Employee.id == data.target_employee_id,
                Employee.is_active == True,
            )
        )
        target_employee = employee_result.scalar_one_or_none()
        if not target_employee:
            raise ValueError("被评估员工不存在或已停用")

        average_score = _quantize(
            Decimal(
                data.work_ability + data.collaboration + data.initiative + data.attitude
            ) / Decimal("4")
        )

        feedback = PerformanceFeedback360(
            giver_employee_id=giver_employee_id,
            target_employee_id=data.target_employee_id,
            work_ability=data.work_ability,
            collaboration=data.collaboration,
            initiative=data.initiative,
            attitude=data.attitude,
            strengths=data.strengths,
            improvements=data.improvements,
            average_score=average_score,
        )
        db.add(feedback)
        await db.flush()
        await db.refresh(feedback)
        return feedback

    @staticmethod
    async def list_submitted(
        db: AsyncSession,
        giver_employee_id: int,
    ) -> list[PerformanceFeedback360]:
        result = await db.execute(
            select(PerformanceFeedback360)
            .where(PerformanceFeedback360.giver_employee_id == giver_employee_id)
            .order_by(PerformanceFeedback360.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_received(
        db: AsyncSession,
        target_employee_id: int,
    ) -> list[PerformanceFeedback360]:
        result = await db.execute(
            select(PerformanceFeedback360)
            .where(PerformanceFeedback360.target_employee_id == target_employee_id)
            .order_by(PerformanceFeedback360.created_at.desc())
        )
        return list(result.scalars().all())


# ============================================================
# Grade Distribution 强制分布校验
# 等级分布服务 — 统计考核周期内的等级分布并校验强制分布合规性
# ============================================================

class GradeDistributionService:
    """等级分布服务。

    强制分布（Forced Distribution / Bell Curve）是绩效考核中常见的管理工具，
    要求各等级人数占比必须符合预设比例，避免"人情分"导致的等级虚高。

    本公司默认强制分布目标（存储在 cycle.forced_distribution_json 中）：
        A ≤ 10%（优秀，稀缺）
        B ≤ 30%（良好）
        C ≈ 50%（合格，主体）
        D ≥ 10%（待改进，淘汰压力）

    允许 5% 的容差（即A级实际占比 ≤ 15% 仍合规）。
    """

    @staticmethod
    async def get_distribution(
        db: AsyncSession, cycle_id: int
    ) -> GradeDistribution:
        """统计某考核周期的等级分布（仅统计 completed 状态的评估）。

        Args:
            db:       异步数据库会话
            cycle_id: 考核周期 ID

        Returns:
            GradeDistribution Schema，包含各等级人数和占比

        Raises:
            ValueError: 考核周期不存在
        """
        cycle = await PerformanceCycleService.get(db, cycle_id)
        if not cycle:
            raise ValueError(f"考核周期{cycle_id}不存在")

        # 仅统计已完成的评估（completed 状态）
        evaluations = await PerformanceEvaluationService.list_by_cycle(
            db, cycle_id, status="completed"
        )

        # 按等级统计人数
        counts: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}
        for ev in evaluations:
            if ev.grade and ev.grade in counts:
                counts[ev.grade] += 1

        total = sum(counts.values())

        def pct(c: int) -> Decimal:
            """计算单个等级的百分比占比（总人数为0时返回0）。"""
            if total == 0:
                return Decimal("0")
            return _quantize(Decimal(str(c)) / Decimal(str(total)) * Decimal("100"))

        return GradeDistribution(
            cycle_id=cycle_id,
            cycle_name=cycle.name,
            grade_a_count=counts["A"],
            grade_b_count=counts["B"],
            grade_c_count=counts["C"],
            grade_d_count=counts["D"],
            total_count=total,
            grade_a_pct=pct(counts["A"]),
            grade_b_pct=pct(counts["B"]),
            grade_c_pct=pct(counts["C"]),
            grade_d_pct=pct(counts["D"]),
        )

    @staticmethod
    async def check_forced_distribution(
        db: AsyncSession, cycle_id: int
    ) -> ForcedDistributionCheck:
        """校验当前考核周期的等级分布是否符合强制分布规则。

        校验逻辑：
        1. 若周期未配置 forced_distribution_json，直接返回合规（is_compliant=True）
        2. 对每个等级，比对实际占比与目标占比
        3. 偏差超过 5%（容差）则记录为违规项
        4. 所有等级均在容差内则视为合规

        Args:
            db:       异步数据库会话
            cycle_id: 考核周期 ID

        Returns:
            ForcedDistributionCheck Schema，包含合规标志和违规详情列表

        Raises:
            ValueError: 考核周期不存在或 forced_distribution_json 格式错误
        """
        cycle = await PerformanceCycleService.get(db, cycle_id)
        if not cycle:
            raise ValueError(f"考核周期{cycle_id}不存在")

        distribution = await GradeDistributionService.get_distribution(db, cycle_id)

        # 未配置强制分布规则时，直接通过校验
        if not cycle.forced_distribution_json:
            return ForcedDistributionCheck(
                cycle_id=cycle_id,
                is_compliant=True,
                violations=[],
                current_distribution=distribution,
            )

        try:
            # 解析目标分布 JSON，格式：{"A": 10, "B": 30, "C": 50, "D": 10}（值为百分比）
            targets = json.loads(cycle.forced_distribution_json)
        except json.JSONDecodeError:
            raise ValueError("强制分布配置JSON格式错误")

        violations: list[str] = []
        grade_pcts = {
            "A": distribution.grade_a_pct,
            "B": distribution.grade_b_pct,
            "C": distribution.grade_c_pct,
            "D": distribution.grade_d_pct,
        }

        tolerance = Decimal("5")  # 5% 容差，在目标比例 ±5% 范围内均视为合规
        for grade, target_pct in targets.items():
            actual = grade_pcts.get(grade, Decimal("0"))
            target = Decimal(str(target_pct))
            # 实际占比超出目标占比 ±5% 时判定为违规
            if actual > target + tolerance or actual < target - tolerance:
                violations.append(
                    f"{grade}等级占比{actual}%超出目标{target}%(容差{tolerance}%)"
                )

        return ForcedDistributionCheck(
            cycle_id=cycle_id,
            is_compliant=len(violations) == 0,
            violations=violations,
            current_distribution=distribution,
        )


# ============================================================
# GoalService KPI/OKR 目标管理
# 目标服务 — 管理员工的 KPI 和 OKR 目标及完成情况
# ============================================================

class GoalService:
    """KPI/OKR 目标服务。

    目标（PerformanceGoal）支持树形结构：
    - 顶层目标（parent_id=None）：公司/部门级目标
    - 子目标（parent_id≠None）：个人级执行目标（KR - Key Results）

    目标属性：
    - goal_type：目标类型（okr/kpi）
    - title/description：目标标题和描述
    - weight：目标权重（用于加权汇总，所有顶层目标权重之和建议为100）
    - target_value：目标值（定量描述）
    - due_date：截止日期
    - status：目标状态（draft/in_progress/completed/cancelled）
    - score：目标得分（0~100，由上级打分）

    list_goals() 只返回顶层目标（parent_id IS NULL），
    子目标通过 ORM 关联的 sub_goals 字段加载。
    """

    @staticmethod
    async def list_goals(
        db: AsyncSession,
        employee_id: Optional[int] = None,
        cycle_id: Optional[int] = None,
        goal_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[PerformanceGoal]:
        """查询顶层目标列表（子目标通过关联关系 sub_goals 加载）。

        仅返回 parent_id IS NULL 的顶层目标，子目标作为树形结构挂载于父目标下。

        Args:
            db:          异步数据库会话
            employee_id: 按员工过滤，None 表示查全部员工
            cycle_id:    按考核周期过滤，None 表示查全部周期
            goal_type:   按目标类型过滤（"okr"/"kpi"），None 表示不过滤
            skip:        分页偏移量（从第 skip 条开始）
            limit:       分页大小（最多返回 limit 条）

        Returns:
            按创建时间倒序排列的顶层目标列表
        """
        query = select(PerformanceGoal).where(PerformanceGoal.parent_id == None)  # noqa: E711
        if employee_id is not None:
            query = query.where(PerformanceGoal.employee_id == employee_id)
        if cycle_id is not None:
            query = query.where(PerformanceGoal.cycle_id == cycle_id)
        if goal_type is not None:
            query = query.where(PerformanceGoal.goal_type == goal_type)
        query = query.order_by(PerformanceGoal.created_at.desc()).offset(skip).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def create_goal(db: AsyncSession, data: GoalCreate) -> PerformanceGoal:
        """创建目标（支持创建子目标，通过 parent_id 指定父目标）。

        新创建的目标初始状态为 draft（草稿），需由员工激活为 in_progress。

        Args:
            db:   异步数据库会话
            data: 目标创建 Schema（含 employee_id/cycle_id/goal_type/title/weight/
                  target_value/due_date/parent_id）

        Returns:
            已创建的 PerformanceGoal 对象，status=draft
        """
        goal = PerformanceGoal(
            employee_id=data.employee_id,
            cycle_id=data.cycle_id,
            goal_type=data.goal_type,
            title=data.title,
            description=data.description,
            weight=data.weight,
            target_value=data.target_value,
            due_date=data.due_date,
            parent_id=data.parent_id,
            status="draft",  # 初始状态：草稿
        )
        db.add(goal)
        await db.flush()
        await db.refresh(goal)
        return goal

    @staticmethod
    async def get_goal(db: AsyncSession, goal_id: int) -> Optional[PerformanceGoal]:
        """按 ID 获取单个目标。

        Args:
            db:      异步数据库会话
            goal_id: 目标主键 ID

        Returns:
            PerformanceGoal 对象，不存在则返回 None
        """
        result = await db.execute(
            select(PerformanceGoal).where(PerformanceGoal.id == goal_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_goal(
        db: AsyncSession, goal_id: int, data: GoalUpdate
    ) -> Optional[PerformanceGoal]:
        """更新目标（包括填写实际完成值、更新状态、打分）。

        常见更新场景：
        - 员工更新 actual_value（实际完成值）
        - 员工更改 status（draft → in_progress → completed）
        - 上级填写 score（0~100分评价完成质量）

        Args:
            db:      异步数据库会话
            goal_id: 目标 ID
            data:    包含待更新字段的 Schema（PATCH 语义）

        Returns:
            更新后的目标对象，不存在则返回 None
        """
        goal = await GoalService.get_goal(db, goal_id)
        if not goal:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(goal, field, value)
        await db.flush()
        await db.refresh(goal)
        return goal

    @staticmethod
    async def delete_goal(db: AsyncSession, goal_id: int) -> bool:
        """删除目标（级联删除子目标需在 DB 级配置 cascade）。

        Args:
            db:      异步数据库会话
            goal_id: 待删除的目标 ID

        Returns:
            True 表示删除成功，False 表示目标不存在
        """
        goal = await GoalService.get_goal(db, goal_id)
        if not goal:
            return False
        await db.delete(goal)
        await db.flush()
        return True

    @staticmethod
    async def get_goal_summary(db: AsyncSession, cycle_id: int) -> GoalSummary:
        """汇总某考核周期内各员工的目标完成情况（仅统计顶层目标）。

        计算维度：
        - total_goals：该员工顶层目标总数
        - completed_goals：status='completed' 的目标数
        - completion_rate：完成率 = completed / total × 100%
        - weighted_score：加权得分 = Σ(score × weight) / Σ(weight)
                         仅统计已打分（score is not None）的目标

        Args:
            db:       异步数据库会话
            cycle_id: 考核周期 ID

        Returns:
            GoalSummary Schema，包含所有员工的汇总列表
        """
        # 查询该周期下所有顶层目标（parent_id IS NULL）
        query = select(PerformanceGoal).where(
            and_(PerformanceGoal.cycle_id == cycle_id, PerformanceGoal.parent_id == None)  # noqa: E711
        )
        result = await db.execute(query)
        goals = list(result.scalars().all())

        # 按员工 ID 分组
        emp_goals: dict[int, list[PerformanceGoal]] = {}
        for g in goals:
            emp_goals.setdefault(g.employee_id, []).append(g)

        items: list[GoalSummaryItem] = []
        for employee_id, emp_goal_list in emp_goals.items():
            total = len(emp_goal_list)
            completed = sum(1 for g in emp_goal_list if g.status == "completed")
            # 完成率：已完成目标数 / 总目标数 × 100%
            completion_rate = round(completed / total * 100, 1) if total > 0 else 0.0

            # 加权得分：仅对已打分的目标计算
            # 公式：weighted_score = Σ(score_i × weight_i) / Σ(weight_i)
            scored = [g for g in emp_goal_list if g.score is not None]
            if scored:
                total_weight = sum(g.weight for g in scored)
                weighted_score = (
                    sum(g.score * g.weight for g in scored) / total_weight
                    if total_weight > 0 else None
                )
                weighted_score = round(weighted_score, 2) if weighted_score is not None else None
            else:
                weighted_score = None  # 尚无打分数据

            # 从关联关系获取员工姓名（避免额外 DB 查询）
            emp_name = ""
            if emp_goal_list and emp_goal_list[0].employee:
                emp = emp_goal_list[0].employee
                emp_name = getattr(emp, "name", "") or ""

            items.append(GoalSummaryItem(
                employee_id=employee_id,
                employee_name=emp_name,
                total_goals=total,
                completed_goals=completed,
                completion_rate=completion_rate,
                weighted_score=weighted_score,
            ))

        return GoalSummary(cycle_id=cycle_id, items=items)


# ============================================================
# ForceDistributionService 绩效强制分布计算
# 强制分布计算服务 — 按配置比例对已评估员工进行排名分档
# ============================================================

class ForceDistributionService:
    """强制分布计算服务。

    根据人为配置的分档比例（excellent/good/average/poor），
    对考核周期内已完成评估的员工按加权得分从高到低排名，
    计算出每位员工应分配到的等级，并与当前等级对比生成调整建议。

    用途：
    - 年度绩效校准会议（Calibration Meeting）前的数据准备
    - HR 和管理层可据此调整边界员工的最终等级

    注意：此方法只"建议"分档，不自动修改评估记录中的 grade 字段。
    管理层需要手动确认后，再通过 submit_superior_score 或单独的校准接口更新。
    """

    @staticmethod
    async def compute_force_distribution(
        db: AsyncSession, cycle_id: int, config: DistributionConfig
    ) -> ForceDistributionResult:
        """按配置比例对已评分员工进行强制分档，返回分档结果及调整建议。

        执行步骤：
        1. 校验各档位比例之和 = 100%（允许0.01%的浮点误差）
        2. 查询该周期所有 completed 状态且有 weighted_score 的评估记录
        3. 按 weighted_score 降序排列
        4. 按比例计算各档人数：
           - excellent_n = round(total × excellent_pct / 100)，最少1人
           - poor_n = round(total × poor_pct / 100)，最少1人
           - average_n = round(total × average_pct / 100)，最少1人
           - good_n = 剩余人数（确保总和 = total）
        5. 按顺序切片分档
        6. 对比每人当前等级与建议档位，生成调整建议列表

        Args:
            db:       异步数据库会话
            cycle_id: 考核周期 ID
            config:   分档配置 Schema（excellent_pct/good_pct/average_pct/poor_pct）

        Returns:
            ForceDistributionResult Schema，包含：
            - total：参与分档的员工总数
            - distribution：分档结果字典（excellent/good/average/poor → 员工列表）
            - adjustment_suggestions：建议调整的员工列表（当前等级≠建议档位时）

        Raises:
            ValueError: 各档位比例之和不等于100%
        """
        # 校验比例之和必须为100（容差0.01%，处理浮点精度问题）
        total_pct = config.excellent_pct + config.good_pct + config.average_pct + config.poor_pct
        if abs(total_pct - 100.0) > 0.01:
            raise ValueError(f"各档位比例之和必须为100%，当前合计 {total_pct:.1f}%")

        # 查询该周期所有已完成的评估记录
        evaluations = await PerformanceEvaluationService.list_by_cycle(
            db, cycle_id, status="completed"
        )
        # 过滤出有加权得分的记录（理论上 completed 状态都应有得分）
        scored = [ev for ev in evaluations if ev.weighted_score is not None]
        total = len(scored)

        # 无数据时返回空结果
        if total == 0:
            return ForceDistributionResult(
                cycle_id=cycle_id,
                total=0,
                distribution={"excellent": [], "good": [], "average": [], "poor": []},
                adjustment_suggestions=[],
            )

        # 按加权得分从高到低排序（排名靠前的优先进入 excellent 档）
        scored.sort(key=lambda ev: float(ev.weighted_score), reverse=True)

        # 计算各档位人数
        # 小团队（< 4人）无法满足每档至少1人的要求，改用简化分档策略
        if total < 4:
            # 小团队：第1名 excellent，最后1名 poor，中间人全部 good
            excellent_n = 1
            poor_n = 1 if total >= 2 else 0
            good_n = max(0, total - excellent_n - poor_n)
            average_n = 0
        else:
            # 正常团队：按比例计算，每档至少1人
            excellent_n = max(1, round(total * config.excellent_pct / 100))
            poor_n = max(1, round(total * config.poor_pct / 100))
            average_n = max(1, round(total * config.average_pct / 100))
            good_n = total - excellent_n - average_n - poor_n
            # 如果取整溢出导致 good_n 为负，从 average_n 中削减
            while good_n < 0 and average_n > 1:
                average_n -= 1
                good_n += 1
            if good_n < 0:
                good_n = 0

        # 按排名顺序切片分档（得分最高的进 excellent，最低的进 poor）
        excellent_list = scored[:excellent_n]
        good_list = scored[excellent_n:excellent_n + good_n]
        average_list = scored[excellent_n + good_n:excellent_n + good_n + average_n]
        poor_list = scored[excellent_n + good_n + average_n:]

        def _ev_to_dict(ev: PerformanceEvaluation, suggested_level: str) -> dict:
            """将评估记录转换为分档结果字典（含员工信息和建议档位）。"""
            emp_name = ""
            if ev.employee:
                emp_name = getattr(ev.employee, "name", "") or ""
            return {
                "employee_id": ev.employee_id,
                "employee_name": emp_name,
                "evaluation_id": ev.id,
                "weighted_score": float(ev.weighted_score),
                "current_grade": ev.grade,
                "suggested_level": suggested_level,
            }

        distribution = {
            "excellent": [_ev_to_dict(ev, "优秀") for ev in excellent_list],
            "good": [_ev_to_dict(ev, "良好") for ev in good_list],
            "average": [_ev_to_dict(ev, "一般") for ev in average_list],
            "poor": [_ev_to_dict(ev, "待改进") for ev in poor_list],
        }

        # 生成调整建议：对比每人的「当前等级」与「建议档位」，不一致的列为需关注
        grade_level_map = {"A": "优秀", "B": "良好", "C": "一般", "D": "待改进"}
        suggestions: list[dict] = []
        all_with_levels = (
            [(ev, "优秀") for ev in excellent_list]
            + [(ev, "良好") for ev in good_list]
            + [(ev, "一般") for ev in average_list]
            + [(ev, "待改进") for ev in poor_list]
        )
        for ev, suggested_level in all_with_levels:
            current_level = grade_level_map.get(ev.grade or "", "")
            if current_level != suggested_level:
                # 当前等级与强制分布建议档位不一致，纳入调整建议列表
                emp_name = ""
                if ev.employee:
                    emp_name = getattr(ev.employee, "name", "") or ""
                suggestions.append({
                    "employee_id": ev.employee_id,
                    "employee_name": emp_name,
                    "current_score": float(ev.weighted_score),
                    "current_grade": ev.grade,
                    "current_level": current_level,
                    "suggested_level": suggested_level,
                })

        return ForceDistributionResult(
            cycle_id=cycle_id,
            total=total,
            distribution=distribution,
            adjustment_suggestions=suggestions,
        )
