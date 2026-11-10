"""
员工模块 API 路由 - 员工全生命周期管理

本模块负责员工档案的完整 CRUD 及所有子资源的管理，涵盖：
  - 员工列表查询（多维筛选）与详情获取
  - 员工创建、更新、删除（软删除）
  - 员工生命周期状态流转：待入职 → 试用期 → 在职 → 离职
  - 合同管理：新建合同、续签、查询即将到期合同
  - 家庭成员、工作经历、教育背景、档案材料、技能特长的子资源 CRUD
  - Excel 批量导入/导出
  - 员工信息登记表（XLS）导入，创建完整员工档案
  - 批量离职、批量调岗
  - 预警查询（合同到期/试用期到期/本月生日）

路由前缀：/api/v1/employees（由 router.py 统一注册）

路由注册顺序关键约定
──────────────────────────────────────────────────────────────────────────
FastAPI 按路由注册顺序匹配路径。所有**固定路径端点**（如 /export、/import、
/alerts、/work-experiences/{id}、/family-members/{id} 等）必须在动态路径
  /{employee_id}
之前注册，否则 FastAPI 会将字面量（如 "export"）误解析为 employee_id 整数，
导致 404 / 422 错误。

权限体系（Depends 注入）
──────────────────────────────────────────────────────────────────────────
  - get_current_user   : 任何已登录用户（JWT 有效 + is_active=True）
  - require_roles(...)  : 指定角色才可访问；写操作通常要求 admin 或 hr

服务层对应关系
──────────────────────────────────────────────────────────────────────────
  - EmployeeService           : 员工主表 CRUD、状态流转、导入导出、批量操作、预警
  - ContractService           : 合同新建、续签、到期查询
  - FamilyMemberService       : 家庭成员 CRUD
  - WorkExperienceService     : 工作经历 CRUD
  - EducationRecordService    : 教育背景 CRUD
  - EmployeeDocumentService   : 档案材料 CRUD
  - EmployeePersonalSkillService : 技能特长 Upsert
  - RegistrationFormService   : 员工信息登记表 XLS 导入
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, get_user_role_names, require_roles
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.schemas.employee import (
    BulkOffboardRequest,
    BulkTransferRequest,
    TransferRequest,
    OffboardRequest,
    ContractCreate,
    ContractExpiryItem,
    ContractOut,
    ContractRenew,
    EducationRecordCreate,
    EducationRecordOut,
    EducationRecordUpdate,
    EmployeeAlert,
    EmployeeBrief,
    EmployeeChangeLogOut,
    EmployeeCreate,
    EmployeeDocumentCreate,
    EmployeeDocumentOut,
    EmployeeDocumentUpdate,
    EmployeeImportResult,
    EmployeeListItemOut,
    EmployeeListResponse,
    EmployeeOut,
    EmployeeOutSensitive,
    EmployeePersonalSkillCreate,
    EmployeePersonalSkillOut,
    EmployeePersonalSkillUpdate,
    EmployeeUpdate,
    FamilyMemberCreate,
    FamilyMemberOut,
    FamilyMemberUpdate,
    RegistrationFormImportResult,
    StatusTransition,
    WorkExperienceCreate,
    WorkExperienceOut,
    WorkExperienceUpdate,
)
from app.services.employee import (
    ContractService,
    EducationRecordService,
    EmployeeDocumentService,
    EmployeePersonalSkillService,
    EmployeeService,
    FamilyMemberService,
    RegistrationFormService,
    WorkExperienceService,
)

router = APIRouter(dependencies=[Depends(get_current_user)])


def _parse_department_ids_from_query(
    request: Request,
    *,
    department_id: Optional[int] = None,
    department_ids: Optional[str] = None,
) -> List[int]:
    """解析员工列表部门筛选，兼容旧单选与前端多选数组序列化。"""
    raw_values: List[object] = []
    if department_id is not None:
        raw_values.append(department_id)
    if department_ids:
        raw_values.append(department_ids)
    raw_values.extend(request.query_params.getlist("department_ids"))
    raw_values.extend(request.query_params.getlist("department_ids[]"))

    parsed: list[int] = []
    seen: set[int] = set()
    for raw_value in raw_values:
        for part in str(raw_value).split(","):
            value = part.strip()
            if not value:
                continue
            try:
                dept_id = int(value)
            except ValueError:
                raise HTTPException(status_code=422, detail="department_ids 必须是部门 ID 列表")
            if dept_id > 0 and dept_id not in seen:
                parsed.append(dept_id)
                seen.add(dept_id)
    return parsed


async def _resolve_employee_scope(
    db: AsyncSession,
    current_user: Employee,
) -> tuple[set[str], bool]:
    roles = await get_user_role_names(db, current_user)
    manager_scoped = "manager" in roles and not ({"admin", "hr"} & roles)
    return roles, manager_scoped


def _mask_phone(phone: Optional[str]) -> str:
    if not phone:
        return ""
    if len(phone) < 7:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


async def _get_employee_in_scope(
    db: AsyncSession,
    current_user: Employee,
    employee_id: int,
) -> Employee:
    emp = await EmployeeService.get_by_id(db, employee_id)
    _roles, manager_scoped = await _resolve_employee_scope(db, current_user)
    if manager_scoped and current_user.department_id != getattr(emp, "department_id", None):
        raise HTTPException(status_code=403, detail="仅可查看本部门员工")
    return emp


# 路由前缀由 router.py 统一设置为 /employees，
# 此处路径相对于该前缀。

# ==================== 员工 CRUD ====================

@router.get("", response_model=EmployeeListResponse, summary="分页查询员工列表")
async def list_employees(
    request: Request,
    keyword: Optional[str] = Query(None, description="搜索关键字（姓名/工号/手机号）"),
    company: Optional[str] = Query(None, description="人员所属公司关键词"),
    department_id: Optional[int] = Query(None, description="部门ID"),
    department_ids: Optional[str] = Query(None, description="多个部门ID，逗号分隔；兼容 department_ids[]=1&department_ids[]=2"),
    location_id: Optional[int] = Query(None, description="工作地点ID"),
    status: Optional[str] = Query(None, description="员工状态"),
    employment_type: Optional[str] = Query(None, description="用工类型"),
    is_field_staff: Optional[bool] = Query(None, description="是否驻场人员"),
    position: Optional[str] = Query(None, description="职位关键词模糊搜索"),
    education: Optional[str] = Query(None, description="学历精确匹配"),
    hire_date_from: Optional[date] = Query(None, description="入职日期起"),
    hire_date_to: Optional[date] = Query(None, description="入职日期止"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=1000, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /api/v1/employees

    分页查询员工列表，支持多维度筛选条件组合。

    Query 参数：
      - keyword        : 模糊匹配姓名、工号或手机号
      - department_id  : 按部门 ID 精确筛选（兼容旧单选）
      - department_ids : 多部门 ID，逗号分隔或重复参数
      - location_id    : 按工作地点 ID 精确筛选
      - status         : 员工状态，枚举值参见 Employee.status 字段
                         （pending/probation/active/resigned 等）
      - employment_type: 用工类型（正式/实习/外包等）
      - is_field_staff : 布尔值，筛选驻场人员
      - position       : 职位关键词模糊搜索
      - education      : 学历精确匹配（大专/本科/硕士/博士等）
      - hire_date_from : 入职日期区间起始（ISO 8601 日期格式）
      - hire_date_to   : 入职日期区间截止
      - page           : 页码，从 1 开始
      - page_size      : 每页条数，1~1000，默认 20

    响应（200）：
      EmployeeListResponse { total: int, items: List[EmployeeListItemOut] }
      items 中每条记录额外携带 location_name、department_name（冗余展示字段）

    权限：任何已登录用户（get_current_user）

    对应 Service：EmployeeService.get_list()
    """
    _roles, manager_scoped = await _resolve_employee_scope(db, current_user)
    scoped_department_ids = _parse_department_ids_from_query(
        request,
        department_id=department_id,
        department_ids=department_ids,
    )
    if manager_scoped:
        if current_user.department_id is None:
            return EmployeeListResponse(total=0, items=[])
        scoped_department_ids = [current_user.department_id]

    total, items = await EmployeeService.get_list(
        db,
        keyword=keyword,
        company=company,
        department_ids=scoped_department_ids,
        location_id=location_id,
        status=status,
        employment_type=employment_type,
        is_field_staff=is_field_staff,
        position=position,
        education=education,
        hire_date_from=hire_date_from,
        hire_date_to=hire_date_to,
        page=page,
        page_size=page_size,
    )
    out_items = []
    for emp in items:
        emp_out = EmployeeListItemOut.model_validate(emp)
        emp_out = emp_out.model_copy(update={
            "location_name": emp.location.name if emp.location else None,
            "department_name": emp.department.name if emp.department else None,
            "direct_manager_name": getattr(getattr(emp, "__dict__", {}).get("direct_manager"), "name", None),
        })
        out_items.append(emp_out)

    return EmployeeListResponse(total=total, items=out_items)


