"""
加班申请管理 API Routes - Overtime Management
==============================================

本模块提供售后管理系统的加班申请管理接口，涵盖：

**核心功能**：
- 加班申请的提交、查询、撤销（员工自助）
- 加班申请的审批（管理层操作）
- 待审批列表查询（管理层视图）

**加班类型与薪资系数**：
- `weekday`（工作日加班）：薪资系数 1.5x（法定标准）
- `weekend`（周末加班）：薪资系数 2.0x（法定标准）
- `holiday`（法定节假日加班）：薪资系数 3.0x（法定标准）

**状态说明**：
本模块采用双状态设计：
- `status`（业务生命周期状态）：
  - `pending`：待审批
  - `approved`：已批准
  - `rejected`：已拒绝
  - `cancelled`：已撤销
- `approval_status`（审批引擎状态，与通用审批流对接用）：
  - `pending`：待审批
  - `approved`：已批准
  - `rejected`：已拒绝

**权限说明**：
- 查询/提交/撤销自己的申请：所有登录用户
- 查看他人申请（详情页）：admin / hr / manager 角色
- 待审批列表查询、审批操作：admin / hr / manager 角色（`require_roles`）

**路由前缀**（注册在 router.py）：`/api/v1/overtime`

**技术栈**：FastAPI + Pydantic v2 + SQLAlchemy 2.0 (async) + PostgreSQL

**数据模型**：`app/models/overtime.py` → `OvertimeRequest`
**内联 Schema**：本文件直接定义 `OvertimeCreateRequest` 和 `OvertimeApproveRequest`，
未拆分到独立 schemas 文件（轻量模块的常见做法）。
"""
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.models.overtime import OvertimeRequest
from app.services.attendance import AttendanceRecordService
from app.services.approval import maybe_create_approval_instance

# 路由器实例，由 api/v1/router.py 以 prefix="/overtime" 挂载
router = APIRouter()


def _block_legacy_mvp_write(request: Request | None) -> None:
    """
    MVP 阶段加班申请统一走通用审批模板，旧加班写入口只保留给内部回放
    或历史兼容，避免新单据分散在 overtime_requests 与 approval_instances。
    """
    if request is None:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "加班申请已收口到审批中心，请使用 "
            "/api/v1/approval/applications 提交 business_code=legal_overtime 的申请"
        ),
    )


# ============================================================
# 内联请求 Schema 定义
# ============================================================

class OvertimeCreateRequest(BaseModel):
    """
    提交加班申请的请求体结构。

    字段说明：
    - `overtime_date`：加班日期（date 类型，格式 YYYY-MM-DD）
    - `start_time`：开始时间（字符串，格式 HH:MM，如 "18:00"）
    - `end_time`：结束时间（字符串，格式 HH:MM，如 "22:00"）
    - `hours`：加班时长（浮点数，单位：小时，范围 0~24）
    - `overtime_type`：加班类型，枚举值：weekday / weekend / holiday
    - `reason`：加班原因（可选）

    校验规则：
    - start_time 和 end_time 均须符合 HH:MM 正则（`^\d{2}:\d{2}$`）
    - start_time 必须早于 end_time（model_validator 在对象层面校验）
    - hours 须大于 0 且不超过 24
    """
    overtime_date: date
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$", description="HH:MM")
    end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$", description="HH:MM")
    hours: float = Field(..., gt=0, le=24)
    overtime_type: str = Field(default="weekday", pattern=r"^(weekday|weekend|holiday)$")
    reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_time_range(self) -> "OvertimeCreateRequest":
        """跨字段校验：开始时间必须早于结束时间（字符串比较，HH:MM 格式天然支持字典序）"""
        if self.start_time >= self.end_time:
            raise ValueError("start_time 必须早于 end_time")
        return self


class OvertimeApproveRequest(BaseModel):
    """
    审批加班申请的请求体结构。

    字段说明：
    - `action`：审批动作，枚举值：approve（批准）/ reject（拒绝）
    - `reject_reason`：拒绝原因（action 为 reject 时建议填写，可选）
    """
    action: str = Field(..., pattern=r"^(approve|reject)$")
    reject_reason: Optional[str] = None


# ============================================================
# 加班申请 CRUD 端点
# ============================================================

