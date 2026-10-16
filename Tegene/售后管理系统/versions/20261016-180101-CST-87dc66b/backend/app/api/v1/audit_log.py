"""
审计日志 API 路由模块

路由前缀（注册于 router.py）: /api/v1/audit-logs

本模块提供对系统操作审计日志的只读查询接口。审计日志由系统在业务操作执行时
自动写入（通过 Service 层调用 AuditLogService.log()），不提供手动创建/修改/删除端点，
确保日志数据的不可篡改性。

功能说明：
  审计日志记录员工在系统中的所有关键操作，包括：
    - 模块（module）：操作发生的功能模块（如 employee / leave / payroll / attendance）
    - 动作（action）：具体操作类型（如 create / update / delete / approve / login）
    - 操作人（operator_id）：执行操作的员工 ID
    - 操作时间（created_at）：操作时间戳
    - 操作详情（detail）：操作的具体内容（JSON 或文本）
    - 目标对象 ID（target_id）：被操作的业务对象主键（可选）

端点清单：
  GET  /audit-logs  — 分页查询操作日志（支持多维度过滤，仅限 admin/hr 角色）

权限说明：
  - 本模块所有端点仅允许 admin 或 hr 角色访问（require_roles）
  - 普通员工和 manager 无权查看审计日志，以保护操作隐私

对应 Service：app.services.audit_log.AuditLogService（静态方法模式）
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.schemas.audit_log import AuditLogOut
from app.services.audit_log import AuditLogService

router = APIRouter()


@router.get("", response_model=List[AuditLogOut], summary="操作日志列表")
async def list_audit_logs(
    operator_id: Optional[int] = Query(None, description="操作人员工ID"),
    action: Optional[str] = Query(None, description="操作类型"),
    module: Optional[str] = Query(None, description="模块"),
    date_from: Optional[datetime] = Query(None, description="开始时间"),
    date_to: Optional[datetime] = Query(None, description="结束时间"),
    resource_types: Optional[str] = Query(None, description="业务对象类型，逗号分隔"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    GET /audit-logs — 查询系统操作审计日志

    用途：供管理员和 HR 查询系统中所有用户的操作历史记录，用于安全审计、
          问题排查（如查找误操作）和合规检查。支持多维度过滤和时间范围查询。

    Query 参数：
        - operator_id: int      — 只查询指定员工的操作记录（可选）
                                  用于排查某员工的操作历史
        - action: str           — 按操作类型过滤（可选）
                                  常见值：create / update / delete / approve / reject /
                                          login / logout / export
        - module: str           — 按功能模块过滤（可选）
                                  常见值：employee / leave / attendance / payroll /
                                          performance / approval / competency
        - date_from: datetime   — 查询时间范围开始（ISO 8601 格式，可选）
                                  如 "2024-01-01T00:00:00"
        - date_to: datetime     — 查询时间范围结束（ISO 8601 格式，可选）
                                  如 "2024-12-31T23:59:59"
        - skip: int             — 跳过条数（分页用，默认 0，最小 0）
        - limit: int            — 每页条数（默认 50，范围 1~200）

    响应（200 OK）：list[AuditLogOut]（按操作时间倒序排列）
        每项含：
            - id: int                  — 日志记录主键
            - operator_id: int         — 操作人员工 ID
            - operator_name: str       — 操作人姓名（关联查询填充）
            - module: str              — 操作所属模块
            - action: str              — 操作类型
            - target_id: Optional[int] — 被操作对象的主键 ID
            - detail: Optional[str]    — 操作详情（JSON 或文本描述）
            - ip_address: Optional[str] — 操作人 IP 地址（若有记录）
            - created_at: datetime     — 操作时间戳

    权限：仅 admin 或 hr 角色（require_roles）
          普通员工和 manager 调用此端点将返回 403 Forbidden

    注意：
        - 此接口为纯只读端点，无对应的写入端点
        - 审计日志由各业务 Service 层在操作时自动写入，不允许手动创建或修改
        - 时间参数使用系统时区（UTC），前端需注意时区转换

    Service：AuditLogService.list_logs(db, operator_id, action, module,
                                        date_from, date_to, skip, limit)
    """
    return await AuditLogService.list_logs(
        db,
        operator_id=operator_id,
        action=action,
        module=module,
        resource_types=resource_types.split(",") if resource_types else None,
        date_from=date_from,
        date_to=date_to,
        skip=skip,
        limit=limit,
    )
