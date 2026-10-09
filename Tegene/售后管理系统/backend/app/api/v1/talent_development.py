"""
人才发展模块 API 路由 — Talent Development
========================================

本模块负责售后管理系统中「人才发展」相关的所有 HTTP 接口，挂载前缀为
``/api/v1/talent``（由 router.py 注册时指定）。

功能分区
--------
1. **职业通道（CareerPath）**
   - 技术通道：P1（助理工程师）→ P7（首席工程师）
   - 管理通道：M1（团队负责人）→ M5（副总裁/CTO 级）
   - CRUD：创建 / 列表 / 详情 / 更新 / 删除

2. **个人发展计划（IDP — Individual Development Plan）**
   - 员工与主管共同制定的年度/季度成长计划
   - 状态机：草稿(draft) → 提交(submitted) → 审批中(in_review) → 生效(active) → 完成(completed)
   - CRUD + 状态流转 + 目标子项管理

3. **员工证书（EmployeeCertification）**
   - 记录员工持有的专业资格证书，支持到期提醒
   - 到期预警：查询未来 N 天（默认30天）内到期的证书

4. **员工技能（EmployeeSkill）**
   - 人工录入或评估填写的技能标签与熟练度
   - 支持技能矩阵聚合视图（按部门×技能）

权限说明
--------
- 所有接口均需登录（``Depends(get_current_user)``），无 JWT 返回 401。
- 暂未细分角色权限，HR / 管理员 / 员工本人均可调用。

服务层对应
----------
- ``app.services.talent.CareerPathService``
- ``app.services.talent.IDPService``
- ``app.services.talent.CertificationService``
- ``app.services.talent.SkillService``
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.schemas.talent import (
    CareerPathCreate,
    CareerPathUpdate,
    CareerPathOut,
    IDPPlanCreate,
    IDPPlanUpdate,
    IDPStatusTransition,
    IDPPlanOut,
    IDPGoalCreate,
    IDPGoalUpdate,
    IDPGoalOut,
    EmployeeCertificationCreate,
    EmployeeCertificationUpdate,
    EmployeeCertificationOut,
    EmployeeSkillCreate,
    EmployeeSkillUpdate,
    EmployeeSkillOut,
    SkillMatrixEntry,
)
from app.services.talent import (
    CareerPathService,
    IDPService,
    CertificationService,
    SkillService,
)

# 路由器标签用于 Swagger UI 分组展示
router = APIRouter(
    tags=["人才发展"],
    dependencies=[Depends(require_roles("admin", "hr"))],
)


# ============================================================
# CareerPath 职业通道 CRUD
# ============================================================

@router.post("/career-paths", response_model=CareerPathOut, status_code=status.HTTP_201_CREATED)
async def create_career_path(
    data: CareerPathCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> CareerPathOut:
    """
    POST /talent/career-paths — 创建职业通道

    用途
    ----
    新建一条职业发展通道定义，例如「技术通道 P1-P7」或「管理通道 M1-M5」。
    通常由 HR 管理员维护，员工通过此通道了解晋升路径及各级别要求。

    请求体 (CareerPathCreate)
    -------------------------
    - name         : str     — 通道名称，例如"技术通道"
    - path_type    : str     — 通道类型，枚举值：technical / management / professional
    - levels       : list    — 各级别定义（级别名称、描述、薪酬范围等）
    - description  : str     — 通道整体说明（可选）

    响应 (201 Created)
    ------------------
    CareerPathOut — 包含新创建记录的完整字段及自动生成的 id

    权限
    ----
    已登录用户（get_current_user），建议仅 HR 角色调用（当前未做角色限制）

    对应服务
    --------
    CareerPathService.create(db, data)
    """
    career_path = await CareerPathService.create(db, data)
    return CareerPathOut.model_validate(career_path)


@router.get("/career-paths", response_model=list[CareerPathOut])
async def list_career_paths(
    path_type: Optional[str] = Query(default=None, description="通道类型筛选"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[CareerPathOut]:
    """
    GET /talent/career-paths — 查询职业通道列表

    用途
    ----
    获取系统中所有已定义的职业通道，支持按类型筛选。前端「职业发展」页面
    用此接口渲染通道选择下拉框及级别展示卡片。

    查询参数
    --------
    - path_type : str (可选) — 按通道类型过滤，如 technical / management

    响应 (200 OK)
    -------------
    list[CareerPathOut] — 职业通道列表，不分页

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    CareerPathService.list_all(db, path_type)
    """
    paths = await CareerPathService.list_all(db, path_type)
    return [CareerPathOut.model_validate(p) for p in paths]


@router.get("/career-paths/{path_id}", response_model=CareerPathOut)
async def get_career_path(
    path_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> CareerPathOut:
    """
    GET /talent/career-paths/{path_id} — 获取职业通道详情

    用途
    ----
    按主键获取单条职业通道的完整信息，包括所有级别定义。

    路径参数
    --------
    - path_id : int — 职业通道主键 ID

    响应 (200 OK)
    -------------
    CareerPathOut — 通道详情

    错误
    ----
    404 — 职业通道不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    CareerPathService.get(db, path_id)
    """
    career_path = await CareerPathService.get(db, path_id)
    if not career_path:
        raise HTTPException(status_code=404, detail="职业通道不存在")
    return CareerPathOut.model_validate(career_path)


@router.put("/career-paths/{path_id}", response_model=CareerPathOut)
async def update_career_path(
    path_id: int,
    data: CareerPathUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> CareerPathOut:
    """
    PUT /talent/career-paths/{path_id} — 更新职业通道

    用途
    ----
    修改职业通道的名称、描述或级别定义。全量更新，未传字段保持原值（Pydantic
    部分更新语义，取决于 CareerPathUpdate 中字段是否为 Optional）。

    路径参数
    --------
    - path_id : int — 目标职业通道 ID

    请求体 (CareerPathUpdate)
    -------------------------
    与 CareerPathCreate 字段相同，均为可选，只传需要修改的字段

    响应 (200 OK)
    -------------
    CareerPathOut — 更新后的通道数据

    错误
    ----
    404 — 职业通道不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    CareerPathService.update(db, path_id, data)
    """
    career_path = await CareerPathService.update(db, path_id, data)
    if not career_path:
        raise HTTPException(status_code=404, detail="职业通道不存在")
    return CareerPathOut.model_validate(career_path)


@router.delete("/career-paths/{path_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_career_path(
    path_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /talent/career-paths/{path_id} — 删除职业通道

    用途
    ----
    物理删除职业通道记录。如有员工 IDP 关联此通道，服务层会进行外键约束检查，
    存在关联时应返回 400（具体由服务层实现）。

    路径参数
    --------
    - path_id : int — 目标职业通道 ID

    响应 (204 No Content)
    ----------------------
    无响应体

    错误
    ----
    404 — 职业通道不存在

    权限
    ----
    已登录用户（get_current_user），建议仅 HR 管理员调用

    对应服务
    --------
    CareerPathService.delete(db, path_id)
    """
    success = await CareerPathService.delete(db, path_id)
    if not success:
        raise HTTPException(status_code=404, detail="职业通道不存在")


