"""
胜任力模型管理 API 路由模块

路由前缀（注册于 router.py）: /api/v1/competency

本模块实现售后管理系统中"胜任力与人才发展"板块的核心功能：
通过建立胜任力模型体系，对员工能力进行量化评估，并与岗位要求对比分析差距，
为人才发展计划（IDP）和培训规划提供数据支撑。

核心概念：
  - 胜任力模型（CompetencyModel）：
      一组能力指标的集合，如"软件工程师胜任力模型"，可包含技术能力、沟通能力等多个能力项。
      model_type 区分通用模型（generic）与专项模型（specific）。

  - 胜任力项（CompetencyItem）：
      模型下的单个能力维度，如"Python 编程能力"，定义名称、描述和等级评分标准（1~5）。

  - 岗位胜任力要求（PositionCompetency）：
      某岗位（position_name + department_id）对某个胜任力项的最低要求等级。
      用于定义"该岗位应具备哪些能力，最低要达到几级"。

  - 员工胜任力评估（EmployeeCompetency）：
      记录某员工在某个胜任力项上的实际等级，由评估人（assessor）打分。

  - 差距分析（Gap Analysis）：
      对比员工的实际评估等级 vs 其岗位的要求等级，
      输出每个能力项的差距值（gap = required_level - actual_level）及整体达标情况。

端点清单：

  胜任力模型 CRUD：
       POST   /competency/models          — 新建胜任力模型（可同时提交能力项）
       GET    /competency/models          — 查询模型列表（可按类型过滤）
       GET    /competency/models/{id}     — 获取模型详情（含能力项）
       PUT    /competency/models/{id}     — 更新模型基本信息
       DELETE /competency/models/{id}     — 删除模型

  胜任力项 CRUD（嵌套在模型下）：
       POST   /competency/models/{id}/items   — 给指定模型添加能力项
       GET    /competency/models/{id}/items   — 查询模型下的所有能力项
       PUT    /competency/items/{item_id}     — 更新能力项
       DELETE /competency/items/{item_id}     — 删除能力项

  岗位胜任力要求：
       POST   /competency/position-requirements            — 新建岗位能力要求
       GET    /competency/position-requirements            — 查询某岗位的所有能力要求
       GET    /competency/position-requirements/positions  — 获取已配置能力要求的岗位列表
       PUT    /competency/position-requirements/{pc_id}    — 更新岗位能力要求
       DELETE /competency/position-requirements/{pc_id}    — 删除岗位能力要求

  员工胜任力评估：
       POST   /competency/assessments                    — 提交员工能力评估
       GET    /competency/assessments                    — 查询某员工的评估记录列表
       PUT    /competency/assessments/{assessment_id}    — 更新评估记录
       DELETE /competency/assessments/{assessment_id}    — 删除评估记录

  差距分析：
       GET    /competency/gap-analysis/{employee_id}    — 获取员工胜任力差距分析报告

权限说明：
  - 所有端点均要求已登录（get_current_user），当前无进一步的角色限制
  - 评估和删除操作建议在生产环境中限制为 hr/manager 角色（可在 Service 层加入业务校验）

对应 Service（Service Class 模式，非模块函数）：
  - CompetencyModelService   — app.services.competency.CompetencyModelService
  - CompetencyItemService    — app.services.competency.CompetencyItemService
  - PositionCompetencyService — app.services.competency.PositionCompetencyService
  - EmployeeCompetencyService — app.services.competency.EmployeeCompetencyService
  - CompetencyGapService     — app.services.competency.CompetencyGapService
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.schemas.competency import (
    CompetencyModelCreate,
    CompetencyModelUpdate,
    CompetencyModelOut,
    CompetencyItemCreate,
    CompetencyItemUpdate,
    CompetencyItemOut,
    PositionCompetencyCreate,
    PositionCompetencyUpdate,
    PositionCompetencyOut,
    EmployeeCompetencyCreate,
    EmployeeCompetencyUpdate,
    EmployeeCompetencyOut,
    CompetencyGapAnalysis,
)
from app.services.competency import (
    CompetencyModelService,
    CompetencyItemService,
    PositionCompetencyService,
    EmployeeCompetencyService,
    CompetencyGapService,
)

router = APIRouter(
    tags=["胜任力模型"],
    dependencies=[Depends(require_roles("admin", "hr"))],
)


# ============================================================
# CompetencyModel 胜任力模型 CRUD
# ============================================================

@router.post("/models", response_model=CompetencyModelOut, status_code=status.HTTP_201_CREATED)
async def create_competency_model(
    data: CompetencyModelCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> CompetencyModelOut:
    """
    POST /competency/models — 新建胜任力模型

    用途：创建一个胜任力模型定义。可在创建时同时提交关联的能力项列表（items 字段），
          也可创建空模型后通过 POST /models/{id}/items 单独添加能力项。

    请求体（CompetencyModelCreate）：
        - name: str              — 模型名称（如"软件工程师胜任力模型"），同一系统内应唯一
        - model_type: str        — 模型类型：generic（通用）/ specific（专项）/ leadership（领导力）
        - description: Optional[str] — 模型说明/适用场景
        - items: Optional[list[CompetencyItemCreate]] — 初始能力项列表（可选，随模型一起创建）

    响应（201 Created）：CompetencyModelOut — 含 id、名称、类型、能力项列表

    权限：任意已登录用户（get_current_user）

    Service：CompetencyModelService.create(db, data)
    """
    model = await CompetencyModelService.create(db, data)
    return CompetencyModelOut.model_validate(model)


@router.get("/models", response_model=list[CompetencyModelOut])
async def list_competency_models(
    model_type: Optional[str] = Query(default=None, description="模型类型筛选"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[CompetencyModelOut]:
    """
    GET /competency/models — 查询胜任力模型列表

    用途：获取所有胜任力模型的列表，可按类型过滤。用于前端下拉选择或配置页面。

    Query 参数：
        - model_type: str — 按模型类型过滤（generic / specific / leadership，可选）

    响应（200 OK）：list[CompetencyModelOut]
        每项含：id、name、model_type、description、item_count（能力项数量）、created_at

    权限：任意已登录用户（get_current_user）

    Service：CompetencyModelService.list_all(db, model_type)
    """
    models = await CompetencyModelService.list_all(db, model_type)
    return [CompetencyModelOut.model_validate(m) for m in models]


@router.get("/models/{model_id}", response_model=CompetencyModelOut)
async def get_competency_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> CompetencyModelOut:
    """
    GET /competency/models/{model_id} — 获取胜任力模型详情

    用途：查询单个胜任力模型的完整信息，包含其所有关联的能力项。

    路径参数：
        - model_id: int — 胜任力模型主键 ID

    响应（200 OK）：CompetencyModelOut — 含完整能力项列表（items 字段）
    响应（404 Not Found）：模型不存在

    权限：任意已登录用户（get_current_user）

    Service：CompetencyModelService.get(db, model_id)
    """
    model = await CompetencyModelService.get(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="胜任力模型不存在")
    return CompetencyModelOut.model_validate(model)


@router.put("/models/{model_id}", response_model=CompetencyModelOut)
async def update_competency_model(
    model_id: int,
    data: CompetencyModelUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> CompetencyModelOut:
    """
    PUT /competency/models/{model_id} — 更新胜任力模型

    用途：修改模型的名称、类型、描述等基本属性。不会影响已关联的能力项。

    路径参数：
        - model_id: int — 目标模型主键 ID

    请求体（CompetencyModelUpdate）：
        - name: Optional[str]        — 新模型名称
        - model_type: Optional[str]  — 新模型类型
        - description: Optional[str] — 新说明

    响应（200 OK）：CompetencyModelOut — 更新后的模型数据
    响应（404 Not Found）：模型不存在

    权限：任意已登录用户（get_current_user）

    Service：CompetencyModelService.update(db, model_id, data)
    """
    model = await CompetencyModelService.update(db, model_id, data)
    if not model:
        raise HTTPException(status_code=404, detail="胜任力模型不存在")
    return CompetencyModelOut.model_validate(model)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_competency_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /competency/models/{model_id} — 删除胜任力模型

    用途：删除指定模型。若模型下存在能力项或岗位引用，需先清除关联数据（或由 Service 级联删除）。

    路径参数：
        - model_id: int — 目标模型主键 ID

    响应（204 No Content）：删除成功，无响应体
    响应（404 Not Found）：模型不存在

    权限：任意已登录用户（get_current_user）

    Service：CompetencyModelService.delete(db, model_id)
             返回 True 表示成功，False 表示不存在（触发 404）
    """
    success = await CompetencyModelService.delete(db, model_id)
    if not success:
        raise HTTPException(status_code=404, detail="胜任力模型不存在")


