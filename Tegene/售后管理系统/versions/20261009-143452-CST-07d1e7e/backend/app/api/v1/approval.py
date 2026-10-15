"""
审批引擎 API 路由模块

路由前缀（注册于 router.py）: /api/v1/approval

本模块实现售后管理系统的通用审批引擎，支持多业务场景（请假、加班、薪资调整等）
的审批流定义、实例管理及审批操作。

架构概述：
  审批流程分为两层：
    1. 流程定义层（Flow + Node）：配置层，定义流程模板和审批节点
    2. 实例层（Instance + Record）：运行层，记录具体审批申请及其审批动作

  business_type 字段决定审批完成后回调哪个业务模块（如 leave、overtime、payroll_adjustment）。

端点清单：

  流程定义管理：
       POST   /approval/flows              — 创建审批流程定义
       GET    /approval/flows              — 查询流程列表（可按模块/状态筛选）
       GET    /approval/flows/{id}         — 获取流程详情（含节点列表）
       PUT    /approval/flows/{id}         — 更新流程定义
       DELETE /approval/flows/{id}         — 删除流程定义

  审批节点管理：
       POST   /approval/nodes              — 创建审批节点（附加在某个流程下）
       GET    /approval/nodes/{id}         — 获取节点详情
       PUT    /approval/nodes/{id}         — 更新节点（如修改审批人、顺序）
       DELETE /approval/nodes/{id}         — 删除节点

  批量操作：
       POST   /approval/bulk-action        — 批量审批通过或批量拒绝

  委托代理管理：
       GET    /approval/delegates          — 查询我的代理授权记录
       POST   /approval/delegates          — 创建代理授权（授权他人代为审批）
       DELETE /approval/delegates/{id}     — 撤销代理授权

  转签操作：
       POST   /approval/transfer           — 将当前审批任务转给他人

  实例管理：
       POST   /approval/instances          — 提交审批申请（自动匹配流程）
       GET    /approval/instances/{id}     — 获取审批实例详情（含审批记录流水）
       GET    /approval/my-pending         — 我的待审批列表（当前用户作为审批人）
       GET    /approval/my-approved        — 我已审批过的列表
       GET    /approval/my-submitted       — 我提交的审批申请列表
       POST   /approval/instances/{id}/process  — 处理审批（通过/拒绝）
       POST   /approval/instances/{id}/withdraw — 撤回申请（申请人撤回）

权限说明：
  - 流程定义/节点的增删改端点要求 admin 角色（require_roles("admin")）
  - 其余端点要求已登录（get_current_user），无角色限制
  - 写操作中：当前用户 ID 从 JWT 中自动注入，不需要在请求体中传递

对应 Service：app.services.approval（模块别名 svc）
"""

import json
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.deps import get_current_user, get_user_role_names, require_roles
from app.models.approval import ApprovalFormField, ApprovalInstance, ApprovalTemplateVersion, ApprovalType
from app.models.employee import Employee
from app.models.organization import Department
from app.models.payroll import Company
from app.schemas.approval import (
    ApprovalActionRequest,
    ApprovalApplicationSubmit,
    ApprovalDelegateCreate,
    ApprovalDelegateOut,
    ApprovalDraftApplicantPreviewRequest,
    ApprovalFlowPreviewRequest,
    ApprovalFormFieldCreate,
    ApprovalFormFieldOut,
    ApprovalFormFieldUpdate,
    ApprovalFlowCreate,
    ApprovalFlowOut,
    ApprovalFlowPreviewOut,
    ApprovalFlowUpdate,
    ApprovalFlowWithNodes,
    ApprovalInstanceCreate,
    ApprovalInstanceDetail,
    ApprovalCenterBucket,
    ApprovalMobileCenterOut,
    ApprovalInstanceOut,
    ApprovalInstancePage,
    ApprovalReminderResult,
    ApprovalNodeCreate,
    ApprovalNodeOut,
    ApprovalNodeUpdate,
    ApprovalRecordOut,
    ApprovalTemplateGroup,
    ApprovalTemplateBulkDisableRequest,
    ApprovalTemplateBulkDisableResult,
    ApprovalTemplateConfigSave,
    ApprovalTemplateCopyRequest,
    ApprovalTemplateVersionOut,
    ApprovalTypeCreate,
    ApprovalTypeOut,
    ApprovalTypeUpdate,
    BulkApprovalRequest,
    BulkApprovalResult,
    TransferApprovalRequest,
)
from app.services import approval as svc

router = APIRouter()


# ───────────────────── Flows ─────────────────────

def _approval_type_out(item: object) -> ApprovalTypeOut:
    form_fields = getattr(item, "__dict__", {}).get("form_fields", [])
    normalized_form_fields = svc.normalized_template_form_fields(item, list(form_fields or []))
    return ApprovalTypeOut.model_validate(
        {
            "id": getattr(item, "id"),
            "name": getattr(item, "name"),
            "business_code": getattr(item, "business_code"),
            "category": getattr(item, "category"),
            "category_key": getattr(item, "category_key", None),
            "scope": getattr(item, "scope", "mobile"),
            "is_active": getattr(item, "is_active", True),
            "sort_order": getattr(item, "sort_order", 0),
            "icon": getattr(item, "icon", None),
            "icon_key": getattr(item, "icon_key", None),
            "icon_tone": getattr(item, "icon_tone", None),
            "description": getattr(item, "description", None),
            "print_format": getattr(item, "print_format", None),
            "status": getattr(item, "status", "draft"),
            "version": getattr(item, "version", 1),
            "permission_rules": getattr(item, "permission_rules", None),
            "exception_rules": getattr(item, "exception_rules", None),
            "auto_approval_rule": getattr(item, "auto_approval_rule", None),
            "created_at": getattr(item, "created_at"),
            "updated_at": getattr(item, "updated_at", None),
            "form_fields": normalized_form_fields,
        }
    )


async def _approval_type_for_instance(db: AsyncSession, instance: object) -> ApprovalType | None:
    template_version_id = getattr(instance, "template_version_id", None)
    if template_version_id:
        version_result = await db.execute(
            select(ApprovalTemplateVersion).where(ApprovalTemplateVersion.id == template_version_id)
        )
        version = version_result.scalar_one_or_none()
        approval_type_id = getattr(version, "approval_type_id", None)
        if approval_type_id:
            type_result = await db.execute(
                select(ApprovalType)
                .options(selectinload(ApprovalType.form_fields))
                .where(ApprovalType.id == approval_type_id)
            )
            approval_type = type_result.scalar_one_or_none()
            if approval_type:
                return approval_type

    module = getattr(instance, "module", None)
    if module:
        type_result = await db.execute(
            select(ApprovalType)
            .options(selectinload(ApprovalType.form_fields))
            .where(ApprovalType.business_code.in_(svc._flow_module_candidates(module)))
            .order_by(ApprovalType.id.desc())
            .limit(1)
        )
        return type_result.scalar_one_or_none()
    return None


