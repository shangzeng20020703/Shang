"""
绩效管理 API 路由模块 - Performance Management

本模块提供售后管理系统的绩效管理全套 REST API，涵盖以下子域：

1. 考核模板 (PerformanceTemplate)
   - 定义考核维度与指标，支持按岗位类型区分
   - 路径前缀: /performance/templates

2. 考核指标 (PerformanceIndicator)
   - 隶属于模板的具体评分项，含权重/评分标准
   - 路径前缀: /performance/templates/{id}/indicators  /performance/indicators/{id}

3. 考核周期 (PerformanceCycle)
   - 驱动一轮完整的绩效评估流程（draft→active→closed）
   - 路径前缀: /performance/cycles

4. 绩效评估 (PerformanceEvaluation)
   - 员工级别的评估实例，包含自评 / 上级评分 / 面谈
   - 路径前缀: /performance/evaluations

5. 等级分布 (GradeDistribution)
   - 统计并校验强制分布（GE/G/M/N/P 各档比例）
   - 路径前缀: /performance/cycles/{id}/distribution

6. 目标管理 (PerformanceGoal / KPI / OKR)
   - 员工设置并跟踪 KPI/OKR 目标，支持加权得分汇总
   - 路径前缀: /performance/goals

7. 强制分布计算 (ForceDistribution)
   - 按配置比例将全周期评估分配到各绩效等级
   - 路径: /performance/force-distribution

架构层次: API 路由 → Service → Model/DB（业务逻辑全部在 services/performance.py）

依赖注入:
  - get_db        : 提供异步数据库会话
  - get_current_user : 验证 JWT Token，返回当前登录员工对象（所有端点均需登录）

注意事项:
  - /goals/summary 路由必须在 /goals/{goal_id} 之前注册，避免路由遮蔽
  - 所有写操作均记录 created_by / updated_by（由 Service 层处理）
"""

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.schemas.performance import (
    PerformanceTemplateCreate,
    PerformanceTemplateUpdate,
    PerformanceTemplateOut,
    PerformanceIndicatorCreate,
    PerformanceIndicatorUpdate,
    PerformanceIndicatorOut,
    PerformanceCycleCreate,
    PerformanceCycleUpdate,
    PerformanceCycleOut,
    PerformanceCycleStatusTransition,
    PerformanceEvaluationCreate,
    PerformanceEvaluationOut,
    PerformanceEvaluationDetail,
    SelfEvalSubmit,
    SuperiorScoreSubmit,
    InterviewRecordSubmit,
    PerformanceFeedback360Create,
    PerformanceFeedback360Out,
    GradeDistribution,
    ForcedDistributionCheck,
    GoalCreate,
    GoalUpdate,
    GoalOut,
    GoalSummary,
    DistributionConfig,
    ForceDistributionResult,
)
from app.services.performance import (
    PerformanceTemplateService,
    PerformanceIndicatorService,
    PerformanceCycleService,
    PerformanceEvaluationService,
    PerformanceFeedback360Service,
    GradeDistributionService,
    GoalService,
    ForceDistributionService,
)

router = APIRouter(
    tags=["绩效管理"],
    dependencies=[Depends(require_roles("admin", "hr", "manager"))],
)


# ============================================================
# PerformanceTemplate 考核模板 CRUD
# ============================================================

