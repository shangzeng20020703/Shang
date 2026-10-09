"""
员工自助服务 (ESS, Employee Self Service) — 业务逻辑层

架构位置：
    API 层 (api/v1/ess.py) → 本文件（独立函数，无 Service 类包装）
    本文件 → app.models.employee / app.models.payroll / app.models.leave
           → app.services.leave (LeaveRequestService，复用请假申请逻辑)

业务职责：
    ESS 是员工面向自身信息的"只读/受限写"入口，与 HR/管理员视角的 employee 端点不同：
    - 员工只能查询自己的工资条（仅已审批/已发放状态）
    - 员工只能查询自己的假期余额和请假记录
    - 员工可以自行提交请假申请（复用 LeaveRequestService.create）
    - 员工可以查看和修改自己的个人档案，但仅限受限字段（邮箱/地址/紧急联系人/银行账号）

ESS vs 管理员员工接口的关键区别：
    - ESS 端点不暴露 employee_id 路径参数，统一使用 JWT 中的 current_user.id 操作自身数据
    - update_my_profile 有白名单限制（ALLOWED_FIELDS），禁止员工修改姓名/工号/状态等敏感字段
    - 工资条只返回已通过审批或已发放的记录，草稿/待审批记录不可见

依赖关系：
    - app.models.payroll: SalaryRecord（工资条，延迟导入避免循环）
    - app.models.leave: LeaveBalance, LeaveRequest（延迟导入）
    - app.models.employee: Employee（个人档案）
    - app.services.leave: LeaveRequestService.create（复用请假申请逻辑）
    - app.schemas.leave: LeaveRequestCreate（请假申请 schema）

被哪些 API 端点调用（所有端点均不含 employee_id 路径参数，操作当前登录用户）：
    - GET  /api/v1/ess/payslips             → get_my_payslips
    - GET  /api/v1/ess/leave-balance        → get_my_leave_balance
    - GET  /api/v1/ess/leave-requests       → get_my_leave_requests
    - POST /api/v1/ess/leave-requests       → submit_leave_request
    - GET  /api/v1/ess/profile              → get_my_profile
    - PUT  /api/v1/ess/profile              → update_my_profile

架构约定：
    - 所有函数以 db: AsyncSession 为第一参数，第二参数为 employee_id（由 get_current_user 注入）
    - 不自行 commit（由 get_db() 统一 commit）
    - 业务校验失败抛出 HTTPException（400/404）
    - 延迟导入（函数内 import）避免循环依赖
"""
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


async def get_my_payslips(
    db: AsyncSession,
    employee_id: int,
    year: Optional[int] = None,
    skip: int = 0,
    limit: int = 12,
) -> list:
    """
    获取当前员工自己的工资条列表。

    安全限制：
    - 仅返回状态为 "approved"（已审批）或 "paid"（已发放）的工资记录
    - 草稿（draft）和待审批（pending）状态的工资记录对员工不可见

    排序：按年份降序、月份降序，最新工资条在前。

    参数：
        db          - 异步数据库会话
        employee_id - 当前登录员工 ID（来自 JWT，由 API 层注入）
        year        - 可选年份过滤（如 2024），不传则查所有年份
        skip        - 分页偏移量（默认 0）
        limit       - 每次返回条数（默认 12，即一年份）

    返回：
        SalaryRecord 列表（已过滤为仅可见状态）
    """
    from app.models.payroll import SalaryRecord  # 延迟导入，避免循环依赖
    stmt = select(SalaryRecord).where(
        SalaryRecord.employee_id == employee_id,
        # 只返回已审批或已发放的工资条，保护薪酬数据安全
        SalaryRecord.status.in_(["approved", "paid"]),
    )
    if year:
        # 按年份精确过滤
        stmt = stmt.where(SalaryRecord.year == year)
    # 按时间降序排列，最新的工资条最先返回
    stmt = stmt.order_by(SalaryRecord.year.desc(), SalaryRecord.month.desc())
    stmt = stmt.offset(skip).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_my_social_insurance_map(
    db: AsyncSession,
    employee_id: int,
    periods: list[tuple[int, int]],
) -> dict[tuple[int, int], object]:
    """
    获取当前员工工资条涉及月份的社保明细映射。

    返回：
        {(year, month): SocialInsuranceRecord}
    """
    from app.models.payroll import SocialInsuranceRecord  # 延迟导入，避免循环依赖

    if not periods:
        return {}

    years = sorted({year for year, _month in periods})
    stmt = select(SocialInsuranceRecord).where(
        SocialInsuranceRecord.employee_id == employee_id,
        SocialInsuranceRecord.year.in_(years),
    )
    result = await db.execute(stmt)
    records = list(result.scalars().all())
    period_set = set(periods)
    return {
        (item.year, item.month): item
        for item in records
        if (item.year, item.month) in period_set
    }


