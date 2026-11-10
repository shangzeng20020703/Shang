"""
人才盘点模块 API 路由 — Talent Review
======================================

本模块负责售后管理系统中「人才盘点」相关的所有 HTTP 接口，挂载前缀为
``/api/v1/talent-review``（由 router.py 注册时指定）。

功能分区
--------
1. **盘点会议（TalentReviewSession）**
   - 组织一次人才盘点活动的容器，例如「2025年Q4全员盘点」
   - 状态机：草稿(draft) → 进行中(in_progress) → 已完成(completed) → 已归档(archived)
   - CRUD + 状态流转

2. **盘点条目（TalentReviewEntry）**
   - 每条盘点记录对应一名员工在某次会议中的评估结果
   - 包含九宫格坐标（绩效轴 x 潜力轴）、是否关键人才、离职风险等级等
   - CRUD

3. **九宫格（NineBox）**
   - 按 3×3 矩阵（绩效高/中/低 × 潜力高/中/低）分布员工
   - 服务层聚合盘点条目数据，计算每格人数及员工名单

4. **继任计划（SuccessionPlan）**
   - 为关键岗位（高风险、高重要性岗位）制定人才储备计划
   - 每个计划含多个候选人（SuccessionCandidate），标注就绪度

5. **继任候选人（SuccessionCandidate）**
   - 嵌套在继任计划下，记录候选人、就绪度、发展差距等

权限说明
--------
- 所有接口均需登录（``Depends(get_current_user)``），无 JWT 返回 401。
- 建议仅 HR 管理员 / 高层管理者调用，当前未做角色细分。

服务层对应
----------
- ``app.services.talent_review.TalentReviewSessionService``
- ``app.services.talent_review.TalentReviewEntryService``
- ``app.services.talent_review.NineBoxService``
- ``app.services.talent_review.SuccessionPlanService``
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.schemas.talent_review import (
    TalentReviewSessionCreate,
    TalentReviewSessionUpdate,
    TalentReviewSessionOut,
    TalentReviewSessionStatusTransition,
    TalentReviewEntryCreate,
    TalentReviewEntryUpdate,
    TalentReviewEntryOut,
    NineBoxData,
    SuccessionPlanCreate,
    SuccessionPlanUpdate,
    SuccessionPlanOut,
    SuccessionCandidateCreate,
    SuccessionCandidateUpdate,
    SuccessionCandidateOut,
)
from app.services.talent_review import (
    TalentReviewSessionService,
    TalentReviewEntryService,
    NineBoxService,
    SuccessionPlanService,
)

# 路由器标签用于 Swagger UI 分组展示
router = APIRouter(
    tags=["人才盘点"],
    dependencies=[Depends(require_roles("admin", "hr"))],
)


# ============================================================
# TalentReviewSession 盘点会议 CRUD
# ============================================================

@router.post(
    "/sessions",
    response_model=TalentReviewSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    data: TalentReviewSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TalentReviewSessionOut:
    """
    POST /talent-review/sessions — 创建盘点会议

    用途
    ----
    发起一次新的人才盘点活动，例如「2025年度全员盘点」或「Q4技术部门专项盘点」。
    新建时状态默认为草稿（draft），需由 HR 修改基础信息后再正式开启。

    请求体 (TalentReviewSessionCreate)
    -----------------------------------
    - name          : str  — 会议名称，如"2025年Q4人才盘点"
    - review_year   : int  — 盘点年份
    - review_type   : str  — 盘点类型：annual（年度）/ quarterly（季度）/ special（专项）
    - scope_dept_ids: list — 纳入盘点的部门 ID 列表（为空则覆盖全公司）
    - planned_date  : date — 计划召开日期（可选）
    - description   : str  — 会议说明（可选）

    响应 (201 Created)
    ------------------
    TalentReviewSessionOut — 新建会议数据（含 entry_count=0）

    权限
    ----
    已登录用户（get_current_user），建议仅 HR 管理员调用

    对应服务
    --------
    TalentReviewSessionService.create(db, data)
    """
    session = await TalentReviewSessionService.create(db, data)
    return _session_to_out(session)


@router.get("/sessions", response_model=list[TalentReviewSessionOut])
async def list_sessions(
    year: Optional[int] = Query(default=None),
    review_type: Optional[str] = Query(default=None),
    session_status: Optional[str] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TalentReviewSessionOut]:
    """
    GET /talent-review/sessions — 查询盘点会议列表

    用途
    ----
    获取盘点会议列表，支持多维度过滤。前端「人才盘点」首页用此接口渲染
    历史盘点卡片及当前进行中的盘点。

    查询参数
    --------
    - year        : int (可选) — 按年份过滤
    - review_type : str (可选) — 按盘点类型过滤：annual / quarterly / special
    - status      : str (可选) — 按状态过滤：draft / in_progress / completed / archived

    响应 (200 OK)
    -------------
    list[TalentReviewSessionOut] — 会议列表（含每场会议的 entry_count 参与人数）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    TalentReviewSessionService.list_all(db, year, review_type, session_status)
    """
    sessions = await TalentReviewSessionService.list_all(
        db, year, review_type, session_status
    )
    return [_session_to_out(s) for s in sessions]


@router.get("/sessions/{session_id}", response_model=TalentReviewSessionOut)
async def get_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TalentReviewSessionOut:
    """
    GET /talent-review/sessions/{session_id} — 获取盘点会议详情

    用途
    ----
    按主键获取单次盘点会议的完整信息。

    路径参数
    --------
    - session_id : int — 盘点会议主键 ID

    响应 (200 OK)
    -------------
    TalentReviewSessionOut — 会议详情（含 entry_count）

    错误
    ----
    404 — 盘点会议不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    TalentReviewSessionService.get(db, session_id)
    """
    session = await TalentReviewSessionService.get(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="盘点会议不存在")
    return _session_to_out(session)


@router.put("/sessions/{session_id}", response_model=TalentReviewSessionOut)
async def update_session(
    session_id: int,
    data: TalentReviewSessionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TalentReviewSessionOut:
    """
    PUT /talent-review/sessions/{session_id} — 更新盘点会议

    用途
    ----
    修改盘点会议的名称、日期、范围等基本信息。已完成或已归档的会议
    不允许修改，服务层会抛出 ValueError 并由路由转换为 400。

    路径参数
    --------
    - session_id : int — 盘点会议主键 ID

    请求体 (TalentReviewSessionUpdate)
    -----------------------------------
    所有字段可选，常见更新字段：
    - name        : str  — 修改会议名称
    - planned_date: date — 修改计划日期
    - description : str  — 修改说明

    响应 (200 OK)
    -------------
    TalentReviewSessionOut — 更新后的会议数据

    错误
    ----
    400 — 不允许修改（如会议已完成）
    404 — 盘点会议不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    TalentReviewSessionService.update(db, session_id, data)
    """
    try:
        session = await TalentReviewSessionService.update(db, session_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not session:
        raise HTTPException(status_code=404, detail="盘点会议不存在")
    return _session_to_out(session)


@router.delete(
    "/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /talent-review/sessions/{session_id} — 删除盘点会议（仅限草稿）

    用途
    ----
    物理删除草稿状态的盘点会议及其所有关联条目。非草稿状态的会议无法删除，
    服务层会抛出 ValueError（转换为 400）。

    路径参数
    --------
    - session_id : int — 盘点会议主键 ID

    响应 (204 No Content)
    ----------------------
    无响应体

    错误
    ----
    400 — 非草稿状态不允许删除
    404 — 盘点会议不存在

    权限
    ----
    已登录用户（get_current_user），建议仅 HR 管理员调用

    对应服务
    --------
    TalentReviewSessionService.delete(db, session_id)
    """
    try:
        success = await TalentReviewSessionService.delete(db, session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not success:
        raise HTTPException(status_code=404, detail="盘点会议不存在")


@router.post(
    "/sessions/{session_id}/transition",
    response_model=TalentReviewSessionOut,
)
async def transition_session_status(
    session_id: int,
    data: TalentReviewSessionStatusTransition,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TalentReviewSessionOut:
    """
    POST /talent-review/sessions/{session_id}/transition — 盘点会议状态流转

    用途
    ----
    驱动盘点会议状态机进行状态切换。合法状态转换路径：
      draft → in_progress（开始盘点）
      in_progress → completed（完成盘点）
      completed → archived（归档）

    路径参数
    --------
    - session_id : int — 盘点会议主键 ID

    请求体 (TalentReviewSessionStatusTransition)
    --------------------------------------------
    - target_status : str — 目标状态枚举值
    - comment       : str — 状态变更说明（可选）

    响应 (200 OK)
    -------------
    TalentReviewSessionOut — 更新后的会议数据（状态已变更）

    错误
    ----
    400 — 非法状态转换（如从 completed 转回 in_progress）
    404 — 盘点会议不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    TalentReviewSessionService.transition_status(db, session_id, data)
    """
    try:
        session = await TalentReviewSessionService.transition_status(
            db, session_id, data
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not session:
        raise HTTPException(status_code=404, detail="盘点会议不存在")
    return _session_to_out(session)


# ============================================================
# TalentReviewEntry 盘点条目 CRUD
# ============================================================

@router.post(
    "/entries",
    response_model=TalentReviewEntryOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_entry(
    data: TalentReviewEntryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TalentReviewEntryOut:
    """
    POST /talent-review/entries — 创建盘点条目

    用途
    ----
    在盘点会议中为一名员工创建评估记录，包含九宫格坐标、绩效评级、
    潜力评级、是否关键人才、离职风险等级等信息。

    请求体 (TalentReviewEntryCreate)
    ----------------------------------
    - session_id      : int  — 关联盘点会议 ID
    - employee_id     : int  — 被评估员工 ID
    - performance_score: float — 绩效得分（通常 1-5）
    - potential_score : float — 潜力得分（通常 1-5）
    - box_position    : str  — 九宫格位置（1A-3C，行=绩效轴，列=潜力轴）
    - is_key_talent   : bool — 是否认定为关键人才
    - flight_risk     : str  — 离职风险：low / medium / high / very_high
    - notes           : str  — 评估备注（可选）

    响应 (201 Created)
    ------------------
    TalentReviewEntryOut — 新建条目数据（含 employee_name）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    TalentReviewEntryService.create(db, data)
    """
    entry = await TalentReviewEntryService.create(db, data)
    return _entry_to_out(entry)


@router.get("/entries", response_model=list[TalentReviewEntryOut])
async def list_entries(
    session_id: Optional[int] = Query(default=None),
    is_key_talent: Optional[bool] = Query(default=None),
    flight_risk: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TalentReviewEntryOut]:
    """
    GET /talent-review/entries — 查询盘点条目列表

    用途
    ----
    获取盘点条目列表，支持按会议、关键人才标记、离职风险等多维度过滤。
    前端「盘点结果」页面用此接口渲染员工评估列表及过滤器。

    查询参数
    --------
    - session_id    : int  (可选) — 按盘点会议 ID 过滤（通常必填）
    - is_key_talent : bool (可选) — 只看关键人才（true）或非关键人才（false）
    - flight_risk   : str  (可选) — 按离职风险过滤：low / medium / high / very_high

    响应 (200 OK)
    -------------
    list[TalentReviewEntryOut] — 盘点条目列表（含 employee_name）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    TalentReviewEntryService.list_all(db, session_id, is_key_talent, flight_risk)
    """
    entries = await TalentReviewEntryService.list_all(
        db, session_id, is_key_talent, flight_risk
    )
    return [_entry_to_out(e) for e in entries]


@router.get("/entries/{entry_id}", response_model=TalentReviewEntryOut)
async def get_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TalentReviewEntryOut:
    """
    GET /talent-review/entries/{entry_id} — 获取盘点条目详情

    用途
    ----
    按主键获取单条盘点评估记录的完整信息。

    路径参数
    --------
    - entry_id : int — 盘点条目主键 ID

    响应 (200 OK)
    -------------
    TalentReviewEntryOut — 条目详情（含 employee_name）

    错误
    ----
    404 — 盘点条目不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    TalentReviewEntryService.get(db, entry_id)
    """
    entry = await TalentReviewEntryService.get(db, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="盘点条目不存在")
    return _entry_to_out(entry)


@router.put("/entries/{entry_id}", response_model=TalentReviewEntryOut)
async def update_entry(
    entry_id: int,
    data: TalentReviewEntryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TalentReviewEntryOut:
    """
    PUT /talent-review/entries/{entry_id} — 更新盘点条目

    用途
    ----
    修改盘点评估记录中的评分、九宫格位置、关键人才标记、离职风险等信息。
    通常在盘点会议讨论过程中会多次调整评估结果。

    路径参数
    --------
    - entry_id : int — 盘点条目主键 ID

    请求体 (TalentReviewEntryUpdate)
    ----------------------------------
    所有字段可选，常见更新字段：
    - performance_score : float — 调整后的绩效得分
    - potential_score   : float — 调整后的潜力得分
    - box_position      : str   — 调整后的九宫格位置
    - is_key_talent     : bool  — 修改关键人才认定
    - flight_risk       : str   — 修改离职风险等级
    - development_action: str   — 发展行动建议

    响应 (200 OK)
    -------------
    TalentReviewEntryOut — 更新后的条目数据

    错误
    ----
    404 — 盘点条目不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    TalentReviewEntryService.update(db, entry_id, data)
    """
    entry = await TalentReviewEntryService.update(db, entry_id, data)
    if not entry:
        raise HTTPException(status_code=404, detail="盘点条目不存在")
    return _entry_to_out(entry)


@router.delete(
    "/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /talent-review/entries/{entry_id} — 删除盘点条目

    用途
    ----
    物理删除指定盘点评估记录。

    路径参数
    --------
    - entry_id : int — 盘点条目主键 ID

    响应 (204 No Content)
    ----------------------
    无响应体

    错误
    ----
    404 — 盘点条目不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    TalentReviewEntryService.delete(db, entry_id)
    """
    success = await TalentReviewEntryService.delete(db, entry_id)
    if not success:
        raise HTTPException(status_code=404, detail="盘点条目不存在")


# ============================================================
# NineBox 九宫格
# ============================================================

@router.get("/nine-box/{session_id}", response_model=NineBoxData)
async def get_nine_box(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> NineBoxData:
    """
    GET /talent-review/nine-box/{session_id} — 获取九宫格分布数据

    用途
    ----
    根据指定盘点会议的所有条目，计算并返回九宫格（3×3矩阵）人才分布数据。
    矩阵轴定义：
      - 横轴（X）: 潜力（Potential）— 低/中/高
      - 纵轴（Y）: 绩效（Performance）— 低/中/高
    每格包含员工人数及员工名单，用于前端渲染九宫格可视化图表。

    典型九宫格分类：
      - 高绩效+高潜力（右上角）: 明日之星（Future Star）
      - 高绩效+中潜力（中上）  : 高绩优者（High Performer）
      - 中绩效+高潜力（右中）  : 高潜质者（High Potential）
      - 低绩效+低潜力（左下角）: 待观察者（Under Performer）

    路径参数
    --------
    - session_id : int — 盘点会议主键 ID

    响应 (200 OK)
    -------------
    NineBoxData — 九宫格数据，结构为：
      {
        "boxes": [
          {
            "row": 1-3,        // 绩效轴（1=低，3=高）
            "col": 1-3,        // 潜力轴（1=低，3=高）
            "label": str,      // 格子标签，如"明日之星"
            "count": int,      // 人数
            "employees": [...]  // 员工基本信息列表
          },
          ...
        ],
        "total": int           // 参与盘点总人数
      }

    错误
    ----
    404 — 盘点会议不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    NineBoxService.calculate_nine_box(db, session_id)
    """
    session = await TalentReviewSessionService.get(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="盘点会议不存在")
    return await NineBoxService.calculate_nine_box(db, session_id)


# ============================================================
# SuccessionPlan 继任计划 CRUD
# ============================================================

@router.post(
    "/succession-plans",
    response_model=SuccessionPlanOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_succession_plan(
    data: SuccessionPlanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> SuccessionPlanOut:
    """
    POST /talent-review/succession-plans — 创建继任计划

    用途
    ----
    为公司关键岗位创建继任计划，规划岗位空缺时的接替方案。
    例如为「技术总监」岗位制定继任计划，储备 2-3 名候选人。
    继任计划有助于降低关键人才流失带来的组织风险。

    请求体 (SuccessionPlanCreate)
    ------------------------------
    - position_name      : str  — 关键岗位名称
    - current_holder_id  : int  — 当前岗位持有人员工 ID
    - criticality        : str  — 岗位重要性：critical（关键）/ high / medium
    - risk_level         : str  — 空缺风险：high / medium / low
    - target_date        : date — 计划准备完成日期（可选）
    - description        : str  — 岗位说明（可选）

    响应 (201 Created)
    ------------------
    SuccessionPlanOut — 新建继任计划（含 current_holder_name，candidates 初始为空列表）

    权限
    ----
    已登录用户（get_current_user），建议仅 HR 管理员 / 高层管理者调用

    对应服务
    --------
    SuccessionPlanService.create(db, data)
    """
    plan = await SuccessionPlanService.create(db, data)
    return _plan_to_out(plan)


@router.get(
    "/succession-plans", response_model=list[SuccessionPlanOut]
)
async def list_succession_plans(
    plan_status: Optional[str] = Query(default=None, alias="status"),
    criticality: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[SuccessionPlanOut]:
    """
    GET /talent-review/succession-plans — 查询继任计划列表

    用途
    ----
    获取所有继任计划，可按状态和重要性过滤。前端「接班人梯队」页面用此
    接口展示关键岗位覆盖情况及候选人准备度。

    查询参数
    --------
    - status      : str (可选) — 计划状态：active（生效）/ completed / archived
    - criticality : str (可选) — 岗位重要性：critical / high / medium

    响应 (200 OK)
    -------------
    list[SuccessionPlanOut] — 继任计划列表（含 current_holder_name 及 candidates 列表）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    SuccessionPlanService.list_all(db, plan_status, criticality)
    """
    plans = await SuccessionPlanService.list_all(db, plan_status, criticality)
    return [_plan_to_out(p) for p in plans]


@router.get(
    "/succession-plans/{plan_id}", response_model=SuccessionPlanOut
)
async def get_succession_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> SuccessionPlanOut:
    """
    GET /talent-review/succession-plans/{plan_id} — 获取继任计划详情

    用途
    ----
    按主键获取单条继任计划完整信息，包含所有候选人列表及就绪度。

    路径参数
    --------
    - plan_id : int — 继任计划主键 ID

    响应 (200 OK)
    -------------
    SuccessionPlanOut — 计划详情（含 current_holder_name 及完整 candidates 列表）

    错误
    ----
    404 — 继任计划不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    SuccessionPlanService.get(db, plan_id)
    """
    plan = await SuccessionPlanService.get(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="继任计划不存在")
    return _plan_to_out(plan)


@router.put(
    "/succession-plans/{plan_id}", response_model=SuccessionPlanOut
)
async def update_succession_plan(
    plan_id: int,
    data: SuccessionPlanUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> SuccessionPlanOut:
    """
    PUT /talent-review/succession-plans/{plan_id} — 更新继任计划

    用途
    ----
    修改继任计划的基本信息，如目标日期、岗位重要性、风险等级等。

    路径参数
    --------
    - plan_id : int — 继任计划主键 ID

    请求体 (SuccessionPlanUpdate)
    ------------------------------
    所有字段可选，常见更新字段：
    - criticality : str  — 修改岗位重要性
    - risk_level  : str  — 修改空缺风险
    - target_date : date — 修改目标日期

    响应 (200 OK)
    -------------
    SuccessionPlanOut — 更新后的计划数据

    错误
    ----
    404 — 继任计划不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    SuccessionPlanService.update(db, plan_id, data)
    """
    plan = await SuccessionPlanService.update(db, plan_id, data)
    if not plan:
        raise HTTPException(status_code=404, detail="继任计划不存在")
    return _plan_to_out(plan)


@router.delete(
    "/succession-plans/{plan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_succession_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /talent-review/succession-plans/{plan_id} — 删除继任计划

    用途
    ----
    物理删除指定继任计划及其所有候选人记录。

    路径参数
    --------
    - plan_id : int — 继任计划主键 ID

    响应 (204 No Content)
    ----------------------
    无响应体

    错误
    ----
    404 — 继任计划不存在

    权限
    ----
    已登录用户（get_current_user），建议仅 HR 管理员调用

    对应服务
    --------
    SuccessionPlanService.delete(db, plan_id)
    """
    success = await SuccessionPlanService.delete(db, plan_id)
    if not success:
        raise HTTPException(status_code=404, detail="继任计划不存在")


# ============================================================
# SuccessionCandidate 继任候选人
# ============================================================

@router.post(
    "/succession-plans/{plan_id}/candidates",
    response_model=SuccessionCandidateOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_succession_candidate(
    plan_id: int,
    data: SuccessionCandidateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> SuccessionCandidateOut:
    """
    POST /talent-review/succession-plans/{plan_id}/candidates — 添加继任候选人

    用途
    ----
    向继任计划中添加一名候选人，并标注其就绪度和发展差距。
    一个计划可有多名候选人，通常按就绪度排序形成「接班人梯队」。

    路径参数
    --------
    - plan_id : int — 继任计划主键 ID

    请求体 (SuccessionCandidateCreate)
    -----------------------------------
    - candidate_id   : int  — 候选员工 ID
    - readiness      : str  — 就绪度：ready_now（即刻可接替）/ ready_1_2yr / ready_3_5yr
    - gap_notes      : str  — 发展差距说明（可选）
    - development_plan: str — 针对性发展建议（可选）
    - priority       : int  — 候选人优先级序号（默认1为最优先）

    响应 (201 Created)
    ------------------
    SuccessionCandidateOut — 新建候选人数据（含 candidate_name）

    错误
    ----
    404 — 继任计划不存在（先校验计划是否存在）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    SuccessionPlanService.add_candidate(db, plan_id, data)
    """
    plan = await SuccessionPlanService.get(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="继任计划不存在")
    candidate = await SuccessionPlanService.add_candidate(db, plan_id, data)
    return _candidate_to_out(candidate)


@router.put(
    "/succession-plans/candidates/{candidate_id}",
    response_model=SuccessionCandidateOut,
)
async def update_succession_candidate(
    candidate_id: int,
    data: SuccessionCandidateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> SuccessionCandidateOut:
    """
    PUT /talent-review/succession-plans/candidates/{candidate_id} — 更新继任候选人

    用途
    ----
    修改继任候选人的就绪度、优先级或发展差距说明。
    随着候选人的成长，HR 可定期更新其就绪度评估。

    路径参数
    --------
    - candidate_id : int — 候选人记录主键 ID（注意路径中不含 plan_id）

    请求体 (SuccessionCandidateUpdate)
    -----------------------------------
    所有字段可选，常见更新字段：
    - readiness      : str — 更新就绪度
    - gap_notes      : str — 更新发展差距说明
    - priority       : int — 调整优先级序号

    响应 (200 OK)
    -------------
    SuccessionCandidateOut — 更新后的候选人数据

    错误
    ----
    404 — 候选人记录不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    SuccessionPlanService.update_candidate(db, candidate_id, data)
    """
    candidate = await SuccessionPlanService.update_candidate(
        db, candidate_id, data
    )
    if not candidate:
        raise HTTPException(status_code=404, detail="候选人不存在")
    return _candidate_to_out(candidate)


@router.delete(
    "/succession-plans/candidates/{candidate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_succession_candidate(
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /talent-review/succession-plans/candidates/{candidate_id} — 移除继任候选人

    用途
    ----
    从继任计划中移除指定候选人记录。

    路径参数
    --------
    - candidate_id : int — 候选人记录主键 ID

    响应 (204 No Content)
    ----------------------
    无响应体

    错误
    ----
    404 — 候选人记录不存在

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    SuccessionPlanService.remove_candidate(db, candidate_id)
    """
    success = await SuccessionPlanService.remove_candidate(db, candidate_id)
    if not success:
        raise HTTPException(status_code=404, detail="候选人不存在")


# ============================================================
# Helper: ORM -> Pydantic 转换函数
# ============================================================

def _session_to_out(session: object) -> TalentReviewSessionOut:
    """
    将 TalentReviewSession ORM 对象转换为 TalentReviewSessionOut 输出 Schema。

    额外处理：
    - entry_count : 统计关联 entries 列表长度，表示本次盘点参与人数。
                    ORM 关系已在查询时 eager-loaded，直接取 len()。
    """
    entries = getattr(session, "entries", None) or []
    data = TalentReviewSessionOut.model_validate(session)
    data.entry_count = len(entries)
    return data


def _entry_to_out(entry: object) -> TalentReviewEntryOut:
    """
    将 TalentReviewEntry ORM 对象转换为 TalentReviewEntryOut 输出 Schema。

    额外处理：
    - employee_name : 从 entry.employee 关联对象取姓名，避免前端二次请求。
    """
    data = TalentReviewEntryOut.model_validate(entry)
    employee = getattr(entry, "employee", None)
    if employee:
        data.employee_name = employee.name
    return data


def _plan_to_out(plan: object) -> SuccessionPlanOut:
    """
    将 SuccessionPlan ORM 对象转换为 SuccessionPlanOut 输出 Schema。

    额外处理：
    - current_holder_name : 当前岗位持有人姓名（来自 plan.current_holder 关联）
    - candidates          : 递归调用 _candidate_to_out 转换所有候选人列表
    """
    data = SuccessionPlanOut.model_validate(plan)
    holder = getattr(plan, "current_holder", None)
    if holder:
        data.current_holder_name = holder.name
    candidates = getattr(plan, "candidates", None) or []
    data.candidates = [_candidate_to_out(c) for c in candidates]
    return data


def _candidate_to_out(candidate: object) -> SuccessionCandidateOut:
    """
    将 SuccessionCandidate ORM 对象转换为 SuccessionCandidateOut 输出 Schema。

    额外处理：
    - candidate_name : 候选员工姓名（来自 candidate.candidate 关联对象）
    注意：candidate ORM 关系属性名也叫 "candidate"（Employee 关联），
          与本函数参数 candidate（ORM 实例）同名，需用 getattr 安全取值。
    """
    data = SuccessionCandidateOut.model_validate(candidate)
    emp = getattr(candidate, "candidate", None)
    if emp:
        data.candidate_name = emp.name
    return data
