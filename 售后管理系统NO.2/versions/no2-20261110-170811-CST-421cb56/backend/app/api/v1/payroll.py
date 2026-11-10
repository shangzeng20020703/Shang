"""
薪酬管理 API Routes - Payroll Management
=========================================

本模块提供售后管理系统的完整薪酬管理接口，涵盖：

**核心业务模块**：
- 薪酬结构（SalaryStructure）：定义员工薪资组成（基本工资、绩效工资等）
- 社保配置（SocialInsuranceConfig）：按城市+年度管理社保缴纳比例
- 社保记录（SocialInsuranceRecord）：月度个人社保数据的生成与查询
- 薪资计算（PayrollCalculation）：批量计算月度薪资（含个税、社保）
- 薪资记录（SalaryRecord）：员工月薪记录的查询、调整、审批、发放
- 个税记录（TaxRecord）：个人所得税明细（常规月薪 + 年终奖）
- 工资条（PaySlip）：工资条发送、查阅、疑问申诉全流程
- 薪资看板（Dashboard）：月度成本汇总 + 月度趋势图
- 公司主体（Company）：多主体用工支持（本公司各子公司）
- 税法版本（TaxLawVersion）：管理个税税率档次（支持法规迭代）
- 专项附加扣除（EmployeeTaxDeduction）：子女教育、住房贷款等
- 奖金管理（BonusRecord）：年终奖/项目奖金/绩效奖金录入与发放
- 银行代发文件（BankPaymentFile）：工行/建行/招行格式代发文件生成
- 薪资成本分析（CostAnalysis）：按部门/月份汇总薪资成本

**权限说明**：
- 所有端点均需登录认证：`Depends(get_current_user)`
- 管理操作（写入/发放/导出）：当前版本使用通用认证，建议添加 finance/admin 角色校验

**路由前缀**（注册在 router.py）：`/api/v1/payroll`

**技术栈**：FastAPI + Pydantic v2 + SQLAlchemy 2.0 (async) + PostgreSQL 17

**Service 对应关系**：
- `SalaryStructureService`       → `app/services/payroll.py`
- `SocialInsuranceConfigService` → `app/services/payroll.py`
- `SocialInsuranceRecordService` → `app/services/payroll.py`
- `PayrollCalculationService`    → `app/services/payroll.py`
- `SalaryRecordService`          → `app/services/payroll.py`
- `TaxRecordService`             → `app/services/payroll.py`
- `TaxCalculationService`        → `app/services/payroll.py`
- `PaySlipService`               → `app/services/payroll.py`
- `PayrollDashboardService`      → `app/services/payroll.py`
- `CompanyService`               → `app/services/payroll.py`
- `TaxLawVersionService`         → `app/services/payroll.py`
- `EmployeeTaxDeductionService`  → `app/services/payroll.py`
- `BonusService`                 → `app/services/payroll.py`
- `BankPaymentService`           → `app/services/payroll.py`
- `CostAnalysisService`          → `app/services/payroll.py`
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.models.organization import Department
from app.schemas.payroll import (
    BonusRecordCreate,
    BonusRecordOut,
    BulkBonusCreate,
    CompanyCreate,
    CompanyUpdate,
    CompanyOut,
    CostAnalysisResult,
    TaxLawVersionCreate,
    TaxLawVersionUpdate,
    TaxLawVersionOut,
    TaxBracketCreate,
    TaxBracketOut,
    EmployeeTaxDeductionCreate,
    EmployeeTaxDeductionUpdate,
    EmployeeTaxDeductionOut,
    SalaryStructureCreate,
    SalaryStructureUpdate,
    SalaryStructureOut,
    SocialInsuranceConfigCreate,
    SocialInsuranceConfigUpdate,
    SocialInsuranceConfigOut,
    ActivateCityYearRequest,
    GenerateSocialInsuranceRequest,
    SocialInsuranceRecordOut,
    SalaryRecordOut,
    SalaryRecordAdjust,
    SalaryRecordApprovalAction,
    PayrollCalcRequest,
    PayrollCalcResult,
    TaxRecordOut,
    TaxImportBatchOut,
    BonusTaxRequest,
    PaySlipOut,
    PaySlipSendRequest,
    PaySlipQuerySubmit,
    PaySlipReply,
    SalaryCostSummary,
    SalaryCostTrend,
)
from app.services.payroll import (
    BankPaymentService,
    BonusService,
    CompanyService,
    CostAnalysisService,
    TaxLawVersionService,
    EmployeeTaxDeductionService,
    SalaryStructureService,
    SocialInsuranceConfigService,
    PayrollCalculationService,
    SalaryRecordService,
    TaxCalculationService,
    TaxRecordService,
    TaxImportService,
    PaySlipService,
    PayrollDashboardService,
    SocialInsuranceRecordService,
)

# 路由器实例，由 api/v1/router.py 以 prefix="/payroll" 挂载
router = APIRouter(tags=["薪酬管理"])


# ============================================================
# EmployeePayrollProfile 员工薪酬档案
# ============================================================

class EmployeePayrollProfileOut(BaseModel):
    """薪酬域中的员工薪酬基础资料，避免散落在员工主档编辑界面。"""

    id: int
    employee_no: Optional[str] = None
    name: str
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    position: Optional[str] = None
    employment_type: Optional[str] = None
    status: Optional[str] = None
    probation_salary: Optional[float] = None
    social_insurance_base: Optional[float] = None
    social_insurance_city: Optional[str] = None
    housing_fund_city: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account: Optional[str] = None
    updated_at: Optional[datetime] = None


class EmployeePayrollProfileUpdate(BaseModel):
    """仅允许薪酬域维护员工发薪、社保和银行相关字段。"""

    probation_salary: Optional[float] = Field(default=None, ge=0)
    social_insurance_base: Optional[float] = Field(default=None, ge=0)
    social_insurance_city: Optional[str] = Field(default=None, max_length=50)
    housing_fund_city: Optional[str] = Field(default=None, max_length=50)
    bank_name: Optional[str] = Field(default=None, max_length=100)
    bank_account: Optional[str] = Field(default=None, max_length=50)


def _build_employee_payroll_profile(
    employee: Employee,
    department_name: Optional[str] = None,
) -> EmployeePayrollProfileOut:
    return EmployeePayrollProfileOut(
        id=employee.id,
        employee_no=employee.employee_no,
        name=employee.name,
        department_id=employee.department_id,
        department_name=department_name,
        position=employee.position,
        employment_type=employee.employment_type,
        status=employee.status,
        probation_salary=float(employee.probation_salary) if employee.probation_salary is not None else None,
        social_insurance_base=float(employee.social_insurance_base) if employee.social_insurance_base is not None else None,
        social_insurance_city=employee.social_insurance_city,
        housing_fund_city=employee.housing_fund_city,
        bank_name=employee.bank_name,
        bank_account=employee.bank_account,
        updated_at=employee.updated_at,
    )


@router.get("/employee-payroll-profiles", response_model=list[EmployeePayrollProfileOut], summary="查询员工薪酬档案")
async def list_employee_payroll_profiles(
    department_id: Optional[int] = Query(default=None, description="按部门筛选"),
    keyword: Optional[str] = Query(default=None, description="按工号、姓名或岗位搜索"),
    status_filter: Optional[str] = Query(default=None, alias="status", description="按员工状态筛选"),
    skip: int = Query(default=0, ge=0, description="跳过条数"),
    limit: int = Query(default=1000, ge=1, le=5000, description="返回条数"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> list[EmployeePayrollProfileOut]:
    stmt = (
        select(Employee, Department.name.label("department_name"))
        .outerjoin(Department, Employee.department_id == Department.id)
        .where(Employee.is_active == True)
    )
    if department_id is not None:
        stmt = stmt.where(Employee.department_id == department_id)
    if status_filter:
        stmt = stmt.where(Employee.status == status_filter)
    if keyword:
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                Employee.employee_no.ilike(kw),
                Employee.name.ilike(kw),
                Employee.position.ilike(kw),
            )
        )
    stmt = stmt.order_by(Employee.employee_no.asc(), Employee.id.asc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return [
        _build_employee_payroll_profile(employee, department_name)
        for employee, department_name in result.all()
    ]


@router.put("/employee-payroll-profiles/{employee_id}", response_model=EmployeePayrollProfileOut, summary="更新员工薪酬档案")
async def update_employee_payroll_profile(
    employee_id: int,
    data: EmployeePayrollProfileUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> EmployeePayrollProfileOut:
    employee = await db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="员工不存在")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(employee, field, value)

    await db.flush()
    await db.refresh(employee)

    department_name = None
    if employee.department_id:
        dept_result = await db.execute(
            select(Department.name).where(Department.id == employee.department_id)
        )
        department_name = dept_result.scalar_one_or_none()
    return _build_employee_payroll_profile(employee, department_name)


# ============================================================
# SalaryStructure 薪酬结构 CRUD
# ============================================================
# 薪酬结构定义员工薪资的各项组成部分（基本工资、岗位工资、绩效工资等），
# 每个员工可以有多条薪酬结构记录（例如调薪时创建新版本），
# 按生效日期区分历史与当前版本。

@router.get("/salary-structures", response_model=list[SalaryStructureOut])
async def list_salary_structures(
    employee_id: Optional[int] = Query(default=None, description="按员工ID筛选"),
    skip: int = Query(default=0, ge=0, description="跳过条数"),
    limit: int = Query(default=1000, ge=1, le=5000, description="返回条数"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> list[SalaryStructureOut]:
    """
    GET /payroll/salary-structures

    查询薪酬结构列表，支持按员工ID筛选。

    **Query 参数**：
    - `employee_id`（可选）：员工ID，指定后仅返回该员工的薪酬结构
    - `skip`：分页偏移量，默认 0
    - `limit`：每页条数，默认 1000，最大 5000

    **响应**：`SalaryStructureOut[]`
    - id、employee_id、基本工资、绩效工资、各类补贴、生效日期等字段

    **权限**：登录用户均可访问（`get_current_user`）

    **Service**：`SalaryStructureService.get_by_employee()` 或 `SalaryStructureService.list_all()`
    """
    if employee_id is not None:
        objs = await SalaryStructureService.get_by_employee(db, employee_id)
        return [SalaryStructureOut.model_validate(o) for o in objs]
    objs = await SalaryStructureService.list_all(db, skip=skip, limit=limit)
    return [SalaryStructureOut.model_validate(o) for o in objs]


@router.post("/salary-structures", response_model=SalaryStructureOut, status_code=status.HTTP_201_CREATED)
async def create_salary_structure(
    data: SalaryStructureCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> SalaryStructureOut:
    """
    POST /payroll/salary-structures

    创建员工薪酬结构。通常在员工入职或调薪时调用。

    **请求体**（`SalaryStructureCreate`）：
    - `employee_id`：所属员工ID
    - `effective_date`：生效日期（新建后即刻生效或未来生效）
    - `basic_salary`：基本工资（Decimal）
    - `performance_salary`：绩效工资
    - 其他补贴字段（交通/餐饮/住房等）

    **响应**：201 Created + `SalaryStructureOut`

    **权限**：登录用户（`get_current_user`）；实际建议限 hr/finance/admin 角色

    **Service**：`SalaryStructureService.create(db, data)`
    """
    obj = await SalaryStructureService.create(db, data)
    return SalaryStructureOut.model_validate(obj)


# 注意：/employee/{employee_id} 路由必须在 /{structure_id} 之前注册，
# 否则 FastAPI 会尝试将字符串 "employee" 解析为整数 structure_id，导致 422 错误。
@router.get("/salary-structures/employee/{employee_id}", response_model=list[SalaryStructureOut])
async def list_salary_structures_by_employee(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> list[SalaryStructureOut]:
    """
    GET /payroll/salary-structures/employee/{employee_id}

    查询指定员工的全部薪酬结构记录，按生效日期倒序排列（最新在前）。

    **Path 参数**：
    - `employee_id`：员工ID

    **响应**：`SalaryStructureOut[]`，包含该员工的历史薪资版本

    **权限**：登录用户（`get_current_user`）

    **Service**：`SalaryStructureService.get_by_employee(db, employee_id)`
    """
    objs = await SalaryStructureService.get_by_employee(db, employee_id)
    return [SalaryStructureOut.model_validate(o) for o in objs]


@router.get("/salary-structures/{structure_id}", response_model=SalaryStructureOut)
async def get_salary_structure(
    structure_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> SalaryStructureOut:
    """
    GET /payroll/salary-structures/{structure_id}

    查询单条薪酬结构的详细信息。

    **Path 参数**：
    - `structure_id`：薪酬结构ID

    **响应**：`SalaryStructureOut`；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`SalaryStructureService.get(db, structure_id)`
    """
    obj = await SalaryStructureService.get(db, structure_id)
    if not obj:
        raise HTTPException(status_code=404, detail="薪酬结构不存在")
    return SalaryStructureOut.model_validate(obj)


@router.put("/salary-structures/{structure_id}", response_model=SalaryStructureOut)
async def update_salary_structure(
    structure_id: int,
    data: SalaryStructureUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> SalaryStructureOut:
    """
    PUT /payroll/salary-structures/{structure_id}

    更新薪酬结构信息（如调整各项工资金额或生效日期）。

    **Path 参数**：
    - `structure_id`：薪酬结构ID

    **请求体**（`SalaryStructureUpdate`）：需要更新的字段（所有字段均为可选）

    **响应**：更新后的 `SalaryStructureOut`；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`SalaryStructureService.update(db, structure_id, data)`
    """
    obj = await SalaryStructureService.update(db, structure_id, data)
    if not obj:
        raise HTTPException(status_code=404, detail="薪酬结构不存在")
    return SalaryStructureOut.model_validate(obj)


@router.delete("/salary-structures/{structure_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_salary_structure(
    structure_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> None:
    """
    DELETE /payroll/salary-structures/{structure_id}

    删除薪酬结构记录。注意：已参与薪资计算的结构建议保留，仅删除错误录入的记录。

    **Path 参数**：
    - `structure_id`：薪酬结构ID

    **响应**：204 No Content；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`SalaryStructureService.delete(db, structure_id)`
    """
    success = await SalaryStructureService.delete(db, structure_id)
    if not success:
        raise HTTPException(status_code=404, detail="薪酬结构不存在")


# ============================================================
# SocialInsuranceConfig 社保配置 CRUD
# ============================================================
# 社保配置按「城市 + 年度 + 险种」三个维度管理缴纳基数与比例。
# 本公司在北京和东莞均有用工，两地社保政策不同，须分别配置。
# 每年度社保基数调整后，通过 activate-city-year 接口切换生效版本。

@router.post("/social-insurance-configs", response_model=SocialInsuranceConfigOut, status_code=status.HTTP_201_CREATED)
async def create_social_insurance_config(
    data: SocialInsuranceConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> SocialInsuranceConfigOut:
    """
    POST /payroll/social-insurance-configs

    创建社保配置（某城市某年度某险种的缴纳比例和基数上下限）。

    **请求体**（`SocialInsuranceConfigCreate`）：
    - `city`：城市名称，如 "北京"、"东莞"
    - `year`：适用年度，如 2024
    - `insurance_type`：险种，如 "养老"、"医疗"、"失业"、"工伤"、"生育"、"公积金"
    - `company_rate`：企业缴费比例（小数，如 0.16）
    - `employee_rate`：个人缴费比例
    - `min_base`：缴费基数下限
    - `max_base`：缴费基数上限

    **响应**：201 Created + `SocialInsuranceConfigOut`

    **权限**：登录用户（`get_current_user`）

    **Service**：`SocialInsuranceConfigService.create(db, data)`
    """
    obj = await SocialInsuranceConfigService.create(db, data)
    return SocialInsuranceConfigOut.model_validate(obj)


@router.get("/social-insurance-configs", response_model=list[SocialInsuranceConfigOut])
async def list_social_insurance_configs(
    city: str = Query(..., description="城市: 北京/东莞"),
    year: int = Query(..., ge=2020, le=2100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[SocialInsuranceConfigOut]:
    """
    GET /payroll/social-insurance-configs?city=北京&year=2024

    按城市和年度查询所有险种的社保配置。常用于薪资计算前获取当前参数。

    **Query 参数**：
    - `city`（必填）：城市名称，如 "北京"
    - `year`（必填）：年度，范围 2020~2100

    **响应**：`SocialInsuranceConfigOut[]`，包含该城市当年所有险种配置

    **权限**：登录用户（`get_current_user`）

    **Service**：`SocialInsuranceConfigService.list_by_city_year(db, city, year)`
    """
    objs = await SocialInsuranceConfigService.list_by_city_year(db, city, year)
    return [SocialInsuranceConfigOut.model_validate(o) for o in objs]


@router.get("/social-insurance-configs/history", response_model=list[SocialInsuranceConfigOut])
async def list_social_insurance_config_history(
    city: str = Query(..., description="城市: 北京/东莞"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[SocialInsuranceConfigOut]:
    """
    GET /payroll/social-insurance-configs/history?city=北京

    查询某城市所有年份的社保配置历史版本，用于年度间的政策对比。

    **Query 参数**：
    - `city`（必填）：城市名称

    **响应**：`SocialInsuranceConfigOut[]`，按年度倒序排列

    **权限**：登录用户（`get_current_user`）

    **Service**：`SocialInsuranceConfigService.list_by_city(db, city)`
    """
    objs = await SocialInsuranceConfigService.list_by_city(db, city)
    return [SocialInsuranceConfigOut.model_validate(o) for o in objs]


# 注意：/social-insurance-configs/all 必须在 /{config_id} 之前注册，
# 否则 FastAPI 会将 "all" 解析为 config_id（整数），导致 422 错误。
@router.get("/social-insurance-configs/all", response_model=list[SocialInsuranceConfigOut], summary="查询城市全部历史配置")
async def list_all_city_configs(
    city: str = Query(..., description="城市名"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> list[SocialInsuranceConfigOut]:
    """
    GET /payroll/social-insurance-configs/all?city=北京

    查询某城市全部年度配置（包含已停用版本），用于配置管理列表页展示。
    与 /history 端点功能相同，此处为语义更清晰的别名路由。

    **Query 参数**：
    - `city`（必填）：城市名称

    **响应**：`SocialInsuranceConfigOut[]`

    **权限**：登录用户（`get_current_user`）

    **Service**：`SocialInsuranceConfigService.list_by_city(db, city)`
    """
    objs = await SocialInsuranceConfigService.list_by_city(db, city)
    return [SocialInsuranceConfigOut.model_validate(o) for o in objs]


@router.get("/social-insurance-configs/{config_id}", response_model=SocialInsuranceConfigOut)
async def get_social_insurance_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> SocialInsuranceConfigOut:
    """
    GET /payroll/social-insurance-configs/{config_id}

    查询单条社保配置的详细信息。

    **Path 参数**：
    - `config_id`：社保配置ID

    **响应**：`SocialInsuranceConfigOut`；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`SocialInsuranceConfigService.get(db, config_id)`
    """
    obj = await SocialInsuranceConfigService.get(db, config_id)
    if not obj:
        raise HTTPException(status_code=404, detail="社保配置不存在")
    return SocialInsuranceConfigOut.model_validate(obj)


@router.put("/social-insurance-configs/{config_id}", response_model=SocialInsuranceConfigOut)
async def update_social_insurance_config(
    config_id: int,
    data: SocialInsuranceConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> SocialInsuranceConfigOut:
    """
    PUT /payroll/social-insurance-configs/{config_id}

    更新社保配置参数（如调整缴费比例或基数上下限）。

    **Path 参数**：
    - `config_id`：社保配置ID

    **请求体**（`SocialInsuranceConfigUpdate`）：可选字段，仅更新传入的字段

    **响应**：更新后的 `SocialInsuranceConfigOut`；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`SocialInsuranceConfigService.update(db, config_id, data)`
    """
    obj = await SocialInsuranceConfigService.update(db, config_id, data)
    if not obj:
        raise HTTPException(status_code=404, detail="社保配置不存在")
    return SocialInsuranceConfigOut.model_validate(obj)


@router.delete("/social-insurance-configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_social_insurance_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> None:
    """
    DELETE /payroll/social-insurance-configs/{config_id}

    删除社保配置记录。建议仅删除错误录入的配置，历史数据应保留以备审计。

    **Path 参数**：
    - `config_id`：社保配置ID

    **响应**：204 No Content；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`SocialInsuranceConfigService.delete(db, config_id)`
    """
    success = await SocialInsuranceConfigService.delete(db, config_id)
    if not success:
        raise HTTPException(status_code=404, detail="社保配置不存在")


@router.post("/social-insurance-configs/activate-city-year", summary="激活城市年度社保配置")
async def activate_city_year_config(
    req: ActivateCityYearRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> dict:
    """
    POST /payroll/social-insurance-configs/activate-city-year

    将指定城市+年度的所有险种配置设为「当前生效」状态，
    同时将该城市其他年度的配置设为「停用」。

    此操作用于年度社保基数调整后，一键切换生效版本。

    **请求体**（`ActivateCityYearRequest`）：
    - `city`：城市名称，如 "北京"
    - `year`：要激活的年度，如 2024

    **响应**：
    ```json
    {"activated": 6, "city": "北京", "year": 2024}
    ```
    - `activated`：被激活的险种配置条数；若为 0 则返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`SocialInsuranceConfigService.activate_city_year(db, city, year)`
    """
    activated = await SocialInsuranceConfigService.activate_city_year(db, req.city, req.year)
    if activated == 0:
        raise HTTPException(status_code=404, detail=f"未找到 {req.city} {req.year}年 的社保配置")
    return {"activated": activated, "city": req.city, "year": req.year}


# ============================================================
# SocialInsuranceRecord 月度社保数据
# ============================================================
# 月度社保记录是按员工+月份生成的实际缴纳数据，
# 由系统根据员工的社保基数和当前生效的社保配置自动计算，
# 生成结果可作为薪资计算的输入和社保申报的依据。

@router.post("/social-insurance/generate", summary="生成月度社保数据")
async def generate_social_insurance(
    req: GenerateSocialInsuranceRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> dict:
    """
    POST /payroll/social-insurance/generate

    为指定年月生成所有在职员工的月度社保明细数据。
    操作幂等：同一年月重复调用会覆盖已有数据（适合数据修正场景）。

    **请求体**（`GenerateSocialInsuranceRequest`）：
    - `year`：年度，如 2024
    - `month`：月份，1~12
    - （可选）`department_id`：仅生成指定部门员工的数据

    **响应**：
    ```json
    {"generated": 85, "year": 2024, "month": 3}
    ```
    - `generated`：本次生成/更新的记录条数

    **权限**：登录用户（`get_current_user`）

    **Service**：`SocialInsuranceRecordService.generate(db, req)`
    """
    return await SocialInsuranceRecordService.generate(db, req)


@router.get("/social-insurance/records", response_model=dict, summary="查询月度社保数据")
async def list_social_insurance_records(
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    department_id: Optional[int] = Query(default=None),
    employee_id: Optional[int] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> dict:
    """
    GET /payroll/social-insurance/records?year=2024&month=3

    分页查询月度社保数据，支持按部门和员工筛选。
    返回数据已关联员工姓名、工号和部门名称，可直接用于前端表格展示。

    **Query 参数**：
    - `year`（必填）：年度
    - `month`（必填）：月份，1~12
    - `department_id`（可选）：部门ID筛选
    - `employee_id`（可选）：员工ID筛选
    - `page`：页码，从 1 开始
    - `page_size`：每页条数，默认 100，最大 500

    **响应**：
    ```json
    {
      "items": [SocialInsuranceRecordOut 数组，含 employee_name/employee_no/department_name],
      "total": 总条数,
      "page": 当前页,
      "page_size": 每页条数
    }
    ```

    **权限**：登录用户（`get_current_user`）

    **Service**：`SocialInsuranceRecordService.list_records(db, ...)`
    """
    records, total = await SocialInsuranceRecordService.list_records(
        db, year, month,
        department_id=department_id,
        employee_id=employee_id,
        page=page,
        page_size=page_size,
    )
    items = []
    for r in records:
        emp = r.employee
        dept_name = ""
        if emp and hasattr(emp, "department") and emp.department:
            dept_name = emp.department.name
        out = SocialInsuranceRecordOut.model_validate(r)
        out.employee_name = emp.name if emp else ""
        out.employee_no = emp.employee_no if emp else ""
        out.department_name = dept_name
        items.append(out)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ============================================================
# Payroll Calculation 薪资计算
# ============================================================
# 核心计算引擎入口：接收年月参数，批量计算所有员工当月薪资，
# 计算过程包括：基本工资 + 绩效 + 补贴 - 考勤扣款 - 个人社保 - 个税，
# 结果写入 salary_records 表，同时返回汇总统计。

@router.post("/calculate", response_model=PayrollCalcResult)
async def calculate_payroll(
    request: PayrollCalcRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> PayrollCalcResult:
    """
    POST /payroll/calculate

    批量计算指定年月的员工薪资，含个税、社保、公积金计算。
    计算完成后返回汇总统计和每人明细。

    **请求体**（`PayrollCalcRequest`）：
    - `year`：年度
    - `month`：月份，1~12
    - `employee_ids`（可选）：指定员工ID列表；不填则计算全部在职员工

    **响应**（`PayrollCalcResult`）：
    - `total_employees`：参与计算的员工数
    - `total_gross`：应发薪资合计（Decimal）
    - `total_net`：实发薪资合计
    - `total_tax`：个税合计
    - `total_social_insurance_company`：企业社保合计
    - `total_housing_fund_company`：企业公积金合计
    - `anomaly_count`：异常记录数（含异常标记的记录，如基数越界）
    - `records`：每人薪资明细 `SalaryRecordOut[]`

    **权限**：登录用户（`get_current_user`）

    **Service**：`PayrollCalculationService.calculate_batch(db, request)`
    """
    records = await PayrollCalculationService.calculate_batch(db, request)

    total_gross = sum((r.gross_salary for r in records), Decimal("0"))
    total_net = sum((r.net_salary for r in records), Decimal("0"))
    total_tax = sum((r.tax_amount for r in records), Decimal("0"))
    total_si_company = sum((r.social_insurance_company for r in records), Decimal("0"))
    total_hf_company = sum((r.housing_fund_company for r in records), Decimal("0"))
    anomaly_count = sum(1 for r in records if r.anomaly_flags_json)

    return PayrollCalcResult(
        total_employees=len(records),
        total_gross=total_gross,
        total_net=total_net,
        total_tax=total_tax,
        total_social_insurance_company=total_si_company,
        total_housing_fund_company=total_hf_company,
        anomaly_count=anomaly_count,
        records=[SalaryRecordOut.model_validate(r) for r in records],
    )


# ============================================================
# SalaryRecord 薪资记录
# ============================================================
# 薪资记录（salary_records 表）是每月每人的薪资计算结果，
# 记录从计算 → 调整 → 审批 → 发放的完整生命周期。
# 状态流转：draft（草稿）→ pending_approval（待审批）→ approved（已审批）→ paid（已发放）

@router.get("/salary-records", response_model=list[SalaryRecordOut])
async def list_salary_records_by_month(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    record_status: Optional[str] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[SalaryRecordOut]:
    """
    GET /payroll/salary-records?year=2024&month=3

    按月份查询薪资记录，可按状态筛选。常用于 HR 管理薪资批次的列表页。

    **Query 参数**：
    - `year`（必填）：年度
    - `month`（必填）：月份，1~12
    - `status`（可选）：记录状态筛选，可选值：
      - `draft`：计算草稿
      - `pending_approval`：待审批
      - `approved`：已审批
      - `paid`：已发放

    **响应**：`SalaryRecordOut[]`，含每人应发/实发/个税/社保各项明细

    **权限**：登录用户（`get_current_user`）

    **Service**：`SalaryRecordService.list_by_month(db, year, month, status)`
    """
    records = await SalaryRecordService.list_by_month(db, year, month, record_status)
    return [SalaryRecordOut.model_validate(r) for r in records]


@router.post("/salary-records/import", summary="导入月度工资数据")
async def import_salary_records(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    file: UploadFile = File(..., description="工资数据文件（.xlsx/.xls/.csv）"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> dict:
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上传文件不能为空")
    return await SalaryRecordService.import_salary_records(
        db,
        file_bytes=file_bytes,
        filename=file.filename or "",
        year=year,
        month=month,
    )


@router.get("/salary-records/export", summary="导出月度薪资明细Excel")
async def export_salary_records(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    department_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> StreamingResponse:
    content = await SalaryRecordService.export_salary_records_excel(db, year, month, department_id)
    filename = f"salary_records_{year}{str(month).zfill(2)}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/salary-records/employee/{employee_id}", response_model=list[SalaryRecordOut])
async def list_salary_records_by_employee(
    employee_id: int,
    year: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[SalaryRecordOut]:
    """
    GET /payroll/salary-records/employee/{employee_id}

    查询员工的历史薪资记录，可按年度过滤。用于员工薪资历史查询和个税汇算辅助。

    **Path 参数**：
    - `employee_id`：员工ID

    **Query 参数**：
    - `year`（可选）：年度筛选；不填则返回全部历史记录

    **响应**：`SalaryRecordOut[]`，按月份倒序排列

    **权限**：登录用户（`get_current_user`）

    **Service**：`SalaryRecordService.list_by_employee(db, employee_id, year)`
    """
    records = await SalaryRecordService.list_by_employee(db, employee_id, year)
    return [SalaryRecordOut.model_validate(r) for r in records]


@router.get("/salary-records/{record_id}", response_model=SalaryRecordOut)
async def get_salary_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> SalaryRecordOut:
    """
    GET /payroll/salary-records/{record_id}

    查询单条薪资记录的详细信息，包含所有计算明细字段。

    **Path 参数**：
    - `record_id`：薪资记录ID

    **响应**：`SalaryRecordOut`；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`SalaryRecordService.get(db, record_id)`
    """
    record = await SalaryRecordService.get(db, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="薪资记录不存在")
    return SalaryRecordOut.model_validate(record)


@router.post("/salary-records/{record_id}/adjust", response_model=SalaryRecordOut)
async def adjust_salary_record(
    record_id: int,
    data: SalaryRecordAdjust,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> SalaryRecordOut:
    """
    POST /payroll/salary-records/{record_id}/adjust

    手动调整薪资记录（用于特殊扣款或补发）。
    调整后记录状态重置为 draft，需重新走审批流程。
    只允许在 draft 状态下调整；已审批/已发放的记录不可调整（返回 400）。

    **Path 参数**：
    - `record_id`：薪资记录ID

    **请求体**（`SalaryRecordAdjust`）：
    - `adjustment_amount`：调整金额（正数为补发，负数为扣款）
    - `adjustment_reason`：调整原因说明

    **响应**：调整后的 `SalaryRecordOut`；若状态不符返回 400，若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`SalaryRecordService.adjust(db, record_id, data)`
    """
    try:
        record = await SalaryRecordService.adjust(db, record_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not record:
        raise HTTPException(status_code=404, detail="薪资记录不存在")
    return SalaryRecordOut.model_validate(record)


# ============================================================
# Approval 审批流程
# ============================================================
# 薪资审批采用批量操作模式：HR 计算完成后统一提交审批，
# 财务/管理层批量审批，审批通过后标记为已发放。
# 也支持单条记录的精细化审批操作。

@router.post("/salary-records/submit-approval")
async def submit_salary_records_for_approval(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> dict:
    """
    POST /payroll/salary-records/submit-approval?year=2024&month=3

    将指定月份所有 draft 状态的薪资记录批量提交审批（状态变为 pending_approval）。

    **Query 参数**：
    - `year`（必填）：年度
    - `month`（必填）：月份，1~12

    **响应**：
    ```json
    {"message": "已提交85条薪资记录审批", "count": 85}
    ```

    **权限**：登录用户（`get_current_user`）

    **Service**：`SalaryRecordService.submit_for_approval(db, year, month)`
    """
    records = await SalaryRecordService.submit_for_approval(db, year, month)
    return {"message": f"已提交{len(records)}条薪资记录审批", "count": len(records)}


@router.post("/salary-records/{record_id}/approve", response_model=SalaryRecordOut)
async def approve_salary_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> SalaryRecordOut:
    """
    POST /payroll/salary-records/{record_id}/approve

    审批单条薪资记录（状态从 pending_approval 变为 approved）。
    若记录状态不是 pending_approval，返回 400 错误。

    **Path 参数**：
    - `record_id`：薪资记录ID

    **响应**：审批后的 `SalaryRecordOut`；若状态不符返回 400，若不存在返回 404

    **权限**：登录用户（`get_current_user`）；建议限 finance/admin 角色

    **Service**：`SalaryRecordService.approve(db, record_id)`
    """
    try:
        record = await SalaryRecordService.approve(db, record_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not record:
        raise HTTPException(status_code=404, detail="薪资记录不存在")
    return SalaryRecordOut.model_validate(record)


@router.post("/salary-records/batch-approve")
async def batch_approve_salary_records(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> dict:
    """
    POST /payroll/salary-records/batch-approve?year=2024&month=3

    将指定月份所有 pending_approval 状态的薪资记录批量审批通过（状态变为 approved）。

    **Query 参数**：
    - `year`（必填）：年度
    - `month`（必填）：月份，1~12

    **响应**：
    ```json
    {"message": "已审批85条薪资记录", "count": 85}
    ```

    **权限**：登录用户（`get_current_user`）；建议限 finance/admin 角色

    **Service**：`SalaryRecordService.batch_approve(db, year, month)`
    """
    count = await SalaryRecordService.batch_approve(db, year, month)
    return {"message": f"已审批{count}条薪资记录", "count": count}


@router.post("/salary-records/mark-paid")
async def mark_salary_records_paid(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> dict:
    """
    POST /payroll/salary-records/mark-paid?year=2024&month=3

    将指定月份所有 approved 状态的薪资记录标记为已发放（状态变为 paid）。
    通常在银行代发完成后调用，作为财务闭环的最后一步。

    **Query 参数**：
    - `year`（必填）：年度
    - `month`（必填）：月份，1~12

    **响应**：
    ```json
    {"message": "已标记85条薪资记录为已发放", "count": 85}
    ```

    **权限**：登录用户（`get_current_user`）；建议限 finance/admin 角色

    **Service**：`SalaryRecordService.mark_paid(db, year, month)`
    """
    count = await SalaryRecordService.mark_paid(db, year, month)
    return {"message": f"已标记{count}条薪资记录为已发放", "count": count}


# ============================================================
# TaxRecord 个税
# ============================================================
# 个税记录记录每名员工每月的个人所得税计算明细，
# 采用累计预扣预缴法，全年累计应税收入减去累计扣除后计算税额。
# 年终奖采用单独的一次性奖金计税方式（不并入当月工资）。

@router.get("/tax-records/employee/{employee_id}", response_model=list[TaxRecordOut])
async def list_tax_records_by_employee(
    employee_id: int,
    year: int = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TaxRecordOut]:
    """
    GET /payroll/tax-records/employee/{employee_id}?year=2024

    查询员工指定年度的个税记录列表（按月逐条，含累计数据）。
    可用于员工个税汇算参考或协助员工处理税务申报。

    **Path 参数**：
    - `employee_id`：员工ID

    **Query 参数**：
    - `year`（必填）：年度

    **响应**：`TaxRecordOut[]`，按月份升序排列，含：
    - 当月应税收入、当月预扣税额、累计预扣税额等

    **权限**：登录用户（`get_current_user`）

    **Service**：`TaxRecordService.list_by_employee_year(db, employee_id, year)`
    """
    records = await TaxRecordService.list_by_employee_year(db, employee_id, year)
    return [TaxRecordOut.model_validate(r) for r in records]


@router.get("/tax-records", response_model=list[TaxRecordOut])
async def list_tax_records_by_month(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TaxRecordOut]:
    """
    GET /payroll/tax-records?year=2024&month=3

    按月查询全员个税记录，主要用于税务局申报文件导出。

    **Query 参数**：
    - `year`（必填）：年度
    - `month`（必填）：月份，1~12

    **响应**：`TaxRecordOut[]`，当月所有员工的个税明细

    **权限**：登录用户（`get_current_user`）

    **Service**：`TaxRecordService.list_by_month(db, year, month)`
    """
    records = await TaxRecordService.list_by_month(db, year, month)
    return [TaxRecordOut.model_validate(r) for r in records]


@router.get("/tax-imports", response_model=TaxImportBatchOut)
async def get_tax_import_batch(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    company_id: int = Query(..., description="公司主体ID"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> TaxImportBatchOut:
    batch = await TaxImportService.get_by_company_month(db, company_id=company_id, year=year, month=month)
    if not batch:
        raise HTTPException(status_code=404, detail="当前月份和公司尚未上传税款表")
    return TaxImportBatchOut(**TaxImportService.serialize_batch(batch))


@router.post("/tax-imports/upload", response_model=TaxImportBatchOut, status_code=status.HTTP_201_CREATED)
async def upload_tax_import_batch(
    company_id: int = Form(...),
    year: int = Form(...),
    month: int = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> TaxImportBatchOut:
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="月份必须在 1-12 之间")
    filename = file.filename or ""
    file_bytes = await file.read()
    batch = await TaxImportService.upload(
        db,
        company_id=company_id,
        year=year,
        month=month,
        filename=filename,
        file_bytes=file_bytes,
        current_user=current_user,
    )
    return TaxImportBatchOut(**TaxImportService.serialize_batch(batch))


@router.get("/tax-records/export", summary="导出月度个税明细Excel")
async def export_tax_records(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> StreamingResponse:
    content = await TaxRecordService.export_tax_records_excel(db, year, month)
    filename = f"tax_records_{year}{str(month).zfill(2)}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tax-records/filing-template/export", summary="导出自然人电子税务局导入模板")
async def export_tax_filing_template(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> StreamingResponse:
    content = await TaxRecordService.export_tax_filing_template_excel(db, year, month)
    filename = f"tax_filing_template_{year}{str(month).zfill(2)}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tax-records/deduction-summary/export", summary="导出专项附加扣除汇总表")
async def export_tax_deduction_summary(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> StreamingResponse:
    content = await TaxRecordService.export_special_deduction_summary_excel(db, year, month)
    filename = f"tax_deduction_summary_{year}{str(month).zfill(2)}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/tax/bonus", response_model=TaxRecordOut)
async def calculate_bonus_tax(
    data: BonusTaxRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> TaxRecordOut:
    """
    POST /payroll/tax/bonus

    计算年终奖个税（采用「全年一次性奖金」计税方式，不并入当月综合所得）。
    计算规则：奖金金额 ÷ 12 确定适用税率档次，再按全额计算税额。

    **请求体**（`BonusTaxRequest`）：
    - `employee_id`：员工ID
    - `year`：年度（影响累计预扣税额起点）
    - `month`：发放月份
    - `bonus_amount`：年终奖金额

    **响应**：`TaxRecordOut`，含计算得出的税额和实发金额

    **权限**：登录用户（`get_current_user`）

    **Service**：`TaxCalculationService.calculate_bonus_tax(db, data)`
    """
    record = await TaxCalculationService.calculate_bonus_tax(db, data)
    return TaxRecordOut.model_validate(record)


# ============================================================
# PaySlip 工资条
# ============================================================
# 工资条是发给员工的薪资通知单，包含当月所有薪资项目的明细。
# 生命周期：发送（sent）→ 员工查阅（viewed）→（有疑问）提交疑问（queried）
#           → HR 回复（replied）→ 员工确认（confirmed）/ 升级处理（escalated）
# 工资条通常在薪资发放后批量发送给所有员工。

@router.post("/pay-slips/send", response_model=list[PaySlipOut])
async def send_pay_slips(
    request: PaySlipSendRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> list[PaySlipOut]:
    """
    POST /payroll/pay-slips/send

    批量发送工资条。为指定年月的所有已发放薪资记录生成工资条并标记为已发送。
    通常在 salary-records/mark-paid 之后调用。

    **请求体**（`PaySlipSendRequest`）：
    - `year`：年度
    - `month`：月份，1~12
    - `employee_ids`（可选）：指定员工ID列表；不填则发送全部

    **响应**：`PaySlipOut[]`，本次生成/发送的所有工资条

    **权限**：登录用户（`get_current_user`）；建议限 hr/finance/admin 角色

    **Service**：`PaySlipService.send_batch(db, request)`
    """
    slips = await PaySlipService.send_batch(db, request)
    return [PaySlipOut.model_validate(s) for s in slips]


@router.get("/pay-slips/admin")
async def list_admin_pay_slips(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    department_id: Optional[int] = Query(default=None),
    send_status: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> list[dict]:
    return await PaySlipService.list_admin(db, year, month, department_id, send_status)


@router.get("/pay-slips/me", response_model=list[PaySlipOut])
async def list_my_pay_slips(
    year: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[PaySlipOut]:
    """
    GET /payroll/pay-slips/me

    查询当前登录员工的工资条列表（员工自助服务 ESS 入口）。

    **Query 参数**：
    - `year`（可选）：按年度筛选；不填则返回全部历史工资条

    **响应**：`PaySlipOut[]`，按月份倒序排列，含查阅状态和疑问状态

    **权限**：登录用户（`get_current_user`），仅返回本人数据

    **Service**：`PaySlipService.list_by_employee(db, current_user.id, year)`
    """
    slips = await PaySlipService.list_by_employee(db, current_user.id, year)
    return [PaySlipOut.model_validate(s) for s in slips]


@router.get("/pay-slips/{slip_id}", response_model=PaySlipOut)
async def get_pay_slip(
    slip_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PaySlipOut:
    """
    GET /payroll/pay-slips/{slip_id}

    查看工资条详情，并自动将状态标记为「已查阅」（viewed）。
    首次查阅会记录查阅时间，用于统计员工是否已阅读工资条。

    **Path 参数**：
    - `slip_id`：工资条ID

    **响应**：`PaySlipOut`，含所有薪资明细和当前疑问状态；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`PaySlipService.mark_viewed(db, slip_id)`
    """
    slip = await PaySlipService.mark_viewed(db, slip_id)
    if not slip:
        raise HTTPException(status_code=404, detail="工资条不存在")
    return PaySlipOut.model_validate(slip)


@router.post("/pay-slips/{slip_id}/query", response_model=PaySlipOut)
async def submit_pay_slip_query(
    slip_id: int,
    data: PaySlipQuerySubmit,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PaySlipOut:
    """
    POST /payroll/pay-slips/{slip_id}/query

    员工对工资条提交疑问（工资条状态变为 queried）。
    适用于员工发现工资金额有误时发起申诉。

    **Path 参数**：
    - `slip_id`：工资条ID

    **请求体**（`PaySlipQuerySubmit`）：
    - `query_content`：疑问描述内容

    **响应**：更新后的 `PaySlipOut`；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`PaySlipService.submit_query(db, slip_id, data)`
    """
    slip = await PaySlipService.submit_query(db, slip_id, data)
    if not slip:
        raise HTTPException(status_code=404, detail="工资条不存在")
    return PaySlipOut.model_validate(slip)


@router.post("/pay-slips/{slip_id}/reply", response_model=PaySlipOut)
async def reply_pay_slip_query(
    slip_id: int,
    data: PaySlipReply,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> PaySlipOut:
    """
    POST /payroll/pay-slips/{slip_id}/reply

    HR 回复员工的工资条疑问（工资条状态变为 replied）。
    只有处于 queried 状态的工资条才能回复（否则返回 400）。

    **Path 参数**：
    - `slip_id`：工资条ID

    **请求体**（`PaySlipReply`）：
    - `reply_content`：HR 的回复内容

    **响应**：更新后的 `PaySlipOut`；若状态不符返回 400，若不存在返回 404

    **权限**：登录用户（`get_current_user`）；建议限 hr/finance/admin 角色

    **Service**：`PaySlipService.reply_query(db, slip_id, data)`
    """
    try:
        slip = await PaySlipService.reply_query(db, slip_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not slip:
        raise HTTPException(status_code=404, detail="工资条不存在")
    return PaySlipOut.model_validate(slip)


@router.post("/pay-slips/{slip_id}/confirm", response_model=PaySlipOut)
async def confirm_pay_slip_query(
    slip_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PaySlipOut:
    """
    POST /payroll/pay-slips/{slip_id}/confirm

    员工确认接受 HR 的回复（工资条状态变为 confirmed，疑问流程结束）。
    只有处于 replied 状态的工资条才能确认（否则返回 400）。

    **Path 参数**：
    - `slip_id`：工资条ID

    **响应**：更新后的 `PaySlipOut`；若状态不符返回 400，若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`PaySlipService.confirm_query(db, slip_id)`
    """
    try:
        slip = await PaySlipService.confirm_query(db, slip_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not slip:
        raise HTTPException(status_code=404, detail="工资条不存在")
    return PaySlipOut.model_validate(slip)


@router.post("/pay-slips/{slip_id}/escalate", response_model=PaySlipOut)
async def escalate_pay_slip_query(
    slip_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PaySlipOut:
    """
    POST /payroll/pay-slips/{slip_id}/escalate

    员工对 HR 回复不满意时升级疑问（工资条状态变为 escalated），
    触发更高层级处理（如转交财务总监或劳资专员）。
    只有处于 replied 状态的工资条才能升级（否则返回 400）。

    **Path 参数**：
    - `slip_id`：工资条ID

    **响应**：更新后的 `PaySlipOut`；若状态不符返回 400，若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`PaySlipService.escalate_query(db, slip_id)`
    """
    try:
        slip = await PaySlipService.escalate_query(db, slip_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not slip:
        raise HTTPException(status_code=404, detail="工资条不存在")
    return PaySlipOut.model_validate(slip)


# ============================================================
# Dashboard 薪资看板
# ============================================================
# 薪资看板提供管理层视角的薪资成本数据，
# 支持单月快照查看和多月趋势分析，
# 是管理驾驶舱（Dashboard 模块）的薪酬数据数据源。

@router.get("/dashboard/monthly-summary", response_model=SalaryCostSummary)
async def get_monthly_salary_summary(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> SalaryCostSummary:
    """
    GET /payroll/dashboard/monthly-summary?year=2024&month=3

    查询指定月份的薪资成本汇总数据（管理驾驶舱用）。

    **Query 参数**：
    - `year`（必填）：年度
    - `month`（必填）：月份，1~12

    **响应**（`SalaryCostSummary`）：
    - `total_gross`：应发薪资总额
    - `total_net`：实发薪资总额
    - `total_tax`：个税总额
    - `total_social_insurance_company`：企业社保总额
    - `total_housing_fund_company`：企业公积金总额
    - `total_cost`：企业用人总成本（应发 + 企业社保 + 企业公积金）
    - `employee_count`：参与计薪的员工数

    **权限**：登录用户（`get_current_user`）

    **Service**：`PayrollDashboardService.get_monthly_summary(db, year, month)`
    """
    return await PayrollDashboardService.get_monthly_summary(db, year, month)


@router.get("/dashboard/cost-trend", response_model=SalaryCostTrend)
async def get_salary_cost_trend(
    year: int = Query(...),
    months: int = Query(default=12, ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> SalaryCostTrend:
    """
    GET /payroll/dashboard/cost-trend?year=2024&months=6

    查询指定年度的薪资成本月度趋势数据，用于折线图/柱状图展示。

    **Query 参数**：
    - `year`（必填）：年度
    - `months`：统计最近几个月，默认 12（整年），范围 1~12

    **响应**（`SalaryCostTrend`）：
    - `data`：按月份排列的成本数据列表，每项含月份、总成本、员工数等

    **权限**：登录用户（`get_current_user`）

    **Service**：`PayrollDashboardService.get_cost_trend(db, year, months)`
    """
    data = await PayrollDashboardService.get_cost_trend(db, year, months)
    return SalaryCostTrend(data=data)


# ============================================================
# Company 公司主体 CRUD
# ============================================================
# 本公司存在多主体用工场景（如北京总部、东莞工厂分别对应不同法人主体），
# 每个主体可有独立的纳税主体编号和银行账户，
# 薪资记录和社保记录均可关联到具体公司主体。

@router.get("/companies", response_model=list[CompanyOut])
async def list_companies(
    active_only: bool = Query(default=True, description="仅返回启用的公司"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[CompanyOut]:
    """
    GET /payroll/companies

    获取公司主体列表（支持过滤停用的主体）。

    **Query 参数**：
    - `active_only`：是否只返回启用的公司，默认 True

    **响应**：`CompanyOut[]`，含公司名称、纳税号、银行账号等

    **权限**：登录用户（`get_current_user`）

    **Service**：`CompanyService.list_all(db, active_only=active_only)`
    """
    objs = await CompanyService.list_all(db, active_only=active_only)
    return [CompanyOut.model_validate(o) for o in objs]


@router.post("/companies", response_model=CompanyOut, status_code=status.HTTP_201_CREATED)
async def create_company(
    data: CompanyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> CompanyOut:
    """
    POST /payroll/companies

    创建公司主体（如新设子公司时录入）。

    **请求体**（`CompanyCreate`）：
    - `name`：公司全称
    - `short_name`：简称
    - `tax_id`：纳税人识别号
    - `bank_account`：代发薪资银行账号
    - `bank_name`：开户行名称

    **响应**：201 Created + `CompanyOut`

    **权限**：登录用户（`get_current_user`）；建议限 admin 角色

    **Service**：`CompanyService.create(db, data)`
    """
    obj = await CompanyService.create(db, data)
    return CompanyOut.model_validate(obj)


@router.get("/companies/{company_id}", response_model=CompanyOut)
async def get_company(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> CompanyOut:
    """
    GET /payroll/companies/{company_id}

    查询公司主体详细信息。

    **Path 参数**：
    - `company_id`：公司ID

    **响应**：`CompanyOut`；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`CompanyService.get(db, company_id)`
    """
    obj = await CompanyService.get(db, company_id)
    if not obj:
        raise HTTPException(status_code=404, detail="公司不存在")
    return CompanyOut.model_validate(obj)


@router.put("/companies/{company_id}", response_model=CompanyOut)
async def update_company(
    company_id: int,
    data: CompanyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> CompanyOut:
    """
    PUT /payroll/companies/{company_id}

    更新公司主体信息（如变更银行账号或停用公司）。

    **Path 参数**：
    - `company_id`：公司ID

    **请求体**（`CompanyUpdate`）：可选字段，仅更新传入的字段

    **响应**：更新后的 `CompanyOut`；若不存在返回 404

    **权限**：登录用户（`get_current_user`）；建议限 admin 角色

    **Service**：`CompanyService.update(db, company_id, data)`
    """
    obj = await CompanyService.update(db, company_id, data)
    if not obj:
        raise HTTPException(status_code=404, detail="公司不存在")
    return CompanyOut.model_validate(obj)


@router.delete("/companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> None:
    """
    DELETE /payroll/companies/{company_id}

    删除公司主体。注意：有关联员工或薪资记录的公司通常不允许删除，
    应改为将 is_active 设为 False（通过 PUT 端点）。

    **Path 参数**：
    - `company_id`：公司ID

    **响应**：204 No Content；若不存在返回 404

    **权限**：登录用户（`get_current_user`）；建议限 admin 角色

    **Service**：`CompanyService.delete(db, company_id)`
    """
    success = await CompanyService.delete(db, company_id)
    if not success:
        raise HTTPException(status_code=404, detail="公司不存在")


# ============================================================
# TaxLawVersion 税法版本管理
# ============================================================
# 个人所得税法律法规会随政策调整而变化（如 2018 年大幅修订），
# 税法版本管理支持维护多套税率表，并为历史薪资记录保留计算时适用的税法版本，
# 确保历史数据的可审计性和可追溯性。

@router.get("/tax-law-versions", response_model=list[TaxLawVersionOut])
async def list_tax_law_versions(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TaxLawVersionOut]:
    """
    GET /payroll/tax-law-versions

    获取所有税法版本列表（含各版本的税率档次）。

    **响应**：`TaxLawVersionOut[]`，含：
    - `id`、`version_name`（如 "2019年税法"）
    - `effective_date`：生效日期
    - `end_date`：废止日期（None 表示当前版本）
    - `brackets`：税率档次列表

    **权限**：登录用户（`get_current_user`）

    **Service**：`TaxLawVersionService.list_all(db)`
    """
    objs = await TaxLawVersionService.list_all(db)
    return [TaxLawVersionOut.model_validate(o) for o in objs]


@router.post("/tax-law-versions", response_model=TaxLawVersionOut, status_code=status.HTTP_201_CREATED)
async def create_tax_law_version(
    data: TaxLawVersionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> TaxLawVersionOut:
    """
    POST /payroll/tax-law-versions

    创建新税法版本（可同时录入税率档次）。
    当个税政策调整时（如提高起征点、修改税率档次），创建新版本而非修改旧版本。

    **请求体**（`TaxLawVersionCreate`）：
    - `version_name`：版本名称，如 "2019年个税法"
    - `effective_date`：生效日期
    - `threshold`：费用扣除标准（起征点），如 5000
    - `brackets`（可选）：税率档次列表，含级次、下限、税率、速算扣除数

    **响应**：201 Created + `TaxLawVersionOut`

    **权限**：登录用户（`get_current_user`）；建议限 admin/finance 角色

    **Service**：`TaxLawVersionService.create(db, data)`
    """
    obj = await TaxLawVersionService.create(db, data)
    return TaxLawVersionOut.model_validate(obj)


@router.get("/tax-law-versions/{version_id}", response_model=TaxLawVersionOut)
async def get_tax_law_version(
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TaxLawVersionOut:
    """
    GET /payroll/tax-law-versions/{version_id}

    查询税法版本详细信息，包含该版本的完整税率档次列表。

    **Path 参数**：
    - `version_id`：税法版本ID

    **响应**：`TaxLawVersionOut`（含 brackets 列表）；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`TaxLawVersionService.get(db, version_id)`
    """
    obj = await TaxLawVersionService.get(db, version_id)
    if not obj:
        raise HTTPException(status_code=404, detail="税法版本不存在")
    return TaxLawVersionOut.model_validate(obj)


@router.put("/tax-law-versions/{version_id}", response_model=TaxLawVersionOut)
async def update_tax_law_version(
    version_id: int,
    data: TaxLawVersionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> TaxLawVersionOut:
    """
    PUT /payroll/tax-law-versions/{version_id}

    更新税法版本信息。典型用途：为旧版本设置 end_date 表示该版本已废止。

    **Path 参数**：
    - `version_id`：税法版本ID

    **请求体**（`TaxLawVersionUpdate`）：
    - `end_date`（可选）：废止日期，设置后该版本不再用于新计算
    - 其他可选字段

    **响应**：更新后的 `TaxLawVersionOut`；若不存在返回 404

    **权限**：登录用户（`get_current_user`）；建议限 admin/finance 角色

    **Service**：`TaxLawVersionService.update(db, version_id, data)`
    """
    obj = await TaxLawVersionService.update(db, version_id, data)
    if not obj:
        raise HTTPException(status_code=404, detail="税法版本不存在")
    return TaxLawVersionOut.model_validate(obj)


@router.post(
    "/tax-law-versions/{version_id}/brackets",
    response_model=TaxBracketOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_tax_bracket(
    version_id: int,
    data: TaxBracketCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> TaxBracketOut:
    """
    POST /payroll/tax-law-versions/{version_id}/brackets

    向指定税法版本添加税率档次（级次）。

    **Path 参数**：
    - `version_id`：税法版本ID

    **请求体**（`TaxBracketCreate`）：
    - `level`：级次，如 1~7
    - `lower_bound`：下限金额（累计预扣预缴应纳税所得额）
    - `upper_bound`（可选）：上限金额，最高档可为 None
    - `tax_rate`：税率，如 0.03 表示 3%
    - `quick_deduction`：速算扣除数

    **响应**：201 Created + `TaxBracketOut`；若版本不存在返回 404

    **权限**：登录用户（`get_current_user`）；建议限 admin/finance 角色

    **Service**：`TaxLawVersionService.add_bracket(db, version_id, data)`
    """
    version = await TaxLawVersionService.get(db, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="税法版本不存在")
    bracket = await TaxLawVersionService.add_bracket(db, version_id, data)
    return TaxBracketOut.model_validate(bracket)


@router.delete("/tax-law-versions/brackets/{bracket_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tax_bracket(
    bracket_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> None:
    """
    DELETE /payroll/tax-law-versions/brackets/{bracket_id}

    删除税率档次记录。
    注意：路由前缀为 /brackets/ 而非 /{version_id}/brackets/，
    可直接通过档次ID删除，无需提供版本ID。

    **Path 参数**：
    - `bracket_id`：税率档次ID

    **响应**：204 No Content；若不存在返回 404

    **权限**：登录用户（`get_current_user`）；建议限 admin/finance 角色

    **Service**：`TaxLawVersionService.delete_bracket(db, bracket_id)`
    """
    success = await TaxLawVersionService.delete_bracket(db, bracket_id)
    if not success:
        raise HTTPException(status_code=404, detail="税率条目不存在")


# ============================================================
# EmployeeTaxDeduction 员工专项附加扣除
# ============================================================
# 专项附加扣除是 2018 年个税改革新增内容，包括：
# 子女教育、继续教育、大病医疗、住房贷款利息、住房租金、赡养老人 6 项。
# 每项扣除有固定月扣除额，在计算当月个税时从预扣所得中抵扣，
# 需员工提交申报，HR 录入并关联到员工档案。

@router.get(
    "/employees/{employee_id}/tax-deductions",
    response_model=list[EmployeeTaxDeductionOut],
)
async def list_employee_tax_deductions(
    employee_id: int,
    year: Optional[int] = Query(default=None, description="按年份筛选"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[EmployeeTaxDeductionOut]:
    """
    GET /payroll/employees/{employee_id}/tax-deductions

    查询员工的专项附加扣除记录，可按年度筛选。

    **Path 参数**：
    - `employee_id`：员工ID

    **Query 参数**：
    - `year`（可选）：年度筛选；不填则返回全部记录

    **响应**：`EmployeeTaxDeductionOut[]`，含扣除类型、月扣除额、适用年度

    **权限**：登录用户（`get_current_user`）

    **Service**：`EmployeeTaxDeductionService.list_by_employee(db, employee_id, year)`
    """
    objs = await EmployeeTaxDeductionService.list_by_employee(db, employee_id, year)
    return [EmployeeTaxDeductionOut.model_validate(o) for o in objs]


@router.post(
    "/employees/{employee_id}/tax-deductions",
    response_model=EmployeeTaxDeductionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_employee_tax_deduction(
    employee_id: int,
    data: EmployeeTaxDeductionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> EmployeeTaxDeductionOut:
    """
    POST /payroll/employees/{employee_id}/tax-deductions

    为员工添加专项附加扣除项（如录入子女教育扣除 1000 元/月）。
    请求体中的 employee_id 必须与 path 中的 employee_id 一致（防止越权）。

    **Path 参数**：
    - `employee_id`：员工ID

    **请求体**（`EmployeeTaxDeductionCreate`）：
    - `employee_id`：员工ID（必须与 path 一致，否则返回 400）
    - `deduction_type`：扣除类型，如 "子女教育"、"住房贷款利息"
    - `monthly_amount`：月扣除额
    - `year`：适用年度
    - `remark`（可选）：备注

    **响应**：201 Created + `EmployeeTaxDeductionOut`；employee_id 不匹配返回 400

    **权限**：登录用户（`get_current_user`）

    **Service**：`EmployeeTaxDeductionService.create(db, data)`
    """
    if data.employee_id != employee_id:
        raise HTTPException(status_code=400, detail="employee_id 不匹配")
    obj = await EmployeeTaxDeductionService.create(db, data)
    return EmployeeTaxDeductionOut.model_validate(obj)


@router.post("/tax-deductions/import", summary="批量导入专项附加扣除")
async def import_employee_tax_deductions(
    file: UploadFile = File(..., description="专项附加扣除文件（.xlsx/.xls/.csv）"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> dict:
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上传文件不能为空")
    return await EmployeeTaxDeductionService.import_file(
        db,
        file_bytes=file_bytes,
        filename=file.filename or "",
    )


@router.put(
    "/tax-deductions/{deduction_id}",
    response_model=EmployeeTaxDeductionOut,
)
async def update_employee_tax_deduction(
    deduction_id: int,
    data: EmployeeTaxDeductionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> EmployeeTaxDeductionOut:
    """
    PUT /payroll/tax-deductions/{deduction_id}

    更新员工专项附加扣除记录（如修改月扣除金额或停止某项扣除）。

    **Path 参数**：
    - `deduction_id`：专项附加扣除记录ID

    **请求体**（`EmployeeTaxDeductionUpdate`）：可选字段，仅更新传入的字段

    **响应**：更新后的 `EmployeeTaxDeductionOut`；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`EmployeeTaxDeductionService.update(db, deduction_id, data)`
    """
    obj = await EmployeeTaxDeductionService.update(db, deduction_id, data)
    if not obj:
        raise HTTPException(status_code=404, detail="专项附加扣除不存在")
    return EmployeeTaxDeductionOut.model_validate(obj)


@router.delete("/tax-deductions/{deduction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_employee_tax_deduction(
    deduction_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> None:
    """
    DELETE /payroll/tax-deductions/{deduction_id}

    删除员工专项附加扣除记录。

    **Path 参数**：
    - `deduction_id`：专项附加扣除记录ID

    **响应**：204 No Content；若不存在返回 404

    **权限**：登录用户（`get_current_user`）

    **Service**：`EmployeeTaxDeductionService.delete(db, deduction_id)`
    """
    success = await EmployeeTaxDeductionService.delete(db, deduction_id)
    if not success:
        raise HTTPException(status_code=404, detail="专项附加扣除不存在")


# ============================================================
# BonusRecord 奖金管理 (年终奖/项目奖金/绩效奖金)
# 注意：/bonus/export 和 /bonus/bulk 固定路径端点必须在 /bonus/{bonus_id} 之前注册，
# 否则 FastAPI 会将 "export" 和 "bulk" 误解析为 bonus_id（整数），导致 422 错误。
# ============================================================
# 奖金管理独立于月薪体系之外，支持三类奖金：
# - 年终奖（annual_bonus）：按「全年一次性奖金」方式计税
# - 项目奖金（project_bonus）：项目完成后发放
# - 绩效奖金（performance_bonus）：季度/半年度绩效考核结果挂钩
# 奖金生命周期：draft（草稿）→ confirmed（已确认发放）


def _bonus_record_to_out(record) -> BonusRecordOut:
    emp = record.employee
    tax_method, notes = BonusService.parse_bonus_metadata(record.notes, record.bonus_type)
    out = BonusRecordOut.model_validate(record)
    out.tax_method = tax_method
    out.notes = notes or None
    if emp:
        out.employee_name = emp.name
        out.employee_no = emp.employee_no
    return out

@router.get("/bonus/export", summary="导出奖金明细Excel")
async def export_bonus_excel(
    year: int = Query(..., ge=2019, le=2100),
    bonus_type: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> StreamingResponse:
    """
    GET /payroll/bonus/export?year=2024

    导出指定年度的奖金明细为 Excel 文件（.xlsx 格式），
    用于财务报表归档或税务申报附件。

    **Query 参数**：
    - `year`（必填）：年度，范围 2019~2100
    - `bonus_type`（可选）：奖金类型筛选，可选值：
      - `annual_bonus`：年终奖
      - `project_bonus`：项目奖金
      - `performance_bonus`：绩效奖金

    **响应**：
    - Content-Type: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
    - Content-Disposition: `attachment; filename="bonus_{year}.xlsx"`
    - 二进制流（Excel 文件内容）

    **权限**：登录用户（`get_current_user`）；建议限 finance/admin 角色

    **Service**：`BonusService.export_bonus_excel(db, year, bonus_type)`
    """
    content = await BonusService.export_bonus_excel(db, year, bonus_type)
    filename = f"bonus_{year}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/bonus", response_model=dict, summary="查询奖金记录列表")
async def list_bonus(
    year: int = Query(..., ge=2019, le=2100),
    bonus_type: Optional[str] = Query(default=None),
    record_status: Optional[str] = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> dict:
    """
    GET /payroll/bonus?year=2024

    分页查询年度奖金记录列表，支持按类型和状态筛选。
    返回数据已关联员工姓名和工号，可直接用于前端表格展示。

    **Query 参数**：
    - `year`（必填）：年度
    - `bonus_type`（可选）：奖金类型（annual_bonus / project_bonus / performance_bonus）
    - `status`（可选）：状态（draft / confirmed）
    - `skip`：分页偏移量
    - `limit`：每页条数，默认 50，最大 200

    **响应**：
    ```json
    {
      "items": [BonusRecordOut 数组，含 employee_name 和 employee_no],
      "total": 总条数,
      "skip": 偏移量,
      "limit": 每页条数
    }
    ```

    **权限**：登录用户（`get_current_user`）

    **Service**：`BonusService.list_bonus(db, year, ...)`
    """
    records, total = await BonusService.list_bonus(
        db, year, bonus_type=bonus_type, status=record_status, skip=skip, limit=limit
    )
    items = [_bonus_record_to_out(record) for record in records]
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.post("/bonus", response_model=BonusRecordOut, status_code=status.HTTP_201_CREATED, summary="创建奖金记录")
async def create_bonus(
    data: BonusRecordCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> BonusRecordOut:
    """
    POST /payroll/bonus

    创建单条奖金记录。创建时系统自动根据奖金类型计算税额（年终奖特殊计税）。
    初始状态为 draft，需调用 /confirm 接口确认发放。

    **请求体**（`BonusRecordCreate`）：
    - `employee_id`：员工ID
    - `year`：年度
    - `month`：发放月份
    - `bonus_type`：奖金类型
    - `amount`：奖金金额
    - `remark`（可选）：备注

    **响应**：201 Created + `BonusRecordOut`（含 employee_name、employee_no、tax_amount）

    **权限**：登录用户（`get_current_user`）；建议限 hr/finance/admin 角色

    **Service**：`BonusService.create_bonus(db, data)`
    """
    record = await BonusService.create_bonus(db, data)
    return _bonus_record_to_out(record)


@router.post("/bonus/bulk", response_model=list[BonusRecordOut], status_code=status.HTTP_201_CREATED, summary="批量录入奖金")
async def bulk_create_bonus(
    data: BulkBonusCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> list[BonusRecordOut]:
    """
    POST /payroll/bonus/bulk

    批量创建奖金记录（一次性录入多名员工的奖金数据）。
    适用于年终奖发放季，从 Excel 导入数据后批量写入。

    **请求体**（`BulkBonusCreate`）：
    - `year`：年度
    - `month`：发放月份
    - `bonus_type`：奖金类型（批量中所有记录使用相同类型）
    - `items`：奖金明细列表，每项含 employee_id 和 amount

    **响应**：201 Created + `BonusRecordOut[]`，含员工姓名和工号

    **权限**：登录用户（`get_current_user`）；建议限 hr/finance/admin 角色

    **Service**：`BonusService.bulk_create_bonus(db, data)`
    """
    records = await BonusService.bulk_create_bonus(db, data)
    return [_bonus_record_to_out(record) for record in records]


@router.post("/bonus/{bonus_id}/confirm", response_model=BonusRecordOut, summary="确认奖金发放")
async def confirm_bonus(
    bonus_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> BonusRecordOut:
    """
    POST /payroll/bonus/{bonus_id}/confirm

    确认奖金发放（状态从 draft 变为 confirmed，锁定数据防止再次修改）。
    只有 draft 状态的奖金记录可以确认（否则返回 400）。

    **Path 参数**：
    - `bonus_id`：奖金记录ID

    **响应**：确认后的 `BonusRecordOut`；若状态不符返回 400，若不存在返回 404

    **权限**：登录用户（`get_current_user`）；建议限 finance/admin 角色

    **Service**：`BonusService.confirm_bonus(db, bonus_id)`
    """
    try:
        record = await BonusService.confirm_bonus(db, bonus_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not record:
        raise HTTPException(status_code=404, detail="奖金记录不存在")
    return _bonus_record_to_out(record)


# ============================================================
# BankPaymentFile 银行代发文件
# ============================================================
# 银行代发是将已审批的薪资通过银行批量转账给员工的流程。
# 不同银行要求不同格式：
# - 工行（icbc）：CSV 格式
# - 建行（ccb）：TXT 格式（固定宽度）
# - 招行（cmb）：CSV 格式（字段顺序不同）
# 生成的文件直接上传到对应银行的企业网银即可完成代发。

@router.get("/bank-payment-file/preview", summary="预览银行代发批次")
async def preview_bank_payment_file(
    year: int = Query(..., ge=2019, le=2100),
    month: int = Query(..., ge=1, le=12),
    bank_type: str = Query(default="icbc", description="银行类型: icbc/ccb/cmb/abc"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> dict:
    try:
        return await BankPaymentService.preview_bank_payment(db, year, month, bank_type=bank_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/bank-payment-file/template", summary="下载银行代发模板")
async def download_bank_payment_template(
    bank_type: str = Query(default="icbc", description="银行类型: icbc/ccb/cmb/abc"),
    _current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> StreamingResponse:
    try:
        content, filename = BankPaymentService.generate_template(bank_type=bank_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    media_type = "text/plain" if bank_type == "ccb" else "text/csv"
    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/bank-payment-file", summary="下载银行代发文件")
async def download_bank_payment_file(
    year: int = Query(..., ge=2019, le=2100),
    month: int = Query(..., ge=1, le=12),
    bank_type: str = Query(default="icbc", description="银行类型: icbc/ccb/cmb/abc"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> StreamingResponse:
    """
    GET /payroll/bank-payment-file?year=2024&month=3&bank_type=icbc

    生成并下载银行代发文件，用于通过企业网银批量转账发薪。
    文件中包含所有已审批（approved）状态薪资记录的实发金额和银行账号。

    **Query 参数**：
    - `year`（必填）：年度
    - `month`（必填）：月份，1~12
    - `bank_type`：银行类型，可选值：
      - `icbc`（默认）：工商银行，CSV 格式
      - `ccb`：建设银行，TXT 格式
      - `cmb`：招商银行，CSV 格式

    **响应**：
    - 工行/招行：Content-Type `text/csv`
    - 建行：Content-Type `text/plain`
    - Content-Disposition: `attachment; filename="..."`
    - 若该月无可代发记录（无 approved 薪资），返回 400

    **权限**：登录用户（`get_current_user`）；建议限 finance/admin 角色

    **Service**：`BankPaymentService.generate_bank_payment_file(db, year, month, bank_type=bank_type)`
    """
    try:
        content, filename = await BankPaymentService.generate_bank_payment_file(
            db, year, month, bank_type=bank_type
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 建行使用 TXT 格式，其他银行使用 CSV 格式
    if bank_type == "ccb":
        media_type = "text/plain"
    else:
        media_type = "text/csv"

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================
# Cost Analysis 薪资成本分析
# ============================================================
# 薪资成本分析从管理视角汇总薪资数据，
# 支持按部门（横向对比各部门用人成本）和按月份（纵向分析全年成本走势）两种维度，
# 是管理驾驶舱中人力成本模块的核心数据接口。

@router.get("/cost-analysis", response_model=CostAnalysisResult, summary="薪资成本分析")
async def get_cost_analysis(
    year: int = Query(..., ge=2019, le=2100),
    group_by: str = Query(default="department", description="分组维度: department/month"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "finance")),
) -> CostAnalysisResult:
    """
    GET /payroll/cost-analysis?year=2024&group_by=department

    薪资成本分析，支持按部门或按月份汇总，用于管理层决策和预算对比。

    **Query 参数**：
    - `year`（必填）：年度，范围 2019~2100
    - `group_by`：分组维度，可选值：
      - `department`（默认）：按部门汇总，可对比各部门人均成本和总成本
      - `month`：按月份汇总，展示全年成本走势

    **响应**（`CostAnalysisResult`）：
    - `year`：统计年度
    - `group_by`：分组维度
    - `items`：分组汇总列表，每项含：
      - `label`：组标签（部门名称或月份）
      - `employee_count`：员工数
      - `total_gross`：应发总额
      - `total_net`：实发总额
      - `total_cost`：用人总成本（含企业社保和公积金）
      - `avg_cost`：人均用人成本

    **错误响应**：
    - 400：`group_by` 参数值非法（非 department/month）

    **权限**：登录用户（`get_current_user`）；建议限 finance/admin/hr 角色

    **Service**：`CostAnalysisService.get_cost_analysis(db, year, group_by=group_by)`
    """
    try:
        return await CostAnalysisService.get_cost_analysis(db, year, group_by=group_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
