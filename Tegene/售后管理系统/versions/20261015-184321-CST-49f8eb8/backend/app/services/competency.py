"""
胜任力管理服务模块 - Competency Service
==========================================

**业务职责**：
    售后管理系统的胜任力管理核心服务层，负责：
    1. 胜任力模型（CompetencyModel）的全生命周期管理
    2. 能力项（CompetencyItem）的增删改查
    3. 岗位胜任力要求（PositionCompetency）的配置与查询
    4. 员工胜任力评估记录（EmployeeCompetency）的维护
    5. 差距分析（CompetencyGapService）：计算员工实际等级与岗位要求等级的差距

**核心数据结构**：
    - CompetencyModel：胜任力模型（如"技术序列通用模型"），包含多个能力项
    - CompetencyItem：具体能力项（如"Python编程"），每项定义1-5级的级别描述
    - PositionCompetency：岗位-能力项-要求等级的三元关系，带权重
    - EmployeeCompetency：员工某次评估的结果（assessed_level 1-5 + 评估人 + 评估日期）

**等级体系**：
    1=了解 / 2=初级 / 3=中级 / 4=高级 / 5=专家

**差距分析算法**（CompetencyGapService.analyze）：
    gap = required_level - assessed_level（正值表示不足，负值表示超出要求）
    - match_rate = 已达标权重 / 总权重 * 100（百分比）
    - total_gap_score = 加权差距之和 / 总权重（平均加权差距）

**模块间依赖**：
    - training.py 的 TrainingRecommendationService 依赖本模块的 CompetencyGapService
    - talent_profile.py 的 TalentProfileService 使用本模块数据构建雷达图

**技术说明**：
    FastAPI + SQLAlchemy 2.0 Async（asyncpg / PostgreSQL）+ Pydantic v2。
"""

from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.competency import (
    CompetencyModel,
    CompetencyItem,
    PositionCompetency,
    EmployeeCompetency,
)
from app.models.employee import Employee
from app.schemas.competency import (
    CompetencyModelCreate,
    CompetencyModelUpdate,
    CompetencyItemCreate,
    CompetencyItemUpdate,
    PositionCompetencyCreate,
    PositionCompetencyUpdate,
    EmployeeCompetencyCreate,
    EmployeeCompetencyUpdate,
    CompetencyGapItem,
    CompetencyGapAnalysis,
)


# ============================================================
# CompetencyModel CRUD
# ============================================================

