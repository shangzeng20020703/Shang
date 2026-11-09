"""
员工自助服务模块 API 路由 — ESS (Employee Self-Service)
=========================================================

本模块负责售后管理系统中「员工自助服务」相关的所有 HTTP 接口，挂载前缀为
``/api/v1/ess``（由 router.py 注册时指定）。

核心设计原则
------------
**ESS 接口一律不接受 employee_id 参数**。所有接口通过 ``Depends(get_current_user)``
获取当前登录用户，只操作登录员工自己的数据，实现数据隔离。

这与 HR 管理类接口（如 GET /employees/{employee_id}）的根本区别：
- HR 接口：管理员视角，可指定任意 employee_id 查看任意员工数据
- ESS 接口：员工视角，只能查看/修改自己的数据，无需传 employee_id

功能分区
--------
1. **个人档案（Profile）**
   - GET  /ess/profile — 查看自己的完整员工档案
   - PUT  /ess/profile — 修改允许自助更新的字段（受限字段集）
     允许修改：邮箱、现居住地址、紧急联系人、紧急联系电话、银行名称、银行账号
     不允许修改：姓名、工号、部门、岗位、薪资（需 HR 通过正式流程变更）

2. **工资条（Payslip）**
   - GET /ess/payslips — 查看自己历史工资条列表
     只能查看已发放（finalized / approved）状态的薪资记录，未发放的无权查看

3. **假期管理（Leave）**
   - GET /ess/leave/balance   — 查看自己各假期类型的余额
   - GET /ess/leave/requests  — 查看自己的请假历史记录
   - POST /ess/leave/request  — 提交请假申请（进入审批流程）

内联 Schema 说明
----------------
本模块在路由文件中内联定义了两个请求体 Schema（未放 schemas/ 目录），
因为这两个 Schema 逻辑简单且仅 ESS 使用：
- ProfileUpdateRequest  : 限制可修改字段的更新请求体
- LeaveSubmitRequest    : 请假申请请求体

权限说明
--------
- 所有接口均需登录（``Depends(get_current_user)``），无 JWT 返回 401。
- 无需特殊角色，所有在职员工均可使用（is_active 检查由 get_current_user 负责）。

服务层对应
----------
- ``app.services.ess``（模块级函数，非类，以 ess_svc 别名导入）
  - get_my_payslips(db, employee_id, year, skip, limit)
  - get_my_leave_balance(db, employee_id, year)
  - get_my_leave_requests(db, employee_id, skip, limit)
  - submit_leave_request(db, employee_id, leave_type_id, start_date, end_date, reason)
  - get_my_profile(db, employee_id)
  - update_my_profile(db, employee_id, data: dict)
"""

from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.approval import ApprovalInstance
from app.models.employee import Employee
from app.models.leave import LeaveType
from app.schemas.payroll import SalaryRecordOut
from app.schemas.leave import LeaveBalanceOut, LeaveRequestOut
from app.schemas.employee import EmployeeOut
from app.schemas.admin import AssetCheckoutOut
from app.services import ess as ess_svc

router = APIRouter()


# ---------------------------------------------------------------------------
# 内联请求体 Schema 定义
# ---------------------------------------------------------------------------

class ProfileUpdateRequest(BaseModel):
    """
    员工自助更新个人档案的请求体。

    字段说明（均为可选，只传需修改的字段）
    ----------------------------------------
    - email             : str — 员工个人邮箱
    - current_address   : str — 现居住地址（非户籍地址）
    - emergency_contact : str — 紧急联系人姓名
    - emergency_phone   : str — 紧急联系人电话
    - bank_name         : str — 开户银行名称（用于工资发放）
    - bank_account      : str — 银行卡号（敏感字段，传输时应走 HTTPS）

    设计说明
    --------
    此 Schema 仅包含员工可自助修改的字段，姓名/工号/部门/岗位/薪资
    等敏感字段不在此列，需通过 HR 正式变更流程操作。
    """
    email: Optional[str] = None
    current_address: Optional[str] = None
    emergency_contact: Optional[str] = None
    emergency_phone: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account: Optional[str] = None


class LeaveSubmitRequest(BaseModel):
    """
    员工提交请假申请的请求体。

    字段说明
    --------
    - leave_type_id : int  — 假期类型 ID（对应 LeaveType 表，如年假=1/病假=2/事假=3）
    - start_date    : date — 请假开始日期（含）
    - end_date      : date — 请假结束日期（含）
    - reason        : str  — 请假原因，默认为空字符串
    """
    leave_type_id: int
    start_date: date
    end_date: date
    reason: str = ""


