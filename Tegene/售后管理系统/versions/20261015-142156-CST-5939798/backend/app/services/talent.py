"""
人才发展服务模块 - Talent Development Service
================================================

**业务职责**：
    售后管理系统的人才发展核心服务层，负责：
    1. 职业发展通道（CareerPath）的管理与展示
    2. 个人发展计划（IDP - Individual Development Plan）的全生命周期管理
    3. IDP 目标（IDPGoal）的增删改查及进度跟踪
    4. 员工证书（EmployeeCertification）的添加、更新与到期预警
    5. 员工技能（EmployeeSkill）的维护及部门技能矩阵汇总

**核心数据结构**：
    - CareerPath：职业发展通道定义（如技术通道 P1-P7、管理通道 M1-M5）
    - IDPPlan：个人发展计划（员工 + 目标周期 + 状态）
    - IDPGoal：IDP 下的具体发展目标（每条目标有进度百分比）
    - EmployeeCertification：员工持有的证书（含有效期、状态）
    - EmployeeSkill：员工技能评级（由 HR/系统评定，区别于员工自填的个人技能）

**职业通道体系**：
    - 技术通道：P1（助理工程师）→ P7（首席专家），path_type="technical"
    - 管理通道：M1（主管）→ M5（VP），path_type="management"
    - 双通道：员工可在技术通道和管理通道间横向转换

**IDP 状态机**：
    draft（草稿）→ active（进行中）→ completed（已完成）| cancelled（已取消）
    - 只有草稿或已取消的 IDP 允许删除
    - 状态流转由 IDP_STATUS_TRANSITIONS 规则控制

**证书到期预警**：
    CertificationService.get_expiring(days_ahead=30) 返回未来30天内到期的证书，
    用于提醒员工和 HR 及时续期，保持合规。

**技能矩阵**：
    SkillService.get_skill_matrix() 按部门聚合技能分布：
    每个部门、每种技能的拥有人数 + 平均熟练度，用于识别团队能力短板。

**模块间依赖**：
    - EmployeeSkill 模型来自 app.models.talent_profile（与人才画像共享）
    - 证书数据可与 training 模块的 ExamAttempt.certificate_no 联动
    - 技能数据被 talent_profile.TalentProfileService 用于构建人才画像

**技术说明**：
    FastAPI + SQLAlchemy 2.0 Async（asyncpg / PostgreSQL）+ Pydantic v2。
"""

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.talent import (
    CareerPath,
    IDPPlan,
    IDPGoal,
    EmployeeCertification,
)
from app.models.talent_profile import EmployeeSkill
from app.models.employee import Employee
from app.schemas.talent import (
    CareerPathCreate,
    CareerPathUpdate,
    IDPPlanCreate,
    IDPPlanUpdate,
    IDPStatusTransition,
    IDPGoalCreate,
    IDPGoalUpdate,
    EmployeeCertificationCreate,
    EmployeeCertificationUpdate,
    EmployeeSkillCreate,
    EmployeeSkillUpdate,
    SkillMatrixEntry,
    SkillMatrixSkillItem,
)


# IDP 状态流转规则
# 说明：key 为当前状态，value 为允许跳转的目标状态列表
# - draft（草稿）：只能激活为 active
# - active（进行中）：可以完成或取消
# - completed / cancelled：终态，不可再流转
IDP_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["active"],
    "active": ["completed", "cancelled"],
    "completed": [],
    "cancelled": [],
}


# ============================================================
# CareerPath CRUD
# ============================================================