# ============================================================
# CompetencyItem 胜任力项 CRUD
# ============================================================

@router.post(
    "/models/{model_id}/items",
    response_model=CompetencyItemOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_competency_item(
    model_id: int,
    data: CompetencyItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> CompetencyItemOut:
    """
    POST /competency/models/{model_id}/items — 给模型添加胜任力项

    用途：在指定胜任力模型下新增一个能力维度（胜任力项），
          如在"软件工程师模型"下添加"系统设计能力"。

    路径参数：
        - model_id: int — 父胜任力模型主键 ID

    请求体（CompetencyItemCreate）：
        - name: str               — 能力项名称（如"Python 编程能力"）
        - category: Optional[str] — 能力分类（如 technical / behavioral / leadership）
        - description: Optional[str] — 能力项说明及各等级行为描述
        - max_level: int          — 最高等级数（通常为 5）
        - weight: Optional[float] — 在模型中的权重（用于加权综合评分）

    响应（201 Created）：CompetencyItemOut — 含 id、model_id 及所有字段
    响应（404 Not Found）：父模型不存在

    权限：任意已登录用户（get_current_user）

    Service：CompetencyItemService.create(db, model_id, data)
    """
    model = await CompetencyModelService.get(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="胜任力模型不存在")
    item = await CompetencyItemService.create(db, model_id, data)
    return CompetencyItemOut.model_validate(item)


@router.get("/models/{model_id}/items", response_model=list[CompetencyItemOut])
async def list_competency_items(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[CompetencyItemOut]:
    """
    GET /competency/models/{model_id}/items — 查询模型下的所有胜任力项

    用途：列出指定胜任力模型下的所有能力维度，用于展示模型详情或配置岗位要求时的选项列表。

    路径参数：
        - model_id: int — 胜任力模型主键 ID

    响应（200 OK）：list[CompetencyItemOut]
        每项含：id、model_id、name、category、description、max_level、weight

    权限：任意已登录用户（get_current_user）

    Service：CompetencyItemService.list_by_model(db, model_id)
    """
    items = await CompetencyItemService.list_by_model(db, model_id)
    return [CompetencyItemOut.model_validate(i) for i in items]


@router.put("/items/{item_id}", response_model=CompetencyItemOut)
async def update_competency_item(
    item_id: int,
    data: CompetencyItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> CompetencyItemOut:
    """
    PUT /competency/items/{item_id} — 更新胜任力项

    用途：修改单个能力项的名称、描述、权重等属性。
          注意：此端点路径为 /items/{item_id}（不含 /models/{model_id} 前缀），
          因为更新时无需再指定父模型。

    路径参数：
        - item_id: int — 能力项主键 ID

    请求体（CompetencyItemUpdate）：
        - name: Optional[str]        — 新名称
        - category: Optional[str]    — 新分类
        - description: Optional[str] — 新描述
        - max_level: Optional[int]   — 新最高等级
        - weight: Optional[float]    — 新权重

    响应（200 OK）：CompetencyItemOut — 更新后的能力项数据
    响应（404 Not Found）：能力项不存在

    权限：任意已登录用户（get_current_user）

    Service：CompetencyItemService.update(db, item_id, data)
    """
    item = await CompetencyItemService.update(db, item_id, data)
    if not item:
        raise HTTPException(status_code=404, detail="胜任力项不存在")
    return CompetencyItemOut.model_validate(item)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_competency_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /competency/items/{item_id} — 删除胜任力项

    用途：从模型中移除单个能力项。若该能力项已有岗位要求或员工评估记录，
          删除前需先清理关联数据，否则可能触发外键约束。

    路径参数：
        - item_id: int — 能力项主键 ID

    响应（204 No Content）：删除成功，无响应体
    响应（404 Not Found）：能力项不存在

    权限：任意已登录用户（get_current_user）

    Service：CompetencyItemService.delete(db, item_id)
             返回 True 表示成功，False 表示不存在（触发 404）
    """
    success = await CompetencyItemService.delete(db, item_id)
    if not success:
        raise HTTPException(status_code=404, detail="胜任力项不存在")


# ============================================================
# PositionCompetency 岗位胜任力要求
# ============================================================

@router.post(
    "/position-requirements",
    response_model=PositionCompetencyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_position_competency(
    data: PositionCompetencyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PositionCompetencyOut:
    """
    POST /competency/position-requirements — 配置岗位胜任力要求

    用途：为指定岗位（position_name）设定某个胜任力项的最低要求等级。
          一个岗位可关联多个胜任力项要求（多次调用此接口）。
          用于差距分析的"标准值"数据来源。

    请求体（PositionCompetencyCreate）：
        - position_name: str       — 岗位名称（如"高级软件工程师"）
        - department_id: Optional[int] — 部门 ID（可选，用于区分不同部门的同名岗位要求）
        - competency_item_id: int  — 对应的胜任力项 ID
        - required_level: int      — 该岗位要求的最低能力等级（1~max_level）
        - is_core: bool            — 是否为核心胜任力项（核心项差距影响更大）

    响应（201 Created）：PositionCompetencyOut
        含：id、position_name、competency_item_id、competency_item_name（关联查询填充）、required_level

    权限：任意已登录用户（get_current_user）

    Service：PositionCompetencyService.create(db, data)
    注意：响应中的 competency_item_name 字段在路由层从关联对象中填充（不依赖 Service）
    """
    pc = await PositionCompetencyService.create(db, data)
    out = PositionCompetencyOut.model_validate(pc)
    if pc.competency_item:
        out.competency_item_name = pc.competency_item.name
    return out


@router.get("/position-requirements", response_model=list[PositionCompetencyOut])
async def list_position_competencies(
    position_name: str = Query(..., description="岗位名称"),
    department_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PositionCompetencyOut]:
    """
    GET /competency/position-requirements — 查询岗位胜任力要求列表

    用途：获取指定岗位（可附加部门限定）的所有胜任力项要求，用于差距分析或岗位配置页展示。

    Query 参数：
        - position_name: str     — 岗位名称（必填），精确匹配
        - department_id: int     — 部门 ID（可选），进一步限定同名岗位的不同部门

    响应（200 OK）：list[PositionCompetencyOut]
        每项含：id、position_name、department_id、competency_item_id、
               competency_item_name、required_level、is_core

    权限：任意已登录用户（get_current_user）

    Service：PositionCompetencyService.list_by_position(db, position_name, department_id)
    """
    pcs = await PositionCompetencyService.list_by_position(
        db, position_name, department_id
    )
    result = []
    for pc in pcs:
        out = PositionCompetencyOut.model_validate(pc)
        if pc.competency_item:
            out.competency_item_name = pc.competency_item.name
        result.append(out)
    return result


@router.get("/position-requirements/positions", response_model=list[str])
async def list_configured_positions(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[str]:
    """
    GET /competency/position-requirements/positions — 获取已配置胜任力要求的岗位列表

    用途：返回系统中所有已配置了胜任力要求的岗位名称（去重），
          用于前端下拉选择岗位时仅展示已配置的岗位，避免空结果查询。

    注意：此路由为固定路径，必须在 /position-requirements/{pc_id} 动态路由之前注册。

    Query 参数：无

    响应（200 OK）：list[str] — 岗位名称字符串列表（已去重），如 ["软件工程师", "产品经理"]

    权限：任意已登录用户（get_current_user）

    Service：PositionCompetencyService.list_positions(db)
    """
    return await PositionCompetencyService.list_positions(db)


@router.put("/position-requirements/{pc_id}", response_model=PositionCompetencyOut)
async def update_position_competency(
    pc_id: int,
    data: PositionCompetencyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PositionCompetencyOut:
    """
    PUT /competency/position-requirements/{pc_id} — 更新岗位胜任力要求

    用途：修改某条岗位能力要求记录，如调整最低要求等级或变更核心标识。

    路径参数：
        - pc_id: int — 岗位胜任力要求记录主键 ID

    请求体（PositionCompetencyUpdate）：
        - required_level: Optional[int] — 新的要求等级
        - is_core: Optional[bool]       — 是否为核心胜任力项

    响应（200 OK）：PositionCompetencyOut — 更新后的记录（含 competency_item_name）
    响应（404 Not Found）：记录不存在

    权限：任意已登录用户（get_current_user）

    Service：PositionCompetencyService.update(db, pc_id, data)
    """
    pc = await PositionCompetencyService.update(db, pc_id, data)
    if not pc:
        raise HTTPException(status_code=404, detail="岗位胜任力要求不存在")
    out = PositionCompetencyOut.model_validate(pc)
    if pc.competency_item:
        out.competency_item_name = pc.competency_item.name
    return out


@router.delete("/position-requirements/{pc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_position_competency(
    pc_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /competency/position-requirements/{pc_id} — 删除岗位胜任力要求

    用途：移除指定岗位的某个能力项要求配置。

    路径参数：
        - pc_id: int — 岗位胜任力要求记录主键 ID

    响应（204 No Content）：删除成功，无响应体
    响应（404 Not Found）：记录不存在

    权限：任意已登录用户（get_current_user）

    Service：PositionCompetencyService.delete(db, pc_id)
             返回 True 表示成功，False 表示不存在（触发 404）
    """
    success = await PositionCompetencyService.delete(db, pc_id)
    if not success:
        raise HTTPException(status_code=404, detail="岗位胜任力要求不存在")


# ============================================================
# EmployeeCompetency 员工胜任力评估
# ============================================================

@router.post(
    "/assessments",
    response_model=EmployeeCompetencyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_assessment(
    data: EmployeeCompetencyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> EmployeeCompetencyOut:
    """
    POST /competency/assessments — 提交员工胜任力评估

    用途：记录评估人对某员工在指定能力项上的实际等级评分。
          评估结果将用于差距分析（gap-analysis）的实际值。

    请求体（EmployeeCompetencyCreate）：
        - employee_id: int         — 被评估员工 ID
        - competency_item_id: int  — 评估的胜任力项 ID
        - actual_level: int        — 员工实际能力等级（1~max_level）
        - assessor_id: int         — 评估人员工 ID（可为直属上级、HR 或本人自评）
        - assessment_date: date    — 评估日期
        - evidence: Optional[str]  — 能力等级判断依据/证据描述
        - notes: Optional[str]     — 补充备注

    响应（201 Created）：EmployeeCompetencyOut
        含：id、employee_id、employee_name、competency_item_name、actual_level、assessor_name

    权限：任意已登录用户（get_current_user）

    Service：EmployeeCompetencyService.create(db, data)
    注意：响应中的 employee_name、competency_item_name、assessor_name 在路由层填充
    """
    ec = await EmployeeCompetencyService.create(db, data)
    out = EmployeeCompetencyOut.model_validate(ec)
    if ec.competency_item:
        out.competency_item_name = ec.competency_item.name
    if ec.employee:
        out.employee_name = ec.employee.name
    if ec.assessor:
        out.assessor_name = ec.assessor.name
    return out


@router.get("/assessments", response_model=list[EmployeeCompetencyOut])
async def list_assessments(
    employee_id: int = Query(..., description="员工ID"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[EmployeeCompetencyOut]:
    """
    GET /competency/assessments — 查询员工胜任力评估记录列表

    用途：获取指定员工的所有胜任力评估历史记录，按评估日期倒序排列。
          用于员工能力档案页或评估历史对比展示。

    Query 参数：
        - employee_id: int — 员工 ID（必填），只查询该员工的评估记录

    响应（200 OK）：list[EmployeeCompetencyOut]
        每项含：id、employee_name、competency_item_name、actual_level、assessor_name、assessment_date

    权限：任意已登录用户（get_current_user）

    Service：EmployeeCompetencyService.list_by_employee(db, employee_id)
    """
    ecs = await EmployeeCompetencyService.list_by_employee(db, employee_id)
    result = []
    for ec in ecs:
        out = EmployeeCompetencyOut.model_validate(ec)
        if ec.competency_item:
            out.competency_item_name = ec.competency_item.name
        if ec.employee:
            out.employee_name = ec.employee.name
        if ec.assessor:
            out.assessor_name = ec.assessor.name
        result.append(out)
    return result


@router.put("/assessments/{assessment_id}", response_model=EmployeeCompetencyOut)
async def update_assessment(
    assessment_id: int,
    data: EmployeeCompetencyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> EmployeeCompetencyOut:
    """
    PUT /competency/assessments/{assessment_id} — 更新员工胜任力评估记录

    用途：修订已有的评估记录，如更正评估等级、补充证据说明等。

    路径参数：
        - assessment_id: int — 评估记录主键 ID

    请求体（EmployeeCompetencyUpdate）：
        - actual_level: Optional[int]  — 修正后的实际等级
        - evidence: Optional[str]      — 新的证据说明
        - notes: Optional[str]         — 新的备注

    响应（200 OK）：EmployeeCompetencyOut — 更新后的评估记录（含关联名称字段）
    响应（404 Not Found）：记录不存在

    权限：任意已登录用户（get_current_user）

    Service：EmployeeCompetencyService.update(db, assessment_id, data)
    """
    ec = await EmployeeCompetencyService.update(db, assessment_id, data)
    if not ec:
        raise HTTPException(status_code=404, detail="评估记录不存在")
    out = EmployeeCompetencyOut.model_validate(ec)
    if ec.competency_item:
        out.competency_item_name = ec.competency_item.name
    if ec.employee:
        out.employee_name = ec.employee.name
    if ec.assessor:
        out.assessor_name = ec.assessor.name
    return out


@router.delete("/assessments/{assessment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_assessment(
    assessment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /competency/assessments/{assessment_id} — 删除员工胜任力评估记录

    用途：删除错误或过期的评估记录。删除后对应的差距分析结果也会变化。

    路径参数：
        - assessment_id: int — 评估记录主键 ID

    响应（204 No Content）：删除成功，无响应体
    响应（404 Not Found）：记录不存在

    权限：任意已登录用户（get_current_user）

    Service：EmployeeCompetencyService.delete(db, assessment_id)
             返回 True 表示成功，False 表示不存在（触发 404）
    """
    success = await EmployeeCompetencyService.delete(db, assessment_id)
    if not success:
        raise HTTPException(status_code=404, detail="评估记录不存在")


# ============================================================
# Gap Analysis 差距分析
# ============================================================

@router.get("/gap-analysis/{employee_id}", response_model=CompetencyGapAnalysis)
async def get_gap_analysis(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> CompetencyGapAnalysis:
    """
    GET /competency/gap-analysis/{employee_id} — 获取员工胜任力差距分析报告

    用途：对比员工的实际胜任力评估等级（来自 EmployeeCompetency）与其当前岗位的要求等级
          （来自 PositionCompetency），计算每个能力项的差距值，输出整体达标情况。

          差距值 = required_level - actual_level
            > 0 表示该项未达标（差距越大，优先级越高）
            = 0 表示恰好达标
            < 0 表示超出要求（能力超额）

    路径参数：
        - employee_id: int — 被分析员工的主键 ID

    响应（200 OK）：CompetencyGapAnalysis
        {
            "employee_id": int,
            "employee_name": str,
            "position_name": str,             — 员工当前岗位
            "overall_match_rate": float,      — 整体达标率（0.0~1.0）
            "items": list[GapItem]            — 每个能力项的详细差距
        }
        GapItem 结构：
        {
            "competency_item_id": int,
            "competency_item_name": str,
            "required_level": int,            — 岗位要求等级
            "actual_level": int,              — 员工实际等级（无评估则为 0）
            "gap": int,                       — 差距值（required - actual）
            "is_core": bool,                  — 是否为核心胜任力项
            "status": str                     — met / gap / exceeded
        }

    响应（404 Not Found）：员工不存在或员工无岗位信息（由 ValueError 转换为 404）

    权限：任意已登录用户（get_current_user）

    Service：CompetencyGapService.analyze(db, employee_id)
             若员工不存在或未设置岗位，抛出 ValueError，路由层捕获后转为 404
    """
    try:
        return await CompetencyGapService.analyze(db, employee_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
