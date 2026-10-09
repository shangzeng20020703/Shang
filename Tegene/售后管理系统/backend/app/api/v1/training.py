"""
培训管理 API 路由模块 - Training Management

本模块提供售后管理系统的培训管理全套 REST API，涵盖以下子域：

1. 培训课程 (TrainingCourse)
   - 管理内部/外部培训课程库，含分类、学时、讲师等信息
   - 路径前缀: /training/courses

2. 课程-胜任力关联 (CourseCompetency)
   - 将课程与胜任力项挂钩，支持基于能力差距的课程推荐
   - 路径: /training/courses/{id}/competencies  /training/courses/competencies/{link_id}

3. 培训计划 (TrainingPlan)
   - 编排年度/季度培训计划，包含计划内的课程安排列表
   - 路径前缀: /training/plans

4. 计划课程安排 (PlanCourse)
   - 培训计划下的具体课程安排（时间、讲师、地点等）
   - 路径: /training/plans/{id}/courses  /training/plans/courses/{pc_id}

5. 培训报名/记录 (TrainingEnrollment)
   - 员工报名参加培训，记录出勤、成绩和反馈
   - 路径前缀: /training/enrollments

6. 培训统计 (TrainingStats)
   - 总览及按部门统计培训覆盖率、完成率
   - 路径: /training/stats/overview  /training/stats/by-department

7. 课程推荐 (TrainingRecommendation)
   - 基于员工胜任力差距智能推荐课程
   - 路径: /training/recommendations/{employee_id}

8. 在线考试 (TrainingExam)
   - 管理课程配套考试，支持题库、在线答题和自动评分
   - 路径前缀: /training/exams  /training/attempts

架构层次: API 路由 → Service → Model/DB（业务逻辑全部在 services/training.py）

依赖注入:
  - get_db          : 提供异步数据库会话
  - get_current_user  : 验证 JWT Token，返回当前登录员工（大多数端点使用）
  - require_roles   : RBAC 角色校验（考试管理类写操作需要 admin 或 hr 角色）

权限层级:
  - 普通员工: 可查询课程/计划，报名/完成/反馈，参加考试
  - HR/Admin : 额外可创建考试、添加/删除题目

辅助函数（文件末尾）:
  - _fill_course_competency_names : 填充胜任力名称
  - _build_plan_out               : 构建计划响应（含课程名称）
  - _build_plan_course_out        : 构建计划课程响应
  - _build_enrollment_out         : 构建报名响应（含员工/课程名称）
  - _build_exam_out               : 构建考试响应（含题目数量/课程名称）
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.schemas.training import (
    TrainingCourseCreate,
    TrainingCourseUpdate,
    TrainingCourseOut,
    CourseCompetencyOut,
    CourseCompetencyCreate,
    TrainingPlanCreate,
    TrainingPlanUpdate,
    TrainingPlanStatusTransition,
    TrainingPlanOut,
    PlanCourseCreate,
    PlanCourseUpdate,
    PlanCourseOut,
    TrainingEnrollmentCreate,
    TrainingEnrollmentBatchCreate,
    TrainingEnrollmentOut,
    EnrollmentComplete,
    EnrollmentFeedback,
    TrainingStatsOverview,
    DepartmentTrainingStat,
    CourseRecommendation,
    TrainingExamCreate,
    TrainingExamOut,
    ExamQuestionCreate,
    ExamQuestionOut,
    ExamAttemptOut,
    SubmitAttemptRequest,
    ScoreDetail,
)
from app.services.training import (
    TrainingCourseService,
    CourseCompetencyService,
    TrainingPlanService,
    PlanCourseService,
    TrainingEnrollmentService,
    TrainingStatsService,
    TrainingRecommendationService,
    ExamService,
)

router = APIRouter(
    tags=["培训管理"],
    dependencies=[Depends(require_roles("admin", "hr"))],
)


# ============================================================
# TrainingCourse 培训课程 CRUD
# ============================================================

@router.post("/courses", response_model=TrainingCourseOut, status_code=status.HTTP_201_CREATED)
async def create_course(
    data: TrainingCourseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TrainingCourseOut:
    """
    创建培训课程

    HTTP 方法: POST
    路径: /training/courses

    用途:
        新建一门培训课程，添加到课程库。
        课程可关联胜任力项，供后续推荐系统使用（创建后通过 competencies 接口关联）。

    请求体 (TrainingCourseCreate):
        - name         : str   — 课程名称（必填）
        - category     : str   — 分类，如 "技术类" / "管理类" / "通用类"（必填）
        - description  : str   — 课程简介（可选）
        - duration_hours: float — 学时（可选）
        - instructor   : str   — 讲师/培训机构（可选）
        - is_mandatory : bool  — 是否必修，默认 False
        - is_active    : bool  — 是否上架，默认 True
        - max_enrollment: int  — 最大报名人数（可选）

    响应 (201 Created):
        TrainingCourseOut — 新建课程完整信息（含 competency_links 关联列表）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingCourseService.create(db, data)
    """
    course = await TrainingCourseService.create(db, data)
    out = TrainingCourseOut.model_validate(course)
    _fill_course_competency_names(course, out)
    return out


@router.get("/courses", response_model=list[TrainingCourseOut])
async def list_courses(
    category: Optional[str] = Query(default=None, description="课程分类"),
    is_mandatory: Optional[bool] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    keyword: Optional[str] = Query(default=None, description="搜索课程名"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TrainingCourseOut]:
    """
    查询培训课程列表

    HTTP 方法: GET
    路径: /training/courses

    用途:
        获取课程库列表，支持多维度过滤和关键词搜索。
        前端课程选择器、培训报名页面均调用此接口。

    查询参数:
        - category     : str  — 按课程分类筛选（可选），如 "技术类"
        - is_mandatory : bool — 是否必修：true=必修，false=选修（可选）
        - is_active    : bool — 是否上架：true=上架，false=下架（可选）
        - keyword      : str  — 模糊搜索课程名（可选）

    响应 (200 OK):
        list[TrainingCourseOut] — 满足条件的课程列表（含胜任力关联）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingCourseService.list_all(db, category, is_mandatory, is_active, keyword)
    """
    courses = await TrainingCourseService.list_all(db, category, is_mandatory, is_active, keyword)
    result = []
    for c in courses:
        out = TrainingCourseOut.model_validate(c)
        _fill_course_competency_names(c, out)
        result.append(out)
    return result


@router.get("/courses/{course_id}", response_model=TrainingCourseOut)
async def get_course(
    course_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TrainingCourseOut:
    """
    获取课程详情

    HTTP 方法: GET
    路径: /training/courses/{course_id}

    用途:
        根据课程 ID 获取单门课程的完整信息，包含关联的胜任力项列表。

    路径参数:
        - course_id : int — 课程主键 ID

    响应 (200 OK):
        TrainingCourseOut — 课程详情（含 competency_links 列表，每项含胜任力名称）

    错误响应:
        404 Not Found — 课程不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingCourseService.get(db, course_id)
    """
    course = await TrainingCourseService.get(db, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="课程不存在")
    out = TrainingCourseOut.model_validate(course)
    _fill_course_competency_names(course, out)
    return out


@router.put("/courses/{course_id}", response_model=TrainingCourseOut)
async def update_course(
    course_id: int,
    data: TrainingCourseUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TrainingCourseOut:
    """
    更新培训课程

    HTTP 方法: PUT
    路径: /training/courses/{course_id}

    用途:
        修改课程的基本属性。注意：胜任力关联通过独立端点管理。

    路径参数:
        - course_id : int — 课程主键 ID

    请求体 (TrainingCourseUpdate):
        所有字段均为可选，仅提供需要修改的字段：
        - name / category / description / duration_hours / instructor
        - is_mandatory / is_active / max_enrollment

    响应 (200 OK):
        TrainingCourseOut — 更新后的课程信息

    错误响应:
        404 Not Found — 课程不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingCourseService.update(db, course_id, data)
    """
    course = await TrainingCourseService.update(db, course_id, data)
    if not course:
        raise HTTPException(status_code=404, detail="课程不存在")
    out = TrainingCourseOut.model_validate(course)
    _fill_course_competency_names(course, out)
    return out


@router.delete("/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(
    course_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    删除培训课程

    HTTP 方法: DELETE
    路径: /training/courses/{course_id}

    用途:
        下架并删除课程。若课程已有报名记录，Service 层应拒绝删除。

    路径参数:
        - course_id : int — 课程主键 ID

    响应 (204 No Content):
        无响应体

    错误响应:
        404 Not Found — 课程不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingCourseService.delete(db, course_id)
    """
    success = await TrainingCourseService.delete(db, course_id)
    if not success:
        raise HTTPException(status_code=404, detail="课程不存在")