class CareerPathService:
    """
    职业发展通道管理服务。

    CareerPath 定义了公司的职级晋升路径，包括：
    - 通道类型（path_type）：technical（技术）/ management（管理）/ dual（双通道）
    - 通道内各级别的描述（如P1-P7的能力要求和薪酬范围）

    职业通道是员工制定 IDP 和制定晋升目标的参照框架，
    也是人才盘点中判断潜力方向的依据。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: CareerPathCreate
    ) -> CareerPath:
        """
        创建职业发展通道。

        参数:
            db (AsyncSession): 异步数据库会话
            data (CareerPathCreate): 通道创建 DTO
                - name: 通道名称（如"技术序列"）
                - path_type: 类型（technical/management/dual）
                - description: 通道描述
                - levels: JSON，定义各级别的名称和要求

        返回:
            CareerPath: 新建的职业通道对象
        """
        career_path = CareerPath(**data.model_dump())
        db.add(career_path)
        await db.flush()
        await db.refresh(career_path)
        return career_path

    @staticmethod
    async def get(db: AsyncSession, path_id: int) -> Optional[CareerPath]:
        """
        按 ID 查询单条职业发展通道。

        参数:
            db (AsyncSession): 异步数据库会话
            path_id (int): 通道主键 ID

        返回:
            CareerPath | None: 找到则返回，否则返回 None
        """
        result = await db.execute(
            select(CareerPath).where(CareerPath.id == path_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        path_type: Optional[str] = None,
    ) -> list[CareerPath]:
        """
        查询所有职业发展通道，可按通道类型过滤。

        参数:
            db (AsyncSession): 异步数据库会话
            path_type (str | None): 通道类型过滤（technical/management/dual），None 返回全部

        返回:
            list[CareerPath]: 通道列表，按创建时间倒序排列
        """
        query = select(CareerPath)
        if path_type:
            query = query.where(CareerPath.path_type == path_type)
        query = query.order_by(CareerPath.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, path_id: int, data: CareerPathUpdate
    ) -> Optional[CareerPath]:
        """
        更新职业发展通道信息（局部更新）。

        参数:
            db (AsyncSession): 异步数据库会话
            path_id (int): 要更新的通道 ID
            data (CareerPathUpdate): 更新 DTO

        返回:
            CareerPath | None: 更新后对象，或 None（不存在时）
        """
        career_path = await CareerPathService.get(db, path_id)
        if not career_path:
            return None
        update_data = data.model_dump(exclude_unset=True)  # 仅更新前端传入的字段
        for field, value in update_data.items():
            setattr(career_path, field, value)
        await db.flush()
        await db.refresh(career_path)
        return career_path

    @staticmethod
    async def delete(db: AsyncSession, path_id: int) -> bool:
        """
        删除职业发展通道（物理删除）。

        参数:
            db (AsyncSession): 异步数据库会话
            path_id (int): 要删除的通道 ID

        返回:
            bool: True=删除成功，False=通道不存在
        """
        career_path = await CareerPathService.get(db, path_id)
        if not career_path:
            return False
        await db.delete(career_path)
        await db.flush()
        return True


# ============================================================
# IDPPlan CRUD + Status Transition
# ============================================================

class IDPService:
    """
    个人发展计划（IDP）管理服务。

    IDP（Individual Development Plan）是员工与直属管理者共同制定的职业成长计划，
    包含：
    - 发展目标描述（target_position、focus_areas）
    - 计划周期（start_date → end_date）
    - 状态（草稿 → 进行中 → 完成/取消）
    - 关联的具体目标列表（IDPGoal）

    服务同时管理 IDPGoal（每条目标有独立进度 progress_pct 和状态）。

    状态流转遵循 IDP_STATUS_TRANSITIONS 规则：
        - draft → active：发布计划，开始跟踪
        - active → completed：所有目标完成后收尾
        - active → cancelled：计划中途取消
        - 只有 draft/cancelled 状态的 IDP 允许删除
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: IDPPlanCreate
    ) -> IDPPlan:
        """
        创建个人发展计划（初始状态为 draft）。

        参数:
            db (AsyncSession): 异步数据库会话
            data (IDPPlanCreate): IDP 创建 DTO
                - employee_id: 制定计划的员工 ID
                - target_position: 目标职级/岗位
                - focus_areas: 重点发展方向列表（JSON）
                - start_date / end_date: 计划周期

        返回:
            IDPPlan: 新建的 IDP 对象，状态为 draft
        """
        plan = IDPPlan(**data.model_dump())
        db.add(plan)
        await db.flush()
        await db.refresh(plan)
        return plan

    @staticmethod
    async def get(db: AsyncSession, plan_id: int) -> Optional[IDPPlan]:
        """
        按 ID 查询单个 IDP 计划。

        参数:
            db (AsyncSession): 异步数据库会话
            plan_id (int): IDP 计划主键 ID

        返回:
            IDPPlan | None: 找到则返回，否则返回 None
        """
        result = await db.execute(
            select(IDPPlan).where(IDPPlan.id == plan_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        employee_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> list[IDPPlan]:
        """
        查询 IDP 计划列表，可按员工和状态过滤。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int | None): 按员工过滤，None 返回全部员工的 IDP
            status (str | None): 按状态过滤（draft/active/completed/cancelled）

        返回:
            list[IDPPlan]: IDP 列表，按创建时间倒序排列
        """
        query = select(IDPPlan)
        if employee_id is not None:
            query = query.where(IDPPlan.employee_id == employee_id)
        if status:
            query = query.where(IDPPlan.status == status)
        query = query.order_by(IDPPlan.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, plan_id: int, data: IDPPlanUpdate
    ) -> Optional[IDPPlan]:
        """
        更新 IDP 计划基本信息（局部更新，不含目标和状态流转）。

        参数:
            db (AsyncSession): 异步数据库会话
            plan_id (int): 要更新的 IDP ID
            data (IDPPlanUpdate): 更新 DTO

        返回:
            IDPPlan | None: 更新后对象，或 None（不存在时）
        """
        plan = await IDPService.get(db, plan_id)
        if not plan:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(plan, field, value)
        await db.flush()
        await db.refresh(plan)
        return plan

    @staticmethod
    async def transition_status(
        db: AsyncSession, plan_id: int, data: IDPStatusTransition
    ) -> Optional[IDPPlan]:
        """
        执行 IDP 计划的状态流转。

        业务规则：
            合法流转路径由 IDP_STATUS_TRANSITIONS 定义，非法流转抛出 ValueError。
            例如：已完成的 IDP 不允许重新激活。

        参数:
            db (AsyncSession): 异步数据库会话
            plan_id (int): IDP 计划 ID
            data (IDPStatusTransition): 含目标状态（target_status）的请求 DTO

        返回:
            IDPPlan | None: 状态更新后的 IDP 对象，或 None（不存在时）

        异常:
            ValueError: 目标状态不在允许的流转列表中
        """
        plan = await IDPService.get(db, plan_id)
        if not plan:
            return None
        allowed = IDP_STATUS_TRANSITIONS.get(plan.status, [])
        if data.target_status not in allowed:
            raise ValueError(
                f"不允许从 '{plan.status}' 转换到 '{data.target_status}'。"
                f"允许的目标状态: {allowed}"
            )
        plan.status = data.target_status
        await db.flush()
        await db.refresh(plan)
        return plan

    @staticmethod
    async def delete(db: AsyncSession, plan_id: int) -> bool:
        """
        删除 IDP 计划（仅允许删除草稿或已取消的计划）。

        业务规则：
            进行中或已完成的计划不可删除，以保留完整的发展历史记录。

        参数:
            db (AsyncSession): 异步数据库会话
            plan_id (int): 要删除的 IDP ID

        返回:
            bool: True=删除成功，False=计划不存在

        异常:
            ValueError: 计划状态不在 draft/cancelled 时抛出
        """
        plan = await IDPService.get(db, plan_id)
        if not plan:
            return False
        if plan.status not in ("draft", "cancelled"):
            raise ValueError("只能删除草稿或已取消的发展计划")
        await db.delete(plan)
        await db.flush()
        return True

    # ---- IDP Goals ----

    @staticmethod
    async def create_goal(
        db: AsyncSession, idp_id: int, data: IDPGoalCreate
    ) -> IDPGoal:
        """
        在 IDP 计划中创建一条具体发展目标。

        每条 IDPGoal 包含：
        - 目标描述（goal_text）
        - 目标类型（如：培训/实践/辅导）
        - 进度百分比（progress_pct 0-100）
        - 预计完成日期（due_date）
        - 状态（not_started / in_progress / completed）

        参数:
            db (AsyncSession): 异步数据库会话
            idp_id (int): 所属 IDP 计划 ID
            data (IDPGoalCreate): 目标创建 DTO

        返回:
            IDPGoal: 新建的 IDP 目标对象
        """
        goal = IDPGoal(idp_id=idp_id, **data.model_dump())
        db.add(goal)
        await db.flush()
        await db.refresh(goal)
        return goal

    @staticmethod
    async def get_goal(db: AsyncSession, goal_id: int) -> Optional[IDPGoal]:
        """
        按 ID 查询单条 IDP 目标。

        参数:
            db (AsyncSession): 异步数据库会话
            goal_id (int): 目标主键 ID

        返回:
            IDPGoal | None: 找到则返回，否则返回 None
        """
        result = await db.execute(
            select(IDPGoal).where(IDPGoal.id == goal_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_goals(
        db: AsyncSession, idp_id: int
    ) -> list[IDPGoal]:
        """
        查询某 IDP 计划下的所有目标，按创建时间升序排列（目标自然顺序）。

        参数:
            db (AsyncSession): 异步数据库会话
            idp_id (int): IDP 计划 ID

        返回:
            list[IDPGoal]: 目标列表，按创建时间升序（创建顺序即展示顺序）
        """
        result = await db.execute(
            select(IDPGoal)
            .where(IDPGoal.idp_id == idp_id)
            .order_by(IDPGoal.created_at)  # 按创建时间升序，保持目标的自然添加顺序
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_goal(
        db: AsyncSession, goal_id: int, data: IDPGoalUpdate
    ) -> Optional[IDPGoal]:
        """
        更新 IDP 目标信息（常用于更新进度 progress_pct 和状态）。

        参数:
            db (AsyncSession): 异步数据库会话
            goal_id (int): 要更新的目标 ID
            data (IDPGoalUpdate): 更新 DTO（含进度、状态等）

        返回:
            IDPGoal | None: 更新后对象，或 None（不存在时）
        """
        goal = await IDPService.get_goal(db, goal_id)
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
        """
        删除 IDP 目标。

        参数:
            db (AsyncSession): 异步数据库会话
            goal_id (int): 要删除的目标 ID

        返回:
            bool: True=删除成功，False=目标不存在
        """
        goal = await IDPService.get_goal(db, goal_id)
        if not goal:
            return False
        await db.delete(goal)
        await db.flush()
        return True


# ============================================================
# EmployeeCertification CRUD + Expiring Query
# ============================================================

class CertificationService:
    """
    员工证书管理服务。

    EmployeeCertification 记录员工持有的各类职业证书，包含：
    - 证书基本信息（cert_name、cert_type、cert_no）
    - 获得日期（issue_date）
    - 有效期（expiry_date）
    - 状态（valid=有效 / expired=已过期 / revoked=已撤销）

    关键功能：
        get_expiring(days_ahead=30): 查询未来30天内到期的有效证书，
        用于生成证书到期预警提醒，推动员工及时续证。

    与培训模块联动：
        ExamService.submit_attempt() 生成的 certificate_no 可对应到此表，
        实现"参加培训考试 → 获得证书" 的完整链路。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: EmployeeCertificationCreate
    ) -> EmployeeCertification:
        """
        创建员工证书记录。

        参数:
            db (AsyncSession): 异步数据库会话
            data (EmployeeCertificationCreate): 证书创建 DTO
                - employee_id: 持证员工 ID
                - cert_name: 证书名称（如"PMP项目管理专业人士"）
                - cert_type: 证书类型（如：professional/technical/language）
                - cert_no: 证书编号
                - issue_date: 获证日期
                - expiry_date: 到期日期（可选，无限期证书为 None）
                - status: 初始状态（通常为 valid）

        返回:
            EmployeeCertification: 新建的证书记录
        """
        cert = EmployeeCertification(**data.model_dump())
        db.add(cert)
        await db.flush()
        await db.refresh(cert)
        return cert

    @staticmethod
    async def get(db: AsyncSession, cert_id: int) -> Optional[EmployeeCertification]:
        """
        按 ID 查询单条员工证书记录。

        参数:
            db (AsyncSession): 异步数据库会话
            cert_id (int): 证书记录主键 ID

        返回:
            EmployeeCertification | None: 找到则返回，否则返回 None
        """
        result = await db.execute(
            select(EmployeeCertification).where(EmployeeCertification.id == cert_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        employee_id: Optional[int] = None,
        cert_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[EmployeeCertification]:
        """
        查询员工证书列表，支持多维度过滤。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int | None): 按员工过滤，None 返回所有员工的证书
            cert_type (str | None): 按证书类型过滤
            status (str | None): 按状态过滤（valid/expired/revoked）

        返回:
            list[EmployeeCertification]: 证书列表，按创建时间倒序排列
        """
        query = select(EmployeeCertification)
        if employee_id is not None:
            query = query.where(EmployeeCertification.employee_id == employee_id)
        if cert_type:
            query = query.where(EmployeeCertification.cert_type == cert_type)
        if status:
            query = query.where(EmployeeCertification.status == status)
        query = query.order_by(EmployeeCertification.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, cert_id: int, data: EmployeeCertificationUpdate
    ) -> Optional[EmployeeCertification]:
        """
        更新员工证书信息（局部更新）。

        常用场景：证书续期时更新 expiry_date，或证书作废时更新 status。

        参数:
            db (AsyncSession): 异步数据库会话
            cert_id (int): 要更新的证书记录 ID
            data (EmployeeCertificationUpdate): 更新 DTO

        返回:
            EmployeeCertification | None: 更新后对象，或 None（不存在时）
        """
        cert = await CertificationService.get(db, cert_id)
        if not cert:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(cert, field, value)
        await db.flush()
        await db.refresh(cert)
        return cert

    @staticmethod
    async def delete(db: AsyncSession, cert_id: int) -> bool:
        """
        删除员工证书记录（物理删除）。

        参数:
            db (AsyncSession): 异步数据库会话
            cert_id (int): 要删除的证书记录 ID

        返回:
            bool: True=删除成功，False=记录不存在
        """
        cert = await CertificationService.get(db, cert_id)
        if not cert:
            return False
        await db.delete(cert)
        await db.flush()
        return True

    @staticmethod
    async def get_expiring(
        db: AsyncSession, days_ahead: int = 30
    ) -> list[EmployeeCertification]:
        """
        查询即将在指定天数内到期的有效证书（到期预警）。

        业务规则：
            - 只查询 status="valid" 的证书（已过期或已撤销的不重复提醒）；
            - 只查询有 expiry_date 的证书（无期限证书不需要提醒）；
            - 到期区间：[today, today + days_ahead]，两端均包含；
            - 结果按到期日期升序排列（最快到期的排最前）。

        使用场景：
            定时任务或 dashboard 调用此方法，生成证书到期提醒通知，
            推送给员工本人及直属 HR，避免合规风险。

        参数:
            db (AsyncSession): 异步数据库会话
            days_ahead (int): 提前预警天数，默认30天

        返回:
            list[EmployeeCertification]: 即将到期的证书列表，按到期日期升序
        """
        today = date.today()
        deadline = today + timedelta(days=days_ahead)  # 预警截止日期
        result = await db.execute(
            select(EmployeeCertification)
            .where(
                and_(
                    EmployeeCertification.status == "valid",          # 只查有效证书
                    EmployeeCertification.expiry_date.is_not(None),   # 必须有有效期
                    EmployeeCertification.expiry_date <= deadline,    # 到期日不超过截止日
                    EmployeeCertification.expiry_date >= today,       # 到期日不早于今天（未过期）
                )
            )
            .order_by(EmployeeCertification.expiry_date)  # 按到期日升序，最紧急的排最前
        )
        return list(result.scalars().all())


# ============================================================
# EmployeeSkill CRUD + Skill Matrix
# ============================================================

class SkillService:
    """
    员工技能管理服务。

    EmployeeSkill 记录员工在某项技能上的熟练度评级（由 HR 或系统评定）。
    区别于 EmployeePersonalSkill（员工自填），本表的数据来源更正式。

    熟练度等级（proficiency_level）：
        1=入门 / 2=初级 / 3=中级 / 4=高级 / 5=专家

    技能矩阵（get_skill_matrix）：
        按部门聚合各技能的拥有人数和平均熟练度，展示为矩阵视图，
        帮助管理者识别团队能力短板（哪个部门缺乏哪种技能）。

    被 TalentProfileService._build_skills() 引用，
    为员工人才画像提供技能维度数据。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: EmployeeSkillCreate
    ) -> EmployeeSkill:
        """
        创建员工技能记录。

        参数:
            db (AsyncSession): 异步数据库会话
            data (EmployeeSkillCreate): 技能创建 DTO
                - employee_id: 员工 ID
                - skill_name: 技能名称（如"Python"、"机器视觉"）
                - skill_category: 技能分类（如：编程语言/算法/工具）
                - proficiency_level: 熟练度等级（1-5）
                - assessed_by: 评定人（可选）
                - assessed_at: 评定日期

        返回:
            EmployeeSkill: 新建的技能记录
        """
        skill = EmployeeSkill(**data.model_dump())
        db.add(skill)
        await db.flush()
        await db.refresh(skill)
        return skill

    @staticmethod
    async def get(db: AsyncSession, skill_id: int) -> Optional[EmployeeSkill]:
        """
        按 ID 查询单条技能记录。

        参数:
            db (AsyncSession): 异步数据库会话
            skill_id (int): 技能记录主键 ID

        返回:
            EmployeeSkill | None: 找到则返回，否则返回 None
        """
        result = await db.execute(
            select(EmployeeSkill).where(EmployeeSkill.id == skill_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_employee(
        db: AsyncSession, employee_id: int
    ) -> list[EmployeeSkill]:
        """
        查询某员工的所有技能记录，按技能分类+名称排序。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 员工 ID

        返回:
            list[EmployeeSkill]: 技能列表，按分类 → 名称升序排列（便于分组展示）
        """
        result = await db.execute(
            select(EmployeeSkill)
            .where(EmployeeSkill.employee_id == employee_id)
            .order_by(EmployeeSkill.skill_category, EmployeeSkill.skill_name)
        )
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, skill_id: int, data: EmployeeSkillUpdate
    ) -> Optional[EmployeeSkill]:
        """
        更新员工技能记录（局部更新）。

        常用场景：更新员工技能熟练度（晋升后重新评级）。

        参数:
            db (AsyncSession): 异步数据库会话
            skill_id (int): 要更新的技能记录 ID
            data (EmployeeSkillUpdate): 更新 DTO

        返回:
            EmployeeSkill | None: 更新后对象，或 None（不存在时）
        """
        skill = await SkillService.get(db, skill_id)
        if not skill:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(skill, field, value)
        await db.flush()
        await db.refresh(skill)
        return skill

    @staticmethod
    async def delete(db: AsyncSession, skill_id: int) -> bool:
        """
        删除员工技能记录（物理删除）。

        参数:
            db (AsyncSession): 异步数据库会话
            skill_id (int): 要删除的技能记录 ID

        返回:
            bool: True=删除成功，False=记录不存在
        """
        skill = await SkillService.get(db, skill_id)
        if not skill:
            return False
        await db.delete(skill)
        await db.flush()
        return True

    @staticmethod
    async def get_skill_matrix(db: AsyncSession) -> list[SkillMatrixEntry]:
        """
        构建全公司技能矩阵：按部门聚合各技能的人数和平均熟练度。

        业务价值：
            帮助管理层和 HR 快速识别：
            - 哪些部门在某项技能上人才充足（高覆盖率）或短缺（低覆盖率）；
            - 各部门某技能的平均熟练度，判断是否需要专项培训。

        SQL 逻辑：
            1. EmployeeSkill JOIN Employee，过滤掉无部门员工；
            2. 按 (department_id, skill_name, skill_category) 三元组分组聚合；
            3. 统计每组的员工数量（COUNT）和平均熟练度（AVG）；
            4. 在 Python 层按部门 ID 汇总为 SkillMatrixEntry 列表；
            5. 批量查询部门名称（避免 N+1 查询问题）。

        参数:
            db (AsyncSession): 异步数据库会话

        返回:
            list[SkillMatrixEntry]: 每个部门的技能矩阵条目，包含：
                - department_id / department_name: 部门信息
                - skills: list[SkillMatrixSkillItem]，每项包含技能名、分类、人数、均值
        """
        from app.models.organization import Department

        # 按部门+技能名+技能分类聚合统计
        result = await db.execute(
            select(
                Employee.department_id,
                EmployeeSkill.skill_name,
                EmployeeSkill.skill_category,
                func.count(EmployeeSkill.id).label("employee_count"),
                func.avg(EmployeeSkill.proficiency_level).label("avg_proficiency"),
            )
            .join(Employee, EmployeeSkill.employee_id == Employee.id)
            .where(Employee.department_id.is_not(None))  # 排除无部门员工
            .group_by(
                Employee.department_id,
                EmployeeSkill.skill_name,
                EmployeeSkill.skill_category,
            )
            .order_by(Employee.department_id, EmployeeSkill.skill_category)
        )
        rows = result.all()

        # 在 Python 层按部门 ID 分组汇总
        dept_map: dict[int, list] = {}
        for row in rows:
            dept_id = row.department_id
            if dept_id not in dept_map:
                dept_map[dept_id] = []
            dept_map[dept_id].append(
                SkillMatrixSkillItem(
                    skill_name=row.skill_name,
                    skill_category=row.skill_category,
                    employee_count=row.employee_count,
                    avg_proficiency=round(float(row.avg_proficiency or 0), 1),
                )
            )

        # 批量获取部门名称（一次查询，避免 N+1）
        dept_ids = list(dept_map.keys())
        dept_names_result = await db.execute(
            select(Department.id, Department.name).where(Department.id.in_(dept_ids))
        )
        dept_name_map: dict[int, str] = {r.id: r.name for r in dept_names_result.all()}

        # 组装最终返回结构
        entries = []
        for dept_id, skills in dept_map.items():
            dept_name = dept_name_map.get(dept_id) or "未知部门"
            entries.append(
                SkillMatrixEntry(
                    department_id=dept_id,
                    department_name=dept_name,
                    skills=skills,
                )
            )

        return entries