# ==================== 固定路径路由（必须在 /{employee_id} 之前） ====================

@router.get(
    "/selector",
    response_model=list[EmployeeBrief],
    summary="查询员工选择器简表",
)
async def list_employee_selector(
    keyword: Optional[str] = Query(None, description="搜索关键字（姓名/工号/手机号）"),
    department_id: Optional[int] = Query(None, description="部门ID"),
    status: Optional[str] = Query(None, description="员工状态"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /api/v1/employees/selector

    返回轻量级员工简表，用于下拉选择、审批转签、资产领用、薪酬录入等关联场景。

    权限：
      - 任何已登录用户可访问，且仅返回简表字段
      - admin / hr：全公司
      - manager：仅本部门
    """
    roles, manager_scoped = await _resolve_employee_scope(db, current_user)
    scoped_department_id = department_id
    if manager_scoped and "manager" in roles:
        if current_user.department_id is None:
            return []
        scoped_department_id = current_user.department_id

    _total, items = await EmployeeService.get_list(
        db,
        keyword=keyword,
        department_id=scoped_department_id,
        status=status,
        page=1,
        page_size=limit,
    )
    employee_ids = [emp.id for emp in items]
    role_map: dict[int, list[str]] = {emp_id: [] for emp_id in employee_ids}
    if employee_ids:
        role_result = await db.execute(
            select(EmployeeRole.employee_id, EmployeeRole.role_name).where(
                EmployeeRole.employee_id.in_(employee_ids),
                EmployeeRole.is_active == True,
            )
        )
        for employee_id, role_name in role_result.all():
            if employee_id and role_name:
                role_map.setdefault(int(employee_id), []).append(str(role_name).strip().lower())
    return [
        EmployeeBrief(
            id=emp.id,
            employee_no=emp.employee_no,
            name=emp.name,
            phone=_mask_phone(emp.phone),
            position=emp.position,
            department_id=emp.department_id,
            department_name=emp.department.name if emp.department else None,
            status=emp.status,
            role_names=sorted(set([*role_map.get(emp.id, []), *(["admin"] if getattr(emp, "is_superuser", False) else [])])),
        )
        for emp in items
    ]

@router.get("/export", summary="导出员工列表 Excel")
async def export_employees(
    request: Request,
    keyword: Optional[str] = Query(None),
    company: Optional[str] = Query(None),
    department_id: Optional[int] = Query(None),
    department_ids: Optional[str] = Query(None),
    location_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    employment_type: Optional[str] = Query(None),
    is_field_staff: Optional[bool] = Query(None),
    position: Optional[str] = Query(None),
    education: Optional[str] = Query(None),
    hire_date_from: Optional[date] = Query(None),
    hire_date_to: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /api/v1/employees/export

    将满足筛选条件的员工列表导出为 Excel 文件（.xlsx）并以附件形式返回。
    筛选参数与 GET /employees 列表接口完全一致，不分页（导出全量数据）。

    Query 参数：（同 list_employees，见上方说明）

    响应（200）：
      Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
      Content-Disposition: attachment; filename=employees.xlsx
      响应体为 Excel 文件的二进制内容

    权限：任何已登录用户（get_current_user）

    注意：此端点必须注册在 /{employee_id} 路由之前，防止 FastAPI 将 "export"
    误识别为整数类型的 employee_id。

    对应 Service：EmployeeService.export_excel()
    """
    _roles, manager_scoped = await _resolve_employee_scope(db, current_user)
    scoped_department_ids = _parse_department_ids_from_query(
        request,
        department_id=department_id,
        department_ids=department_ids,
    )
    if manager_scoped:
        if current_user.department_id is None:
            raise HTTPException(status_code=403, detail="无可访问的员工范围")
        scoped_department_ids = [current_user.department_id]

    excel_io = await EmployeeService.export_excel(
        db,
        keyword=keyword,
        company=company,
        department_ids=scoped_department_ids,
        location_id=location_id,
        status=status,
        employment_type=employment_type,
        is_field_staff=is_field_staff,
        position=position,
        education=education,
        hire_date_from=hire_date_from,
        hire_date_to=hire_date_to,
    )
    return Response(
        content=excel_io.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=employees.xlsx"},
    )


@router.get("/import-template", summary="下载员工导入模板")
async def get_import_template(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    GET /api/v1/employees/import-template

    下载员工批量导入所需的 Excel 模板文件（含表头和字段填写说明）。
    前端引导用户按模板格式填写后，再通过 POST /employees/import 上传。

    响应（200）：
      Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
      Content-Disposition: attachment; filename=employee_import_template.xlsx
      响应体为 Excel 模板文件的二进制内容

    权限：任何已登录用户（get_current_user）

    注意：此端点必须注册在 /{employee_id} 路由之前。

    对应 Service：EmployeeService.generate_import_template()（同步方法，无需 DB）
    """
    template_io = EmployeeService.generate_import_template()
    return Response(
        content=template_io.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=employee_import_template.xlsx"},
    )


@router.post(
    "/import",
    response_model=EmployeeImportResult,
    summary="批量导入员工（上传 Excel）",
)
async def import_employees(
    file: UploadFile = File(..., description="Excel 文件（.xlsx）"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/import

    通过上传 Excel 文件批量创建员工记录。文件格式须符合 /import-template 返回的模板。

    Body（multipart/form-data）：
      - file : 必填，符合导入模板格式的 .xlsx 文件

    响应（200）：
      EmployeeImportResult {
        success_count : int   -- 成功导入的员工数
        fail_count    : int   -- 失败的行数
        errors        : list  -- 失败明细（行号 + 错误原因）
      }

    权限：仅 admin 或 hr 角色（require_roles("admin", "hr")）

    注意：此端点必须注册在 /{employee_id} 路由之前。

    对应 Service：EmployeeService.import_excel()
    """
    file_bytes = await file.read()
    result = await EmployeeService.import_excel(db, file_bytes)
    return result


@router.post(
    "/bulk-offboard",
    summary="批量离职",
)
async def bulk_offboard(
    data: BulkOffboardRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/bulk-offboard

    批量将多名员工状态变更为"已离职"，同时记录离职日期和离职原因。

    Body（JSON）：
      BulkOffboardRequest {
        employee_ids : List[int]  -- 要离职的员工 ID 列表
        leave_date   : date       -- 统一离职日期
        leave_reason : str        -- 离职原因（适用于所有员工）
      }

    响应（200）：
      { "updated": int, "message": str }
      updated 为实际变更成功的员工数量

    权限：仅 admin 或 hr 角色（require_roles("admin", "hr")）

    注意：此端点必须注册在 /{employee_id} 路由之前。

    对应 Service：EmployeeService.bulk_offboard()
    """
    count = await EmployeeService.bulk_offboard(
        db, data.employee_ids, data.leave_date, data.leave_reason
    )
    return {"updated": count, "message": f"成功办理 {count} 名员工离职"}


@router.post(
    "/bulk-transfer",
    summary="批量调岗",
)
async def bulk_transfer(
    data: BulkTransferRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/bulk-transfer

    批量将多名员工调岗至指定部门及职位。

    Body（JSON）：
      BulkTransferRequest {
        employee_ids  : List[int]  -- 要调岗的员工 ID 列表
        department_id : int        -- 目标部门 ID
        position      : str        -- 新职位名称
      }

    响应（200）：
      { "updated": int, "message": str }
      updated 为实际变更成功的员工数量

    权限：仅 admin 或 hr 角色（require_roles("admin", "hr")）

    注意：此端点必须注册在 /{employee_id} 路由之前。

    对应 Service：EmployeeService.bulk_transfer()
    """
    count = await EmployeeService.bulk_transfer(
        db,
        data.employee_ids,
        data.department_id,
        data.position,
        data.effective_date,
        current_user.id,
        data.remark,
    )
    return {"updated": count, "message": f"成功调岗 {count} 名员工"}


@router.get(
    "/alerts",
    response_model=List[EmployeeAlert],
    summary="获取员工预警列表（合同到期/试用期到期/本月生日）",
)
async def get_employee_alerts(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /api/v1/employees/alerts

    查询系统中所有需要 HR 关注的员工预警事项，包括：
      - 合同即将到期（默认 30 天内）
      - 试用期即将结束
      - 本月生日的员工

    响应（200）：
      List[EmployeeAlert] — 每条预警包含员工基本信息、预警类型和关键日期

    权限：任何已登录用户（get_current_user）

    注意：此端点必须注册在 /{employee_id} 路由之前，防止 "alerts" 被
    解析为 employee_id。

    对应 Service：EmployeeService.get_alerts()
    """
    roles, _manager_scoped = await _resolve_employee_scope(db, current_user)
    if "admin" in roles or "hr" in roles:
        return await EmployeeService.get_alerts(db)
    raise HTTPException(status_code=403, detail="仅 HR 或管理员可查看员工预警")


@router.post(
    "/{employee_id}/transfer",
    response_model=EmployeeOut,
    summary="单员工调岗",
)
async def transfer_employee(
    employee_id: int,
    data: TransferRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/{employee_id}/transfer

    对单个员工执行调岗，支持部门和职位任意或同时变更，并记录组织变更历史。
    """
    emp = await EmployeeService.transfer_employee(
        db,
        employee_id,
        department_id=data.department_id,
        position=data.position,
        effective_date=data.effective_date,
        operator_id=current_user.id,
        remark=data.remark,
    )
    out = EmployeeOut.model_validate(emp)
    out.location_name = emp.location.name if emp.location else None
    out.department_name = emp.department.name if emp.department else None
    direct_manager = getattr(emp, "__dict__", {}).get("direct_manager")
    out.direct_manager_name = direct_manager.name if direct_manager else None
    return out


@router.post(
    "/{employee_id}/offboard",
    response_model=EmployeeOut,
    summary="单员工离职",
)
async def offboard_employee(
    employee_id: int,
    data: OffboardRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/{employee_id}/offboard

    对单个员工执行离职流程：状态流转为离职，并记录离职日期与原因。
    """
    emp = await EmployeeService.transition_status(
        db,
        employee_id,
        StatusTransition(
            new_status="离职",
            leave_date=data.leave_date,
            leave_reason=data.leave_reason,
        ),
    )
    out = EmployeeOut.model_validate(emp)
    out.location_name = emp.location.name if emp.location else None
    out.department_name = emp.department.name if emp.department else None
    out.direct_manager_name = getattr(getattr(emp, "__dict__", {}).get("direct_manager"), "name", None)
    return out


@router.post(
    "/import-registration-form",
    response_model=RegistrationFormImportResult,
    summary="导入员工信息登记表（XLS格式，创建单个员工及附属数据）",
)
async def import_registration_form(
    file: UploadFile = File(..., description="员工信息登记表 XLS 文件"),
    employee_no: Optional[str] = Query(None, description="指定工号（不填则自动生成）"),
    password: Optional[str] = Query(None, description="初始密码（不填则默认123456）"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/import-registration-form

    解析员工自填的信息登记表（XLS 格式），一次性创建员工主记录及其全部附属数据
    （家庭成员、教育背景、工作经历等），适用于新员工入职时批量录入。

    Body（multipart/form-data）：
      - file        : 必填，员工信息登记表 .xls 文件
      - employee_no : 可选 Query 参数，指定工号；不填则由系统自动生成
      - password    : 可选 Query 参数，初始登录密码；不填则默认使用 123456

    响应（200）：
      RegistrationFormImportResult {
        success      : bool    -- 是否成功
        employee_id  : int     -- 新建员工的 ID
        employee_no  : str     -- 最终使用的工号
        errors       : list    -- 解析警告或错误列表
      }

    权限：任何已登录用户（get_current_user）

    注意：此端点必须注册在 /{employee_id} 路由之前。

    对应 Service：RegistrationFormService.import_xls()
    """
    file_bytes = await file.read()
    result = await RegistrationFormService.import_xls(
        db, file_bytes, employee_no=employee_no, default_password=password
    )
    return result


# ==================== 工作经历（固定路径，必须在 /{employee_id} 之前） ====================

@router.put(
    "/work-experiences/{exp_id}",
    response_model=WorkExperienceOut,
    summary="更新工作经历",
)
async def update_work_experience(
    exp_id: int,
    data: WorkExperienceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    PUT /api/v1/employees/work-experiences/{exp_id}

    更新指定 ID 的工作经历记录（不限定员工，通过 exp_id 直接定位）。
    此端点用于在已知经历 ID 的情况下快速修改，无需再携带 employee_id。

    Path 参数：
      - exp_id : int — 工作经历记录的主键 ID

    Body（JSON）：
      WorkExperienceUpdate — 需要更新的字段（公司名/职位/起止时间/描述等）

    响应（200）：
      WorkExperienceOut — 更新后的工作经历完整数据

    权限：任何已登录用户（get_current_user）

    注意：此端点路径以 /work-experiences/ 开头，属于固定路径，必须注册在
    /{employee_id} 之前，防止 FastAPI 将 "work-experiences" 误识别为 employee_id。

    对应 Service：WorkExperienceService.update()
    """
    return await WorkExperienceService.update(db, exp_id, data)


@router.delete(
    "/work-experiences/{exp_id}",
    status_code=204,
    summary="删除工作经历",
)
async def delete_work_experience(
    exp_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    DELETE /api/v1/employees/work-experiences/{exp_id}

    删除指定 ID 的工作经历记录。

    Path 参数：
      - exp_id : int — 工作经历记录的主键 ID

    响应（204）：No Content

    权限：任何已登录用户（get_current_user）

    注意：固定路径，必须注册在 /{employee_id} 之前。

    对应 Service：WorkExperienceService.delete()
    """
    await WorkExperienceService.delete(db, exp_id)


# ==================== 家庭成员（固定路径，必须在 /{employee_id} 之前） ====================

@router.put(
    "/family-members/{member_id}",
    response_model=FamilyMemberOut,
    summary="更新家庭成员",
)
async def update_family_member(
    member_id: int,
    data: FamilyMemberUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    PUT /api/v1/employees/family-members/{member_id}

    更新指定 ID 的家庭成员记录。

    Path 参数：
      - member_id : int — 家庭成员记录的主键 ID

    Body（JSON）：
      FamilyMemberUpdate — 需要更新的字段（姓名/关系/联系方式等）

    响应（200）：
      FamilyMemberOut — 更新后的家庭成员完整数据

    权限：任何已登录用户（get_current_user）

    注意：固定路径，必须注册在 /{employee_id} 之前。

    对应 Service：FamilyMemberService.update()
    """
    return await FamilyMemberService.update(db, member_id, data)


@router.delete(
    "/family-members/{member_id}",
    status_code=204,
    summary="删除家庭成员",
)
async def delete_family_member(
    member_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    DELETE /api/v1/employees/family-members/{member_id}

    删除指定 ID 的家庭成员记录。

    Path 参数：
      - member_id : int — 家庭成员记录的主键 ID

    响应（204）：No Content

    权限：任何已登录用户（get_current_user）

    注意：固定路径，必须注册在 /{employee_id} 之前。

    对应 Service：FamilyMemberService.delete()
    """
    await FamilyMemberService.delete(db, member_id)


# ==================== 教育背景（固定路径，必须在 /{employee_id} 之前） ====================

@router.put(
    "/education-records/{record_id}",
    response_model=EducationRecordOut,
    summary="更新教育记录",
)
async def update_education_record(
    record_id: int,
    data: EducationRecordUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    PUT /api/v1/employees/education-records/{record_id}

    更新指定 ID 的教育背景记录。

    Path 参数：
      - record_id : int — 教育记录的主键 ID

    Body（JSON）：
      EducationRecordUpdate — 需要更新的字段（学校/专业/学历/起止时间等）

    响应（200）：
      EducationRecordOut — 更新后的教育记录完整数据

    权限：任何已登录用户（get_current_user）

    注意：固定路径，必须注册在 /{employee_id} 之前。

    对应 Service：EducationRecordService.update()
    """
    return await EducationRecordService.update(db, record_id, data)


@router.delete(
    "/education-records/{record_id}",
    status_code=204,
    summary="删除教育记录",
)
async def delete_education_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    DELETE /api/v1/employees/education-records/{record_id}

    删除指定 ID 的教育背景记录。

    Path 参数：
      - record_id : int — 教育记录的主键 ID

    响应（204）：No Content

    权限：任何已登录用户（get_current_user）

    注意：固定路径，必须注册在 /{employee_id} 之前。

    对应 Service：EducationRecordService.delete()
    """
    await EducationRecordService.delete(db, record_id)


# ==================== 档案材料（固定路径，必须在 /{employee_id} 之前） ====================

@router.put(
    "/documents/{doc_id}",
    response_model=EmployeeDocumentOut,
    summary="更新档案材料",
)
async def update_document(
    doc_id: int,
    data: EmployeeDocumentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    PUT /api/v1/employees/documents/{doc_id}

    更新指定 ID 的档案材料记录（如身份证/学历证书/资质证书的扫描件信息）。

    Path 参数：
      - doc_id : int — 档案材料记录的主键 ID

    Body（JSON）：
      EmployeeDocumentUpdate — 需要更新的字段（材料名称/文件路径/有效期等）

    响应（200）：
      EmployeeDocumentOut — 更新后的档案材料完整数据

    权限：任何已登录用户（get_current_user）

    注意：固定路径，必须注册在 /{employee_id} 之前。

    对应 Service：EmployeeDocumentService.update()
    """
    return await EmployeeDocumentService.update(db, doc_id, data)


@router.delete(
    "/documents/{doc_id}",
    status_code=204,
    summary="删除档案材料",
)
async def delete_document(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    DELETE /api/v1/employees/documents/{doc_id}

    删除指定 ID 的档案材料记录。

    Path 参数：
      - doc_id : int — 档案材料记录的主键 ID

    响应（204）：No Content

    权限：任何已登录用户（get_current_user）

    注意：固定路径，必须注册在 /{employee_id} 之前。

    对应 Service：EmployeeDocumentService.delete()
    """
    await EmployeeDocumentService.delete(db, doc_id)


@router.get(
    "/{employee_id}/change-logs",
    response_model=List[EmployeeChangeLogOut],
    summary="获取员工任职变动历史",
)
async def list_employee_change_logs(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /api/v1/employees/{employee_id}/change-logs

    查询员工部门、职位、职级的历史变动记录，用于员工详情页展示调动前后归属和时间节点。
    """
    await _get_employee_in_scope(db, current_user, employee_id)
    return await EmployeeService.list_change_logs(db, employee_id)


# ==================== 员工详情（含 /{employee_id} 的路由） ====================

@router.get(
    "/{employee_id}",
    response_model=EmployeeOut,
    summary="获取员工详情（脱敏）",
)
async def get_employee(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /api/v1/employees/{employee_id}

    获取指定员工的详细信息（脱敏版本）。
    敏感字段（如身份证号、银行卡号）会被部分遮盖处理。

    Path 参数：
      - employee_id : int — 员工主键 ID

    响应（200）：
      EmployeeOut — 脱敏后的员工完整档案，额外包含：
        - location_name   : 工作地点名称（冗余展示）
        - department_name : 所属部门名称（冗余展示）

    响应（404）：员工不存在

    权限：任何已登录用户（get_current_user）

    如需查看明文敏感信息，请使用 GET /{employee_id}/sensitive（需本人或 admin/hr 权限）。

    对应 Service：EmployeeService.get_by_id()
    """
    emp = await _get_employee_in_scope(db, current_user, employee_id)
    out = EmployeeOut.model_validate(emp)
    out.location_name = emp.location.name if emp.location else None
    out.department_name = emp.department.name if emp.department else None
    return out


@router.get(
    "/{employee_id}/sensitive",
    response_model=EmployeeOutSensitive,
    summary="获取员工详情（含明文敏感信息）",
)
async def get_employee_sensitive(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /api/v1/employees/{employee_id}/sensitive

    获取指定员工的完整档案，包含明文敏感字段（身份证号、银行卡号等）。
    仅供员工本人、HR 或管理员在实际业务需要时调用。

    Path 参数：
      - employee_id : int — 员工主键 ID

    响应（200）：
      EmployeeOutSensitive — 包含全部明文敏感字段的员工档案，
      额外携带 location_name 和 department_name。

    响应（403）：当前用户不是员工本人，且不具备 admin 或 hr 角色
    响应（404）：员工不存在

    权限：员工本人，或 admin / hr 角色

    对应 Service：EmployeeService.get_by_id()
    """
    role_names = await get_user_role_names(db, current_user)
    if employee_id != current_user.id and not (role_names & {"admin", "hr"}):
        raise HTTPException(status_code=403, detail="仅可查看本人或 HR/管理员授权的敏感信息")

    emp = await EmployeeService.get_by_id(db, employee_id)
    out = EmployeeOutSensitive.model_validate(emp)
    return out.model_copy(update={
        "location_name": emp.location.name if emp.location else None,
        "department_name": emp.department.name if emp.department else None,
        "direct_manager_name": getattr(getattr(emp, "__dict__", {}).get("direct_manager"), "name", None),
    })


@router.post(
    "",
    response_model=EmployeeOut,
    status_code=201,
    summary="创建员工",
)
async def create_employee(
    data: EmployeeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees

    创建新员工档案记录，初始状态通常为"待入职"（pending）。

    Body（JSON）：
      EmployeeCreate {
        name          : str        -- 姓名（必填）
        phone         : str        -- 手机号（必填，作为登录账号）
        password      : str        -- 初始密码（可选，不填默认 123456）
        employee_no   : str        -- 工号（可选，不填则自动生成）
        department_id : int        -- 所属部门 ID
        location_id   : int        -- 工作地点 ID
        position      : str        -- 职位
        employment_type: str       -- 用工类型
        hire_date     : date       -- 入职日期
        ...（其他员工基本信息字段）
      }

    响应（201）：
      EmployeeOut — 新建员工的脱敏档案

    响应（409）：手机号或工号已存在（IntegrityError）

    权限：仅 admin 或 hr 角色（require_roles("admin", "hr")）

    对应 Service：EmployeeService.create()
    """
    emp = await EmployeeService.create(db, data)
    out = EmployeeOut.model_validate(emp)
    out.location_name = getattr(getattr(emp, "__dict__", {}).get("location"), "name", None)
    out.department_name = getattr(getattr(emp, "__dict__", {}).get("department"), "name", None)
    out.direct_manager_name = getattr(getattr(emp, "__dict__", {}).get("direct_manager"), "name", None)
    return out


@router.put(
    "/{employee_id}",
    response_model=EmployeeOut,
    summary="更新员工信息",
)
async def update_employee(
    employee_id: int,
    data: EmployeeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    PUT /api/v1/employees/{employee_id}

    更新指定员工的基本信息（不含状态流转，状态变更请使用 /transition 端点）。

    Path 参数：
      - employee_id : int — 员工主键 ID

    Body（JSON）：
      EmployeeUpdate — 需要更新的字段（支持部分更新）：
        name / phone / department_id / location_id / position /
        employment_type / education / ...

    响应（200）：
      EmployeeOut — 更新后的员工脱敏档案，含 location_name 和 department_name

    响应（403）：当前用户不具备 admin 或 hr 角色
    响应（404）：员工不存在

    权限：仅 admin 或 hr 角色（require_roles("admin", "hr")）

    对应 Service：EmployeeService.update()
    """
    emp = await EmployeeService.update(db, employee_id, data)
    out = EmployeeOut.model_validate(emp)
    out.location_name = getattr(getattr(emp, "__dict__", {}).get("location"), "name", None)
    out.department_name = getattr(getattr(emp, "__dict__", {}).get("department"), "name", None)
    out.direct_manager_name = getattr(getattr(emp, "__dict__", {}).get("direct_manager"), "name", None)
    return out


# ==================== 工作经历（含 employee_id 参数） ====================

@router.get(
    "/{employee_id}/work-experiences",
    response_model=List[WorkExperienceOut],
    summary="获取员工工作经历列表",
)
async def list_work_experiences(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /api/v1/employees/{employee_id}/work-experiences

    获取指定员工的全部工作经历记录列表（按时间倒序排列）。

    Path 参数：
      - employee_id : int — 员工主键 ID

    响应（200）：
      List[WorkExperienceOut] — 工作经历列表，每条包含公司名/职位/起止时间/描述

    响应（404）：员工不存在

    权限：任何已登录用户（get_current_user）

    对应 Service：WorkExperienceService.get_by_employee()
    """
    await _get_employee_in_scope(db, current_user, employee_id)
    return await WorkExperienceService.get_by_employee(db, employee_id)


@router.post(
    "/{employee_id}/work-experiences",
    response_model=WorkExperienceOut,
    status_code=201,
    summary="新增工作经历",
)
async def create_work_experience(
    employee_id: int,
    data: WorkExperienceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/{employee_id}/work-experiences

    为指定员工新增一条工作经历记录。
    系统会将 URL 中的 employee_id 自动写入 data.employee_id，无需前端重复传递。

    Path 参数：
      - employee_id : int — 员工主键 ID

    Body（JSON）：
      WorkExperienceCreate {
        company_name : str  -- 公司名称（必填）
        position     : str  -- 担任职位
        start_date   : date -- 入职日期
        end_date     : date -- 离职日期（在职中可为 null）
        description  : str  -- 工作内容描述
      }

    响应（201）：
      WorkExperienceOut — 新建的工作经历记录

    权限：任何已登录用户（get_current_user）

    对应 Service：WorkExperienceService.create()
    """
    data.employee_id = employee_id
    return await WorkExperienceService.create(db, data)


# ==================== 档案材料（含 employee_id 参数） ====================

@router.get(
    "/{employee_id}/documents",
    response_model=List[EmployeeDocumentOut],
    summary="获取员工档案材料列表",
)
async def list_documents(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    GET /api/v1/employees/{employee_id}/documents

    获取指定员工的全部档案材料列表（身份证、学历证书、资质证书等扫描件信息）。

    Path 参数：
      - employee_id : int — 员工主键 ID

    响应（200）：
      List[EmployeeDocumentOut] — 档案材料列表，每条包含材料名称/类型/文件路径/有效期

    响应（404）：员工不存在

    权限：任何已登录用户（get_current_user）

    对应 Service：EmployeeDocumentService.get_by_employee()
    """
    return await EmployeeDocumentService.get_by_employee(db, employee_id)


@router.post(
    "/{employee_id}/documents",
    response_model=EmployeeDocumentOut,
    status_code=201,
    summary="新增档案材料",
)
async def create_document(
    employee_id: int,
    data: EmployeeDocumentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/{employee_id}/documents

    为指定员工新增一条档案材料记录。
    系统会将 URL 中的 employee_id 自动写入 data.employee_id。

    Path 参数：
      - employee_id : int — 员工主键 ID

    Body（JSON）：
      EmployeeDocumentCreate {
        doc_type     : str  -- 材料类型（identity_card/diploma/certificate 等）
        doc_name     : str  -- 材料名称
        file_path    : str  -- 文件存储路径（上传后获得的 URL）
        expiry_date  : date -- 有效期（无固定有效期填 null）
      }

    响应（201）：
      EmployeeDocumentOut — 新建的档案材料记录

    权限：任何已登录用户（get_current_user）

    对应 Service：EmployeeDocumentService.create()
    """
    data.employee_id = employee_id
    return await EmployeeDocumentService.create(db, data)


# ==================== 家庭成员（含 employee_id 参数） ====================

@router.get(
    "/{employee_id}/family-members",
    response_model=List[FamilyMemberOut],
    summary="获取员工家庭成员列表",
)
async def list_family_members(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    GET /api/v1/employees/{employee_id}/family-members

    获取指定员工的全部家庭成员记录列表。

    Path 参数：
      - employee_id : int — 员工主键 ID

    响应（200）：
      List[FamilyMemberOut] — 家庭成员列表，每条包含姓名/与员工关系/联系方式/工作单位

    响应（404）：员工不存在

    权限：任何已登录用户（get_current_user）

    对应 Service：FamilyMemberService.get_by_employee()
    """
    return await FamilyMemberService.get_by_employee(db, employee_id)


@router.post(
    "/{employee_id}/family-members",
    response_model=FamilyMemberOut,
    status_code=201,
    summary="新增家庭成员",
)
async def create_family_member(
    employee_id: int,
    data: FamilyMemberCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/{employee_id}/family-members

    为指定员工新增一条家庭成员记录。
    系统会将 URL 中的 employee_id 自动写入 data.employee_id。

    Path 参数：
      - employee_id : int — 员工主键 ID

    Body（JSON）：
      FamilyMemberCreate {
        name         : str  -- 家庭成员姓名（必填）
        relationship : str  -- 与员工的关系（配偶/子女/父母等）
        phone        : str  -- 联系方式
        company      : str  -- 工作单位
      }

    响应（201）：
      FamilyMemberOut — 新建的家庭成员记录

    权限：任何已登录用户（get_current_user）

    对应 Service：FamilyMemberService.create()
    """
    data.employee_id = employee_id
    return await FamilyMemberService.create(db, data)


# ==================== 教育背景（含 employee_id 参数） ====================

@router.get(
    "/{employee_id}/education-records",
    response_model=List[EducationRecordOut],
    summary="获取员工教育背景列表",
)
async def list_education_records(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    GET /api/v1/employees/{employee_id}/education-records

    获取指定员工的全部教育背景记录列表（按时间倒序排列）。

    Path 参数：
      - employee_id : int — 员工主键 ID

    响应（200）：
      List[EducationRecordOut] — 教育记录列表，每条包含学校/专业/学历/起止时间

    响应（404）：员工不存在

    权限：任何已登录用户（get_current_user）

    对应 Service：EducationRecordService.get_by_employee()
    """
    return await EducationRecordService.get_by_employee(db, employee_id)


@router.post(
    "/{employee_id}/education-records",
    response_model=EducationRecordOut,
    status_code=201,
    summary="新增教育背景",
)
async def create_education_record(
    employee_id: int,
    data: EducationRecordCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/{employee_id}/education-records

    为指定员工新增一条教育背景记录。
    系统会将 URL 中的 employee_id 自动写入 data.employee_id。

    Path 参数：
      - employee_id : int — 员工主键 ID

    Body（JSON）：
      EducationRecordCreate {
        school      : str  -- 学校名称（必填）
        major       : str  -- 专业
        degree      : str  -- 学历（高中/大专/本科/硕士/博士）
        start_date  : date -- 入学日期
        end_date    : date -- 毕业日期
      }

    响应（201）：
      EducationRecordOut — 新建的教育背景记录

    权限：任何已登录用户（get_current_user）

    对应 Service：EducationRecordService.create()
    """
    data.employee_id = employee_id
    return await EducationRecordService.create(db, data)


# ==================== 技能特长（含 employee_id 参数） ====================

@router.get(
    "/{employee_id}/skills",
    response_model=Optional[EmployeePersonalSkillOut],
    summary="获取员工技能特长",
)
async def get_employee_skills(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /api/v1/employees/{employee_id}/skills

    获取指定员工的技能特长记录。每名员工只有一条技能特长记录（1:1 关系）。

    Path 参数：
      - employee_id : int — 员工主键 ID

    响应（200）：
      EmployeePersonalSkillOut — 员工技能特长数据（语言能力/专业技能/证书等）
      若员工尚未填写技能特长，返回 null

    权限：任何已登录用户（get_current_user）

    对应 Service：EmployeePersonalSkillService.get_by_employee()
    """
    await _get_employee_in_scope(db, current_user, employee_id)
    return await EmployeePersonalSkillService.get_by_employee(db, employee_id)


@router.put(
    "/{employee_id}/skills",
    response_model=EmployeePersonalSkillOut,
    summary="创建或更新员工技能特长",
)
async def upsert_employee_skills(
    employee_id: int,
    data: EmployeePersonalSkillCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    PUT /api/v1/employees/{employee_id}/skills

    创建或更新指定员工的技能特长记录（Upsert 语义）。
    若该员工已有技能记录则更新，否则新建。
    系统会将 URL 中的 employee_id 自动写入 data.employee_id。

    Path 参数：
      - employee_id : int — 员工主键 ID

    Body（JSON）：
      EmployeePersonalSkillCreate {
        languages    : str  -- 语言能力描述
        skills       : str  -- 专业技能描述
        certificates : str  -- 持有证书列表
        others       : str  -- 其他特长
      }

    响应（200）：
      EmployeePersonalSkillOut — 最终的技能特长记录

    权限：任何已登录用户（get_current_user）

    对应 Service：EmployeePersonalSkillService.upsert()
    """
    data.employee_id = employee_id
    return await EmployeePersonalSkillService.upsert(db, data)


# ==================== 状态流转 ====================

@router.post(
    "/{employee_id}/transition",
    response_model=EmployeeOut,
    summary="员工状态变更",
)
async def transition_employee_status(
    employee_id: int,
    transition: StatusTransition,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/{employee_id}/transition

    触发员工生命周期状态的流转，执行合法的状态迁移。

    合法状态迁移路径：
      待入职（pending）
        → 试用期（probation）：正式入职
      试用期（probation）
        → 在职（active）    ：试用期转正
        → 离职（resigned）  ：试用期内离职
      在职（active）
        → 离职（resigned）  ：正常离职/辞退

    Path 参数：
      - employee_id : int — 员工主键 ID

    Body（JSON）：
      StatusTransition {
        action       : str   -- 流转动作（onboard/confirm/resign 等）
        effective_date: date -- 生效日期
        reason       : str  -- 变更原因（可选）
      }

    响应（200）：
      EmployeeOut — 状态变更后的员工档案

    响应（400）：非法的状态迁移路径
    响应（404）：员工不存在

    权限：任何已登录用户（get_current_user）；
    注意：实际业务中建议限制为 admin/hr，当前实现由 Service 层校验流转权限。

    对应 Service：EmployeeService.transition_status()
    """
    emp = await EmployeeService.transition_status(db, employee_id, transition)
    return EmployeeOut.model_validate(emp)


# ==================== 合同管理 ====================

@router.get(
    "/{employee_id}/contracts",
    response_model=List[ContractOut],
    summary="获取员工合同列表",
)
async def list_employee_contracts(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    GET /api/v1/employees/{employee_id}/contracts

    获取指定员工的全部合同记录列表（含历史合同及当前有效合同）。

    Path 参数：
      - employee_id : int — 员工主键 ID

    响应（200）：
      List[ContractOut] — 合同列表，每条包含合同编号/类型/起止日期/状态/签署信息

    响应（404）：员工不存在

    权限：任何已登录用户（get_current_user）

    对应 Service：ContractService.get_by_employee()
    """
    return await ContractService.get_by_employee(db, employee_id)


@router.post(
    "/contracts",
    response_model=ContractOut,
    status_code=201,
    summary="创建合同",
)
async def create_contract(
    data: ContractCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/contracts

    为员工创建新合同记录（新签合同，非续签）。
    员工 ID 通过 Body 中的 employee_id 字段传入，不在 URL 路径中。

    注意：此端点路径为 /contracts（固定路径），已在 /{employee_id} 之前注册，
    可以正常路由。

    Body（JSON）：
      ContractCreate {
        employee_id    : int   -- 员工 ID（必填）
        contract_type  : str   -- 合同类型（劳动合同/劳务合同）
        start_date     : date  -- 合同开始日期
        end_date       : date  -- 合同结束日期
        status         : str   -- 合同状态（生效中/已到期/已解除）
      }

    响应（201）：
      ContractOut — 新建的合同记录

    权限：任何已登录用户（get_current_user）

    对应 Service：ContractService.create()
    """
    return await ContractService.create(db, data)


@router.post(
    "/contracts/{contract_id}/renew",
    response_model=ContractOut,
    summary="续签合同",
)
async def renew_contract(
    contract_id: int,
    data: ContractRenew,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/employees/contracts/{contract_id}/renew

    对指定合同执行续签操作，生成新的合同记录并将旧合同标记为"已到期"。

    Path 参数：
      - contract_id : int — 原合同的主键 ID

    Body（JSON）：
      ContractRenew {
        end_date  : date  -- 新合同的结束日期（必填）
        salary    : float -- 续签后的薪资（可与原合同不同）
        notes     : str   -- 续签备注
      }

    响应（200）：
      ContractOut — 新生成的续签合同记录

    响应（404）：原合同不存在

    权限：任何已登录用户（get_current_user）

    对应 Service：ContractService.renew()
    """
    return await ContractService.renew(db, contract_id, data)


@router.get(
    "/contracts/expiring",
    response_model=List[ContractExpiryItem],
    summary="查询即将到期的合同",
)
async def list_expiring_contracts(
    days: int = Query(30, ge=1, le=365, description="到期天数范围"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    GET /api/v1/employees/contracts/expiring

    查询在未来指定天数内即将到期的所有合同，默认查询 30 天内到期的合同。
    供 HR 提前安排续签或离职手续。

    Query 参数：
      - days : int — 查询窗口（天数），范围 1~365，默认 30

    响应（200）：
      List[ContractExpiryItem] — 即将到期合同列表，每条包含：
        employee_id   : 员工 ID
        employee_name : 员工姓名
        contract_id   : 合同 ID
        end_date      : 合同到期日
        days_remaining: 剩余天数

    权限：任何已登录用户（get_current_user）

    对应 Service：ContractService.get_expiring()
    """
    return await ContractService.get_expiring(db, days=days)
