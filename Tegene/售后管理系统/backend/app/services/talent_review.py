"""
人才盘点服务模块 - Talent Review Service
==========================================

业务职责：
    负责售后管理系统的人才盘点（Talent Review）全流程，包括：
    - 盘点会议（TalentReviewSession）的创建和生命周期管理
    - 员工九宫格评估记录（TalentReviewEntry）的录入和更新
    - 九宫格（Nine-Box Grid）分布数据的统计和展示
    - 继任计划（SuccessionPlan）的管理
    - 继任候选人（SuccessionCandidate）的识别和管理

核心概念——九宫格（Nine-Box Grid）：
    人才盘点的标准工具，以「绩效（Performance）」为 X 轴、「潜力（Potential）」为 Y 轴，
    将员工分布到 3×3 = 9 个象限中，用于识别高潜人才、关键人才和待发展员工。

    九宫格坐标系（position 格式："绩效评分-潜力评分"）：
                     潜力低(1)   潜力中(2)   潜力高(3)
        绩效高(3)  │  "3-1"   │  "3-2"   │  "3-3"  │ ← 高绩效/高潜力 = 明星人才（顶层目标）
        绩效中(2)  │  "2-1"   │  "2-2"   │  "2-3"  │ ← 绩效良好/高潜力 = 未来之星
        绩效低(1)  │  "1-1"   │  "1-2"   │  "1-3"  │ ← 低绩效/潜力待观察

    评分维度：
    - performance_rating（绩效评分）：1=低 / 2=中 / 3=高
    - potential_rating（潜力评分）：1=低 / 2=中 / 3=高
    - 组合成 nine_box_position：如 "3-3" 代表高绩效高潜力

业务流程：
    1. HR 创建盘点会议（TalentReviewSession），状态：draft
    2. 组织盘点会议，录入参会员工的九宫格评估（TalentReviewEntry）
    3. 激活会议（状态：draft → in_progress）
    4. 查询九宫格分布数据（NineBoxService.calculate_nine_box）
    5. 完成盘点会议（状态：in_progress → completed）
    6. 基于盘点结果制定继任计划（SuccessionPlan）和识别继任候选人（SuccessionCandidate）

盘点会议状态机：
    draft（草稿）→ in_progress（进行中）→ completed（已完成）
    已完成的会议不允许修改；只有草稿状态的会议可以删除。

与绩效服务的关联：
    TalentReviewEntryService.auto_populate_performance() 可从绩效评估（PerformanceEvaluation）
    自动读取员工的历史绩效等级，转换为 performance_rating（A→3, B→2, C/D→1），
    减少盘点录入的重复工作。

技术栈：
    FastAPI + SQLAlchemy 2.0 Async + PostgreSQL + Pydantic v2
"""

from typing import Optional

from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.talent_review import (
    TalentReviewSession,
    TalentReviewEntry,
    SuccessionPlan,
    SuccessionCandidate,
)
from app.models.performance import PerformanceEvaluation
from app.schemas.talent_review import (
    TalentReviewSessionCreate,
    TalentReviewSessionUpdate,
    TalentReviewSessionStatusTransition,
    TalentReviewEntryCreate,
    TalentReviewEntryUpdate,
    NineBoxData,
    NineBoxCell,
    SuccessionPlanCreate,
    SuccessionPlanUpdate,
    SuccessionCandidateCreate,
    SuccessionCandidateUpdate,
)

# ---------------------------------------------------------------------------
# 盘点会议状态流转规则
# draft（草稿，可编辑）→ in_progress（进行中）→ completed（已完成，只读）
# ---------------------------------------------------------------------------
SESSION_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["in_progress"],
    "in_progress": ["completed"],
    "completed": [],  # 终态，不允许任何转换
}

# 所有九宫格位置的完整列表（绩效1-3 × 潜力1-3 = 9个象限）
# 格式："绩效评分-潜力评分"，如 "1-1"（低绩效低潜力）到 "3-3"（高绩效高潜力）
ALL_NINE_BOX_POSITIONS: list[str] = [
    f"{p}-{pot}" for p in [1, 2, 3] for pot in [1, 2, 3]
]