# ---- Course-Competency Links ----

@router.post("/courses/{course_id}/competencies", response_model=CourseCompetencyOut, status_code=status.HTTP_201_CREATED)
async def add_course_competency(
    course_id: int,
    data: CourseCompetencyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> CourseCompetencyOut:
    """
    给课程关联胜任力项

    HTTP 方法: POST
    路径: /training/courses/{course_id}/competencies

    用途:
        建立课程与胜任力项之间的映射关系，并指定本课程能提升该胜任力的级差。
        推荐引擎据此为胜任力不足的员工推荐匹配课程。

    路径参数:
        - course_id : int — 课程主键 ID

    请求体 (CourseCompetencyCreate):
        - competency_item_id   : int — 胜任力项 ID（必填）
        - target_level_delta   : int — 完成本课程预期提升的胜任力级别差值，
                                       如 1 表示提升 1 级（可选，默认 1）

    响应 (201 Created):
        CourseCompetencyOut — 关联记录（含 competency_item_name 胜任力项名称）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        CourseCompetencyService.add_link(db, course_id, competency_item_id, target_level_delta)
    """
    link = await CourseCompetencyService.add_link(
        db, course_id, data.competency_item_id, data.target_level_delta
    )
    out = CourseCompetencyOut.model_validate(link)
    if link.competency_item:
        out.competency_item_name = link.competency_item.name
    return out


@router.delete("/courses/competencies/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_course_competency(
    link_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    移除课程-胜任力关联

    HTTP 方法: DELETE
    路径: /training/courses/competencies/{link_id}

    用途:
        解除课程与某胜任力项之间的映射关系。

    路径参数:
        - link_id : int — 课程-胜任力关联记录主键 ID

    响应 (204 No Content):
        无响应体

    错误响应:
        404 Not Found — 关联记录不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        CourseCompetencyService.remove_link(db, link_id)
    """
    success = await CourseCompetencyService.remove_link(db, link_id)
    if not success:
        raise HTTPException(status_code=404, detail="关联不存在")


# ============================================================
# TrainingPlan 培训计划 CRUD
# ============================================================

@router.post("/plans", response_model=TrainingPlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(
    data: TrainingPlanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TrainingPlanOut:
    """
    创建培训计划

    HTTP 方法: POST
    路径: /training/plans

    用途:
        新建一个培训计划（如"2025年Q2技术提升计划"）。
        计划初始状态为 draft，可包含多门课程安排（通过 plan_courses 接口追加）。

    请求体 (TrainingPlanCreate):
        - name        : str  — 计划名称（必填）
        - year        : int  — 计划年份（必填）
        - plan_type   : str  — 计划类型：annual（年度）/ quarterly（季度）/ special（专项）
        - description : str  — 描述（可选）
        - start_date  : date — 计划开始日期（可选）
        - end_date    : date — 计划结束日期（可选）
        - budget      : float — 预算金额（可选）
        - target_dept_ids : list[int] — 目标部门 ID 列表（可选）

    响应 (201 Created):
        TrainingPlanOut — 含计划 ID、状态（draft）及课程列表（此时为空）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingPlanService.create(db, data)
    """
    plan = await TrainingPlanService.create(db, data)
    return _build_plan_out(plan)


@router.get("/plans", response_model=list[TrainingPlanOut])
async def list_plans(
    year: Optional[int] = Query(default=None),
    plan_type: Optional[str] = Query(default=None),
    plan_status: Optional[str] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TrainingPlanOut]:
    """
    查询培训计划列表

    HTTP 方法: GET
    路径: /training/plans

    用途:
        获取培训计划列表，支持按年份、类型、状态过滤。
        响应中每个计划包含其课程安排列表（plan_courses，含课程名称）。

    查询参数:
        - year       : int — 计划年份（可选）
        - plan_type  : str — 计划类型：annual / quarterly / special（可选）
        - status     : str — 计划状态：draft / approved / in_progress / completed / cancelled
                             （URL 参数名为 status，内部变量名 plan_status）（可选）

    响应 (200 OK):
        list[TrainingPlanOut] — 满足条件的计划列表

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingPlanService.list_all(db, year, plan_type, plan_status)
    """
    plans = await TrainingPlanService.list_all(db, year, plan_type, plan_status)
    return [_build_plan_out(p) for p in plans]


@router.get("/plans/{plan_id}", response_model=TrainingPlanOut)
async def get_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TrainingPlanOut:
    """
    获取培训计划详情

    HTTP 方法: GET
    路径: /training/plans/{plan_id}

    用途:
        根据计划 ID 获取单个培训计划的完整信息，包含所有课程安排。

    路径参数:
        - plan_id : int — 计划主键 ID

    响应 (200 OK):
        TrainingPlanOut — 计划详情（含 plan_courses 列表，每项附课程名称和分类）

    错误响应:
        404 Not Found — 计划不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingPlanService.get(db, plan_id)
    """
    plan = await TrainingPlanService.get(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="培训计划不存在")
    return _build_plan_out(plan)


@router.put("/plans/{plan_id}", response_model=TrainingPlanOut)
async def update_plan(
    plan_id: int,
    data: TrainingPlanUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TrainingPlanOut:
    """
    更新培训计划

    HTTP 方法: PUT
    路径: /training/plans/{plan_id}

    用途:
        修改培训计划的基本属性（名称、日期、预算、描述等）。
        课程安排通过 /plans/{id}/courses 接口单独管理。

    路径参数:
        - plan_id : int — 计划主键 ID

    请求体 (TrainingPlanUpdate):
        所有字段均可选：name / year / plan_type / description /
        start_date / end_date / budget / target_dept_ids

    响应 (200 OK):
        TrainingPlanOut — 更新后的计划信息

    错误响应:
        404 Not Found — 计划不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingPlanService.update(db, plan_id, data)
    """
    plan = await TrainingPlanService.update(db, plan_id, data)
    if not plan:
        raise HTTPException(status_code=404, detail="培训计划不存在")
    return _build_plan_out(plan)


@router.post("/plans/{plan_id}/transition", response_model=TrainingPlanOut)
async def transition_plan_status(
    plan_id: int,
    data: TrainingPlanStatusTransition,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TrainingPlanOut:
    """
    培训计划状态流转

    HTTP 方法: POST
    路径: /training/plans/{plan_id}/transition

    用途:
        推进培训计划的状态机。合法状态转换：
          draft → approved        （HR/管理层审批通过）
          approved → in_progress  （培训正式开始）
          in_progress → completed （所有课程完成）
          任意状态 → cancelled    （取消计划）
        Service 层会校验转换合法性。

    路径参数:
        - plan_id : int — 计划主键 ID

    请求体 (TrainingPlanStatusTransition):
        - action : str — 动作：approve / start / complete / cancel

    响应 (200 OK):
        TrainingPlanOut — 状态更新后的计划信息

    错误响应:
        400 Bad Request — 不合法的状态转换
        404 Not Found   — 计划不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingPlanService.transition_status(db, plan_id, data)
    """
    try:
        plan = await TrainingPlanService.transition_status(db, plan_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not plan:
        raise HTTPException(status_code=404, detail="培训计划不存在")
    return _build_plan_out(plan)


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    删除培训计划

    HTTP 方法: DELETE
    路径: /training/plans/{plan_id}

    用途:
        删除培训计划。Service 层通常限制仅 draft 状态的计划可删除。
        如已有报名记录，应拒绝删除。

    路径参数:
        - plan_id : int — 计划主键 ID

    响应 (204 No Content):
        无响应体

    错误响应:
        400 Bad Request — 计划状态不允许删除
        404 Not Found   — 计划不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingPlanService.delete(db, plan_id)
    """
    try:
        success = await TrainingPlanService.delete(db, plan_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not success:
        raise HTTPException(status_code=404, detail="培训计划不存在")


# ---- Plan-Course ----

@router.post("/plans/{plan_id}/courses", response_model=PlanCourseOut, status_code=status.HTTP_201_CREATED)
async def add_plan_course(
    plan_id: int,
    data: PlanCourseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PlanCourseOut:
    """
    给培训计划添加课程安排

    HTTP 方法: POST
    路径: /training/plans/{plan_id}/courses

    用途:
        在培训计划下添加一门课程的具体安排（时间、讲师、地点、课时数等）。
        同一课程可在计划中出现多次（不同场次）。

    路径参数:
        - plan_id : int — 计划主键 ID

    请求体 (PlanCourseCreate):
        - course_id      : int   — 课程 ID（必填）
        - scheduled_date : date  — 计划上课日期（可选）
        - location       : str   — 上课地点/方式（可选），如"线上/北京总部"
        - instructor     : str   — 本次讲师（可选，可覆盖课程默认讲师）
        - duration_hours : float — 本次课时（可选）
        - max_enrollment : int   — 本次最大报名人数（可选）
        - notes          : str   — 备注（可选）

    响应 (201 Created):
        PlanCourseOut — 课程安排记录（含 course_name、course_category）

    错误响应:
        404 Not Found — 计划不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PlanCourseService.add_course(db, plan_id, data)
    """
    plan = await TrainingPlanService.get(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="培训计划不存在")
    pc = await PlanCourseService.add_course(db, plan_id, data)
    return _build_plan_course_out(pc)


@router.get("/plans/{plan_id}/courses", response_model=list[PlanCourseOut])
async def list_plan_courses(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PlanCourseOut]:
    """
    查询培训计划下的课程安排列表

    HTTP 方法: GET
    路径: /training/plans/{plan_id}/courses

    用途:
        获取某培训计划的所有课程安排，用于计划详情页展示和排课管理。

    路径参数:
        - plan_id : int — 计划主键 ID

    响应 (200 OK):
        list[PlanCourseOut] — 课程安排列表（每项含 course_name、course_category）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PlanCourseService.list_by_plan(db, plan_id)
    """
    pcs = await PlanCourseService.list_by_plan(db, plan_id)
    return [_build_plan_course_out(pc) for pc in pcs]


@router.put("/plans/courses/{pc_id}", response_model=PlanCourseOut)
async def update_plan_course(
    pc_id: int,
    data: PlanCourseUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PlanCourseOut:
    """
    更新计划课程安排

    HTTP 方法: PUT
    路径: /training/plans/courses/{pc_id}

    用途:
        修改培训计划中某门课程安排的具体信息（如调整上课日期、地点、讲师）。

    路径参数:
        - pc_id : int — 计划课程安排记录主键 ID

    请求体 (PlanCourseUpdate):
        所有字段可选：scheduled_date / location / instructor /
        duration_hours / max_enrollment / notes

    响应 (200 OK):
        PlanCourseOut — 更新后的课程安排（含 course_name、course_category）

    错误响应:
        404 Not Found — 记录不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PlanCourseService.update_course(db, pc_id, data)
    """
    pc = await PlanCourseService.update_course(db, pc_id, data)
    if not pc:
        raise HTTPException(status_code=404, detail="计划课程不存在")
    return _build_plan_course_out(pc)


@router.delete("/plans/courses/{pc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_plan_course(
    pc_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    从培训计划移除课程安排

    HTTP 方法: DELETE
    路径: /training/plans/courses/{pc_id}

    用途:
        删除培训计划中的某门课程安排。
        若该课程安排已有报名记录，Service 层应拒绝删除。

    路径参数:
        - pc_id : int — 计划课程安排记录主键 ID

    响应 (204 No Content):
        无响应体

    错误响应:
        404 Not Found — 记录不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PlanCourseService.remove_course(db, pc_id)
    """
    success = await PlanCourseService.remove_course(db, pc_id)
    if not success:
        raise HTTPException(status_code=404, detail="计划课程不存在")


# ============================================================
# TrainingEnrollment 培训报名/记录
# ============================================================

@router.post("/enrollments", response_model=TrainingEnrollmentOut, status_code=status.HTTP_201_CREATED)
async def create_enrollment(
    data: TrainingEnrollmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TrainingEnrollmentOut:
    """
    创建培训报名记录

    HTTP 方法: POST
    路径: /training/enrollments

    用途:
        为员工创建单条培训报名记录（单人报名单门课程）。
        初始状态为 enrolled（已报名），完成后状态变为 completed。
        Service 层会检查课程是否已报满（max_enrollment）。

    请求体 (TrainingEnrollmentCreate):
        - employee_id   : int — 报名员工 ID（必填）
        - course_id     : int — 报名课程 ID（必填）
        - plan_course_id: int — 对应的计划课程安排 ID（可选，可为自由报名）

    响应 (201 Created):
        TrainingEnrollmentOut — 报名记录（含 employee_name、course_name）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingEnrollmentService.create(db, data)
    """
    enrollment = await TrainingEnrollmentService.create(db, data)
    return _build_enrollment_out(enrollment)


@router.post("/enrollments/batch", response_model=list[TrainingEnrollmentOut])
async def create_enrollments_batch(
    data: TrainingEnrollmentBatchCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TrainingEnrollmentOut]:
    """
    批量创建培训报名

    HTTP 方法: POST
    路径: /training/enrollments/batch

    用途:
        一次性为多名员工批量报名同一门课程（或同一计划课程安排）。
        常见场景：HR 将某部门所有员工批量加入必修培训。
        已报名的员工会被 Service 层跳过（幂等处理）。

    请求体 (TrainingEnrollmentBatchCreate):
        - employee_ids   : list[int] — 员工 ID 列表（必填）
        - course_id      : int       — 课程 ID（必填）
        - plan_course_id : int       — 计划课程安排 ID（可选）

    响应 (200 OK):
        list[TrainingEnrollmentOut] — 成功创建的报名记录列表

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingEnrollmentService.create_batch(db, employee_ids, course_id, plan_course_id)
    """
    enrollments = await TrainingEnrollmentService.create_batch(
        db, data.employee_ids, data.course_id, data.plan_course_id
    )
    return [_build_enrollment_out(e) for e in enrollments]


@router.get("/enrollments/course/{course_id}", response_model=list[TrainingEnrollmentOut])
async def list_enrollments_by_course(
    course_id: int,
    enrollment_status: Optional[str] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TrainingEnrollmentOut]:
    """
    按课程查询报名记录

    HTTP 方法: GET
    路径: /training/enrollments/course/{course_id}

    用途:
        查询某门课程的所有报名情况，用于签到管理、出勤统计和课程效果评估。

    路径参数:
        - course_id : int — 课程主键 ID

    查询参数:
        - status : str — 报名状态过滤：enrolled / completed / cancelled
                         （URL 参数名为 status，内部变量名 enrollment_status）（可选）

    响应 (200 OK):
        list[TrainingEnrollmentOut] — 该课程的报名记录列表（含员工姓名）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingEnrollmentService.list_by_course(db, course_id, enrollment_status)
    """
    enrollments = await TrainingEnrollmentService.list_by_course(db, course_id, enrollment_status)
    return [_build_enrollment_out(e) for e in enrollments]


@router.get("/enrollments/employee/{employee_id}", response_model=list[TrainingEnrollmentOut])
async def list_enrollments_by_employee(
    employee_id: int,
    enrollment_status: Optional[str] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TrainingEnrollmentOut]:
    """
    按员工查询培训记录

    HTTP 方法: GET
    路径: /training/enrollments/employee/{employee_id}

    用途:
        查询指定员工的全部培训参与记录，用于员工培训档案、绩效考核参考。

    路径参数:
        - employee_id : int — 员工主键 ID

    查询参数:
        - status : str — 报名状态过滤（可选）：enrolled / completed / cancelled

    响应 (200 OK):
        list[TrainingEnrollmentOut] — 该员工的培训记录列表（含课程名称）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingEnrollmentService.list_by_employee(db, employee_id, enrollment_status)
    """
    enrollments = await TrainingEnrollmentService.list_by_employee(db, employee_id, enrollment_status)
    return [_build_enrollment_out(e) for e in enrollments]


@router.get("/enrollments/me", response_model=list[TrainingEnrollmentOut])
async def list_my_enrollments(
    enrollment_status: Optional[str] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TrainingEnrollmentOut]:
    """
    查询我自己的培训记录

    HTTP 方法: GET
    路径: /training/enrollments/me

    用途:
        员工自助查看个人培训参与历史（ESS 模块）。
        从 JWT Token 自动识别当前用户，无需传入员工 ID。

    查询参数:
        - status : str — 报名状态过滤（可选）：enrolled / completed / cancelled

    响应 (200 OK):
        list[TrainingEnrollmentOut] — 当前登录员工的培训记录

    权限:
        需要登录（Depends(get_current_user)），只返回当前用户自己的数据

    对应 Service:
        TrainingEnrollmentService.list_by_employee(db, current_user.id, enrollment_status)
    """
    enrollments = await TrainingEnrollmentService.list_by_employee(
        db, current_user.id, enrollment_status
    )
    return [_build_enrollment_out(e) for e in enrollments]


@router.post("/enrollments/{enrollment_id}/complete", response_model=TrainingEnrollmentOut)
async def complete_enrollment(
    enrollment_id: int,
    data: EnrollmentComplete,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TrainingEnrollmentOut:
    """
    完成培训（标记已完成并记录成绩）

    HTTP 方法: POST
    路径: /training/enrollments/{enrollment_id}/complete

    用途:
        将报名记录标记为已完成，并记录出勤情况和培训成绩。
        仅 enrolled 状态的记录可完成（Service 层校验）。
        完成后会同步更新员工对应胜任力项的级别（若课程有关联胜任力）。

    路径参数:
        - enrollment_id : int — 报名记录主键 ID

    请求体 (EnrollmentComplete):
        - completion_date : date  — 完成日期（必填）
        - score          : float  — 培训成绩（可选，0-100）
        - attendance_rate : float — 出勤率，如 0.9 表示 90%（可选）
        - notes          : str   — 备注（可选）

    响应 (200 OK):
        TrainingEnrollmentOut — 更新后的报名记录（状态变为 completed）

    错误响应:
        400 Bad Request — 状态不允许完成（如已完成或已取消）
        404 Not Found   — 记录不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingEnrollmentService.complete(db, enrollment_id, data)
    """
    try:
        enrollment = await TrainingEnrollmentService.complete(db, enrollment_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not enrollment:
        raise HTTPException(status_code=404, detail="报名记录不存在")
    return _build_enrollment_out(enrollment)


@router.post("/enrollments/{enrollment_id}/feedback", response_model=TrainingEnrollmentOut)
async def submit_enrollment_feedback(
    enrollment_id: int,
    data: EnrollmentFeedback,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TrainingEnrollmentOut:
    """
    提交培训反馈

    HTTP 方法: POST
    路径: /training/enrollments/{enrollment_id}/feedback

    用途:
        员工对已参加的培训课程提交评价反馈（满意度评分和文字评价）。
        通常在培训完成后填写，用于培训效果评估和课程改进参考。

    路径参数:
        - enrollment_id : int — 报名记录主键 ID

    请求体 (EnrollmentFeedback):
        - rating   : int — 满意度评分，1-5 星（必填）
        - comment  : str — 文字评价（可选）

    响应 (200 OK):
        TrainingEnrollmentOut — 更新后的报名记录（含反馈内容）

    错误响应:
        404 Not Found — 记录不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingEnrollmentService.submit_feedback(db, enrollment_id, data)
    """
    enrollment = await TrainingEnrollmentService.submit_feedback(db, enrollment_id, data)
    if not enrollment:
        raise HTTPException(status_code=404, detail="报名记录不存在")
    return _build_enrollment_out(enrollment)


@router.post("/enrollments/{enrollment_id}/cancel", response_model=TrainingEnrollmentOut)
async def cancel_enrollment(
    enrollment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TrainingEnrollmentOut:
    """
    取消培训报名

    HTTP 方法: POST
    路径: /training/enrollments/{enrollment_id}/cancel

    用途:
        取消员工的培训报名。仅 enrolled（已报名未完成）状态可取消。
        取消后名额释放，其他员工可重新报名（Service 层处理）。

    路径参数:
        - enrollment_id : int — 报名记录主键 ID

    响应 (200 OK):
        TrainingEnrollmentOut — 更新后的报名记录（状态变为 cancelled）

    错误响应:
        400 Bad Request — 状态不允许取消（如已完成）
        404 Not Found   — 记录不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingEnrollmentService.cancel(db, enrollment_id)
    """
    try:
        enrollment = await TrainingEnrollmentService.cancel(db, enrollment_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not enrollment:
        raise HTTPException(status_code=404, detail="报名记录不存在")
    return _build_enrollment_out(enrollment)


# ============================================================
# Training Stats 培训统计
# ============================================================

@router.get("/stats/overview", response_model=TrainingStatsOverview)
async def get_training_stats_overview(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TrainingStatsOverview:
    """
    获取培训总览统计

    HTTP 方法: GET
    路径: /training/stats/overview

    用途:
        返回全公司培训情况的汇总数据，用于管理驾驶舱和培训年度报告。
        统计维度包括：课程总数、计划总数、报名人次、完成人次、总培训学时、
        平均满意度评分等。

    响应 (200 OK):
        TrainingStatsOverview — 总览统计数据

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingStatsService.get_overview(db)
    """
    return await TrainingStatsService.get_overview(db)


@router.get("/stats/by-department", response_model=list[DepartmentTrainingStat])
async def get_training_stats_by_department(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[DepartmentTrainingStat]:
    """
    按部门统计培训完成情况

    HTTP 方法: GET
    路径: /training/stats/by-department

    用途:
        按部门维度汇总培训覆盖率、完成率和人均学时，
        用于横向对比各部门培训投入情况和发现薄弱环节。

    响应 (200 OK):
        list[DepartmentTrainingStat] — 各部门培训统计数据列表
        每项包含：部门名称、应训人数、实训人数、完成率、人均学时等

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingStatsService.get_by_department(db)
    """
    return await TrainingStatsService.get_by_department(db)


# ============================================================
# Training Recommendation 课程推荐
# ============================================================

@router.get("/recommendations/{employee_id}", response_model=list[CourseRecommendation])
async def get_training_recommendations(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[CourseRecommendation]:
    """
    基于胜任力差距推荐培训课程

    HTTP 方法: GET
    路径: /training/recommendations/{employee_id}

    用途:
        分析员工当前胜任力评估结果与岗位要求的差距，
        从课程库中筛选出能弥补差距的课程并按匹配度排序推荐。
        算法：针对每项胜任力差距，查找关联该胜任力且 target_level_delta 合适的课程。

    路径参数:
        - employee_id : int — 员工主键 ID

    响应 (200 OK):
        list[CourseRecommendation] — 推荐课程列表，每项含：
          - course_id / course_name / category
          - competency_gap : str  — 匹配的胜任力差距说明
          - match_score    : float — 匹配度评分

    错误响应:
        404 Not Found — 员工不存在或无胜任力评估数据

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        TrainingRecommendationService.recommend_courses(db, employee_id)
    """
    try:
        return await TrainingRecommendationService.recommend_courses(db, employee_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================
# TrainingExam 在线考试
# ============================================================

@router.get("/exams", response_model=list[TrainingExamOut])
async def list_exams(
    course_id: Optional[int] = Query(default=None, description="按课程过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TrainingExamOut]:
    """
    查询考试列表

    HTTP 方法: GET
    路径: /training/exams

    用途:
        获取所有在线考试，可按课程过滤。
        响应中附带题目数量（questions_count）和课程名称（course_name）。

    查询参数:
        - course_id : int — 按课程过滤，只返回该课程关联的考试（可选）

    响应 (200 OK):
        list[TrainingExamOut] — 考试列表，每项含题目数量和课程名称

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        ExamService.list_exams(db, course_id=course_id)
    """
    exams = await ExamService.list_exams(db, course_id=course_id)
    return [_build_exam_out(e) for e in exams]


@router.post("/exams", response_model=TrainingExamOut, status_code=status.HTTP_201_CREATED)
async def create_exam(
    data: TrainingExamCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
) -> TrainingExamOut:
    """
    创建考试（hr/admin 权限）

    HTTP 方法: POST
    路径: /training/exams

    用途:
        为某门课程创建配套在线考试，设置考试基本规则（时限、合格分、最大次数）。
        创建考试后，通过 /exams/{id}/questions 接口添加题目。

    请求体 (TrainingExamCreate):
        - course_id    : int   — 关联课程 ID（必填）
        - title        : str   — 考试标题（必填）
        - description  : str   — 考试说明（可选）
        - time_limit   : int   — 限时（分钟），0 表示不限时（可选）
        - passing_score: float — 合格分数线，如 60.0（可选）
        - max_attempts : int   — 最大参考次数，0 表示不限（可选）

    响应 (201 Created):
        TrainingExamOut — 考试信息（questions_count 初始为 0）

    权限:
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）
        普通员工无法创建考试

    对应 Service:
        ExamService.create_exam(db, data)
    """
    exam = await ExamService.create_exam(db, data)
    return _build_exam_out(exam)


@router.get("/exams/{exam_id}/questions", response_model=list[ExamQuestionOut])
async def list_exam_questions(
    exam_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[ExamQuestionOut]:
    """
    获取考试题目列表（不含答案）

    HTTP 方法: GET
    路径: /training/exams/{exam_id}/questions

    用途:
        返回考试的所有题目（按 order 和 id 排序），供参考者答题使用。
        重要：响应中不包含正确答案（correct_answer），防止泄题。
        管理员查看完整题目（含答案）需通过后台专用接口。

    路径参数:
        - exam_id : int — 考试主键 ID

    响应 (200 OK):
        list[ExamQuestionOut] — 题目列表（按 order 升序），每题含：
          - id / question_text / question_type（单选/多选/判断）
          - options / score / order
          注意：correct_answer 字段不对外暴露

    错误响应:
        404 Not Found — 考试不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        ExamService.get_exam(db, exam_id)（通过 exam.questions 获取题目列表）
    """
    exam = await ExamService.get_exam(db, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="考试不存在")
    questions = sorted(exam.questions, key=lambda q: (q.order, q.id))
    return [ExamQuestionOut.model_validate(q) for q in questions]


@router.post("/exams/{exam_id}/questions", response_model=ExamQuestionOut, status_code=status.HTTP_201_CREATED)
async def add_exam_question(
    exam_id: int,
    data: ExamQuestionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
) -> ExamQuestionOut:
    """
    添加考试题目（hr/admin 权限）

    HTTP 方法: POST
    路径: /training/exams/{exam_id}/questions

    用途:
        向指定考试添加一道题目，支持单选、多选、判断等题型。
        题目含正确答案（存储在数据库，不对外暴露给参考者）。

    路径参数:
        - exam_id : int — 考试主键 ID

    请求体 (ExamQuestionCreate):
        - question_text : str  — 题目内容（必填）
        - question_type : str  — 题型：single_choice / multi_choice / true_false（必填）
        - options       : dict — 选项，如 {"A": "选项1", "B": "选项2", ...}（选择题必填）
        - correct_answer: str  — 正确答案，如 "A" 或 "A,B"（多选用逗号分隔）（必填）
        - score         : float — 本题分值（必填）
        - order         : int  — 题目排序（可选，默认追加到末尾）
        - explanation   : str  — 答题解析（可选）

    响应 (201 Created):
        ExamQuestionOut — 新建题目信息（含 correct_answer，仅管理员可见）

    错误响应:
        404 Not Found — 考试不存在

    权限:
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）

    对应 Service:
        ExamService.add_question(db, exam_id, data)
    """
    exam = await ExamService.get_exam(db, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="考试不存在")
    question = await ExamService.add_question(db, exam_id, data)
    return ExamQuestionOut.model_validate(question)


@router.delete("/exams/{exam_id}/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_exam_question(
    exam_id: int,
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
) -> None:
    """
    删除考试题目（hr/admin 权限）

    HTTP 方法: DELETE
    路径: /training/exams/{exam_id}/questions/{question_id}

    用途:
        删除考试中的某道题目。若该考试已有作答记录，通常应限制删除。

    路径参数:
        - exam_id     : int — 考试主键 ID（用于路由语义，实际删除仅用 question_id）
        - question_id : int — 题目主键 ID

    响应 (204 No Content):
        无响应体

    错误响应:
        404 Not Found — 题目不存在

    权限:
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）

    对应 Service:
        ExamService.delete_question(db, question_id)
    """
    success = await ExamService.delete_question(db, question_id)
    if not success:
        raise HTTPException(status_code=404, detail="题目不存在")


@router.post("/exams/{exam_id}/start", response_model=ExamAttemptOut, status_code=status.HTTP_201_CREATED)
async def start_exam(
    exam_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ExamAttemptOut:
    """
    开始参加考试（返回题目，不含答案）

    HTTP 方法: POST
    路径: /training/exams/{exam_id}/start

    用途:
        当前登录员工开始参加指定考试，系统创建一条作答记录（ExamAttempt）
        并记录开始时间。返回题目列表（不含正确答案）。
        Service 层会校验：
          1. 考试是否存在
          2. 该员工是否已超过最大参考次数（max_attempts）

    路径参数:
        - exam_id : int — 考试主键 ID

    响应 (201 Created):
        ExamAttemptOut — 作答记录（含 exam_title、employee_name、题目列表）
        注意：题目列表中不含 correct_answer

    错误响应:
        400 Bad Request — 超过最大参考次数或考试不可用

    权限:
        需要登录（Depends(get_current_user)），考生为当前登录用户

    对应 Service:
        ExamService.start_attempt(db, exam_id, current_user.id)
    """
    try:
        attempt = await ExamService.start_attempt(db, exam_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    exam = attempt.exam
    out = ExamAttemptOut.model_validate(attempt)
    if exam:
        out.exam_title = exam.title
    emp = getattr(attempt, "employee", None)
    if emp:
        out.employee_name = emp.name
    return out


@router.post("/attempts/{attempt_id}/submit", response_model=ExamAttemptOut)
async def submit_attempt(
    attempt_id: int,
    data: SubmitAttemptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ExamAttemptOut:
    """
    提交考试答案，返回成绩单（含每题对错详情）

    HTTP 方法: POST
    路径: /training/attempts/{attempt_id}/submit

    用途:
        考生提交答卷，系统自动评分并返回成绩单。
        评分逻辑：逐题比对答案（多选题答案归一化后比对），计算总得分，
        与 passing_score 对比判断是否通过。
        成绩单包含每题的作答结果（your_answer vs correct_answer，是否正确）。

    路径参数:
        - attempt_id : int — 作答记录主键 ID

    请求体 (SubmitAttemptRequest):
        - answers : dict[str, str] — 答案字典，key 为题目 ID（字符串），value 为答案
                                      示例：{"1": "A", "2": "B,C", "3": "True"}

    响应 (200 OK):
        ExamAttemptOut — 完整成绩单，含：
          - score       : float  — 本次得分
          - passed      : bool   — 是否通过
          - score_detail: list[ScoreDetail] — 每题对错详情列表
            每项含：question_id / your_answer / correct_answer / correct（bool）
          - exam_title / employee_name

    错误响应:
        400 Bad Request — 非本人作答记录，或答卷已提交（重复提交）

    权限:
        需要登录（Depends(get_current_user)），只能提交自己的作答

    对应 Service:
        ExamService.submit_attempt(db, attempt_id, current_user.id, data)
    """
    try:
        attempt = await ExamService.submit_attempt(db, attempt_id, current_user.id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    out = ExamAttemptOut.model_validate(attempt)
    exam = attempt.exam
    if exam:
        out.exam_title = exam.title
        # 构建成绩单详情
        details: list[ScoreDetail] = []
        submitted = {str(k): v for k, v in attempt.answers.items()}
        for q in exam.questions:
            your_ans = submitted.get(str(q.id), "")
            submitted_norm = ",".join(sorted(your_ans.replace(" ", "").upper().split(","))) if your_ans else ""
            correct_norm = ",".join(sorted(q.correct_answer.replace(" ", "").upper().split(",")))
            details.append(ScoreDetail(
                question_id=q.id,
                your_answer=your_ans,
                correct_answer=q.correct_answer,
                correct=(submitted_norm == correct_norm),
            ))
        out.score_detail = details
    emp = getattr(attempt, "employee", None)
    if emp:
        out.employee_name = emp.name
    return out


@router.get("/attempts", response_model=list[ExamAttemptOut])
async def list_attempts(
    employee_id: Optional[int] = Query(default=None),
    exam_id: Optional[int] = Query(default=None),
    passed: Optional[bool] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[ExamAttemptOut]:
    """
    查询考试作答记录

    HTTP 方法: GET
    路径: /training/attempts

    用途:
        查询考试作答历史，支持按员工、考试和通过状态过滤。
        HR 用于统计各员工考试通过情况；员工用于查看自己的历次成绩。
        响应中附带 exam_title 和 employee_name 便于展示。

    查询参数:
        - employee_id : int  — 按员工过滤（可选）
        - exam_id     : int  — 按考试过滤（可选）
        - passed      : bool — 按是否通过过滤：true=已通过，false=未通过（可选）

    响应 (200 OK):
        list[ExamAttemptOut] — 作答记录列表（含 exam_title、employee_name、得分）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        ExamService.list_attempts(db, employee_id=employee_id, exam_id=exam_id, passed=passed)
    """
    attempts = await ExamService.list_attempts(db, employee_id=employee_id, exam_id=exam_id, passed=passed)
    result = []
    for attempt in attempts:
        out = ExamAttemptOut.model_validate(attempt)
        exam = getattr(attempt, "exam", None)
        if exam:
            out.exam_title = exam.title
        emp = getattr(attempt, "employee", None)
        if emp:
            out.employee_name = emp.name
        result.append(out)
    return result


# ============================================================
# Helper functions（辅助函数，不对外暴露为路由）
# ============================================================

def _fill_course_competency_names(course: object, out: TrainingCourseOut) -> None:
    """
    填充课程响应中各胜任力关联项的名称。

    遍历 out.competency_links 列表，从 ORM 对象的 course.competency_links
    中读取已加载的 competency_item 关系对象，将名称写入响应 Schema。

    参数:
        course : ORM TrainingCourse 对象（已加载 competency_links → competency_item）
        out    : TrainingCourseOut Pydantic 响应对象（原地修改）
    """
    for i, link_out in enumerate(out.competency_links):
        links = getattr(course, "competency_links", [])
        if i < len(links) and links[i].competency_item:
            link_out.competency_item_name = links[i].competency_item.name


def _build_plan_out(plan: object) -> TrainingPlanOut:
    """
    构建培训计划响应对象，并填充每门课程安排的课程名称和分类。

    参数:
        plan : ORM TrainingPlan 对象（已加载 plan_courses → course）

    返回:
        TrainingPlanOut — 含 course_name 和 course_category 的完整响应
    """
    out = TrainingPlanOut.model_validate(plan)
    for i, pc_out in enumerate(out.plan_courses):
        pcs = getattr(plan, "plan_courses", [])
        if i < len(pcs) and pcs[i].course:
            pc_out.course_name = pcs[i].course.name
            pc_out.course_category = pcs[i].course.category
    return out


def _build_plan_course_out(pc: object) -> PlanCourseOut:
    """
    构建计划课程安排响应对象，填充课程名称和分类。

    参数:
        pc : ORM PlanCourse 对象（已加载 course 关系）

    返回:
        PlanCourseOut — 含 course_name 和 course_category 的响应
    """
    out = PlanCourseOut.model_validate(pc)
    course = getattr(pc, "course", None)
    if course:
        out.course_name = course.name
        out.course_category = course.category
    return out


def _build_enrollment_out(enrollment: object) -> TrainingEnrollmentOut:
    """
    构建培训报名响应对象，填充员工姓名和课程名称。

    参数:
        enrollment : ORM TrainingEnrollment 对象（已加载 employee 和 course 关系）

    返回:
        TrainingEnrollmentOut — 含 employee_name 和 course_name 的响应
    """
    out = TrainingEnrollmentOut.model_validate(enrollment)
    emp = getattr(enrollment, "employee", None)
    course = getattr(enrollment, "course", None)
    if emp:
        out.employee_name = emp.name
    if course:
        out.course_name = course.name
    return out


def _build_exam_out(exam: object) -> TrainingExamOut:
    """
    构建考试响应对象，填充题目数量和课程名称。

    参数:
        exam : ORM TrainingExam 对象（已加载 questions 和 course 关系）

    返回:
        TrainingExamOut — 含 questions_count 和 course_name 的响应
    """
    out = TrainingExamOut.model_validate(exam)
    questions = getattr(exam, "questions", [])
    out.questions_count = len(questions)
    course = getattr(exam, "course", None)
    if course:
        out.course_name = course.name
    return out