def _asset_checkout_out(record) -> AssetCheckoutOut:
    out = AssetCheckoutOut.model_validate(record)
    out.asset_no = record.asset.asset_no if record.asset else None
    out.asset_name = record.asset.name if record.asset else None
    out.usage_department = record.asset.usage_department if record.asset else None
    out.employee_name = record.employee.name if record.employee else None
    out.employee_no = record.employee.employee_no if record.employee else None
    out.department_name = getattr(record.employee, "department_name", None) if record.employee else None
    return out


def _salary_record_out(record, si_record=None) -> SalaryRecordOut:
    out = SalaryRecordOut.model_validate(record)
    if si_record:
        detail = []
        if si_record.pension_employee and float(si_record.pension_employee) > 0:
            detail.append({"label": "养老保险", "amount": si_record.pension_employee})
        if si_record.medical_employee and float(si_record.medical_employee) > 0:
            detail.append({"label": "医疗保险", "amount": si_record.medical_employee})
        if si_record.unemployment_employee and float(si_record.unemployment_employee) > 0:
            detail.append({"label": "失业保险", "amount": si_record.unemployment_employee})
        out.social_insurance_detail = detail
    else:
        out.social_insurance_detail = []
    return out


# ---------------------------------------------------------------------------
# 工资条接口
# ---------------------------------------------------------------------------

@router.get("/payslips", response_model=List[SalaryRecordOut], summary="我的工资条")
async def my_payslips(
    year: Optional[int] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=12, ge=1, le=120),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /ess/payslips — 查看自己的工资条列表

    用途
    ----
    员工查看自己历史薪资发放记录（工资条）。出于安全考虑，只返回
    已审批（approved）或已发放（finalized）状态的薪资记录，
    草稿或审批中的薪资记录员工无权查看。

    ESS 数据隔离
    ------------
    使用 current_user.id 作为 employee_id 过滤，员工只能查看自己的工资条，
    不接受外部传入的 employee_id 参数。

    查询参数
    --------
    - year  : int (可选) — 按薪资年份过滤，不传则返回全部历史
    - skip  : int        — 分页偏移量，默认0（用于无限滚动场景）
    - limit : int        — 每页条数，默认12（按月查询时12条=1年），最大120

    响应 (200 OK)
    -------------
    list[SalaryRecordOut] — 工资条列表，按发薪月份降序排列（最近的在前），
    每条包含工资月份、基本工资、各项加项/减项、实发金额等

    权限
    ----
    已登录用户（get_current_user），操作当前用户自己的数据

    对应服务
    --------
    ess_svc.get_my_payslips(db, current_user.id, year, skip, limit)
    """
    records = await ess_svc.get_my_payslips(
        db, current_user.id, year=year, skip=skip, limit=limit
    )
    periods = [(record.year, record.month) for record in records]
    si_map = await ess_svc.get_my_social_insurance_map(db, current_user.id, periods)
    return [_salary_record_out(record, si_map.get((record.year, record.month))) for record in records]


# ---------------------------------------------------------------------------
# 假期接口
# ---------------------------------------------------------------------------

@router.get("/leave/balance", response_model=List[LeaveBalanceOut], summary="我的假期余额")
async def my_leave_balance(
    year: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /ess/leave/balance — 查看自己的假期余额

    用途
    ----
    员工查看当前各假期类型（年假/病假/调休等）的余额情况：
    已分配天数、已使用天数、剩余可用天数。

    ESS 数据隔离
    ------------
    使用 current_user.id 过滤，只返回当前登录员工的假期余额。

    查询参数
    --------
    - year : int (可选) — 按年份查询假期余额，不传则默认为当前年份
                          （服务层自动取 datetime.now().year）

    响应 (200 OK)
    -------------
    list[LeaveBalanceOut] — 各假期类型余额列表，每条包含：
      - leave_type_name : str   — 假期类型名称（如"年假"）
      - entitled_days   : float — 年度分配天数
      - used_days       : float — 已使用天数
      - remaining_days  : float — 剩余天数（entitled - used）
      - carry_over_days : float — 上年度结转天数（如有）

    权限
    ----
    已登录用户（get_current_user），操作当前用户自己的数据

    对应服务
    --------
    ess_svc.get_my_leave_balance(db, current_user.id, year)
    """
    if not year:
        # 未指定年份时默认取当前年份
        year = datetime.now(timezone.utc).year
    from app.services.attendance import AttendanceRecordService

    await AttendanceRecordService.sync_comp_time_balances_from_attendance(
        db,
        year=year,
        employee_ids=[current_user.id],
        allow_decrease=True,
        recalculate_records=True,
    )
    records = await ess_svc.get_my_leave_balance(db, current_user.id, year)
    return [LeaveBalanceOut.model_validate(r) for r in records]


