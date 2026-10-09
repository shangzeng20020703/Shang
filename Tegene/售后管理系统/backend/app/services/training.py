"""
培训管理服务模块 - Training Service
=====================================

**业务职责**：
    售后管理系统的培训管理核心服务层，负责：
    1. 培训课程（TrainingCourse）的全生命周期管理
    2. 培训计划（TrainingPlan）的创建、状态流转与删除
    3. 计划-课程关联（TrainingPlanCourse）的编排
    4. 员工报名（TrainingEnrollment）、完成记录与反馈收集
    5. 培训统计（全局概览 + 按部门分类）
    6. 基于胜任力差距的课程推荐
    7. 在线考试（TrainingExam / ExamAttempt）的创建、作答与自动批改

**数据流**：
    - 课程由 HR 配置，关联若干胜任力项（TrainingCourseCompetency）表示完课后可提升的能力
    - 培训计划聚合多门课程，并安排具体排期（TrainingPlanCourse）
    - 员工通过 TrainingEnrollment 报名/参训，完成后可触发胜任力更新
    - 考试与课程绑定，员工提交答案后系统自动批改并生成证书编号

**状态机**：
    - TrainingPlan 状态: draft → active → completed|cancelled（不可逆）
    - TrainingEnrollment 状态: enrolled → completed|cancelled

**模块间依赖**：
    - TrainingRecommendationService 依赖 competency.CompetencyGapService 做差距分析
    - 证书编号生成于 ExamService.submit_attempt()，后续可与 talent 模块的 EmployeeCertification 联动

**技术说明**：
    FastAPI + SQLAlchemy 2.0 Async（asyncpg / PostgreSQL）+ Pydantic v2。
    所有写操作使用 db.flush() 刷新但不提交，事务由 API 层的依赖注入管理。
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.training import (
    TrainingCourse,
    TrainingPlan,
    TrainingPlanCourse,
    TrainingEnrollment,
    TrainingCourseCompetency,
    TrainingExam,
    ExamQuestion,
    ExamAttempt,
)
from app.models.employee import Employee
from app.models.competency import CompetencyItem
from app.schemas.training import (
    TrainingCourseCreate,
    TrainingCourseUpdate,
    PlanCourseCreate,
    PlanCourseUpdate,
    TrainingPlanCreate,
    TrainingPlanUpdate,
    TrainingPlanStatusTransition,
    TrainingEnrollmentCreate,
    EnrollmentComplete,
    EnrollmentFeedback,
    TrainingStatsOverview,
    DepartmentTrainingStat,
    CourseRecommendation,
    TrainingExamCreate,
    ExamQuestionCreate,
    SubmitAttemptRequest,
)
from app.services.competency import CompetencyGapService


# 培训计划状态流转规则
# 说明：key 为当前状态，value 为允许跳转的目标状态列表
# - draft（草稿）：只能发布为 active
# - active（进行中）：可以完成或取消
# - completed / cancelled：终态，不可再流转
PLAN_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["active"],
    "active": ["completed", "cancelled"],
    "completed": [],
    "cancelled": [],
}


def _q(value: Decimal) -> Decimal:
    """
    将 Decimal 值四舍五入到2位小数（ROUND_HALF_UP）。

    参数:
        value (Decimal): 待格式化的数值

    返回:
        Decimal: 精确到2位小数的新值
    """
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ============================================================
# TrainingCourse CRUD
# ============================================================

class TrainingCourseService:
    """
    培训课程管理服务。

    负责课程的增删改查，以及创建时同步写入课程与胜任力项的关联关系。

    关键说明：
        - 创建课程时，data.competency_links 中的每个条目会同步写入
          TrainingCourseCompetency 表，记录该课程完成后可提升的胜任力等级增量。
        - 课程逻辑删除方式为物理删除（级联删除关联的 competency_links 和 enrollments）。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: TrainingCourseCreate
    ) -> TrainingCourse:
        """
        创建培训课程，并同步写入胜任力关联。

        业务流程：
            1. 创建 TrainingCourse 记录；
            2. 遍历 data.competency_links，逐条写入 TrainingCourseCompetency；
            3. flush 确保 ORM 主键赋值后刷新返回完整对象。

        参数:
            db (AsyncSession): 异步数据库会话
            data (TrainingCourseCreate): 课程创建 DTO，包含名称、类别、交付方式、
                                          时长、是否必修、目标岗位及胜任力关联列表

        返回:
            TrainingCourse: 已持久化的课程 ORM 对象（含 id）
        """
        course = TrainingCourse(
            name=data.name,
            category=data.category,
            description=data.description,
            delivery_method=data.delivery_method,
            duration_hours=data.duration_hours,
            is_mandatory=data.is_mandatory,
            target_positions=data.target_positions,
            is_active=data.is_active,
        )
        db.add(course)
        await db.flush()  # 获取 course.id，供下方关联写入使用

        # 写入课程-胜任力关联：记录该课程完成后可提升的胜任力项及其等级增量
        for link in data.competency_links:
            cl = TrainingCourseCompetency(
                course_id=course.id,
                competency_item_id=link.competency_item_id,
                target_level_delta=link.target_level_delta,  # 完课后预期等级提升量（如 +1）
            )
            db.add(cl)

        await db.flush()
        await db.refresh(course)
        return course

    @staticmethod
    async def get(db: AsyncSession, course_id: int) -> Optional[TrainingCourse]:
        """
        按 ID 查询单条培训课程。

        参数:
            db (AsyncSession): 异步数据库会话
            course_id (int): 课程主键 ID

        返回:
            TrainingCourse | None: 找到则返回 ORM 对象，否则返回 None
        """
        result = await db.execute(
            select(TrainingCourse).where(TrainingCourse.id == course_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        category: Optional[str] = None,
        is_mandatory: Optional[bool] = None,
        is_active: Optional[bool] = None,
        keyword: Optional[str] = None,
    ) -> list[TrainingCourse]:
        """
        查询培训课程列表，支持多条件过滤。

        参数:
            db (AsyncSession): 异步数据库会话
            category (str | None): 按课程类别过滤（精确匹配）
            is_mandatory (bool | None): True=必修课，False=选修课，None=全部
            is_active (bool | None): True=启用，False=停用，None=全部
            keyword (str | None): 按课程名称模糊搜索（ILIKE 大小写不敏感）

        返回:
            list[TrainingCourse]: 满足条件的课程列表，按创建时间倒序排列
        """
        query = select(TrainingCourse)
        if category:
            query = query.where(TrainingCourse.category == category)
        if is_mandatory is not None:
            query = query.where(TrainingCourse.is_mandatory == is_mandatory)
        if is_active is not None:
            query = query.where(TrainingCourse.is_active == is_active)
        if keyword:
            query = query.where(TrainingCourse.name.ilike(f"%{keyword}%"))
        query = query.order_by(TrainingCourse.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, course_id: int, data: TrainingCourseUpdate
    ) -> Optional[TrainingCourse]:
        """
        更新培训课程信息（仅更新 DTO 中明确传入的字段）。

        参数:
            db (AsyncSession): 异步数据库会话
            course_id (int): 要更新的课程 ID
            data (TrainingCourseUpdate): 课程更新 DTO，使用 exclude_unset 只更新变更字段

        返回:
            TrainingCourse | None: 更新后的课程对象，或 None（课程不存在时）
        """
        course = await TrainingCourseService.get(db, course_id)
        if not course:
            return None
        update_data = data.model_dump(exclude_unset=True)  # 仅处理前端明确传入的字段
        for field, value in update_data.items():
            setattr(course, field, value)
        await db.flush()
        await db.refresh(course)
        return course

    @staticmethod
    async def delete(db: AsyncSession, course_id: int) -> bool:
        """
        删除培训课程（物理删除）。

        注意：删除课程会级联删除其关联的胜任力配置（TrainingCourseCompetency）。
        已有报名记录（TrainingEnrollment）的课程删除需确认级联策略。

        参数:
            db (AsyncSession): 异步数据库会话
            course_id (int): 要删除的课程 ID

        返回:
            bool: True=删除成功，False=课程不存在
        """
        course = await TrainingCourseService.get(db, course_id)
        if not course:
            return False
        await db.delete(course)
        await db.flush()
        return True


# ============================================================
# Course-Competency Link CRUD
# ============================================================

class CourseCompetencyService:
    """
    课程-胜任力关联管理服务。

    维护 TrainingCourseCompetency 表，记录培训课程与胜任力能力项之间的关联，
    以及完成该课程后预期能提升的等级增量（target_level_delta）。

    该关联数据被 TrainingRecommendationService 用于推荐能弥补差距的课程。
    """

    @staticmethod
    async def add_link(
        db: AsyncSession, course_id: int, competency_item_id: int, target_level_delta: int = 1
    ) -> TrainingCourseCompetency:
        """
        为培训课程添加一条胜任力关联。

        参数:
            db (AsyncSession): 异步数据库会话
            course_id (int): 培训课程 ID
            competency_item_id (int): 胜任力能力项 ID
            target_level_delta (int): 完成本课程后预期提升的等级增量，默认为 1

        返回:
            TrainingCourseCompetency: 新建的关联记录
        """
        link = TrainingCourseCompetency(
            course_id=course_id,
            competency_item_id=competency_item_id,
            target_level_delta=target_level_delta,
        )
        db.add(link)
        await db.flush()
        await db.refresh(link)
        return link

    @staticmethod
    async def remove_link(db: AsyncSession, link_id: int) -> bool:
        """
        删除指定的课程-胜任力关联记录。

        参数:
            db (AsyncSession): 异步数据库会话
            link_id (int): 关联记录的主键 ID

        返回:
            bool: True=删除成功，False=记录不存在
        """
        result = await db.execute(
            select(TrainingCourseCompetency).where(TrainingCourseCompetency.id == link_id)
        )
        link = result.scalar_one_or_none()
        if not link:
            return False
        await db.delete(link)
        await db.flush()
        return True


# ============================================================
# TrainingPlan CRUD
# ============================================================

class TrainingPlanService:
    """
    培训计划管理服务。

    培训计划是对若干培训课程的年度/季度编排，包含：
    - 计划类型（年度/部门/岗位等）
    - 计划年份
    - 状态（草稿 → 进行中 → 完成/取消）

    状态流转遵循 PLAN_STATUS_TRANSITIONS 规则，非法流转会抛出 ValueError。
    只有草稿或已取消状态的计划允许删除。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: TrainingPlanCreate
    ) -> TrainingPlan:
        """
        创建培训计划（初始状态为 draft）。

        参数:
            db (AsyncSession): 异步数据库会话
            data (TrainingPlanCreate): 计划创建 DTO

        返回:
            TrainingPlan: 新建的培训计划对象
        """
        plan = TrainingPlan(**data.model_dump())
        db.add(plan)
        await db.flush()
        await db.refresh(plan)
        return plan

    @staticmethod
    async def get(db: AsyncSession, plan_id: int) -> Optional[TrainingPlan]:
        """
        按 ID 查询单条培训计划。

        参数:
            db (AsyncSession): 异步数据库会话
            plan_id (int): 计划主键 ID

        返回:
            TrainingPlan | None: 找到则返回，否则返回 None
        """
        result = await db.execute(
            select(TrainingPlan).where(TrainingPlan.id == plan_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        year: Optional[int] = None,
        plan_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[TrainingPlan]:
        """
        查询培训计划列表，支持按年份、类型、状态过滤。

        参数:
            db (AsyncSession): 异步数据库会话
            year (int | None): 按计划年份过滤
            plan_type (str | None): 按计划类型过滤（如 annual/department/position）
            status (str | None): 按状态过滤（draft/active/completed/cancelled）

        返回:
            list[TrainingPlan]: 满足条件的计划列表，按年份+创建时间倒序排列
        """
        query = select(TrainingPlan)
        if year:
            query = query.where(TrainingPlan.year == year)
        if plan_type:
            query = query.where(TrainingPlan.plan_type == plan_type)
        if status:
            query = query.where(TrainingPlan.status == status)
        query = query.order_by(TrainingPlan.year.desc(), TrainingPlan.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        db: AsyncSession, plan_id: int, data: TrainingPlanUpdate
    ) -> Optional[TrainingPlan]:
        """
        更新培训计划基本信息（仅更新有变更的字段）。

        参数:
            db (AsyncSession): 异步数据库会话
            plan_id (int): 要更新的计划 ID
            data (TrainingPlanUpdate): 计划更新 DTO

        返回:
            TrainingPlan | None: 更新后对象，或 None（计划不存在时）
        """
        plan = await TrainingPlanService.get(db, plan_id)
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
        db: AsyncSession, plan_id: int, data: TrainingPlanStatusTransition
    ) -> Optional[TrainingPlan]:
        """
        执行培训计划状态流转。

        业务规则：
            合法流转路径由 PLAN_STATUS_TRANSITIONS 定义，非法流转抛出 ValueError。
            例如：已完成的计划不允许回退到草稿。

        参数:
            db (AsyncSession): 异步数据库会话
            plan_id (int): 计划 ID
            data (TrainingPlanStatusTransition): 含目标状态（target_status）的请求 DTO

        返回:
            TrainingPlan | None: 状态更新后的计划对象，或 None（计划不存在时）

        异常:
            ValueError: 目标状态不在允许的流转列表中
        """
        plan = await TrainingPlanService.get(db, plan_id)
        if not plan:
            return None
        allowed = PLAN_STATUS_TRANSITIONS.get(plan.status, [])
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
        删除培训计划（仅允许删除草稿或已取消的计划）。

        业务规则：
            进行中或已完成的计划不可删除，防止删除后造成报名记录孤立。

        参数:
            db (AsyncSession): 异步数据库会话
            plan_id (int): 要删除的计划 ID

        返回:
            bool: True=删除成功，False=计划不存在

        异常:
            ValueError: 计划状态不在 draft/cancelled 时抛出
        """
        plan = await TrainingPlanService.get(db, plan_id)
        if not plan:
            return False
        if plan.status not in ("draft", "cancelled"):
            raise ValueError("只能删除草稿或已取消的培训计划")
        await db.delete(plan)
        await db.flush()
        return True


# ============================================================
# TrainingPlanCourse (计划-课程)
# ============================================================

class PlanCourseService:
    """
    培训计划-课程关联管理服务。

    TrainingPlanCourse 表示一个培训计划中某门课程的排期信息，包含：
    - 计划 ID + 课程 ID
    - 具体上课日期（scheduled_date）
    - 讲师、地点等附加信息

    提供列表、新增、修改、删除4个操作。
    """

    @staticmethod
    async def add_course(
        db: AsyncSession, plan_id: int, data: PlanCourseCreate
    ) -> TrainingPlanCourse:
        """
        向培训计划中添加一门课程排期。

        参数:
            db (AsyncSession): 异步数据库会话
            plan_id (int): 目标培训计划 ID
            data (PlanCourseCreate): 课程排期信息 DTO（课程 ID、日期、讲师等）

        返回:
            TrainingPlanCourse: 新建的计划-课程关联对象
        """
        pc = TrainingPlanCourse(plan_id=plan_id, **data.model_dump())
        db.add(pc)
        await db.flush()
        await db.refresh(pc)
        return pc

    @staticmethod
    async def update_course(
        db: AsyncSession, pc_id: int, data: PlanCourseUpdate
    ) -> Optional[TrainingPlanCourse]:
        """
        更新培训计划中某门课程的排期信息。

        参数:
            db (AsyncSession): 异步数据库会话
            pc_id (int): 计划-课程关联记录 ID
            data (PlanCourseUpdate): 更新内容 DTO

        返回:
            TrainingPlanCourse | None: 更新后对象，或 None（记录不存在）
        """
        result = await db.execute(
            select(TrainingPlanCourse).where(TrainingPlanCourse.id == pc_id)
        )
        pc = result.scalar_one_or_none()
        if not pc:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(pc, field, value)
        await db.flush()
        await db.refresh(pc)
        return pc

    @staticmethod
    async def remove_course(db: AsyncSession, pc_id: int) -> bool:
        """
        从培训计划中移除一门课程排期。

        参数:
            db (AsyncSession): 异步数据库会话
            pc_id (int): 计划-课程关联记录 ID

        返回:
            bool: True=移除成功，False=记录不存在
        """
        result = await db.execute(
            select(TrainingPlanCourse).where(TrainingPlanCourse.id == pc_id)
        )
        pc = result.scalar_one_or_none()
        if not pc:
            return False
        await db.delete(pc)
        await db.flush()
        return True

    @staticmethod
    async def list_by_plan(
        db: AsyncSession, plan_id: int
    ) -> list[TrainingPlanCourse]:
        """
        查询培训计划下的所有课程排期，按上课日期升序排列。

        参数:
            db (AsyncSession): 异步数据库会话
            plan_id (int): 培训计划 ID

        返回:
            list[TrainingPlanCourse]: 该计划下的课程排期列表
        """
        result = await db.execute(
            select(TrainingPlanCourse)
            .where(TrainingPlanCourse.plan_id == plan_id)
            .order_by(TrainingPlanCourse.scheduled_date)  # 按排期日期升序，方便展示日历视图
        )
        return list(result.scalars().all())


# ============================================================
# TrainingEnrollment 培训报名/记录
# ============================================================

class TrainingEnrollmentService:
    """
    培训报名与参训记录管理服务。

    TrainingEnrollment 记录员工参与培训的全过程，包含：
    - 报名信息（employee_id + course_id + plan_course_id）
    - 参训进度（progress_pct 0-100）
    - 完成记录（status="completed"、completed_at、score）
    - 反馈评分（feedback_score、feedback_comment）

    支持单条报名和批量报名（create_batch），并支持提交反馈和取消报名。

    状态说明：
        enrolled（已报名） → completed（已完成） | cancelled（已取消）
        已完成的记录不允许再次取消。
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: TrainingEnrollmentCreate
    ) -> TrainingEnrollment:
        """
        创建单条培训报名记录。

        参数:
            db (AsyncSession): 异步数据库会话
            data (TrainingEnrollmentCreate): 报名 DTO（员工ID、课程ID、计划课程ID等）

        返回:
            TrainingEnrollment: 新建的报名记录，初始状态为 enrolled
        """
        enrollment = TrainingEnrollment(**data.model_dump())
        db.add(enrollment)
        await db.flush()
        await db.refresh(enrollment)
        return enrollment

    @staticmethod
    async def create_batch(
        db: AsyncSession,
        employee_ids: list[int],
        course_id: int,
        plan_course_id: Optional[int] = None,
    ) -> list[TrainingEnrollment]:
        """
        批量为多名员工创建同一门课程的报名记录。

        使用场景：HR 批量将某部门员工加入某次培训排期。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_ids (list[int]): 要批量报名的员工 ID 列表
            course_id (int): 培训课程 ID
            plan_course_id (int | None): 所属计划-课程排期 ID，可选

        返回:
            list[TrainingEnrollment]: 所有新建报名记录的列表
        """
        # 查重：查询已存在的报名记录（enrolled 状态），避免重复报名
        already_enrolled: set[int] = set()
        if employee_ids:
            existing_result = await db.execute(
                select(TrainingEnrollment.employee_id).where(
                    and_(
                        TrainingEnrollment.course_id == course_id,
                        TrainingEnrollment.employee_id.in_(employee_ids),
                        TrainingEnrollment.status == "enrolled",
                    )
                )
            )
            already_enrolled = set(existing_result.scalars().all())

        enrollments = []
        for eid in employee_ids:
            if eid in already_enrolled:
                continue  # 跳过已报名的员工
            e = TrainingEnrollment(
                employee_id=eid,
                course_id=course_id,
                plan_course_id=plan_course_id,
            )
            db.add(e)
            enrollments.append(e)
        await db.flush()
        # 批量刷新，确保所有记录获得数据库赋值的字段（如 created_at）
        for e in enrollments:
            await db.refresh(e)
        return enrollments

    @staticmethod
    async def get(db: AsyncSession, enrollment_id: int) -> Optional[TrainingEnrollment]:
        """
        按 ID 查询单条报名记录。

        参数:
            db (AsyncSession): 异步数据库会话
            enrollment_id (int): 报名记录主键 ID

        返回:
            TrainingEnrollment | None: 找到则返回，否则返回 None
        """
        result = await db.execute(
            select(TrainingEnrollment).where(TrainingEnrollment.id == enrollment_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_course(
        db: AsyncSession, course_id: int, status: Optional[str] = None
    ) -> list[TrainingEnrollment]:
        """
        查询某门课程的所有报名记录，可按状态过滤。

        参数:
            db (AsyncSession): 异步数据库会话
            course_id (int): 培训课程 ID
            status (str | None): 按报名状态过滤（enrolled/completed/cancelled）

        返回:
            list[TrainingEnrollment]: 报名记录列表，按创建时间倒序
        """
        query = select(TrainingEnrollment).where(
            TrainingEnrollment.course_id == course_id
        )
        if status:
            query = query.where(TrainingEnrollment.status == status)
        query = query.order_by(TrainingEnrollment.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def list_by_employee(
        db: AsyncSession, employee_id: int, status: Optional[str] = None
    ) -> list[TrainingEnrollment]:
        """
        查询某名员工的所有培训报名记录，可按状态过滤。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 员工 ID
            status (str | None): 按报名状态过滤

        返回:
            list[TrainingEnrollment]: 报名记录列表，按创建时间倒序
        """
        query = select(TrainingEnrollment).where(
            TrainingEnrollment.employee_id == employee_id
        )
        if status:
            query = query.where(TrainingEnrollment.status == status)
        query = query.order_by(TrainingEnrollment.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def complete(
        db: AsyncSession, enrollment_id: int, data: EnrollmentComplete
    ) -> Optional[TrainingEnrollment]:
        """
        标记培训报名为已完成，记录完成时间和成绩。

        业务规则：
            - 已完成的记录不能重复完成（避免重复打分）。
            - progress_pct 自动设为 100。

        参数:
            db (AsyncSession): 异步数据库会话
            enrollment_id (int): 报名记录 ID
            data (EnrollmentComplete): 完成信息 DTO（score 成绩可选）

        返回:
            TrainingEnrollment | None: 更新后的记录，或 None（记录不存在）

        异常:
            ValueError: 该记录已完成时抛出
        """
        enrollment = await TrainingEnrollmentService.get(db, enrollment_id)
        if not enrollment:
            return None
        if enrollment.status == "completed":
            raise ValueError("该培训记录已完成")
        enrollment.status = "completed"
        enrollment.progress_pct = 100
        enrollment.score = data.score
        enrollment.completed_at = datetime.now(timezone.utc)
        await db.flush()
        await db.refresh(enrollment)
        return enrollment

    @staticmethod
    async def submit_feedback(
        db: AsyncSession, enrollment_id: int, data: EnrollmentFeedback
    ) -> Optional[TrainingEnrollment]:
        """
        提交学员对本次培训的反馈评分与意见。

        业务规则：
            - 反馈不限于已完成状态，已报名即可提交（根据业务灵活调整）。
            - feedback_score 用于统计课程满意度，参与 TrainingStatsService 汇总。

        参数:
            db (AsyncSession): 异步数据库会话
            enrollment_id (int): 报名记录 ID
            data (EnrollmentFeedback): 反馈 DTO（feedback_score 1-5星 + feedback_comment）

        返回:
            TrainingEnrollment | None: 更新后的记录，或 None（记录不存在）
        """
        enrollment = await TrainingEnrollmentService.get(db, enrollment_id)
        if not enrollment:
            return None
        enrollment.feedback_score = data.feedback_score
        enrollment.feedback_comment = data.feedback_comment
        await db.flush()
        await db.refresh(enrollment)
        return enrollment

    @staticmethod
    async def cancel(
        db: AsyncSession, enrollment_id: int
    ) -> Optional[TrainingEnrollment]:
        """
        取消培训报名。

        业务规则：
            已完成的培训记录不可取消，防止删除历史成绩数据。

        参数:
            db (AsyncSession): 异步数据库会话
            enrollment_id (int): 报名记录 ID

        返回:
            TrainingEnrollment | None: 更新后的记录，或 None（记录不存在）

        异常:
            ValueError: 记录已完成时抛出
        """
        enrollment = await TrainingEnrollmentService.get(db, enrollment_id)
        if not enrollment:
            return None
        if enrollment.status == "completed":
            raise ValueError("已完成的培训不能取消")
        enrollment.status = "cancelled"
        await db.flush()
        await db.refresh(enrollment)
        return enrollment


# ============================================================
# Training Stats 培训统计
# ============================================================

class TrainingStatsService:
    """
    培训统计分析服务。

    提供两种维度的统计报表：
    1. get_overview(): 全局概览（课程数、计划数、报名数、完成率、满意度、总学时）
    2. get_by_department(): 按部门统计报名数、完成数、完成率、平均成绩

    统计结果直接返回 Pydantic Schema 对象，供 API 层直接序列化响应。
    """

    @staticmethod
    async def get_overview(db: AsyncSession) -> TrainingStatsOverview:
        """
        获取培训系统全局概览统计数据。

        统计指标：
            - total_courses: 启用课程总数
            - active_plans: 进行中的培训计划数
            - total_enrollments: 总报名人次
            - completed_enrollments: 已完成人次
            - completion_rate: 完成率 = 已完成/总报名 * 100（百分比）
            - avg_feedback_score: 平均满意度（1-5分）
            - total_training_hours: 已完成培训的总学时（每条完成记录贡献课程学时）

        参数:
            db (AsyncSession): 异步数据库会话

        返回:
            TrainingStatsOverview: 包含上述指标的统计对象
        """
        # Total courses
        total_courses = (await db.execute(
            select(func.count()).select_from(TrainingCourse).where(TrainingCourse.is_active == True)
        )).scalar() or 0

        # Active plans
        active_plans = (await db.execute(
            select(func.count()).select_from(TrainingPlan).where(TrainingPlan.status == "active")
        )).scalar() or 0

        # Enrollment stats
        total_enrollments = (await db.execute(
            select(func.count()).select_from(TrainingEnrollment)
        )).scalar() or 0

        completed_enrollments = (await db.execute(
            select(func.count()).select_from(TrainingEnrollment)
            .where(TrainingEnrollment.status == "completed")
        )).scalar() or 0

        # 计算完成率，防止除零
        completion_rate = (
            (completed_enrollments / total_enrollments * 100)
            if total_enrollments > 0 else 0
        )

        # Avg feedback
        avg_feedback = (await db.execute(
            select(func.avg(TrainingEnrollment.feedback_score))
            .where(TrainingEnrollment.feedback_score.is_not(None))
        )).scalar()

        # 总学时 = 已完成报名记录对应课程的 duration_hours 之和
        hours_result = await db.execute(
            select(func.sum(TrainingCourse.duration_hours))
            .join(TrainingEnrollment, TrainingEnrollment.course_id == TrainingCourse.id)
            .where(TrainingEnrollment.status == "completed")
        )
        total_hours = hours_result.scalar()

        return TrainingStatsOverview(
            total_courses=total_courses,
            active_plans=active_plans,
            total_enrollments=total_enrollments,
            completed_enrollments=completed_enrollments,
            completion_rate=round(completion_rate, 1),
            avg_feedback_score=round(float(avg_feedback or 0), 1),
            total_training_hours=round(float(total_hours or 0), 1),
        )

    @staticmethod
    async def get_by_department(db: AsyncSession) -> list[DepartmentTrainingStat]:
        """
        按部门统计培训报名和完成情况。

        业务逻辑：
            - 通过 TrainingEnrollment → Employee → Department 三表关联统计
            - 每个部门统计：报名总人次、完成人次、完成率、平均成绩
            - 没有部门的员工报名记录被排除（WHERE department_id IS NOT NULL）

        参数:
            db (AsyncSession): 异步数据库会话

        返回:
            list[DepartmentTrainingStat]: 每个部门的培训统计对象列表
        """
        from app.models.organization import Department

        result = await db.execute(
            select(
                Employee.department_id,
                Department.name.label("dept_name"),
                func.count(TrainingEnrollment.id).label("enrollment_count"),
                func.count(
                    func.nullif(TrainingEnrollment.status != "completed", True)
                ).label("completed_count"),
                func.avg(TrainingEnrollment.score).label("avg_score"),
            )
            .join(Employee, TrainingEnrollment.employee_id == Employee.id)
            .outerjoin(Department, Employee.department_id == Department.id)
            .where(Employee.department_id.is_not(None))
            .group_by(Employee.department_id, Department.name)
        )
        rows = result.all()

        stats = []
        for row in rows:
            dept_name = row.dept_name or "未知部门"

            e_count = row.enrollment_count or 0
            c_count = row.completed_count or 0
            rate = (c_count / e_count * 100) if e_count > 0 else 0

            stats.append(DepartmentTrainingStat(
                department_id=row.department_id,
                department_name=dept_name,
                enrollment_count=e_count,
                completed_count=c_count,
                completion_rate=round(rate, 1),
                avg_score=round(float(row.avg_score or 0), 1),
            ))
        return stats


# ============================================================
# Training Recommendation 基于胜任力差距推荐课程
# ============================================================

class TrainingRecommendationService:
    """
    基于胜任力差距的培训课程推荐服务。

    推荐算法：
        1. 调用 CompetencyGapService.analyze() 获取员工各胜任力项的差距数据；
        2. 筛选出存在正向差距（gap > 0）的胜任力项；
        3. 在 TrainingCourseCompetency 表中查找关联这些能力项的课程；
        4. 对每门课程计算"相关性得分" = gap * weight / 100；
        5. 按相关性得分降序排列，返回推荐列表。

    依赖关系：
        - CompetencyGapService（来自 app.services.competency）
        - TrainingCourseCompetency（课程-胜任力关联表）
    """

    @staticmethod
    async def recommend_courses(
        db: AsyncSession, employee_id: int
    ) -> list[CourseRecommendation]:
        """
        为指定员工推荐能弥补胜任力差距的培训课程。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int): 需要推荐的员工 ID

        返回:
            list[CourseRecommendation]: 推荐课程列表，按相关性得分降序排列。
                若员工无胜任力差距，返回空列表。

        数据结构:
            CourseRecommendation 包含：
                - course_id / course_name / category: 课程基本信息
                - gap_competency_name: 对应的差距胜任力名称
                - current_level / required_level / gap: 差距详情
                - target_level_delta: 完成本课程预期提升幅度
                - relevance_score: 相关性得分（越高越推荐）
        """
        gap_analysis = await CompetencyGapService.analyze(db, employee_id)

        # 找出有差距的胜任力项
        gap_items = [item for item in gap_analysis.items if item.gap > 0]
        if not gap_items:
            return []

        gap_item_ids = [item.competency_item_id for item in gap_items]
        # 构建 competency_item_id → gap 数据的映射，供后续快速查找
        gap_map = {item.competency_item_id: item for item in gap_items}

        # 查找关联这些胜任力项的课程
        result = await db.execute(
            select(TrainingCourseCompetency)
            .where(TrainingCourseCompetency.competency_item_id.in_(gap_item_ids))
        )
        links = list(result.scalars().all())

        recommendations = []
        seen_courses: set[int] = set()  # 避免同一课程因关联多个差距项而重复推荐

        for link in links:
            if link.course_id in seen_courses:
                continue
            seen_courses.add(link.course_id)

            course = link.course
            if not course or not course.is_active:
                continue  # 跳过已停用的课程

            gap_item = gap_map[link.competency_item_id]
            comp_item = link.competency_item

            # 相关性得分 = 差距等级 × 胜任力权重 / 100，值越大表示越优先推荐
            relevance = gap_item.gap * gap_item.weight / 100.0

            recommendations.append(CourseRecommendation(
                course_id=course.id,
                course_name=course.name,
                category=course.category,
                gap_competency_name=comp_item.name if comp_item else "",
                current_level=gap_item.assessed_level,
                required_level=gap_item.required_level,
                gap=gap_item.gap,
                target_level_delta=link.target_level_delta,
                relevance_score=round(relevance, 2),
            ))

        # Sort by relevance descending
        recommendations.sort(key=lambda r: r.relevance_score, reverse=True)
        return recommendations


# ============================================================
# ExamService 在线考试
# ============================================================

class ExamService:
    """
    在线考试服务。

    功能覆盖：
        - 考试 CRUD（TrainingExam）
        - 题目 CRUD（ExamQuestion）
        - 开始考试（start_attempt）—— 创建 ExamAttempt 记录
        - 提交考试（submit_attempt）—— 自动批改并生成证书编号
        - 查询考试记录（list_attempts）

    自动批改逻辑：
        - 遍历考试中所有题目，逐题比对提交答案与标准答案；
        - 答案规范化：去空格 + 大写 + 按逗号分割后排序（多选题顺序不敏感）；
        - 得分 = 各题分数之和；
        - 合格条件：total_score >= exam.pass_score；
        - 合格后自动生成证书编号 CERT-{年份}-{考试ID}-{记录ID:05d}。

    证书编号与 talent 模块联动：
        生成的 certificate_no 可被 CertificationService（talent.py）引用，
        将考试合格与员工证书数据关联，形成完整的培训→认证闭环。
    """

    @staticmethod
    async def list_exams(
        db: AsyncSession,
        course_id: Optional[int] = None,
    ) -> list[TrainingExam]:
        """
        查询考试列表，可按课程过滤。

        参数:
            db (AsyncSession): 异步数据库会话
            course_id (int | None): 按所属课程过滤，None 则返回全部

        返回:
            list[TrainingExam]: 考试列表，按创建时间倒序
        """
        query = select(TrainingExam).order_by(TrainingExam.created_at.desc())
        if course_id is not None:
            query = query.where(TrainingExam.course_id == course_id)
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def create_exam(db: AsyncSession, data: TrainingExamCreate) -> TrainingExam:
        """
        创建一场考试（不含题目，题目通过 add_question 单独添加）。

        参数:
            db (AsyncSession): 异步数据库会话
            data (TrainingExamCreate): 考试创建 DTO
                - course_id: 关联的培训课程
                - title: 考试名称
                - pass_score: 合格分数线
                - time_limit: 限时（分钟）

        返回:
            TrainingExam: 新建的考试对象
        """
        exam = TrainingExam(
            course_id=data.course_id,
            title=data.title,
            pass_score=data.pass_score,
            time_limit=data.time_limit,
        )
        db.add(exam)
        await db.flush()
        await db.refresh(exam)
        return exam

    @staticmethod
    async def get_exam(db: AsyncSession, exam_id: int) -> Optional[TrainingExam]:
        """
        按 ID 查询考试。

        参数:
            db (AsyncSession): 异步数据库会话
            exam_id (int): 考试主键 ID

        返回:
            TrainingExam | None: 找到则返回，否则 None
        """
        result = await db.execute(
            select(TrainingExam).where(TrainingExam.id == exam_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def add_question(
        db: AsyncSession, exam_id: int, data: ExamQuestionCreate
    ) -> ExamQuestion:
        """
        向考试中添加一道题目。

        题目类型支持：单选（single_choice）、多选（multiple_choice）、
        判断（true_false）等（由 question_type 字段控制）。

        参数:
            db (AsyncSession): 异步数据库会话
            exam_id (int): 所属考试 ID
            data (ExamQuestionCreate): 题目 DTO
                - question_text: 题干
                - question_type: 题目类型
                - options: 选项列表（JSON）
                - correct_answer: 标准答案（如 "A" 或 "A,C"）
                - score: 该题分值
                - order: 题目排序序号

        返回:
            ExamQuestion: 新建的题目对象
        """
        q = ExamQuestion(
            exam_id=exam_id,
            question_text=data.question_text,
            question_type=data.question_type,
            options=data.options,
            correct_answer=data.correct_answer,
            score=data.score,
            order=data.order,
        )
        db.add(q)
        await db.flush()
        await db.refresh(q)
        return q

    @staticmethod
    async def delete_question(db: AsyncSession, question_id: int) -> bool:
        """
        删除考试中的一道题目。

        参数:
            db (AsyncSession): 异步数据库会话
            question_id (int): 题目主键 ID

        返回:
            bool: True=删除成功，False=题目不存在
        """
        result = await db.execute(
            select(ExamQuestion).where(ExamQuestion.id == question_id)
        )
        q = result.scalar_one_or_none()
        if not q:
            return False
        await db.delete(q)
        await db.flush()
        return True

    @staticmethod
    async def start_attempt(
        db: AsyncSession, exam_id: int, employee_id: int
    ) -> ExamAttempt:
        """
        开始考试，创建 ExamAttempt 记录（作答会话）。

        员工每次考试触发一条 ExamAttempt 记录，初始答案为空 {}，分数为 0。
        考试必须处于 is_active=True 状态才能开始。

        参数:
            db (AsyncSession): 异步数据库会话
            exam_id (int): 考试 ID
            employee_id (int): 参加考试的员工 ID

        返回:
            ExamAttempt: 新建的考试记录（started_at 由数据库默认值自动填入）

        异常:
            ValueError: 考试不存在或已停用
        """
        exam = await ExamService.get_exam(db, exam_id)
        if not exam:
            raise ValueError("考试不存在")
        if not exam.is_active:
            raise ValueError("该考试已停用")

        # 防止重复开考：同一员工同一考试只能有一次未提交的作答
        existing_result = await db.execute(
            select(ExamAttempt).where(
                and_(
                    ExamAttempt.exam_id == exam_id,
                    ExamAttempt.employee_id == employee_id,
                    ExamAttempt.submitted_at.is_(None),
                )
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            raise ValueError("您已有一次进行中的考试，请先完成或提交当前考试")

        attempt = ExamAttempt(
            exam_id=exam_id,
            employee_id=employee_id,
            answers={},    # 初始为空答案字典
            score=0,
            passed=False,
        )
        db.add(attempt)
        await db.flush()
        await db.refresh(attempt)
        return attempt

    @staticmethod
    async def submit_attempt(
        db: AsyncSession,
        attempt_id: int,
        employee_id: int,
        data: SubmitAttemptRequest,
    ) -> ExamAttempt:
        """
        提交考试答案，系统自动批改，返回包含成绩的考试记录。

        批改算法：
            1. 遍历考试的所有题目；
            2. 从 data.answers 中按题目 ID（字符串键或整数键）取出提交答案；
            3. 答案规范化：去空格 → 大写 → 按逗号分割 → 排序后重新拼接；
               （多选题答案顺序不影响得分，如 "B,A" 与 "A,B" 视为等价）；
            4. 规范化后与标准答案比对，完全一致才得分；
            5. 总分达到 pass_score 则标记为合格；
            6. 合格时生成证书编号 CERT-{year}-{exam_id}-{attempt_id:05d}。

        安全检查：
            - 不允许代他人提交（employee_id 必须匹配 attempt.employee_id）；
            - 不允许重复提交（submitted_at 不为 None 时拒绝）。

        参数:
            db (AsyncSession): 异步数据库会话
            attempt_id (int): 考试记录 ID（由 start_attempt 返回）
            employee_id (int): 提交者员工 ID（用于权限校验）
            data (SubmitAttemptRequest): 答案 DTO，answers 为 {题目ID: 答案字符串} 字典

        返回:
            ExamAttempt: 更新后的考试记录，含最终分数、合格状态、证书编号

        异常:
            ValueError: 记录不存在 / 无权操作 / 重复提交 / 考试不存在
        """
        result = await db.execute(
            select(ExamAttempt).where(ExamAttempt.id == attempt_id)
        )
        attempt = result.scalar_one_or_none()
        if not attempt:
            raise ValueError("考试记录不存在")
        if attempt.employee_id != employee_id:
            raise ValueError("无权操作此考试记录")
        if attempt.submitted_at is not None:
            raise ValueError("该考试已提交，不能重复提交")

        exam = await ExamService.get_exam(db, attempt.exam_id)
        if not exam:
            raise ValueError("考试不存在")

        # 超时检查：若考试设有时限，超过时限则拒绝提交
        if exam.time_limit and attempt.started_at:
            from datetime import timedelta
            deadline = attempt.started_at + timedelta(minutes=exam.time_limit)
            if datetime.now(timezone.utc) > deadline:
                # 标记为已提交（超时），得分为0
                attempt.answers = {str(k): v for k, v in data.answers.items()}
                attempt.score = 0
                attempt.passed = False
                attempt.submitted_at = datetime.now(timezone.utc)
                await db.flush()
                await db.refresh(attempt)
                raise ValueError(
                    f"考试已超时（时限{exam.time_limit}分钟），答卷已自动提交，得分为0"
                )

        # 批改：计算总分
        total_score = 0
        for question in exam.questions:
            qid_str = str(question.id)
            # 兼容 key 为字符串或整数的情况（前端提交可能两种格式都有）
            submitted_ans = data.answers.get(qid_str) or data.answers.get(question.id)
            if submitted_ans is None:
                submitted_ans = ""
            # 规范化：去除空格、排序（多选题答案顺序不敏感）
            submitted_norm = ",".join(sorted(submitted_ans.replace(" ", "").upper().split(",")))
            correct_norm = ",".join(sorted(question.correct_answer.replace(" ", "").upper().split(",")))
            if submitted_norm == correct_norm:
                total_score += question.score

        passed = total_score >= exam.pass_score
        now = datetime.now(timezone.utc)

        # 生成合格证编号（仅合格时生成）
        cert_no: Optional[str] = None
        if passed:
            year = now.year
            cert_no = f"CERT-{year}-{exam.id}-{attempt.id:05d}"

        # 序列化 answers（确保 key 为字符串，保证 JSON 格式一致）
        serialized_answers = {str(k): v for k, v in data.answers.items()}

        attempt.answers = serialized_answers
        attempt.score = total_score
        attempt.passed = passed
        attempt.submitted_at = now
        attempt.certificate_no = cert_no

        await db.flush()
        await db.refresh(attempt)
        return attempt

    @staticmethod
    async def list_attempts(
        db: AsyncSession,
        employee_id: Optional[int] = None,
        exam_id: Optional[int] = None,
        passed: Optional[bool] = None,
    ) -> list[ExamAttempt]:
        """
        查询考试记录列表，支持多维度过滤。

        参数:
            db (AsyncSession): 异步数据库会话
            employee_id (int | None): 按员工过滤
            exam_id (int | None): 按考试过滤
            passed (bool | None): True=只返回合格记录，False=只返回不合格，None=全部

        返回:
            list[ExamAttempt]: 考试记录列表，按开始时间倒序排列
        """
        query = select(ExamAttempt).order_by(ExamAttempt.started_at.desc())
        if employee_id is not None:
            query = query.where(ExamAttempt.employee_id == employee_id)
        if exam_id is not None:
            query = query.where(ExamAttempt.exam_id == exam_id)
        if passed is not None:
            query = query.where(ExamAttempt.passed == passed)
        result = await db.execute(query)
        return list(result.scalars().all())