# ============================================================
# IDPPlan 个人发展计划 CRUD
# ============================================================

@router.post("/idp", response_model=IDPPlanOut, status_code=status.HTTP_201_CREATED)
async def create_idp_plan(
    data: IDPPlanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> IDPPlanOut:
    """
    POST /talent/idp — 创建个人发展计划（IDP）

    用途
    ----
    为指定员工创建一份新的 IDP（Individual Development Plan）。IDP 记录员工在
    某段周期内的发展目标、所需技能、期望职级晋升等信息，由员工本人或 HR 发起。
    新建后状态默认为「草稿(draft)」，需后续提交审批才能生效。

    请求体 (IDPPlanCreate)
    ----------------------
    - employee_id    : int  — 员工 ID
    - career_path_id : int  — 关联职业通道 ID（可选）
    - title          : str  — 计划标题
    - period_start   : date — 计划起始日期
    - period_end     : date — 计划截止日期
    - goals          : list — 初始目标列表（可选，也可后续通过 /goals 接口追加）

    响应 (201 Created)
    ------------------
    IDPPlanOut — 新计划数据（含 employee_name、career_path_name 展示字段）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    IDPService.create(db, data)
    """
    plan = await IDPService.create(db, data)
    return _build_idp_out(plan)


@router.get("/idp", response_model=list[IDPPlanOut])
async def list_idp_plans(
    employee_id: Optional[int] = Query(default=None, description="员工ID"),
    idp_status: Optional[str] = Query(default=None, alias="status", description="状态筛选"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[IDPPlanOut]:
    """
    GET /talent/idp — 查询个人发展计划列表

    用途
    ----
    获取 IDP 列表。可按员工 ID 或计划状态过滤，用于 HR 管理视图（查看所有员工计划）
    或员工个人视图（通过 employee_id 过滤自己的计划）。

    查询参数
    --------
    - employee_id : int (可选) — 按员工 ID 过滤
    - status      : str (可选) — 按状态过滤，枚举值：
                    draft / submitted / in_review / active / completed / archived

    响应 (200 OK)
    -------------
    list[IDPPlanOut] — 计划列表，含 employee_name、career_path_name 展示字段

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    IDPService.list_all(db, employee_id, idp_status)
    """
    plans = await IDPService.list_all(db, employee_id, idp_status)
    return [_build_idp_out(p) for p in plans]


@router.get("/idp/{plan_id}", response_model=IDPPlanOut)
async def get_idp_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> IDPPlanOut:
    """
    GET /talent/idp/{plan_id} — 获取个人发展计划详情

    用途
    ----
    按主键获取单条 IDP 完整数据，包含计划目标列表。

    路径参数
    --------
    - plan_id : int — IDP 主键 ID

    响应 (200 OK)
    -------------
    IDPPlanOut — 计划详情（含 goals 子列表、employee_name、career_path_name）

    错误
    ----
    404 — 发展计划不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    IDPService.get(db, plan_id)
    """
    plan = await IDPService.get(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="发展计划不存在")
    return _build_idp_out(plan)


@router.put("/idp/{plan_id}", response_model=IDPPlanOut)
async def update_idp_plan(
    plan_id: int,
    data: IDPPlanUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> IDPPlanOut:
    """
    PUT /talent/idp/{plan_id} — 更新个人发展计划

    用途
    ----
    修改 IDP 的基本信息（标题、周期、关联通道等）。注意：已生效或已完成的计划
    通常不允许修改，具体校验由服务层执行。

    路径参数
    --------
    - plan_id : int — IDP 主键 ID

    请求体 (IDPPlanUpdate)
    ----------------------
    与 IDPPlanCreate 字段相同，均为可选，只传需修改的字段

    响应 (200 OK)
    -------------
    IDPPlanOut — 更新后的计划数据

    错误
    ----
    404 — 发展计划不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    IDPService.update(db, plan_id, data)
    """
    plan = await IDPService.update(db, plan_id, data)
    if not plan:
        raise HTTPException(status_code=404, detail="发展计划不存在")
    return _build_idp_out(plan)


@router.post("/idp/{plan_id}/transition", response_model=IDPPlanOut)
async def transition_idp_status(
    plan_id: int,
    data: IDPStatusTransition,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> IDPPlanOut:
    """
    POST /talent/idp/{plan_id}/transition — IDP 状态流转

    用途
    ----
    驱动 IDP 状态机进行状态切换。合法的状态转换路径：
      draft → submitted（员工提交）
      submitted → in_review（HR/主管接收审核）
      in_review → active（审批通过，计划生效）
      in_review → draft（审批退回，重新修改）
      active → completed（计划到期或提前完成）
      active / completed → archived（归档）

    路径参数
    --------
    - plan_id : int — IDP 主键 ID

    请求体 (IDPStatusTransition)
    ----------------------------
    - target_status : str  — 目标状态枚举值
    - comment       : str  — 状态变更备注（可选）

    响应 (200 OK)
    -------------
    IDPPlanOut — 更新后的计划数据（状态已变更）

    错误
    ----
    400 — 非法状态转换（ValueError 转换为 400）
    404 — 发展计划不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    IDPService.transition_status(db, plan_id, data)
    """
    try:
        plan = await IDPService.transition_status(db, plan_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not plan:
        raise HTTPException(status_code=404, detail="发展计划不存在")
    return _build_idp_out(plan)


@router.delete("/idp/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_idp_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /talent/idp/{plan_id} — 删除个人发展计划

    用途
    ----
    物理删除 IDP 记录及其所有子目标。已生效（active）的计划不允许删除，
    服务层会抛出 ValueError，路由层将其转换为 400 错误。

    路径参数
    --------
    - plan_id : int — IDP 主键 ID

    响应 (204 No Content)
    ----------------------
    无响应体

    错误
    ----
    400 — 不允许删除（如计划已生效）
    404 — 发展计划不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    IDPService.delete(db, plan_id)
    """
    try:
        success = await IDPService.delete(db, plan_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not success:
        raise HTTPException(status_code=404, detail="发展计划不存在")


# ============================================================
# IDP Goals 发展目标
# ============================================================

@router.get("/idp/{plan_id}/goals", response_model=list[IDPGoalOut])
async def list_idp_goals(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[IDPGoalOut]:
    """
    GET /talent/idp/{plan_id}/goals — 查询 IDP 目标列表

    用途
    ----
    获取某个 IDP 下的所有发展目标（Goal），按序号排列。每个目标可独立追踪进度。

    路径参数
    --------
    - plan_id : int — IDP 主键 ID

    响应 (200 OK)
    -------------
    list[IDPGoalOut] — 目标列表，含进度百分比、截止日期、完成状态等

    错误
    ----
    404 — 发展计划不存在（先校验计划是否存在）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    IDPService.list_goals(db, plan_id)
    """
    plan = await IDPService.get(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="发展计划不存在")
    goals = await IDPService.list_goals(db, plan_id)
    return [IDPGoalOut.model_validate(g) for g in goals]


@router.post(
    "/idp/{plan_id}/goals",
    response_model=IDPGoalOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_idp_goal(
    plan_id: int,
    data: IDPGoalCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> IDPGoalOut:
    """
    POST /talent/idp/{plan_id}/goals — 给 IDP 添加发展目标

    用途
    ----
    在指定 IDP 下新增一条发展目标，例如「Q3 完成 AWS 认证」或「掌握 Python 异步编程」。
    目标支持设置截止日期、优先级和评估方式。

    路径参数
    --------
    - plan_id : int — IDP 主键 ID

    请求体 (IDPGoalCreate)
    ----------------------
    - title         : str  — 目标标题
    - description   : str  — 详细描述（可选）
    - due_date      : date — 截止日期（可选）
    - priority      : str  — 优先级：high / medium / low
    - goal_type     : str  — 目标类型：skill / knowledge / behavior / performance

    响应 (201 Created)
    ------------------
    IDPGoalOut — 新建目标数据

    错误
    ----
    404 — 发展计划不存在（先校验计划是否存在）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    IDPService.create_goal(db, plan_id, data)
    """
    plan = await IDPService.get(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="发展计划不存在")
    goal = await IDPService.create_goal(db, plan_id, data)
    return IDPGoalOut.model_validate(goal)


@router.put("/idp/goals/{goal_id}", response_model=IDPGoalOut)
async def update_idp_goal(
    goal_id: int,
    data: IDPGoalUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> IDPGoalOut:
    """
    PUT /talent/idp/goals/{goal_id} — 更新发展目标

    用途
    ----
    修改指定目标的标题、进度（progress_pct）、完成状态等字段。
    员工可通过此接口定期更新目标进展。

    路径参数
    --------
    - goal_id : int — 目标主键 ID（注意：路径中不含 plan_id）

    请求体 (IDPGoalUpdate)
    ----------------------
    所有字段可选，常用更新字段：
    - progress_pct  : int  — 完成进度百分比（0-100）
    - is_completed  : bool — 是否已完成
    - actual_result : str  — 完成情况描述

    响应 (200 OK)
    -------------
    IDPGoalOut — 更新后的目标数据

    错误
    ----
    404 — 发展目标不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    IDPService.update_goal(db, goal_id, data)
    """
    goal = await IDPService.update_goal(db, goal_id, data)
    if not goal:
        raise HTTPException(status_code=404, detail="发展目标不存在")
    return IDPGoalOut.model_validate(goal)


@router.delete("/idp/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_idp_goal(
    goal_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /talent/idp/goals/{goal_id} — 删除发展目标

    用途
    ----
    从 IDP 中删除指定目标记录。

    路径参数
    --------
    - goal_id : int — 目标主键 ID

    响应 (204 No Content)
    ----------------------
    无响应体

    错误
    ----
    404 — 发展目标不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    IDPService.delete_goal(db, goal_id)
    """
    success = await IDPService.delete_goal(db, goal_id)
    if not success:
        raise HTTPException(status_code=404, detail="发展目标不存在")


# ============================================================
# EmployeeCertification 证书管理
# ============================================================

@router.post(
    "/certifications",
    response_model=EmployeeCertificationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_certification(
    data: EmployeeCertificationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> EmployeeCertificationOut:
    """
    POST /talent/certifications — 创建员工证书记录

    用途
    ----
    为员工录入一条专业资格证书信息，例如 PMP、AWS 认证、注册会计师等。
    系统将自动计算到期状态并支持定期预警查询。

    请求体 (EmployeeCertificationCreate)
    -------------------------------------
    - employee_id   : int  — 员工 ID
    - cert_name     : str  — 证书名称
    - cert_number   : str  — 证书编号（可选）
    - cert_type     : str  — 证书类型：professional / language / technical / safety
    - issue_date    : date — 颁发日期
    - expire_date   : date — 到期日期（可选，长期有效则不填）
    - issuing_org   : str  — 颁发机构（可选）

    响应 (201 Created)
    ------------------
    EmployeeCertificationOut — 新建证书数据（含 employee_name 展示字段）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    CertificationService.create(db, data)
    """
    cert = await CertificationService.create(db, data)
    return _build_cert_out(cert)


@router.get("/certifications", response_model=list[EmployeeCertificationOut])
async def list_certifications(
    employee_id: Optional[int] = Query(default=None, description="员工ID"),
    cert_type: Optional[str] = Query(default=None, description="证书类型"),
    cert_status: Optional[str] = Query(default=None, alias="status", description="状态"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[EmployeeCertificationOut]:
    """
    GET /talent/certifications — 查询证书列表

    用途
    ----
    获取证书列表，支持多维度过滤。HR 可查看全员证书情况；员工可按
    employee_id 过滤自己的证书。

    查询参数
    --------
    - employee_id : int (可选) — 按员工 ID 过滤
    - cert_type   : str (可选) — 按证书类型过滤：professional / language / technical / safety
    - status      : str (可选) — 按状态过滤：valid（有效）/ expired（已过期）/ expiring（即将到期）

    响应 (200 OK)
    -------------
    list[EmployeeCertificationOut] — 证书列表（含 employee_name）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    CertificationService.list_all(db, employee_id, cert_type, cert_status)
    """
    certs = await CertificationService.list_all(db, employee_id, cert_type, cert_status)
    return [_build_cert_out(c) for c in certs]


@router.get("/certifications/expiring", response_model=list[EmployeeCertificationOut])
async def list_expiring_certifications(
    days: int = Query(default=30, ge=1, le=365, description="未来N天内到期"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[EmployeeCertificationOut]:
    """
    GET /talent/certifications/expiring — 查询即将到期的证书

    用途
    ----
    查询未来 N 天内到期的所有证书，用于 HR 预警管理。默认查询 30 天内到期。
    前端驾驶舱告警卡片和定时邮件通知均调用此接口。

    重要路由说明
    ------------
    此固定路径端点（/certifications/expiring）必须在参数化路径
    （/certifications/{cert_id}）之前注册，否则「expiring」会被误解析为 cert_id。

    查询参数
    --------
    - days : int — 预警天数范围，默认30，最小1，最大365

    响应 (200 OK)
    -------------
    list[EmployeeCertificationOut] — 即将到期的证书列表（按到期日升序排列）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    CertificationService.get_expiring(db, days)
    """
    certs = await CertificationService.get_expiring(db, days)
    return [_build_cert_out(c) for c in certs]


@router.put("/certifications/{cert_id}", response_model=EmployeeCertificationOut)
async def update_certification(
    cert_id: int,
    data: EmployeeCertificationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> EmployeeCertificationOut:
    """
    PUT /talent/certifications/{cert_id} — 更新证书信息

    用途
    ----
    修改证书的到期日、编号、颁发机构等信息。员工续期证书后应更新到期日。

    路径参数
    --------
    - cert_id : int — 证书记录主键 ID

    请求体 (EmployeeCertificationUpdate)
    -------------------------------------
    所有字段可选，常见更新字段：
    - expire_date : date — 新的到期日期（续期后更新）
    - cert_number : str  — 证书编号
    - issuing_org : str  — 颁发机构

    响应 (200 OK)
    -------------
    EmployeeCertificationOut — 更新后的证书数据

    错误
    ----
    404 — 证书不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    CertificationService.update(db, cert_id, data)
    """
    cert = await CertificationService.update(db, cert_id, data)
    if not cert:
        raise HTTPException(status_code=404, detail="证书不存在")
    return _build_cert_out(cert)


@router.delete("/certifications/{cert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_certification(
    cert_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /talent/certifications/{cert_id} — 删除证书记录

    用途
    ----
    物理删除指定证书记录。

    路径参数
    --------
    - cert_id : int — 证书记录主键 ID

    响应 (204 No Content)
    ----------------------
    无响应体

    错误
    ----
    404 — 证书不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    CertificationService.delete(db, cert_id)
    """
    success = await CertificationService.delete(db, cert_id)
    if not success:
        raise HTTPException(status_code=404, detail="证书不存在")


# ============================================================
# EmployeeSkill 技能管理
# ============================================================

@router.post(
    "/skills",
    response_model=EmployeeSkillOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_skill(
    data: EmployeeSkillCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> EmployeeSkillOut:
    """
    POST /talent/skills — 添加员工技能记录

    用途
    ----
    为员工录入一项技能及其熟练度评级。技能可来源于员工自填（personal）
    或 HR/主管评估（assessed）。

    请求体 (EmployeeSkillCreate)
    ----------------------------
    - employee_id   : int  — 员工 ID
    - skill_name    : str  — 技能名称，如"Python"、"项目管理"
    - skill_category: str  — 技能分类：technical / management / soft / domain
    - proficiency   : int  — 熟练度等级（1-5，1=入门，5=专家）
    - source        : str  — 来源：self / assessed（可选）

    响应 (201 Created)
    ------------------
    EmployeeSkillOut — 新建技能数据（含 employee_name）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    SkillService.create(db, data)
    """
    skill = await SkillService.create(db, data)
    return _build_skill_out(skill)


@router.get("/skills", response_model=list[EmployeeSkillOut])
async def list_skills(
    employee_id: int = Query(..., description="员工ID"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[EmployeeSkillOut]:
    """
    GET /talent/skills — 查询员工技能列表

    用途
    ----
    获取指定员工的全部技能记录，用于人才画像技能标签展示。
    employee_id 为必填参数（...），不传则 422 报错。

    查询参数
    --------
    - employee_id : int (必填) — 员工 ID

    响应 (200 OK)
    -------------
    list[EmployeeSkillOut] — 技能列表，含熟练度等级

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    SkillService.list_by_employee(db, employee_id)
    """
    skills = await SkillService.list_by_employee(db, employee_id)
    return [_build_skill_out(s) for s in skills]


@router.get("/skills/matrix", response_model=list[SkillMatrixEntry])
async def get_skill_matrix(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[SkillMatrixEntry]:
    """
    GET /talent/skills/matrix — 获取全员技能矩阵

    用途
    ----
    按部门×技能聚合，返回各部门在各技能上的平均熟练度，用于管理驾驶舱
    的「技能矩阵热力图」和「部门能力差距分析」可视化。

    重要路由说明
    ------------
    此固定路径端点（/skills/matrix）必须在参数化路径（/skills/{skill_id}）
    之前注册，否则「matrix」会被误解析为 skill_id。

    响应 (200 OK)
    -------------
    list[SkillMatrixEntry] — 矩阵条目列表，每条包含：
      - department_name : str   — 部门名称
      - skill_name      : str   — 技能名称
      - avg_proficiency : float — 部门平均熟练度（1-5）
      - headcount       : int   — 拥有该技能的员工数量

    权限
    ----
    已登录用户（get_current_user），建议仅 HR / 管理员查看

    对应服务
    --------
    SkillService.get_skill_matrix(db)
    """
    return await SkillService.get_skill_matrix(db)


@router.put("/skills/{skill_id}", response_model=EmployeeSkillOut)
async def update_skill(
    skill_id: int,
    data: EmployeeSkillUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> EmployeeSkillOut:
    """
    PUT /talent/skills/{skill_id} — 更新技能信息

    用途
    ----
    修改指定技能记录的熟练度、分类或备注等信息。

    路径参数
    --------
    - skill_id : int — 技能记录主键 ID

    请求体 (EmployeeSkillUpdate)
    ----------------------------
    所有字段可选，常见更新字段：
    - proficiency : int — 更新后的熟练度等级（1-5）
    - note        : str — 备注

    响应 (200 OK)
    -------------
    EmployeeSkillOut — 更新后的技能数据

    错误
    ----
    404 — 技能记录不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    SkillService.update(db, skill_id, data)
    """
    skill = await SkillService.update(db, skill_id, data)
    if not skill:
        raise HTTPException(status_code=404, detail="技能记录不存在")
    return _build_skill_out(skill)


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /talent/skills/{skill_id} — 删除技能记录

    用途
    ----
    物理删除指定员工技能记录。

    路径参数
    --------
    - skill_id : int — 技能记录主键 ID

    响应 (204 No Content)
    ----------------------
    无响应体

    错误
    ----
    404 — 技能记录不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    SkillService.delete(db, skill_id)
    """
    success = await SkillService.delete(db, skill_id)
    if not success:
        raise HTTPException(status_code=404, detail="技能记录不存在")


# ============================================================
# Helper functions — ORM 转 Pydantic 输出 Schema
# ============================================================

def _build_idp_out(plan: object) -> IDPPlanOut:
    """
    将 IDPPlan ORM 对象转换为 IDPPlanOut 输出 Schema。

    在 model_validate 基础上额外填充关联对象的展示字段：
    - employee_name    : 员工姓名（来自 plan.employee 关系）
    - career_path_name : 职业通道名称（来自 plan.career_path 关系）

    这些字段在 ORM 中是关联对象属性，通过此 helper 统一处理，
    避免在每个路由函数中重复编写同样的转换逻辑。
    """
    out = IDPPlanOut.model_validate(plan)
    emp = getattr(plan, "employee", None)
    cp = getattr(plan, "career_path", None)
    if emp:
        out.employee_name = emp.name
    if cp:
        out.career_path_name = cp.name
    return out


def _build_cert_out(cert: object) -> EmployeeCertificationOut:
    """
    将 EmployeeCertification ORM 对象转换为 EmployeeCertificationOut 输出 Schema。

    额外填充：
    - employee_name : 员工姓名（来自 cert.employee 关系）
    """
    out = EmployeeCertificationOut.model_validate(cert)
    emp = getattr(cert, "employee", None)
    if emp:
        out.employee_name = emp.name
    return out


def _build_skill_out(skill: object) -> EmployeeSkillOut:
    """
    将 EmployeeSkill ORM 对象转换为 EmployeeSkillOut 输出 Schema。

    额外填充：
    - employee_name : 员工姓名（来自 skill.employee 关系）
    """
    out = EmployeeSkillOut.model_validate(skill)
    emp = getattr(skill, "employee", None)
    if emp:
        out.employee_name = emp.name
    return out