def _calculate_nine_box_position(
    performance_rating: int, potential_rating: int
) -> str:
    """根据绩效评分和潜力评分计算九宫格象限位置字符串（同步工具函数）。

    位置格式："绩效评分-潜力评分"，例如：
    - "3-3"：高绩效高潜力（明星人才）
    - "1-1"：低绩效低潜力（需重点关注）
    - "2-3"：中等绩效高潜力（未来之星）

    Args:
        performance_rating: 绩效评分（1=低, 2=中, 3=高）
        potential_rating:   潜力评分（1=低, 2=中, 3=高）

    Returns:
        九宫格位置字符串（格式 "X-Y"）
    """
    return f"{performance_rating}-{potential_rating}"


# ============================================================
# TalentReviewSession CRUD
# 盘点会议服务 — 管理人才盘点会议的生命周期
# ============================================================

class TalentReviewSessionService:
    """盘点会议服务。

    TalentReviewSession 是一次完整人才盘点活动的容器，包含：
    - 会议名称、年份、盘点类型（review_type，如 annual/mid-year）
    - 状态机（draft → in_progress → completed）
    - 会议参与者（facilitator_id）和截止日期

    一次盘点会议可包含多条员工评估记录（TalentReviewEntry），
    通过 NineBoxService 聚合生成九宫格分布报告。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: TalentReviewSessionCreate
    ) -> TalentReviewSession:
        """创建盘点会议（初始状态为 draft）。

        Args:
            db:   异步数据库会话
            data: 会议创建 Schema（name/year/review_type/start_date/end_date/
                  facilitator_id 等）

        Returns:
            已创建的 TalentReviewSession ORM 对象，状态为 draft
        """
        session = TalentReviewSession(**data.model_dump())
        db.add(session)
        await db.flush()
        await db.refresh(session)
        return session

    @staticmethod
    async def get(
        db: AsyncSession, session_id: int
    ) -> Optional[TalentReviewSession]:
        """按 ID 获取单个盘点会议。

        Args:
            db:         异步数据库会话
            session_id: 盘点会议主键 ID

        Returns:
            TalentReviewSession 对象，不存在则返回 None
        """
        result = await db.execute(
            select(TalentReviewSession).where(
                TalentReviewSession.id == session_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        year: Optional[int] = None,
        review_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[TalentReviewSession]:
        """查询盘点会议列表，支持多维度过滤。

        Args:
            db:          异步数据库会话
            year:        按年份过滤（如 2024），None 表示不过滤
            review_type: 按盘点类型过滤（annual/mid-year 等），None 表示不过滤
            status:      按状态过滤（draft/in_progress/completed），None 表示不过滤

        Returns:
            按年份倒序、创建时间倒序排列的会议列表
        """
        query = select(TalentReviewSession)
        if year:
            query = query.where(TalentReviewSession.year == year)
        if review_type:
            query = query.where(TalentReviewSession.review_type == review_type)
        if status:
            query = query.where(TalentReviewSession.status == status)
        query = query.order_by(
            TalentReviewSession.year.desc(),
            TalentReviewSession.created_at.desc(),
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession,
        session_id: int,
        data: TalentReviewSessionUpdate,
    ) -> Optional[TalentReviewSession]:
        """更新盘点会议基本信息（已完成的会议不允许修改）。

        Args:
            db:         异步数据库会话
            session_id: 待更新的会议 ID
            data:       包含待更新字段的 Schema（PATCH 语义，exclude_unset）

        Returns:
            更新后的会议对象，不存在则返回 None

        Raises:
            ValueError: 会议状态为 completed 时拒绝修改
        """
        session = await TalentReviewSessionService.get(db, session_id)
        if not session:
            return None
        if session.status == "completed":
            raise ValueError("已完成的盘点会议不能修改")
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(session, field, value)
        await db.flush()
        await db.refresh(session)
        return session

    @staticmethod
    async def transition_status(
        db: AsyncSession,
        session_id: int,
        data: TalentReviewSessionStatusTransition,
    ) -> Optional[TalentReviewSession]:
        """执行盘点会议状态流转（严格按状态机规则）。

        状态机：draft → in_progress → completed（单向，不可逆）
        - draft → in_progress：正式启动盘点，开始录入员工评估
        - in_progress → completed：完成盘点，结果不再修改

        Args:
            db:         异步数据库会话
            session_id: 目标会议 ID
            data:       包含 target_status 的 Schema

        Returns:
            状态更新后的会议对象，不存在则返回 None

        Raises:
            ValueError: 目标状态不在当前状态允许的转换列表中
        """
        session = await TalentReviewSessionService.get(db, session_id)
        if not session:
            return None

        # 查找当前状态允许的目标状态列表
        allowed = SESSION_STATUS_TRANSITIONS.get(session.status, [])
        if data.target_status not in allowed:
            raise ValueError(
                f"不允许从 '{session.status}' 转换到 '{data.target_status}'。"
                f"允许的目标状态: {allowed}"
            )

        session.status = data.target_status
        await db.flush()
        await db.refresh(session)
        return session

    @staticmethod
    async def delete(db: AsyncSession, session_id: int) -> bool:
        """删除盘点会议（仅允许删除草稿状态的会议）。

        已启动（in_progress）或已完成（completed）的会议包含员工评估数据，
        不允许删除以保护历史记录。

        Args:
            db:         异步数据库会话
            session_id: 待删除的会议 ID

        Returns:
            True 表示删除成功，False 表示会议不存在

        Raises:
            ValueError: 会议状态不为 draft 时拒绝删除
        """
        session = await TalentReviewSessionService.get(db, session_id)
        if not session:
            return False
        if session.status != "draft":
            raise ValueError("只能删除草稿状态的盘点会议")
        await db.delete(session)
        await db.flush()
        return True


# ============================================================
# TalentReviewEntry CRUD
# 员工盘点记录服务 — 管理每位员工在盘点会议中的九宫格评估记录
# ============================================================

class TalentReviewEntryService:
    """员工盘点记录服务。

    TalentReviewEntry 记录一位员工在某次盘点会议中的评估结果，包含：
    - session_id：所属盘点会议
    - employee_id：被盘点的员工
    - performance_rating：绩效评分（1=低/2=中/3=高）
    - potential_rating：潜力评分（1=低/2=中/3=高）
    - nine_box_position：系统自动计算的九宫格位置（"绩效-潜力" 格式）
    - is_key_talent：是否为关键人才（布尔值）
    - flight_risk：离职风险（low/medium/high）
    - development_notes：发展建议

    nine_box_position 在创建和更新时均会自动重新计算，无需手动传入。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: TalentReviewEntryCreate
    ) -> TalentReviewEntry:
        """创建员工盘点记录（自动计算九宫格位置）。

        九宫格位置由 performance_rating 和 potential_rating 自动计算，
        无需调用方传入 nine_box_position 字段。

        Args:
            db:   异步数据库会话
            data: 盘点记录创建 Schema（session_id/employee_id/performance_rating/
                  potential_rating/is_key_talent/flight_risk/development_notes 等）

        Returns:
            已创建的 TalentReviewEntry ORM 对象（含自动计算的 nine_box_position）
        """
        entry = TalentReviewEntry(**data.model_dump())
        # 自动计算并写入九宫格位置（"绩效评分-潜力评分" 格式）
        entry.nine_box_position = _calculate_nine_box_position(
            entry.performance_rating, entry.potential_rating
        )
        db.add(entry)
        await db.flush()
        await db.refresh(entry)
        return entry

    @staticmethod
    async def get(
        db: AsyncSession, entry_id: int
    ) -> Optional[TalentReviewEntry]:
        """按 ID 获取单条盘点记录。

        Args:
            db:       异步数据库会话
            entry_id: 盘点记录主键 ID

        Returns:
            TalentReviewEntry 对象，不存在则返回 None
        """
        result = await db.execute(
            select(TalentReviewEntry).where(TalentReviewEntry.id == entry_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_session(
        db: AsyncSession, session_id: int
    ) -> list[TalentReviewEntry]:
        """获取某盘点会议下的所有员工评估记录。

        主要供 NineBoxService.calculate_nine_box() 调用，聚合生成九宫格分布。

        Args:
            db:         异步数据库会话
            session_id: 盘点会议 ID

        Returns:
            按 employee_id 升序排列的盘点记录列表
        """
        result = await db.execute(
            select(TalentReviewEntry)
            .where(TalentReviewEntry.session_id == session_id)
            .order_by(TalentReviewEntry.employee_id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_all(
        db: AsyncSession,
        session_id: Optional[int] = None,
        is_key_talent: Optional[bool] = None,
        flight_risk: Optional[str] = None,
    ) -> list[TalentReviewEntry]:
        """查询盘点记录列表，支持多条件过滤（跨会议查询）。

        可用于查询所有会议中被标记为关键人才或高离职风险的员工清单。

        Args:
            db:            异步数据库会话
            session_id:    按盘点会议过滤，None 表示查全部会议
            is_key_talent: 按关键人才标记过滤，None 表示不过滤
            flight_risk:   按离职风险过滤（"low"/"medium"/"high"），None 表示不过滤

        Returns:
            按创建时间倒序排列的盘点记录列表
        """
        query = select(TalentReviewEntry)
        if session_id:
            query = query.where(TalentReviewEntry.session_id == session_id)
        if is_key_talent is not None:
            query = query.where(TalentReviewEntry.is_key_talent == is_key_talent)
        if flight_risk:
            query = query.where(TalentReviewEntry.flight_risk == flight_risk)
        query = query.order_by(TalentReviewEntry.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, entry_id: int, data: TalentReviewEntryUpdate
    ) -> Optional[TalentReviewEntry]:
        """更新员工盘点记录（自动重新计算九宫格位置）。

        即使调用方只更新了 performance_rating 或 potential_rating 其中之一，
        nine_box_position 也会根据最新的两个评分值重新计算，确保数据一致性。

        Args:
            db:       异步数据库会话
            entry_id: 待更新的盘点记录 ID
            data:     包含待更新字段的 Schema（PATCH 语义）

        Returns:
            更新后的盘点记录对象（含重新计算的 nine_box_position），不存在则返回 None
        """
        entry = await TalentReviewEntryService.get(db, entry_id)
        if not entry:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(entry, field, value)
        # 无论是否修改了评分，统一重新计算九宫格位置（保证数据一致）
        entry.nine_box_position = _calculate_nine_box_position(
            entry.performance_rating, entry.potential_rating
        )
        await db.flush()
        await db.refresh(entry)
        return entry

    @staticmethod
    async def delete(db: AsyncSession, entry_id: int) -> bool:
        """删除员工盘点记录。

        Args:
            db:       异步数据库会话
            entry_id: 待删除的盘点记录 ID

        Returns:
            True 表示删除成功，False 表示记录不存在
        """
        entry = await TalentReviewEntryService.get(db, entry_id)
        if not entry:
            return False
        await db.delete(entry)
        await db.flush()
        return True

    @staticmethod
    async def auto_populate_performance(
        db: AsyncSession, session_id: int, employee_id: int
    ) -> Optional[int]:
        """从绩效评估历史自动映射绩效评分（辅助函数，减少录入重复工作）。

        查找该员工最近一次完成（completed）的绩效评估，
        根据绩效等级（grade）自动映射为九宫格的 performance_rating：
            A → 3（高绩效）
            B → 2（中等绩效）
            C → 1（低绩效）
            D → 1（低绩效，待改进）

        使用场景：HR 录入盘点记录时，系统自动预填 performance_rating，
        HR 只需关注 potential_rating 和其他定性评估即可。

        Args:
            db:          异步数据库会话
            session_id:  当前盘点会议 ID（暂未使用，保留供未来版本按周期过滤）
            employee_id: 目标员工 ID

        Returns:
            自动计算的 performance_rating（1/2/3），无历史绩效记录时返回 None
        """
        # 查询员工最近一次完成的绩效评估（按创建时间倒序取第一条）
        result = await db.execute(
            select(PerformanceEvaluation)
            .where(PerformanceEvaluation.employee_id == employee_id)
            .where(PerformanceEvaluation.status == "completed")
            .order_by(PerformanceEvaluation.created_at.desc())
        )
        evaluation = result.scalars().first()
        if not evaluation or not evaluation.grade:
            return None  # 无绩效历史记录，无法自动填充

        # 绩效等级到九宫格绩效评分的映射
        # A（优秀）→ 3（高），B（良好）→ 2（中），C/D（合格/待改进）→ 1（低）
        grade_to_rating = {"A": 3, "B": 2, "C": 1, "D": 1}
        return grade_to_rating.get(evaluation.grade, 2)  # 未知等级默认为 2（中等）


# ============================================================
# NineBox 九宫格
# 九宫格统计服务 — 聚合盘点记录生成九宫格分布数据
# ============================================================

class NineBoxService:
    """九宫格统计服务。

    将某次盘点会议中所有员工的评估记录，
    聚合到 3×3 九宫格的每个象限中，
    返回每个象限的人数和员工姓名列表。

    输出数据结构：
    NineBoxData（含 session_id 和 9 个 NineBoxCell）
    每个 NineBoxCell 包含：
        - position：象限坐标（"绩效-潜力" 格式，如 "3-3"）
        - count：该象限的员工人数
        - employee_names：该象限的员工姓名列表
    """

    @staticmethod
    async def calculate_nine_box(
        db: AsyncSession, session_id: int
    ) -> NineBoxData:
        """计算并返回某次盘点会议的九宫格分布数据。

        执行逻辑：
        1. 查询该会议下所有员工盘点记录（TalentReviewEntry）
        2. 初始化 9 个象限的员工列表（确保无数据的象限也出现在结果中）
        3. 遍历记录，读取 nine_box_position（若为空则实时计算），
           将员工姓名追加到对应象限
        4. 构建 NineBoxCell 列表并返回

        注意：员工姓名通过 entry.employee（关联关系）获取，
        调用前须确保该关联已加载（由 SQLAlchemy lazy/eager loading 处理）。

        Args:
            db:         异步数据库会话
            session_id: 盘点会议 ID

        Returns:
            NineBoxData Schema，包含 9 个象限的分布数据（含空象限）
        """
        entries = await TalentReviewEntryService.list_by_session(db, session_id)

        # 初始化所有 9 个象限为空列表（确保结果始终包含全部 9 个格子）
        box_data: dict[str, list[str]] = {pos: [] for pos in ALL_NINE_BOX_POSITIONS}

        for entry in entries:
            # 优先使用已存储的位置，若为空则实时计算（理论上创建时已自动计算）
            pos = entry.nine_box_position or _calculate_nine_box_position(
                entry.performance_rating, entry.potential_rating
            )
            if pos in box_data:
                # 通过关联关系获取员工姓名（需 SQLAlchemy 关联已加载）
                employee_name = ""
                if entry.employee:
                    employee_name = entry.employee.name
                box_data[pos].append(employee_name)

        # 将字典转换为 NineBoxCell 列表
        boxes = [
            NineBoxCell(
                position=pos,
                count=len(names),
                employee_names=names,
            )
            for pos, names in box_data.items()
        ]

        return NineBoxData(session_id=session_id, boxes=boxes)


# ============================================================
# SuccessionPlan CRUD
# 继任计划服务 — 管理关键岗位的继任候选人规划
# ============================================================

class SuccessionPlanService:
    """继任计划服务。

    SuccessionPlan（继任计划）针对某个关键岗位（position_id/position_title）制定接班人方案，包含：
    - 关键程度（criticality：critical/important/standard）
    - 计划状态（status：active/inactive/completed）
    - 关联的继任候选人列表（SuccessionCandidate）

    SuccessionCandidate（继任候选人）记录：
    - 候选员工（candidate_id）
    - 接班准备度（readiness：ready_now/ready_1_2_years/ready_3_5_years）
    - 发展行动（development_actions）

    通常基于九宫格盘点结果（高绩效+高潜力员工）来识别候选人。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: SuccessionPlanCreate
    ) -> SuccessionPlan:
        """创建继任计划。

        Args:
            db:   异步数据库会话
            data: 继任计划创建 Schema（position_id/position_title/criticality/
                  current_holder_id/status 等）

        Returns:
            已创建的 SuccessionPlan ORM 对象
        """
        plan = SuccessionPlan(**data.model_dump())
        db.add(plan)
        await db.flush()
        await db.refresh(plan)
        return plan

    @staticmethod
    async def get(
        db: AsyncSession, plan_id: int
    ) -> Optional[SuccessionPlan]:
        """按 ID 获取单个继任计划。

        Args:
            db:      异步数据库会话
            plan_id: 继任计划主键 ID

        Returns:
            SuccessionPlan 对象，不存在则返回 None
        """
        result = await db.execute(
            select(SuccessionPlan).where(SuccessionPlan.id == plan_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        status: Optional[str] = None,
        criticality: Optional[str] = None,
    ) -> list[SuccessionPlan]:
        """查询继任计划列表，支持按状态和关键程度过滤。

        Args:
            db:          异步数据库会话
            status:      按状态过滤（active/inactive/completed），None 表示不过滤
            criticality: 按关键程度过滤（critical/important/standard），None 表示不过滤

        Returns:
            按创建时间倒序排列的继任计划列表
        """
        query = select(SuccessionPlan)
        if status:
            query = query.where(SuccessionPlan.status == status)
        if criticality:
            query = query.where(SuccessionPlan.criticality == criticality)
        query = query.order_by(SuccessionPlan.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, plan_id: int, data: SuccessionPlanUpdate
    ) -> Optional[SuccessionPlan]:
        """更新继任计划基本信息（PATCH 语义）。

        Args:
            db:      异步数据库会话
            plan_id: 待更新的计划 ID
            data:    包含待更新字段的 Schema

        Returns:
            更新后的计划对象，不存在则返回 None
        """
        plan = await SuccessionPlanService.get(db, plan_id)
        if not plan:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(plan, field, value)
        await db.flush()
        await db.refresh(plan)
        return plan

    @staticmethod
    async def delete(db: AsyncSession, plan_id: int) -> bool:
        """删除继任计划（级联删除关联的候选人记录需在 DB 层配置）。

        Args:
            db:      异步数据库会话
            plan_id: 待删除的计划 ID

        Returns:
            True 表示删除成功，False 表示计划不存在
        """
        plan = await SuccessionPlanService.get(db, plan_id)
        if not plan:
            return False
        await db.delete(plan)
        await db.flush()
        return True

    # ---- 继任候选人管理 ----

    @staticmethod
    async def add_candidate(
        db: AsyncSession, plan_id: int, data: SuccessionCandidateCreate
    ) -> SuccessionCandidate:
        """向继任计划添加候选人。

        候选人通常从九宫格盘点结果中识别（高绩效+高潜力的员工），
        也可由 HR 手动添加。

        Args:
            db:      异步数据库会话
            plan_id: 目标继任计划 ID
            data:    候选人创建 Schema（candidate_id/readiness/priority/
                     development_actions 等）

        Returns:
            已创建的 SuccessionCandidate ORM 对象
        """
        candidate = SuccessionCandidate(
            plan_id=plan_id,
            **data.model_dump(),
        )
        db.add(candidate)
        await db.flush()
        await db.refresh(candidate)
        return candidate

    @staticmethod
    async def get_candidate(
        db: AsyncSession, candidate_id: int
    ) -> Optional[SuccessionCandidate]:
        """按 ID 获取单个继任候选人记录。

        Args:
            db:           异步数据库会话
            candidate_id: 候选人记录主键 ID

        Returns:
            SuccessionCandidate 对象，不存在则返回 None
        """
        result = await db.execute(
            select(SuccessionCandidate).where(
                SuccessionCandidate.id == candidate_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_candidate(
        db: AsyncSession,
        candidate_id: int,
        data: SuccessionCandidateUpdate,
    ) -> Optional[SuccessionCandidate]:
        """更新继任候选人信息（PATCH 语义）。

        常见更新场景：调整接班准备度（readiness）、更新发展行动计划（development_actions）。

        Args:
            db:           异步数据库会话
            candidate_id: 待更新的候选人记录 ID
            data:         包含待更新字段的 Schema

        Returns:
            更新后的候选人记录，不存在则返回 None
        """
        candidate = await SuccessionPlanService.get_candidate(db, candidate_id)
        if not candidate:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(candidate, field, value)
        await db.flush()
        await db.refresh(candidate)
        return candidate

    @staticmethod
    async def remove_candidate(
        db: AsyncSession, candidate_id: int
    ) -> bool:
        """从继任计划中移除候选人记录（物理删除）。

        Args:
            db:           异步数据库会话
            candidate_id: 待移除的候选人记录 ID

        Returns:
            True 表示移除成功，False 表示记录不存在
        """
        candidate = await SuccessionPlanService.get_candidate(db, candidate_id)
        if not candidate:
            return False
        await db.delete(candidate)
        await db.flush()
        return True