def _attachment_display_text(value: Any) -> str:
    if isinstance(value, list):
        labels = [_attachment_display_text(item) for item in value]
        return "、".join(item for item in labels if item)
    if not isinstance(value, dict):
        return ""
    label = value.get("name") or value.get("filename") or value.get("original_filename")
    if label:
        return str(label)
    url = str(value.get("url") or value.get("path") or value.get("file_url") or "").strip()
    if not url:
        return ""
    return url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or "附件"


def _form_field_int_ids(value: Any) -> list[int]:
    values = value if isinstance(value, list) else [value]
    ids: list[int] = []
    seen: set[int] = set()
    for item in values:
        if isinstance(item, dict):
            item = item.get("id") if item.get("id") is not None else item.get("value")
        try:
            item_id = int(item)
        except (TypeError, ValueError):
            continue
        if item_id in seen:
            continue
        seen.add(item_id)
        ids.append(item_id)
    return ids


def _duration_value_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("display", "label", "text", "value", "duration", "days", "hours"):
            item = value.get(key)
            if item not in (None, ""):
                return str(item).strip()
        return ""
    if isinstance(value, list):
        return ""
    return str(value).strip() if value not in (None, "") else ""


def _duration_unit_label(options: Any, field_code: str = "") -> str:
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except Exception:
            options = {}
    options = options if isinstance(options, dict) else {}
    scale = str(options.get("time_scale") or options.get("unit") or "").strip().lower()
    if scale in {"hour", "hours", "小时"}:
        return "小时"
    if scale in {"minute", "minutes", "分钟"}:
        return "分钟"
    if scale in {"day", "days", "workday", "natural_day", "天"}:
        return "天"
    component = str(options.get("attendance_component") or options.get("status") or "").strip().lower()
    if component in {"leave", "outside", "business_trip", "travel"}:
        return "天"
    if options.get("duration_mode") in {"natural_day", "workday"}:
        return "天"
    field_text = str(field_code or "").lower()
    if any(alias in field_text for alias in ("trip", "leave", "outside", "day")):
        return "天"
    return "小时"


def _duration_display_text(value: Any, options: Any = None, field_code: str = "") -> str:
    text = _duration_value_text(value)
    if not text or any(unit in text for unit in ("天", "小时", "分钟")):
        return text
    return f"{text} {_duration_unit_label(options, field_code)}"


async def _form_field_display_values(
    db: AsyncSession,
    form_data: dict[str, Any],
    field_types: dict[str, str],
    field_options: dict[str, Any] | None = None,
) -> dict[str, str]:
    member_ids: set[int] = set()
    department_ids: set[int] = set()
    company_ids: set[int] = set()
    for key, value in form_data.items():
        field_type = field_types.get(key)
        if field_type == "member":
            member_ids.update(_form_field_int_ids(value))
        elif field_type == "department":
            department_ids.update(_form_field_int_ids(value))
        elif field_type == "company":
            company_ids.update(_form_field_int_ids(value))

    employee_names: dict[int, str] = {}
    if member_ids:
        result = await db.execute(select(Employee.id, Employee.name).where(Employee.id.in_(member_ids)))
        employee_names = {int(row[0]): str(row[1]) for row in result.all() if row[1]}

    department_names: dict[int, str] = {}
    if department_ids:
        result = await db.execute(select(Department.id, Department.name).where(Department.id.in_(department_ids)))
        department_names = {int(row[0]): str(row[1]) for row in result.all() if row[1]}

    company_names: dict[int, str] = {}
    if company_ids:
        result = await db.execute(select(Company.id, Company.name, Company.short_name).where(Company.id.in_(company_ids)))
        company_names = {
            int(row[0]): str(row[2] or row[1])
            for row in result.all()
            if row[1] or row[2]
        }

    display_values: dict[str, str] = {}
    for key, value in form_data.items():
        field_type = field_types.get(key)
        value_ids = _form_field_int_ids(value)
        if field_type == "member" and value_ids:
            display_values[key] = "、".join(
                employee_names.get(value_id, f"员工 #{value_id}") for value_id in value_ids
            )
        elif field_type == "department" and value_ids:
            display_values[key] = "、".join(
                department_names.get(value_id, f"部门 #{value_id}") for value_id in value_ids
            )
        elif field_type == "company" and value_ids:
            display_values[key] = "、".join(
                company_names.get(value_id, f"公司 #{value_id}") for value_id in value_ids
            )
        elif field_type == "attachment":
            display_text = _attachment_display_text(value)
            if display_text:
                display_values[key] = display_text
        elif field_type == "duration":
            display_text = _duration_display_text(value, (field_options or {}).get(key), key)
            if display_text:
                display_values[key] = display_text
    return display_values


async def _approval_instance_out(db: AsyncSession, instance: object) -> ApprovalInstanceOut:
    step_summary = svc.approval_instance_step_summary(instance)
    out = ApprovalInstanceOut.model_validate(
        {
            "id": getattr(instance, "id"),
            "template_version_id": getattr(instance, "template_version_id", None)
            if isinstance(getattr(instance, "template_version_id", None), int)
            else None,
            "flow_id": getattr(instance, "flow_id"),
            "applicant_id": getattr(instance, "applicant_id"),
            "module": getattr(instance, "module"),
            "business_id": getattr(instance, "business_id"),
            "business_type": getattr(instance, "business_type"),
            "summary": getattr(instance, "summary", None)
            if isinstance(getattr(instance, "summary", None), str)
            else None,
            "form_data": getattr(instance, "form_data", None)
            if isinstance(getattr(instance, "form_data", None), dict)
            else None,
            "flow_snapshot": getattr(instance, "flow_snapshot", None)
            if isinstance(getattr(instance, "flow_snapshot", None), str)
            else None,
            "current_node_order": getattr(instance, "current_node_order"),
            "current_node_name": step_summary.get("current_node_name"),
            "current_node_status": step_summary.get("current_node_status"),
            "current_node_status_label": step_summary.get("current_node_status_label"),
            "status": getattr(instance, "status"),
            "created_at": getattr(instance, "created_at"),
            "updated_at": getattr(instance, "updated_at"),
        }
    )
    applicant_id = getattr(instance, "applicant_id", None)
    if applicant_id:
        applicant_result = await db.execute(
            select(
                Employee.name,
                Employee.department_id,
                Department.name,
                Employee.company_id,
                Employee.company,
                Company.name,
                Company.short_name,
            )
            .outerjoin(Department, Employee.department_id == Department.id)
            .outerjoin(Company, Employee.company_id == Company.id)
            .where(Employee.id == applicant_id)
        )
        applicant_row = applicant_result.first()
        if applicant_row:
            applicant_name = applicant_row[0]
            if isinstance(applicant_name, str):
                out.applicant_name = applicant_name
            out.department_id = applicant_row[1]
            department_name = applicant_row[2]
            if isinstance(department_name, str):
                out.department_name = department_name
            out.company_id = applicant_row[3]
            company_name = applicant_row[6] or applicant_row[5] or applicant_row[4]
            if isinstance(company_name, str):
                out.company_name = company_name

    approval_type = await _approval_type_for_instance(db, instance)
    if approval_type:
        approval_type_name = getattr(approval_type, "name", None)
        if isinstance(approval_type_name, str):
            out.approval_type_name = approval_type_name
        fields: list[ApprovalFormField] = list(getattr(approval_type, "form_fields", []) or [])
        out.form_field_labels = {
            str(getattr(field, "code", "")): str(getattr(field, "label", ""))
            for field in fields
            if getattr(field, "code", None) and getattr(field, "label", None)
        }
        out.form_field_types = {
            str(getattr(field, "code", "")): str(getattr(field, "field_type", ""))
            for field in fields
            if getattr(field, "code", None) and getattr(field, "field_type", None)
        }
        field_options = {
            str(getattr(field, "code", "")): svc._field_options(field)
            for field in fields
            if getattr(field, "code", None)
        }
    else:
        field_options = {}

    form_data = getattr(instance, "form_data", None)
    if isinstance(form_data, dict):
        out.form_field_display_values = await _form_field_display_values(db, form_data, out.form_field_types, field_options)
    return out