@router.post("/templates", response_model=PerformanceTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    data: PerformanceTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceTemplateOut:
    """
    创建考核模板（含指标）

    HTTP 方法: POST
    路径: /performance/templates

    用途:
        新建一套考核模板，模板可同时携带多个考核指标（indicators）。
        模板通常按岗位类型（如"研发"/"销售"）区分，不同类型员工使用不同模板。

    请求体 (PerformanceTemplateCreate):
        - name         : str  — 模板名称（必填）
        - position_type: str  — 适用岗位类型，如 "technical" / "sales"（可选）
        - description  : str  — 模板说明（可选）
        - is_active    : bool — 是否启用，默认 True
        - indicators   : list[PerformanceIndicatorCreate] — 初始指标列表（可选）

    响应 (201 Created):
        PerformanceTemplateOut — 含模板ID、名称、指标列表等完整信息

    权限:
        需要登录（Depends(get_current_user)），无角色限制

    对应 Service:
        PerformanceTemplateService.create(db, data)
    """
    template = await PerformanceTemplateService.create(db, data)
    return PerformanceTemplateOut.model_validate(template)


@router.get("/templates", response_model=list[PerformanceTemplateOut])
async def list_templates(
    position_type: Optional[str] = Query(default=None, description="岗位类型筛选"),
    is_active: Optional[bool] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PerformanceTemplateOut]:
    """
    查询考核模板列表

    HTTP 方法: GET
    路径: /performance/templates

    用途:
        获取所有考核模板，支持按岗位类型和启用状态过滤。
        前端模板选择下拉框、批量创建评估时调用。

    查询参数:
        - position_type : str  — 按岗位类型筛选，如 "technical"（可选）
        - is_active     : bool — 按启用状态筛选：true=启用，false=停用（可选）

    响应 (200 OK):
        list[PerformanceTemplateOut] — 满足条件的模板列表（含每个模板的指标）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceTemplateService.list_all(db, position_type, is_active)
    """
    templates = await PerformanceTemplateService.list_all(db, position_type, is_active)
    return [PerformanceTemplateOut.model_validate(t) for t in templates]


@router.get("/templates/{template_id}", response_model=PerformanceTemplateOut)
async def get_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceTemplateOut:
    """
    获取考核模板详情

    HTTP 方法: GET
    路径: /performance/templates/{template_id}

    用途:
        根据模板 ID 获取单个模板的完整信息，包含全部考核指标。

    路径参数:
        - template_id : int — 模板主键 ID

    响应 (200 OK):
        PerformanceTemplateOut — 模板详情（含指标列表）

    错误响应:
        404 Not Found — 模板不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceTemplateService.get(db, template_id)
    """
    template = await PerformanceTemplateService.get(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="考核模板不存在")
    return PerformanceTemplateOut.model_validate(template)


@router.put("/templates/{template_id}", response_model=PerformanceTemplateOut)
async def update_template(
    template_id: int,
    data: PerformanceTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceTemplateOut:
    """
    更新考核模板

    HTTP 方法: PUT
    路径: /performance/templates/{template_id}

    用途:
        修改模板的基本属性（名称、描述、岗位类型、启用状态等）。
        注意：指标的增删改通过独立的 indicators 端点操作。

    路径参数:
        - template_id : int — 模板主键 ID

    请求体 (PerformanceTemplateUpdate):
        - name         : str  — 模板名称（可选）
        - position_type: str  — 岗位类型（可选）
        - description  : str  — 描述（可选）
        - is_active    : bool — 是否启用（可选）

    响应 (200 OK):
        PerformanceTemplateOut — 更新后的完整模板信息

    错误响应:
        404 Not Found — 模板不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceTemplateService.update(db, template_id, data)
    """
    template = await PerformanceTemplateService.update(db, template_id, data)
    if not template:
        raise HTTPException(status_code=404, detail="考核模板不存在")
    return PerformanceTemplateOut.model_validate(template)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    删除考核模板

    HTTP 方法: DELETE
    路径: /performance/templates/{template_id}

    用途:
        删除指定考核模板。Service 层会检查模板是否被周期引用，
        如已被使用则应拒绝删除（由 Service 层逻辑控制）。

    路径参数:
        - template_id : int — 模板主键 ID

    响应 (204 No Content):
        无响应体

    错误响应:
        404 Not Found — 模板不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceTemplateService.delete(db, template_id)
    """
    success = await PerformanceTemplateService.delete(db, template_id)
    if not success:
        raise HTTPException(status_code=404, detail="考核模板不存在")


# ============================================================
# PerformanceIndicator 考核指标 CRUD
# ============================================================

@router.post(
    "/templates/{template_id}/indicators",
    response_model=PerformanceIndicatorOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_indicator(
    template_id: int,
    data: PerformanceIndicatorCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceIndicatorOut:
    """
    给模板添加考核指标

    HTTP 方法: POST
    路径: /performance/templates/{template_id}/indicators

    用途:
        向已有模板追加一个考核指标（如"工作质量""团队协作"等）。
        指标含权重，所有指标权重之和应为 100（由前端或 Service 校验）。

    路径参数:
        - template_id : int — 所属模板主键 ID

    请求体 (PerformanceIndicatorCreate):
        - name        : str   — 指标名称（必填）
        - weight      : float — 权重百分比，如 30.0 表示 30%（必填）
        - description : str   — 评分标准说明（可选）
        - max_score   : float — 单项满分，默认 100（可选）

    响应 (201 Created):
        PerformanceIndicatorOut — 新建指标的完整信息

    错误响应:
        404 Not Found — 模板不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceIndicatorService.create(db, template_id, data)
    """
    template = await PerformanceTemplateService.get(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="考核模板不存在")
    indicator = await PerformanceIndicatorService.create(db, template_id, data)
    return PerformanceIndicatorOut.model_validate(indicator)


@router.get("/templates/{template_id}/indicators", response_model=list[PerformanceIndicatorOut])
async def list_indicators(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PerformanceIndicatorOut]:
    """
    查询模板下的考核指标

    HTTP 方法: GET
    路径: /performance/templates/{template_id}/indicators

    用途:
        列出指定模板的所有考核指标，用于指标管理页面展示。

    路径参数:
        - template_id : int — 模板主键 ID

    响应 (200 OK):
        list[PerformanceIndicatorOut] — 指标列表（含权重、描述、满分等）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceIndicatorService.list_by_template(db, template_id)
    """
    indicators = await PerformanceIndicatorService.list_by_template(db, template_id)
    return [PerformanceIndicatorOut.model_validate(i) for i in indicators]


@router.put("/indicators/{indicator_id}", response_model=PerformanceIndicatorOut)
async def update_indicator(
    indicator_id: int,
    data: PerformanceIndicatorUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceIndicatorOut:
    """
    更新考核指标

    HTTP 方法: PUT
    路径: /performance/indicators/{indicator_id}

    用途:
        修改单个考核指标的名称、权重、描述或满分值。

    路径参数:
        - indicator_id : int — 指标主键 ID

    请求体 (PerformanceIndicatorUpdate):
        - name        : str   — 指标名称（可选）
        - weight      : float — 权重（可选）
        - description : str   — 说明（可选）
        - max_score   : float — 满分（可选）

    响应 (200 OK):
        PerformanceIndicatorOut — 更新后的指标信息

    错误响应:
        404 Not Found — 指标不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceIndicatorService.update(db, indicator_id, data)
    """
    indicator = await PerformanceIndicatorService.update(db, indicator_id, data)
    if not indicator:
        raise HTTPException(status_code=404, detail="考核指标不存在")
    return PerformanceIndicatorOut.model_validate(indicator)


@router.delete("/indicators/{indicator_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_indicator(
    indicator_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    删除考核指标

    HTTP 方法: DELETE
    路径: /performance/indicators/{indicator_id}

    用途:
        从模板中删除指定考核指标。
        如果该指标已被评估引用（有打分记录），Service 层应拒绝删除。

    路径参数:
        - indicator_id : int — 指标主键 ID

    响应 (204 No Content):
        无响应体

    错误响应:
        404 Not Found — 指标不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceIndicatorService.delete(db, indicator_id)
    """
    success = await PerformanceIndicatorService.delete(db, indicator_id)
    if not success:
        raise HTTPException(status_code=404, detail="考核指标不存在")


# ============================================================
# PerformanceCycle 考核周期 CRUD
# ============================================================

@router.post("/cycles", response_model=PerformanceCycleOut, status_code=status.HTTP_201_CREATED)
async def create_cycle(
    data: PerformanceCycleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceCycleOut:
    """
    创建考核周期

    HTTP 方法: POST
    路径: /performance/cycles

    用途:
        新建一个绩效考核周期（如"2025年Q1季度考核"）。
        周期初始状态为 draft（草稿），需通过 transition 端点推进状态。

    请求体 (PerformanceCycleCreate):
        - name        : str  — 周期名称（必填），如"2025年Q1考核"
        - year        : int  — 考核年份（必填）
        - cycle_type  : str  — 周期类型：quarter（季度）/ half_year（半年）/ year（年度）
        - start_date  : date — 考核开始日期（必填）
        - end_date    : date — 考核结束日期（必填）
        - template_id : int  — 默认使用的考核模板 ID（可选）
        - description : str  — 描述（可选）

    响应 (201 Created):
        PerformanceCycleOut — 包含周期 ID、状态（draft）等完整信息

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceCycleService.create(db, data)
    """
    cycle = await PerformanceCycleService.create(db, data)
    return PerformanceCycleOut.model_validate(cycle)


@router.get("/cycles", response_model=list[PerformanceCycleOut])
async def list_cycles(
    year: Optional[int] = Query(default=None),
    cycle_type: Optional[str] = Query(default=None),
    cycle_status: Optional[str] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PerformanceCycleOut]:
    """
    查询考核周期列表

    HTTP 方法: GET
    路径: /performance/cycles

    用途:
        列出所有考核周期，支持按年份、类型、状态过滤。
        前端考核周期下拉、管理列表页均使用此接口。

    查询参数:
        - year       : int — 考核年份，如 2025（可选）
        - cycle_type : str — 周期类型：quarter / half_year / year（可选）
        - status     : str — 周期状态：draft / active / closed（可选，URL 参数名为 status）

    响应 (200 OK):
        list[PerformanceCycleOut] — 满足条件的周期列表

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceCycleService.list_all(db, year, cycle_type, cycle_status)
    """
    cycles = await PerformanceCycleService.list_all(db, year, cycle_type, cycle_status)
    return [PerformanceCycleOut.model_validate(c) for c in cycles]


@router.get("/cycles/{cycle_id}", response_model=PerformanceCycleOut)
async def get_cycle(
    cycle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceCycleOut:
    """
    获取考核周期详情

    HTTP 方法: GET
    路径: /performance/cycles/{cycle_id}

    用途:
        根据周期 ID 获取单个考核周期的完整信息，包含关联模板信息。

    路径参数:
        - cycle_id : int — 周期主键 ID

    响应 (200 OK):
        PerformanceCycleOut — 周期详情

    错误响应:
        404 Not Found — 周期不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceCycleService.get(db, cycle_id)
    """
    cycle = await PerformanceCycleService.get(db, cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail="考核周期不存在")
    return PerformanceCycleOut.model_validate(cycle)


@router.put("/cycles/{cycle_id}", response_model=PerformanceCycleOut)
async def update_cycle(
    cycle_id: int,
    data: PerformanceCycleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceCycleOut:
    """
    更新考核周期

    HTTP 方法: PUT
    路径: /performance/cycles/{cycle_id}

    用途:
        修改考核周期的基本属性（名称、日期范围、描述等）。
        Service 层会校验状态约束（如已关闭的周期不可修改）。

    路径参数:
        - cycle_id : int — 周期主键 ID

    请求体 (PerformanceCycleUpdate):
        - name        : str  — 周期名称（可选）
        - start_date  : date — 开始日期（可选）
        - end_date    : date — 结束日期（可选）
        - description : str  — 描述（可选）

    响应 (200 OK):
        PerformanceCycleOut — 更新后的周期信息

    错误响应:
        400 Bad Request — 状态约束校验失败（如已关闭）
        404 Not Found   — 周期不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceCycleService.update(db, cycle_id, data)
    """
    try:
        cycle = await PerformanceCycleService.update(db, cycle_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not cycle:
        raise HTTPException(status_code=404, detail="考核周期不存在")
    return PerformanceCycleOut.model_validate(cycle)


@router.post("/cycles/{cycle_id}/transition", response_model=PerformanceCycleOut)
async def transition_cycle_status(
    cycle_id: int,
    data: PerformanceCycleStatusTransition,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceCycleOut:
    """
    考核周期状态流转

    HTTP 方法: POST
    路径: /performance/cycles/{cycle_id}/transition

    用途:
        推进考核周期的状态机。合法的状态转换：
          draft → active  （开始考核，自动创建评估实例）
          active → closed （关闭考核，锁定所有评估）

    路径参数:
        - cycle_id : int — 周期主键 ID

    请求体 (PerformanceCycleStatusTransition):
        - action : str — 动作：start（开始）/ close（关闭）

    响应 (200 OK):
        PerformanceCycleOut — 状态更新后的周期信息

    错误响应:
        400 Bad Request — 不合法的状态转换（如已关闭不能再开始）
        404 Not Found   — 周期不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceCycleService.transition_status(db, cycle_id, data)
    """
    try:
        cycle = await PerformanceCycleService.transition_status(db, cycle_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not cycle:
        raise HTTPException(status_code=404, detail="考核周期不存在")
    return PerformanceCycleOut.model_validate(cycle)


@router.delete("/cycles/{cycle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cycle(
    cycle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    删除考核周期（仅限未开始）

    HTTP 方法: DELETE
    路径: /performance/cycles/{cycle_id}

    用途:
        删除处于 draft 状态的考核周期。
        Service 层会拒绝删除已 active 或 closed 的周期，避免数据丢失。

    路径参数:
        - cycle_id : int — 周期主键 ID

    响应 (204 No Content):
        无响应体

    错误响应:
        400 Bad Request — 周期已启动，不允许删除
        404 Not Found   — 周期不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceCycleService.delete(db, cycle_id)
    """
    try:
        success = await PerformanceCycleService.delete(db, cycle_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not success:
        raise HTTPException(status_code=404, detail="考核周期不存在")


# ============================================================
# PerformanceEvaluation 绩效评估
# ============================================================

@router.post("/evaluations", response_model=PerformanceEvaluationOut, status_code=status.HTTP_201_CREATED)
async def create_evaluation(
    data: PerformanceEvaluationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceEvaluationOut:
    """
    创建绩效评估

    HTTP 方法: POST
    路径: /performance/evaluations

    用途:
        为单个员工在某考核周期内创建评估实例。
        通常在周期状态流转为 active 时由 Service 自动批量创建，
        也可通过此接口手动补充创建。

    请求体 (PerformanceEvaluationCreate):
        - cycle_id    : int — 所属考核周期 ID（必填）
        - employee_id : int — 被考核员工 ID（必填）
        - evaluator_id: int — 上级评估人 ID（可选，默认取员工直属上级）
        - template_id : int — 使用的考核模板 ID（可选，默认使用周期模板）

    响应 (201 Created):
        PerformanceEvaluationOut — 评估实例（初始状态为 pending）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceEvaluationService.create(db, data)
    """
    evaluation = await PerformanceEvaluationService.create(db, data)
    return PerformanceEvaluationOut.model_validate(evaluation)


@router.post("/evaluations/batch", response_model=list[PerformanceEvaluationOut])
async def create_evaluations_batch(
    cycle_id: int = Query(...),
    template_id: Optional[int] = Query(default=None),
    employee_evaluator_pairs: list[dict] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PerformanceEvaluationOut]:
    """
    批量创建绩效评估

    HTTP 方法: POST
    路径: /performance/evaluations/batch

    用途:
        一次性为多个员工创建评估实例，通常在周期启动时调用。
        如果某员工的评估已存在，Service 层应跳过（幂等处理）。

    查询参数:
        - cycle_id    : int — 考核周期 ID（必填）
        - template_id : int — 统一使用的模板 ID（可选）

    请求体:
        list[dict] — 员工-评估人配对列表
        格式示例:
            [
                {"employee_id": 1, "evaluator_id": 2},
                {"employee_id": 3, "evaluator_id": 4},
                ...
            ]
        注意：evaluator_id 可省略，省略时取员工直属上级

    响应 (200 OK):
        list[PerformanceEvaluationOut] — 创建成功的评估列表

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceEvaluationService.create_batch(db, cycle_id, pairs, template_id)
    """
    pairs = [
        (item["employee_id"], item.get("evaluator_id"))
        for item in employee_evaluator_pairs
    ]
    evaluations = await PerformanceEvaluationService.create_batch(
        db, cycle_id, pairs, template_id
    )
    return [PerformanceEvaluationOut.model_validate(e) for e in evaluations]


@router.get("/evaluations/cycle/{cycle_id}", response_model=list[PerformanceEvaluationOut])
async def list_evaluations_by_cycle(
    cycle_id: int,
    eval_status: Optional[str] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PerformanceEvaluationOut]:
    """
    按考核周期查询评估列表

    HTTP 方法: GET
    路径: /performance/evaluations/cycle/{cycle_id}

    用途:
        获取某考核周期内所有员工的评估进度，HR 管理后台使用。
        可按评估状态过滤，用于监控哪些员工尚未完成自评或上级评分。

    路径参数:
        - cycle_id : int — 考核周期主键 ID

    查询参数:
        - status : str — 评估状态过滤，可选值：
                        pending（待自评）/ self_evaluated（已自评）/
                        superior_scored（上级已评）/ completed（已完成）
                        （URL 参数名为 status，内部变量名 eval_status）

    响应 (200 OK):
        list[PerformanceEvaluationOut] — 该周期所有符合条件的评估列表

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceEvaluationService.list_by_cycle(db, cycle_id, eval_status)
    """
    evaluations = await PerformanceEvaluationService.list_by_cycle(db, cycle_id, eval_status)
    return [PerformanceEvaluationOut.model_validate(e) for e in evaluations]


@router.get("/evaluations/employee/{employee_id}", response_model=list[PerformanceEvaluationOut])
async def list_evaluations_by_employee(
    employee_id: int,
    year: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PerformanceEvaluationOut]:
    """
    查询指定员工的绩效评估历史

    HTTP 方法: GET
    路径: /performance/evaluations/employee/{employee_id}

    用途:
        获取某员工在所有（或指定年份）考核周期中的评估记录。
        HR 查看员工绩效档案时使用，也用于绩效面谈前的信息准备。

    路径参数:
        - employee_id : int — 员工主键 ID

    查询参数:
        - year : int — 按年份过滤（可选），如 2025

    响应 (200 OK):
        list[PerformanceEvaluationOut] — 该员工的评估记录列表（时间倒序）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceEvaluationService.list_by_employee(db, employee_id, year)
    """
    evaluations = await PerformanceEvaluationService.list_by_employee(db, employee_id, year)
    return [PerformanceEvaluationOut.model_validate(e) for e in evaluations]


@router.get("/evaluations/me", response_model=list[PerformanceEvaluationOut])
async def list_my_evaluations(
    year: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PerformanceEvaluationOut]:
    """
    查询我自己的绩效评估历史

    HTTP 方法: GET
    路径: /performance/evaluations/me

    用途:
        员工自助查看个人历次绩效评估结果（ESS 模块）。
        从 JWT Token 自动识别当前用户，无需传入员工 ID。

    查询参数:
        - year : int — 按年份过滤（可选），如 2025

    响应 (200 OK):
        list[PerformanceEvaluationOut] — 当前登录员工的评估记录

    权限:
        需要登录（Depends(get_current_user)），只返回当前用户自己的数据

    对应 Service:
        PerformanceEvaluationService.list_by_employee(db, current_user.id, year)
    """
    evaluations = await PerformanceEvaluationService.list_by_employee(
        db, current_user.id, year
    )
    return [PerformanceEvaluationOut.model_validate(e) for e in evaluations]


@router.get("/evaluations/to-review", response_model=list[PerformanceEvaluationOut])
async def list_evaluations_to_review(
    cycle_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PerformanceEvaluationOut]:
    """
    查询我需要评分的评估列表（作为上级/评估人）

    HTTP 方法: GET
    路径: /performance/evaluations/to-review

    用途:
        上级管理者查看自己需要为下属打分的评估任务列表（待办事项）。
        当员工完成自评后，对应上级会在此列表中看到待处理任务。

    查询参数:
        - cycle_id : int — 按考核周期过滤（可选）

    响应 (200 OK):
        list[PerformanceEvaluationOut] — 当前用户作为 evaluator 的评估列表

    权限:
        需要登录（Depends(get_current_user)），返回以当前用户为评估人的记录

    对应 Service:
        PerformanceEvaluationService.list_by_evaluator(db, current_user.id, cycle_id)
    """
    evaluations = await PerformanceEvaluationService.list_by_evaluator(
        db, current_user.id, cycle_id
    )
    return [PerformanceEvaluationOut.model_validate(e) for e in evaluations]


@router.get("/evaluations/{evaluation_id}", response_model=PerformanceEvaluationOut)
async def get_evaluation(
    evaluation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceEvaluationOut:
    """
    获取绩效评估详情

    HTTP 方法: GET
    路径: /performance/evaluations/{evaluation_id}

    用途:
        获取单条评估记录的完整详情，包含自评内容、上级评分、各指标得分、
        加权总分、绩效等级和面谈记录。

    路径参数:
        - evaluation_id : int — 评估实例主键 ID

    响应 (200 OK):
        PerformanceEvaluationOut — 评估完整详情

    错误响应:
        404 Not Found — 评估记录不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceEvaluationService.get(db, evaluation_id)
    """
    evaluation = await PerformanceEvaluationService.get(db, evaluation_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="绩效评估不存在")
    return PerformanceEvaluationOut.model_validate(evaluation)


@router.post("/evaluations/{evaluation_id}/self-eval", response_model=PerformanceEvaluationOut)
async def submit_self_evaluation(
    evaluation_id: int,
    data: SelfEvalSubmit,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceEvaluationOut:
    """
    员工提交自评

    HTTP 方法: POST
    路径: /performance/evaluations/{evaluation_id}/self-eval

    用途:
        员工针对本次评估进行自我评价，填写各指标自评分数和文字说明。
        提交后评估状态从 pending 变为 self_evaluated。
        Service 层会校验：评估状态必须为 pending，且当前用户必须是被考核员工。

    路径参数:
        - evaluation_id : int — 评估实例主键 ID

    请求体 (SelfEvalSubmit):
        - self_scores  : list[{indicator_id, score}] — 各指标自评分（必填）
        - self_comment : str — 自评文字说明（可选）

    响应 (200 OK):
        PerformanceEvaluationOut — 更新后的评估信息（状态变为 self_evaluated）

    错误响应:
        400 Bad Request — 状态不允许自评（如已提交自评）
        404 Not Found   — 评估不存在

    权限:
        需要登录（Depends(get_current_user)），通常只有被考核员工本人可操作

    对应 Service:
        PerformanceEvaluationService.submit_self_eval(db, evaluation_id, data)
    """
    try:
        evaluation = await PerformanceEvaluationService.submit_self_eval(
            db, evaluation_id, data
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not evaluation:
        raise HTTPException(status_code=404, detail="绩效评估不存在")
    return PerformanceEvaluationOut.model_validate(evaluation)


@router.post("/evaluations/{evaluation_id}/superior-score", response_model=PerformanceEvaluationOut)
async def submit_superior_score(
    evaluation_id: int,
    data: SuperiorScoreSubmit,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceEvaluationOut:
    """
    上级提交评分（自动计算加权得分+绩效等级）

    HTTP 方法: POST
    路径: /performance/evaluations/{evaluation_id}/superior-score

    用途:
        上级管理者对下属完成评分，系统自动根据各指标权重计算加权总分，
        并按预设等级阈值（如 90+→S、75-89→A、60-74→B、<60→C）分配等级。
        提交后评估状态从 self_evaluated 变为 superior_scored。

    路径参数:
        - evaluation_id : int — 评估实例主键 ID

    请求体 (SuperiorScoreSubmit):
        - superior_scores  : list[{indicator_id, score}] — 各指标评分（必填）
        - superior_comment : str — 上级评语（可选）

    响应 (200 OK):
        PerformanceEvaluationOut — 含加权总分（weighted_score）和绩效等级（grade）

    错误响应:
        400 Bad Request — 状态不允许（如员工尚未完成自评）
        404 Not Found   — 评估不存在

    权限:
        需要登录（Depends(get_current_user)），通常为评估人（evaluator）

    对应 Service:
        PerformanceEvaluationService.submit_superior_score(db, evaluation_id, data)
    """
    try:
        evaluation = await PerformanceEvaluationService.submit_superior_score(
            db, evaluation_id, data
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not evaluation:
        raise HTTPException(status_code=404, detail="绩效评估不存在")
    return PerformanceEvaluationOut.model_validate(evaluation)


@router.post("/evaluations/{evaluation_id}/interview", response_model=PerformanceEvaluationOut)
async def record_interview(
    evaluation_id: int,
    data: InterviewRecordSubmit,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceEvaluationOut:
    """
    记录绩效面谈

    HTTP 方法: POST
    路径: /performance/evaluations/{evaluation_id}/interview

    用途:
        上级与员工完成绩效面谈后，记录面谈时间、主要内容和改进计划。
        面谈记录后评估状态变为 completed（终态）。

    路径参数:
        - evaluation_id : int — 评估实例主键 ID

    请求体 (InterviewRecordSubmit):
        - interview_date    : date — 面谈日期（必填）
        - interview_content : str  — 面谈主要内容摘要（可选）
        - improvement_plan  : str  — 改进计划（可选）

    响应 (200 OK):
        PerformanceEvaluationOut — 更新后的评估信息（状态变为 completed）

    错误响应:
        404 Not Found — 评估不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        PerformanceEvaluationService.record_interview(db, evaluation_id, data)
    """
    evaluation = await PerformanceEvaluationService.record_interview(
        db, evaluation_id, data
    )
    if not evaluation:
        raise HTTPException(status_code=404, detail="绩效评估不存在")
    return PerformanceEvaluationOut.model_validate(evaluation)


# ============================================================
# 360 Feedback
# ============================================================

@router.post("/feedback-360", response_model=PerformanceFeedback360Out, status_code=status.HTTP_201_CREATED)
async def create_feedback_360(
    data: PerformanceFeedback360Create,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PerformanceFeedback360Out:
    try:
        feedback = await PerformanceFeedback360Service.create(db, current_user.id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    out = PerformanceFeedback360Out.model_validate(feedback)
    if feedback.giver_employee:
        out.giver_employee_name = getattr(feedback.giver_employee, "name", None)
    if feedback.target_employee:
        out.target_employee_name = getattr(feedback.target_employee, "name", None)
    return out


@router.get("/feedback-360/me/submitted", response_model=list[PerformanceFeedback360Out])
async def list_my_submitted_feedback_360(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PerformanceFeedback360Out]:
    records = await PerformanceFeedback360Service.list_submitted(db, current_user.id)
    result: list[PerformanceFeedback360Out] = []
    for record in records:
        out = PerformanceFeedback360Out.model_validate(record)
        if record.giver_employee:
            out.giver_employee_name = getattr(record.giver_employee, "name", None)
        if record.target_employee:
            out.target_employee_name = getattr(record.target_employee, "name", None)
        result.append(out)
    return result


@router.get("/feedback-360/me/received", response_model=list[PerformanceFeedback360Out])
async def list_my_received_feedback_360(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PerformanceFeedback360Out]:
    records = await PerformanceFeedback360Service.list_received(db, current_user.id)
    result: list[PerformanceFeedback360Out] = []
    for record in records:
        out = PerformanceFeedback360Out.model_validate(record)
        if record.giver_employee:
            out.giver_employee_name = getattr(record.giver_employee, "name", None)
        if record.target_employee:
            out.target_employee_name = getattr(record.target_employee, "name", None)
        result.append(out)
    return result


# ============================================================
# Grade Distribution 等级分布
# ============================================================

@router.get("/cycles/{cycle_id}/distribution", response_model=GradeDistribution)
async def get_grade_distribution(
    cycle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> GradeDistribution:
    """
    获取考核等级分布统计

    HTTP 方法: GET
    路径: /performance/cycles/{cycle_id}/distribution

    用途:
        统计指定考核周期内各绩效等级（S/A/B/C 或 优/良/中/差）的人员数量和占比。
        HR 用于了解整体绩效分布，判断是否需要进行强制分布校准。

    路径参数:
        - cycle_id : int — 考核周期主键 ID

    响应 (200 OK):
        GradeDistribution — 各等级人数、百分比及汇总信息

    错误响应:
        404 Not Found — 周期不存在或无评估数据

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        GradeDistributionService.get_distribution(db, cycle_id)
    """
    try:
        return await GradeDistributionService.get_distribution(db, cycle_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/cycles/{cycle_id}/forced-distribution-check", response_model=ForcedDistributionCheck)
async def check_forced_distribution(
    cycle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ForcedDistributionCheck:
    """
    校验强制分布是否合规

    HTTP 方法: GET
    路径: /performance/cycles/{cycle_id}/forced-distribution-check

    用途:
        检查当前考核周期的等级分布是否符合公司预设的强制分布规则。
        例如：优秀(S)不超过 15%，待改进(C)不少于 5%。
        返回各等级的实际占比与目标占比的对比，以及是否合规的结论。

    路径参数:
        - cycle_id : int — 考核周期主键 ID

    响应 (200 OK):
        ForcedDistributionCheck — 含合规标志、各等级差异详情

    错误响应:
        400 Bad Request — 周期数据不足无法校验

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        GradeDistributionService.check_forced_distribution(db, cycle_id)
    """
    try:
        return await GradeDistributionService.check_forced_distribution(db, cycle_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# PerformanceGoal KPI/OKR 目标管理
# NOTE: /goals/summary must be declared BEFORE /goals/{goal_id}
# ============================================================

@router.get("/goals/summary", response_model=GoalSummary)
async def get_goal_summary(
    cycle_id: int = Query(..., description="考核周期ID"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> GoalSummary:
    """
    汇总各员工目标完成率和加权得分

    HTTP 方法: GET
    路径: /performance/goals/summary

    用途:
        对指定考核周期内所有员工的目标完成情况进行汇总统计，
        计算每人目标完成率（actual/target 比值）和加权总得分。
        用于周期汇总报表和管理驾驶舱。

    重要：此路由必须在 /goals/{goal_id} 之前注册，
          否则 "summary" 字符串会被误认为是 goal_id 参数。

    查询参数:
        - cycle_id : int — 考核周期 ID（必填）

    响应 (200 OK):
        GoalSummary — 各员工目标完成情况汇总（含完成率、加权分、人员列表）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        GoalService.get_goal_summary(db, cycle_id)
    """
    return await GoalService.get_goal_summary(db, cycle_id)


@router.get("/goals", response_model=list[GoalOut])
async def list_goals(
    employee_id: Optional[int] = Query(default=None, description="员工ID"),
    cycle_id: Optional[int] = Query(default=None, description="考核周期ID"),
    goal_type: Optional[str] = Query(default=None, description="目标类型: kpi/okr"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[GoalOut]:
    """
    查询目标列表

    HTTP 方法: GET
    路径: /performance/goals

    用途:
        分页查询绩效目标（KPI/OKR），支持多维度过滤。
        响应中附带员工姓名（employee_name）便于前端展示。

    查询参数:
        - employee_id : int — 按员工过滤（可选）
        - cycle_id    : int — 按考核周期过滤（可选）
        - goal_type   : str — 目标类型：kpi（量化指标）或 okr（目标关键结果）（可选）
        - skip        : int — 分页偏移，默认 0（≥0）
        - limit       : int — 每页条数，默认 50（1-200）

    响应 (200 OK):
        list[GoalOut] — 目标列表，每项含员工姓名（employee_name）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        GoalService.list_goals(db, employee_id, cycle_id, goal_type, skip, limit)
    """
    goals = await GoalService.list_goals(db, employee_id, cycle_id, goal_type, skip, limit)
    result = []
    for g in goals:
        out = GoalOut.model_validate(g)
        if g.employee:
            out.employee_name = getattr(g.employee, "name", None)
        result.append(out)
    return result


@router.post("/goals", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
async def create_goal(
    data: GoalCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> GoalOut:
    """
    创建目标

    HTTP 方法: POST
    路径: /performance/goals

    用途:
        为员工创建一个 KPI 或 OKR 目标。
        目标创建后处于进行中状态，员工后续填写实际完成值。

    请求体 (GoalCreate):
        - employee_id  : int   — 目标所属员工 ID（必填）
        - cycle_id     : int   — 所属考核周期 ID（必填）
        - goal_type    : str   — 目标类型：kpi / okr（必填）
        - name         : str   — 目标名称（必填）
        - description  : str   — 详细描述（可选）
        - target_value : float — 目标值（KPI 必填）
        - weight       : float — 权重百分比（可选）
        - due_date     : date  — 截止日期（可选）

    响应 (201 Created):
        GoalOut — 新建目标信息（含 employee_name）

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        GoalService.create_goal(db, data)
    """
    goal = await GoalService.create_goal(db, data)
    out = GoalOut.model_validate(goal)
    if goal.employee:
        out.employee_name = getattr(goal.employee, "name", None)
    return out


@router.put("/goals/{goal_id}", response_model=GoalOut)
async def update_goal(
    goal_id: int,
    data: GoalUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> GoalOut:
    """
    更新目标（包括填写实际值、打分）

    HTTP 方法: PUT
    路径: /performance/goals/{goal_id}

    用途:
        更新目标信息，常见场景：
          1. 员工期末填写 actual_value（实际完成值）
          2. 上级根据完成率打分（score）
          3. 修改目标描述或截止日期

    路径参数:
        - goal_id : int — 目标主键 ID

    请求体 (GoalUpdate):
        - name         : str   — 目标名称（可选）
        - description  : str   — 描述（可选）
        - target_value : float — 目标值（可选）
        - actual_value : float — 实际完成值（可选，员工填写）
        - score        : float — 上级打分（可选）
        - weight       : float — 权重（可选）
        - due_date     : date  — 截止日期（可选）

    响应 (200 OK):
        GoalOut — 更新后的目标信息（含 employee_name）

    错误响应:
        404 Not Found — 目标不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        GoalService.update_goal(db, goal_id, data)
    """
    goal = await GoalService.update_goal(db, goal_id, data)
    if not goal:
        raise HTTPException(status_code=404, detail="目标不存在")
    out = GoalOut.model_validate(goal)
    if goal.employee:
        out.employee_name = getattr(goal.employee, "name", None)
    return out


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    删除目标

    HTTP 方法: DELETE
    路径: /performance/goals/{goal_id}

    用途:
        删除指定 KPI/OKR 目标。通常仅允许在考核周期进行中且目标尚未被评分时删除。

    路径参数:
        - goal_id : int — 目标主键 ID

    响应 (204 No Content):
        无响应体

    错误响应:
        404 Not Found — 目标不存在

    权限:
        需要登录（Depends(get_current_user)）

    对应 Service:
        GoalService.delete_goal(db, goal_id)
    """
    success = await GoalService.delete_goal(db, goal_id)
    if not success:
        raise HTTPException(status_code=404, detail="目标不存在")


# ============================================================
# ForceDistribution 强制分布计算
# ============================================================

@router.post("/force-distribution", response_model=ForceDistributionResult)
async def compute_force_distribution(
    cycle_id: int = Query(..., description="考核周期ID"),
    config: DistributionConfig = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ForceDistributionResult:
    """
    计算强制分布

    HTTP 方法: POST
    路径: /performance/force-distribution

    用途:
        按配置的等级比例，将考核周期内所有已评分的员工强制分配到
        优秀(S) / 良好(A) / 一般(B) / 待改进(C) 四档绩效等级。
        算法：先按加权得分降序排列，再按比例划分各档人数。
        此操作通常为建议性质（预览），最终是否保存由前端控制。

    查询参数:
        - cycle_id : int — 考核周期 ID（必填，URL query 参数）

    请求体 (DistributionConfig):
        - s_ratio : float — 优秀比例，如 0.15 表示 15%（必填）
        - a_ratio : float — 良好比例，如 0.30（必填）
        - b_ratio : float — 一般比例，如 0.40（必填）
        - c_ratio : float — 待改进比例，如 0.15（必填）
        注意：四档比例之和应为 1.0

    响应 (200 OK):
        ForceDistributionResult — 各档员工名单及调整结果

    错误响应:
        400 Bad Request — 比例配置非法（如总和不为 1）或数据不足

    权限:
        需要登录（Depends(get_current_user)），建议仅限 HR/admin 角色（由前端控制访问）

    对应 Service:
        ForceDistributionService.compute_force_distribution(db, cycle_id, config)
    """
    try:
        return await ForceDistributionService.compute_force_distribution(db, cycle_id, config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