@router.get("", summary="加班申请列表（我的）")
async def list_my_overtime(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /overtime

    查询当前登录员工自己的加班申请列表，按加班日期倒序排列（最新在前）。

    **Query 参数**：
    - `skip`：分页偏移量，默认 0
    - `limit`：每页条数，默认 20，最大 100

    **响应**：`OvertimeRequest[]`（SQLAlchemy ORM 对象，FastAPI 自动序列化），含：
    - id、employee_id、overtime_date、start_time、end_time
    - hours、overtime_type、reason、status、approval_status
    - approver_id、approved_at、reject_reason
    - created_at、updated_at

    **权限**：登录用户（`get_current_user`），仅返回本人申请

    **注意**：此端点无独立 Service，直接通过 SQLAlchemy 查询 `OvertimeRequest`
    """
    stmt = (
        select(OvertimeRequest)
        .where(OvertimeRequest.employee_id == current_user.id)
        .order_by(OvertimeRequest.overtime_date.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("", summary="提交加班申请", deprecated=True)
async def create_overtime(
    data: OvertimeCreateRequest,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /overtime

    旧版加班写入口。MVP 阶段加班申请统一从审批中心提交，本端点仅保留
    历史兼容的路由形态。

    **请求体**（`OvertimeCreateRequest`）：
    - `overtime_date`：加班日期（YYYY-MM-DD）
    - `start_time`：开始时间（HH:MM）
    - `end_time`：结束时间（HH:MM，须晚于 start_time）
    - `hours`：加班时长（小时，0 < hours <= 24）
    - `overtime_type`：类型（weekday / weekend / holiday），默认 weekday
    - `reason`（可选）：加班原因

    **响应**：HTTP 409，提示使用审批中心 business_code=legal_overtime

    **权限**：登录用户（`get_current_user`）

    **注意**：旧实现无独立 Service，直接操作 ORM 对象
    """
    _block_legacy_mvp_write(request)
    req = OvertimeRequest(
        employee_id=current_user.id,
        overtime_date=data.overtime_date,
        start_time=data.start_time,
        end_time=data.end_time,
        hours=data.hours,
        overtime_type=data.overtime_type,
        reason=data.reason,
        status="pending",
        approval_status="pending",
    )

    db.add(req)
    await db.flush()
    await db.refresh(req)

    approval_instance = await maybe_create_approval_instance(
        db,
        module="overtime",
        business_id=req.id,
        business_type="overtime_request",
        applicant_id=current_user.id,
    )
    if not approval_instance:
        raise HTTPException(status_code=400, detail="加班审批流程未配置")

    req.approval_status = "pending"
    return req


@router.get("/pending", summary="待审批加班列表（管理层）")
async def list_pending_overtime(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /overtime/pending

    查询所有待审批状态（status = 'pending'）的加班申请，按创建时间倒序排列。
    此端点仅供管理层使用，普通员工无权访问。

    **Query 参数**：
    - `skip`：分页偏移量，默认 0
    - `limit`：每页条数，默认 20，最大 100

    **响应**：`OvertimeRequest[]`，所有 status='pending' 的加班申请

    **权限**：
    - 需要 admin / hr / manager 角色（`require_roles`）
    - 其他角色用户访问将收到 403 Forbidden

    **注意**：此端点须在 `/{request_id}` 之前注册，避免路由冲突
    """
    stmt = (
        select(OvertimeRequest)
        .where(OvertimeRequest.status == "pending")
        .order_by(OvertimeRequest.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{request_id}", summary="加班申请详情")
async def get_overtime(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /overtime/{request_id}

    查询单条加班申请的详细信息。

    **Path 参数**：
    - `request_id`：加班申请ID

    **访问控制**（详情页特殊逻辑）：
    - 申请本人：可直接查看
    - 超级管理员（`is_superuser = True`）：可查看任意申请
    - admin / hr / manager 角色：通过 `EmployeeRole` 表校验，有权查看所有申请
    - 其他用户查看他人申请：返回 403 Forbidden

    **响应**：`OvertimeRequest` 对象；若申请不存在返回 404

    **权限**：登录用户（`get_current_user`），详细权限见上方访问控制说明

    **实现细节**：
    - 通过 lazy import `app.models.employee_role.EmployeeRole` 避免循环导入
    - 若 `EmployeeRole` 模型不存在（ImportError），默认拒绝非本人的访问请求
    """
    result = await db.execute(
        select(OvertimeRequest).where(OvertimeRequest.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="加班申请不存在")
    # 允许本人、超级管理员、hr/admin/manager 角色查看
    if req.employee_id != current_user.id and not current_user.is_superuser:
        # check via roles (lazy import to avoid circular)
        try:
            from app.models.employee_role import EmployeeRole
            role_result = await db.execute(
                select(EmployeeRole).where(
                    EmployeeRole.employee_id == current_user.id,
                    EmployeeRole.role_name.in_(["admin", "hr", "manager"]),
                    EmployeeRole.is_active == True,
                )
            )
            if not role_result.scalars().first():
                raise HTTPException(status_code=403, detail="无权查看")
        except ImportError:
            raise HTTPException(status_code=403, detail="无权查看")
    return req


@router.put("/{request_id}/cancel", summary="撤销加班申请")
async def cancel_overtime(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    PUT /overtime/{request_id}/cancel

    员工撤销自己提交的加班申请。仅限以下条件：
    1. 申请人须为当前登录用户（不能撤销他人申请，否则返回 403）
    2. 申请须处于 `pending`（待审批）状态（已审批/已拒绝不可撤销，否则返回 400）

    **Path 参数**：
    - `request_id`：加班申请ID

    **响应**：
    - 成功：`{"message": "已撤销"}`
    - 申请不存在：404
    - 非本人申请：403
    - 状态不是 pending：400

    **权限**：登录用户（`get_current_user`），仅可撤销自己的申请

    **状态变更**：`pending` → `cancelled`
    """
    result = await db.execute(
        select(OvertimeRequest).where(OvertimeRequest.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="加班申请不存在")
    if req.employee_id != current_user.id:
        raise HTTPException(status_code=403, detail="只能撤销自己的申请")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail="只有待审批状态可以撤销")
    req.status = "cancelled"
    await db.flush()
    return {"message": "已撤销"}


# ============================================================
# 加班申请审批端点
# ============================================================

@router.put("/{request_id}/approve", summary="审批加班申请")
async def approve_overtime(
    request_id: int,
    data: OvertimeApproveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    PUT /overtime/{request_id}/approve

    管理层审批加班申请（批准或拒绝）。
    申请须处于 `pending`（待审批）状态，否则返回 400。

    **Path 参数**：
    - `request_id`：加班申请ID

    **请求体**（`OvertimeApproveRequest`）：
    - `action`：审批动作
      - `approve`：批准申请
      - `reject`：拒绝申请
    - `reject_reason`（可选）：拒绝原因（action 为 reject 时建议填写）

    **状态变更**：
    - approve 操作：
      - `status`: `pending` → `approved`
      - `approval_status`: `pending` → `approved`
      - 记录 `approver_id`（当前审批人ID）和 `approved_at`（审批时间）
    - reject 操作：
      - `status`: `pending` → `rejected`
      - `approval_status`: `pending` → `rejected`
      - 记录 `approver_id`、`approved_at` 和 `reject_reason`

    **响应**：审批后的 `OvertimeRequest` 对象；若不存在返回 404；状态不符返回 400

    **权限**：需要 admin / hr / manager 角色（`require_roles`）
    """
    result = await db.execute(
        select(OvertimeRequest).where(OvertimeRequest.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="加班申请不存在")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail="该申请不在待审批状态")
    # 记录审批时间（使用 timezone.utc 确保时区感知，避免 PostgreSQL TIMESTAMPTZ 插入错误）
    now = datetime.now(timezone.utc)
    if data.action == "approve":
        req.status = "approved"
        req.approval_status = "approved"
        req.approver_id = current_user.id
        req.approved_at = now
        await db.flush()
        await AttendanceRecordService.sync_approved_overtime_to_attendance(db, req)
    else:
        req.status = "rejected"
        req.approval_status = "rejected"
        req.approver_id = current_user.id
        req.reject_reason = data.reject_reason
        req.approved_at = now
    await db.flush()
    return req