async def _approval_instance_detail(db: AsyncSession, instance: object) -> ApprovalInstanceDetail:
    base = await _approval_instance_out(db, instance)
    detail = ApprovalInstanceDetail.model_validate(base)
    detail.records = [
        ApprovalRecordOut.model_validate(item)
        for item in (getattr(instance, "records", None) or [])
    ]
    detail.progress_nodes = await svc.build_instance_progress(db, instance)
    return detail


@router.post("/types", response_model=ApprovalTypeOut, status_code=201)
async def create_approval_type(
    data: ApprovalTypeCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
) -> ApprovalTypeOut:
    item = await svc.create_approval_type(db, data)
    return _approval_type_out(item)


@router.get("/types", response_model=list[ApprovalTypeOut])
async def list_approval_types(
    category: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
) -> list[ApprovalTypeOut]:
    items = await svc.list_approval_types(db, category=category, is_active=is_active)
    return [_approval_type_out(item) for item in items]


@router.post("/types/bulk-disable", response_model=ApprovalTemplateBulkDisableResult)
async def bulk_disable_approval_types(
    data: ApprovalTemplateBulkDisableRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
) -> ApprovalTemplateBulkDisableResult:
    result = await svc.bulk_disable_approval_types(db, data.ids, _current_user)
    return ApprovalTemplateBulkDisableResult(**result)


@router.get("/control-library", response_model=list[dict[str, Any]])
async def get_control_library(
    _current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
) -> list[dict[str, Any]]:
    return svc.list_template_control_definitions()


@router.get("/types/{approval_type_id}/flow-preview", response_model=ApprovalFlowPreviewOut)
async def get_template_flow_preview(
    approval_type_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalFlowPreviewOut:
    return await svc.build_template_flow_preview(db, approval_type_id, current_user.id)


@router.post("/types/{approval_type_id}/flow-preview", response_model=ApprovalFlowPreviewOut)
async def get_template_flow_preview_with_form_data(
    approval_type_id: int,
    data: ApprovalFlowPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalFlowPreviewOut:
    return await svc.build_template_flow_preview(db, approval_type_id, current_user.id, data.form_data)


@router.post("/types/{approval_type_id}/flow-preview/draft-applicant", response_model=ApprovalFlowPreviewOut)
async def get_template_flow_preview_for_draft_applicant(
    approval_type_id: int,
    data: ApprovalDraftApplicantPreviewRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
) -> ApprovalFlowPreviewOut:
    return await svc.build_template_flow_preview_for_draft_applicant(db, approval_type_id, data)


@router.post("/types/{approval_type_id}/copy", response_model=ApprovalTypeOut, status_code=201)
async def copy_template_config(
    approval_type_id: int,
    data: ApprovalTemplateCopyRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
) -> ApprovalTypeOut:
    item = await svc.copy_template_config(db, approval_type_id, data, _current_user)
    return _approval_type_out(item)


@router.get("/types/{approval_type_id}/export", response_model=dict[str, Any])
async def export_template_config(
    approval_type_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
) -> dict[str, Any]:
    return await svc.export_template_config(db, approval_type_id)


@router.get("/types/{approval_type_id}", response_model=ApprovalTypeOut)
async def get_approval_type(
    approval_type_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
) -> ApprovalTypeOut:
    item = await svc.get_approval_type(db, approval_type_id)
    return _approval_type_out(item)


@router.get("/types/{approval_type_id}/versions", response_model=list[ApprovalTemplateVersionOut])
async def list_template_versions(
    approval_type_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
) -> list[ApprovalTemplateVersionOut]:
    exists_result = await db.execute(select(ApprovalType.id).where(ApprovalType.id == approval_type_id))
    if exists_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="template not found")
    result = await db.execute(
        select(ApprovalTemplateVersion)
        .where(ApprovalTemplateVersion.approval_type_id == approval_type_id)
        .order_by(ApprovalTemplateVersion.version.desc(), ApprovalTemplateVersion.id.desc())
    )
    return [ApprovalTemplateVersionOut.model_validate(item) for item in result.scalars().all()]


@router.put("/types/{approval_type_id}", response_model=ApprovalTypeOut)
async def update_approval_type(
    approval_type_id: int,
    data: ApprovalTypeUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "manager", "finance", "asset_admin")),
) -> ApprovalTypeOut:
    item = await svc.update_approval_type(db, approval_type_id, data, _current_user)
    return _approval_type_out(item)


@router.delete("/types/{approval_type_id}", status_code=204)
async def delete_approval_type(
    approval_type_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
) -> None:
    await svc.delete_approval_type(db, approval_type_id, _current_user)


@router.post("/template-configs", response_model=ApprovalTypeOut, status_code=201)
async def save_template_config(
    data: ApprovalTemplateConfigSave,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
) -> ApprovalTypeOut:
    item = await svc.save_template_config(db, data, _current_user)
    return _approval_type_out(item)


@router.post("/template-configs/{approval_type_id}/publish", response_model=ApprovalTypeOut)
async def publish_template(
    approval_type_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
) -> ApprovalTypeOut:
    item = await svc.publish_template(db, approval_type_id, _current_user)
    return _approval_type_out(item)


@router.get("/types/{approval_type_id}/fields", response_model=list[ApprovalFormFieldOut])
async def list_form_fields(
    approval_type_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
) -> list[ApprovalFormFieldOut]:
    fields = await svc.list_form_fields(db, approval_type_id)
    return [ApprovalFormFieldOut.model_validate(field) for field in fields]


