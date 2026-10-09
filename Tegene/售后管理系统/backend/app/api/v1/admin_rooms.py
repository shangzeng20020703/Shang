"""
会议室预约 API 路由模块 — admin_rooms.py

本模块负责会议室预约的创建、查询、更新和取消，由 router.py 以前缀
/api/v1/admin/rooms 挂载（具体前缀以 router.py 配置为准）。

功能概览：
  - 预约会议室（含时间冲突检测）
  - 查询预约列表（支持会议室名称/日期/预约人筛选，分页）
  - 查询指定会议室某天的空闲时段（availability 接口）
  - 获取单条预约详情
  - 更新预约信息（时间段修改后重新做冲突检测）
  - 取消预约（软删除语义，保留审计记录）

设计说明：
  本模块中会议室通过 room_name 字符串标识，系统中不存在独立的 Room 实体表。
  若需要会议室的独立属性管理（容量/设备/图片等），可扩展增加 Room 模型和 CRUD 端点。

时间冲突检测逻辑（在 Service 层实现）：
  同一 room_name + 同一 booking_date，start_time/end_time 时间段不能与已有
  非 cancelled 预约重叠。重叠判断：新预约的 start_time < 已有预约的 end_time
  AND 新预约的 end_time > 已有预约的 start_time。

路由注册顺序（重要）：
  FastAPI 按注册顺序匹配路由，固定路径字面量必须在路径参数路由之前注册，
  因此本文件的注册顺序为：
    1. POST   /book               （固定路径，预约新会议室）
    2. GET    /bookings           （固定路径，列表查询）
    3. GET    /availability       （固定路径，空闲查询）
    4. GET    /bookings/{id}      （含参数，查详情）
    5. PUT    /bookings/{id}      （含参数，更新）
    6. POST   /bookings/{id}/cancel（含参数，取消）

认证与权限：
  所有端点均需 JWT 认证（Depends(get_current_user)）。
  创建预约：预约人 (booker_id) 自动取当前登录用户 ID，任何员工均可预约。
  取消/更新：建议限制为预约人本人或 admin/hr 角色（当前实现未强制）。

对应 Service：app.services.admin（以 svc 导入）
对应 Schema ：app.schemas.admin.BookingRoom*
关联模型    ：app.models.admin.MeetingRoomBooking（或同等命名）

注意：本文件与 admin.py 中的 /rooms 路由组功能完全相同，
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
    BookingRoomCreate,
    BookingRoomOut,
    BookingRoomPage,
    BookingRoomUpdate,
)
from app.services import admin as svc

# 会议室路由器，不含前缀（由 router.py 统一挂载时指定前缀）
router = APIRouter()


@router.post("/book", response_model=BookingRoomOut, status_code=201)
async def create_room_booking(
    data: BookingRoomCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> BookingRoomOut:
    """
    HTTP POST /admin/rooms/book
    用途：预约指定会议室的某个时间段，Service 层自动检测时间冲突。
          预约人（booker_id）自动设为当前登录用户，无需在请求体中传入。

    请求体 (BookingRoomCreate)：
      - room_name      : str  — 会议室名称（必填），如 "A101会议室" / "大会议室"
      - booking_date   : date — 预约日期（必填，格式：YYYY-MM-DD）
      - start_time     : time — 开始时间（必填，格式：HH:MM，如 "09:00"）
      - end_time       : time — 结束时间（必填，格式：HH:MM，如 "11:00"，须晚于 start_time）
      - title          : str  — 会议主题（必填）
      - attendees      : int  — 预计参会人数（可选，用于选择合适容量的会议室）
      - description    : str  — 备注说明（可选），如会议内容、所需设备

    响应 (BookingRoomOut, HTTP 201)：
      - 新建的预约记录，含：booking_id / booker_id / room_name / status="confirmed"

    错误：
      - 400 Bad Request：所选时间段与同一会议室已有预约冲突
      - 422 Unprocessable：必填字段缺失或 end_time <= start_time

    权限：需登录，booker_id 自动取 current_user.id
    Service：svc.create_room_booking(db, data, booker_id=current_user.id)
    """
    booking = await svc.create_room_booking(db, data, booker_id=current_user.id)
    return BookingRoomOut.model_validate(booking)


@router.get("/bookings", response_model=BookingRoomPage)
async def list_room_bookings(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    room_name: Optional[str] = Query(None, description="会议室名称"),
    date: Optional[date] = Query(None, description="预约日期"),
    booker_id: Optional[int] = Query(None, description="预约人ID"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingRoomPage:
    """
    HTTP GET /admin/rooms/bookings
    用途：分页查询会议室预约列表，支持按会议室名称、日期、预约人筛选，
          按预约日期和开始时间升序排列。

    Query 参数：
      - skip      : int（默认 0，最小 0）       — 分页偏移量
      - limit     : int（默认 20，范围 1-1000）  — 每页条数
      - room_name : str（可选）                  — 按会议室名称筛选（模糊或精确匹配）
      - date      : date（可选）                 — 筛选特定预约日期（格式：YYYY-MM-DD）
      - booker_id : int（可选）                  — 筛选指定预约人（员工 ID）的所有预约

    响应 (BookingRoomPage, HTTP 200)：
      - total : int                  — 满足条件的预约总数（用于前端分页组件）
      - items : list[BookingRoomOut] — 当页预约记录，含状态信息（confirmed/cancelled）

    权限：需登录
    Service：svc.list_room_bookings(db, skip, limit, room_name, booking_date, booker_id)
    """
    total, items = await svc.list_room_bookings(
        db,
        skip=skip,
        limit=limit,
        room_name=room_name,
        booking_date=date,
        booker_id=booker_id,
    )
    return BookingRoomPage(
        total=total,
        items=[BookingRoomOut.model_validate(b) for b in items],
    )


@router.get("/availability", response_model=list[BookingRoomOut])
async def check_room_availability(
    room_name: str = Query(..., description="会议室名称"),
    date: date = Query(..., description="查询日期"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> list[BookingRoomOut]:
    """
    HTTP GET /admin/rooms/availability
    用途：查询指定会议室在某天的所有有效预约（不含已取消的预约），
          前端据此渲染时间轴视图，让用户直观看到哪些时段已被占用，选择空闲时段下单。
          此接口是预约前的前置查询，不修改任何数据。

    Query 参数（均为必填）：
      - room_name : str  — 会议室名称（必填），如 "A101会议室"
      - date      : date — 查询日期（必填，格式：YYYY-MM-DD）

    响应 (list[BookingRoomOut], HTTP 200)：
      - 该会议室当天所有非 cancelled 预约记录列表，按 start_time 升序排列
      - 返回空列表表示当天无任何预约，全天可用

    典型使用方式：
      前端在预约对话框中，当用户选择会议室和日期后，自动调用此接口，
      将已占用时段标红/置灰，帮助用户快速选择空闲时段。

    权限：需登录
    Service：svc.check_room_availability(db, room_name, date)
    """
    bookings = await svc.check_room_availability(db, room_name, date)
    return [BookingRoomOut.model_validate(b) for b in bookings]


@router.get("/bookings/{booking_id}", response_model=BookingRoomOut)
async def get_room_booking(
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingRoomOut:
    """
    HTTP GET /admin/rooms/bookings/{booking_id}
    用途：获取单条会议室预约记录的完整详情，包含预约人信息、会议主题、状态等。

    Path 参数：
      - booking_id : int — 会议室预约记录主键 ID

    响应 (BookingRoomOut, HTTP 200)：
      - 完整预约信息，含：booking_id / room_name / booking_date /
        start_time / end_time / title / attendees / booker_id / status

    错误：
      - 404 Not Found：预约记录 ID 不存在

    权限：需登录
    Service：svc.get_room_booking(db, booking_id)
    """
    booking = await svc.get_room_booking(db, booking_id)
    return BookingRoomOut.model_validate(booking)


@router.put("/bookings/{booking_id}", response_model=BookingRoomOut)
async def update_room_booking(
    booking_id: int,
    data: BookingRoomUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingRoomOut:
    """
    HTTP PUT /admin/rooms/bookings/{booking_id}
    用途：更新会议室预约信息（如调整会议时间、修改会议主题）。
          若修改了 start_time 或 end_time，Service 层会自动重新做冲突检测
          （排除本条预约自身后，与其他预约的时间重叠判断）。

    Path 参数：
      - booking_id : int — 会议室预约记录主键 ID

    请求体 (BookingRoomUpdate)：Pydantic v2 partial 模式，所有字段均为可选
      - start_time  : time（可选）— 更新开始时间
      - end_time    : time（可选）— 更新结束时间（须晚于 start_time）
      - title       : str（可选） — 更新会议主题
      - attendees   : int（可选） — 更新参会人数
      - description : str（可选） — 更新备注

    响应 (BookingRoomOut, HTTP 200)：更新后的完整预约信息

    错误：
      - 404 Not Found  ：预约记录 ID 不存在
      - 400 Bad Request：新时间段与其他预约冲突；或预约已取消无法修改
      - 422 Unprocessable：end_time <= start_time

    权限：需登录（建议限制为预约人本人或 admin/hr 角色）
    Service：svc.update_room_booking(db, booking_id, data)
    """
    booking = await svc.update_room_booking(db, booking_id, data)
    return BookingRoomOut.model_validate(booking)


@router.post("/bookings/{booking_id}/cancel", response_model=BookingRoomOut)
async def cancel_room_booking(
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingRoomOut:
    """
    HTTP POST /admin/rooms/bookings/{booking_id}/cancel
    用途：取消指定会议室预约，将预约状态变更为 cancelled，
          释放该会议室在该时间段的占用，其他人可重新预约该时段。
          使用 POST（而非 DELETE）实现软删除语义，保留预约历史记录以供审计。

    Path 参数：
      - booking_id : int — 会议室预约记录主键 ID

    响应 (BookingRoomOut, HTTP 200)：
      - 取消后的预约记录，status 字段变为 "cancelled"

    错误：
      - 404 Not Found  ：预约记录 ID 不存在
      - 400 Bad Request：预约已处于 cancelled 状态（不允许重复取消）

    权限：需登录（建议限制为预约人本人或 admin/hr 角色）
    Service：svc.cancel_room_booking(db, booking_id)
    """
    booking = await svc.cancel_room_booking(db, booking_id)
    return BookingRoomOut.model_validate(booking)
