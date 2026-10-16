"""
通知中心 API 路由模块

路由前缀（注册于 router.py）: /api/v1/notifications

本模块管理售后管理系统的用户通知功能，负责向员工推送系统事件消息
（如审批结果通知、待办提醒、系统公告等），并提供通知的阅读状态管理。

通知生命周期：
  1. 系统产生（由业务 Service 层调用 NotificationService.create() 写入，此 API 无创建端点）
  2. 用户查询（GET /notifications — 获取列表）
  3. 标记已读（POST /notifications/{id}/read 或 POST /notifications/read-all）
  4. 查看未读数（GET /notifications/unread-count — 用于首页角标展示）

通知类型（type 字段，由创建方设置）：
  - approval_result   — 审批结果通知（申请被通过/拒绝）
  - approval_pending  — 待审批提醒（有新申请需要我处理）
  - system            — 系统通知
  - announcement      — 公告通知
  - hr_notice         — HR 人工推送通知

端点清单：
  GET    /notifications/unread-count   — 获取当前用户未读通知数量（用于角标）
  POST   /notifications/read-all       — 将当前用户所有未读通知标记为已读
  GET    /notifications                — 获取当前用户通知列表（分页，支持未读过滤）
  POST   /notifications/{id}/read      — 将单条通知标记为已读

路由顺序说明：
  /unread-count 和 /read-all 为固定路径，必须在 /{notif_id} 动态路由之前注册，
  否则字符串 "unread-count" 会被误解析为 notif_id。

权限说明：
  - 所有端点均要求已登录（get_current_user），但无角色限制
  - 所有查询和操作仅针对当前登录用户自己的通知，Service 层强制按 user_id 过滤
  - 无法查看或操作他人的通知（由 Service 层校验所有权）

对应 Service：app.services.notification.NotificationService（静态方法模式）
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.employee import Employee
from app.schemas.notification import NotificationListResponse, NotificationOut
from app.services.notification import NotificationService

router = APIRouter(tags=["通知中心"])


@router.get("/unread-count", summary="获取未读通知数量")
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /notifications/unread-count — 获取当前用户未读通知数量

    用途：返回当前登录用户的未读通知总数，主要用于前端导航栏或应用角标（badge）展示，
          让用户了解是否有新通知需要处理。建议以较高频率轮询（如每分钟一次）或通过 WebSocket 推送。

    Query 参数：无

    响应（200 OK）：
        { "count": int }  — 未读通知条数（0 表示无未读通知）

    权限：任意已登录用户（get_current_user）
          自动以 current_user.id 过滤，只统计当前用户的通知

    注意：此路由为固定路径，必须在 /{notif_id} 动态路由之前注册。

    Service：NotificationService.get_unread_count(db, current_user.id)
    """
    count = await NotificationService.get_unread_count(db, current_user.id)
    return {"count": count}


@router.post("/read-all", summary="全部标为已读")
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /notifications/read-all — 批量将所有未读通知标记为已读

    用途：一键将当前用户的所有未读通知标记为已读状态，
          用于"清空通知"或"一键已读"功能按钮。

    请求体：无（无需请求体）

    响应（200 OK）：
        { "marked": int }  — 本次操作实际标记的通知条数（若无未读则为 0）

    权限：任意已登录用户（get_current_user）
          只操作当前用户自己的未读通知，不会影响其他用户

    注意：此路由为固定路径，必须在 /{notif_id} 动态路由之前注册。

    Service：NotificationService.mark_all_read(db, current_user.id)
    """
    affected = await NotificationService.mark_all_read(db, current_user.id)
    return {"marked": affected}


@router.get("", response_model=NotificationListResponse, summary="获取通知列表")
async def list_notifications(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页数量"),
    unread_only: bool = Query(default=False, description="仅返回未读通知"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /notifications — 获取当前用户通知列表

    用途：获取当前登录用户的通知消息列表，按创建时间倒序排列（最新通知在前）。
          支持分页和只看未读过滤，用于通知中心页面展示。

    Query 参数：
        - page: int          — 页码（从 1 开始，默认 1）
        - page_size: int     — 每页条数（默认 20，范围 1~100）
        - unread_only: bool  — true 只返回未读通知；false（默认）返回所有通知

    响应（200 OK）：NotificationListResponse
        {
            "total": int              — 符合条件的通知总数（用于分页计算）
            "page": int               — 当前页码
            "page_size": int          — 每页条数
            "items": list[NotificationOut]
        }
        NotificationOut 含：
            - id: int                — 通知主键
            - type: str              — 通知类型（approval_result / approval_pending /
                                       system / announcement / hr_notice）
            - title: str             — 通知标题
            - content: str           — 通知正文内容
            - is_read: bool          — 是否已读（false = 未读）
            - related_id: Optional[int] — 关联业务对象 ID（如 approval_instance_id）
            - related_type: Optional[str] — 关联业务类型（如 approval_instance）
            - created_at: datetime   — 通知创建时间

    权限：任意已登录用户（get_current_user）
          自动以 current_user.id 过滤，只返回当前用户的通知

    Service：NotificationService.list_for_user(db, user_id, page, page_size, unread_only)
    """
    items, total = await NotificationService.list_for_user(
        db,
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        unread_only=unread_only,
    )
    return NotificationListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[NotificationOut.model_validate(n) for n in items],
    )


@router.post("/{notif_id}/read", summary="标记单条通知为已读")
async def mark_read(
    notif_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /notifications/{notif_id}/read — 将单条通知标记为已读

    用途：用户点击查看某条通知详情后，将该通知的已读状态更新为 true。
          Service 层会校验该通知确实属于当前用户，防止越权操作。

    路径参数：
        - notif_id: int — 要标记为已读的通知主键 ID

    请求体：无（无需请求体）

    响应（200 OK）：
        { "ok": true }  — 标记成功

    响应（404 Not Found）：
        通知不存在，或该通知不属于当前用户时返回 404（不区分两种情况，防止枚举攻击）

    权限：任意已登录用户（get_current_user）
          Service 层强制校验 notif_id 对应的 user_id 必须等于 current_user.id

    Service：NotificationService.mark_read(db, notif_id, current_user.id)
             返回 True 表示成功，False 表示不存在或无权限（触发 404）
    """
    ok = await NotificationService.mark_read(db, notif_id, current_user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="通知不存在或无权限操作")
    return {"ok": True}