class CompetencyModelService:
    """
    胜任力模型管理服务。

    胜任力模型是一组能力项的集合，通常按岗位序列或职级划分：
    - 技术序列通用模型（model_type="technical"）
    - 管理序列通用模型（model_type="management"）
    - 特定岗位模型（model_type="position"）

    创建模型时可同步创建其包含的能力项（data.items）。
    删除模型时会级联删除其所有能力项及关联的岗位要求配置。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: CompetencyModelCreate
    ) -> CompetencyModel:
        """
        创建胜任力模型，并同步写入初始能力项。

        业务流程：
            1. 创建 CompetencyModel 记录（flush 获取主键）；
            2. 遍历 data.items，逐条写入 CompetencyItem；
            3. 刷新返回完整的模型对象。

        参数:
            db (AsyncSession): 异步数据库会话
            data (CompetencyModelCreate): 模型创建 DTO
                - name: 模型名称
                - model_type: 模型类型（technical/management/position等）
                - description: 模型描述
                - items: 初始能力项列表（可为空）

        返回:
            CompetencyModel: 已持久化的模型对象（含 id 和关联的 items）
        """
        model = CompetencyModel(
            name=data.name,
            model_type=data.model_type,
            description=data.description,
        )
        db.add(model)
        await db.flush()  # 获取 model.id，供下方能力项写入使用

        # 同步创建模型包含的能力项
        for item_data in data.items:
            item = CompetencyItem(
                model_id=model.id,
                name=item_data.name,
                category=item_data.category,               # 能力项分类（如：技术能力/通用能力）
                description=item_data.description,
                level_definitions=item_data.level_definitions,  # 1-5级定义（JSON格式）
            )
            db.add(item)

        await db.flush()
        await db.refresh(model)
        return model

    @staticmethod
    async def get(db: AsyncSession, model_id: int) -> Optional[CompetencyModel]:
        """
        按 ID 查询单个胜任力模型。

        参数:
            db (AsyncSession): 异步数据库会话
            model_id (int): 模型主键 ID

        返回:
            CompetencyModel | None: 找到则返回，否则返回 None
        """
        result = await db.execute(
            select(CompetencyModel).where(CompetencyModel.id == model_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        model_type: Optional[str] = None,
    ) -> list[CompetencyModel]:
        """
        查询所有胜任力模型，可按模型类型过滤。

        参数:
            db (AsyncSession): 异步数据库会话
            model_type (str | None): 按模型类型精确过滤，None 则返回全部

        返回:
            list[CompetencyModel]: 模型列表，按创建时间倒序排列
        """
        query = select(CompetencyModel)
        if model_type:
            query = query.where(CompetencyModel.model_type == model_type)
        query = query.order_by(CompetencyModel.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, model_id: int, data: CompetencyModelUpdate
    ) -> Optional[CompetencyModel]:
        """
        更新胜任力模型基本信息（仅更新有传入的字段）。

        注意：此方法不更新模型下的能力项，能力项通过 CompetencyItemService 单独维护。

        参数:
            db (AsyncSession): 异步数据库会话
            model_id (int): 要更新的模型 ID
            data (CompetencyModelUpdate): 更新 DTO（使用 exclude_unset 局部更新）

        返回:
            CompetencyModel | None: 更新后的对象，或 None（不存在时）
        """
        model = await CompetencyModelService.get(db, model_id)
        if not model:
            return None
        update_data = data.model_dump(exclude_unset=True)  # 只更新前端明确传入的字段
        for field, value in update_data.items():
            setattr(model, field, value)
        await db.flush()
        await db.refresh(model)
        return model

    @staticmethod
    async def delete(db: AsyncSession, model_id: int) -> bool:
        """
        删除胜任力模型（物理删除，级联删除其所有能力项）。

        参数:
            db (AsyncSession): 异步数据库会话
            model_id (int): 要删除的模型 ID

        返回:
            bool: True=删除成功，False=模型不存在
        """
        model = await CompetencyModelService.get(db, model_id)
        if not model:
            return False
        await db.delete(model)
        await db.flush()
        return True


# ============================================================
# CompetencyItem CRUD
# ============================================================

class CompetencyItemService:
    """
    胜任力能力项管理服务。

    CompetencyItem 是胜任力模型的基本单元，代表一种具体能力（如"Python编程"、"项目管理"）。
    每个能力项属于某一胜任力模型，并定义了1-5级的级别描述（level_definitions）。

    能力项与岗位要求的关联通过 PositionCompetency 维护，
    与员工评估的关联通过 EmployeeCompetency 维护，
    与培训课程的关联通过 TrainingCourseCompetency 维护。
    """

    @staticmethod
    async def create(
        db: AsyncSession, model_id: int, data: CompetencyItemCreate
    ) -> CompetencyItem:
        """
        在指定胜任力模型下创建一个能力项。

        参数:
            db (AsyncSession): 异步数据库会话
            model_id (int): 所属胜任力模型 ID
            data (CompetencyItemCreate): 能力项创建 DTO
                - name: 能力名称（如"沟通能力"）
                - category: 分类（如"通用能力"/"技术能力"）
                - description: 能力描述
                - level_definitions: JSON，定义1-5级别的行为描述

        返回:
            CompetencyItem: 新建的能力项对象
        """
        item = CompetencyItem(model_id=model_id, **data.model_dump())
        db.add(item)
        await db.flush()
        await db.refresh(item)
        return item

    @staticmethod
    async def get(db: AsyncSession, item_id: int) -> Optional[CompetencyItem]:
        """
        按 ID 查询单个能力项。

        参数:
            db (AsyncSession): 异步数据库会话
            item_id (int): 能力项主键 ID

        返回:
            CompetencyItem | None: 找到则返回，否则返回 None
        """
        result = await db.execute(
            select(CompetencyItem).where(CompetencyItem.id == item_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_model(
        db: AsyncSession, model_id: int
    ) -> list[CompetencyItem]:
        """
        查询某个胜任力模型下的所有能力项，按分类+名称排序。

        参数:
            db (AsyncSession): 异步数据库会话
            model_id (int): 胜任力模型 ID

        返回:
            list[CompetencyItem]: 能力项列表，按 category → name 升序排列
        """
        result = await db.execute(
            select(CompetencyItem)
            .where(CompetencyItem.model_id == model_id)
            .order_by(CompetencyItem.category, CompetencyItem.name)
        )
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, item_id: int, data: CompetencyItemUpdate
    ) -> Optional[CompetencyItem]:
        """
        更新能力项信息（仅更新有传入的字段）。

        参数:
            db (AsyncSession): 异步数据库会话
            item_id (int): 要更新的能力项 ID
            data (CompetencyItemUpdate): 更新 DTO

        返回:
            CompetencyItem | None: 更新后对象，或 None（不存在时）
        """
        item = await CompetencyItemService.get(db, item_id)
        if not item:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(item, field, value)
        await db.flush()
        await db.refresh(item)
        return item

    @staticmethod
    async def delete(db: AsyncSession, item_id: int) -> bool:
        """
        删除能力项（物理删除）。

        注意：删除能力项会影响关联的岗位要求（PositionCompetency）和员工评估（EmployeeCompetency），
        需确认数据库级联策略，或在删除前检查关联数据。

        参数:
            db (AsyncSession): 异步数据库会话
            item_id (int): 要删除的能力项 ID

        返回:
            bool: True=删除成功，False=能力项不存在
        """
        item = await CompetencyItemService.get(db, item_id)
        if not item:
            return False
        await db.delete(item)
        await db.flush()
        return True


# ============================================================
# PositionCompetency CRUD
# ============================================================

class PositionCompetencyService:
    """
    岗位胜任力要求管理服务。

    PositionCompetency 定义了某岗位（position_name）对某能力项的要求等级（required_level）
    及权重（weight），构成岗位的胜任力要求矩阵。

    关键说明：
        - 同一岗位可关联多个能力项，每项有独立的权重和要求等级；
        - weight 字段用于差距分析加权计算（权重越高，该项差距影响越大）；
        - list_by_position 按权重倒序返回，权重高的能力项排在前面；
        - 同一岗位在不同部门可以有不同的能力要求（通过 department_id 区分）。

    被以下服务依赖：
        - CompetencyGapService.analyze()（差距分析核心）
        - TalentProfileService.get_radar_data()（雷达图数据）
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: PositionCompetencyCreate
    ) -> PositionCompetency:
        """
        创建岗位胜任力要求记录。

        参数:
            db (AsyncSession): 异步数据库会话
            data (PositionCompetencyCreate): 岗位胜任力要求 DTO
                - position_name: 岗位名称（如"高级工程师"）
                - department_id: 所属部门（可选，None 表示全公司通用）
                - competency_item_id: 关联能力项 ID
                - required_level: 该岗位要求的能力等级（1-5）
                - weight: 权重（默认1.0）

        返回:
            PositionCompetency: 新建的岗位要求对象
        """
        pc = PositionCompetency(**data.model_dump())
        db.add(pc)
        await db.flush()
        await db.refresh(pc)
        return pc

    @staticmethod
    async def get(db: AsyncSession, pc_id: int) -> Optional[PositionCompetency]:
        """
        按 ID 查询单条岗位胜任力要求。

        参数:
            db (AsyncSession): 异步数据库会话
            pc_id (int): 岗位胜任力要求主键 ID

        返回:
            PositionCompetency | None: 找到则返回，否则返回 None
        """
        result = await db.execute(
            select(PositionCompetency).where(PositionCompetency.id == pc_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_position(
        db: AsyncSession,
        position_name: str,
        department_id: Optional[int] = None,
    ) -> list[PositionCompetency]:
        """
        查询指定岗位的胜任力要求矩阵，按权重倒序排列。

        使用场景：差距分析（CompetencyGapService）和雷达图（TalentProfileService）都调用此方法。

        参数:
            db (AsyncSession): 异步数据库会话
            position_name (str): 岗位名称（精确匹配）
            department_id (int | None): 部门 ID，指定后只返回该部门的配置；
                                        None 则返回该岗位名称下所有配置

        返回:
            list[PositionCompetency]: 岗位胜任力要求列表，按 weight 倒序（重要能力项排前）
        """
        query = select(PositionCompetency).where(
            PositionCompetency.position_name == position_name
        )
        if department_id is not None:
            query = query.where(PositionCompetency.department_id == department_id)
        query = query.order_by(PositionCompetency.weight.desc())  # 权重高的排前面
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def list_positions(db: AsyncSession) -> list[str]:
        """
        获取所有已配置胜任力要求的岗位名称列表（去重）。

        用途：前端下拉框展示可选岗位，或批量差距分析时枚举所有岗位。

        参数:
            db (AsyncSession): 异步数据库会话

        返回:
            list[str]: 岗位名称列表，按字母/拼音升序排列
        """
        result = await db.execute(
            select(PositionCompetency.position_name)
            .distinct()
            .order_by(PositionCompetency.position_name)
        )
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, pc_id: int, data: PositionCompetencyUpdate
    ) -> Optional[PositionCompetency]:
        """
        更新岗位胜任力要求（局部更新）。

        常用场景：调整某岗位某能力项的要求等级或权重。

        参数:
            db (AsyncSession): 异步数据库会话
            pc_id (int): 要更新的记录 ID
            data (PositionCompetencyUpdate): 更新 DTO

        返回:
            PositionCompetency | None: 更新后对象，或 None（不存在时）
        """
        pc = await PositionCompetencyService.get(db, pc_id)
        if not pc:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(pc, field, value)
        await db.flush()
        await db.refresh(pc)
        return pc

    @staticmethod
    async def delete(db: AsyncSession, pc_id: int) -> bool:
        """
        删除岗位胜任力要求记录。

        参数:
            db (AsyncSession): 异步数据库会话
            pc_id (int): 要删除的记录 ID

        返回:
            bool: True=删除成功，False=记录不存在
        """
        pc = await PositionCompetencyService.get(db, pc_id)
        if not pc:
            return False
        await db.delete(pc)
        await db.flush()
        return True


# ============================================================
# EmployeeCompetency 评估 CRUD
# ============================================================

class EmployeeCompetencyService:
    """
    员工胜任力评估记录管理服务。

    EmployeeCompetency 记录某次评估中，某员工在某能力项上的实际等级（assessed_level）。
    同一员工同一能力项可以有多条历史评估记录（按 assessed_at 追踪成长轨迹）。

    差距分析时取每个能力项的最新一条评估（list_by_employee 返回按时间倒序排列后，
    CompetencyGapService 取 assessed_map 的首条不重复记录）。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: EmployeeCompetencyCreate
    ) -> EmployeeCompetency:
        """
        创建员工胜任力评估记录。

        参数:
            db (AsyncSession): 异步数据库会话
            data (EmployeeCompetencyCreate): 评估 DTO
                - employee_id: 被评估员工 ID
                - competency_item_id: 评估的能力项 ID
                - assessed_level: 实际能力等级（1-5）
                - assessor_id: 评估人 ID（可选）
                - assessed_at: 评估日期
                - comments: 评估备注

        返回:
            EmployeeCompetency: 新建的评估记录
        """
        ec = EmployeeCompetency(**data.model_dump())
        db.add(ec)
        await db.flush()
        await db.refresh(ec)
        return ec

    @staticmethod
    async def get(db: AsyncSession, ec_id: int) -> Optional[EmployeeCompetency]:
        """
        按 ID 查询单条评估记录。

        参数:
            db (AsyncSession): 异步数据库会话
            ec_id (int): 评估记录主键 ID

        返回:
            EmployeeCompetency | None: 找到则返回，否则返回 None
        """
        result = await db.execute(
            select(EmployeeCompetency).where(EmployeeCompetency.id == ec_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_employee(
        db: AsyncSession, employee_id: int
    ) -> list[EmployeeCompetency]:
        """
        查询某员工的所有胜任力评估历史，按评估时间倒序返回。

        重要：返回结果按 assessed_at 倒序，CompetencyGapService 使用此顺序
        通过 assessed_map 取每个能力项的最新评估（首次出现即为最新）。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 员工 ID

        返回:
            list[EmployeeCompetency]: 评估历史列表，最新评估在前
        """
        result = await db.execute(
            select(EmployeeCompetency)
            .where(EmployeeCompetency.employee_id == employee_id)
            .order_by(EmployeeCompetency.assessed_at.desc())  # 最新评估在前，供差距分析取最新值
        )
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, ec_id: int, data: EmployeeCompetencyUpdate
    ) -> Optional[EmployeeCompetency]:
        """
        更新员工胜任力评估记录（局部更新）。

        参数:
            db (AsyncSession): 异步数据库会话
            ec_id (int): 评估记录 ID
            data (EmployeeCompetencyUpdate): 更新 DTO

        返回:
            EmployeeCompetency | None: 更新后对象，或 None（不存在时）
        """
        ec = await EmployeeCompetencyService.get(db, ec_id)
        if not ec:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(ec, field, value)
        await db.flush()
        await db.refresh(ec)
        return ec

    @staticmethod
    async def delete(db: AsyncSession, ec_id: int) -> bool:
        """
        删除员工胜任力评估记录。

        参数:
            db (AsyncSession): 异步数据库会话
            ec_id (int): 要删除的评估记录 ID

        返回:
            bool: True=删除成功，False=记录不存在
        """
        ec = await EmployeeCompetencyService.get(db, ec_id)
        if not ec:
            return False
        await db.delete(ec)
        await db.flush()
        return True


# ============================================================
# Gap Analysis 差距分析
# ============================================================

class CompetencyGapService:
    """
    胜任力差距分析服务。

    核心功能：
        对比员工的实际胜任力等级（EmployeeCompetency）与岗位要求等级（PositionCompetency），
        计算每个能力项的差距，并汇总为整体匹配率和加权差距分数。

    算法说明：
        对每个岗位要求的能力项：
            gap = required_level - assessed_level
            （gap > 0 表示不足，gap = 0 表示达标，gap < 0 表示超出要求）

        整体指标：
            match_rate = 已达标能力项的权重之和 / 总权重 * 100（%）
            total_gap_score = 正向差距的加权均值（越小越好）

    被依赖关系：
        - training.TrainingRecommendationService.recommend_courses() 调用此服务，
          筛选 gap > 0 的能力项，推荐能弥补差距的培训课程。
        - TalentProfileService.get_radar_data() 使用相似逻辑生成雷达图数据。
    """

    @staticmethod
    async def analyze(
        db: AsyncSession, employee_id: int
    ) -> CompetencyGapAnalysis:
        """
        对指定员工进行胜任力差距分析。

        业务流程：
            1. 获取员工基本信息（姓名、岗位）；
            2. 查询该岗位的胜任力要求列表；
            3. 若无要求配置，直接返回空差距（match_rate=100%）；
            4. 获取员工所有历史评估，按时间倒序去重，取每项最新评估等级；
            5. 对每个要求项计算差距，汇总权重、加权差距、达标权重；
            6. 返回完整的差距分析对象。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 要分析的员工 ID

        返回:
            CompetencyGapAnalysis: 差距分析结果，包含：
                - employee_id / employee_name / position_name: 员工信息
                - items: 每个能力项的详细差距（CompetencyGapItem 列表）
                - total_gap_score: 总体加权差距分数（越小越好，0表示完全达标）
                - match_rate: 达标率百分比（100.0 = 完全达标）

        异常:
            ValueError: 员工不存在时抛出

        数据结构 CompetencyGapItem：
            - competency_item_id / competency_name / category: 能力项信息
            - required_level: 岗位要求等级（1-5）
            - assessed_level: 员工实际等级（0表示未评估）
            - gap: required_level - assessed_level（正值=不足）
            - weight: 该能力项在岗位要求中的权重
        """
        # 获取员工信息
        emp_result = await db.execute(
            select(Employee).where(Employee.id == employee_id)
        )
        emp = emp_result.scalar_one_or_none()
        if not emp:
            raise ValueError(f"员工{employee_id}不存在")

        position_name = emp.position or ""
        employee_name = emp.name

        # 获取该岗位的胜任力要求（按权重倒序）
        requirements = await PositionCompetencyService.list_by_position(
            db, position_name
        )
        if not requirements:
            # 岗位无胜任力配置，视为完全达标（不做差距分析）
            return CompetencyGapAnalysis(
                employee_id=employee_id,
                employee_name=employee_name,
                position_name=position_name,
                items=[],
                total_gap_score=0,
                match_rate=100.0,
            )

        # 获取员工的最新评估（每个能力项取最新一条）
        # list_by_employee 按 assessed_at 倒序，遍历时首次出现的即为最新
        assessments = await EmployeeCompetencyService.list_by_employee(db, employee_id)
        assessed_map: dict[int, int] = {}
        for a in assessments:
            if a.competency_item_id not in assessed_map:
                assessed_map[a.competency_item_id] = a.assessed_level  # 只保留最新值

        # 计算差距并汇总统计指标
        gap_items: list[CompetencyGapItem] = []
        total_weight = 0       # 所有能力项的权重之和
        weighted_gap = 0.0     # 正向差距的加权累计值（用于计算 total_gap_score）
        matched_weight = 0     # 已达标能力项的权重之和（gap <= 0 即达标）

        for req in requirements:
            item = req.competency_item
            # 未评估的能力项按 0 级处理（远低于任何要求等级）
            actual = assessed_map.get(req.competency_item_id, 0)
            gap = req.required_level - actual  # 正值=不足，0=达标，负值=超出

            gap_items.append(CompetencyGapItem(
                competency_item_id=req.competency_item_id,
                competency_name=item.name if item else "",
                category=item.category if item else "",
                required_level=req.required_level,
                assessed_level=actual,
                gap=gap,
                weight=req.weight,
            ))

            total_weight += req.weight
            weighted_gap += max(gap, 0) * req.weight  # 只累计正向差距（已超出不计入）
            if gap <= 0:
                matched_weight += req.weight  # gap <= 0 视为达标，累计达标权重

        # 计算整体指标（防止除零）
        match_rate = (matched_weight / total_weight * 100) if total_weight > 0 else 100.0
        total_gap_score = weighted_gap / total_weight if total_weight > 0 else 0

        return CompetencyGapAnalysis(
            employee_id=employee_id,
            employee_name=employee_name,
            position_name=position_name,
            items=gap_items,
            total_gap_score=round(total_gap_score, 2),
            match_rate=round(match_rate, 1),
        )
