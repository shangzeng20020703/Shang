"""
访客管理 API 路由模块 — admin_visitors.py

本模块负责外部访客来访预约的登记、签到、签退和查询全流程管理，由 router.py 以前缀
/api/v1/admin/visitors 挂载（具体前缀以 router.py 配置为准）。

功能概览：
  - 登记访客预约（内部接待人填写访客信息，初始状态 pending）
  - 查询访客记录列表（支持来访日期/状态/接待人筛选，分页）
  - 获取访客记录详情
  - 更新访客预约信息（调整来访日期、事由等）
  - 访客签到（前台操作，记录签到时间和证件信息）
  - 访客签退（前台操作，记录离开时间）
  - 删除访客记录（清理错误数据）

访客状态机（5个状态）：
  pending（待确认）
    ├──确认/预批准──> confirmed（已确认）
    │                   └──前台签到──> checked_in（已签到/在场）
    │                                    └──前台签退──> checked_out（已离开，终态）
    └──取消/拒绝──> cancelled（已取消，终态）
  任何 pending/confirmed 状态均可被取消（cancelled）。

典型业务流程：
  1. 内部员工（接待人）在系统中提前登记访客信息 → pending
  2. 行政/前台确认来访信息无误 → confirmed
  3. 访客到达，前台核验证件并执行签到 → checked_in
  4. 访客离开，前台执行签退并归还访客证 → checked_out

认证与权限：
  所有端点均需 JWT 认证（Depends(get_current_user)）。
  登记预约：任何已登录员工均可为自己接待的访客填写预约信息。
  签到/签退：通常由前台（reception）角色操作，建议在 Service 层或 RBAC 中限制。
  删除：建议限制为 admin 角色。

对应 Service：app.services.admin（以 svc 导入）
对应 Schema ：app.schemas.admin.Visitor*
关联模型    ：app.models.admin.Visitor

注意：本文件与 admin.py 中的 /visitors 路由组功能完全相同，
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
    VisitorCheckInRequest,
    VisitorCreate,
    VisitorOut,
    VisitorPage,
    VisitorUpdate,
)
from app.services import admin as svc

# 访客路由器，不含前缀（由 router.py 统一挂载时指定前缀）
router = APIRouter()


@router.post("", response_model=VisitorOut, status_code=201)
async def create_visitor(
    data: VisitorCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VisitorOut:
    """
    HTTP POST /admin/visitors
    用途：内部员工（接待人）预先登记访客来访信息，创建访客记录，初始状态为 pending（待确认）。
          通常在访客来访前 1 天或当天由接待负责人填写，以便前台提前了解来访情况。

    请求体 (VisitorCreate)：
      - visitor_name      : str  — 访客姓名（必填）
      - visitor_phone     : str  — 访客手机号（必填，用于联系确认）
      - visitor_company   : str  — 访客所在公司/单位（可选）
      - host_employee_id  : int  — 接待人员工 ID（必填，关联内部员工表）
      - visit_date        : date — 预约来访日期（必填，格式：YYYY-MM-DD）
      - visit_purpose     : str  — 来访事由（可选），如 "商务洽谈" / "参观考察" / "技术交流"
      - expected_duration : int  — 预计停留时长（可选，单位：分钟）
      - notes             : str  — 补充说明（可选），如访客特殊需求、随行人数

    响应 (VisitorOut, HTTP 201)：
      - 新建的访客记录，含：id / status="pending" / created_at

    错误：
      - 404 Not Found：接待人员工 ID (host_employee_id) 不存在
      - 422 Unprocessable：必填字段缺失或格式错误

    权限：需登录（任何员工均可为自己接待的访客填写预约）
    Service：svc.create_visitor(db, data)
    """
    visitor = await svc.create_visitor(db, data)
    return VisitorOut.model_validate(visitor)


@router.get("", response_model=VisitorPage)
async def list_visitors(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    visit_date: Optional[date] = Query(None, description="来访日期"),
    status: Optional[str] = Query(None, description="按状态筛选"),
    host_employee_id: Optional[int] = Query(None, description="接待人ID"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VisitorPage:
    """
    HTTP GET /admin/visitors
    用途：分页查询访客预约记录，支持按来访日期、状态、接待人多维度筛选，
          按来访日期和创建时间排序。

    Query 参数：
      - skip             : int（默认 0，最小 0）       — 分页偏移量
      - limit            : int（默认 20，范围 1-1000）  — 每页条数
      - visit_date       : date（可选）                 — 筛选特定来访日期（格式：YYYY-MM-DD）
      - status           : str（可选）                  — 筛选访客状态：
                                                            "pending"     = 待确认
                                                            "confirmed"   = 已确认
                                                            "checked_in"  = 已签到（在场）
                                                            "checked_out" = 已离开
                                                            "cancelled"   = 已取消
      - host_employee_id : int（可选）                  — 筛选指定接待人的访客记录

    响应 (VisitorPage, HTTP 200)：
      - total : int            — 满足条件的访客记录总数（用于前端分页组件）
      - items : list[VisitorOut] — 当页访客记录列表

    典型使用场景：
      - 前台每日查看当天所有预约来访：传 visit_date=今日
      - 员工查看自己接待的访客：传 host_employee_id=current_user.id
      - 管理员统计当前在场访客：传 status="checked_in"

    权限：需登录
    Service：svc.list_visitors(db, skip, limit, visit_date, status_filter, host_employee_id)
    """
    total, items = await svc.list_visitors(
        db,
        skip=skip,
        limit=limit,
        visit_date=visit_date,
        status_filter=status,
        host_employee_id=host_employee_id,
    )
    return VisitorPage(
        total=total,
        items=[VisitorOut.model_validate(v) for v in items],
    )


@router.get("/{visitor_id}", response_model=VisitorOut)
async def get_visitor(
    visitor_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VisitorOut:
    """
    HTTP GET /admin/visitors/{visitor_id}
    用途：获取单条访客预约记录的完整详情，包含访客基本信息、接待人信息、
          来访状态、签到/签退时间和证件信息。

    Path 参数：
      - visitor_id : int — 访客预约记录主键 ID

    响应 (VisitorOut, HTTP 200)：
      - 完整访客记录，含：id / visitor_name / visitor_phone / visitor_company /
        host_employee_id / visit_date / visit_purpose / status /
        check_in_time / check_out_time / id_type / id_number / badge_number

    错误：
      - 404 Not Found：访客记录 ID 不存在

    权限：需登录
    Service：svc.get_visitor(db, visitor_id)
    """
    visitor = await svc.get_visitor(db, visitor_id)
    return VisitorOut.model_validate(visitor)


@router.put("/{visitor_id}", response_model=VisitorOut)
async def update_visitor(
    visitor_id: int,
    data: VisitorUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VisitorOut:
    """
    HTTP PUT /admin/visitors/{visitor_id}
    用途：更新访客预约信息（如调整来访日期、修改来访事由、更新联系电话）。
          建议仅在 pending / confirmed 状态时允许修改，
          已签到/已离开的访客记录应视为历史数据，不应修改。

    Path 参数：
      - visitor_id : int — 访客预约记录主键 ID

    请求体 (VisitorUpdate)：Pydantic v2 partial 模式，所有字段均为可选
      - visitor_name      : str（可选）  — 更新访客姓名
      - visitor_phone     : str（可选）  — 更新访客手机号
      - visitor_company   : str（可选）  — 更新访客公司
      - visit_date        : date（可选） — 更新来访日期
      - visit_purpose     : str（可选）  — 更新来访事由
      - expected_duration : int（可选）  — 更新预计停留时长（分钟）
      - notes             : str（可选）  — 更新备注

    响应 (VisitorOut, HTTP 200)：更新后的完整访客记录

    错误：
      - 404 Not Found  ：访客记录 ID 不存在
      - 400 Bad Request：访客已处于 checked_in/checked_out/cancelled 状态，不允许修改基本信息

    权限：需登录（建议限制为接待人本人或 admin/hr 角色）
    Service：svc.update_visitor(db, visitor_id, data)
    """
    visitor = await svc.update_visitor(db, visitor_id, data)
    return VisitorOut.model_validate(visitor)


@router.post("/{visitor_id}/check-in", response_model=VisitorOut)
async def visitor_check_in(
    visitor_id: int,
    data: VisitorCheckInRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VisitorOut:
    """
    HTTP POST /admin/visitors/{visitor_id}/check-in
    用途：访客到达公司时，前台执行签到操作。
          状态由 confirmed（已确认）变更为 checked_in（已签到/在场），
          记录访客的实际到达时间和证件信息，并发放访客临时通行证。

    Path 参数：
      - visitor_id : int — 访客预约记录主键 ID

    请求体 (VisitorCheckInRequest)：
      - check_in_time : datetime — 签到时间（可选，若不传则取服务器当前时间）
      - id_type       : str      — 证件类型（可选）：身份证 / 护照 / 驾驶证 / 其他
      - id_number     : str      — 证件号码（可选，出于隐私保护可部分脱敏存储）
      - badge_number  : str      — 访客临时通行证/访客牌编号（可选，用于登记归还）

    响应 (VisitorOut, HTTP 200)：
      - 签到后的访客记录，check_in_time 已填写，status="checked_in"

    错误：
      - 404 Not Found  ：访客记录 ID 不存在
      - 400 Bad Request：访客当前状态非 confirmed（
                          若为 pending 表示未确认，
                          若为 checked_in 表示已签到过，
                          若为 checked_out/cancelled 表示无效记录）

    权限：需登录（通常由前台接待人员操作，建议限制为 reception/admin 角色）
    Service：svc.visitor_check_in(db, visitor_id, data)
    """
    visitor = await svc.visitor_check_in(db, visitor_id, data)
    return VisitorOut.model_validate(visitor)


@router.post("/{visitor_id}/check-out", response_model=VisitorOut)
async def visitor_check_out(
    visitor_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VisitorOut:
    """
    HTTP POST /admin/visitors/{visitor_id}/check-out
    用途：访客离开公司时，前台执行签退操作。
          状态由 checked_in（已签到）变更为 checked_out（已离开），
          记录实际离开时间，并回收访客临时通行证。
          无需请求体，离开时间自动取服务器当前时间。

    Path 参数：
      - visitor_id : int — 访客预约记录主键 ID

    响应 (VisitorOut, HTTP 200)：
      - 签退后的访客记录，check_out_time 已填写，status="checked_out"
      - 实际停留时长可通过 check_out_time - check_in_time 计算

    错误：
      - 404 Not Found  ：访客记录 ID 不存在
      - 400 Bad Request：访客当前状态非 checked_in（未签到的访客不能直接签退）

    权限：需登录（通常由前台接待人员操作，建议限制为 reception/admin 角色）
    Service：svc.visitor_check_out(db, visitor_id)
    """
    visitor = await svc.visitor_check_out(db, visitor_id)
    return VisitorOut.model_validate(visitor)


@router.delete("/{visitor_id}", status_code=204)
async def delete_visitor(
    visitor_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> None:
    """
    HTTP DELETE /admin/visitors/{visitor_id}
    用途：物理删除访客预约记录。
          适用场景：
            - 清理测试数据
            - 删除错误录入的预约（建议仅在 pending/confirmed 状态时允许删除）
          已完成来访（checked_out）的访客记录属于安全审计数据，
          建议保留并设置访问控制，不做物理删除。

    Path 参数：
      - visitor_id : int — 访客预约记录主键 ID

    响应：HTTP 204 No Content（无响应体）

    错误：
      - 404 Not Found  ：访客记录 ID 不存在
      - 400 Bad Request：访客已处于 checked_in 状态（在场访客不允许删除记录）

    权限：需登录（建议严格限制为 admin 角色）
    Service：svc.delete_visitor(db, visitor_id)
    """
    await svc.delete_visitor(db, visitor_id)