@router.get("/leave/requests", response_model=List[LeaveRequestOut], summary="我的请假记录")
async def my_leave_requests(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /ess/leave/requests — 查看自己的请假历史记录

    用途
    ----
    员工查看自己提交的所有请假申请，包含各种状态（待审批、已批准、已拒绝）。
    用于员工自助跟踪请假申请进度。

    ESS 数据隔离
    ------------
    使用 current_user.id 过滤，员工只能看到自己提交的请假申请。

    查询参数
    --------
    - skip  : int — 分页偏移量，默认0
    - limit : int — 每页条数，默认20，最大100

    响应 (200 OK)
    -------------
    list[LeaveRequestOut] — 请假记录列表（按申请时间降序），每条包含：
      - leave_type_name    : str  — 假期类型名称
      - start_date         : date — 请假开始日期
      - end_date           : date — 请假结束日期
      - days               : float — 请假天数
      - reason             : str  — 请假原因
      - approval_status    : str  — 审批状态（pending/approved/rejected）
      - created_at         : datetime — 申请提交时间

    权限
    ----
    已登录用户（get_current_user），操作当前用户自己的数据

    对应服务
    --------
    ess_svc.get_my_leave_requests(db, current_user.id, skip, limit)
    """
    records = await ess_svc.get_my_leave_requests(db, current_user.id, skip=skip, limit=limit)
    if not records:
        return []

    request_ids = [record.id for record in records]
    leave_type_ids = {record.leave_type_id for record in records}

    type_result = await db.execute(
        select(LeaveType.id, LeaveType.name).where(LeaveType.id.in_(leave_type_ids))
    )
    leave_type_names = {row.id: row.name for row in type_result.all()}

    instance_result = await db.execute(
        select(ApprovalInstance)
        .options(selectinload(ApprovalInstance.records))
        .where(
            ApprovalInstance.module == "leave",
            ApprovalInstance.business_type == "leave_request",
            ApprovalInstance.business_id.in_(request_ids),
        )
    )
    instances = {
        instance.business_id: instance
        for instance in instance_result.scalars().unique().all()
    }

    approver_ids = {
        approval_record.approver_id
        for instance in instances.values()
        for approval_record in instance.records
    }
    approver_names = {}
    if approver_ids:
        approver_result = await db.execute(
            select(Employee.id, Employee.name, Employee.employee_no).where(Employee.id.in_(approver_ids))
        )
        approver_names = {
            row.id: {
                "name": row.name,
                "employee_no": row.employee_no,
            }
            for row in approver_result.all()
        }

    response = []
    for record in records:
        item = LeaveRequestOut.model_validate(record)
        item.leave_type_name = leave_type_names.get(record.leave_type_id)

        instance = instances.get(record.id)
        if instance:
            item.approval_instance_id = instance.id
            item.approval_instance_status = instance.status
            item.approval_records = [
                {
                    "id": approval_record.id,
                    "node_order": approval_record.node_order,
                    "approver_id": approval_record.approver_id,
                    "approver_name": approver_names.get(approval_record.approver_id, {}).get("name"),
                    "approver_no": approver_names.get(approval_record.approver_id, {}).get("employee_no"),
                    "action": approval_record.action,
                    "comment": approval_record.comment,
                    "acted_at": approval_record.acted_at,
                    "created_at": approval_record.created_at,
                }
                for approval_record in instance.records
            ]
        response.append(item)

    return response


@router.get("/assets", response_model=List[AssetCheckoutOut], summary="我的资产")
async def my_assets(
    active_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    records = await ess_svc.get_my_asset_checkouts(
        db,
        current_user.id,
        active_only=active_only,
    )
    return [_asset_checkout_out(item) for item in records]


@router.post("/leave/request", response_model=LeaveRequestOut, summary="提交请假申请")
async def submit_leave(
    data: LeaveSubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /ess/leave/request — 员工自助提交请假申请

    用途
    ----
    员工自助发起请假申请，申请提交后进入审批流程（由主管或 HR 审批）。
    服务层会自动校验：
    - 假期余额是否充足（不足则拒绝）
    - 日期合理性（end_date >= start_date）
    - 是否存在重叠的请假申请

    ESS 数据隔离
    ------------
    employee_id 取自 current_user.id，员工只能为自己提交申请，
    不接受外部传入其他员工的 ID。

    请求体 (LeaveSubmitRequest)
    ---------------------------
    - leave_type_id : int  — 假期类型 ID（对应 LeaveType 表）
    - start_date    : date — 请假开始日期（格式：YYYY-MM-DD）
    - end_date      : date — 请假结束日期（格式：YYYY-MM-DD）
    - reason        : str  — 请假原因（可不填，默认空字符串）

    响应 (200 OK)
    -------------
    服务层返回值（具体结构由 ess_svc.submit_leave_request 决定），通常包含：
      - request_id      : int  — 新创建的请假申请 ID
      - approval_status : str  — "pending"（已提交，待审批）
      - days            : float — 系统计算的请假天数

    注意
    ----
    此接口在服务层调用后显式执行 db.commit()（与其他接口不同）。
    原因：请假申请创建后需立即持久化，以便触发审批流通知。

    权限
    ----
    已登录用户（get_current_user），操作当前用户自己的数据

    对应服务
    --------
    ess_svc.submit_leave_request(db, current_user.id, leave_type_id, start_date, end_date, reason)
    """
    result = await ess_svc.submit_leave_request(
        db, current_user.id,
        data.leave_type_id, data.start_date, data.end_date, data.reason
    )
    return LeaveRequestOut.model_validate(result)


# ---------------------------------------------------------------------------
# 个人档案接口
# ---------------------------------------------------------------------------

@router.get("/profile", response_model=EmployeeOut, summary="我的档案")
async def my_profile(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /ess/profile — 查看自己的个人信息档案

    用途
    ----
    员工查看自己的完整个人档案信息，包括基本信息、工作信息、联系方式等。
    返回与 HR 管理端 GET /employees/{id} 相同的 EmployeeOut Schema，
    但数据来源限制为当前登录用户。

    ESS 数据隔离
    ------------
    直接使用 current_user.id，不暴露 employee_id 参数，
    员工只能查看自己的档案。

    响应 (200 OK)
    -------------
    EmployeeOut — 员工完整档案，包含：
      - 基本信息：姓名、工号、性别、出生日期
      - 工作信息：部门、岗位、入职日期、工作地点
      - 联系方式：邮箱、手机号、现居地址
      - 紧急联系人信息
      - 银行信息（脱敏后的卡号）

    权限
    ----
    已登录用户（get_current_user），操作当前用户自己的数据

    对应服务
    --------
    ess_svc.get_my_profile(db, current_user.id)
    """
    emp = await ess_svc.get_my_profile(db, current_user.id)
    return EmployeeOut.model_validate(emp)


@router.put("/profile", response_model=EmployeeOut, summary="更新我的档案")
async def update_profile(
    data: ProfileUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    PUT /ess/profile — 员工自助更新个人档案（受限字段）

    用途
    ----
    员工更新自己档案中允许自助修改的字段。系统采用白名单机制，
    只有 ProfileUpdateRequest 中声明的字段才能被修改，其他字段
    （如姓名、工号、部门、薪资）即使传入也会被忽略。

    ESS 数据隔离
    ------------
    使用 current_user.id 确定更新目标，员工不能修改他人档案。

    可修改字段（白名单）
    --------------------
    - email             — 个人邮箱
    - current_address   — 现居住地址
    - emergency_contact — 紧急联系人姓名
    - emergency_phone   — 紧急联系人电话
    - bank_name         — 开户银行名称
    - bank_account      — 银行卡号

    请求体 (ProfileUpdateRequest)
    ------------------------------
    所有字段可选，使用 model_dump(exclude_none=True) 确保只更新传入的字段，
    未传入的字段保持原值不变。

    响应 (200 OK)
    -------------
    EmployeeOut — 更新后的完整员工档案

    注意
    ----
    此接口显式执行 db.commit()，确保更新立即持久化。

    权限
    ----
    已登录用户（get_current_user），操作当前用户自己的数据

    对应服务
    --------
    ess_svc.update_my_profile(db, current_user.id, data.model_dump(exclude_none=True))
    """
    emp = await ess_svc.update_my_profile(
        db, current_user.id, data.model_dump(exclude_none=True)
    )
    return EmployeeOut.model_validate(emp)
