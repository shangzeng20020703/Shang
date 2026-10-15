"""
用车管理 API 路由模块 — admin_vehicles.py

本模块负责公司车辆申请的提交、查询、更新和审批全流程，由 router.py 以前缀
/api/v1/admin/vehicles 挂载（具体前缀以 router.py 配置为准）。

功能概览：
  - 提交用车申请（员工发起，初始状态 pending）
  - 查询用车申请列表（支持申请人/审批状态/日期筛选，分页）
  - 获取单条申请详情
  - 更新申请信息（仅 pending 状态建议允许）
  - 审批申请（同意/拒绝，可同时指定车辆和司机）
  - 删除申请记录（清理测试数据或撤销错误申请）

用车申请审批流程：
  员工提交申请 (pending)
    ├──管理员审批通过──> approved（已批准，含分配的车辆/司机信息）
    │                      └──完成使用──> completed（已完成，终态）
    └──管理员驳回──> rejected（已拒绝，终态）

approval_status 枚举值：
  - "pending"   : 待审批（初始状态）
  - "approved"  : 已批准
  - "rejected"  : 已拒绝
  - "completed" : 已完成（行程结束后手动标记）

路由注册顺序（重要）：
  FastAPI 按注册顺序匹配路由，固定路径字面量必须在路径参数路由之前注册：
    1. POST /book                    （固定路径，提交新申请）
    2. GET  /bookings                （固定路径，列表查询）
    3. GET  /bookings/{id}           （含参数，查详情）
    4. PUT  /bookings/{id}           （含参数，更新）
    5. POST /bookings/{id}/approve   （含参数，审批）
    6. DELETE /bookings/{id}         （含参数，删除）

认证与权限：
  所有端点均需 JWT 认证（Depends(get_current_user)）。
  提交申请：申请人 ID 自动取当前登录用户，任何员工均可申请。
  审批：建议限制为 admin/hr/manager 角色（当前实现未强制 RBAC）。
  删除：建议限制为申请人本人（仅 pending 状态）或 admin 角色。

对应 Service：app.services.admin（以 svc 导入）
对应 Schema ：app.schemas.admin.BookingVehicle*
关联模型    ：app.models.admin.VehicleBooking（或同等命名）

注意：本文件与 admin.py 中的 /vehicles 路由组功能完全相同，
      是从综合文件拆分出来的独立版本，由 router.py 选择性挂载其中之一。
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.employee import Employee
from app.schemas.admin import (
    BookingVehicleApproval,
    BookingVehicleCreate,
    BookingVehicleOut,
    BookingVehiclePage,
    BookingVehicleUpdate,
)
from app.services import admin as svc

# 用车路由器，不含前缀（由 router.py 统一挂载时指定前缀）
router = APIRouter()


@router.post("/book", response_model=BookingVehicleOut, status_code=201)
async def create_vehicle_booking(
    data: BookingVehicleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> BookingVehicleOut:
    """
    HTTP POST /admin/vehicles/book
    用途：员工提交用车申请，初始审批状态为 pending（待审批）。
          申请人 ID 自动取当前登录用户，无需在请求体中手动传入。

    请求体 (BookingVehicleCreate)：
      - booking_date    : date — 用车日期（必填，格式：YYYY-MM-DD）
      - start_time      : time — 出发时间（必填，格式：HH:MM，如 "09:00"）
      - end_time        : time — 预计返回时间（可选，如有跨天行程可不填）
      - destination     : str  — 目的地（必填），如 "北京首都机场" / "客户公司"
      - purpose         : str  — 用车事由（必填），如 "接待客户" / "商务洽谈"
      - passenger_count : int  — 乘车人数（可选，帮助分配合适车型）
      - notes           : str  — 补充说明（可选），如特殊要求、联系方式

    响应 (BookingVehicleOut, HTTP 201)：
      - 新建的用车申请记录，含：booking_id / applicant_id / approval_status="pending" / created_at

    错误：
      - 422 Unprocessable：必填字段缺失或格式错误

    权限：需登录，applicant_id 自动取 current_user.id
    Service：svc.create_vehicle_booking(db, data, applicant_id=current_user.id)
    """
    booking = await svc.create_vehicle_booking(
        db, data, applicant_id=current_user.id
    )
    return BookingVehicleOut.model_validate(booking)


@router.get("/bookings", response_model=BookingVehiclePage)
async def list_vehicle_bookings(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    applicant_id: Optional[int] = Query(None, description="申请人ID"),
    approval_status: Optional[str] = Query(None, description="审批状态"),
    date: Optional[date] = Query(None, description="用车日期"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingVehiclePage:
    """
    HTTP GET /admin/vehicles/bookings
    用途：分页查询用车申请列表，支持按申请人、审批状态、用车日期多维度筛选，
          按申请创建时间倒序排列（最新申请优先显示）。

    Query 参数：
      - skip            : int（默认 0，最小 0）       — 分页偏移量
      - limit           : int（默认 20，范围 1-1000）  — 每页条数
      - applicant_id    : int（可选）                  — 筛选指定申请人（员工 ID）的申请记录
      - approval_status : str（可选）                  — 筛选审批状态：
                                                           "pending"   = 待审批
                                                           "approved"  = 已批准
                                                           "rejected"  = 已拒绝
                                                           "completed" = 已完成
      - date            : date（可选）                 — 筛选特定用车日期（格式：YYYY-MM-DD）

    响应 (BookingVehiclePage, HTTP 200)：
      - total : int                     — 满足条件的申请总数（用于前端分页组件）
      - items : list[BookingVehicleOut] — 当页用车申请列表

    典型使用场景：
      - 员工查看自己的用车申请：传 applicant_id=current_user.id
      - 管理员查看所有待审批申请：传 approval_status="pending"
      - 统计某日用车情况：传 date=某日期

    权限：需登录（员工可查自己，管理员可查全部；筛选权限由前端控制）
    Service：svc.list_vehicle_bookings(db, skip, limit, applicant_id, approval_status, booking_date)
    """
    total, items = await svc.list_vehicle_bookings(
        db,
        skip=skip,
        limit=limit,
        applicant_id=applicant_id,
        approval_status=approval_status,
        booking_date=date,
    )
    return BookingVehiclePage(
        total=total,
        items=[BookingVehicleOut.model_validate(b) for b in items],
    )


@router.get("/bookings/{booking_id}", response_model=BookingVehicleOut)
async def get_vehicle_booking(
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingVehicleOut:
    """
    HTTP GET /admin/vehicles/bookings/{booking_id}
    用途：获取单条用车申请的完整详情，包含申请信息、审批意见、
          分配的车辆（车牌号）和司机信息。

    Path 参数：
      - booking_id : int — 用车申请记录主键 ID

    响应 (BookingVehicleOut, HTTP 200)：
      - 完整申请信息，含：booking_id / applicant_id / booking_date /
        start_time / end_time / destination / purpose / approval_status /
        approval_notes / vehicle_plate / driver_name / driver_phone

    错误：
      - 404 Not Found：申请记录 ID 不存在

    权限：需登录
    Service：svc.get_vehicle_booking(db, booking_id)
    """
    booking = await svc.get_vehicle_booking(db, booking_id)
    return BookingVehicleOut.model_validate(booking)


@router.put("/bookings/{booking_id}", response_model=BookingVehicleOut)
async def update_vehicle_booking(
    booking_id: int,
    data: BookingVehicleUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingVehicleOut:
    """
    HTTP PUT /admin/vehicles/bookings/{booking_id}
    用途：更新用车申请的行程信息（如调整出行日期、目的地、事由等）。
          建议仅在 approval_status="pending" 状态时允许申请人修改，
          已审批的申请修改应抛出 400 错误（由 Service 层控制）。

    Path 参数：
      - booking_id : int — 用车申请记录主键 ID

    请求体 (BookingVehicleUpdate)：Pydantic v2 partial 模式，所有字段均为可选
      - booking_date    : date（可选）— 更新用车日期
      - start_time      : time（可选）— 更新出发时间
      - end_time        : time（可选）— 更新预计返回时间
      - destination     : str（可选） — 更新目的地
      - purpose         : str（可选） — 更新用车事由
      - passenger_count : int（可选） — 更新乘车人数
      - notes           : str（可选） — 更新补充说明

    响应 (BookingVehicleOut, HTTP 200)：更新后的完整申请信息

    错误：
      - 404 Not Found  ：申请记录 ID 不存在
      - 400 Bad Request：申请已处于 approved/rejected/completed 状态，不允许修改

    权限：需登录（建议限制为申请人本人或 admin/hr 角色）
    Service：svc.update_vehicle_booking(db, booking_id, data)
    """
    booking = await svc.update_vehicle_booking(db, booking_id, data)
    return BookingVehicleOut.model_validate(booking)


@router.post(
    "/bookings/{booking_id}/approve",
    response_model=BookingVehicleOut,
)
async def approve_vehicle_booking(
    booking_id: int,
    data: BookingVehicleApproval,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingVehicleOut:
    """
    HTTP POST /admin/vehicles/bookings/{booking_id}/approve
    用途：管理员/行政专员对待审批（pending）的用车申请进行审批，
          可选择同意（approved）或拒绝（rejected）。
          审批通过时可在响应体中同时写入分配的车牌号、司机姓名和联系电话。

    Path 参数：
      - booking_id : int — 用车申请记录主键 ID

    请求体 (BookingVehicleApproval)：
      - approved       : bool — 审批结果（必填）：
                                  true  = 同意，申请状态变为 approved
                                  false = 拒绝，申请状态变为 rejected
      - approval_notes : str  — 审批意见（可选，拒绝时建议填写拒绝原因，如 "该日期无可用车辆"）
      - vehicle_plate  : str  — 分配车牌号（可选，仅 approved=true 时有意义）
      - driver_name    : str  — 分配司机姓名（可选，仅 approved=true 时有意义）
      - driver_phone   : str  — 司机联系电话（可选，方便乘车人直接联系司机）

    响应 (BookingVehicleOut, HTTP 200)：
      - 审批后的申请记录，approval_status 变为 "approved" 或 "rejected"

    错误：
      - 404 Not Found  ：申请记录 ID 不存在
      - 400 Bad Request：申请当前状态非 pending（已审批的申请不可重复审批）

    权限：需登录（建议限制为 admin/hr/manager 角色）
    Service：svc.approve_vehicle_booking(db, booking_id, data)
    """
    booking = await svc.approve_vehicle_booking(db, booking_id, data)
    return BookingVehicleOut.model_validate(booking)


@router.delete("/bookings/{booking_id}", status_code=204)
async def delete_vehicle_booking(
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> None:
    """
    HTTP DELETE /admin/vehicles/bookings/{booking_id}
    用途：物理删除用车申请记录。
          适用于以下场景：
            - 清理测试数据
            - 申请人撤销错误提交的 pending 状态申请（建议 Service 层校验只能删除 pending 状态）
          已审批或已完成的申请建议保留审计记录，不做物理删除。

    Path 参数：
      - booking_id : int — 用车申请记录主键 ID

    响应：HTTP 204 No Content（无响应体）

    错误：
      - 404 Not Found  ：申请记录 ID 不存在
      - 400 Bad Request：申请已处于 approved/completed 状态，不允许删除（视 Service 实现而定）

    权限：需登录（建议限制为申请人本人（仅 pending 状态）或 admin 角色）
    Service：svc.delete_vehicle_booking(db, booking_id)
    """
    await svc.delete_vehicle_booking(db, booking_id)