async def get_my_leave_balance(
    db: AsyncSession,
    employee_id: int,
    year: int,
) -> list:
    """
    获取当前员工指定年度的假期余额列表。

    每条记录对应一种假期类型（如年假/病假/事假等），
    包含总额度、已用天数、可用余额等字段。

    参数：
        db          - 异步数据库会话
        employee_id - 当前登录员工 ID
        year        - 查询年度（必填，如 2024）

    返回：
        LeaveBalance 列表（可能为空，若未初始化假期余额）
    """
    from app.models.leave import LeaveBalance  # 延迟导入，避免循环依赖
    stmt = select(LeaveBalance).where(
        LeaveBalance.employee_id == employee_id,
        LeaveBalance.year == year,
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_my_leave_requests(
    db: AsyncSession,
    employee_id: int,
    skip: int = 0,
    limit: int = 20,
) -> list:
    """
    获取当前员工自己的请假记录列表。

    返回该员工提交的所有请假申请（无论状态：待审批/已批准/已拒绝/已撤回），
    按创建时间降序排列（最新申请在前）。

    参数：
        db          - 异步数据库会话
        employee_id - 当前登录员工 ID
        skip        - 分页偏移量（默认 0）
        limit       - 每次返回条数（默认 20）

    返回：
        LeaveRequest 列表
    """
    from app.models.leave import LeaveRequest  # 延迟导入，避免循环依赖
    stmt = (
        select(LeaveRequest)
        .where(LeaveRequest.employee_id == employee_id)
        .order_by(LeaveRequest.created_at.desc())  # 最新申请在前
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def submit_leave_request(
    db: AsyncSession,
    employee_id: int,
    leave_type_id: int,
    start_date,
    end_date,
    reason: str,
):
    """
    员工自助提交请假申请。

    本函数是对 LeaveRequestService.create 的薄包装：
    - 组装 LeaveRequestCreate schema
    - 调用 LeaveRequestService.create，复用完整的请假业务逻辑
      （包括：余额检查、日期合法性验证、触发审批流等）
    - 将 LeaveRequestService 的异常包装为 HTTPException(400) 返回给前端

    参数：
        db            - 异步数据库会话
        employee_id   - 申请人员工 ID（来自 JWT current_user）
        leave_type_id - 假期类型 ID（对应 LeaveType 表）
        start_date    - 请假开始日期（date 对象）
        end_date      - 请假结束日期（date 对象）
        reason        - 请假原因（文字说明）

    返回：
        新建的 LeaveRequest ORM 对象

    异常：
        HTTPException(400) - 请假创建失败（余额不足/日期非法等，来自 LeaveRequestService）

    下游调用：
        app.services.leave.LeaveRequestService.create()
    """
    from app.schemas.leave import LeaveRequestCreate
    from app.services.leave import LeaveRequestService

    # 组装请假申请 schema（employee_id 由 service 层通过函数参数传入，不在 schema 中）
    data = LeaveRequestCreate(
        leave_type_id=leave_type_id,
        start_date=start_date,
        end_date=end_date,
        reason=reason,
    )
    try:
        return await LeaveRequestService.create(db, employee_id, data)
    except Exception as e:
        # 将 LeaveRequestService 内部异常（ValueError / HTTPException 等）统一包装为 400
        raise HTTPException(status_code=400, detail=f"请假申请失败: {str(e)}")


async def get_my_profile(db: AsyncSession, employee_id: int):
    """
    获取当前员工自己的完整档案信息。

    与管理员视角的 GET /employees/{id} 不同，本接口不做权限细分，
    仅保证员工只能查看自己的档案（employee_id 来自 JWT，由 API 层注入）。

    参数：
        db          - 异步数据库会话
        employee_id - 当前登录员工 ID

    返回：
        Employee ORM 对象

    异常：
        HTTPException(404) - 员工不存在（理论上不应发生，因为 JWT 已验证）
    """
    from app.models.employee import Employee
    result = await db.execute(select(Employee).where(Employee.id == employee_id))
    emp = result.scalar_one_or_none()
    if not emp:
        raise HTTPException(status_code=404, detail="员工不存在")
    return emp


async def update_my_profile(
    db: AsyncSession,
    employee_id: int,
    update_data: dict,
):
    """
    员工自助更新个人档案（仅限受限白名单字段）。

    安全设计：
    - ALLOWED_FIELDS 白名单严格限定可修改字段，防止员工篡改姓名/工号/状态/部门等敏感信息
    - 白名单字段：email / current_address / emergency_contact / emergency_phone / bank_name / bank_account
    - 请求中不在白名单的字段自动忽略（不报错，静默丢弃）
    - None 值不覆盖已有数据（防止空提交清空字段）

    业务场景：
        员工入职后可自行更新：
        - 邮箱地址（email）
        - 现居住地址（current_address）
        - 紧急联系人姓名（emergency_contact）和电话（emergency_phone）
        - 银行名称（bank_name）和账号（bank_account，用于工资发放）

    参数：
        db          - 异步数据库会话
        employee_id - 当前登录员工 ID
        update_data - 包含待更新字段的字典（key 为字段名，value 为新值）

    返回：
        更新后的 Employee ORM 对象

    异常：
        HTTPException(404) - 员工不存在
    """
    from app.models.employee import Employee

    # 白名单：员工仅可修改这几个字段，其余字段即使传入也会被忽略
    ALLOWED_FIELDS = {
        "email", "current_address", "emergency_contact",
        "emergency_phone", "bank_name", "bank_account",
    }

    result = await db.execute(select(Employee).where(Employee.id == employee_id))
    emp = result.scalar_one_or_none()
    if not emp:
        raise HTTPException(status_code=404, detail="员工不存在")

    # 仅处理白名单内且值非 None 的字段（None 表示"不修改此字段"）
    for key, value in update_data.items():
        if key in ALLOWED_FIELDS and value is not None:
            setattr(emp, key, value)

    await db.flush()   # 刷写到数据库但不提交，由 get_db() 统一 commit
    await db.refresh(emp)  # 刷新 ORM 对象状态
    return emp


async def get_my_asset_checkouts(
    db: AsyncSession,
    employee_id: int,
    active_only: bool = False,
) -> list:
    """获取当前员工名下的固定资产记录。"""
    from app.models.admin import AssetCheckout

    stmt = (
        select(AssetCheckout)
        .options(
            selectinload(AssetCheckout.asset),
            selectinload(AssetCheckout.employee),
        )
        .where(AssetCheckout.employee_id == employee_id)
        .order_by(AssetCheckout.checkout_date.desc(), AssetCheckout.id.desc())
    )
    if active_only:
        stmt = stmt.where(AssetCheckout.status.in_(["active", "overdue"]))
    result = await db.execute(stmt)
    return list(result.scalars().all())
