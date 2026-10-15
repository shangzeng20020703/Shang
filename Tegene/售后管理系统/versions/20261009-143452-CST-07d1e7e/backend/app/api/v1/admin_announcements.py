"""
公告管理 API 路由模块 — admin_announcements.py

本模块负责公司内部公告的完整生命周期管理，由 router.py 以前缀
/api/v1/admin/announcements 挂载（具体前缀以 router.py 配置为准）。

功能概览：
  - 创建公告（草稿）
  - 查询公告列表（支持状态/类别筛选，分页）
  - 获取公告详情
  - 更新公告内容
  - 发布公告（draft → published）
  - 记录员工已读（累计计数）
  - 删除公告

公告状态机：
  draft（草稿）──publish──> published（已发布）──archive──> archived（已归档）
  任何状态均可被删除（物理删除）。

目标范围（target_type / target_ids）：
  - "all"        : 全体员工，target_ids 为空
  - "department" : 指定部门，target_ids 为部门 ID 列表
  - "individual" : 指定个人，target_ids 为员工 ID 列表

认证与权限：
  所有端点均需 JWT 认证（Depends(get_current_user)）。
  创建/发布/删除建议限制为 admin/hr 角色（当前实现未强制 RBAC，可按需添加）。
  发布人 publisher_id 自动取当前登录用户 ID。

对应 Service：app.services.admin（以 svc 导入）
对应 Schema ：app.schemas.admin.Announcement*
关联模型    ：app.models.admin.Announcement

注意：本文件与 admin.py 中的 /announcements 路由组功能完全相同，
      是将公告路由从综合文件中拆分出来的独立版本，由 router.py 选择性挂载其中之一。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.employee import Employee
from app.schemas.admin import (
    AnnouncementCreate,
    AnnouncementOut,
    AnnouncementPage,
    AnnouncementUpdate,
)
from app.services import admin as svc

# 公告路由器，不含前缀（由 router.py 统一挂载时指定前缀）
router = APIRouter()


@router.post("", response_model=AnnouncementOut, status_code=201)
async def create_announcement(
    data: AnnouncementCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> AnnouncementOut:
    """
    HTTP POST /admin/announcements
    用途：创建一条新公告，初始状态为草稿（draft），不立即对员工可见，
          需后续调用 /publish 接口才能正式发布。

    请求体 (AnnouncementCreate)：
      - title       : str       — 公告标题（必填）
      - content     : str       — 公告正文，支持富文本（必填）
      - category    : str       — 公告类别（可选），如：通知 / 规章 / 活动 / 福利
      - target_type : str       — 目标范围（必填）：
                                    "all"        = 全体员工
                                    "department" = 指定部门
                                    "individual" = 指定个人
      - target_ids  : list[int] — 目标 ID 列表（可选）：
                                    target_type="all" 时留空
                                    target_type="department" 时填部门 ID 列表
                                    target_type="individual" 时填员工 ID 列表

    响应 (AnnouncementOut, HTTP 201)：
      - 新建公告完整信息，包含：id / publisher_id / status="draft" / created_at
      - publisher_id 自动设为当前登录用户 ID

    权限：需登录（建议限制为 admin/hr 角色）
    Service：svc.create_announcement(db, data, publisher_id=current_user.id)
    """
    ann = await svc.create_announcement(db, data, publisher_id=current_user.id)
    return AnnouncementOut.model_validate(ann)


@router.get("", response_model=AnnouncementPage)
async def list_announcements(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    status: Optional[str] = Query(None, description="按状态筛选"),
    category: Optional[str] = Query(None, description="按类别筛选"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AnnouncementPage:
    """
    HTTP GET /admin/announcements
    用途：分页查询公告列表，支持按状态和类别筛选，按创建时间倒序排列。

    Query 参数：
      - skip     : int（默认 0，最小 0）       — 分页偏移量
      - limit    : int（默认 20，范围 1-1000）  — 每页条数
      - status   : str（可选）                  — 筛选公告状态：
                                                   "draft"     = 草稿
                                                   "published" = 已发布
                                                   "archived"  = 已归档
      - category : str（可选）                  — 筛选公告类别，精确匹配

    响应 (AnnouncementPage, HTTP 200)：
      - total : int                   — 满足条件的公告总数（用于前端分页组件）
      - items : list[AnnouncementOut] — 当页公告列表

    权限：需登录（所有员工可查看，草稿可见性由前端或 Service 层控制）
    Service：svc.list_announcements(db, skip, limit, status_filter, category)
    """
    total, items = await svc.list_announcements(
        db, skip=skip, limit=limit, status_filter=status, category=category
    )
    return AnnouncementPage(
        total=total,
        items=[AnnouncementOut.model_validate(a) for a in items],
    )


@router.get("/{announcement_id}", response_model=AnnouncementOut)
async def get_announcement(
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AnnouncementOut:
    """
    HTTP GET /admin/announcements/{announcement_id}
    用途：获取指定公告的完整详情，包含发布人信息、目标范围和已读计数。

    Path 参数：
      - announcement_id : int — 公告主键 ID

    响应 (AnnouncementOut, HTTP 200)：
      - 公告完整信息，含：id / title / content / category / status /
        target_type / target_ids / publisher_id / read_count / created_at / updated_at

    错误：
      - 404 Not Found：公告 ID 不存在

    权限：需登录
    Service：svc.get_announcement(db, announcement_id)
    """
    ann = await svc.get_announcement(db, announcement_id)
    return AnnouncementOut.model_validate(ann)


@router.put("/{announcement_id}", response_model=AnnouncementOut)
async def update_announcement(
    announcement_id: int,
    data: AnnouncementUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AnnouncementOut:
    """
    HTTP PUT /admin/announcements/{announcement_id}
    用途：更新公告的标题、正文、类别或目标范围（建议仅草稿状态可完整编辑，
          已发布公告的修改需谨慎，可能导致已读用户看到不同内容）。

    Path 参数：
      - announcement_id : int — 公告主键 ID

    请求体 (AnnouncementUpdate)：Pydantic v2 partial 模式，所有字段均为可选
      - title       : str（可选）       — 更新标题
      - content     : str（可选）       — 更新正文
      - category    : str（可选）       — 更新类别
      - target_type : str（可选）       — 更新目标范围类型
      - target_ids  : list[int]（可选） — 更新目标 ID 列表

    响应 (AnnouncementOut, HTTP 200)：更新后的完整公告信息

    错误：
      - 404 Not Found：公告 ID 不存在

    权限：需登录（建议限制为发布人本人或 admin/hr 角色）
    Service：svc.update_announcement(db, announcement_id, data)
    """
    ann = await svc.update_announcement(db, announcement_id, data)
    return AnnouncementOut.model_validate(ann)


@router.post("/{announcement_id}/publish", response_model=AnnouncementOut)
async def publish_announcement(
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AnnouncementOut:
    """
    HTTP POST /admin/announcements/{announcement_id}/publish
    用途：将草稿公告正式发布，状态由 draft 变更为 published。
          发布后公告对符合 target_type/target_ids 条件的员工可见。
          此操作不可逆（已发布不能退回草稿，只能归档）。

    Path 参数：
      - announcement_id : int — 公告主键 ID

    响应 (AnnouncementOut, HTTP 200)：
      - 发布后公告信息，status 字段变为 "published"

    错误：
      - 404 Not Found ：公告 ID 不存在
      - 400 Bad Request：公告已处于 published 或 archived 状态

    权限：需登录（建议限制为 admin/hr 角色）
    Service：svc.publish_announcement(db, announcement_id)
    """
    ann = await svc.publish_announcement(db, announcement_id)
    return AnnouncementOut.model_validate(ann)


@router.post("/{announcement_id}/read", response_model=AnnouncementOut)
async def read_announcement(
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AnnouncementOut:
    """
    HTTP POST /admin/announcements/{announcement_id}/read
    用途：员工打开公告详情时调用，将公告的全局已读计数（read_count）加 1，
          用于统计公告触达率和阅读量。
          注意：当前实现为全局累加，不做用户级幂等，同一用户多次调用会多次计数。
          如需精确的"每人只计一次"，需要在 Service 层增加 AnnouncementRead 明细表。

    Path 参数：
      - announcement_id : int — 公告主键 ID

    响应 (AnnouncementOut, HTTP 200)：
      - 含更新后 read_count 的公告信息（read_count 已加 1）

    错误：
      - 404 Not Found：公告 ID 不存在

    权限：需登录（由前端在员工打开公告详情页时自动触发）
    Service：svc.increment_read_count(db, announcement_id)
    """
    ann = await svc.increment_read_count(db, announcement_id)
    return AnnouncementOut.model_validate(ann)


@router.delete("/{announcement_id}", status_code=204)
async def delete_announcement(
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> None:
    """
    HTTP DELETE /admin/announcements/{announcement_id}
    用途：物理删除指定公告记录及关联数据。
          建议生产环境中仅对草稿公告执行删除，已发布的公告改为归档操作以保留审计记录。

    Path 参数：
      - announcement_id : int — 公告主键 ID

    响应：HTTP 204 No Content（无响应体）

    错误：
      - 404 Not Found：公告 ID 不存在

    权限：需登录（建议严格限制为 admin 角色）
    Service：svc.delete_announcement(db, announcement_id)
    """
    await svc.delete_announcement(db, announcement_id)