@router.post("/types/{approval_type_id}/fields", response_model=ApprovalFormFieldOut, status_code=201)
async def create_form_field(
    approval_type_id: int,
    data: ApprovalFormFieldCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
) -> ApprovalFormFieldOut:
    field = await svc.create_form_field(db, approval_type_id, data)
    return ApprovalFormFieldOut.model_validate(field)


@router.put("/fields/{field_id}", response_model=ApprovalFormFieldOut)
async def update_form_field(
    field_id: int,
    data: ApprovalFormFieldUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
) -> ApprovalFormFieldOut:
    field = await svc.update_form_field(db, field_id, data)
    return ApprovalFormFieldOut.model_validate(field)


@router.delete("/fields/{field_id}", status_code=204)
async def delete_form_field(
    field_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
) -> None:
    await svc.delete_form_field(db, field_id)


@router.post("/applications", response_model=ApprovalInstanceOut, status_code=201)
async def submit_application(
    data: ApprovalApplicationSubmit,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalInstanceOut:
    instance = await svc.submit_dynamic_application(db, data, current_user.id)
    return ApprovalInstanceOut.model_validate(instance)


@router.post("/flows", response_model=ApprovalFlowOut, status_code=201)
async def create_flow(
    data: ApprovalFlowCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin")),
) -> ApprovalFlowOut:
    """
    POST /approval/flows — 创建审批流程定义

    用途：定义一个新的审批流程模板，后续提交审批申请时会根据 business_type 自动匹配此流程。
          流程定义后需通过 POST /approval/nodes 添加审批节点方可生效。

    请求体（ApprovalFlowCreate）：
        - name: str             — 流程名称（如"请假审批流程"）
        - business_type: str    — 业务类型标识（如 leave / overtime / payroll_adjustment），
                                  与业务模块回调机制对应
        - module: str           — 所属功能模块（如 leave / performance），用于筛选展示
        - description: Optional[str] — 流程说明
        - is_active: bool       — 是否启用（默认 True）

    响应（201 Created）：ApprovalFlowOut — 含 id 及所有字段

    权限：仅限 admin 角色（require_roles("admin")）

    Service：svc.create_flow(db, data)
    """
    flow = await svc.create_flow(db, data)
    return ApprovalFlowOut.model_validate(flow)


@router.get("/flows", response_model=list[ApprovalFlowWithNodes])
async def list_flows(
    module: Optional[str] = Query(None, description="按模块筛选"),
    is_active: Optional[bool] = Query(None, description="按启用状态筛选"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> list[ApprovalFlowWithNodes]:
    """
    GET /approval/flows — 查询审批流程定义列表

    用途：获取所有审批流程模板，含每个流程下的节点列表。
          常用于管理后台的流程配置页面。

    Query 参数：
        - module: str      — 按功能模块筛选（如 leave / attendance，可选）
        - is_active: bool  — true 只返回已启用的流程，false 只返回已禁用的（可选）

    响应（200 OK）：list[ApprovalFlowWithNodes]
        每项包含流程基本信息 + nodes: list[ApprovalNodeOut]（节点列表，按 order 排序）

    权限：任意已登录用户（get_current_user）

    Service：svc.list_flows(db, module, is_active)
    """
    flows = await svc.list_flows(db, module=module, is_active=is_active)
    return [ApprovalFlowWithNodes.model_validate(f) for f in flows]


@router.get("/templates", response_model=list[ApprovalTemplateGroup])
async def list_templates(
    include_inactive: bool = Query(False, description="管理端是否包含已停用模板"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> list[ApprovalTemplateGroup]:
    roles = await get_user_role_names(db, _current_user)
    setattr(_current_user, "roles", list(roles))
    can_manage_templates = bool(roles & {"admin", "hr"})
    templates = await svc.list_approval_templates(
        db,
        _current_user,
        include_inactive=include_inactive and can_manage_templates,
    )
    return templates


@router.get("/flows/{flow_id}", response_model=ApprovalFlowWithNodes)
async def get_flow(
    flow_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> ApprovalFlowWithNodes:
    """
    GET /approval/flows/{flow_id} — 获取审批流程详情（含节点）

    用途：查询单个审批流程的完整配置，包含所有关联节点的详细信息。

    路径参数：
        - flow_id: int — 审批流程主键 ID

    响应（200 OK）：ApprovalFlowWithNodes — 流程信息 + 完整节点列表
    响应（404 Not Found）：流程不存在时由 svc 抛出

    权限：任意已登录用户（get_current_user）

    Service：svc.get_flow(db, flow_id)
    """
    flow = await svc.get_flow(db, flow_id)
    return ApprovalFlowWithNodes.model_validate(flow)


@router.put("/flows/{flow_id}", response_model=ApprovalFlowOut)
async def update_flow(
    flow_id: int,
    data: ApprovalFlowUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin")),
) -> ApprovalFlowOut:
    """
    PUT /approval/flows/{flow_id} — 更新审批流程定义

    用途：修改已有审批流程的基本配置（如流程名称、启停状态等）。
          注意：修改流程定义不会影响已在进行中的审批实例。

    路径参数：
        - flow_id: int — 目标审批流程主键 ID

    请求体（ApprovalFlowUpdate）：与 Create 字段相同，全部可选

    响应（200 OK）：ApprovalFlowOut — 更新后的流程数据（不含节点列表）
    响应（404 Not Found）：流程不存在时由 svc 抛出

    权限：仅限 admin 角色（require_roles("admin")）

    Service：svc.update_flow(db, flow_id, data)
    """
    flow = await svc.update_flow(db, flow_id, data)
    return ApprovalFlowOut.model_validate(flow)


@router.delete("/flows/{flow_id}", status_code=204)
async def delete_flow(
    flow_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin")),
) -> None:
    """
    DELETE /approval/flows/{flow_id} — 删除审批流程定义

    用途：删除整个审批流程模板。若存在进行中的审批实例，建议先将流程设为
          is_active=False 而非直接删除。

    路径参数：
        - flow_id: int — 目标审批流程主键 ID

    响应（204 No Content）：删除成功，无响应体
    响应（404 Not Found）：流程不存在时由 svc 抛出

    权限：仅限 admin 角色（require_roles("admin")）

    Service：svc.delete_flow(db, flow_id)
    """
    await svc.delete_flow(db, flow_id)


# ───────────────────── Nodes ─────────────────────


@router.post("/nodes", response_model=ApprovalNodeOut, status_code=201)
async def create_node(
    data: ApprovalNodeCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin")),
) -> ApprovalNodeOut:
    """
    POST /approval/nodes — 创建审批节点

    用途：在指定流程下添加一个审批节点。节点按 order 字段顺序执行，支持
          审批节点（node_type=approval）、通知节点（notify）、自动节点（auto）三种类型。

    请求体（ApprovalNodeCreate）：
        - flow_id: int          — 所属审批流程 ID
        - node_name: str        — 节点名称（如"部门经理审批"）
        - node_type: str        — 节点类型：approval（审批）/ notify（通知）/ auto（自动通过）
        - approver_id: Optional[int]  — 固定审批人员工 ID（node_type=approval 时）
        - approver_role: Optional[str] — 按角色动态指定审批人（如 manager）
        - order: int            — 节点执行顺序（从 1 开始）
        - is_required: bool     — 是否必审节点（false 时可跳过）

    响应（201 Created）：ApprovalNodeOut — 含 id 及所有字段

    权限：仅限 admin 角色（require_roles("admin")）

    Service：svc.create_node(db, data)
    """
    node = await svc.create_node(db, data)
    return ApprovalNodeOut.model_validate(node)


@router.get("/nodes/{node_id}", response_model=ApprovalNodeOut)
async def get_node(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> ApprovalNodeOut:
    """
    GET /approval/nodes/{node_id} — 获取审批节点详情

    用途：查询单个审批节点的配置信息。

    路径参数：
        - node_id: int — 审批节点主键 ID

    响应（200 OK）：ApprovalNodeOut
    响应（404 Not Found）：节点不存在时由 svc 抛出

    权限：任意已登录用户（get_current_user）

    Service：svc.get_node(db, node_id)
    """
    node = await svc.get_node(db, node_id)
    return ApprovalNodeOut.model_validate(node)


@router.put("/nodes/{node_id}", response_model=ApprovalNodeOut)
async def update_node(
    node_id: int,
    data: ApprovalNodeUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin")),
) -> ApprovalNodeOut:
    """
    PUT /approval/nodes/{node_id} — 更新审批节点配置

    用途：修改节点的审批人、执行顺序、节点类型等配置。
          修改只影响新创建的审批实例，已在进行中的实例不受影响。

    路径参数：
        - node_id: int — 目标节点主键 ID

    请求体（ApprovalNodeUpdate）：与 Create 字段相同，全部可选

    响应（200 OK）：ApprovalNodeOut — 更新后的节点数据
    响应（404 Not Found）：节点不存在时由 svc 抛出

    权限：仅限 admin 角色（require_roles("admin")）

    Service：svc.update_node(db, node_id, data)
    """
    node = await svc.update_node(db, node_id, data)
    return ApprovalNodeOut.model_validate(node)


@router.delete("/nodes/{node_id}", status_code=204)
async def delete_node(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin")),
) -> None:
    """
    DELETE /approval/nodes/{node_id} — 删除审批节点

    用途：从流程中移除指定节点。操作后需检查流程中剩余节点的 order 是否仍连续。

    路径参数：
        - node_id: int — 目标节点主键 ID

    响应（204 No Content）：删除成功，无响应体
    响应（404 Not Found）：节点不存在时由 svc 抛出

    权限：仅限 admin 角色（require_roles("admin")）

    Service：svc.delete_node(db, node_id)
    """
    await svc.delete_node(db, node_id)


# ───────────────────── Bulk Action ─────────────────────


@router.post("/bulk-action", response_model=BulkApprovalResult)
async def bulk_action(
    data: BulkApprovalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> BulkApprovalResult:
    """
    POST /approval/bulk-action — 批量审批操作

    用途：对多个审批实例同时执行通过或拒绝操作，提升 HR/管理人员处理大量审批申请的效率。
          当前用户必须是这些实例对应节点的有效审批人，否则单条操作会被跳过。

    请求体（BulkApprovalRequest）：
        - instance_ids: list[int]  — 要批量操作的审批实例 ID 列表
        - action: str              — 操作类型：approve（通过）/ reject（拒绝）
        - comment: Optional[str]   — 批量审批意见（所有实例共用同一条备注）

    响应（200 OK）：BulkApprovalResult
        {
            "success_count": int      — 成功处理的实例数量
            "failed_count": int       — 处理失败（如权限不足、状态不允许）的数量
            "failed_ids": list[int]   — 失败的实例 ID 列表
        }

    权限：任意已登录用户（get_current_user）
          实际权限由 Service 层逐条校验（当前用户是否为该节点审批人）

    Service：svc.bulk_approve(db, instance_ids, approver_id=current_user.id, action, comment)
    """
    return await svc.bulk_approve(
        db,
        instance_ids=data.instance_ids,
        approver_id=current_user.id,
        action=data.action,
        comment=data.comment,
    )


# ───────────────────── Delegates ─────────────────────


@router.get("/delegates", response_model=list[ApprovalDelegateOut])
async def list_delegates(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[ApprovalDelegateOut]:
    """
    GET /approval/delegates — 查询我的代理授权列表

    用途：查看当前用户（作为委托方）已创建的所有代理授权记录，包括有效期和代理人信息。

    Query 参数：无（自动使用当前登录用户的 ID 过滤）

    响应（200 OK）：list[ApprovalDelegateOut]
        每项含：delegate_id、delegatee_id（代理人）、delegatee_name、
               start_date、end_date、is_active、created_at 等

    权限：任意已登录用户（get_current_user）
          只返回当前用户自己创建的委托记录

    Service：svc.list_delegates(db, employee_id=current_user.id)
    """
    return await svc.list_delegates(db, employee_id=current_user.id)


@router.post("/delegates", response_model=ApprovalDelegateOut, status_code=201)
async def create_delegate(
    data: ApprovalDelegateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalDelegateOut:
    """
    POST /approval/delegates — 创建审批代理授权

    用途：当前用户将自己的审批权限在指定时间段内授权给另一名员工代为处理。
          常用于出差、休假等场景下的审批权限临时转交。

    请求体（ApprovalDelegateCreate）：
        - delegatee_id: int    — 被授权代理人的员工 ID
        - start_date: date     — 代理有效期开始日期
        - end_date: date       — 代理有效期结束日期
        - flow_ids: Optional[list[int]] — 指定仅代理哪些流程（为空则代理所有流程）
        - notes: Optional[str] — 说明

    响应（201 Created）：ApprovalDelegateOut — 含 id 及代理人信息
    响应（409 Conflict）：时间段内已存在相同授权时由 svc 抛出

    权限：任意已登录用户（get_current_user）
          delegator_id 自动取自 current_user.id，不需要在请求体中传入

    Service：svc.create_delegate(db, delegator_id=current_user.id, data)
    """
    delegate = await svc.create_delegate(db, delegator_id=current_user.id, data=data)
    return await svc._delegate_to_out(db, delegate)


@router.delete("/delegates/{delegate_id}", status_code=204)
async def revoke_delegate(
    delegate_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /approval/delegates/{delegate_id} — 撤销代理授权

    用途：提前撤销当前用户创建的某条代理授权记录，被代理人随即失去对应的审批权限。

    路径参数：
        - delegate_id: int — 要撤销的代理授权记录主键 ID

    响应（204 No Content）：撤销成功，无响应体
    响应（404 Not Found）：记录不存在时由 svc 抛出
    响应（403 Forbidden）：尝试撤销他人的委托时由 svc 校验并拒绝

    权限：任意已登录用户（get_current_user）
          Service 层会校验 delegate_id 的委托方必须是当前用户（employee_id=current_user.id）

    Service：svc.revoke_delegate(db, delegate_record_id=delegate_id, employee_id=current_user.id)
    """
    await svc.revoke_delegate(db, delegate_record_id=delegate_id, employee_id=current_user.id)


# ───────────────────── Transfer ─────────────────────


@router.post("/transfer", response_model=ApprovalInstanceOut)
async def transfer_approval(
    data: TransferApprovalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalInstanceOut:
    """
    POST /approval/transfer — 转签审批

    用途：将当前用户待审批的某个实例转给其他人审批。
          转签后当前用户不再是该实例的审批人，新审批人接管处理权。
          与代理授权不同，转签是针对单个实例的一次性操作。

    请求体（TransferApprovalRequest）：
        - instance_id: int      — 要转签的审批实例 ID
        - new_approver_id: int  — 新审批人的员工 ID
        - reason: Optional[str] — 转签原因说明

    响应（200 OK）：ApprovalInstanceOut — 含更新后的当前审批人信息
    响应（403 Forbidden）：当前用户不是该实例的审批人时由 svc 拒绝
    响应（404 Not Found）：实例不存在时由 svc 抛出

    权限：任意已登录用户（get_current_user）
          current_approver_id 自动取自 current_user.id

    Service：svc.transfer_approval(db, instance_id, current_approver_id=current_user.id,
                                   new_approver_id, reason)
    """
    instance = await svc.transfer_approval(
        db,
        instance_id=data.instance_id,
        current_approver_id=current_user.id,
        new_approver_id=data.new_approver_id,
        reason=data.reason,
    )
    return ApprovalInstanceOut.model_validate(instance)


# ───────────────────── Instances ─────────────────────


@router.post("/instances", response_model=ApprovalInstanceOut, status_code=201)
async def create_instance(
    data: ApprovalInstanceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalInstanceOut:
    """
    POST /approval/instances — 提交审批申请

    用途：员工发起一个新的审批请求。Service 层会根据 business_type 自动匹配对应的审批流程，
          并按流程节点顺序初始化待审批状态。

    请求体（ApprovalInstanceCreate）：
        - business_type: str       — 业务类型（如 leave / overtime / payroll_adjustment）
        - business_id: int         — 关联业务记录的主键 ID（如 LeaveRequest.id）
        - title: str               — 审批申请标题（如"2024-03-01 病假申请"）
        - content: Optional[str]   — 申请说明/摘要（供审批人参考）
        - flow_id: Optional[int]   — 可指定流程 ID（不传则自动匹配）

    响应（201 Created）：ApprovalInstanceOut
        含：instance_id、status（pending）、current_node_id、applicant_id、created_at

    权限：任意已登录用户（get_current_user）
          applicant_id 自动取自 current_user.id

    Service：svc.create_instance(db, data, applicant_id=current_user.id)
    """
    instance = await svc.create_instance(db, data, applicant_id=current_user.id)
    return ApprovalInstanceOut.model_validate(instance)


@router.get("/instances", response_model=ApprovalInstancePage)
async def list_instances(
    group: str = Query(
        "submitted",
        pattern="^(submitted|pending|approved)$",
        description="管理后台分组：submitted=全部已提交，pending=待审批，approved=已审批完成",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    status: Optional[str] = Query(None, description="按状态筛选"),
    business_type: Optional[str] = Query(None, description="按业务类型或模块筛选"),
    start_date: Optional[date] = Query(None, description="按提交开始日期筛选"),
    end_date: Optional[date] = Query(None, description="按提交结束日期筛选"),
    applicant_id: Optional[int] = Query(None, description="按申请人员工 ID 筛选"),
    applicant_keyword: Optional[str] = Query(None, description="按申请人姓名或工号筛选"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
) -> ApprovalInstancePage:
    """
    GET /approval/instances — 管理后台审批实例列表

    用途：审批管理页查看系统内全部审批实例，并按已提交、待审批、已审批分组展示。
    """
    filters = []
    if group == "pending":
        filters.append(ApprovalInstance.status == "pending")
    elif group == "approved":
        filters.append(ApprovalInstance.status.in_(["approved", "rejected"]))
    if status:
        filters.append(ApprovalInstance.status == status)
    if business_type:
        filters.append(
            or_(
                ApprovalInstance.business_type == business_type,
                ApprovalInstance.module == business_type,
            )
        )
    if start_date:
        filters.append(ApprovalInstance.created_at >= svc._approval_archive_boundary(start_date, "start"))
    if end_date:
        filters.append(ApprovalInstance.created_at <= svc._approval_archive_boundary(end_date, "end"))
    if applicant_id:
        filters.append(ApprovalInstance.applicant_id == applicant_id)
    applicant_keyword_text = str(applicant_keyword or "").strip()
    if applicant_keyword_text:
        keyword_pattern = f"%{applicant_keyword_text}%"
        filters.append(
            ApprovalInstance.applicant_id.in_(
                select(Employee.id).where(
                    or_(
                        Employee.name.ilike(keyword_pattern),
                        Employee.employee_no.ilike(keyword_pattern),
                    )
                )
            )
        )

    total_result = await db.execute(
        select(func.count()).select_from(ApprovalInstance).where(*filters)
    )
    total = int(total_result.scalar_one() or 0)
    result = await db.execute(
        select(ApprovalInstance)
        .options(
            selectinload(ApprovalInstance.records),
            selectinload(ApprovalInstance.tasks),
        )
        .where(*filters)
        .order_by(ApprovalInstance.created_at.desc(), ApprovalInstance.id.desc())
        .offset(skip)
        .limit(limit)
    )
    items = result.scalars().all()
    return ApprovalInstancePage(
        total=total,
        items=[await _approval_instance_out(db, item) for item in items],
    )


@router.get("/instances/{instance_id}", response_model=ApprovalInstanceDetail)
async def get_instance(
    instance_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalInstanceDetail:
    """
    GET /approval/instances/{instance_id} — 获取审批实例详情

    用途：查询单个审批申请的完整信息，包括申请内容、当前状态、审批流水记录、
          每个节点的审批人及审批意见。用于审批详情页展示。

    路径参数：
        - instance_id: int — 审批实例主键 ID

    响应（200 OK）：ApprovalInstanceDetail
        在 ApprovalInstanceOut 基础上额外包含：
        - records: list[ApprovalRecordOut]  — 审批动作历史（每个节点的操作记录）
        - flow: ApprovalFlowOut             — 关联的流程定义信息
        - nodes: list[ApprovalNodeOut]      — 流程节点列表（含当前节点标记）

    响应（404 Not Found）：实例不存在时由 svc 抛出

    权限：任意已登录用户（get_current_user）

    Service：svc.get_instance(db, instance_id)
    """
    instance = await svc.get_instance(db, instance_id)
    roles = await get_user_role_names(db, current_user)
    if not (roles & {"admin", "hr"}) and not await svc.user_can_view_instance(db, instance, current_user.id):
        raise HTTPException(status_code=403, detail="无权查看该审批记录")
    await svc.mark_approval_notifications_read_for_recipient(db, current_user.id, instance.id)
    return await _approval_instance_detail(db, instance)


@router.post("/instances/{instance_id}/remind", response_model=ApprovalReminderResult)
async def remind_instance(
    instance_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalReminderResult:
    return await svc.remind_instance(db, instance_id, requester_id=current_user.id)


async def _approval_center_bucket(
    db: AsyncSession,
    total: int,
    items: list[object],
) -> ApprovalCenterBucket:
    return ApprovalCenterBucket(
        total=total,
        items=[await _approval_instance_out(db, item) for item in items],
    )


@router.get("/mobile/center", response_model=ApprovalMobileCenterOut)
async def mobile_approval_center(
    limit: int = Query(50, ge=1, le=100),
    business_type: Optional[str] = Query(None, description="按业务类型筛选"),
    start_date: Optional[date] = Query(None, description="按提交开始日期筛选"),
    end_date: Optional[date] = Query(None, description="按提交结束日期筛选"),
    keyword: Optional[str] = Query(None, description="按申请人姓名或工号筛选"),
    submitted_status: Optional[str] = Query(None, description="我发起的审批状态筛选"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalMobileCenterOut:
    """
    GET /approval/mobile/center — 移动端审批中心聚合数据。

    返回图 2 分类口径下的四个列表：待处理、已处理、我发起的、抄送我的。
    新移动端审批中心以此接口作为页面数据源，避免前端自行拼多个旧接口。
    """
    normalized_submitted_status = (submitted_status or "").strip().lower() or None
    if normalized_submitted_status == "all":
        normalized_submitted_status = None

    common_filters = {
        "business_type": business_type,
        "start_date": start_date,
        "end_date": end_date,
        "applicant_keyword": keyword,
    }

    pending_total, pending_items = await svc.list_my_pending(
        db,
        approver_id=current_user.id,
        skip=0,
        limit=limit,
        **common_filters,
    )
    processed_total, processed_items = await svc.list_my_approved(
        db,
        approver_id=current_user.id,
        skip=0,
        limit=limit,
        **common_filters,
    )
    submitted_total, submitted_items = await svc.list_my_submitted(
        db,
        applicant_id=current_user.id,
        skip=0,
        limit=limit,
        status_filter=normalized_submitted_status,
        business_type=business_type,
        start_date=start_date,
        end_date=end_date,
        applicant_keyword=keyword,
    )
    submitted_all_total, _ = await svc.list_my_submitted(
        db,
        applicant_id=current_user.id,
        skip=0,
        limit=1,
        status_filter=None,
        business_type=business_type,
        start_date=start_date,
        end_date=end_date,
        applicant_keyword=keyword,
    )
    cc_total, cc_items = await svc.list_my_cc(
        db,
        employee_id=current_user.id,
        skip=0,
        limit=limit,
        **common_filters,
    )

    submitted_status_counts: dict[str, int] = {"all": submitted_all_total}
    for status_key in ("pending", "approved", "rejected", "withdrawn", "cancelled"):
        status_total, _ = await svc.list_my_submitted(
            db,
            applicant_id=current_user.id,
            skip=0,
            limit=1,
            status_filter=status_key,
            business_type=business_type,
            start_date=start_date,
            end_date=end_date,
            applicant_keyword=keyword,
        )
        submitted_status_counts[status_key] = status_total

    return ApprovalMobileCenterOut(
        pending=await _approval_center_bucket(db, pending_total, pending_items),
        processed=await _approval_center_bucket(db, processed_total, processed_items),
        submitted=await _approval_center_bucket(db, submitted_total, submitted_items),
        cc=await _approval_center_bucket(db, cc_total, cc_items),
        counts={
            "pending": pending_total,
            "processed": processed_total,
            "submitted": submitted_all_total,
            "cc": cc_total,
        },
        submitted_status_counts=submitted_status_counts,
    )


@router.get("/my-pending", response_model=ApprovalInstancePage)
async def my_pending(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    business_type: Optional[str] = Query(None, description="按业务类型筛选"),
    start_date: Optional[date] = Query(None, description="按提交开始日期筛选"),
    end_date: Optional[date] = Query(None, description="按提交结束日期筛选"),
    applicant_id: Optional[int] = Query(None, description="按申请人员工 ID 筛选"),
    applicant_keyword: Optional[str] = Query(None, description="按申请人姓名或工号筛选"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalInstancePage:
    """
    GET /approval/my-pending — 查询我的待审批列表

    用途：返回当前用户作为审批人、尚未处理的审批实例列表。
          前端通常在"工作台"或"消息中心"展示此列表，并显示待处理数量角标。

    Query 参数：
        - skip: int   — 跳过条数（分页用，默认 0）
        - limit: int  — 每页条数（默认 20，范围 1~1000）

    响应（200 OK）：ApprovalInstancePage { total: int, items: list[ApprovalInstanceOut] }

    权限：任意已登录用户（get_current_user）
          自动以 current_user.id 作为 approver_id 过滤

    Service：svc.list_my_pending(db, approver_id=current_user.id, skip, limit)
    """
    total, items = await svc.list_my_pending(
        db,
        approver_id=current_user.id,
        skip=skip,
        limit=limit,
        business_type=business_type,
        start_date=start_date,
        end_date=end_date,
        applicant_id=applicant_id,
        applicant_keyword=applicant_keyword,
    )
    return ApprovalInstancePage(
        total=total,
        items=[await _approval_instance_out(db, i) for i in items],
    )


@router.get("/my-approved", response_model=ApprovalInstancePage)
async def my_approved(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    business_type: Optional[str] = Query(None, description="按业务类型筛选"),
    start_date: Optional[date] = Query(None, description="按提交开始日期筛选"),
    end_date: Optional[date] = Query(None, description="按提交结束日期筛选"),
    applicant_id: Optional[int] = Query(None, description="按申请人员工 ID 筛选"),
    applicant_keyword: Optional[str] = Query(None, description="按申请人姓名或工号筛选"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalInstancePage:
    """
    GET /approval/my-approved — 查询我已审批的列表

    用途：返回当前用户历史上已经审批过（无论通过还是拒绝）的审批实例列表。
          数据来源于 ApprovalRecord 表中存在 approver_id=current_user.id 的实例。

    Query 参数：
        - skip: int   — 跳过条数（分页用，默认 0）
        - limit: int  — 每页条数（默认 20，范围 1~1000）

    响应（200 OK）：ApprovalInstancePage { total: int, items: list[ApprovalInstanceOut] }

    权限：任意已登录用户（get_current_user）
          自动以 current_user.id 作为 approver_id 过滤

    Service：svc.list_my_approved(db, approver_id=current_user.id, skip, limit)
    """
    total, items = await svc.list_my_approved(
        db,
        approver_id=current_user.id,
        skip=skip,
        limit=limit,
        business_type=business_type,
        start_date=start_date,
        end_date=end_date,
        applicant_id=applicant_id,
        applicant_keyword=applicant_keyword,
    )
    return ApprovalInstancePage(
        total=total,
        items=[await _approval_instance_out(db, i) for i in items],
    )


@router.get("/my-submitted", response_model=ApprovalInstancePage)
async def my_submitted(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    status: Optional[str] = Query(None, description="按状态筛选"),
    business_type: Optional[str] = Query(None, description="按业务类型筛选"),
    start_date: Optional[date] = Query(None, description="按提交开始日期筛选"),
    end_date: Optional[date] = Query(None, description="按提交结束日期筛选"),
    applicant_id: Optional[int] = Query(None, description="按申请人员工 ID 筛选"),
    applicant_keyword: Optional[str] = Query(None, description="按申请人姓名或工号筛选"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalInstancePage:
    """
    GET /approval/my-submitted — 查询我提交的审批申请列表

    用途：返回当前用户作为申请人提交的所有审批实例，包括进行中、已通过、已拒绝和已撤回的申请。
          用于员工自助查看自己的申请记录和审批进度。

    Query 参数：
        - skip: int    — 跳过条数（分页用，默认 0）
        - limit: int   — 每页条数（默认 20，范围 1~1000）
        - status: str  — 按审批状态筛选（pending / approved / rejected / withdrawn，可选）

    响应（200 OK）：ApprovalInstancePage { total: int, items: list[ApprovalInstanceOut] }

    权限：任意已登录用户（get_current_user）
          自动以 current_user.id 作为 applicant_id 过滤

    Service：svc.list_my_submitted(db, applicant_id=current_user.id, skip, limit, status_filter)
    """
    total, items = await svc.list_my_submitted(
        db,
        applicant_id=current_user.id,
        skip=skip,
        limit=limit,
        status_filter=status,
        business_type=business_type,
        start_date=start_date,
        end_date=end_date,
        filter_applicant_id=applicant_id,
        applicant_keyword=applicant_keyword,
    )
    return ApprovalInstancePage(
        total=total,
        items=[await _approval_instance_out(db, i) for i in items],
    )


@router.get("/my-cc", response_model=ApprovalInstancePage)
async def my_cc(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    business_type: Optional[str] = Query(None, description="按业务类型筛选"),
    start_date: Optional[date] = Query(None, description="按提交开始日期筛选"),
    end_date: Optional[date] = Query(None, description="按提交结束日期筛选"),
    applicant_id: Optional[int] = Query(None, description="按申请人员工 ID 筛选"),
    applicant_keyword: Optional[str] = Query(None, description="按申请人姓名或工号筛选"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalInstancePage:
    total, items = await svc.list_my_cc(
        db,
        employee_id=current_user.id,
        skip=skip,
        limit=limit,
        business_type=business_type,
        start_date=start_date,
        end_date=end_date,
        applicant_id=applicant_id,
        applicant_keyword=applicant_keyword,
    )
    return ApprovalInstancePage(
        total=total,
        items=[await _approval_instance_out(db, i) for i in items],
    )


@router.post(
    "/instances/{instance_id}/process",
    response_model=ApprovalInstanceOut,
)
async def process_approval(
    instance_id: int,
    data: ApprovalActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalInstanceOut:
    """
    POST /approval/instances/{instance_id}/process — 处理审批（通过/拒绝）

    用途：审批人对指定审批实例执行审批动作。
          - 通过（approve）：流程推进到下一节点；若为最后节点则实例状态变为 approved，
            并触发对应业务的回调（如将请假申请状态改为 approved）。
          - 拒绝（reject）：实例状态直接变为 rejected，同时触发业务回调。

    路径参数：
        - instance_id: int — 目标审批实例主键 ID

    请求体（ApprovalActionRequest）：
        - action: str              — 操作类型：approve（通过）/ reject（拒绝）
        - comment: Optional[str]   — 审批意见（拒绝时强烈建议填写原因）

    响应（200 OK）：ApprovalInstanceOut — 含更新后的实例状态及当前节点信息
    响应（403 Forbidden）：当前用户不是该节点的审批人时由 svc 拒绝
    响应（404 Not Found）：实例不存在时由 svc 抛出
    响应（409 Conflict）：实例已处于终态（approved/rejected/withdrawn）时由 svc 拒绝

    权限：任意已登录用户（get_current_user）
          approver_id 自动取自 current_user.id，Service 层校验其是否为当前节点的有效审批人

    Service：svc.process_approval(db, instance_id, approver_id=current_user.id, data)
    """
    instance = await svc.process_approval(
        db, instance_id, approver_id=current_user.id, data=data
    )
    return ApprovalInstanceOut.model_validate(instance)


@router.post(
    "/instances/{instance_id}/withdraw",
    response_model=ApprovalInstanceOut,
)
async def withdraw_instance(
    instance_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ApprovalInstanceOut:
    """
    POST /approval/instances/{instance_id}/withdraw — 撤回审批申请

    用途：申请人主动撤回尚在审批中的申请。
          撤回后实例状态变为 withdrawn，流程终止，审批人无需再处理。
          注意：已通过或已拒绝的申请不可撤回。

    路径参数：
        - instance_id: int — 目标审批实例主键 ID

    请求体：无（无需请求体，操作人从 JWT 中识别）

    响应（200 OK）：ApprovalInstanceOut — 含更新后的 status = "withdrawn"
    响应（403 Forbidden）：当前用户不是该申请的申请人时由 svc 拒绝
    响应（409 Conflict）：实例已处于终态（approved/rejected/withdrawn）时由 svc 拒绝
    响应（404 Not Found）：实例不存在时由 svc 抛出

    权限：任意已登录用户（get_current_user）
          applicant_id 自动取自 current_user.id，Service 层校验其是否为该申请的提交人

    Service：svc.withdraw_instance(db, instance_id, applicant_id=current_user.id)
    """
    instance = await svc.withdraw_instance(
        db, instance_id, applicant_id=current_user.id
    )
    return ApprovalInstanceOut.model_validate(instance)
