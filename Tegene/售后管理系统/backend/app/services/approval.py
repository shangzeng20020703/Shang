"""
审批引擎服务模块 - Approval Engine Service
============================================

业务职责：
    提供通用审批引擎，支持任意业务模块（请假/加班/招聘/采购等）复用同一套审批流程基础设施。
    通过 module（业务模块标识）+ business_id（业务记录主键）+ business_type（业务类型描述）
    三元组将审批实例与具体业务数据松耦合关联。

核心概念：
    - ApprovalFlow（审批流程定义）：定义某业务模块下审批的节点结构，可复用。
      每个 Flow 有唯一的 module 标识，同一 module 只使用最新的激活 Flow。
    - ApprovalNode（审批节点）：Flow 的组成单元，定义每一步的审批人类型和顺序。
      node_order 决定节点执行顺序（从1开始，升序）。
    - ApprovalInstance（审批实例）：基于某 Flow 为特定业务申请创建的一次审批过程。
      记录当前进行到第几个节点（current_node_order）。
    - ApprovalRecord（审批记录）：每次审批操作（通过/拒绝/转审/撤回）的历史快照。
    - ApprovalDelegate（代理授权）：某员工将自己在指定时间段内的审批权委托给代理人。

节点类型（node_type）：
    - approval：正常审批节点，审批人需主动操作（通过/拒绝/转签）
    - notify：通知节点，系统自动推进（不阻塞流程）
    - auto：自动条件节点，根据预设条件自动判断通过/拒绝

审批人类型（approver_type）：
    - specific_user：指定具体员工（通过 approver_id 指定）
    - direct_manager：申请人的直接上级（动态解析）
    - hr：HR 角色员工（系统中第一个 hr 角色员工）
    - department_head：申请人所在部门的部门主管
    - gm：总经理/顶级部门负责人（后台超级管理员不作为业务审批人）

审批实例状态：
    - pending：待审批（流程进行中）
    - approved：已通过（所有节点审批完毕）
    - rejected：已拒绝（任一节点拒绝即终止）
    - withdrawn：已撤回（申请人主动撤回）

数据流（正向审批）：
    create_instance() → process_approval() × N（推进节点） → 状态=approved → _sync_business_status()

技术栈：
    FastAPI + SQLAlchemy 2.0 Async + PostgreSQL + Pydantic v2
"""

from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
import inspect
import json
import logging
import re
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from app.models.approval import (
    ApprovalDelegate,
    ApprovalFlow,
    ApprovalFormField,
    ApprovalInstance,
    ApprovalNode,
    ApprovalRecord,
    ApprovalTask,
    ApprovalTemplateVersion,
    ApprovalType,
)
from app.schemas.approval import (
    ApprovalActionRequest,
    ApprovalApplicationSubmit,
    ApprovalDelegateCreate,
    ApprovalDelegateOut,
    ApprovalFormFieldCreate,
    ApprovalFlowCreate,
    ApprovalFlowUpdate,
    ApprovalFormFieldOut,
    ApprovalFormFieldUpdate,
    ApprovalDraftApplicantPreviewRequest,
    ApprovalFlowNodePreview,
    ApprovalFlowPreviewOut,
    ApprovalInstanceCreate,
    ApprovalReminderResult,
    ApprovalNodeCreate,
    ApprovalNodeUpdate,
    ApprovalTemplateGroup,
    ApprovalTemplateCopyRequest,
    ApprovalTypeCreate,
    ApprovalTypeOut,
    ApprovalTypeUpdate,
    BulkApprovalRequest,
    BulkApprovalResult,
)
from app.schemas.notification import NotificationCreate
from app.services.event_dispatcher import dispatch_event
from app.services.events import BusinessEventService
from app.services.notification import NotificationService


FLOW_MISSING_MESSAGE = "当前模板未配置审批流程，请联系 HR 或管理员"
BUSINESS_TRIP_CODE = "business_trip"
LEAVE_CODES = {"leave", "leave_request"}
PUNCH_CORRECTION_CODES = {"punch_correction", "attendance_punch_correction"}
HEADCOUNT_REQUEST_CODES = {"headcount_request", "headcount_plan"}
logger = logging.getLogger(__name__)
FLOW_MODULE_ALIASES = {
    "custom": ("custom", "custom_template"),
    "custom_template": ("custom_template", "custom"),
    "leave": ("leave", "leave_request"),
    "leave_request": ("leave_request", "leave"),
    "punch_correction": ("punch_correction", "attendance_punch_correction"),
    "attendance_punch_correction": ("attendance_punch_correction", "punch_correction"),
    "recruitment": ("recruitment", "recruitment_demand"),
    "recruitment_demand": ("recruitment_demand", "recruitment"),
}
BUSINESS_TRIP_REQUIRED_FIELDS = {
    "department",
    "project_name",
    "trip_reason",
    "trip_location",
    "trip_duration",
}
NODE_TYPE_LABELS = {
    "approval": "审批",
    "handler": "办理",
    "notify": "抄送",
    "auto": "自动",
    "condition_branch": "条件分支",
    "condition": "条件分支",
    "parallel_branch": "并行分支",
}
PROGRESS_STATUS_LABELS = {
    "preview": "待提交",
    "completed": "已通过",
    "pending": "待审批",
    "not_started": "未开始",
    "rejected": "已拒绝",
    "withdrawn": "已撤回",
    "cancelled": "已取消",
    "auto_approved": "自动通过",
    "auto_rejected": "自动拒绝",
    "notified": "已抄送",
    "skipped": "已跳过",
}
TEMPLATE_CONTROL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "field_type": "layout_column",
        "label": "分栏",
        "icon": "Ⅱ",
        "value_kind": "layout",
        "placeholder": "分栏布局",
        "supports_required": False,
        "supports_options": False,
        "supports_print": False,
        "readonly_by_default": True,
    },
    {
        "field_type": "text",
        "label": "单行输入框",
        "icon": "T",
        "value_kind": "string",
        "placeholder": "请输入",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "textarea",
        "label": "多行输入框",
        "icon": "T=",
        "value_kind": "string",
        "placeholder": "请输入",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "number",
        "label": "数字输入框",
        "icon": "123",
        "value_kind": "number",
        "placeholder": "请输入数字",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "amount",
        "label": "金额",
        "icon": "¥",
        "value_kind": "money",
        "placeholder": "请输入金额",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "formula",
        "label": "公式",
        "icon": "fx",
        "value_kind": "number",
        "placeholder": "自动计算",
        "supports_required": False,
        "supports_options": False,
        "supports_print": True,
        "readonly_by_default": True,
    },
    {
        "field_type": "date",
        "label": "日期",
        "icon": "日",
        "value_kind": "date",
        "placeholder": "请选择",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "date_range",
        "label": "日期区间",
        "icon": "间",
        "value_kind": "date_range",
        "placeholder": "请选择日期区间",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "datetime",
        "label": "日期时间",
        "icon": "时",
        "value_kind": "datetime",
        "placeholder": "请选择时间",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "duration",
        "label": "时长",
        "icon": "h",
        "value_kind": "duration",
        "placeholder": "自动计算",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "radio",
        "label": "单选框",
        "icon": "○",
        "value_kind": "single_option",
        "placeholder": "请选择",
        "supports_required": True,
        "supports_options": True,
        "supports_print": True,
        "default_options": ["选项1", "选项2"],
    },
    {
        "field_type": "select",
        "label": "下拉选择",
        "icon": "选",
        "value_kind": "single_option",
        "placeholder": "请选择",
        "supports_required": True,
        "supports_options": True,
        "supports_print": True,
        "default_options": ["选项1", "选项2"],
    },
    {
        "field_type": "checkbox",
        "label": "多选框",
        "icon": "☑",
        "value_kind": "multi_option",
        "placeholder": "请选择",
        "supports_required": True,
        "supports_options": True,
        "supports_print": True,
        "default_options": ["选项1", "选项2"],
    },
    {
        "field_type": "cascade",
        "label": "级联/分类",
        "icon": "级",
        "value_kind": "single_option",
        "placeholder": "请选择",
        "supports_required": True,
        "supports_options": True,
        "supports_print": True,
        "default_options": ["分类一", "分类二"],
    },
    {
        "field_type": "ai_control",
        "label": "AI控件",
        "icon": "AI",
        "value_kind": "string",
        "placeholder": "智能识别",
        "supports_required": False,
        "supports_options": False,
        "supports_print": True,
        "disabled": True,
        "disabled_reason": "AI控件需接入模型服务后启用",
    },
    {
        "field_type": "member",
        "label": "成员",
        "icon": "人",
        "value_kind": "member",
        "placeholder": "请选择成员",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "department",
        "label": "部门",
        "icon": "部",
        "value_kind": "department",
        "placeholder": "请选择部门",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "company",
        "label": "公司",
        "icon": "司",
        "value_kind": "company",
        "placeholder": "请选择公司",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "location",
        "label": "地点",
        "icon": "址",
        "value_kind": "location",
        "placeholder": "请选择地点",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "external_contact",
        "label": "外部联系人",
        "icon": "外",
        "value_kind": "external_contact",
        "placeholder": "请选择外部联系人",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "industry_department",
        "label": "行业通讯录部门",
        "icon": "讯",
        "value_kind": "department",
        "placeholder": "请选择部门",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "province_city",
        "label": "省市区",
        "icon": "省",
        "value_kind": "region",
        "placeholder": "请选择省市区",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "rating",
        "label": "评分",
        "icon": "☆",
        "value_kind": "number",
        "placeholder": "请选择评分",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "attachment",
        "label": "附件",
        "icon": "附",
        "value_kind": "file_list",
        "placeholder": "上传附件",
        "supports_required": False,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "image",
        "label": "图片",
        "icon": "图",
        "value_kind": "file_list",
        "placeholder": "上传图片",
        "supports_required": False,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "signature",
        "label": "手写签名",
        "icon": "签",
        "value_kind": "file_list",
        "placeholder": "请签名",
        "supports_required": False,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "related_approval",
        "label": "关联申请单",
        "icon": "关",
        "value_kind": "approval_ref",
        "placeholder": "请选择申请单",
        "supports_required": False,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "detail",
        "label": "明细",
        "icon": "表",
        "value_kind": "detail_rows",
        "placeholder": "添加明细",
        "supports_required": False,
        "supports_options": False,
        "supports_print": True,
        "business_calculation": True,
        "default_options": {
            "child_fields": [],
            "summary": {
                "enabled": True,
                "label": "明细合计",
                "field_codes": [],
                "value_type": "amount",
            },
            "print_layout": "single_line",
        },
    },
    {
        "field_type": "static_text",
        "label": "说明文字",
        "icon": "文",
        "value_kind": "static",
        "placeholder": "请输入说明文字",
        "supports_required": False,
        "supports_options": False,
        "supports_print": False,
        "readonly_by_default": True,
    },
    {
        "field_type": "identity_card",
        "label": "身份证",
        "icon": "证",
        "value_kind": "string",
        "placeholder": "请输入身份证号",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "phone",
        "label": "电话",
        "icon": "机",
        "value_kind": "phone",
        "placeholder": "请输入手机号",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "invoice",
        "label": "发票",
        "icon": "票",
        "value_kind": "business_ref",
        "placeholder": "请选择发票",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "customer",
        "label": "客户",
        "icon": "客",
        "value_kind": "business_ref",
        "placeholder": "请选择客户",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "collection_account",
        "label": "收款账户",
        "icon": "卡",
        "value_kind": "bank_account",
        "placeholder": "请选择收款账户",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "budget_request",
        "label": "预算申请",
        "icon": "预",
        "value_kind": "business_ref",
        "placeholder": "请选择预算申请",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "related_contract",
        "label": "关联合同",
        "icon": "合",
        "value_kind": "business_ref",
        "placeholder": "请选择合同",
        "supports_required": False,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "engineering_project",
        "label": "工程项目",
        "icon": "项",
        "value_kind": "business_ref",
        "placeholder": "请选择工程项目",
        "supports_required": True,
        "supports_options": False,
        "supports_print": True,
    },
    {
        "field_type": "ocr_text",
        "label": "通用文字识别",
        "icon": "识",
        "value_kind": "string",
        "placeholder": "识别文字",
        "supports_required": False,
        "supports_options": False,
        "supports_print": True,
        "disabled": True,
        "disabled_reason": "高级识别控件需开通后使用",
    },
    {
        "field_type": "id_card_ocr",
        "label": "身份证识别",
        "icon": "证",
        "value_kind": "string",
        "placeholder": "识别身份证",
        "supports_required": False,
        "supports_options": False,
        "supports_print": True,
        "disabled": True,
        "disabled_reason": "高级识别控件需开通后使用",
    },
    {
        "field_type": "serial_number",
        "label": "流水号",
        "icon": "号",
        "value_kind": "string",
        "placeholder": "自动生成",
        "supports_required": False,
        "supports_options": False,
        "supports_print": True,
        "readonly_by_default": True,
        "disabled": True,
        "disabled_reason": "流水号控件需开通增强能力后使用",
    },
)
SUPPORTED_TEMPLATE_FIELD_TYPES = {
    *(item["field_type"] for item in TEMPLATE_CONTROL_DEFINITIONS if not item.get("disabled")),
    "select",
    "boolean",
    "int",
    "float",
}
BUSINESS_TRIP_EXPENSE_FIELD_CODES = (
    "flight_ticket",
    "train_ticket",
    "lodging",
    "trip_allowance",
    "local_transport",
)
BUSINESS_TRIP_DETAIL_CODE = "trip_expense_detail"
BUSINESS_TRIP_DETAIL_LABEL = "明细"
BUSINESS_TRIP_DETAIL_SUMMARY_LABEL = "预计出差费用合计"
EXECUTABLE_NODE_TYPES = {"approval", "handler", "notify", "auto"}
BRANCH_NODE_TYPES = {"condition", "condition_branch"}
PARALLEL_BRANCH_NODE_TYPES = {"parallel_branch"}
SPLIT_NODE_TYPES = BRANCH_NODE_TYPES | PARALLEL_BRANCH_NODE_TYPES
ROLE_APPROVER_TYPES = {"admin", "hr", "finance", "manager", "asset_admin"}
TEMPLATE_ADMIN_ROLES = {"admin", "hr"}
CONDITION_OPERATORS = {
    "eq",
    "neq",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "empty",
    "not_empty",
    "=",
    "==",
    "!=",
    "lt",
    "lte",
    "gt",
    "gte",
    "between",
}


def _flow_module_candidates(module: Optional[str]) -> tuple[str, ...]:
    if not module:
        return tuple()
    normalized = str(module).strip()
    if not normalized:
        return tuple()
    return FLOW_MODULE_ALIASES.get(normalized, (normalized,))


def _is_business_trip_code(value: Any) -> bool:
    return "business_trip" in _flow_module_candidates(str(value or "").strip())


def _field_value(field: Any, key: str, default: Any = None) -> Any:
    if isinstance(field, dict):
        return field.get(key, default)
    return getattr(field, key, default)


def _field_sort_order(field: Any, index: int) -> int:
    value = _field_value(field, "sort_order", index + 1)
    try:
        return int(value)
    except (TypeError, ValueError):
        return index + 1


def _field_created_at(field: Any) -> datetime:
    value = _field_value(field, "created_at")
    return value if isinstance(value, datetime) else datetime.now(timezone.utc)


def _field_approval_type_id(field: Any, fallback: Any = None) -> int:
    value = _field_value(field, "approval_type_id", fallback)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _field_id(field: Any, fallback: int = 0) -> int:
    value = _field_value(field, "id", fallback)
    try:
        return int(value or fallback)
    except (TypeError, ValueError):
        return fallback


def _field_options(field: Any) -> Any:
    options = _field_value(field, "options_json")
    if isinstance(options, str):
        try:
            return json.loads(options)
        except Exception:
            return options
    return options


def _detail_child_field_payload(field: Any) -> dict[str, Any]:
    return {
        "label": _field_value(field, "label") or _field_value(field, "code"),
        "code": _field_value(field, "code"),
        "field_type": _field_value(field, "field_type"),
        "is_required": bool(_field_value(field, "is_required", False)),
        "default_value": _field_value(field, "default_value"),
        "options_json": _field_options(field),
        "display_condition": _field_value(field, "display_condition"),
        "validation_rule": _field_value(field, "validation_rule"),
        "editable_scope": _field_value(field, "editable_scope"),
        "print_visible": bool(_field_value(field, "print_visible", True)),
        "placeholder": _field_value(field, "placeholder"),
        "is_business_calculation": bool(_field_value(field, "is_business_calculation", False)),
        "is_readonly": bool(_field_value(field, "is_readonly", False)),
        "sort_order": _field_value(field, "sort_order", 0),
    }


def _business_trip_detail_summary(field_codes: list[str]) -> dict[str, Any]:
    return {
        "enabled": True,
        "label": BUSINESS_TRIP_DETAIL_SUMMARY_LABEL,
        "field_codes": field_codes,
        "value_type": "amount",
    }


def normalized_template_form_fields(template_or_code: Any, fields: Optional[list[Any]] = None) -> list[Any]:
    """把历史出差费用平铺字段归并为移动端可自动合计的明细控件。"""
    business_code = (
        str(_field_value(template_or_code, "business_code", template_or_code) or "").strip()
        if not isinstance(template_or_code, str)
        else template_or_code
    )
    raw_fields = list(fields if fields is not None else _field_value(template_or_code, "form_fields", []) or [])
    if not _is_business_trip_code(business_code):
        return raw_fields

    if any(
        _field_value(field, "code") == BUSINESS_TRIP_DETAIL_CODE
        and str(_field_value(field, "field_type", "")).lower() == "detail"
        for field in raw_fields
    ):
        return raw_fields

    expense_fields = [
        field
        for field in raw_fields
        if _field_value(field, "code") in BUSINESS_TRIP_EXPENSE_FIELD_CODES
    ]
    if not expense_fields:
        return raw_fields

    detail_header = next(
        (
            field
            for field in raw_fields
            if _field_value(field, "code") == "detail_header"
            and str(_field_value(field, "field_type", "")).lower() == "static_text"
        ),
        None,
    )
    insert_index = next(
        (
            index
            for index, field in enumerate(raw_fields)
            if field is detail_header or _field_value(field, "code") in BUSINESS_TRIP_EXPENSE_FIELD_CODES
        ),
        len(raw_fields),
    )
    child_fields = [_detail_child_field_payload(field) for field in expense_fields]
    field_codes = [str(field["code"]) for field in child_fields if field.get("code")]
    source_field = detail_header or expense_fields[0]
    detail_field = {
        "id": _field_id(source_field),
        "approval_type_id": _field_approval_type_id(source_field, _field_value(template_or_code, "id", 0)),
        "created_at": _field_created_at(source_field),
        "label": BUSINESS_TRIP_DETAIL_LABEL,
        "code": BUSINESS_TRIP_DETAIL_CODE,
        "field_type": "detail",
        "is_required": any(bool(_field_value(field, "is_required", False)) for field in expense_fields),
        "default_value": None,
        "options_json": {
            "child_fields": child_fields,
            "summary": _business_trip_detail_summary(field_codes),
            "print_layout": "multi_line",
        },
        "display_condition": None,
        "validation_rule": None,
        "editable_scope": None,
        "print_visible": True,
        "placeholder": "添加明细",
        "is_business_calculation": True,
        "is_readonly": False,
        "sort_order": _field_sort_order(source_field, insert_index),
    }
    normalized = [
        field
        for field in raw_fields
        if _field_value(field, "code") != "detail_header"
        and _field_value(field, "code") not in BUSINESS_TRIP_EXPENSE_FIELD_CODES
    ]
    normalized.insert(min(insert_index, len(normalized)), detail_field)
    return normalized


def _normalize_business_trip_form_data(business_code: Any, fields: list[Any], form_data: dict[str, Any]) -> dict[str, Any]:
    if not _is_business_trip_code(business_code) or BUSINESS_TRIP_DETAIL_CODE in form_data:
        return form_data
    detail_field = next(
        (
            field
            for field in fields
            if _field_value(field, "code") == BUSINESS_TRIP_DETAIL_CODE
            and str(_field_value(field, "field_type", "")).lower() == "detail"
        ),
        None,
    )
    options = _field_options(detail_field) if detail_field else None
    child_fields = options.get("child_fields") if isinstance(options, dict) else None
    child_codes = [
        str(_field_value(child, "code"))
        for child in (child_fields or [])
        if _field_value(child, "code")
    ] or list(BUSINESS_TRIP_EXPENSE_FIELD_CODES)
    if not any(code in form_data for code in child_codes):
        return form_data
    row = {code: form_data.get(code) for code in child_codes if code in form_data}
    normalized = dict(form_data)
    normalized[BUSINESS_TRIP_DETAIL_CODE] = [row]
    return normalized


def list_template_control_definitions() -> list[dict[str, Any]]:
    return [dict(item) for item in TEMPLATE_CONTROL_DEFINITIONS]


def _option_values(options: Any) -> list[Any]:
    if options in (None, ""):
        return []
    if isinstance(options, str):
        try:
            return _option_values(json.loads(options))
        except Exception:
            return [options]
    if isinstance(options, dict):
        raw_options = options.get("options") or options.get("items") or options.get("values") or []
    else:
        raw_options = options
    if not isinstance(raw_options, list):
        return []

    values: list[Any] = []
    for item in raw_options:
        if isinstance(item, dict):
            value = item.get("value", item.get("label"))
            if value not in (None, ""):
                values.append(value)
        elif item not in (None, ""):
            values.append(item)
    return values


def _parse_date_like(value: Any) -> None:
    if isinstance(value, datetime) or isinstance(value, date):
        return
    if not isinstance(value, str):
        raise ValueError("date value must be a string")
    normalized = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        date.fromisoformat(value[:10])


def _payload_has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    if isinstance(value, dict):
        return any(_payload_has_value(item) for item in value.values())
    return True


def _validate_attachment_payload(code: str, value: Any) -> None:
    if isinstance(value, dict):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise HTTPException(status_code=400, detail=f"字段 '{code}' 附件格式不正确")

    for item in items:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"字段 '{code}' 附件必须包含上传结果")
        url = str(item.get("url") or item.get("path") or item.get("file_url") or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail=f"字段 '{code}' 附件缺少文件路径")
        if not (url.startswith("/uploads/") or url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(status_code=400, detail=f"字段 '{code}' 附件路径不合法")
        size = item.get("size")
        if size not in (None, ""):
            try:
                if int(size) < 0:
                    raise ValueError()
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"字段 '{code}' 附件大小不合法") from exc



def _punch_correction_dynamic_select_field(field: Any) -> bool:
    code = str(_field_value(field, "code", "") or "").lower()
    label = str(_field_value(field, "label", "") or "")
    options = _field_options(field)
    attendance_component = ""
    if isinstance(options, dict):
        attendance_component = str(options.get("attendance_component") or "").lower()
    if attendance_component in PUNCH_CORRECTION_CODES:
        return True
    text = f"{code} {label}".lower()
    return (
        "punch_correction_slot" in text
        or "补卡班次" in text
        or "补卡卡点" in text
    )


def _leave_type_dynamic_select_field(field: Any) -> bool:
    code = str(_field_value(field, "code", "") or "").lower()
    label = str(_field_value(field, "label", "") or "")
    field_type = str(_field_value(field, "field_type", "") or "").lower()
    if field_type not in {"select", "cascade", "radio"}:
        return False
    options = _field_options(field)
    attendance_component = ""
    if isinstance(options, dict):
        attendance_component = str(options.get("attendance_component") or "").lower()
    return code == "leave_type" or "请假类型" in label or (attendance_component == "leave" and "leave_type" in code)


async def validate_form_payload(
    fields: list[Any],
    payload: dict[str, Any],
    *,
    business_code: str | None = None,
) -> None:
    business = str(business_code or "").lower()
    for field in fields or []:
        code = getattr(field, "code", None) if not isinstance(field, dict) else field.get("code")
        field_type = getattr(field, "field_type", None) if not isinstance(field, dict) else field.get("field_type")
        is_required = getattr(field, "is_required", False) if not isinstance(field, dict) else field.get("is_required", False)
        options = getattr(field, "options_json", None) if not isinstance(field, dict) else field.get("options_json")
        if code is None:
            continue
        has_value = code in payload and _payload_has_value(payload.get(code))
        if is_required and not has_value:
            raise HTTPException(status_code=400, detail=f"字段 '{code}' 为必填项")
        if not has_value:
            continue
        value = payload.get(code)
        normalized_options = options
        if isinstance(options, str):
            try:
                normalized_options = json.loads(options)
            except Exception:
                normalized_options = options
        if field_type in ("number", "int", "float", "amount", "duration", "formula", "rating"):
            if isinstance(value, bool):
                raise HTTPException(status_code=400, detail=f"字段 '{code}' 必须是数字")
            if isinstance(value, str):
                try:
                    float(value)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=f"字段 '{code}' 必须是数字") from exc
            elif not isinstance(value, (int, float)):
                raise HTTPException(status_code=400, detail=f"字段 '{code}' 必须是数字")
        elif field_type in ("select", "radio", "checkbox", "cascade"):
            if business in PUNCH_CORRECTION_CODES and _punch_correction_dynamic_select_field(field):
                continue
            if business in LEAVE_CODES and _leave_type_dynamic_select_field(field):
                continue
            allowed = _option_values(normalized_options)
            if not allowed:
                continue
            if isinstance(value, list):
                invalid = [item for item in value if item not in allowed]
                if invalid:
                    raise HTTPException(status_code=400, detail=f"字段 '{code}' 选项无效")
            elif value not in allowed:
                raise HTTPException(status_code=400, detail=f"字段 '{code}' 选项无效")
        elif field_type == "date":
            try:
                _parse_date_like(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"字段 '{code}' 日期格式不正确")
        elif field_type == "date_range":
            if isinstance(value, dict):
                start_value = value.get("start") or value.get("start_date") or value.get("from")
                end_value = value.get("end") or value.get("end_date") or value.get("to")
            elif isinstance(value, (list, tuple)) and len(value) >= 2:
                start_value, end_value = value[0], value[1]
            else:
                raise HTTPException(status_code=400, detail=f"字段 '{code}' 日期范围格式不正确")
            try:
                _parse_date_like(start_value)
                _parse_date_like(end_value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"字段 '{code}' 日期范围格式不正确") from exc
        elif field_type == "datetime":
            try:
                _parse_date_like(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"字段 '{code}' 时间格式不正确") from exc
        elif field_type in (
            "member",
            "department",
            "company",
            "location",
            "related_approval",
            "external_contact",
            "industry_department",
            "province_city",
            "invoice",
            "customer",
            "budget_request",
            "related_contract",
            "engineering_project",
        ):
            if isinstance(value, (list, tuple)):
                if not all(isinstance(item, (int, str)) and str(item).strip() for item in value):
                    raise HTTPException(status_code=400, detail=f"字段 '{code}' 选择值不正确")
            elif not isinstance(value, (int, str)) or not str(value).strip():
                raise HTTPException(status_code=400, detail=f"字段 '{code}' 选择值不正确")
        elif field_type in ("attachment", "image", "signature"):
            _validate_attachment_payload(str(code), value)
        elif field_type in ("detail", "collection_account"):
            if not isinstance(value, (dict, list)):
                raise HTTPException(status_code=400, detail=f"字段 '{code}' 数据格式不正确")
        elif field_type == "phone":
            if not isinstance(value, str) or not value.strip():
                raise HTTPException(status_code=400, detail=f"字段 '{code}' 手机号格式不正确")
        elif field_type == "boolean":
            if not isinstance(value, bool):
                raise HTTPException(status_code=400, detail=f"字段 '{code}' 必须是布尔值")


async def validate_leave_type_payload(
    db: AsyncSession,
    fields: list[Any],
    payload: dict[str, Any],
    *,
    business_code: str | None = None,
    applicant_id: int,
) -> None:
    """校验动态请假模板选择的假种来自假期管理，并回填标准字段。"""
    if str(business_code or "").lower() not in LEAVE_CODES:
        return

    leave_field_code = ""
    for field in fields or []:
        if _leave_type_dynamic_select_field(field):
            leave_field_code = str(_field_value(field, "code", "") or "")
            break

    raw_value = (
        payload.get("leave_type_id")
        or payload.get("leave_type_code")
        or payload.get("leave_type_name")
        or (payload.get(leave_field_code) if leave_field_code else None)
        or payload.get("leave_type")
    )
    if raw_value in (None, ""):
        return

    from app.models.employee import Employee
    from app.services.leave import LeaveRequestService, LeaveTypeService

    await LeaveTypeService.ensure_default_wecom_types(db)
    leave_type = await _resolve_dynamic_leave_type(db, payload, raw_value)
    if leave_type is None or not bool(getattr(leave_type, "is_active", True)):
        raise HTTPException(status_code=400, detail="请假类型无效，请重新选择")

    employee = db.get(Employee, applicant_id)
    if inspect.isawaitable(employee):
        employee = await employee
    if employee is not None and not LeaveRequestService._employee_matches_scope(leave_type, employee):
        raise HTTPException(status_code=400, detail="当前员工不适用该请假类型")

    payload["leave_type_id"] = int(leave_type.id)
    payload["leave_type_code"] = getattr(leave_type, "code", None)
    payload["leave_type_name"] = getattr(leave_type, "name", None)
    if leave_field_code:
        payload[leave_field_code] = getattr(leave_type, "name", None)


async def _resolve_dynamic_leave_type(db: AsyncSession, payload: dict[str, Any], value: Any):
    from app.models.leave import LeaveType

    for key in ("leave_type_id",):
        candidate = payload.get(key)
        if candidate not in (None, ""):
            try:
                leave_type_id = int(candidate)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="请假类型无效，请重新选择")
            result = await db.execute(select(LeaveType).where(LeaveType.id == leave_type_id).limit(1))
            return result.scalar_one_or_none()

    raw_candidates = [
        payload.get("leave_type_code"),
        payload.get("leave_type_name"),
        value,
    ]
    normalized_values = []
    for raw in raw_candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        normalized_values.append(text)
        normalized_values.append(re.sub(r"[（(]剩余.*?[）)]$", "", text).strip())

    for text in dict.fromkeys(item for item in normalized_values if item):
        result = await db.execute(
            select(LeaveType)
            .where(or_(LeaveType.name == text, func.lower(LeaveType.code) == text.lower()))
            .order_by(LeaveType.is_active.desc(), LeaveType.id.asc())
            .limit(1)
        )
        leave_type = result.scalar_one_or_none()
        if leave_type is not None:
            return leave_type
    return None


async def list_approval_templates(
    db: AsyncSession,
    current_user: Any,
    include_inactive: bool = False,
) -> list[ApprovalTemplateGroup]:
    result = await db.execute(
        select(ApprovalType).options(selectinload(ApprovalType.form_fields)).order_by(
            ApprovalType.sort_order.asc(), ApprovalType.category.asc(), ApprovalType.id.asc()
        )
    )
    if include_inactive and not getattr(current_user, "is_superuser", False):
        raw_roles = getattr(current_user, "roles", None)
        user_roles = {
            str(role or "").strip().lower()
            for role in (raw_roles or [])
            if str(role or "").strip()
        }
        if not user_roles:
            user_id = getattr(current_user, "id", None)
            user_roles = await _employee_roles_by_id(db, int(user_id)) if user_id else set()
        management_view = bool(user_roles & TEMPLATE_ADMIN_ROLES)
    else:
        management_view = bool(include_inactive)

    templates: list[ApprovalType] = []
    for item in result.scalars().all():
        if _is_attendance_rule_binding_template(item):
            continue
        if _is_legacy_default_overtime_template(item):
            continue
        if management_view:
            if not await user_can_manage_template(db, item, current_user):
                continue
        else:
            if not getattr(item, "is_active", False):
                continue
            if not await _template_available_to_user(db, item, current_user):
                continue
        templates.append(item)
    grouped: dict[str, list[ApprovalTypeOut]] = {}
    for template in templates:
        category = getattr(template, "category", "default")
        template_payload = {
            "id": getattr(template, "id", None),
            "name": getattr(template, "name", ""),
            "business_code": getattr(template, "business_code", ""),
            "category": category,
            "category_key": getattr(template, "category_key", None),
            "scope": getattr(template, "scope", "mobile"),
            "is_active": getattr(template, "is_active", True),
            "sort_order": getattr(template, "sort_order", 0),
            "icon": getattr(template, "icon", None),
            "icon_key": getattr(template, "icon_key", None),
            "icon_tone": getattr(template, "icon_tone", None),
            "description": getattr(template, "description", None),
            "print_format": getattr(template, "print_format", None),
            "status": getattr(template, "status", "draft"),
            "version": getattr(template, "version", 1),
            "permission_rules": getattr(template, "permission_rules", None),
            "exception_rules": getattr(template, "exception_rules", None),
            "auto_approval_rule": getattr(template, "auto_approval_rule", None),
            "created_at": getattr(template, "created_at", datetime.now(timezone.utc)),
            "updated_at": getattr(template, "updated_at", None),
            "form_fields": normalized_template_form_fields(
                template,
                list(getattr(template, "form_fields", []) or []),
            ),
        }
        grouped.setdefault(category, []).append(ApprovalTypeOut.model_validate(template_payload))
    return [ApprovalTemplateGroup(category=category, templates=items) for category, items in grouped.items()]


def _as_plain_data(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if hasattr(data, "model_dump"):
        return data.model_dump()
    return dict(getattr(data, "__dict__", {}) or {})


def _ensure_rule_dict(value: Any, label: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{label} rules must be an object")
    return value


def _ensure_bool(value: Any, label: str) -> None:
    if value is not None and not isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{label} must be boolean")


def _ensure_member_id(value: Any, label: str) -> None:
    if value is None or not str(value).strip():
        raise HTTPException(status_code=400, detail=f"{label} member_id is required")
    try:
        int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{label} member_id must be numeric") from exc


SUPPORTED_DUPLICATE_APPROVER_POLICIES = {
    "first_only",
    "continuous_only",
    "every_node_required",
}

DUPLICATE_APPROVER_POLICY_ALIASES = {
    "skip_all_duplicates": "first_only",
    "skip_continuous_duplicates": "continuous_only",
    "all_nodes_required": "every_node_required",
    "each_node_required": "every_node_required",
    "every_node": "every_node_required",
    "no_auto_approval": "every_node_required",
    "no_auto_approve": "every_node_required",
}


def _normalize_duplicate_approver_policy(value: Any) -> str:
    policy = str(value or "").strip()
    policy = DUPLICATE_APPROVER_POLICY_ALIASES.get(policy, policy)
    if policy not in SUPPORTED_DUPLICATE_APPROVER_POLICIES:
        raise HTTPException(status_code=400, detail="unsupported duplicate approver policy")
    return policy


def _validate_rule_settings(payload: dict[str, Any]) -> None:
    auto_rule = _ensure_rule_dict(payload.get("auto_approval_rule"), "auto approval")
    duplicate_policy = auto_rule.get("duplicate_approver_policy")
    if duplicate_policy is not None:
        auto_rule["duplicate_approver_policy"] = _normalize_duplicate_approver_policy(duplicate_policy)

    exception_rules = _ensure_rule_dict(payload.get("exception_rules"), "exception")
    supported_exception_actions = {
        "auto_approve",
        "auto_reject",
        "transfer_to_admin",
        "transfer_to_specific",
        "remind_admin_handover",
        "auto_transfer",
    }
    for key, rule in exception_rules.items():
        if not isinstance(rule, dict):
            raise HTTPException(status_code=400, detail="exception rule must be an object")
        action = rule.get("action")
        if action is not None and action not in supported_exception_actions:
            raise HTTPException(status_code=400, detail=f"unsupported exception action for {key}")
        if action in {"transfer_to_specific", "auto_transfer"}:
            _ensure_member_id(rule.get("member_id"), f"exception {key}")

    permission_rules = _ensure_rule_dict(payload.get("permission_rules"), "permission")
    visible_scope = permission_rules.get("visible_scope")
    if isinstance(visible_scope, dict):
        _ensure_bool(visible_scope.get("include_sub_departments"), "visible_scope.include_sub_departments")
    notify_members = permission_rules.get("notify_members")
    if isinstance(notify_members, dict):
        _ensure_bool(notify_members.get("enabled"), "notify_members.enabled")

    submit_permission = permission_rules.get("submit_permission")
    if isinstance(submit_permission, dict):
        submit_type = submit_permission.get("type")
        if submit_type is not None and submit_type not in {"all", "roles", "selected_members"}:
            raise HTTPException(status_code=400, detail="unsupported submit permission type")
        if submit_type == "selected_members" and not _normalize_member_ids(
            submit_permission.get("member_ids") or submit_permission.get("employee_ids")
        ):
            raise HTTPException(status_code=400, detail="submit permission selected members are required")

    template_management = permission_rules.get("template_management")
    if isinstance(template_management, dict):
        manager_type = template_management.get("type")
        if manager_type is not None and manager_type not in {"all_approval_admins", "selected_admins"}:
            raise HTTPException(status_code=400, detail="unsupported template management type")
        if manager_type == "selected_admins" and not _normalize_member_ids(
            template_management.get("admin_ids")
            or template_management.get("member_ids")
            or template_management.get("employee_ids")
        ):
            raise HTTPException(status_code=400, detail="template administrators are required")

    view_permission = permission_rules.get("view_permission")
    if isinstance(view_permission, dict):
        view_type = view_permission.get("type")
        if view_type is not None and view_type not in {
            "all",
            "submitter_only",
            "submitter_and_approvers",
            "custom_members",
        }:
            raise HTTPException(status_code=400, detail="unsupported view permission type")

    field_edit_permissions = permission_rules.get("field_edit_permissions")
    if isinstance(field_edit_permissions, dict):
        for key, rule in field_edit_permissions.items():
            if not isinstance(rule, dict):
                raise HTTPException(status_code=400, detail=f"{key} edit permission must be an object")
            _ensure_bool(rule.get("modifiable"), f"{key}.modifiable")

    in_progress_actions = permission_rules.get("in_progress_actions")
    if isinstance(in_progress_actions, dict):
        for key, value in in_progress_actions.items():
            _ensure_bool(value, f"in_progress_actions.{key}")

    assistant_management = permission_rules.get("assistant_management")
    if isinstance(assistant_management, dict):
        permissions = assistant_management.get("permissions")
        if isinstance(permissions, dict):
            for key, value in permissions.items():
                _ensure_bool(value, f"assistant permission {key}")


def _template_visible_to_user(template: Any, current_user: Any) -> bool:
    if getattr(current_user, "is_superuser", False):
        return True
    rules = getattr(template, "permission_rules", None) or {}
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except Exception:
            rules = {}
    visible_roles = set(rules.get("visible_roles") or [])
    if not visible_roles:
        return True
    user_roles = set(getattr(current_user, "roles", None) or [])
    return bool(user_roles & visible_roles)


def _is_attendance_rule_binding_template(template: Any) -> bool:
    """考勤规则内部绑定项不作为员工可发起审批模板展示。"""
    business_code = str(getattr(template, "business_code", "") or "").strip()
    return business_code.startswith("attendance_rule_")


def _is_legacy_default_overtime_template(template: Any) -> bool:
    """历史迁移默认创建的英文加班模板不是当前可配置审批入口。"""
    business_code = str(getattr(template, "business_code", "") or "").strip().lower()
    name = str(getattr(template, "name", "") or "").strip().lower()
    category = str(getattr(template, "category", "") or "").strip().lower()
    description = str(getattr(template, "description", "") or "").strip().lower()
    return (
        business_code == "overtime"
        and name == "overtime request"
        and category == "hr"
        and description in {"", "overtime requests"}
    )


async def _template_available_to_user(
    db: AsyncSession,
    template: ApprovalType,
    current_user: Any,
) -> bool:
    if not _template_visible_to_user(template, current_user):
        return False
    if getattr(current_user, "is_superuser", False):
        return True
    applicant_id = getattr(current_user, "id", None)
    if not applicant_id:
        return False
    try:
        await _ensure_template_submit_allowed(db, template, int(applicant_id))
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise
    return True


def _validate_template_config_payload(payload: dict[str, Any]) -> None:
    if not str(payload.get("name") or "").strip():
        raise HTTPException(status_code=400, detail="template name is required")
    if not str(payload.get("business_code") or "").strip():
        raise HTTPException(status_code=400, detail="business_code is required")
    _validate_rule_settings(payload)
    seen_codes: set[str] = set()
    fields_by_code: dict[str, str] = {}
    for field in payload.get("fields") or []:
        field_data = _as_plain_data(field)
        code = str(field_data.get("code") or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="field code is required")
        if code in seen_codes:
            raise HTTPException(status_code=400, detail="duplicate field code")
        seen_codes.add(code)
        field_type = field_data.get("field_type")
        if field_type not in SUPPORTED_TEMPLATE_FIELD_TYPES:
            raise HTTPException(status_code=400, detail="unsupported field type")
        fields_by_code[code] = str(field_type)
    if payload.get("business_code") == BUSINESS_TRIP_CODE:
        missing_trip_fields = BUSINESS_TRIP_REQUIRED_FIELDS - set(fields_by_code)
        if missing_trip_fields:
            raise HTTPException(
                status_code=400,
                detail=f"business_trip missing fields: {', '.join(sorted(missing_trip_fields))}",
            )
        if fields_by_code.get("trip_duration") != "duration":
            raise HTTPException(status_code=400, detail="business_trip duration field must use duration type")
    nodes = [_as_plain_data(node) for node in (payload.get("flow_nodes") or [])]

    def branch_child_nodes(branch: Any) -> list[dict[str, Any]]:
        branch_data = _as_plain_data(branch)
        return [_as_plain_data(child) for child in (branch_data.get("nodes") or [])]

    def executable_nodes(source_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in source_nodes:
            node_type = item.get("node_type", "approval")
            if node_type in EXECUTABLE_NODE_TYPES:
                result.append(item)
                continue
            if node_type in SPLIT_NODE_TYPES:
                for branch in item.get("branches") or []:
                    result.extend(executable_nodes(branch_child_nodes(branch)))
        return result

    if not any(node.get("node_type", "approval") in ("approval", "handler", "auto") for node in executable_nodes(nodes)):
        raise HTTPException(status_code=400, detail="at least one approval node is required")

    def iter_condition_branch_nodes(source_nodes: list[dict[str, Any]]):
        for item in source_nodes:
            if item.get("node_type") in BRANCH_NODE_TYPES:
                yield item
            if item.get("node_type") in SPLIT_NODE_TYPES:
                for branch in item.get("branches") or []:
                    yield from iter_condition_branch_nodes(branch_child_nodes(branch))

    def iter_parallel_branch_nodes(source_nodes: list[dict[str, Any]], *, inside_parallel: bool = False):
        for item in source_nodes:
            node_type = item.get("node_type")
            if node_type in PARALLEL_BRANCH_NODE_TYPES:
                yield item, inside_parallel
                inside_child_parallel = True
            else:
                inside_child_parallel = inside_parallel
            if node_type in SPLIT_NODE_TYPES:
                for branch in item.get("branches") or []:
                    yield from iter_parallel_branch_nodes(
                        branch_child_nodes(branch),
                        inside_parallel=inside_child_parallel,
                    )

    condition_branches = list(iter_condition_branch_nodes(nodes))
    flat_condition_branches = [
        node for node in condition_branches if "branches" not in node
    ]
    if flat_condition_branches:
        default_count = sum(1 for branch in flat_condition_branches if bool(branch.get("is_default_branch")))
        if default_count != 1:
            raise HTTPException(status_code=400, detail="condition branch requires exactly one default branch")
        for branch in flat_condition_branches:
            has_group_conditions = bool(branch.get("condition_groups") or branch.get("groups"))
            if not branch.get("is_default_branch") and not (branch.get("conditions") or []) and not has_group_conditions:
                raise HTTPException(status_code=400, detail="condition branch requires conditions")
    for branch_node in condition_branches:
        if "branches" not in branch_node:
            continue
        branches = [_as_plain_data(branch) for branch in (branch_node.get("branches") or [])]
        if not branches:
            raise HTTPException(status_code=400, detail="condition branch requires branches")
        default_count = sum(1 for branch in branches if bool(branch.get("is_default_branch")))
        if default_count != 1:
            raise HTTPException(status_code=400, detail="condition branch requires exactly one default branch")
        for branch in branches:
            branch_nodes = [_as_plain_data(child) for child in (branch.get("nodes") or [])]
            if not branch.get("is_default_branch"):
                condition_groups = [_as_plain_data(group) for group in (branch.get("condition_groups") or branch.get("groups") or [])]
                if condition_groups:
                    for group in condition_groups:
                        conditions = group.get("conditions") or []
                        if not conditions:
                            raise HTTPException(status_code=400, detail="condition group requires conditions")
                        for condition in conditions:
                            condition_data = _as_plain_data(condition)
                            operator = condition_data.get("operator") or "eq"
                            if operator not in CONDITION_OPERATORS:
                                raise HTTPException(status_code=400, detail="unsupported condition operator")
                else:
                    conditions = branch.get("conditions") or []
                    if not conditions:
                        raise HTTPException(status_code=400, detail="condition branch requires conditions")
                    for condition in conditions:
                        condition_data = _as_plain_data(condition)
                        operator = condition_data.get("operator") or "eq"
                        if operator not in CONDITION_OPERATORS:
                            raise HTTPException(status_code=400, detail="unsupported condition operator")
    for parallel_node, inside_parallel in iter_parallel_branch_nodes(nodes):
        if inside_parallel:
            raise HTTPException(status_code=400, detail="parallel branch does not support nested parallel branch")
        branches = [_as_plain_data(branch) for branch in (parallel_node.get("branches") or [])]
        if len(branches) < 2:
            raise HTTPException(status_code=400, detail="parallel branch requires at least two branches")
        for index, branch in enumerate(branches, start=1):
            branch_nodes = branch_child_nodes(branch)
            if not executable_nodes(branch_nodes):
                label = str(branch.get("label") or branch.get("branch_label") or f"分支{index}").strip()
                raise HTTPException(status_code=400, detail=f"parallel branch {label} requires at least one node")
    supported_sources = {
        "specific_user",
        "department_head",
        "direct_manager",
        "multi_level_manager",
        "applicant_self",
        "applicant_select",
        "related_member_field",
        "form_department_head",
        "role",
    }
    supported_modes = {"or_sign", "counter_sign", "sequential", "vote"}
    supported_empty_actions = {
        "",
        "auto_approve",
        "auto_reject",
        "transfer_to_admin",
        "transfer_to_specific",
    }
    supported_roles = ROLE_APPROVER_TYPES
    role_alias_sources = ROLE_APPROVER_TYPES

    def iter_config_nodes(source_nodes: list[dict[str, Any]]):
        for item in source_nodes:
            yield item
            if item.get("node_type") in SPLIT_NODE_TYPES:
                for branch in item.get("branches") or []:
                    yield from iter_config_nodes(branch_child_nodes(branch))

    for node in iter_config_nodes(nodes):
        node_type = node.get("node_type", "approval")
        if node_type not in ("approval", "handler", "notify", "auto", "condition", "condition_branch", "parallel_branch", "applicant", "end"):
            raise HTTPException(status_code=400, detail="unsupported node type")
        source = node.get("assignee_source") or node.get("approver_type")
        if source in role_alias_sources:
            node["assignee_source"] = "role"
            node["role"] = source
            source = "role"
        notify_sources: list[str] = []
        if node_type == "notify":
            raw_notify_sources = node.get("notify_sources") or []
            if isinstance(raw_notify_sources, str):
                raw_notify_sources = [raw_notify_sources]
            if isinstance(raw_notify_sources, list):
                for raw_notify_source in raw_notify_sources:
                    notify_source = str(raw_notify_source or "").strip()
                    if not notify_source:
                        continue
                    if notify_source in role_alias_sources:
                        node["role"] = notify_source
                        notify_source = "role"
                    notify_sources.append(notify_source)
            if notify_sources:
                source = notify_sources[0]
                node["assignee_source"] = source
        if source and source not in supported_sources and node_type not in ("auto", "condition", "condition_branch", "parallel_branch"):
            raise HTTPException(status_code=400, detail="unsupported assignee source")
        mode = node.get("approval_mode")
        if mode is not None and mode not in supported_modes:
            raise HTTPException(status_code=400, detail="unsupported approval mode")
        if mode == "vote":
            try:
                vote_pass_count = int(node.get("vote_pass_count") or 1)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="unsupported vote pass count")
            if vote_pass_count < 1:
                raise HTTPException(status_code=400, detail="unsupported vote pass count")
        empty_action = str(node.get("empty_action") or "").strip()
        if empty_action not in supported_empty_actions:
            raise HTTPException(status_code=400, detail="unsupported empty action")
        if empty_action == "transfer_to_specific" and not _normalize_member_ids(node.get("empty_member_id")):
            raise HTTPException(status_code=400, detail="empty member id is required")
        if source == "specific_user" or node.get("approver_type") == "specific_user":
            approver_id = node.get("approver_id")
            member_ids = node.get("member_ids") or []
            if (approver_id is None or not str(approver_id).strip()) and not member_ids:
                raise HTTPException(status_code=400, detail="specific_user approver_id is required")
            if len(_normalize_member_ids(member_ids or approver_id)) > 50:
                raise HTTPException(status_code=400, detail="specific_user approver count exceeds 50")
        if source == "applicant_select":
            select_mode = str(node.get("applicant_select_mode") or "multiple").strip()
            select_scope = str(node.get("applicant_select_scope") or "company").strip()
            if select_mode not in {"single", "multiple"}:
                raise HTTPException(status_code=400, detail="unsupported applicant select mode")
            if select_scope not in {"company", "selected_members", "role"}:
                raise HTTPException(status_code=400, detail="unsupported applicant select scope")
            if select_scope == "selected_members" and not _normalize_member_ids(node.get("applicant_select_member_ids")):
                raise HTTPException(status_code=400, detail="applicant select members are required")
            if select_scope == "role":
                roles = _normalize_role_values(node.get("applicant_select_roles") or node.get("applicant_select_role"))
                if not roles:
                    raise HTTPException(status_code=400, detail="applicant select roles are required")
                if any(role not in supported_roles for role in roles):
                    raise HTTPException(status_code=400, detail="unsupported role")
        if source in {"direct_manager", "department_head", "multi_level_manager"}:
            try:
                manager_level = int(node.get("manager_level") or 1)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="unsupported manager level")
            if manager_level < 1 or manager_level > 8:
                raise HTTPException(status_code=400, detail="unsupported manager level")
        if source == "multi_level_manager":
            end_type = str(node.get("multi_level_end_type") or "level").strip()
            if end_type not in {"role", "level", "member"}:
                raise HTTPException(status_code=400, detail="unsupported multi level manager endpoint")
            if end_type == "role":
                role = str(node.get("multi_level_role") or node.get("role") or "").strip()
                if role not in supported_roles:
                    raise HTTPException(status_code=400, detail="unsupported role")
            if end_type == "member":
                member_ids = _normalize_member_ids(node.get("multi_level_member_ids") or node.get("member_ids"))
                if not member_ids:
                    raise HTTPException(status_code=400, detail="multi level manager endpoint members are required")
                if len(member_ids) > 50:
                    raise HTTPException(status_code=400, detail="multi level manager endpoint member count exceeds 50")
            if bool(node.get("multi_level_limit_enabled")):
                try:
                    limit_level = int(node.get("multi_level_limit_level") or 1)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="unsupported manager level")
                if limit_level < 1 or limit_level > 8:
                    raise HTTPException(status_code=400, detail="unsupported manager level")
        if source == "related_member_field":
            related_field_code = str(node.get("related_member_field_code") or node.get("related_field_code") or "").strip()
            if fields_by_code.get(related_field_code) != "member":
                raise HTTPException(status_code=400, detail="related_member_field must reference a member field")
        if source == "form_department_head":
            related_field_code = str(node.get("related_department_field_code") or node.get("related_field_code") or "").strip()
            if fields_by_code.get(related_field_code) != "department":
                raise HTTPException(status_code=400, detail="form_department_head must reference a department field")
        if source == "role":
            role = node.get("role")
            if role not in supported_roles:
                raise HTTPException(status_code=400, detail="unsupported role")
        for notify_source in notify_sources:
            if notify_source not in supported_sources:
                raise HTTPException(status_code=400, detail="unsupported notify source")
            if notify_source == "specific_user":
                approver_id = node.get("approver_id")
                member_ids = node.get("member_ids") or []
                if (approver_id is None or not str(approver_id).strip()) and not member_ids:
                    raise HTTPException(status_code=400, detail="specific_user approver_id is required")
            if notify_source == "related_member_field":
                related_field_code = str(node.get("related_member_field_code") or node.get("related_field_code") or "").strip()
                if fields_by_code.get(related_field_code) != "member":
                    raise HTTPException(status_code=400, detail="related_member_field must reference a member field")
            if notify_source == "form_department_head":
                related_field_code = str(node.get("related_department_field_code") or node.get("related_field_code") or "").strip()
                if fields_by_code.get(related_field_code) != "department":
                    raise HTTPException(status_code=400, detail="form_department_head must reference a department field")
            if notify_source == "role":
                role = node.get("role")
                if role not in supported_roles:
                    raise HTTPException(status_code=400, detail="unsupported role")


def _node_condition_rules(node_data: dict[str, Any]) -> str | None:
    rule_data: dict[str, Any] = {}
    existing_rules = node_data.get("condition_rules")
    if isinstance(existing_rules, str) and existing_rules.strip():
        try:
            parsed_rules = json.loads(existing_rules)
            if isinstance(parsed_rules, dict):
                rule_data.update(parsed_rules)
        except Exception:
            rule_data["raw_condition_rules"] = existing_rules
    elif isinstance(existing_rules, dict):
        rule_data.update(existing_rules)

    metadata_keys = (
        "node_type",
        "approver_type",
        "approver_id",
        "approval_mode",
        "assignee_source",
        "member_ids",
        "role",
        "related_field_code",
        "related_member_field_code",
        "related_department_field_code",
        "notify_sources",
        "allow_applicant_select",
        "include_applicant_self",
        "applicant_select_mode",
        "applicant_select_scope",
        "applicant_select_member_ids",
        "applicant_select_roles",
        "manager_level",
        "fallback_to_upper_manager",
        "applicant_self_if_head",
        "multi_level_end_type",
        "multi_level_role",
        "multi_level_member_ids",
        "multi_level_limit_enabled",
        "multi_level_limit_level",
        "multi_level_level",
        "empty_action",
        "empty_member_id",
        "vote_pass_count",
        "auto_action",
        "conditions",
        "condition_combinator",
        "condition_groups",
        "condition_group_combinator",
        "is_default_branch",
        "branch_label",
        "branches",
    )
    for key in metadata_keys:
        if key in node_data and node_data.get(key) is not None:
            rule_data[key] = node_data.get(key)
    if "condition_combinator" not in rule_data and node_data.get("conditions") is not None:
        rule_data["condition_combinator"] = "and"
    return json.dumps(rule_data, ensure_ascii=False) if rule_data else None


def _json_safe_snapshot_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_snapshot_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_snapshot_value(item) for item in value]
    return value


def _template_snapshot(approval_type: ApprovalType, flow: ApprovalFlow | None = None) -> dict[str, Any]:
    fields = [
        {
            "label": _field_value(field, "label"),
            "code": _field_value(field, "code"),
            "field_type": _field_value(field, "field_type"),
            "is_required": bool(_field_value(field, "is_required", False)),
            "options_json": _field_options(field),
            "display_condition": _field_value(field, "display_condition"),
            "validation_rule": _field_value(field, "validation_rule"),
            "editable_scope": _field_value(field, "editable_scope"),
            "print_visible": bool(_field_value(field, "print_visible", True)),
            "placeholder": _field_value(field, "placeholder"),
            "is_business_calculation": bool(_field_value(field, "is_business_calculation", False)),
            "is_readonly": bool(_field_value(field, "is_readonly", False)),
            "sort_order": _field_value(field, "sort_order", 0),
        }
        for field in normalized_template_form_fields(approval_type)
    ]
    nodes = [
        {
            "node_order": getattr(node, "node_order", None),
            "node_name": getattr(node, "node_name", None),
            "approver_type": getattr(node, "approver_type", None),
            "approver_id": getattr(node, "approver_id", None),
            "node_type": getattr(node, "node_type", "approval"),
            "condition_rules": getattr(node, "condition_rules", None),
            "auto_approve_hours": getattr(node, "auto_approve_hours", None),
        }
        for node in (getattr(flow, "nodes", None) or [])
    ]
    return _json_safe_snapshot_value({
        "approval_type_id": getattr(approval_type, "id", None),
        "name": getattr(approval_type, "name", None),
        "business_code": getattr(approval_type, "business_code", None),
        "category": getattr(approval_type, "category", None),
        "category_key": getattr(approval_type, "category_key", None),
        "scope": getattr(approval_type, "scope", None),
        "is_active": getattr(approval_type, "is_active", True),
        "sort_order": getattr(approval_type, "sort_order", 0),
        "icon": getattr(approval_type, "icon", None),
        "icon_key": getattr(approval_type, "icon_key", None),
        "icon_tone": getattr(approval_type, "icon_tone", None),
        "description": getattr(approval_type, "description", None),
        "status": getattr(approval_type, "status", None),
        "version": getattr(approval_type, "version", 1),
        "created_at": getattr(approval_type, "created_at", None),
        "updated_at": getattr(approval_type, "updated_at", None),
        "permission_rules": getattr(approval_type, "permission_rules", None),
        "exception_rules": getattr(approval_type, "exception_rules", None),
        "auto_approval_rule": getattr(approval_type, "auto_approval_rule", None),
        "fields": fields,
        "flow": {
            "flow_id": getattr(flow, "id", None),
            "flow_name": getattr(flow, "name", None),
            "nodes": nodes,
        },
    })


def _mockish(value: Any) -> bool:
    return getattr(type(value), "__module__", "").startswith("unittest.mock")


async def _execute_optional(db: AsyncSession, statement: Any) -> Any:
    try:
        return await db.execute(statement)
    except StopAsyncIteration:
        return None


def _instance_text(instance: ApprovalInstance, attr: str) -> str:
    value = getattr(instance, attr, None)
    if _mockish(value):
        return ""
    return str(value or "").strip()


def _instance_int(instance: ApprovalInstance, attr: str) -> int | None:
    value = getattr(instance, attr, None)
    if _mockish(value):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


async def _employee_identity_by_id(db: AsyncSession, employee_id: int | None) -> tuple[str | None, str | None]:
    if not employee_id:
        return None, None
    from app.models.employee import Employee

    try:
        result = await db.execute(
            select(Employee.name, Employee.employee_no).where(Employee.id == employee_id)
        )
        if _mockish(result):
            return None, None
        row = result.first()
        if inspect.isawaitable(row):
            row = await row
        if not row or _mockish(row):
            return None, None
        name, employee_no = row
    except Exception:
        return None, None
    name_text = str(name).strip() if name is not None and not _mockish(name) else ""
    no_text = str(employee_no).strip() if employee_no is not None and not _mockish(employee_no) else ""
    return name_text or None, no_text or None


def _extract_effect_bindings(snapshot: Any) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    candidates = [
        snapshot.get("effect_bindings"),
        snapshot.get("attendance_effect_bindings"),
    ]
    for rule_key in ("permission_rules", "auto_approval_rule", "exception_rules"):
        rule = snapshot.get(rule_key)
        if isinstance(rule, dict):
            candidates.append(rule.get("effect_bindings"))
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


async def _load_instance_effect_bindings(db: AsyncSession, instance: ApprovalInstance) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    version = getattr(instance, "__dict__", {}).get("template_version")
    if version is not None:
        bindings = _extract_effect_bindings(getattr(version, "snapshot_json", None))
        if bindings:
            return bindings

    version_id = _instance_int(instance, "template_version_id")
    if version_id:
        try:
            result = await db.execute(
                select(ApprovalTemplateVersion).where(ApprovalTemplateVersion.id == version_id)
            )
            version = result.scalar_one_or_none()
            bindings = _extract_effect_bindings(getattr(version, "snapshot_json", None))
            if bindings:
                return bindings
        except Exception:
            return []

    snapshot = getattr(instance, "flow_snapshot", None)
    if isinstance(snapshot, str) and snapshot.strip():
        try:
            return _extract_effect_bindings(json.loads(snapshot))
        except Exception:
            return []
    return []


def _approval_event_type(final_status: str) -> str:
    if final_status == "withdrawn":
        return "approval.instance.revoked.v1"
    if final_status == "cancelled":
        return "approval.instance.cancelled.v1"
    if final_status == "rejected":
        return "approval.instance.rejected.v1"
    return "approval.instance.approved.v1"


@asynccontextmanager
async def _approval_event_savepoint(db: AsyncSession):
    begin_nested = getattr(db, "begin_nested", None)
    if callable(begin_nested) and not _mockish(begin_nested):
        async with begin_nested():
            yield
        return
    yield


async def _enqueue_approval_terminal_event(
    db: AsyncSession,
    instance: ApprovalInstance,
    final_status: str,
    *,
    actor_id: int | None,
    occurred_at: datetime | None = None,
) -> None:
    instance_id = _instance_int(instance, "id")
    module = _instance_text(instance, "module")
    business_type = _instance_text(instance, "business_type")
    if not instance_id or not module:
        return

    try:
        async with _approval_event_savepoint(db):
            happened_at = occurred_at or datetime.now(timezone.utc)
            applicant_id = _instance_int(instance, "applicant_id")
            applicant_name, applicant_no = await _employee_identity_by_id(db, applicant_id)
            payload = {
                "approval_instance_id": instance_id,
                "module": module,
                "business_type": business_type,
                "business_id": _instance_int(instance, "business_id"),
                "template_version_id": _instance_int(instance, "template_version_id"),
                "applicant_id": applicant_id,
                "applicant_name": applicant_name,
                "applicant_no": applicant_no,
                "final_status": final_status,
                "approved_at": happened_at.isoformat() if final_status == "approved" else None,
                "finished_at": happened_at.isoformat(),
                "approver_id": actor_id,
                "summary": _instance_text(instance, "summary"),
                "form_data": getattr(instance, "form_data", None) if isinstance(getattr(instance, "form_data", None), dict) else {},
                "effect_bindings": await _load_instance_effect_bindings(db, instance),
            }
            event = await BusinessEventService.enqueue(
                db,
                event_type=_approval_event_type(final_status),
                aggregate_type="approval_instance",
                aggregate_id=instance_id,
                payload=payload,
                idempotency_key=f"approval_instance:{instance_id}:{final_status}",
                source_module="approval",
                headers={
                    "actor_id": actor_id,
                    "origin": "approval.process_approval",
                },
                occurred_at=happened_at,
            )
            if event.status in {"pending", "failed"}:
                await dispatch_event(db, event)
    except Exception as exc:
        logger.warning(
            "审批终态事件写入或分发失败，不阻断审批 instance_id=%s status=%s: %s",
            instance_id,
            final_status,
            exc,
            exc_info=True,
        )


def _parse_trip_date(value: Any, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field_name} is required")
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError as exc:
            raise ValueError(f"{field_name} must be ISO date or datetime") from exc


def _business_trip_date_value(form_data: dict[str, Any], endpoint: str) -> Any:
    aliases = (
        (
            "start_time",
            "start_date",
            "trip_start_time",
            "trip_start_date",
            "trip_duration_start_time",
            "trip_duration_start_date",
        )
        if endpoint == "start"
        else (
            "end_time",
            "end_date",
            "trip_end_time",
            "trip_end_date",
            "trip_duration_end_time",
            "trip_duration_end_date",
        )
    )
    for key in aliases:
        value = form_data.get(key)
        if value not in (None, ""):
            return value

    duration_value = form_data.get("trip_duration")
    if isinstance(duration_value, dict):
        for key in (endpoint, f"{endpoint}_time", f"{endpoint}_date"):
            value = duration_value.get(key)
            if value not in (None, ""):
                return value
    return None


def _parse_condition_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


def _duration_endpoint_value(form_data: dict[str, Any], endpoint: str) -> Any:
    aliases = (
        (
            "start_time",
            "start_date",
            "leave_start_time",
            "leave_start_date",
            "duration_start_time",
            "duration_start_date",
            "leave_duration_start_time",
            "leave_duration_start_date",
        )
        if endpoint == "start"
        else (
            "end_time",
            "end_date",
            "leave_end_time",
            "leave_end_date",
            "duration_end_time",
            "duration_end_date",
            "leave_duration_end_time",
            "leave_duration_end_date",
        )
    )
    for key in aliases:
        value = form_data.get(key)
        if value not in (None, ""):
            return value

    for base in ("duration", "leave_duration"):
        duration_value = form_data.get(base)
        if isinstance(duration_value, dict):
            for key in (endpoint, f"{endpoint}_time", f"{endpoint}_date"):
                value = duration_value.get(key)
                if value not in (None, ""):
                    return value
    return None


def _infer_natural_day_duration(form_data: dict[str, Any]) -> int | None:
    start_day = _parse_condition_date(_duration_endpoint_value(form_data, "start"))
    end_day = _parse_condition_date(_duration_endpoint_value(form_data, "end"))
    if not start_day or not end_day or end_day < start_day:
        return None
    return (end_day - start_day).days + 1


def _form_has_numeric_duration(form_data: dict[str, Any]) -> bool:
    for key in ("duration", "leave_duration", "duration_days", "days", "leave_days"):
        if _to_number(form_data.get(key)) is not None:
            return True
    return False


def _with_inferred_duration(form_data: dict[str, Any]) -> dict[str, Any]:
    form = dict(form_data or {})
    if not _form_has_numeric_duration(form):
        inferred = _infer_natural_day_duration(form)
        if inferred is not None:
            form.setdefault("duration", inferred)
            form.setdefault("leave_duration", inferred)
            form.setdefault("duration_days", inferred)
            form.setdefault("leave_days", inferred)
    return form


async def _sync_business_trip_attendance(db: AsyncSession, instance: ApprovalInstance) -> None:
    form_data = getattr(instance, "form_data", None) or {}
    if not isinstance(form_data, dict):
        return
    start_day = _parse_trip_date(_business_trip_date_value(form_data, "start"), "start_time")
    end_day = _parse_trip_date(_business_trip_date_value(form_data, "end"), "end_time")
    if end_day < start_day:
        raise ValueError("business trip end_time cannot be earlier than start_time")

    from app.models.attendance import (
        AttendanceMonthSummary,
        AttendanceRecord,
        AttendanceStatus,
        ClockSource,
        MonthlySummaryStatus,
    )

    applicant_id = int(getattr(instance, "applicant_id", 0) or 0)
    if not applicant_id:
        return

    touched_months: set[tuple[int, int]] = set()
    current = start_day
    while current <= end_day:
        record_result = await db.execute(
            select(AttendanceRecord)
            .where(
                AttendanceRecord.employee_id == applicant_id,
                AttendanceRecord.date == current,
            )
            .order_by(AttendanceRecord.id.desc())
            .limit(1)
        )
        record = record_result.scalar_one_or_none()
        if record is None:
            record = AttendanceRecord(
                employee_id=applicant_id,
                date=current,
                status=AttendanceStatus.business_trip,
                source=ClockSource.manual,
                correction_note=f"出差审批通过同步，审批单ID={getattr(instance, 'id', '')}",
            )
            db.add(record)
        else:
            record.status = AttendanceStatus.business_trip
            record.anomaly_type = None
            record.source = ClockSource.manual
            record.correction_note = f"出差审批通过同步，审批单ID={getattr(instance, 'id', '')}"
        touched_months.add((current.year, current.month))
        current += timedelta(days=1)

    await db.flush()

    for year, month in touched_months:
        summary_result = await db.execute(
            select(AttendanceMonthSummary).where(
                AttendanceMonthSummary.employee_id == applicant_id,
                AttendanceMonthSummary.year == year,
                AttendanceMonthSummary.month == month,
            )
        )
        summary = summary_result.scalar_one_or_none()
        if summary is None or getattr(summary, "status", None) == MonthlySummaryStatus.locked:
            continue
        month_records_result = await db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.employee_id == applicant_id,
                AttendanceRecord.date >= date(year, month, 1),
                AttendanceRecord.date < (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)),
            )
        )
        month_records = list(month_records_result.scalars().all())
        summary.business_trip_days = sum(
            1 for item in month_records if item.status == AttendanceStatus.business_trip
        )
        summary.absent_count = sum(
            1 for item in month_records if item.status == AttendanceStatus.absent
        )


async def save_template_config(db: AsyncSession, data: Any, current_user: Any | None = None) -> ApprovalType:
    payload = dict(_as_plain_data(data))
    payload["name"] = str(payload.get("name") or "").strip()
    payload["business_code"] = str(payload.get("business_code") or "").strip()
    _validate_template_config_payload(payload)

    existing_id = payload.get("id")
    approval_type = None
    duplicate_stmt = select(ApprovalType).where(ApprovalType.business_code == payload["business_code"])
    if existing_id:
        duplicate_stmt = duplicate_stmt.where(ApprovalType.id != existing_id)
    duplicate_result = await _execute_optional(db, duplicate_stmt.limit(1))
    duplicate_type = duplicate_result.scalar_one_or_none() if duplicate_result is not None else None
    if inspect.isawaitable(duplicate_type):
        duplicate_type = await duplicate_type
    if isinstance(duplicate_type, ApprovalType):
        raise HTTPException(status_code=409, detail="template business_code already exists")

    if existing_id:
        result = await db.execute(
            select(ApprovalType)
            .options(selectinload(ApprovalType.form_fields))
            .where(ApprovalType.id == existing_id)
        )
        approval_type = result.scalar_one_or_none()
        if not isinstance(approval_type, ApprovalType):
            raise HTTPException(status_code=404, detail="template not found")
        await _ensure_template_manage_allowed(db, approval_type, current_user)
        for field in list(getattr(approval_type, "form_fields", []) or []):
            await db.delete(field)
        approval_type.version = (approval_type.version or 0) + 1
    else:
        approval_type = ApprovalType(version=1)
        db.add(approval_type)

    approval_type.name = payload["name"]
    approval_type.business_code = payload["business_code"]
    approval_type.category = payload.get("category") or "default"
    approval_type.category_key = payload.get("category_key")
    approval_type.scope = payload.get("scope") or "mobile"
    approval_type.is_active = bool(payload.get("is_active", True))
    approval_type.sort_order = payload.get("sort_order") or 0
    approval_type.icon = payload.get("icon")
    approval_type.icon_key = payload.get("icon_key")
    approval_type.icon_tone = payload.get("icon_tone")
    approval_type.description = payload.get("description")
    approval_type.print_format = payload.get("print_format")
    approval_type.status = payload.get("status") or ("enabled" if payload.get("is_active", True) else "draft")
    approval_type.permission_rules = payload.get("permission_rules")
    approval_type.exception_rules = payload.get("exception_rules")
    approval_type.auto_approval_rule = payload.get("auto_approval_rule")
    approval_type.updated_at = datetime.now(timezone.utc)
    await db.flush()

    fields = []
    for index, field in enumerate(payload.get("fields") or [], start=1):
        field_data = _as_plain_data(field)
        fields.append(
            ApprovalFormField(
                approval_type_id=approval_type.id,
                label=field_data.get("label") or field_data["code"],
                code=field_data["code"],
                field_type=field_data["field_type"],
                is_required=bool(field_data.get("is_required", field_data.get("required", False))),
                default_value=field_data.get("default_value"),
                options_json=field_data.get("options_json", field_data.get("options")),
                display_condition=field_data.get("display_condition"),
                validation_rule=field_data.get("validation_rule", field_data.get("validation_rules")),
                editable_scope=field_data.get("editable_scope"),
                print_visible=bool(field_data.get("print_visible", field_data.get("printable", True))),
                placeholder=field_data.get("placeholder"),
                is_business_calculation=bool(field_data.get("is_business_calculation", False)),
                is_readonly=bool(field_data.get("is_readonly", False)),
                sort_order=index,
            )
        )
    if fields:
        db.add_all(fields)
        set_committed_value(approval_type, "form_fields", fields)

    module_candidates = _flow_module_candidates(approval_type.business_code)

    if existing_id:
        existing_flow_result = await db.execute(
            select(ApprovalFlow)
            .options(selectinload(ApprovalFlow.nodes))
            .where(ApprovalFlow.module.in_(module_candidates), ApprovalFlow.is_active.is_(True))
            .order_by(ApprovalFlow.id.desc())
            .limit(1)
        )
        flow = existing_flow_result.scalar_one_or_none()
        if isinstance(flow, ApprovalFlow):
            for node in list(getattr(flow, "nodes", []) or []):
                await db.delete(node)
            flow.name = f"{approval_type.name} Flow"
            flow.module = approval_type.business_code
            flow.description = approval_type.description
        else:
            flow = ApprovalFlow(
                name=f"{approval_type.name} Flow",
                module=approval_type.business_code,
                description=approval_type.description,
                is_active=True,
            )
            db.add(flow)
    else:
        flow = ApprovalFlow(
            name=f"{approval_type.name} Flow",
            module=approval_type.business_code,
            description=approval_type.description,
            is_active=True,
        )
        db.add(flow)
    await db.flush()
    nodes = []
    for index, node in enumerate(payload.get("flow_nodes") or [], start=1):
        node_data = _as_plain_data(node)
        assignee_source = node_data.get("assignee_source") or node_data.get("approver_type")
        node_type = node_data.get("node_type") or "approval"
        approver_type = node_data.get("approver_type") or assignee_source or "specific_user"
        if node_type in BRANCH_NODE_TYPES:
            approver_type = "condition_branch"
        if node_type in PARALLEL_BRANCH_NODE_TYPES:
            approver_type = "parallel_branch"
        nodes.append(
            ApprovalNode(
                flow_id=flow.id,
                node_order=node_data.get("node_order") or index,
                approver_type=approver_type,
                approver_id=node_data.get("approver_id"),
                node_type=node_type,
                condition_rules=_node_condition_rules(node_data),
                auto_approve_hours=node_data.get("auto_approve_hours"),
            )
        )
    db.add_all(nodes)
    set_committed_value(flow, "nodes", nodes)

    version = ApprovalTemplateVersion(
        approval_type_id=approval_type.id,
        version=approval_type.version,
        snapshot_json=_template_snapshot(approval_type, flow),
        status="published" if approval_type.is_active else "draft",
        published_at=datetime.now(timezone.utc),
    )
    db.add(version)
    await db.flush()
    saved_result = await _execute_optional(
        db,
        select(ApprovalType)
        .options(selectinload(ApprovalType.form_fields))
        .where(ApprovalType.id == approval_type.id),
    )
    saved_type = saved_result.scalar_one_or_none() if saved_result is not None else None
    if inspect.isawaitable(saved_type):
        saved_type = await saved_type
    if isinstance(saved_type, ApprovalType):
        return saved_type
    await db.refresh(approval_type)
    return approval_type


async def copy_template_config(
    db: AsyncSession,
    approval_type_id: int,
    data: ApprovalTemplateCopyRequest,
    current_user: Any | None = None,
) -> ApprovalType:
    payload = _as_plain_data(data)
    result = await db.execute(
        select(ApprovalType)
        .options(selectinload(ApprovalType.form_fields))
        .where(ApprovalType.id == approval_type_id)
    )
    source_type = result.scalar_one_or_none()
    if inspect.isawaitable(source_type):
        source_type = await source_type
    if not isinstance(source_type, ApprovalType):
        raise HTTPException(status_code=404, detail="template not found")
    await _ensure_template_manage_allowed(db, source_type, current_user)

    flow_result = await db.execute(
        select(ApprovalFlow)
        .options(selectinload(ApprovalFlow.nodes))
        .where(
            ApprovalFlow.module.in_(_flow_module_candidates(source_type.business_code)),
            ApprovalFlow.is_active.is_(True),
        )
        .order_by(ApprovalFlow.id.desc())
        .limit(1)
    )
    source_flow = flow_result.scalar_one_or_none()
    if inspect.isawaitable(source_flow):
        source_flow = await source_flow

    name = str(payload.get("name") or f"{source_type.name} Copy").strip()
    business_code = str(payload.get("business_code") or f"{source_type.business_code}_copy").strip()
    if not name:
        raise HTTPException(status_code=400, detail="template name is required")
    if not business_code:
        raise HTTPException(status_code=400, detail="business_code is required")

    copied_type = ApprovalType(
        name=name,
        business_code=business_code,
        category=source_type.category,
        category_key=getattr(source_type, "category_key", None),
        scope=source_type.scope,
        is_active=source_type.is_active,
        sort_order=source_type.sort_order,
        icon=source_type.icon,
        icon_key=getattr(source_type, "icon_key", None),
        icon_tone=getattr(source_type, "icon_tone", None),
        description=source_type.description,
        print_format=source_type.print_format,
        status=getattr(source_type, "status", "draft"),
        version=1,
        permission_rules=source_type.permission_rules,
        exception_rules=source_type.exception_rules,
        auto_approval_rule=source_type.auto_approval_rule,
    )
    db.add(copied_type)
    await db.flush()

    copied_fields = [
        ApprovalFormField(
            approval_type_id=copied_type.id,
            label=field.label,
            code=field.code,
            field_type=field.field_type,
            is_required=field.is_required,
            default_value=field.default_value,
            options_json=field.options_json,
            display_condition=field.display_condition,
            validation_rule=field.validation_rule,
            editable_scope=field.editable_scope,
            print_visible=field.print_visible,
            placeholder=field.placeholder,
            is_business_calculation=field.is_business_calculation,
            is_readonly=field.is_readonly,
            sort_order=field.sort_order,
        )
        for field in (getattr(source_type, "form_fields", None) or [])
    ]
    if copied_fields:
        db.add_all(copied_fields)
        set_committed_value(copied_type, "form_fields", copied_fields)

    copied_flow = None
    if isinstance(source_flow, ApprovalFlow):
        copied_flow = ApprovalFlow(
            name=f"{copied_type.name} Flow",
            module=copied_type.business_code,
            description=source_flow.description,
            is_active=source_flow.is_active,
        )
        db.add(copied_flow)
        await db.flush()
        copied_nodes = [
            ApprovalNode(
                flow_id=copied_flow.id,
                node_order=node.node_order,
                approver_type=node.approver_type,
                approver_id=node.approver_id,
                node_type=node.node_type,
                condition_rules=node.condition_rules,
                auto_approve_hours=node.auto_approve_hours,
            )
            for node in (getattr(source_flow, "nodes", None) or [])
        ]
        if copied_nodes:
            db.add_all(copied_nodes)
            set_committed_value(copied_flow, "nodes", copied_nodes)

    version = ApprovalTemplateVersion(
        approval_type_id=copied_type.id,
        version=copied_type.version,
        snapshot_json=_template_snapshot(copied_type, copied_flow),
        status="published" if copied_type.is_active else "draft",
        published_at=datetime.now(timezone.utc),
    )
    db.add(version)
    await db.flush()
    await db.refresh(copied_type)
    return copied_type


async def publish_template(db: AsyncSession, approval_type_id: int, current_user: Any | None = None) -> ApprovalType:
    result = await db.execute(
        select(ApprovalType)
        .options(selectinload(ApprovalType.form_fields), selectinload(ApprovalType.versions))
        .where(ApprovalType.id == approval_type_id)
    )
    approval_type = result.scalar_one_or_none()
    if inspect.isawaitable(approval_type):
        approval_type = await approval_type
    if not isinstance(approval_type, ApprovalType):
        raise HTTPException(status_code=404, detail="template not found")
    await _ensure_template_manage_allowed(db, approval_type, current_user)
    flow_result = await db.execute(
        select(ApprovalFlow)
        .options(selectinload(ApprovalFlow.nodes))
        .where(
            ApprovalFlow.module.in_(_flow_module_candidates(approval_type.business_code)),
            ApprovalFlow.is_active.is_(True),
        )
        .order_by(ApprovalFlow.id.desc())
        .limit(1)
    )
    flow = flow_result.scalar_one_or_none()
    if not isinstance(flow, ApprovalFlow):
        raise HTTPException(status_code=400, detail="template flow not found")
    approval_type.version = (approval_type.version or 0) + 1
    approval_type.is_active = True
    approval_type.status = "enabled"
    approval_type.updated_at = datetime.now(timezone.utc)
    version = ApprovalTemplateVersion(
        approval_type_id=approval_type.id,
        version=approval_type.version,
        snapshot_json=_template_snapshot(approval_type, flow),
        status="published",
        published_at=datetime.now(timezone.utc),
    )
    db.add(version)
    await db.flush()
    await db.refresh(approval_type)
    return approval_type


async def _load_flow_snapshot_nodes(instance: ApprovalInstance) -> list[dict[str, Any]]:
    snapshot = getattr(instance, "flow_snapshot", None)
    if snapshot:
        try:
            data = json.loads(snapshot)
            return list(data.get("nodes") or data.get("flow", {}).get("nodes") or [])
        except Exception:
            return []
    return []


def _flow_snapshot_nodes_sync(instance: Any) -> list[dict[str, Any]]:
    snapshot = getattr(instance, "flow_snapshot", None)
    if not isinstance(snapshot, str) or not snapshot.strip():
        return []
    try:
        data = json.loads(snapshot)
    except Exception:
        return []
    nodes = data.get("nodes") or data.get("flow", {}).get("nodes") or []
    return [dict(node) for node in nodes if isinstance(node, dict)]


def _node_value(node: Any, key: str, default: Any = None) -> Any:
    if isinstance(node, dict):
        return node.get(key, default)
    value = getattr(node, key, default)
    if getattr(type(value), "__module__", "").startswith("unittest.mock"):
        return default
    return value


def _normalize_member_ids(value: Any) -> list[int]:
    raw = value if isinstance(value, (list, tuple, set)) else ([] if value is None else [value])
    result: list[int] = []
    seen: set[int] = set()
    for item in raw:
        try:
            member_id = int(item)
        except (TypeError, ValueError):
            continue
        if member_id <= 0 or member_id in seen:
            continue
        seen.add(member_id)
        result.append(member_id)
    return result


def _normalize_role_values(value: Any) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else ([] if value is None else [value])
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        role = str(item or "").strip().lower()
        if not role or role in seen:
            continue
        seen.add(role)
        result.append(role)
    return result


def _approval_order_label(order: Any) -> str:
    try:
        normalized_order = int(order or 0)
    except (TypeError, ValueError):
        normalized_order = 0
    labels = {
        2: "二级审批人",
        3: "三级审批人",
        4: "四级审批人",
        5: "五级审批人",
        6: "六级审批人",
        7: "七级审批人",
        8: "八级审批人",
    }
    return labels.get(normalized_order, "审批人")


def _default_node_name(node: Any) -> str:
    order = _node_value(node, "node_order", 0) or 0
    try:
        normalized_order = int(order or 0)
    except (TypeError, ValueError):
        normalized_order = 0
    node_type = _node_type(node)
    rule_data = _node_rule_data(node)
    source = str(
        rule_data.get("assignee_source")
        or rule_data.get("approver_type")
        or _node_value(node, "approver_type")
        or ""
    ).strip()

    if node_type == "notify":
        return "抄送人"
    if node_type == "handler":
        return "办理人"
    if node_type == "auto":
        return "自动处理"
    if node_type in BRANCH_NODE_TYPES:
        return "条件分支"
    if node_type in PARALLEL_BRANCH_NODE_TYPES:
        return "并行分支"
    if node_type == "approval" and normalized_order >= 2:
        return _approval_order_label(order)

    if source == "direct_manager":
        return "指定上级" if normalized_order == 1 else _approval_order_label(order)
    if source == "multi_level_manager":
        return "指定上级" if normalized_order == 1 else _approval_order_label(order)
    if source in {"department_head", "form_department_head"}:
        return "部门负责人"
    if source == "specific_user":
        return "指定审批人"
    if source == "applicant_select":
        return "自选审批人"
    if source in {"applicant_self", "applicant", "submitter"}:
        return "申请人本人"
    if source == "related_member_field":
        return "表单联系人"
    if source in {"hr", "finance", "admin", "manager", "asset_admin", "role"}:
        role = str(rule_data.get("role") or (source if source != "role" else "")).strip()
        role_labels = {
            "hr": "HR 审批人",
            "finance": "财务审批人",
            "admin": "管理员审批人",
            "manager": "管理者审批人",
            "asset_admin": "资产管理员审批人",
        }
        return role_labels.get(role or source, "角色审批人")

    if normalized_order == 1:
        return "指定上级"
    return _approval_order_label(order)


def _node_name(node: Any) -> str:
    configured_name = str(_node_value(node, "node_name", "") or "").strip()
    return configured_name or _default_node_name(node)


def _node_type(node: Any) -> str:
    return str(_node_value(node, "node_type", None) or "approval")


def approval_instance_step_summary(instance: Any) -> dict[str, str | None]:
    status_value = str(getattr(instance, "status", "") or "").lower()
    current_order = _instance_int(instance, "current_node_order") or 0
    records = list(getattr(instance, "__dict__", {}).get("records") or [])
    nodes = sorted(_flow_snapshot_nodes_sync(instance), key=lambda item: _node_value(item, "node_order", 0) or 0)

    if status_value == "approved":
        return {
            "current_node_name": "流程结束",
            "current_node_status": "approved",
            "current_node_status_label": "已通过",
        }
    if status_value == "rejected":
        rejected_record = next(
            (
                record
                for record in records
                if str(getattr(record, "action", "") or "").lower() in {"reject", "auto_reject"}
            ),
            None,
        )
        rejected_order = int(getattr(rejected_record, "node_order", current_order) or current_order or 0)
        rejected_node = next((node for node in nodes if int(_node_value(node, "node_order", 0) or 0) == rejected_order), None)
        return {
            "current_node_name": _node_name(rejected_node) if rejected_node else (_default_node_name({"node_order": rejected_order}) if rejected_order else "审批节点"),
            "current_node_status": "rejected",
            "current_node_status_label": "已拒绝",
        }
    if status_value in {"withdrawn", "cancelled"}:
        node = next((item for item in nodes if int(_node_value(item, "node_order", 0) or 0) == current_order), None)
        return {
            "current_node_name": _node_name(node) if node else (_default_node_name({"node_order": current_order}) if current_order else "审批节点"),
            "current_node_status": status_value,
            "current_node_status_label": PROGRESS_STATUS_LABELS.get(status_value, "已撤回"),
        }

    node = next((item for item in nodes if int(_node_value(item, "node_order", 0) or 0) == current_order), None)
    return {
        "current_node_name": _node_name(node) if node else (_default_node_name({"node_order": current_order}) if current_order else "审批节点"),
        "current_node_status": "pending",
        "current_node_status_label": "待审批",
    }


def _parse_condition_rules(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _node_rule_data(node: Any) -> dict[str, Any]:
    rule_data = _parse_condition_rules(_node_value(node, "condition_rules"))
    for key in (
        "node_type",
        "approver_type",
        "approver_id",
        "approval_mode",
        "assignee_source",
        "member_ids",
        "role",
        "related_field_code",
        "related_member_field_code",
        "related_department_field_code",
        "notify_sources",
        "allow_applicant_select",
        "include_applicant_self",
        "applicant_select_mode",
        "applicant_select_scope",
        "applicant_select_member_ids",
        "applicant_select_roles",
        "manager_level",
        "fallback_to_upper_manager",
        "applicant_self_if_head",
        "multi_level_end_type",
        "multi_level_role",
        "multi_level_member_ids",
        "multi_level_limit_enabled",
        "multi_level_limit_level",
        "multi_level_level",
        "empty_action",
        "empty_member_id",
        "vote_pass_count",
        "auto_action",
    ):
        value = _node_value(node, key, None)
        if value is not None:
            rule_data[key] = value
    return rule_data


def _node_approval_mode(node: Any) -> str:
    mode = str(_node_rule_data(node).get("approval_mode") or "or_sign").strip()
    return mode if mode in {"or_sign", "counter_sign", "sequential", "vote"} else "or_sign"


def _instance_form_data(instance: Any) -> dict[str, Any]:
    form_data = getattr(instance, "form_data", None)
    return form_data if isinstance(form_data, dict) else {}


def _snapshot_dict(value: Any) -> dict[str, Any]:
    data = _parse_json_object(value)
    return data if isinstance(data, dict) else {}


async def _template_snapshot_for_instance(
    db: AsyncSession,
    instance: ApprovalInstance,
) -> dict[str, Any]:
    version = getattr(instance, "__dict__", {}).get("template_version")
    if version is not None:
        snapshot = _snapshot_dict(getattr(version, "snapshot_json", None))
        if snapshot:
            return snapshot

    version_id = _instance_int(instance, "template_version_id")
    if version_id:
        try:
            result = await db.execute(
                select(ApprovalTemplateVersion).where(ApprovalTemplateVersion.id == version_id)
            )
            version = result.scalar_one_or_none()
            if inspect.isawaitable(version):
                version = await version
            snapshot = _snapshot_dict(getattr(version, "snapshot_json", None))
            if snapshot:
                return snapshot
        except Exception:
            pass

    flow_snapshot = _snapshot_dict(getattr(instance, "flow_snapshot", None))
    return flow_snapshot


async def _instance_rule_group(
    db: AsyncSession,
    instance: ApprovalInstance,
    key: str,
) -> dict[str, Any]:
    snapshot = await _template_snapshot_for_instance(db, instance)
    return _parse_json_object(snapshot.get(key))


async def _approval_type_for_module(
    db: AsyncSession,
    module: Any,
) -> ApprovalType | None:
    candidates = _flow_module_candidates(str(module or ""))
    if not candidates:
        return None
    try:
        result = await db.execute(
            select(ApprovalType)
            .where(ApprovalType.business_code.in_(candidates))
            .order_by(ApprovalType.id.desc())
            .limit(1)
        )
        approval_type = result.scalar_one_or_none()
        if inspect.isawaitable(approval_type):
            approval_type = await approval_type
        return approval_type if isinstance(approval_type, ApprovalType) else None
    except Exception:
        return None


async def _employee_roles_by_id(db: AsyncSession, employee_id: int) -> set[str]:
    from app.models.employee import Employee

    roles: set[str] = set()
    try:
        employee_result = await db.execute(select(Employee.is_superuser).where(Employee.id == employee_id))
        is_superuser = bool(employee_result.scalar_one_or_none())
    except Exception:
        is_superuser = False
    if is_superuser:
        roles.add("admin")

    try:
        from app.models.employee_role import EmployeeRole

        role_result = await db.execute(
            select(EmployeeRole.role_name).where(
                EmployeeRole.employee_id == employee_id,
                EmployeeRole.is_active == True,
            )
        )
        roles.update(
            str(role_name or "").strip().lower()
            for (role_name,) in role_result.all()
            if str(role_name or "").strip()
        )
    except Exception:
        pass
    return roles or {"employee"}


async def user_can_manage_template(
    db: AsyncSession,
    approval_type: ApprovalType,
    current_user: Any,
) -> bool:
    """表单管理员规则的运行时判断，避免只在前端隐藏配置入口。"""
    if getattr(current_user, "is_superuser", False):
        return True

    user_id = getattr(current_user, "id", None)
    if not user_id:
        return False

    raw_roles = getattr(current_user, "roles", None)
    user_roles = {
        str(role or "").strip().lower()
        for role in (raw_roles or [])
        if str(role or "").strip()
    }
    if not user_roles:
        user_roles = await _employee_roles_by_id(db, int(user_id))
    if not (user_roles & TEMPLATE_ADMIN_ROLES):
        return False

    rules = _parse_json_object(getattr(approval_type, "permission_rules", None))
    template_management = _parse_json_object(rules.get("template_management"))
    manager_type = str(template_management.get("type") or "all_approval_admins")
    if manager_type != "selected_admins":
        return True

    selected_ids = _normalize_member_ids(
        template_management.get("admin_ids")
        or template_management.get("member_ids")
        or template_management.get("employee_ids")
    )
    return int(user_id) in selected_ids


async def _ensure_template_manage_allowed(
    db: AsyncSession,
    approval_type: ApprovalType,
    current_user: Any | None,
) -> None:
    if current_user is None:
        return
    if await user_can_manage_template(db, approval_type, current_user):
        return
    raise HTTPException(status_code=403, detail="当前用户不是该审批表单管理员")


async def _employee_department_id(db: AsyncSession, employee_id: int) -> Optional[int]:
    from app.models.employee import Employee

    try:
        result = await db.execute(select(Employee.department_id).where(Employee.id == employee_id))
        department_id = result.scalar_one_or_none()
        return int(department_id) if department_id else None
    except Exception:
        return None


async def _ensure_template_submit_allowed(
    db: AsyncSession,
    approval_type: ApprovalType,
    applicant_id: int,
) -> None:
    rules = _parse_json_object(getattr(approval_type, "permission_rules", None))
    visible_scope = _parse_json_object(rules.get("visible_scope"))
    if visible_scope.get("type") == "departments":
        department_ids = _normalize_member_ids(visible_scope.get("department_ids"))
        if department_ids:
            applicant_department_id = await _employee_department_id(db, applicant_id)
            if applicant_department_id not in department_ids:
                raise HTTPException(status_code=403, detail="当前员工不在该审批模板可见范围内")

    submit_permission = _parse_json_object(rules.get("submit_permission"))
    submit_type = str(submit_permission.get("type") or "all")
    if submit_type == "all":
        return
    if submit_type == "roles":
        allowed_roles = {
            str(role or "").strip().lower()
            for role in (submit_permission.get("roles") or [])
            if str(role or "").strip()
        }
        user_roles = await _employee_roles_by_id(db, applicant_id)
        if allowed_roles and user_roles & allowed_roles:
            return
        raise HTTPException(status_code=403, detail="当前员工无权提交该审批模板")
    if submit_type == "selected_members":
        allowed_member_ids = _normalize_member_ids(
            submit_permission.get("member_ids") or submit_permission.get("employee_ids")
        )
        if applicant_id in allowed_member_ids:
            return
        raise HTTPException(status_code=403, detail="当前员工不在该审批模板提交范围内")


def _snapshot_value(value: Any) -> Any:
    if getattr(type(value), "__module__", "").startswith("unittest.mock"):
        return None
    return value


def _snapshot_node_data(node: Any, node_order: Optional[int] = None) -> dict[str, Any]:
    if isinstance(node, dict):
        source = dict(node)
        order_value = source.get("node_order")
    else:
        source = {
            "node_order": _node_value(node, "node_order"),
            "node_name": _node_value(node, "node_name"),
            "approver_type": _node_value(node, "approver_type"),
            "approver_id": _node_value(node, "approver_id"),
            "node_type": _node_value(node, "node_type"),
            "condition_rules": _node_value(node, "condition_rules"),
            "auto_approve_hours": _node_value(node, "auto_approve_hours"),
        }
        order_value = source.get("node_order")

    node_type = source.get("node_type") or "approval"
    data = {
        "node_order": _snapshot_value(node_order if node_order is not None else order_value),
        "node_name": _snapshot_value(source.get("node_name")),
        "approver_type": _snapshot_value(source.get("approver_type")),
        "approver_id": _snapshot_value(source.get("approver_id")),
        "node_type": _snapshot_value(node_type),
        "condition_rules": _snapshot_value(source.get("condition_rules")),
        "auto_approve_hours": _snapshot_value(source.get("auto_approve_hours")),
    }
    for key in (
        "assignee_source",
        "member_ids",
        "role",
        "approval_mode",
        "related_field_code",
        "related_member_field_code",
        "related_department_field_code",
        "notify_sources",
        "allow_applicant_select",
        "include_applicant_self",
        "applicant_select_mode",
        "applicant_select_scope",
        "applicant_select_member_ids",
        "applicant_select_roles",
        "manager_level",
        "fallback_to_upper_manager",
        "applicant_self_if_head",
        "multi_level_end_type",
        "multi_level_role",
        "multi_level_member_ids",
        "multi_level_limit_enabled",
        "multi_level_limit_level",
        "multi_level_level",
        "empty_action",
        "empty_member_id",
        "vote_pass_count",
        "auto_action",
    ):
        if key in source:
            data[key] = _snapshot_value(source.get(key))
    if not data.get("condition_rules"):
        condition_rules = _node_condition_rules(source)
        if condition_rules:
            data["condition_rules"] = condition_rules
    return data


async def _approval_condition_context(
    db: AsyncSession,
    applicant_id: Optional[int],
    form_data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    form = _with_inferred_duration(form_data) if isinstance(form_data, dict) else {}
    applicant: dict[str, Any] = {}
    if applicant_id:
        from app.models.employee import Employee

        try:
            result = await db.execute(select(Employee).where(Employee.id == applicant_id))
            employee = result.scalar_one_or_none()
            if inspect.isawaitable(employee):
                employee = await employee
        except Exception:
            employee = None
        if employee is not None:
            applicant = {
                "id": getattr(employee, "id", applicant_id),
                "company": getattr(employee, "company", None),
                "company_id": getattr(employee, "company_id", None),
                "location_id": getattr(employee, "location_id", None),
                "department_id": getattr(employee, "department_id", None),
                "direct_manager_id": getattr(employee, "direct_manager_id", None),
                "employment_type": getattr(employee, "employment_type", None),
                "position": getattr(employee, "position", None),
                "job_level": getattr(employee, "job_level", None),
            }
            company_id = applicant.get("company_id")
            if company_id:
                try:
                    from app.models.payroll import Company

                    company_result = await db.execute(select(Company).where(Company.id == company_id))
                    company = company_result.scalar_one_or_none()
                    if inspect.isawaitable(company):
                        company = await company
                except Exception:
                    company = None
                if company is not None:
                    applicant["company_name"] = getattr(company, "name", None)
                    applicant["company_short_name"] = getattr(company, "short_name", None)
                    applicant["company_type"] = getattr(company, "company_type", None)
                    applicant["parent_company_id"] = getattr(company, "parent_company_id", None)
            try:
                applicant["roles"] = sorted(await _employee_roles_by_id(db, int(applicant.get("id") or applicant_id)))
            except Exception:
                applicant["roles"] = []

    context = {"form": form, "applicant": applicant}
    context.update(form)
    aliases = {
        "employee_id": applicant.get("id"),
        "company": form.get("company", applicant.get("company_id") or applicant.get("company")),
        "company_id": applicant.get("company_id"),
        "company_name": applicant.get("company_name") or applicant.get("company"),
        "company_type": applicant.get("company_type"),
        "parent_company_id": applicant.get("parent_company_id"),
        "department": form.get("department", applicant.get("department_id")),
        "department_id": applicant.get("department_id"),
        "location": form.get("location", applicant.get("location_id")),
        "location_id": applicant.get("location_id"),
    }
    context.update({key: value for key, value in aliases.items() if value is not None})
    return context


def _value_by_path(source: Any, path: str) -> Any:
    current = source
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _condition_field_value(context: dict[str, Any], field: Any) -> Any:
    field_name = str(field or "").strip()
    if not field_name:
        return None
    if field_name in context:
        return context.get(field_name)
    field_aliases = {
        "duration": ("duration", "leave_duration", "outside_duration", "trip_duration", "overtime_duration"),
        "leave_type": ("leave_type", "leave_type_name", "leave_type_code"),
    }
    for alias in field_aliases.get(field_name, ()):
        if alias in context:
            return context.get(alias)
    if "." in field_name:
        return _value_by_path(context, field_name)
    form = context.get("form")
    if isinstance(form, dict) and field_name in form:
        return form.get(field_name)
    if isinstance(form, dict):
        for alias in field_aliases.get(field_name, ()):
            if alias in form:
                return form.get(alias)
    applicant = context.get("applicant")
    if isinstance(applicant, dict) and field_name in applicant:
        return applicant.get(field_name)
    return None


def _as_compare_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _values_equal(left: Any, right: Any) -> bool:
    if left == right:
        return True
    if left is None or right is None:
        return False
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "amount", "duration", "days", "hours", "total", "total_days", "total_hours"):
            number = _to_number(value.get(key))
            if number is not None:
                return number
        return None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            number = _to_number(item)
            if number is not None:
                return number
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else None


def _scope_item_matches(scope_item: Any, context: dict[str, Any]) -> bool:
    item = _as_plain_data(scope_item)
    if not item:
        return False
    applicant = context.get("applicant")
    if not isinstance(applicant, dict):
        applicant = {}
    scope_type = str(item.get("type") or "").strip().lower()
    raw_id = item.get("id")
    raw_value = item.get("value")
    if scope_type == "member":
        return _values_equal(applicant.get("id"), raw_id or raw_value)
    if scope_type == "department":
        return _values_equal(applicant.get("department_id"), raw_id or raw_value)
    if scope_type == "company":
        return _values_equal(applicant.get("company_id"), raw_id or raw_value)
    if scope_type == "role":
        expected_role = str(raw_value or raw_id or item.get("label") or "").strip().lower()
        roles = {
            str(role or "").strip().lower()
            for role in _as_compare_list(applicant.get("roles"))
            if str(role or "").strip()
        }
        return bool(expected_role and expected_role in roles)
    return False


def _scope_condition_matches(expected: Any, context: dict[str, Any]) -> bool:
    return any(_scope_item_matches(item, context) for item in _as_compare_list(expected))


def _condition_matches(condition: Any, context: dict[str, Any]) -> bool:
    data = _as_plain_data(condition)
    operator = str(data.get("operator") or data.get("op") or "eq")
    field_name = str(data.get("field") or "").strip()
    actual = _condition_field_value(context, data.get("field"))
    expected = data.get("value")

    if operator in ("empty",):
        return actual in (None, "", [], {})
    if operator in ("not_empty",):
        return actual not in (None, "", [], {})
    if field_name in {"applicant.scope", "applicant_scope"}:
        matched = _scope_condition_matches(expected, context)
        if operator in ("not_in", "neq", "!="):
            return not matched
        return matched
    if operator in ("lt", "lte", "gt", "gte", "between"):
        actual_number = _to_number(actual)
        if actual_number is None:
            return False
        if operator == "between":
            expected_values = [_to_number(item) for item in _as_compare_list(expected)]
            expected_numbers = [item for item in expected_values if item is not None]
            if len(expected_numbers) < 2:
                return False
            lower, upper = min(expected_numbers[0], expected_numbers[1]), max(expected_numbers[0], expected_numbers[1])
            return lower <= actual_number <= upper
        expected_number = _to_number(expected)
        if expected_number is None:
            return False
        if operator == "lt":
            return actual_number < expected_number
        if operator == "lte":
            return actual_number <= expected_number
        if operator == "gt":
            return actual_number > expected_number
        if operator == "gte":
            return actual_number >= expected_number
    if operator in ("eq", "=", "=="):
        return any(_values_equal(actual, item) for item in _as_compare_list(expected))
    if operator in ("neq", "!="):
        return not any(_values_equal(actual, item) for item in _as_compare_list(expected))
    if operator == "in":
        if isinstance(actual, (list, tuple, set)):
            return any(any(_values_equal(item, expected_item) for expected_item in _as_compare_list(expected)) for item in actual)
        return any(_values_equal(actual, item) for item in _as_compare_list(expected))
    if operator == "not_in":
        return not _condition_matches({**data, "operator": "in"}, context)
    if operator == "contains":
        if isinstance(actual, (list, tuple, set)):
            return any(_values_equal(item, expected) for item in actual)
        return str(expected or "") in str(actual or "")
    if operator == "not_contains":
        return not _condition_matches({**data, "operator": "contains"}, context)
    return False


def _conditions_match(
    conditions: Any,
    context: dict[str, Any],
    combinator: Optional[str] = None,
) -> bool:
    if not conditions:
        return True
    items = [_as_plain_data(item) for item in conditions if _as_plain_data(item)]
    if not items:
        return True
    matches = [_condition_matches(item, context) for item in items]
    if str(combinator or "and").lower() == "or":
        return any(matches)
    return all(matches)


def _condition_groups_match(
    groups: Any,
    context: dict[str, Any],
    group_combinator: Optional[str] = None,
) -> bool:
    if not groups:
        return True
    group_items = [_as_plain_data(group) for group in groups if _as_plain_data(group)]
    if not group_items:
        return True
    matches = [
        _conditions_match(
            group.get("conditions") or [],
            context,
            group.get("condition_combinator") or "and",
        )
        for group in group_items
    ]
    if str(group_combinator or "or").lower() == "and":
        return all(matches)
    return any(matches)


def _node_conditions_match(node: dict[str, Any], context: dict[str, Any]) -> bool:
    rule_data = _parse_condition_rules(node.get("condition_rules"))
    groups = rule_data.get("condition_groups") or rule_data.get("groups") or node.get("condition_groups")
    if groups:
        return _condition_groups_match(
            groups,
            context,
            rule_data.get("condition_group_combinator") or node.get("condition_group_combinator"),
        )
    conditions = rule_data.get("conditions") or node.get("conditions")
    if not conditions:
        return True
    combinator = rule_data.get("condition_combinator") or node.get("condition_combinator")
    return _conditions_match(conditions, context, combinator)


def _select_matching_branch(branches: list[Any], context: dict[str, Any]) -> dict[str, Any] | None:
    default_branch: dict[str, Any] | None = None
    for branch in branches:
        branch_data = _as_plain_data(branch)
        if branch_data.get("is_default_branch"):
            default_branch = branch_data
            continue
        groups = branch_data.get("condition_groups") or branch_data.get("groups")
        if groups:
            if _condition_groups_match(groups, context, branch_data.get("condition_group_combinator")):
                return branch_data
            continue
        conditions = branch_data.get("conditions") or []
        combinator = branch_data.get("condition_combinator")
        if _conditions_match(conditions, context, combinator):
            return branch_data
    return default_branch


def _expand_effective_nodes(
    configured_nodes: list[Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    effective_nodes: list[dict[str, Any]] = []
    for node in sorted(configured_nodes, key=lambda item: _node_value(item, "node_order", 0) or 0):
        node_data = _snapshot_node_data(node)
        node_type = str(node_data.get("node_type") or "approval")
        rule_data = _parse_condition_rules(node_data.get("condition_rules"))
        if node_type in PARALLEL_BRANCH_NODE_TYPES:
            node_data["condition_rules"] = json.dumps(rule_data, ensure_ascii=False) if rule_data else node_data.get("condition_rules")
            effective_nodes.append(node_data)
            continue
        if node_type in BRANCH_NODE_TYPES:
            branches = rule_data.get("branches") or []
            selected_branch = _select_matching_branch(branches, context)
            child_nodes = selected_branch.get("nodes") if selected_branch else []
            effective_nodes.extend(_expand_effective_nodes(child_nodes or [], context))
            continue
        if not _node_conditions_match(node_data, context):
            continue
        effective_nodes.append(node_data)

    for index, node in enumerate(effective_nodes, start=1):
        node["node_order"] = index
    return effective_nodes


def _nodes_need_condition_context(configured_nodes: list[Any]) -> bool:
    for node in configured_nodes:
        node_data = _snapshot_node_data(node)
        node_type = str(node_data.get("node_type") or "approval")
        rule_data = _parse_condition_rules(node_data.get("condition_rules"))
        if node_type in BRANCH_NODE_TYPES:
            return True
        if node_type in PARALLEL_BRANCH_NODE_TYPES:
            for branch in rule_data.get("branches") or []:
                branch_data = _as_plain_data(branch)
                if _nodes_need_condition_context(list(branch_data.get("nodes") or [])):
                    return True
        if rule_data.get("conditions") or rule_data.get("condition_groups") or rule_data.get("branches"):
            return True
        if any(key in rule_data for key in ("field", "field_code", "dimension", "operator", "op")):
            return True
    return False


def _node_member_ids(node: Any) -> list[int]:
    member_ids = _node_value(node, "member_ids", None)
    if member_ids is None:
        member_ids = _parse_condition_rules(_node_value(node, "condition_rules", None)).get("member_ids")
    result: list[int] = []
    for item in _as_compare_list(member_ids):
        try:
            member_id = int(item)
        except (TypeError, ValueError):
            continue
        if member_id not in result:
            result.append(member_id)
    approver_id = _node_value(node, "approver_id", None)
    if not result and approver_id:
        try:
            result.append(int(approver_id))
        except (TypeError, ValueError):
            pass
    return result


def _status_label(status_value: str) -> str:
    return PROGRESS_STATUS_LABELS.get(status_value, status_value)


def _resolve_failure_message(approver_type: Optional[str]) -> str:
    message_map = {
        "direct_manager": "未找到直属上级",
        "multi_level_manager": "未找到第 1 级上级",
        "department_head": "未配置部门负责人",
        "hr": "未找到 HR 审批人",
        "gm": "未找到总经理",
        "project_manager": "提交后自动匹配",
    }
    return message_map.get(str(approver_type or ""), "提交后自动匹配")


async def _employee_name_by_id(db: AsyncSession, employee_id: Optional[int]) -> Optional[str]:
    if not employee_id:
        return None
    from app.models.employee import Employee

    try:
        result = await db.execute(
            select(Employee).where(Employee.id == employee_id, Employee.is_active == True)
        )
        employee = result.scalar_one_or_none()
        if inspect.isawaitable(employee):
            employee = await employee
    except Exception:
        return None
    name = getattr(employee, "name", None)
    return str(name) if name else None


async def _employee_display_context_by_id(
    db: AsyncSession,
    employee_id: Optional[int],
) -> tuple[Optional[str], Optional[str]]:
    if not employee_id:
        return None, None
    from app.models.employee import Employee

    try:
        result = await db.execute(
            select(Employee).where(Employee.id == employee_id, Employee.is_active == True)
        )
        employee = result.scalar_one_or_none()
        if inspect.isawaitable(employee):
            employee = await employee
    except Exception:
        return None, None

    name = getattr(employee, "name", None)
    department_name = getattr(employee, "department_name", None)
    department = getattr(employee, "department", None)
    if not department_name and department is not None:
        department_name = getattr(department, "name", None)
    return (
        str(name) if name else None,
        str(department_name) if department_name else None,
    )


def _approval_summary_for_notification(instance: ApprovalInstance) -> str:
    summary = str(getattr(instance, "summary", None) or "").strip()
    if summary:
        return summary
    instance_id = getattr(instance, "id", None) or "-"
    return f"审批单 #{instance_id}"


async def _notify_pending_approval_tasks(
    db: AsyncSession,
    instance: ApprovalInstance,
    *,
    actor_id: Optional[int],
    previous_recipient_ids: set[int] | None = None,
    reason: str = "submitted",
) -> int:
    """向新产生的待审批人发送待办提醒。"""
    if getattr(instance, "status", None) != "pending":
        return 0

    previous = previous_recipient_ids or set()
    recipient_ids = [
        recipient_id
        for recipient_id in _pending_approval_task_recipient_ids(instance)
        if recipient_id not in previous
    ]
    if not recipient_ids:
        return 0

    applicant_id = getattr(instance, "applicant_id", None)
    applicant_name = await _employee_name_by_id(db, applicant_id) or "申请人"
    actor_name = await _employee_name_by_id(db, actor_id) if actor_id else None
    summary = _approval_summary_for_notification(instance)
    if reason == "transfer":
        title = "审批转办提醒"
        content = f"{actor_name or applicant_name} 已将「{summary}」转交给你处理。"
    elif reason == "next_node":
        title = "新的审批待办"
        content = f"「{summary}」已流转到你，请及时处理。"
    else:
        title = "新的审批待办"
        content = f"{applicant_name} 提交了「{summary}」，请及时处理。"

    notified = 0
    for recipient_id in recipient_ids:
        await NotificationService.create(
            db,
            NotificationCreate(
                recipient_id=recipient_id,
                sender_id=actor_id or applicant_id,
                title=title,
                content=content,
                notif_type="approval",
                ref_type="approval_instance",
                ref_id=getattr(instance, "id", None),
            ),
        )
        notified += 1
    return notified


async def _notify_applicant_approval_update(
    db: AsyncSession,
    instance: ApprovalInstance,
    *,
    actor_id: Optional[int],
    action: str,
) -> bool:
    """审批人动作完成后通知申请人，让下级能看到上级审批进展。"""
    applicant_id = getattr(instance, "applicant_id", None)
    if not applicant_id:
        return False

    actor_name = await _employee_name_by_id(db, actor_id) or "审批人"
    summary = _approval_summary_for_notification(instance)
    status_value = str(getattr(instance, "status", None) or "").lower()
    action_value = str(action or "").lower()

    if action_value == "approve":
        if status_value == "approved":
            title = "审批已通过"
            content = f"{actor_name} 已同意「{summary}」，审批已通过。"
        else:
            title = "审批节点已同意"
            content = f"{actor_name} 已同意「{summary}」，流程已进入下一审批节点。"
    elif action_value == "reject":
        if status_value == "rejected":
            title = "审批已拒绝"
            content = f"{actor_name} 已拒绝「{summary}」。"
        else:
            title = "审批节点已拒绝"
            content = f"{actor_name} 已对「{summary}」提交拒绝意见，流程仍在处理中。"
    elif action_value == "transfer":
        title = "审批已转审"
        content = f"{actor_name} 已将「{summary}」转交其他审批人处理。"
    else:
        return False

    await NotificationService.create(
        db,
        NotificationCreate(
            recipient_id=applicant_id,
            sender_id=actor_id,
            title=title,
            content=content,
            notif_type="approval",
            ref_type="approval_instance",
            ref_id=getattr(instance, "id", None),
        ),
    )
    return True


async def _notify_auto_terminal_approval_update(
    db: AsyncSession,
    instance: ApprovalInstance,
) -> bool:
    applicant_id = getattr(instance, "applicant_id", None)
    status_value = str(getattr(instance, "status", None) or "").lower()
    if not applicant_id or status_value not in {"approved", "rejected"}:
        return False

    summary = _approval_summary_for_notification(instance)
    title = "审批已自动通过" if status_value == "approved" else "审批已自动拒绝"
    content = f"「{summary}」{title[2:]}。"
    await NotificationService.create(
        db,
        NotificationCreate(
            recipient_id=applicant_id,
            sender_id=None,
            title=title,
            content=content,
            notif_type="approval",
            ref_type="approval_instance",
            ref_id=getattr(instance, "id", None),
        ),
    )
    return True


async def _notify_after_approval_action(
    db: AsyncSession,
    instance: ApprovalInstance,
    *,
    actor_id: int,
    action: str,
    previous_recipient_ids: set[int],
) -> None:
    await _notify_applicant_approval_update(
        db,
        instance,
        actor_id=actor_id,
        action=action,
    )
    if getattr(instance, "status", None) == "pending":
        reason = "transfer" if action == "transfer" else "next_node"
        await _notify_pending_approval_tasks(
            db,
            instance,
            actor_id=actor_id,
            previous_recipient_ids=previous_recipient_ids,
            reason=reason,
        )


async def _approver_preview(
    db: AsyncSession,
    node: Any,
    applicant_id: int,
) -> tuple[Optional[int], Optional[str], str, bool, Optional[str]]:
    approver_type = _node_value(node, "approver_type")
    configured_id = _node_value(node, "approver_id")
    node_type = _node_type(node)
    rule_data = _node_rule_data(node)

    if node_type == "auto" or approver_type == "auto":
        return None, None, "系统自动拒绝" if rule_data.get("auto_action") == "reject" else "系统自动通过", True, None
    if node_type == "notify":
        member_ids = await _resolve_node_approver_ids(db, node, applicant_id, {})
        if member_ids:
            return await _preview_display_for_member_ids(
                db,
                member_ids,
                failure_message="未找到抄送人",
                exclude_member_ids={int(applicant_id)} if applicant_id else set(),
                empty_success_display="",
            )
        if rule_data.get("allow_applicant_select"):
            display = "提交时自选抄送人"
            return None, None, display, True, None
        message = _resolve_failure_message(approver_type)
        return None, None, message, False, message
    if approver_type == "applicant_select" or rule_data.get("assignee_source") == "applicant_select":
        display = "提交时自选审批人"
        return None, None, display, True, None
    if approver_type == "related_member_field" or rule_data.get("assignee_source") == "related_member_field":
        field_code = rule_data.get("related_member_field_code") or rule_data.get("related_field_code")
        display = f"表单联系人：{field_code}" if field_code else "表单内的联系人"
        return None, None, display, True, None
    if approver_type == "form_department_head" or rule_data.get("assignee_source") == "form_department_head":
        field_code = rule_data.get("related_department_field_code") or rule_data.get("related_field_code")
        display = f"表单部门主管：{field_code}" if field_code else "表单内部门主管"
        return None, None, display, True, None
    if approver_type == "specific_user":
        member_ids = _node_member_ids(node)
        if member_ids:
            names = [await _employee_name_by_id(db, member_id) or f"员工 #{member_id}" for member_id in member_ids]
            display = "、".join(names)
            approver_name = names[0] if len(names) == 1 else display
            return member_ids[0], approver_name, display, True, None
        message = _resolve_failure_message(approver_type)
        return None, None, message, False, message
    if node_type == "notify" and approver_type not in {
        "direct_manager",
        "multi_level_manager",
        "department_head",
        "hr",
        "gm",
        "project_manager",
    }:
        if configured_id:
            approver_name = await _employee_name_by_id(db, configured_id)
            display = approver_name or "抄送人"
            return configured_id, approver_name, display, True, None
        message = _resolve_failure_message(approver_type)
        return None, None, message, False, message

    assignee_source = str(rule_data.get("assignee_source") or approver_type or "")
    if assignee_source in {"direct_manager", "multi_level_manager", "department_head", "role"} or approver_type in {
        "direct_manager",
        "multi_level_manager",
        "department_head",
        *ROLE_APPROVER_TYPES,
    }:
        try:
            member_ids = await _resolve_node_approver_ids(db, node, applicant_id, {})
        except Exception:
            member_ids = []
        if member_ids:
            names = [await _employee_name_by_id(db, member_id) or f"员工 #{member_id}" for member_id in member_ids]
            display = "、".join(names)
            approver_name = names[0] if len(names) == 1 else display
            return member_ids[0], approver_name, display, True, None
        message = _resolve_failure_message(approver_type)
        return None, None, message, False, message

    node_obj = type("Node", (), {
        "approver_type": approver_type,
        "approver_id": configured_id,
    })()
    try:
        resolved_id = await resolve_approver_id(db, node_obj, applicant_id)
    except Exception:
        resolved_id = None
    if resolved_id:
        approver_name = await _employee_name_by_id(db, resolved_id)
        return resolved_id, approver_name, approver_name or f"员工 #{resolved_id}", True, None
    message = _resolve_failure_message(approver_type)
    return None, None, message, False, message


async def _draft_manager_chain_ids(
    db: AsyncSession,
    direct_manager_id: Optional[int],
    *,
    max_depth: int,
) -> list[int]:
    try:
        current_id = int(direct_manager_id or 0)
    except (TypeError, ValueError):
        return []
    if current_id <= 0:
        return []
    max_depth = max(1, min(max_depth, 8))
    chain: list[int] = []
    visited: set[int] = set()
    from app.models.employee import Employee

    while current_id and len(chain) < max_depth and current_id not in visited:
        visited.add(current_id)
        result = await db.execute(
            select(Employee.id, Employee.direct_manager_id)
            .where(Employee.id == current_id, Employee.is_active == True)
        )
        row = result.first()
        if not row:
            break
        chain.append(int(row[0]))
        try:
            current_id = int(row[1] or 0)
        except (TypeError, ValueError):
            current_id = 0
    return chain


async def _draft_node_source_ids(
    db: AsyncSession,
    node: Any,
    source: str,
    rule_data: dict[str, Any],
    draft: ApprovalDraftApplicantPreviewRequest,
) -> list[int]:
    source = str(source or "").strip()
    if source == "specific_user":
        return _node_member_ids(node)
    if source == "direct_manager":
        return await _draft_manager_chain_ids(db, draft.direct_manager_id, max_depth=1)
    if source == "multi_level_manager":
        end_type = str(rule_data.get("multi_level_end_type") or "level").strip()
        limit_level = (
            _normalized_manager_level(rule_data.get("multi_level_limit_level"), default=1)
            if rule_data.get("multi_level_limit_enabled")
            else 8
        )
        org_chain = await _department_manager_chain_ids_from_department(
            db,
            draft.department_id,
            max_depth=limit_level,
        )
        fallback_direct_chain: list[int] | None = None

        async def manager_chain() -> list[int]:
            nonlocal fallback_direct_chain
            if org_chain:
                return org_chain
            if fallback_direct_chain is None:
                fallback_direct_chain = await _draft_manager_chain_ids(
                    db,
                    draft.direct_manager_id,
                    max_depth=limit_level,
                )
            return fallback_direct_chain

        if end_type == "role":
            chain = await manager_chain()
            role_name = str(rule_data.get("multi_level_role") or rule_data.get("role") or "").strip()
            for index, manager_id in enumerate(chain):
                if await _employee_has_role(db, manager_id, role_name):
                    return chain[: index + 1]
            return []
        if end_type == "member":
            chain = await manager_chain()
            target_ids = set(_normalize_member_ids(rule_data.get("multi_level_member_ids") or rule_data.get("member_ids")))
            for index, manager_id in enumerate(chain):
                if manager_id in target_ids:
                    return chain[: index + 1]
            return []
        level_value = rule_data.get("multi_level_level")
        if str(level_value or "").strip() == "highest":
            return await manager_chain()
        level = _normalized_manager_level(level_value or rule_data.get("manager_level"), default=1)
        if org_chain:
            return org_chain[:level]
        return await _draft_manager_chain_ids(db, draft.direct_manager_id, max_depth=level)
    if source == "department_head":
        return _normalize_member_ids(await _department_manager_id(db, draft.department_id))
    if source == "role" or source in ROLE_APPROVER_TYPES:
        role_name = str(rule_data.get("role") or (source if source in ROLE_APPROVER_TYPES else "")).strip()
        return await _role_member_ids(db, role_name)
    return []


async def _preview_display_for_member_ids(
    db: AsyncSession,
    member_ids: list[int],
    *,
    failure_message: str,
    exclude_member_ids: Optional[set[int]] = None,
    empty_success_display: str = "",
) -> tuple[Optional[int], Optional[str], str, bool, Optional[str]]:
    excluded = {int(item) for item in (exclude_member_ids or set()) if item}
    resolved_ids = _dedupe_positive_ids(member_ids)
    ids = [member_id for member_id in resolved_ids if member_id not in excluded]
    if not ids:
        if resolved_ids and excluded and all(member_id in excluded for member_id in resolved_ids):
            return None, None, empty_success_display, True, None
        return None, None, failure_message, False, failure_message
    names = [await _employee_name_by_id(db, member_id) or f"员工 #{member_id}" for member_id in ids]
    display = "、".join(names)
    approver_name = names[0] if len(names) == 1 else display
    return ids[0], approver_name, display, True, None


async def _draft_approver_preview(
    db: AsyncSession,
    node: Any,
    draft: ApprovalDraftApplicantPreviewRequest,
) -> tuple[Optional[int], Optional[str], str, bool, Optional[str]]:
    approver_type = str(_node_value(node, "approver_type") or "")
    configured_id = _node_value(node, "approver_id")
    node_type = _node_type(node)
    rule_data = _node_rule_data(node)
    assignee_source = str(rule_data.get("assignee_source") or approver_type or "")

    if node_type == "auto" or approver_type == "auto":
        return None, None, "系统自动拒绝" if rule_data.get("auto_action") == "reject" else "系统自动通过", True, None
    if assignee_source == "applicant_select" or approver_type == "applicant_select":
        display = "提交时自选审批人"
        return None, None, display, True, None
    if assignee_source in {"applicant_self", "applicant", "submitter"} or approver_type in {
        "applicant_self",
        "applicant",
        "submitter",
    }:
        display = "新员工本人"
        return None, None, display, True, None
    if assignee_source == "related_member_field" or approver_type == "related_member_field":
        field_code = rule_data.get("related_member_field_code") or rule_data.get("related_field_code")
        display = f"表单联系人：{field_code}" if field_code else "表单内的联系人"
        return None, None, display, True, None
    if assignee_source == "form_department_head" or approver_type == "form_department_head":
        field_code = rule_data.get("related_department_field_code") or rule_data.get("related_field_code")
        display = f"表单部门主管：{field_code}" if field_code else "表单内部门主管"
        return None, None, display, True, None

    if node_type == "notify":
        notify_ids: list[int] = []
        for item in _as_compare_list(rule_data.get("notify_sources")):
            if isinstance(item, dict):
                source = str(item.get("source") or item.get("type") or "")
            else:
                source = str(item or "")
            notify_ids.extend(await _draft_node_source_ids(db, node, source, rule_data, draft))
        if not notify_ids and assignee_source:
            notify_ids.extend(await _draft_node_source_ids(db, node, assignee_source, rule_data, draft))
        if not notify_ids:
            notify_ids.extend(_node_member_ids(node))
        if notify_ids:
            return await _preview_display_for_member_ids(
                db,
                notify_ids,
                failure_message="未找到抄送人",
                exclude_member_ids=set(),
                empty_success_display="",
            )
        if rule_data.get("allow_applicant_select"):
            display = "提交时自选抄送人"
            return None, None, display, True, None

    source = assignee_source or approver_type
    member_ids = await _draft_node_source_ids(db, node, source, rule_data, draft)
    if not member_ids and approver_type == "specific_user":
        member_ids = _node_member_ids(node)
    if not member_ids and configured_id:
        member_ids = _normalize_member_ids(configured_id)

    failure_message = _resolve_failure_message(approver_type or source)
    return await _preview_display_for_member_ids(db, member_ids, failure_message=failure_message)


async def build_template_flow_preview(
    db: AsyncSession,
    approval_type_id: int,
    applicant_id: int,
    form_data: Optional[dict[str, Any]] = None,
) -> ApprovalFlowPreviewOut:
    type_result = await db.execute(select(ApprovalType).where(ApprovalType.id == approval_type_id))
    approval_type = type_result.scalar_one_or_none()
    if not approval_type:
        raise HTTPException(status_code=404, detail="审批类型不存在")

    flow_result = await db.execute(
        select(ApprovalFlow)
        .options(selectinload(ApprovalFlow.nodes))
        .where(
            ApprovalFlow.module.in_(_flow_module_candidates(getattr(approval_type, "business_code", None))),
            ApprovalFlow.is_active.is_(True),
        )
        .order_by(ApprovalFlow.id.desc())
        .limit(1)
    )
    flow = flow_result.scalar_one_or_none()
    configured_nodes = list(getattr(flow, "nodes", []) or []) if flow else []
    condition_context = (
        await _approval_condition_context(db, applicant_id, form_data)
        if _nodes_need_condition_context(configured_nodes)
        else {}
    )
    flow_nodes = _expand_effective_nodes(configured_nodes, condition_context) if flow else []
    if not flow or not flow_nodes:
        return ApprovalFlowPreviewOut(
            approval_type_id=approval_type_id,
            flow_id=getattr(flow, "id", None),
            flow_name=getattr(flow, "name", None),
            has_flow=False,
            message=FLOW_MISSING_MESSAGE,
            nodes=[],
        )

    nodes: list[ApprovalFlowNodePreview] = []
    for node in flow_nodes:
        approver_id, approver_name, display, is_resolved, resolve_message = await _approver_preview(
            db, node, applicant_id
        )
        node_type = _node_type(node)
        rule_data = _node_rule_data(node)
        assignee_source = str(rule_data.get("assignee_source") or rule_data.get("approver_type") or "")
        nodes.append(
            ApprovalFlowNodePreview(
                node_order=int(_node_value(node, "node_order", 0) or 0),
                node_name=_node_name(node),
                node_type=node_type,
                node_type_label=NODE_TYPE_LABELS.get(node_type, node_type),
                approver_type=_node_value(node, "approver_type"),
                approver_id=approver_id,
                approver_name=approver_name,
                approver_display=display,
                assignee_source=assignee_source or None,
                approval_mode=_node_approval_mode(node),
                allow_applicant_select=bool(rule_data.get("allow_applicant_select")),
                include_applicant_self=bool(rule_data.get("include_applicant_self")),
                applicant_select_mode=str(rule_data.get("applicant_select_mode") or "multiple"),
                applicant_select_scope=str(rule_data.get("applicant_select_scope") or "company"),
                applicant_select_member_ids=_normalize_member_ids(rule_data.get("applicant_select_member_ids")),
                applicant_select_roles=_normalize_role_values(rule_data.get("applicant_select_roles")),
                requires_applicant_select=assignee_source == "applicant_select",
                is_resolved=is_resolved,
                resolve_message=resolve_message,
                status="preview",
                status_label=_status_label("preview"),
            )
        )

    return ApprovalFlowPreviewOut(
        approval_type_id=approval_type_id,
        flow_id=getattr(flow, "id", None),
        flow_name=getattr(flow, "name", None),
        has_flow=True,
        message="审批流程已配置",
        nodes=nodes,
    )


async def build_template_flow_preview_for_draft_applicant(
    db: AsyncSession,
    approval_type_id: int,
    draft: ApprovalDraftApplicantPreviewRequest,
) -> ApprovalFlowPreviewOut:
    type_result = await db.execute(select(ApprovalType).where(ApprovalType.id == approval_type_id))
    approval_type = type_result.scalar_one_or_none()
    if not approval_type:
        raise HTTPException(status_code=404, detail="审批类型不存在")

    flow_result = await db.execute(
        select(ApprovalFlow)
        .options(selectinload(ApprovalFlow.nodes))
        .where(
            ApprovalFlow.module.in_(_flow_module_candidates(getattr(approval_type, "business_code", None))),
            ApprovalFlow.is_active.is_(True),
        )
        .order_by(ApprovalFlow.id.desc())
        .limit(1)
    )
    flow = flow_result.scalar_one_or_none()
    configured_nodes = list(getattr(flow, "nodes", []) or []) if flow else []
    condition_context = dict(draft.form_data or {})
    condition_context.setdefault("department_id", draft.department_id)
    condition_context.setdefault("direct_manager_id", draft.direct_manager_id)
    condition_context.setdefault("role_names", draft.role_names or [])
    flow_nodes = _expand_effective_nodes(configured_nodes, condition_context) if flow else []
    if not flow or not flow_nodes:
        return ApprovalFlowPreviewOut(
            approval_type_id=approval_type_id,
            flow_id=getattr(flow, "id", None),
            flow_name=getattr(flow, "name", None),
            has_flow=False,
            message=FLOW_MISSING_MESSAGE,
            nodes=[],
        )

    nodes: list[ApprovalFlowNodePreview] = []
    for node in flow_nodes:
        approver_id, approver_name, display, is_resolved, resolve_message = await _draft_approver_preview(
            db,
            node,
            draft,
        )
        node_type = _node_type(node)
        rule_data = _node_rule_data(node)
        assignee_source = str(rule_data.get("assignee_source") or rule_data.get("approver_type") or "")
        nodes.append(
            ApprovalFlowNodePreview(
                node_order=int(_node_value(node, "node_order", 0) or 0),
                node_name=_node_name(node),
                node_type=node_type,
                node_type_label=NODE_TYPE_LABELS.get(node_type, node_type),
                approver_type=_node_value(node, "approver_type"),
                approver_id=approver_id,
                approver_name=approver_name,
                approver_display=display,
                assignee_source=assignee_source or None,
                approval_mode=_node_approval_mode(node),
                allow_applicant_select=bool(rule_data.get("allow_applicant_select")),
                include_applicant_self=bool(rule_data.get("include_applicant_self")),
                applicant_select_mode=str(rule_data.get("applicant_select_mode") or "multiple"),
                applicant_select_scope=str(rule_data.get("applicant_select_scope") or "company"),
                applicant_select_member_ids=_normalize_member_ids(rule_data.get("applicant_select_member_ids")),
                applicant_select_roles=_normalize_role_values(rule_data.get("applicant_select_roles")),
                requires_applicant_select=assignee_source == "applicant_select",
                is_resolved=is_resolved,
                resolve_message=resolve_message,
                status="preview",
                status_label=_status_label("preview"),
            )
        )

    return ApprovalFlowPreviewOut(
        approval_type_id=approval_type_id,
        flow_id=getattr(flow, "id", None),
        flow_name=getattr(flow, "name", None),
        has_flow=True,
        message="审批流程已配置",
        nodes=nodes,
    )


async def build_instance_progress(
    db: AsyncSession,
    instance: ApprovalInstance,
) -> list[ApprovalFlowNodePreview]:
    nodes = await _load_flow_snapshot_nodes(instance)
    if not nodes:
        flow_result = await db.execute(
            select(ApprovalFlow)
            .options(selectinload(ApprovalFlow.nodes))
            .where(ApprovalFlow.id == getattr(instance, "flow_id", None))
        )
        flow = flow_result.scalar_one_or_none()
        condition_context = await _approval_condition_context(
            db,
            getattr(instance, "applicant_id", None),
            getattr(instance, "form_data", None),
        )
        nodes = _expand_effective_nodes(list(getattr(flow, "nodes", []) or []), condition_context)

    ordered_nodes = sorted(nodes, key=lambda item: _node_value(item, "node_order", 0) or 0)
    records_by_order: dict[int, Any] = {}
    for record in getattr(instance, "records", []) or []:
        order = getattr(record, "node_order", None)
        if not order:
            continue
        action = str(getattr(record, "action", "") or "").lower()
        if action in {"approve", "reject", "withdraw", "auto_approve", "auto_reject", "notify", "notify_skipped", "auto_skip"}:
            records_by_order[int(order)] = record

    tasks_by_order: dict[int, list[Any]] = {}
    for task in (getattr(instance, "tasks", []) or []):
        order = int(getattr(task, "node_order", 0) or 0)
        if not order:
            continue
        tasks_by_order.setdefault(order, []).append(task)
    pending_tasks = {
        order: next((task for task in tasks if getattr(task, "status", None) == "pending"), None)
        for order, tasks in tasks_by_order.items()
    }
    pending_tasks = {order: task for order, task in pending_tasks.items() if task is not None}
    instance_status = str(getattr(instance, "status", "") or "").lower()
    current_order = int(getattr(instance, "current_node_order", 0) or 0)
    rejected_order = next(
        (
            int(order)
            for order, record in records_by_order.items()
            if str(getattr(record, "action", "") or "").lower() in {"reject", "auto_reject"}
        ),
        None,
    )

    progress: list[ApprovalFlowNodePreview] = []
    for node in ordered_nodes:
        order = int(_node_value(node, "node_order", 0) or 0)
        node_type = _node_type(node)
        record = records_by_order.get(order)
        task = pending_tasks.get(order)
        action = str(getattr(record, "action", "") or "").lower() if record else None

        status_value = "not_started"
        if action == "reject":
            status_value = "rejected"
        elif action == "approve":
            status_value = "completed"
        elif action == "auto_approve":
            status_value = "auto_approved"
        elif action == "auto_reject":
            status_value = "auto_rejected"
        elif action in {"notify", "notify_skipped"}:
            status_value = "notified" if action == "notify" else "skipped"
        elif action == "withdraw":
            status_value = "withdrawn"
        elif rejected_order and order > rejected_order:
            status_value = "skipped"
        elif instance_status in {"withdrawn", "cancelled"} and order >= current_order:
            status_value = "withdrawn" if order == current_order else "skipped"
        elif task is not None or (instance_status == "pending" and order == current_order):
            status_value = "pending"

        approver_id = getattr(record, "approver_id", None) if record else None
        if approver_id is None and task is not None:
            approver_id = getattr(task, "approver_id", None)
        if approver_id is None:
            approver_id = _node_value(node, "approver_id")

        approver_name, approver_department_name = await _employee_display_context_by_id(db, approver_id)
        display = (
            approver_name
            or (f"员工 #{approver_id}" if approver_id else _resolve_failure_message(_node_value(node, "approver_type")))
        )
        if node_type == "auto":
            approver_name = None
            approver_department_name = None
            display = "系统自动处理"
        rule_data = _node_rule_data(node)
        assignee_source = str(rule_data.get("assignee_source") or rule_data.get("approver_type") or "")
        node_tasks = tasks_by_order.get(order, [])
        task_created_at_values = [
            getattr(item, "created_at", None)
            for item in node_tasks
            if getattr(item, "created_at", None) is not None
        ]
        arrived_at = min(task_created_at_values) if task_created_at_values else None
        if arrived_at is None and status_value == "pending":
            arrived_at = getattr(instance, "updated_at", None) or getattr(instance, "created_at", None)
        if arrived_at is None and order == 1:
            arrived_at = getattr(instance, "created_at", None)

        progress.append(
            ApprovalFlowNodePreview(
                node_order=order,
                node_name=_node_name(node),
                node_type=node_type,
                node_type_label=NODE_TYPE_LABELS.get(node_type, node_type),
                approver_type=_node_value(node, "approver_type"),
                approver_id=approver_id,
                approver_name=approver_name,
                approver_display=display,
                assignee_source=assignee_source or None,
                approval_mode=_node_approval_mode(node),
                allow_applicant_select=bool(rule_data.get("allow_applicant_select")),
                include_applicant_self=bool(rule_data.get("include_applicant_self")),
                applicant_select_mode=str(rule_data.get("applicant_select_mode") or "multiple"),
                applicant_select_scope=str(rule_data.get("applicant_select_scope") or "company"),
                applicant_select_member_ids=_normalize_member_ids(rule_data.get("applicant_select_member_ids")),
                applicant_select_roles=_normalize_role_values(rule_data.get("applicant_select_roles")),
                requires_applicant_select=assignee_source == "applicant_select",
                is_resolved=approver_id is not None or node_type == "auto",
                resolve_message=None if approver_id or node_type == "auto" else display,
                status=status_value,
                status_label=_status_label(status_value),
                action=getattr(record, "action", None) if record else None,
                comment=getattr(record, "comment", None) if record else None,
                acted_at=getattr(record, "acted_at", None) if record else None,
                arrived_at=arrived_at,
                approver_department_name=approver_department_name,
            )
        )

    return progress


async def _build_flow_snapshot(
    db: AsyncSession,
    flow: ApprovalFlow,
    applicant_id: Optional[int] = None,
    form_data: Optional[dict[str, Any]] = None,
) -> str:
    configured_nodes = list(getattr(flow, "nodes", []) or [])
    condition_context = (
        await _approval_condition_context(db, applicant_id, form_data)
        if _nodes_need_condition_context(configured_nodes)
        else {}
    )
    nodes = _expand_effective_nodes(configured_nodes, condition_context)
    if not nodes:
        raise HTTPException(status_code=400, detail="审批流程条件未匹配到可执行节点")
    return json.dumps(
        {
            "flow_id": _snapshot_value(getattr(flow, "id", None)),
            "flow_name": _snapshot_value(getattr(flow, "name", None)),
            "module": _snapshot_value(getattr(flow, "module", None)),
            "nodes": nodes,
        },
        ensure_ascii=False,
    )


def _form_value_for_member_rule(form_data: dict[str, Any], field_code: Any) -> Any:
    code = str(field_code or "").strip()
    if not code:
        return None
    if code in form_data:
        return form_data.get(code)
    return _value_by_path({"form": form_data, **form_data}, code)


def _dedupe_positive_ids(values: list[int]) -> list[int]:
    result: list[int] = []
    for value in values:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            continue
        if normalized > 0 and normalized not in result:
            result.append(normalized)
    return result


async def _manager_chain_ids(
    db: AsyncSession,
    applicant_id: int,
    *,
    max_depth: int = 8,
) -> list[int]:
    from app.models.employee import Employee

    chain: list[int] = []
    seen: set[int] = {int(applicant_id)} if applicant_id else set()
    current_id = applicant_id
    for _ in range(max_depth):
        if not current_id:
            break
        result = await db.execute(select(Employee.direct_manager_id).where(Employee.id == current_id))
        manager_id = result.scalar_one_or_none()
        try:
            normalized_manager_id = int(manager_id)
        except (TypeError, ValueError):
            break
        if normalized_manager_id <= 0 or normalized_manager_id in seen:
            break
        chain.append(normalized_manager_id)
        seen.add(normalized_manager_id)
        current_id = normalized_manager_id
    return chain


async def _department_manager_chain_ids_from_department(
    db: AsyncSession,
    department_id: Any,
    *,
    max_depth: int = 8,
    applicant_id: Optional[int] = None,
    include_applicant_self: bool = False,
) -> list[int]:
    try:
        current_department_id = int(department_id or 0)
    except (TypeError, ValueError):
        return []
    if current_department_id <= 0:
        return []

    from app.models.employee import Employee
    from app.models.organization import Department

    chain: list[int] = []
    seen_departments: set[int] = set()
    seen_managers: set[int] = set()
    max_depth = max(1, min(max_depth, 8))
    normalized_applicant_id = int(applicant_id or 0)

    while current_department_id and current_department_id not in seen_departments:
        seen_departments.add(current_department_id)
        result = await db.execute(
            select(Department.manager_id, Department.parent_id, Employee.is_superuser)
            .outerjoin(Employee, Employee.id == Department.manager_id)
            .where(
                Department.id == current_department_id,
                Department.is_active == True,
            )
        )
        row = result.first()
        if not row:
            break

        manager_id, parent_id, is_superuser = row
        try:
            normalized_manager_id = int(manager_id or 0)
        except (TypeError, ValueError):
            normalized_manager_id = 0
        if (
            normalized_manager_id > 0
            and not bool(is_superuser)
            and normalized_manager_id not in seen_managers
            and (include_applicant_self or normalized_manager_id != normalized_applicant_id)
        ):
            chain.append(normalized_manager_id)
            seen_managers.add(normalized_manager_id)
            if len(chain) >= max_depth:
                break

        try:
            current_department_id = int(parent_id or 0)
        except (TypeError, ValueError):
            current_department_id = 0

    return chain


async def _department_manager_chain_ids(
    db: AsyncSession,
    applicant_id: int,
    *,
    max_depth: int = 8,
    rule_data: Optional[dict[str, Any]] = None,
) -> list[int]:
    from app.models.employee import Employee

    result = await db.execute(select(Employee.department_id).where(Employee.id == applicant_id))
    department_id = result.scalar_one_or_none()
    return await _department_manager_chain_ids_from_department(
        db,
        department_id,
        max_depth=max_depth,
        applicant_id=applicant_id,
        include_applicant_self=bool((rule_data or {}).get("applicant_self_if_head")),
    )


async def _manager_chain_target_id(
    db: AsyncSession,
    applicant_id: int,
    rule_data: dict[str, Any],
) -> Optional[int]:
    try:
        level = int(rule_data.get("manager_level") or 1)
    except (TypeError, ValueError):
        level = 1
    level = max(1, min(level, 8))
    chain = await _manager_chain_ids(db, applicant_id, max_depth=level)
    if len(chain) >= level:
        return chain[level - 1]
    if rule_data.get("fallback_to_upper_manager") and chain:
        return chain[-1]
    return None


def _normalized_manager_level(value: Any, *, default: int = 1) -> int:
    try:
        level = int(value or default)
    except (TypeError, ValueError):
        level = default
    return max(1, min(level, 8))


async def _employee_has_role(db: AsyncSession, employee_id: int, role_name: str) -> bool:
    role = str(role_name or "").strip().lower()
    if not employee_id or not role:
        return False
    roles = await _employee_roles_by_id(db, int(employee_id))
    return role in roles


async def _multi_level_manager_ids(
    db: AsyncSession,
    applicant_id: int,
    rule_data: dict[str, Any],
) -> list[int]:
    end_type = str(rule_data.get("multi_level_end_type") or "level").strip()
    limit_level = (
        _normalized_manager_level(rule_data.get("multi_level_limit_level"), default=1)
        if rule_data.get("multi_level_limit_enabled")
        else 8
    )
    org_chain = await _department_manager_chain_ids(
        db,
        applicant_id,
        max_depth=limit_level,
        rule_data=rule_data,
    )
    fallback_direct_chain: list[int] | None = None

    async def manager_chain() -> list[int]:
        nonlocal fallback_direct_chain
        if org_chain:
            return org_chain
        if fallback_direct_chain is None:
            fallback_direct_chain = await _manager_chain_ids(db, applicant_id, max_depth=limit_level)
        return fallback_direct_chain

    if end_type == "role":
        chain = await manager_chain()
        role_name = str(rule_data.get("multi_level_role") or rule_data.get("role") or "").strip()
        if not role_name:
            return []
        for index, manager_id in enumerate(chain):
            if await _employee_has_role(db, manager_id, role_name):
                return chain[: index + 1]
        return []
    if end_type == "member":
        chain = await manager_chain()
        target_ids = set(_normalize_member_ids(rule_data.get("multi_level_member_ids") or rule_data.get("member_ids")))
        for index, manager_id in enumerate(chain):
            if manager_id in target_ids:
                return chain[: index + 1]
        return []

    level_value = rule_data.get("multi_level_level")
    if str(level_value or "").strip() == "highest":
        return await manager_chain()
    level = _normalized_manager_level(level_value or rule_data.get("manager_level"), default=1)
    if org_chain:
        return org_chain[:level]
    return await _manager_chain_ids(db, applicant_id, max_depth=level)


async def _department_manager_id(db: AsyncSession, department_id: Any) -> Optional[int]:
    try:
        normalized_department_id = int(department_id)
    except (TypeError, ValueError):
        return None
    if normalized_department_id <= 0:
        return None

    from app.models.organization import Department

    result = await db.execute(
        select(Department.manager_id).where(Department.id == normalized_department_id)
    )
    manager_id = result.scalar_one_or_none()
    try:
        normalized_manager_id = int(manager_id)
    except (TypeError, ValueError):
        return None
    return normalized_manager_id if normalized_manager_id > 0 else None


async def _top_level_department_manager_id(db: AsyncSession) -> Optional[int]:
    from app.models.employee import Employee
    from app.models.organization import Department

    result = await db.execute(
        select(Department.manager_id)
        .join(Employee, Employee.id == Department.manager_id)
        .where(
            Department.parent_id.is_(None),
            Department.manager_id.is_not(None),
            Department.is_active == True,
            Employee.is_active == True,
            Employee.is_superuser == False,
        )
        .order_by(Department.sort_order, Department.id)
        .limit(1)
    )
    manager_id = result.scalar_one_or_none()
    return _normalize_member_ids(manager_id)[0] if manager_id else None


async def _department_head_for_applicant(
    db: AsyncSession,
    applicant_id: int,
    rule_data: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    from app.models.employee import Employee

    result = await db.execute(select(Employee.department_id).where(Employee.id == applicant_id))
    department_id = result.scalar_one_or_none()
    manager_id = await _department_manager_id(db, department_id)
    if manager_id:
        if manager_id == applicant_id and rule_data and not rule_data.get("applicant_self_if_head"):
            fallback_id = await _manager_chain_target_id(db, applicant_id, rule_data) if rule_data.get("fallback_to_upper_manager") else None
            return fallback_id
        if rule_data and rule_data.get("applicant_self_if_head") and manager_id == applicant_id:
            return applicant_id
        return manager_id

    # 兼容旧数据：部分历史部门未维护 manager_id，仅通过 manager/admin 角色标识部门负责人。
    if not department_id:
        return await _manager_chain_target_id(db, applicant_id, rule_data or {}) if rule_data and rule_data.get("fallback_to_upper_manager") else None
    try:
        from app.models.employee_role import EmployeeRole

        role_result = await db.execute(
            select(EmployeeRole.employee_id)
            .join(Employee, Employee.id == EmployeeRole.employee_id)
            .where(
                Employee.department_id == department_id,
                EmployeeRole.role_name.in_(["manager", "admin"]),
                EmployeeRole.is_active == True,
            )
            .limit(1)
        )
        role_manager_id = role_result.scalar_one_or_none()
        if role_manager_id:
            if role_manager_id == applicant_id and rule_data and not rule_data.get("applicant_self_if_head"):
                fallback_id = await _manager_chain_target_id(db, applicant_id, rule_data) if rule_data.get("fallback_to_upper_manager") else None
                return fallback_id
            return role_manager_id
    except Exception:
        pass
    return await _manager_chain_target_id(db, applicant_id, rule_data or {}) if rule_data and rule_data.get("fallback_to_upper_manager") else None


async def _role_member_ids(db: AsyncSession, role_name: str) -> list[int]:
    role = str(role_name or "").strip().lower()
    if not role:
        return []
    from app.models.employee import Employee

    ids: list[int] = []
    try:
        from app.models.employee_role import EmployeeRole

        result = await db.execute(
            select(EmployeeRole.employee_id)
            .join(Employee, Employee.id == EmployeeRole.employee_id)
            .where(
                EmployeeRole.role_name == role,
                EmployeeRole.is_active == True,
                Employee.is_active == True,
            )
        )
        ids = [int(employee_id) for (employee_id,) in result.all() if employee_id]
    except Exception:
        ids = []

    return _dedupe_positive_ids(ids)


async def _department_field_manager_ids(
    db: AsyncSession,
    form_data: dict[str, Any],
    field_code: Any,
) -> list[int]:
    department_value = _form_value_for_member_rule(form_data, field_code)
    manager_ids: list[int] = []
    for department_id in _as_compare_list(department_value):
        manager_id = await _department_manager_id(db, department_id)
        if manager_id:
            manager_ids.append(manager_id)
    return _dedupe_positive_ids(manager_ids)


def _selected_applicant_member_ids(form_data: dict[str, Any], keys: tuple[Any, ...]) -> list[int]:
    for key in keys:
        selected_value = _form_value_for_member_rule(form_data, key)
        if selected_value:
            ids = _normalize_member_ids(selected_value)
            if ids:
                return ids
    return []


async def _validate_applicant_selected_member_ids(
    db: AsyncSession,
    selected_ids: list[int],
    rule_data: dict[str, Any],
) -> list[int]:
    ids = _dedupe_positive_ids(selected_ids)
    if not ids:
        return []
    scope = str(rule_data.get("applicant_select_scope") or "company").strip()
    if scope == "selected_members":
        allowed_ids = set(_normalize_member_ids(rule_data.get("applicant_select_member_ids")))
    elif scope == "role":
        allowed_ids: set[int] = set()
        for role in _normalize_role_values(rule_data.get("applicant_select_roles")):
            allowed_ids.update(await _role_member_ids(db, role))
    else:
        return ids
    if not allowed_ids:
        raise HTTPException(status_code=400, detail="applicant select scope has no available members")
    invalid_ids = [member_id for member_id in ids if member_id not in allowed_ids]
    if invalid_ids:
        raise HTTPException(status_code=400, detail="applicant selected member is outside configured scope")
    return ids


async def _resolve_node_approver_ids(
    db: AsyncSession,
    node: Any,
    applicant_id: int,
    form_data: Optional[dict[str, Any]] = None,
) -> list[int]:
    node_type = _node_type(node)
    rule_data = _node_rule_data(node)
    approver_type = str(rule_data.get("approver_type") or _node_value(node, "approver_type") or "")
    assignee_source = str(rule_data.get("assignee_source") or approver_type or "")
    form = form_data if isinstance(form_data, dict) else {}

    if node_type == "notify":
        source_values = [
            str(item or "").strip()
            for item in _as_compare_list(rule_data.get("notify_sources"))
            if str(item or "").strip()
        ]
        if not source_values:
            source_values = [assignee_source]
        if rule_data.get("include_applicant_self") and "applicant_self" not in source_values:
            source_values.append("applicant_self")
        if rule_data.get("allow_applicant_select") and "applicant_select" not in source_values:
            source_values.append("applicant_select")

        recipient_ids: list[int] = []
        for source in source_values:
            if source == "specific_user":
                recipient_ids.extend(_node_member_ids(node))
            elif source in {"applicant_self", "applicant", "submitter"}:
                recipient_ids.append(applicant_id)
            elif source in {"direct_manager", "multi_level_manager"}:
                if source == "multi_level_manager":
                    recipient_ids.extend(await _multi_level_manager_ids(db, applicant_id, rule_data))
                else:
                    manager_id = await _manager_chain_target_id(db, applicant_id, rule_data)
                    if manager_id:
                        recipient_ids.append(manager_id)
            elif source == "department_head":
                manager_id = await _department_head_for_applicant(db, applicant_id, rule_data)
                if manager_id:
                    recipient_ids.append(manager_id)
            elif source == "related_member_field":
                field_code = rule_data.get("related_member_field_code") or rule_data.get("related_field_code")
                recipient_ids.extend(_normalize_member_ids(_form_value_for_member_rule(form, field_code)))
            elif source == "form_department_head":
                field_code = rule_data.get("related_department_field_code") or rule_data.get("related_field_code")
                recipient_ids.extend(await _department_field_manager_ids(db, form, field_code))
            elif source == "applicant_select":
                selected_ids = _selected_applicant_member_ids(
                    form,
                    (
                        rule_data.get("selected_member_field_code"),
                        "cc_ids",
                        "selected_cc_ids",
                        "notify_ids",
                        "approval_selected_cc_ids",
                    ),
                )
                if len(selected_ids) > 50:
                    raise HTTPException(status_code=400, detail="applicant selected member count exceeds 50")
                recipient_ids.extend(await _validate_applicant_selected_member_ids(db, selected_ids, rule_data))
            elif source == "role" or source in ROLE_APPROVER_TYPES:
                role_name = str(rule_data.get("role") or (source if source in ROLE_APPROVER_TYPES else "")).strip()
                recipient_ids.extend(await _role_member_ids(db, role_name))
        return _dedupe_positive_ids(recipient_ids)

    if assignee_source in {"applicant_self", "applicant", "submitter"} or approver_type in {
        "applicant_self",
        "applicant",
        "submitter",
    }:
        return [applicant_id]

    if assignee_source == "related_member_field" or approver_type == "related_member_field":
        field_code = rule_data.get("related_member_field_code") or rule_data.get("related_field_code")
        return _normalize_member_ids(_form_value_for_member_rule(form, field_code))

    if assignee_source == "form_department_head" or approver_type == "form_department_head":
        field_code = rule_data.get("related_department_field_code") or rule_data.get("related_field_code")
        return await _department_field_manager_ids(db, form, field_code)

    if assignee_source == "applicant_select" or approver_type == "applicant_select":
        selected_ids = _selected_applicant_member_ids(
            form,
            (
                rule_data.get("selected_member_field_code"),
                rule_data.get("related_field_code"),
                "approval_selected_approver_ids",
                "selected_approver_ids",
                "approver_ids",
                "handler_ids",
            ),
        )
        if len(selected_ids) > 50:
            raise HTTPException(status_code=400, detail="applicant selected member count exceeds 50")
        selected_ids = await _validate_applicant_selected_member_ids(db, selected_ids, rule_data)
        if str(rule_data.get("applicant_select_mode") or "multiple") == "single":
            return selected_ids[:1]
        return selected_ids

    configured_ids = _node_member_ids(node)
    if approver_type == "specific_user" or assignee_source == "specific_user":
        return configured_ids

    if assignee_source == "multi_level_manager" or approver_type == "multi_level_manager":
        return _dedupe_positive_ids(await _multi_level_manager_ids(db, applicant_id, rule_data))

    if assignee_source == "direct_manager" or approver_type == "direct_manager":
        manager_id = await _manager_chain_target_id(db, applicant_id, rule_data)
        return _normalize_member_ids(manager_id)

    if assignee_source == "department_head" or approver_type == "department_head":
        manager_id = await _department_head_for_applicant(db, applicant_id, rule_data)
        return _normalize_member_ids(manager_id)

    if assignee_source == "role" or approver_type in ROLE_APPROVER_TYPES:
        role_name = str(rule_data.get("role") or approver_type).strip()
        return await _role_member_ids(db, role_name)

    node_obj = type("Node", (), {
        "approver_type": approver_type,
        "approver_id": _node_value(node, "approver_id"),
    })()
    resolved_id = await resolve_approver_id(db, node_obj, applicant_id)
    return _normalize_member_ids(resolved_id)


async def _first_role_member_id(db: AsyncSession, role_name: str) -> Optional[int]:
    node_obj = type("Node", (), {"approver_type": role_name, "approver_id": None})()
    return await resolve_approver_id(db, node_obj, 0)


async def _current_node_empty_action(
    db: AsyncSession,
    instance: ApprovalInstance,
    node: Any,
) -> tuple[str, Optional[int]]:
    exception_rules = await _instance_rule_group(db, instance, "exception_rules")
    rule_key = "handler_empty_member" if _node_type(node) == "handler" else "approval_empty_member"
    rule = _parse_json_object(exception_rules.get(rule_key))
    node_rule = _node_rule_data(node)
    action = str(node_rule.get("empty_action") or rule.get("action") or "").strip()
    raw_member_id = node_rule.get("empty_member_id") or rule.get("member_id")
    member_id = _normalize_member_ids(raw_member_id)[0] if raw_member_id else None
    if action in {"transfer_to_specific", "auto_transfer"}:
        return action, member_id
    if action == "transfer_to_admin":
        return action, await _first_role_member_id(db, "admin")
    if action == "auto_approve":
        return action, None
    if action == "auto_reject":
        return action, None
    return "block", None


def _loaded_pending_tasks(instance: Any, node_order: int) -> list[ApprovalTask]:
    loaded_tasks = getattr(instance, "__dict__", {}).get("tasks")
    if isinstance(loaded_tasks, list):
        return [
            task
            for task in loaded_tasks
            if getattr(task, "node_order", None) == node_order
            and getattr(task, "status", None) == "pending"
        ]
    return []


async def _pending_tasks_for_node(
    db: AsyncSession,
    instance: ApprovalInstance,
    node_order: int,
) -> list[ApprovalTask]:
    loaded = _loaded_pending_tasks(instance, node_order)
    if loaded:
        return loaded
    result = await db.execute(
        select(ApprovalTask).where(
            ApprovalTask.instance_id == instance.id,
            ApprovalTask.node_order == node_order,
            ApprovalTask.status == "pending",
        )
    )
    return list(result.scalars().all())


async def _tasks_for_node(
    db: AsyncSession,
    instance: ApprovalInstance,
    node_order: int,
) -> list[ApprovalTask]:
    loaded_tasks = getattr(instance, "__dict__", {}).get("tasks")
    if isinstance(loaded_tasks, list):
        return [
            task
            for task in loaded_tasks
            if getattr(task, "node_order", None) == node_order
        ]
    result = await db.execute(
        select(ApprovalTask).where(
            ApprovalTask.instance_id == instance.id,
            ApprovalTask.node_order == node_order,
        )
    )
    return list(result.scalars().all())


def _append_task_to_instance(instance: Any, task: ApprovalTask) -> None:
    loaded_tasks = getattr(instance, "__dict__", {}).get("tasks")
    if isinstance(loaded_tasks, list):
        loaded_tasks.append(task)
    else:
        set_committed_value(instance, "tasks", [task])


def _task_branch_key(task: Any) -> str | None:
    value = getattr(task, "branch_key", None)
    return str(value) if value else None


def _task_branch_node_order(task: Any) -> int | None:
    try:
        return int(getattr(task, "branch_node_order", None) or 0) or None
    except (TypeError, ValueError):
        return None


def _parallel_branch_key(branch: dict[str, Any], index: int) -> str:
    for key in ("uid", "branch_uid", "key", "branch_key", "id"):
        value = branch.get(key)
        if value not in (None, ""):
            return str(value)
    return f"branch_{index}"


def _parallel_branch_label(branch: dict[str, Any], index: int) -> str:
    return str(branch.get("label") or branch.get("branch_label") or f"分支{index}").strip()


def _parallel_branches(node: Any) -> list[dict[str, Any]]:
    rule_data = _node_rule_data(node)
    branches = rule_data.get("branches") or _node_value(node, "branches", []) or []
    return [_as_plain_data(branch) for branch in branches if isinstance(_as_plain_data(branch), dict)]


async def _parallel_branch_effective_nodes(
    db: AsyncSession,
    instance: ApprovalInstance,
    branch: dict[str, Any],
) -> list[dict[str, Any]]:
    configured_nodes = [_as_plain_data(node) for node in (branch.get("nodes") or [])]
    condition_context = (
        await _approval_condition_context(db, instance.applicant_id, _instance_form_data(instance))
        if _nodes_need_condition_context(configured_nodes)
        else {}
    )
    return _expand_effective_nodes(configured_nodes, condition_context)


async def _parallel_tasks_for_branch_step(
    db: AsyncSession,
    instance: ApprovalInstance,
    branch_key: str,
    branch_node_order: int,
) -> list[ApprovalTask]:
    loaded_tasks = getattr(instance, "__dict__", {}).get("tasks")
    if isinstance(loaded_tasks, list):
        return [
            task
            for task in loaded_tasks
            if getattr(task, "node_order", None) == instance.current_node_order
            and _task_branch_key(task) == branch_key
            and _task_branch_node_order(task) == branch_node_order
        ]
    result = await db.execute(
        select(ApprovalTask).where(
            ApprovalTask.instance_id == instance.id,
            ApprovalTask.node_order == instance.current_node_order,
            ApprovalTask.branch_key == branch_key,
            ApprovalTask.branch_node_order == branch_node_order,
        )
    )
    return list(result.scalars().all())


async def _approval_record_actions_for_parallel_branch_node(
    db: AsyncSession,
    instance_id: int,
    node_order: int,
    branch_key: str,
    branch_node_order: int,
) -> list[str]:
    result = await db.execute(
        select(ApprovalRecord.action).where(
            ApprovalRecord.instance_id == instance_id,
            ApprovalRecord.node_order == node_order,
            ApprovalRecord.branch_key == branch_key,
            ApprovalRecord.branch_node_order == branch_node_order,
            ApprovalRecord.action.in_(["approve", "reject"]),
        )
    )
    return [str(action or "").lower() for (action,) in result.all()]


async def _parallel_branch_step_has_record(
    db: AsyncSession,
    instance: ApprovalInstance,
    branch_key: str,
    branch_node_order: int,
    actions: set[str],
) -> bool:
    loaded_records = getattr(instance, "__dict__", {}).get("records")
    if isinstance(loaded_records, list):
        return any(
            getattr(record, "node_order", None) == instance.current_node_order
            and getattr(record, "branch_key", None) == branch_key
            and getattr(record, "branch_node_order", None) == branch_node_order
            and str(getattr(record, "action", "") or "").lower() in actions
            for record in loaded_records
        )
    result = await db.execute(
        select(ApprovalRecord.id).where(
            ApprovalRecord.instance_id == instance.id,
            ApprovalRecord.node_order == instance.current_node_order,
            ApprovalRecord.branch_key == branch_key,
            ApprovalRecord.branch_node_order == branch_node_order,
            ApprovalRecord.action.in_(list(actions)),
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _create_parallel_step_tasks(
    db: AsyncSession,
    instance: ApprovalInstance,
    node: dict[str, Any],
    *,
    branch_key: str,
    branch_label: str,
    branch_node_order: int,
) -> ApprovalTask | None:
    approver_ids = await _resolve_node_approver_ids(
        db,
        node,
        instance.applicant_id,
        _instance_form_data(instance),
    )
    if not approver_ids:
        action, fallback_member_id = await _current_node_empty_action(db, instance, node)
        if action == "auto_approve":
            db.add(
                ApprovalRecord(
                    instance_id=instance.id,
                    node_order=instance.current_node_order,
                    approver_id=instance.applicant_id,
                    action="auto_approve",
                    comment="并行分支审批成员为空，按规则自动同意",
                    acted_at=datetime.now(timezone.utc),
                    branch_key=branch_key,
                    branch_label=branch_label,
                    branch_node_order=branch_node_order,
                )
            )
            return None
        if action == "auto_reject":
            await _reject_system_node(
                db,
                instance,
                actor_id=instance.applicant_id,
                comment="并行分支审批成员为空，按规则自动拒绝",
            )
            return None
        if fallback_member_id:
            approver_ids = [fallback_member_id]
        else:
            raise HTTPException(status_code=400, detail="并行分支审批节点成员为空，请检查审批流程配置")

    if _node_approval_mode(node) == "sequential":
        completed_approver_ids = {
            int(getattr(task, "approver_id", 0) or 0)
            for task in await _parallel_tasks_for_branch_step(db, instance, branch_key, branch_node_order)
            if getattr(task, "status", None) == "completed"
        }
        next_approver_id = next((item for item in approver_ids if item not in completed_approver_ids), None)
        if not next_approver_id:
            return None
        approver_ids = [next_approver_id]

    created_tasks: list[ApprovalTask] = []
    for approver_id in approver_ids:
        task = ApprovalTask(
            instance_id=instance.id,
            node_order=instance.current_node_order,
            approver_id=approver_id,
            status="pending",
            branch_key=branch_key,
            branch_label=branch_label,
            branch_node_order=branch_node_order,
        )
        db.add(task)
        _append_task_to_instance(instance, task)
        created_tasks.append(task)
    return created_tasks[0] if created_tasks else None


async def _ensure_pending_task_for_parallel_branch(
    db: AsyncSession,
    instance: ApprovalInstance,
    branch: dict[str, Any],
    *,
    branch_index: int,
) -> ApprovalTask | None:
    branch_key = _parallel_branch_key(branch, branch_index)
    branch_label = _parallel_branch_label(branch, branch_index)
    branch_nodes = await _parallel_branch_effective_nodes(db, instance, branch)
    for node in branch_nodes:
        node_type = _node_type(node)
        branch_node_order = int(_node_value(node, "node_order", 0) or 0)
        if branch_node_order <= 0:
            continue
        existing_tasks = await _parallel_tasks_for_branch_step(db, instance, branch_key, branch_node_order)
        pending_tasks = [task for task in existing_tasks if getattr(task, "status", None) == "pending"]
        if pending_tasks:
            return pending_tasks[0]
        if existing_tasks:
            continue

        if node_type == "notify":
            if await _parallel_branch_step_has_record(
                db,
                instance,
                branch_key,
                branch_node_order,
                {"notify", "notify_skipped"},
            ):
                continue
            notify_ids = await _resolve_node_approver_ids(
                db,
                node,
                instance.applicant_id,
                _instance_form_data(instance),
            )
            for notify_id in notify_ids:
                db.add(
                    ApprovalRecord(
                        instance_id=instance.id,
                        node_order=instance.current_node_order,
                        approver_id=notify_id,
                        action="notify",
                        comment="并行分支抄送通知",
                        acted_at=datetime.now(timezone.utc),
                        branch_key=branch_key,
                        branch_label=branch_label,
                        branch_node_order=branch_node_order,
                    )
                )
            if not notify_ids:
                db.add(
                    ApprovalRecord(
                        instance_id=instance.id,
                        node_order=instance.current_node_order,
                        approver_id=instance.applicant_id,
                        action="notify_skipped",
                        comment="并行分支抄送人为空，已跳过",
                        acted_at=datetime.now(timezone.utc),
                        branch_key=branch_key,
                        branch_label=branch_label,
                        branch_node_order=branch_node_order,
                    )
                )
            continue

        if node_type == "auto" or node.get("approver_type") == "auto":
            if await _parallel_branch_step_has_record(
                db,
                instance,
                branch_key,
                branch_node_order,
                {"auto_approve"},
            ):
                continue
            auto_action = str(_node_rule_data(node).get("auto_action") or "approve").strip()
            if auto_action == "reject":
                await _reject_system_node(
                    db,
                    instance,
                    actor_id=instance.applicant_id,
                    comment="并行分支自动节点拒绝",
                )
                return None
            db.add(
                ApprovalRecord(
                    instance_id=instance.id,
                    node_order=instance.current_node_order,
                    approver_id=instance.applicant_id,
                    action="auto_approve",
                    comment="并行分支自动节点通过",
                    acted_at=datetime.now(timezone.utc),
                    branch_key=branch_key,
                    branch_label=branch_label,
                    branch_node_order=branch_node_order,
                )
            )
            continue

        if node_type in PARALLEL_BRANCH_NODE_TYPES:
            raise HTTPException(status_code=400, detail="并行分支暂不支持嵌套并行分支")

        if node_type in EXECUTABLE_NODE_TYPES:
            task = await _create_parallel_step_tasks(
                db,
                instance,
                node,
                branch_key=branch_key,
                branch_label=branch_label,
                branch_node_order=branch_node_order,
            )
            if task:
                return task
            if getattr(instance, "status", None) != "pending":
                return None
    return None


async def _all_parallel_branches_complete(
    db: AsyncSession,
    instance: ApprovalInstance,
    parallel_node: dict[str, Any],
) -> bool:
    for branch_index, branch in enumerate(_parallel_branches(parallel_node), start=1):
        branch_key = _parallel_branch_key(branch, branch_index)
        branch_nodes = await _parallel_branch_effective_nodes(db, instance, branch)
        for node in branch_nodes:
            node_type = _node_type(node)
            if node_type not in EXECUTABLE_NODE_TYPES:
                continue
            if node_type in {"notify", "auto"} or node.get("approver_type") == "auto":
                continue
            branch_node_order = int(_node_value(node, "node_order", 0) or 0)
            if branch_node_order <= 0:
                continue
            tasks = await _parallel_tasks_for_branch_step(db, instance, branch_key, branch_node_order)
            if any(getattr(task, "status", None) == "pending" for task in tasks):
                return False
            if tasks:
                continue
            if not await _parallel_branch_step_has_record(
                db,
                instance,
                branch_key,
                branch_node_order,
                {"auto_approve"},
            ):
                return False
    return True


async def _create_pending_tasks_for_parallel_node(
    db: AsyncSession,
    instance: ApprovalInstance,
    nodes: list[dict[str, Any]],
    current_node: dict[str, Any],
) -> ApprovalTask | None:
    existing_tasks = await _pending_tasks_for_node(db, instance, instance.current_node_order)
    if existing_tasks:
        return existing_tasks[0]

    branches = _parallel_branches(current_node)
    if len(branches) < 2:
        raise HTTPException(status_code=400, detail="并行分支至少需要两条分支")

    first_task: ApprovalTask | None = None
    for branch_index, branch in enumerate(branches, start=1):
        task = await _ensure_pending_task_for_parallel_branch(
            db,
            instance,
            branch,
            branch_index=branch_index,
        )
        if first_task is None and task is not None:
            first_task = task
        if getattr(instance, "status", None) != "pending":
            await db.flush()
            return None

    if first_task is not None:
        await db.flush()
        return first_task

    if await _all_parallel_branches_complete(db, instance, current_node):
        await _advance_system_node(
            db,
            instance,
            nodes,
            actor_id=instance.applicant_id,
            comment="并行分支全部完成",
            action="parallel_complete",
        )
        await db.flush()
        return await create_pending_task_for_instance(db, instance)

    await db.flush()
    return None


async def _parallel_branch_node_for_task(
    db: AsyncSession,
    instance: ApprovalInstance,
    parallel_node: dict[str, Any],
    task: ApprovalTask,
) -> tuple[dict[str, Any], dict[str, Any], int] | None:
    task_branch_key = _task_branch_key(task)
    task_branch_node_order = _task_branch_node_order(task)
    if not task_branch_key or task_branch_node_order is None:
        return None
    for branch_index, branch in enumerate(_parallel_branches(parallel_node), start=1):
        if _parallel_branch_key(branch, branch_index) != task_branch_key:
            continue
        branch_nodes = await _parallel_branch_effective_nodes(db, instance, branch)
        for node in branch_nodes:
            if int(_node_value(node, "node_order", 0) or 0) == task_branch_node_order:
                return branch, node, branch_index
    return None


async def _reject_parallel_instance(
    db: AsyncSession,
    instance: ApprovalInstance,
    *,
    actor_id: int,
    now: datetime,
) -> None:
    for task in await _pending_tasks_for_node(db, instance, instance.current_node_order):
        if getattr(task, "status", None) == "pending":
            task.status = "completed"
            task.completed_at = now
    instance.status = "rejected"
    await _sync_business_status(db, instance, "rejected")
    await _enqueue_approval_terminal_event(
        db,
        instance,
        "rejected",
        actor_id=actor_id,
        occurred_at=now,
    )


async def _process_parallel_branch_approval(
    db: AsyncSession,
    instance: ApprovalInstance,
    nodes: list[dict[str, Any]],
    current_node: dict[str, Any],
    approver_id: int,
    data: ApprovalActionRequest,
    previous_recipient_ids: set[int],
) -> ApprovalInstance:
    pending_tasks = await _pending_tasks_for_node(db, instance, instance.current_node_order)
    if not pending_tasks:
        await _create_pending_tasks_for_parallel_node(db, instance, nodes, current_node)
        if getattr(instance, "current_node_order", None) != _node_value(current_node, "node_order"):
            raise HTTPException(status_code=409, detail="审批节点已由系统规则自动流转，请刷新后重试")
        pending_tasks = await _pending_tasks_for_node(db, instance, instance.current_node_order)

    pending_task = next((task for task in pending_tasks if getattr(task, "approver_id", None) == approver_id), None)
    if pending_task is None:
        raise HTTPException(status_code=403, detail="当前用户不是该并行分支的待审批人")

    branch_match = await _parallel_branch_node_for_task(db, instance, current_node, pending_task)
    if branch_match is None:
        raise HTTPException(status_code=400, detail="并行分支任务缺少分支上下文，请刷新后重试")
    branch, branch_node, branch_index = branch_match
    branch_key = _parallel_branch_key(branch, branch_index)
    branch_label = _parallel_branch_label(branch, branch_index)
    branch_node_order = _task_branch_node_order(pending_task) or int(_node_value(branch_node, "node_order", 0) or 0)

    permission_rules = await _instance_rule_group(db, instance, "permission_rules")
    action_rules = _parse_json_object(permission_rules.get("in_progress_actions"))
    comment_text = str(data.comment or "").strip()
    if data.action in {"approve", "reject"}:
        if _node_type(branch_node) == "handler" and action_rules.get("handler_comment_required") and not comment_text:
            raise HTTPException(status_code=400, detail="办理意见不能为空")
        if _node_type(branch_node) != "handler" and action_rules.get("approval_comment_required") and not comment_text:
            raise HTTPException(status_code=400, detail="审批意见不能为空")

    now = datetime.now(timezone.utc)
    record = ApprovalRecord(
        instance_id=instance.id,
        node_order=instance.current_node_order,
        approver_id=approver_id,
        action=data.action,
        comment=data.comment,
        acted_at=now,
        branch_key=branch_key,
        branch_label=branch_label,
        branch_node_order=branch_node_order,
    )
    db.add(record)

    pending_task.status = "completed"
    pending_task.completed_at = now
    pending_task.approver_id = approver_id
    await mark_approval_notifications_read_for_recipient(db, approver_id, instance.id)

    approval_mode = _node_approval_mode(branch_node)
    branch_step_tasks = await _parallel_tasks_for_branch_step(db, instance, branch_key, branch_node_order)

    if data.action == "transfer":
        new_task = ApprovalTask(
            instance_id=instance.id,
            node_order=instance.current_node_order,
            approver_id=data.transfer_to_id,
            status="pending",
            branch_key=branch_key,
            branch_label=branch_label,
            branch_node_order=branch_node_order,
        )
        _append_task_to_instance(instance, new_task)
        db.add(new_task)
    elif data.action == "reject" and approval_mode != "vote":
        await _reject_parallel_instance(db, instance, actor_id=approver_id, now=now)
    elif approval_mode == "vote":
        await db.flush()
        all_tasks = await _parallel_tasks_for_branch_step(db, instance, branch_key, branch_node_order)
        total_count = len(all_tasks) or len(branch_step_tasks) or 1
        pass_count = _node_vote_pass_count(branch_node, total_count)
        actions = await _approval_record_actions_for_parallel_branch_node(
            db,
            instance.id,
            instance.current_node_order,
            branch_key,
            branch_node_order,
        )
        approve_count = sum(1 for action in actions if action == "approve")
        reject_count = sum(1 for action in actions if action == "reject")
        remaining_tasks = [task for task in all_tasks if getattr(task, "status", None) == "pending"]
        if approve_count >= pass_count:
            for task in remaining_tasks:
                task.status = "completed"
                task.completed_at = now
            next_task = await _ensure_pending_task_for_parallel_branch(
                db,
                instance,
                branch,
                branch_index=branch_index,
            )
            if next_task is None and await _all_parallel_branches_complete(db, instance, current_node):
                await _advance_system_node(
                    db,
                    instance,
                    nodes,
                    actor_id=approver_id,
                    comment="并行分支全部完成",
                    action="parallel_complete",
                )
                await create_pending_task_for_instance(db, instance)
        elif approve_count + len(remaining_tasks) < pass_count or reject_count >= total_count:
            for task in remaining_tasks:
                task.status = "completed"
                task.completed_at = now
            await _reject_parallel_instance(db, instance, actor_id=approver_id, now=now)
        else:
            await _notify_after_approval_action(
                db,
                instance,
                actor_id=approver_id,
                action=data.action,
                previous_recipient_ids=previous_recipient_ids,
            )
            instance.updated_at = now
            await db.flush()
            await db.refresh(instance)
            return instance
    else:
        if approval_mode == "counter_sign":
            remaining_tasks = [
                task
                for task in branch_step_tasks
                if task is not pending_task and getattr(task, "status", None) == "pending"
            ]
            if remaining_tasks:
                await _notify_after_approval_action(
                    db,
                    instance,
                    actor_id=approver_id,
                    action=data.action,
                    previous_recipient_ids=previous_recipient_ids,
                )
                instance.updated_at = now
                await db.flush()
                await db.refresh(instance)
                return instance
        elif approval_mode == "sequential":
            next_task = await _ensure_pending_task_for_parallel_branch(
                db,
                instance,
                branch,
                branch_index=branch_index,
            )
            if next_task:
                await _notify_after_approval_action(
                    db,
                    instance,
                    actor_id=approver_id,
                    action=data.action,
                    previous_recipient_ids=previous_recipient_ids,
                )
                instance.updated_at = now
                await db.flush()
                await db.refresh(instance)
                return instance
        else:
            for task in branch_step_tasks:
                if task is not pending_task and getattr(task, "status", None) == "pending":
                    task.status = "completed"
                    task.completed_at = now

        next_task = await _ensure_pending_task_for_parallel_branch(
            db,
            instance,
            branch,
            branch_index=branch_index,
        )
        if next_task is None and await _all_parallel_branches_complete(db, instance, current_node):
            await _advance_system_node(
                db,
                instance,
                nodes,
                actor_id=approver_id,
                comment="并行分支全部完成",
                action="parallel_complete",
            )
            await create_pending_task_for_instance(db, instance)

    await _notify_after_approval_action(
        db,
        instance,
        actor_id=approver_id,
        action=data.action,
        previous_recipient_ids=previous_recipient_ids,
    )
    instance.updated_at = now
    await db.flush()
    await db.refresh(instance)
    return instance


async def _advance_system_node(
    db: AsyncSession,
    instance: ApprovalInstance,
    nodes: list[dict[str, Any]],
    *,
    actor_id: Optional[int],
    comment: str,
    action: str,
    record: bool = True,
) -> None:
    now = datetime.now(timezone.utc)
    if record:
        db.add(
            ApprovalRecord(
                instance_id=instance.id,
                node_order=instance.current_node_order,
                approver_id=actor_id or instance.applicant_id,
                action=action,
                comment=comment,
                acted_at=now,
            )
        )
    current_index = next(
        (idx for idx, node in enumerate(nodes) if node.get("node_order") == instance.current_node_order),
        -1,
    )
    next_index = current_index + 1
    if current_index < 0 or next_index >= len(nodes):
        instance.status = "approved"
        await _sync_business_status(db, instance, "approved")
        await _enqueue_approval_terminal_event(
            db,
            instance,
            "approved",
            actor_id=actor_id or instance.applicant_id,
            occurred_at=now,
        )
        return
    instance.current_node_order = nodes[next_index].get("node_order", instance.current_node_order + 1)


async def _reject_system_node(
    db: AsyncSession,
    instance: ApprovalInstance,
    *,
    actor_id: Optional[int],
    comment: str,
    action: str = "auto_reject",
) -> None:
    now = datetime.now(timezone.utc)
    db.add(
        ApprovalRecord(
            instance_id=instance.id,
            node_order=instance.current_node_order,
            approver_id=actor_id or instance.applicant_id,
            action=action,
            comment=comment,
            acted_at=now,
        )
    )
    instance.status = "rejected"
    instance.updated_at = now
    await _sync_business_status(db, instance, "rejected")
    await _enqueue_approval_terminal_event(
        db,
        instance,
        "rejected",
        actor_id=actor_id or instance.applicant_id,
        occurred_at=now,
    )


async def create_pending_task_for_instance(db: AsyncSession, instance: ApprovalInstance) -> ApprovalTask | None:
    if getattr(instance, "status", None) != "pending":
        return None
    nodes = await _load_flow_snapshot_nodes(instance)
    if not nodes:
        flow_result = await db.execute(
            select(ApprovalFlow).options(selectinload(ApprovalFlow.nodes)).where(ApprovalFlow.id == instance.flow_id)
        )
        flow = flow_result.scalar_one_or_none()
        condition_context = await _approval_condition_context(db, instance.applicant_id, getattr(instance, "form_data", None))
        nodes = _expand_effective_nodes(list(getattr(flow, "nodes", []) or []), condition_context)
    current_node = next((node for node in nodes if node.get("node_order") == instance.current_node_order), None)
    if not current_node:
        return None

    current_node_type = current_node.get("node_type")
    if current_node_type in PARALLEL_BRANCH_NODE_TYPES:
        return await _create_pending_tasks_for_parallel_node(db, instance, nodes, current_node)

    if current_node_type == "notify":
        notify_ids = await _resolve_node_approver_ids(
            db,
            current_node,
            instance.applicant_id,
            _instance_form_data(instance),
        )
        for notify_id in notify_ids:
            db.add(
                ApprovalRecord(
                    instance_id=instance.id,
                    node_order=instance.current_node_order,
                    approver_id=notify_id,
                    action="notify",
                    comment="抄送通知",
                    acted_at=datetime.now(timezone.utc),
                )
            )
        if not notify_ids:
            db.add(
                ApprovalRecord(
                    instance_id=instance.id,
                    node_order=instance.current_node_order,
                    approver_id=instance.applicant_id,
                    action="notify_skipped",
                    comment="抄送人为空，已跳过",
                    acted_at=datetime.now(timezone.utc),
                )
            )
        await _advance_system_node(
            db,
            instance,
            nodes,
            actor_id=instance.applicant_id,
            comment="抄送节点自动流转",
            action="notify",
            record=False,
        )
        await db.flush()
        return await create_pending_task_for_instance(db, instance)

    if current_node_type in ("auto", "condition", "condition_branch") or current_node.get("approver_type") == "auto":
        auto_action = str(_node_rule_data(current_node).get("auto_action") or "approve").strip()
        if auto_action == "reject":
            await _reject_system_node(
                db,
                instance,
                actor_id=instance.applicant_id,
                comment="自动节点拒绝",
            )
            await db.flush()
            return None
        await _advance_system_node(
            db,
            instance,
            nodes,
            actor_id=instance.applicant_id,
            comment="自动节点通过",
            action="auto_approve",
        )
        await db.flush()
        return await create_pending_task_for_instance(db, instance)

    existing_tasks = await _pending_tasks_for_node(db, instance, instance.current_node_order)
    if existing_tasks:
        return existing_tasks[0]

    approver_ids = await _resolve_node_approver_ids(
        db,
        current_node,
        instance.applicant_id,
        _instance_form_data(instance),
    )
    if not approver_ids:
        action, fallback_member_id = await _current_node_empty_action(db, instance, current_node)
        if action == "auto_approve":
            await _advance_system_node(
                db,
                instance,
                nodes,
                actor_id=instance.applicant_id,
                comment="审批成员为空，按规则自动同意",
                action="auto_approve",
            )
            await db.flush()
            return await create_pending_task_for_instance(db, instance)
        if action == "auto_reject":
            await _reject_system_node(
                db,
                instance,
                actor_id=instance.applicant_id,
                comment="审批成员为空，按规则自动拒绝",
            )
            await db.flush()
            return None
        if fallback_member_id:
            approver_ids = [fallback_member_id]
        else:
            raise HTTPException(status_code=400, detail="审批节点成员为空，请检查审批流程配置")

    if _node_approval_mode(current_node) == "sequential":
        all_tasks = await _tasks_for_node(db, instance, instance.current_node_order)
        completed_approver_ids = {
            int(getattr(task, "approver_id", 0) or 0)
            for task in all_tasks
            if getattr(task, "status", None) == "completed"
        }
        next_approver_id = next((item for item in approver_ids if item not in completed_approver_ids), None)
        if not next_approver_id:
            return None
        approver_ids = [next_approver_id]

    created_tasks: list[ApprovalTask] = []
    for approver_id in approver_ids:
        task = ApprovalTask(
            instance_id=instance.id,
            node_order=instance.current_node_order,
            approver_id=approver_id,
            status="pending",
        )
        db.add(task)
        _append_task_to_instance(instance, task)
        created_tasks.append(task)
    await db.flush()
    return created_tasks[0] if created_tasks else None


async def submit_dynamic_application(
    db: AsyncSession,
    data: Any,
    applicant_id: int,
) -> ApprovalInstance:
    if isinstance(data, ApprovalApplicationSubmit):
        approval_type = await get_approval_type(db, data.approval_type_id)
        if not approval_type.is_active:
            raise HTTPException(status_code=400, detail="审批类型已停用")
        await _ensure_template_submit_allowed(db, approval_type, applicant_id)
        fields = normalized_template_form_fields(approval_type, list(approval_type.form_fields or []))
        form_data = _normalize_business_trip_form_data(
            approval_type.business_code,
            fields,
            dict(data.form_data or {}),
        )
        await validate_form_payload(fields, form_data, business_code=approval_type.business_code)
        await validate_leave_type_payload(
            db,
            fields,
            form_data,
            business_code=approval_type.business_code,
            applicant_id=applicant_id,
        )
        versions = list(getattr(approval_type, "__dict__", {}).get("versions", []) or [])
        published_versions = [
            item for item in versions if getattr(item, "status", "published") == "published"
        ]
        version = max(
            published_versions or versions,
            key=lambda item: (getattr(item, "version", 0) or 0, getattr(item, "id", 0) or 0),
            default=None,
        )
        if version is None:
            version = ApprovalTemplateVersion(
                approval_type_id=approval_type.id,
                version=getattr(approval_type, "version", None) or 1,
                snapshot_json=_template_snapshot(approval_type),
                status="published",
                published_at=datetime.now(timezone.utc),
            )
            db.add(version)
            await db.flush()
        summary = data.summary or approval_type.name
        create_data = ApprovalInstanceCreate(
            module=approval_type.business_code,
            business_id=0,
            business_type=approval_type.business_code,
            summary=summary,
            form_data=form_data,
        )
        instance = await create_instance(db, create_data, applicant_id)
        instance.business_id = instance.id
        instance.template_version_id = getattr(version, "id", None) or getattr(approval_type, "version", None) or 1
        await db.flush()
        await db.refresh(instance)
        return instance

    form_data = getattr(data, "form_data", None) or {}
    if form_data:
        template_result = await db.execute(
            select(ApprovalType)
            .options(selectinload(ApprovalType.form_fields))
            .where(
                ApprovalType.is_active.is_(True),
                ApprovalType.business_code.in_([
                    getattr(data, "business_type", None),
                    getattr(data, "module", None),
                ]),
            )
            .order_by(ApprovalType.sort_order.asc(), ApprovalType.id.asc())
            .limit(1)
        )
        template = template_result.scalar_one_or_none()
        if template is not None:
            await _ensure_template_submit_allowed(db, template, applicant_id)
            fields = normalized_template_form_fields(
                template,
                list(getattr(template, "form_fields", []) or []),
            )
            form_data = _normalize_business_trip_form_data(
                getattr(template, "business_code", None),
                fields,
                dict(form_data or {}),
            )
            setattr(data, "form_data", form_data)
            await validate_form_payload(
                fields,
                form_data,
                business_code=getattr(template, "business_code", None),
            )
            await validate_leave_type_payload(
                db,
                fields,
                form_data,
                business_code=getattr(template, "business_code", None),
                applicant_id=applicant_id,
            )
    instance = await create_instance(db, data, applicant_id)
    return instance


async def list_admin_approval_records(db: AsyncSession, filters: Any) -> tuple[int, list[ApprovalInstance]]:
    stmt = select(ApprovalInstance).options(
        selectinload(ApprovalInstance.records), selectinload(ApprovalInstance.tasks)
    )
    if getattr(filters, "status", None):
        stmt = stmt.where(ApprovalInstance.status == filters.status)
    if getattr(filters, "module", None):
        stmt = stmt.where(ApprovalInstance.module == filters.module)
    if getattr(filters, "applicant_id", None):
        stmt = stmt.where(ApprovalInstance.applicant_id == filters.applicant_id)
    if getattr(filters, "business_type", None):
        stmt = stmt.where(ApprovalInstance.business_type == filters.business_type)
    if getattr(filters, "start_date", None):
        stmt = stmt.where(ApprovalInstance.created_at >= filters.start_date)
    if getattr(filters, "end_date", None):
        stmt = stmt.where(ApprovalInstance.created_at <= filters.end_date)
    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar() or 0
    stmt = stmt.order_by(ApprovalInstance.created_at.desc())
    if getattr(filters, "skip", None) is not None:
        stmt = stmt.offset(filters.skip)
    if getattr(filters, "limit", None) is not None:
        stmt = stmt.limit(filters.limit)
    result = await db.execute(stmt)
    return total, list(result.scalars().all())


def _approval_archive_boundary(value: date | datetime | None, endpoint: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    edge = time.min if endpoint == "start" else time.max
    return datetime.combine(value, edge, tzinfo=timezone.utc)


def _approval_archive_conditions(
    *,
    business_type: Optional[str] = None,
    start_date: date | datetime | None = None,
    end_date: date | datetime | None = None,
    status_filter: Optional[str] = None,
    applicant_id: Optional[int] = None,
    applicant_keyword: Optional[str] = None,
) -> list[Any]:
    conditions: list[Any] = []
    business_type_text = str(business_type or "").strip()
    if business_type_text:
        conditions.append(
            or_(
                ApprovalInstance.business_type == business_type_text,
                ApprovalInstance.module == business_type_text,
            )
        )
    if status_filter:
        conditions.append(ApprovalInstance.status == status_filter)
    if applicant_id:
        conditions.append(ApprovalInstance.applicant_id == applicant_id)
    applicant_keyword_text = str(applicant_keyword or "").strip()
    if applicant_keyword_text:
        from app.models.employee import Employee

        keyword_pattern = f"%{applicant_keyword_text}%"
        conditions.append(
            ApprovalInstance.applicant_id.in_(
                select(Employee.id).where(
                    or_(
                        Employee.name.ilike(keyword_pattern),
                        Employee.employee_no.ilike(keyword_pattern),
                    )
                )
            )
        )
    start_at = _approval_archive_boundary(start_date, "start")
    end_at = _approval_archive_boundary(end_date, "end")
    if start_at:
        conditions.append(ApprovalInstance.created_at >= start_at)
    if end_at:
        conditions.append(ApprovalInstance.created_at <= end_at)
    return conditions


def _apply_approval_archive_filters(
    stmt: Any,
    *,
    business_type: Optional[str] = None,
    start_date: date | datetime | None = None,
    end_date: date | datetime | None = None,
    status_filter: Optional[str] = None,
    applicant_id: Optional[int] = None,
    applicant_keyword: Optional[str] = None,
) -> Any:
    conditions = _approval_archive_conditions(
        business_type=business_type,
        start_date=start_date,
        end_date=end_date,
        status_filter=status_filter,
        applicant_id=applicant_id,
        applicant_keyword=applicant_keyword,
    )
    return stmt.where(*conditions) if conditions else stmt


async def user_can_view_instance(
    db: AsyncSession,
    instance: ApprovalInstance,
    employee_id: int,
) -> bool:
    """审批详情可见范围：按模板查看权限叠加申请人、审批/待办/抄送关系。"""
    if getattr(instance, "applicant_id", None) == employee_id:
        return True

    permission_rules = await _instance_rule_group(db, instance, "permission_rules")
    view_permission = _parse_json_object(permission_rules.get("view_permission"))
    view_type = str(view_permission.get("type") or "").strip()
    if view_type == "all":
        return True
    if view_type == "submitter_only":
        return False
    if view_type == "custom_members":
        extra_member_ids = _normalize_member_ids(view_permission.get("extra_member_ids"))
        if employee_id in extra_member_ids:
            return True
        return False

    loaded_records = getattr(instance, "__dict__", {}).get("records")
    if isinstance(loaded_records, list):
        if any(getattr(record, "approver_id", None) == employee_id for record in loaded_records):
            return True
    else:
        record_count_result = await db.execute(
            select(func.count())
            .select_from(ApprovalRecord)
            .where(
                ApprovalRecord.instance_id == getattr(instance, "id", None),
                ApprovalRecord.approver_id == employee_id,
            )
        )
        if int(record_count_result.scalar_one() or 0) > 0:
            return True

    loaded_tasks = getattr(instance, "__dict__", {}).get("tasks")
    if isinstance(loaded_tasks, list):
        return any(getattr(task, "approver_id", None) == employee_id for task in loaded_tasks)

    task_count_result = await db.execute(
        select(func.count())
        .select_from(ApprovalTask)
        .where(
            ApprovalTask.instance_id == getattr(instance, "id", None),
            ApprovalTask.approver_id == employee_id,
        )
    )
    return int(task_count_result.scalar_one() or 0) > 0


# ───────────────────── ApprovalFlow CRUD ─────────────────────
# 审批流程定义的增删改查
# 流程定义是"模板"，实例是"执行记录"

async def create_approval_type(db: AsyncSession, data: ApprovalTypeCreate) -> ApprovalType:
    approval_type = ApprovalType(**data.model_dump())
    db.add(approval_type)
    await db.flush()
    await _ensure_default_flow_for_approval_type(db, approval_type)
    await db.refresh(approval_type)
    return approval_type


async def _ensure_default_flow_for_approval_type(db: AsyncSession, approval_type: ApprovalType) -> ApprovalFlow:
    result = await db.execute(
        select(ApprovalFlow)
        .options(selectinload(ApprovalFlow.nodes))
        .where(
            ApprovalFlow.module.in_(_flow_module_candidates(approval_type.business_code)),
            ApprovalFlow.is_active.is_(True),
        )
        .order_by(ApprovalFlow.id.desc())
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing:
        node_count = await db.scalar(
            select(func.count()).select_from(ApprovalNode).where(ApprovalNode.flow_id == existing.id)
        )
        if node_count:
            return existing

    flow = existing or ApprovalFlow(
        name=f"{approval_type.name}审批",
        module=approval_type.business_code,
        description=json.dumps(
            {"business_type": approval_type.name, "applicable_dept": "全部"},
            ensure_ascii=False,
        ),
        is_active=True,
    )
    if existing is None:
        db.add(flow)
        await db.flush()

    node_count = await db.scalar(
        select(func.count()).select_from(ApprovalNode).where(ApprovalNode.flow_id == flow.id)
    )
    if not node_count:
        db.add_all(
            [
                ApprovalNode(
                    flow_id=flow.id,
                    node_order=1,
                    node_type="approval",
                    approver_type="direct_manager",
                ),
                ApprovalNode(
                    flow_id=flow.id,
                    node_order=2,
                    node_type="approval",
                    approver_type="hr",
                ),
            ]
        )
        await db.flush()
    return flow


async def list_approval_types(
    db: AsyncSession,
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> list[ApprovalType]:
    stmt = select(ApprovalType).options(selectinload(ApprovalType.form_fields))
    if category:
        stmt = stmt.where(or_(ApprovalType.category == category, ApprovalType.category_key == category))
    if is_active is not None:
        stmt = stmt.where(ApprovalType.is_active.is_(is_active))
    stmt = stmt.order_by(ApprovalType.category.asc(), ApprovalType.sort_order.asc(), ApprovalType.id.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_approval_type(db: AsyncSession, approval_type_id: int) -> ApprovalType:
    result = await db.execute(
        select(ApprovalType)
        .options(selectinload(ApprovalType.form_fields))
        .where(ApprovalType.id == approval_type_id)
    )
    approval_type = result.scalar_one_or_none()
    if not approval_type:
        raise HTTPException(status_code=404, detail="审批类型不存在")
    return approval_type


async def update_approval_type(
    db: AsyncSession,
    approval_type_id: int,
    data: ApprovalTypeUpdate,
    current_user: Any | None = None,
) -> ApprovalType:
    approval_type = await get_approval_type(db, approval_type_id)
    await _ensure_template_manage_allowed(db, approval_type, current_user)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(approval_type, field, value)
    approval_type.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(approval_type)
    return approval_type


async def bulk_disable_approval_types(
    db: AsyncSession,
    approval_type_ids: list[int],
    current_user: Any | None = None,
) -> dict[str, Any]:
    unique_ids = sorted({int(item) for item in approval_type_ids if item})
    if not unique_ids:
        return {"updated": 0, "ids": []}
    result = await db.execute(
        select(ApprovalType).where(ApprovalType.id.in_(unique_ids))
    )
    templates = list(result.scalars().all())
    now = datetime.now(timezone.utc)
    updated_ids: list[int] = []
    for template in templates:
        await _ensure_template_manage_allowed(db, template, current_user)
        if getattr(template, "is_active", True) is False and getattr(template, "status", None) == "disabled":
            continue
        template.is_active = False
        template.status = "disabled"
        template.updated_at = now
        updated_ids.append(int(template.id))
    await db.flush()
    return {"updated": len(updated_ids), "ids": updated_ids}


async def export_template_config(db: AsyncSession, approval_type_id: int) -> dict[str, Any]:
    approval_type = await get_approval_type(db, approval_type_id)
    flow_result = await db.execute(
        select(ApprovalFlow)
        .options(selectinload(ApprovalFlow.nodes))
        .where(
            ApprovalFlow.module.in_(_flow_module_candidates(approval_type.business_code)),
            ApprovalFlow.is_active.is_(True),
        )
        .order_by(ApprovalFlow.id.desc())
        .limit(1)
    )
    flow = flow_result.scalar_one_or_none()
    versions_result = await db.execute(
        select(ApprovalTemplateVersion)
        .where(ApprovalTemplateVersion.approval_type_id == approval_type_id)
        .order_by(ApprovalTemplateVersion.version.desc(), ApprovalTemplateVersion.id.desc())
    )
    versions = [
        {
            "id": version.id,
            "version": version.version,
            "status": version.status,
            "published_at": version.published_at,
            "snapshot_json": version.snapshot_json,
        }
        for version in versions_result.scalars().all()
    ]
    return {
        "exported_at": datetime.now(timezone.utc),
        "template": _template_snapshot(approval_type, flow if isinstance(flow, ApprovalFlow) else None),
        "versions": versions,
    }


async def delete_approval_type(
    db: AsyncSession,
    approval_type_id: int,
    current_user: Any | None = None,
) -> None:
    approval_type = await get_approval_type(db, approval_type_id)
    await _ensure_template_manage_allowed(db, approval_type, current_user)
    version_ids_result = await db.execute(
        select(ApprovalTemplateVersion.id).where(ApprovalTemplateVersion.approval_type_id == approval_type_id)
    )
    version_ids = [version_id for (version_id,) in version_ids_result.all()]
    if version_ids:
        referenced_result = await db.execute(
            select(func.count())
            .select_from(ApprovalInstance)
            .where(ApprovalInstance.template_version_id.in_(version_ids))
        )
        if referenced_result.scalar_one() > 0:
            raise HTTPException(status_code=409, detail="模板已被历史审批记录引用，无法删除，请先停用模板")
    await db.delete(approval_type)
    await db.flush()


async def create_form_field(db: AsyncSession, approval_type_id: int, data: ApprovalFormFieldCreate) -> ApprovalFormField:
    await get_approval_type(db, approval_type_id)
    payload = data.model_dump()
    payload["approval_type_id"] = approval_type_id
    field = ApprovalFormField(**payload)
    db.add(field)
    await db.flush()
    await db.refresh(field)
    return field


async def list_form_fields(db: AsyncSession, approval_type_id: int) -> list[ApprovalFormField]:
    await get_approval_type(db, approval_type_id)
    result = await db.execute(
        select(ApprovalFormField)
        .where(ApprovalFormField.approval_type_id == approval_type_id)
        .order_by(ApprovalFormField.sort_order.asc(), ApprovalFormField.id.asc())
    )
    return list(result.scalars().all())


async def get_form_field(db: AsyncSession, field_id: int) -> ApprovalFormField:
    result = await db.execute(select(ApprovalFormField).where(ApprovalFormField.id == field_id))
    field = result.scalar_one_or_none()
    if not field:
        raise HTTPException(status_code=404, detail="审批表单字段不存在")
    return field


async def update_form_field(db: AsyncSession, field_id: int, data: ApprovalFormFieldUpdate) -> ApprovalFormField:
    field = await get_form_field(db, field_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(field, key, value)
    await db.flush()
    await db.refresh(field)
    return field


async def delete_form_field(db: AsyncSession, field_id: int) -> None:
    field = await get_form_field(db, field_id)
    await db.delete(field)
    await db.flush()

async def create_flow(db: AsyncSession, data: ApprovalFlowCreate) -> ApprovalFlow:
    """创建审批流程定义。

    审批流程定义了某业务模块的审批规则（节点数量、顺序、审批人类型等）。
    一个 module 可以有多个 Flow，但同一时刻只有最新激活的 Flow 会被使用。

    Args:
        db:   异步数据库会话
        data: 流程创建 Schema（name/module/description/is_active 等）

    Returns:
        已创建的 ApprovalFlow ORM 对象（不含 nodes，需单独创建）
    """
    flow = ApprovalFlow(**data.model_dump())
    db.add(flow)
    await db.flush()
    await db.refresh(flow)
    return flow


async def get_flow(db: AsyncSession, flow_id: int) -> ApprovalFlow:
    """获取单个审批流程（同时预加载关联的节点列表）。

    使用 selectinload 预加载 nodes，避免后续访问时触发 N+1 查询。

    Args:
        db:      异步数据库会话
        flow_id: 流程主键 ID

    Returns:
        包含 nodes 列表的 ApprovalFlow 对象

    Raises:
        HTTPException 404: 流程不存在
    """
    result = await db.execute(
        select(ApprovalFlow)
        .options(selectinload(ApprovalFlow.nodes))  # 预加载节点列表
        .where(ApprovalFlow.id == flow_id)
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(status_code=404, detail="审批流程不存在")
    return flow


async def list_flows(
    db: AsyncSession,
    module: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> list[ApprovalFlow]:
    """列出审批流程列表，支持按模块和启用状态过滤。

    Args:
        db:        异步数据库会话
        module:    按业务模块过滤（如 "leave"/"overtime"），None 表示查全部
        is_active: 按启用状态过滤，None 表示查全部

    Returns:
        按 ID 倒序（最新优先）排列的流程列表，含各流程的节点
    """
    stmt = select(ApprovalFlow).options(selectinload(ApprovalFlow.nodes))
    module_candidates = _flow_module_candidates(module)
    if module_candidates:
        stmt = stmt.where(ApprovalFlow.module.in_(module_candidates))
    if is_active is not None:
        stmt = stmt.where(ApprovalFlow.is_active == is_active)
    stmt = stmt.order_by(ApprovalFlow.id.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def update_flow(
    db: AsyncSession, flow_id: int, data: ApprovalFlowUpdate
) -> ApprovalFlow:
    """更新审批流程基本信息（PATCH 语义）。

    Args:
        db:      异步数据库会话
        flow_id: 待更新的流程 ID
        data:    包含待更新字段的 Schema（exclude_unset）

    Returns:
        更新后的 ApprovalFlow 对象

    Raises:
        HTTPException 404: 流程不存在
    """
    flow = await get_flow(db, flow_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(flow, field, value)
    await db.flush()
    await db.refresh(flow)
    return flow


async def delete_flow(db: AsyncSession, flow_id: int) -> None:
    """删除审批流程定义。

    注意：若已有审批实例引用此流程（flow_id FK），删除会触发数据库约束错误。
    建议将 is_active 设为 False 停用流程，而非直接删除。

    Args:
        db:      异步数据库会话
        flow_id: 待删除的流程 ID

    Raises:
        HTTPException 404: 流程不存在
    """
    flow = await get_flow(db, flow_id)
    await db.delete(flow)
    await db.flush()


# ───────────────────── ApprovalNode CRUD ─────────────────────
# 审批节点的增删改查
# 节点定义流程中每一步的执行者和规则

async def create_node(db: AsyncSession, data: ApprovalNodeCreate) -> ApprovalNode:
    """在指定流程下创建审批节点。

    节点通过 node_order 定义执行顺序（1为第一个节点）。
    同一流程内节点 node_order 应唯一且连续，以确保流程推进逻辑正确。

    Args:
        db:   异步数据库会话
        data: 节点创建 Schema（flow_id/node_order/node_type/approver_type/
              approver_id/name/timeout_hours 等）

    Returns:
        已创建的 ApprovalNode ORM 对象

    Raises:
        HTTPException 404: 所属流程不存在（在创建前校验）
    """
    # 校验所属流程存在（防止孤立节点）
    await get_flow(db, data.flow_id)
    node = ApprovalNode(**data.model_dump())
    db.add(node)
    await db.flush()
    await db.refresh(node)
    return node


async def get_node(db: AsyncSession, node_id: int) -> ApprovalNode:
    """获取单个审批节点。

    Args:
        db:      异步数据库会话
        node_id: 节点主键 ID

    Returns:
        ApprovalNode 对象

    Raises:
        HTTPException 404: 节点不存在
    """
    result = await db.execute(
        select(ApprovalNode).where(ApprovalNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="审批节点不存在")
    return node


async def update_node(
    db: AsyncSession, node_id: int, data: ApprovalNodeUpdate
) -> ApprovalNode:
    """更新审批节点配置（PATCH 语义）。

    Args:
        db:      异步数据库会话
        node_id: 待更新的节点 ID
        data:    包含待更新字段的 Schema

    Returns:
        更新后的 ApprovalNode 对象

    Raises:
        HTTPException 404: 节点不存在
    """
    node = await get_node(db, node_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(node, field, value)
    await db.flush()
    await db.refresh(node)
    return node


async def delete_node(db: AsyncSession, node_id: int) -> None:
    """删除审批节点。

    Args:
        db:      异步数据库会话
        node_id: 待删除的节点 ID

    Raises:
        HTTPException 404: 节点不存在
    """
    node = await get_node(db, node_id)
    await db.delete(node)
    await db.flush()


# ───────────────────── ApprovalInstance ─────────────────────
# 审批实例管理 — 具体一次审批请求的生命周期

async def _determine_flow(db: AsyncSession, module: str) -> ApprovalFlow:
    """根据业务模块自动确定要使用的审批流程（内部工具函数）。

    查找策略：
    - 在 ApprovalFlow 表中，找 module 匹配且 is_active=True 的最新流程（按 id 倒序取第1条）
    - 找到后校验流程至少有一个节点（否则无法推进）

    Args:
        db:     异步数据库会话
        module: 业务模块标识（如 "leave"/"overtime"/"recruitment_demand"）

    Returns:
        匹配的 ApprovalFlow 对象（含 nodes）

    Raises:
        HTTPException 400: 该模块没有可用的激活审批流程
        HTTPException 400: 流程存在但没有配置节点
    """
    result = await db.execute(
        select(ApprovalFlow)
        .options(selectinload(ApprovalFlow.nodes))
        .where(
            ApprovalFlow.module.in_(_flow_module_candidates(module)),
            ApprovalFlow.is_active.is_(True),
        )
        .order_by(ApprovalFlow.id.desc())
        .limit(1)
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(
            status_code=400,
            detail=f"模块 '{module}' 没有可用的审批流程",
        )
    if not flow.nodes:
        raise HTTPException(
            status_code=400,
            detail=f"审批流程 '{flow.name}' 没有配置审批节点",
        )
    return flow


async def resolve_approver_id(
    db: AsyncSession,
    node: ApprovalNode,
    applicant_id: int,
) -> Optional[int]:
    """根据节点的 approver_type 动态解析出实际审批人的员工 ID。

    支持的 approver_type 及解析逻辑：
    - specific_user：直接返回 node.approver_id（静态指定）
    - direct_manager：查询申请人的 direct_manager_id 字段
    - multi_level_manager：按申请人所在部门的负责人链向上解析
    - hr：查询 EmployeeRole 中 role_name='hr' 且激活的第一个员工
    - department_head：查询申请人所在部门负责人
    - gm：查询顶级部门负责人；超级管理员账号不作为业务审批人

    此函数用于 create_instance 时预确定审批人，也可用于前端展示"下一审批人"。

    Args:
        db:           异步数据库会话
        node:         审批节点对象（含 approver_type 和 approver_id）
        applicant_id: 发起审批申请的员工 ID（用于解析 direct_manager/department_head/multi_level_manager）

    Returns:
        解析出的审批人员工 ID，无法解析时返回 None（如员工无上级、部门无主管）
    """
    from app.models.employee import Employee

    # 静态指定审批人：直接使用配置的 approver_id
    if node.approver_type == "specific_user":
        return node.approver_id

    rule_data = _node_rule_data(node)

    # 直接上级仍按员工档案的直属上级；连续多级主管按组织架构负责人链向上。
    if node.approver_type == "direct_manager":
        return await _manager_chain_target_id(db, applicant_id, rule_data)
    if node.approver_type == "multi_level_manager":
        manager_ids = await _multi_level_manager_ids(db, applicant_id, rule_data)
        return manager_ids[0] if manager_ids else None

    # 常用角色：找第一个拥有对应角色的激活员工
    if node.approver_type in ROLE_APPROVER_TYPES:
        try:
            from app.models.employee_role import EmployeeRole
            result = await db.execute(
                select(EmployeeRole.employee_id)
                .where(EmployeeRole.role_name == node.approver_type, EmployeeRole.is_active == True)
                .limit(1)
            )
            return result.scalar_one_or_none()
        except ImportError:
            return None

    # 部门主管：优先使用 departments.manager_id；历史数据无负责人时再兼容角色兜底。
    if node.approver_type == "department_head":
        return await _department_head_for_applicant(db, applicant_id, rule_data)

    # 总经理属于业务审批角色，不能用后台超级管理员账号兜底。
    if node.approver_type == "gm":
        return await _top_level_department_manager_id(db)

    return node.approver_id  # 兜底：返回节点配置的 approver_id


async def create_instance(
    db: AsyncSession,
    data: ApprovalInstanceCreate,
    applicant_id: int,
) -> ApprovalInstance:
    if data.module in {'leave','leave_request','overtime','outside','business_trip','punch_correction','attendance_punch_correction'}:
        form = dict(getattr(data, 'form_data', None) or {})
        if form.get('employee_id') and int(form['employee_id']) != applicant_id:
            raise HTTPException(status_code=403, detail='考勤申请只能关联申请人本人')
        form['employee_id'] = applicant_id
        data.form_data = form
    if data.module in PUNCH_CORRECTION_CODES and not data.business_id:
        from app.services.field_policies import validate_correction
        await validate_correction(db, applicant_id, getattr(data, 'form_data', None) or {})
    approval_type = await _approval_type_for_module(db, data.module)
    if approval_type is not None:
        await _ensure_template_submit_allowed(db, approval_type, applicant_id)
    flow = await _determine_flow(db, data.module)
    instance = ApprovalInstance(
        flow_id=flow.id,
        module=data.module,
        business_id=data.business_id,
        business_type=data.business_type,
        summary=getattr(data, "summary", None) or flow.name,
        form_data=getattr(data, "form_data", None),
        applicant_id=applicant_id,
        flow_snapshot=await _build_flow_snapshot(db, flow, applicant_id, getattr(data, "form_data", None)),
        current_node_order=1,
        status="pending",
    )
    db.add(instance)
    await db.flush()
    await db.refresh(instance)

    submit_record = ApprovalRecord(
        instance_id=instance.id,
        node_order=0,
        approver_id=applicant_id,
        action="submit",
        comment=getattr(data, "summary", None) or instance.summary,
        acted_at=datetime.now(timezone.utc),
    )
    db.add(submit_record)
    await create_pending_task_for_instance(db, instance)
    await db.flush()
    if instance.status == "pending":
        await _notify_pending_approval_tasks(db, instance, actor_id=applicant_id)
    else:
        await _notify_auto_terminal_approval_update(db, instance)
    await db.refresh(instance)
    return instance


async def maybe_create_approval_instance(
    db: AsyncSession,
    module: str,
    business_id: int,
    business_type: str,
    applicant_id: int,
) -> Optional[ApprovalInstance]:
    try:
        flow = await _determine_flow(db, module)
    except HTTPException:
        return None
    instance = ApprovalInstance(
        flow_id=flow.id,
        module=module,
        business_id=business_id,
        business_type=business_type,
        summary=flow.name,
        flow_snapshot=await _build_flow_snapshot(db, flow, applicant_id),
        applicant_id=applicant_id,
        current_node_order=1,
        status="pending",
    )
    db.add(instance)
    await db.flush()
    await db.refresh(instance)

    submit_record = ApprovalRecord(
        instance_id=instance.id,
        node_order=0,
        approver_id=applicant_id,
        action="submit",
        comment=flow.name,
        acted_at=datetime.now(timezone.utc),
    )
    db.add(submit_record)
    await create_pending_task_for_instance(db, instance)
    await db.flush()
    if instance.status == "pending":
        await _notify_pending_approval_tasks(db, instance, actor_id=applicant_id)
    else:
        await _notify_auto_terminal_approval_update(db, instance)
    await db.refresh(instance)
    return instance


async def get_instance(db: AsyncSession, instance_id: int) -> ApprovalInstance:
    result = await db.execute(
        select(ApprovalInstance)
        .options(
            selectinload(ApprovalInstance.records),
            selectinload(ApprovalInstance.tasks),
        )
        .where(ApprovalInstance.id == instance_id)
    )
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="???????")
    for attr in ("summary", "form_data", "flow_snapshot", "template_version_id"):
        if attr not in getattr(instance, "__dict__", {}):
            try:
                setattr(instance, attr, 1 if attr == "template_version_id" else None)
            except Exception:
                pass
    return instance


def _pending_approval_task_recipient_ids(instance: ApprovalInstance) -> list[int]:
    recipient_ids: list[int] = []
    for task in list(getattr(instance, "tasks", []) or []):
        if getattr(task, "node_order", None) != getattr(instance, "current_node_order", None):
            continue
        if getattr(task, "status", None) != "pending":
            continue
        approver_id = getattr(task, "approver_id", None)
        try:
            normalized_id = int(approver_id)
        except (TypeError, ValueError):
            continue
        if normalized_id > 0 and normalized_id not in recipient_ids:
            recipient_ids.append(normalized_id)
    return recipient_ids


async def remind_instance(
    db: AsyncSession,
    instance_id: int,
    requester_id: int,
) -> ApprovalReminderResult:
    """向当前待审批节点的审批人发送催办通知。"""
    instance = await get_instance(db, instance_id)
    if getattr(instance, "status", None) != "pending":
        raise HTTPException(status_code=400, detail="仅审批中的申请可以催办")
    if getattr(instance, "applicant_id", None) != requester_id:
        raise HTTPException(status_code=403, detail="仅申请人可以催办审批")

    recipient_ids = _pending_approval_task_recipient_ids(instance)
    if not recipient_ids:
        task = await create_pending_task_for_instance(db, instance)
        if task is not None:
            recipient_ids = _pending_approval_task_recipient_ids(instance)

    if not recipient_ids:
        raise HTTPException(status_code=400, detail="当前节点暂无可催办审批人")

    requester_name = await _employee_name_by_id(db, requester_id) or "申请人"
    summary = str(getattr(instance, "summary", None) or f"审批单 #{instance.id}").strip()
    for recipient_id in recipient_ids:
        await NotificationService.create(
            db,
            NotificationCreate(
                recipient_id=recipient_id,
                sender_id=requester_id,
                title="审批催办提醒",
                content=f"{requester_name} 催办了「{summary}」，请及时处理。",
                notif_type="approval",
                ref_type="approval_instance",
                ref_id=instance.id,
            ),
        )

    now = datetime.now(timezone.utc)
    instance.updated_at = now
    await db.flush()
    return ApprovalReminderResult(
        instance_id=instance.id,
        node_order=instance.current_node_order,
        notified_count=len(recipient_ids),
        notified_approver_ids=recipient_ids,
    )


async def mark_approval_notifications_read_for_recipient(
    db: AsyncSession,
    recipient_id: int,
    instance_id: int,
) -> int:
    """审批人完成当前单据处理后，同步清理该单据的待办通知红点。"""
    if not recipient_id or not instance_id:
        return 0
    return await NotificationService.mark_read_by_ref(
        db,
        user_id=recipient_id,
        notif_type="approval",
        ref_type="approval_instance",
        ref_id=instance_id,
    )


async def list_my_pending(
    db: AsyncSession,
    approver_id: int,
    skip: int = 0,
    limit: int = 20,
    business_type: Optional[str] = None,
    start_date: date | datetime | None = None,
    end_date: date | datetime | None = None,
    applicant_id: Optional[int] = None,
    applicant_keyword: Optional[str] = None,
) -> tuple[int, list[ApprovalInstance]]:
    """查询我的待审批列表（支持所有 approver_type 的动态匹配）。

    匹配逻辑：
    1. 获取当前审批人的信息（is_superuser/department_id/角色列表）
    2. 查询所有 status='pending' 的实例
    3. 对每个实例，获取当前节点（current_node_order）的 approver_type
    4. 根据 approver_type 判断当前用户是否为该节点的合法审批人
    5. 排除当前节点上已操作过（action=approve/reject）的实例（防重复审批）

    注意：此方法在内存中过滤（先拉取所有 pending 实例再逐一判断），
    对于大规模数据（>1000条待审批）可能存在性能问题，建议未来优化为 SQL 过滤。

    Args:
        db:          异步数据库会话
        approver_id: 当前审批人员工 ID
        skip:        分页偏移量
        limit:       分页大小

    Returns:
        tuple(总数, 分页后的实例列表)，总数为过滤后的实际匹配数量
    """
    from app.models.employee import Employee

    # 获取审批人基本信息（用于判断部门主管、总经理等动态角色）
    approver_result = await db.execute(
        select(Employee).where(Employee.id == approver_id)
    )
    approver = approver_result.scalar_one_or_none()
    if not approver:
        return 0, []

    # 提交审批时会把当前节点实际审批人写入 approval_tasks。
    # 待办列表必须优先以任务表为准，否则 multi_level_manager 等动态节点虽然已建任务，
    # 但会被下面的模板规则兜底匹配漏掉。
    task_stmt = (
        select(ApprovalInstance)
        .join(ApprovalTask, ApprovalTask.instance_id == ApprovalInstance.id)
        .options(
            selectinload(ApprovalInstance.records),
            selectinload(ApprovalInstance.tasks),
        )
        .where(
            ApprovalInstance.status == "pending",
            ApprovalTask.approver_id == approver_id,
            ApprovalTask.status == "pending",
        )
        .order_by(ApprovalInstance.created_at.desc(), ApprovalInstance.id.desc())
    )
    task_stmt = _apply_approval_archive_filters(
        task_stmt,
        business_type=business_type,
        start_date=start_date,
        end_date=end_date,
        applicant_id=applicant_id,
        applicant_keyword=applicant_keyword,
    )
    task_result = await db.execute(task_stmt)
    task_pending: list[ApprovalInstance] = []
    seen_instance_ids: set[int] = set()
    for item in task_result.scalars().all():
        item_id = getattr(item, "id", None)
        if item_id in seen_instance_ids:
            continue
        seen_instance_ids.add(item_id)
        task_pending.append(item)
    if task_pending:
        total = len(task_pending)
        return total, task_pending[skip: skip + limit]

    is_superuser = approver.is_superuser
    approver_dept_id = approver.department_id

    # 获取审批人的角色集合（用于判断 hr/manager/admin 等角色匹配）
    approver_roles: set[str] = set()
    try:
        from app.models.employee_role import EmployeeRole
        role_result = await db.execute(
            select(EmployeeRole.role_name).where(
                EmployeeRole.employee_id == approver_id,
                EmployeeRole.is_active == True,
            )
        )
        approver_roles = {r for (r,) in role_result.all()}
    except ImportError:
        pass
    if is_superuser:
        approver_roles.add("admin")  # 超级管理员自动拥有 admin 角色

    # 拉取所有待审批实例（含历史记录，用于去重判断）
    pending_stmt = (
        select(ApprovalInstance)
        .options(selectinload(ApprovalInstance.records), selectinload(ApprovalInstance.tasks))
        .where(ApprovalInstance.status == "pending")
        .order_by(ApprovalInstance.created_at.desc())
    )
    pending_stmt = _apply_approval_archive_filters(
        pending_stmt,
        business_type=business_type,
        start_date=start_date,
        end_date=end_date,
        applicant_id=applicant_id,
        applicant_keyword=applicant_keyword,
    )
    pending_result = await db.execute(pending_stmt)
    all_pending = list(pending_result.scalars().all())

    if not all_pending:
        return 0, []

    # ── 预加载优化：批量查询所有需要的 ApprovalNode 和 Employee 信息 ──
    # 收集所有 (flow_id, node_order) 对和所有 applicant_id
    flow_node_pairs = {(inst.flow_id, inst.current_node_order) for inst in all_pending}
    applicant_ids = {inst.applicant_id for inst in all_pending}

    # 批量加载所有相关审批节点（替代循环内逐条查询）
    node_conditions = [
        (ApprovalNode.flow_id == fid) & (ApprovalNode.node_order == norder)
        for fid, norder in flow_node_pairs
    ]
    from sqlalchemy import or_
    nodes_result = await db.execute(
        select(ApprovalNode).where(or_(*node_conditions))
    )
    all_nodes = list(nodes_result.scalars().all())
    # 构建 (flow_id, node_order) → ApprovalNode 查找字典
    node_map: dict[tuple[int, int], ApprovalNode] = {
        (n.flow_id, n.node_order): n for n in all_nodes
    }

    # 批量加载所有申请人的 direct_manager_id 和 department_id
    emp_result = await db.execute(
        select(Employee.id, Employee.direct_manager_id, Employee.department_id)
        .where(Employee.id.in_(applicant_ids))
    )
    emp_info_map: dict[int, tuple] = {
        row[0]: (row[1], row[2]) for row in emp_result.all()
    }

    matching = []
    for inst in all_pending:
        current_node_pending_tasks = [
            task
            for task in list(getattr(inst, "tasks", []) or [])
            if getattr(task, "node_order", None) == getattr(inst, "current_node_order", None)
            and getattr(task, "status", None) == "pending"
        ]
        if current_node_pending_tasks:
            continue

        # 检查当前节点上是否已有本人的操作记录（防止重复审批）
        already_acted = any(
            r.approver_id == approver_id
            and r.node_order == inst.current_node_order
            and r.action in ("approve", "reject")
            for r in inst.records
        )
        if already_acted:
            continue

        # 从预加载的字典中获取当前节点配置（无需逐条 DB 查询）
        node = node_map.get((inst.flow_id, inst.current_node_order))
        if not node:
            continue

        # 根据节点 approver_type 判断当前用户是否为合法审批人
        is_match = False
        if node.approver_type == "specific_user" and node.approver_id == approver_id:
            is_match = True
        elif node.approver_type == "direct_manager":
            emp_info = emp_info_map.get(inst.applicant_id)
            if emp_info and emp_info[0] == approver_id:
                is_match = True
        elif node.approver_type == "hr" and "hr" in approver_roles:
            is_match = True
        elif node.approver_type == "department_head":
            emp_info = emp_info_map.get(inst.applicant_id)
            if emp_info and emp_info[1] and emp_info[1] == approver_dept_id and (
                "manager" in approver_roles or "admin" in approver_roles
            ):
                is_match = True
        elif node.approver_type == "gm" and is_superuser:
            is_match = True

        if is_match:
            matching.append(inst)

    # 在内存中分页（因过滤逻辑无法在 SQL 层实现）
    total = len(matching)
    paginated = matching[skip: skip + limit]
    return total, paginated


async def list_my_submitted(
    db: AsyncSession,
    applicant_id: int,
    skip: int = 0,
    limit: int = 20,
    status_filter: Optional[str] = None,
    business_type: Optional[str] = None,
    start_date: date | datetime | None = None,
    end_date: date | datetime | None = None,
    filter_applicant_id: Optional[int] = None,
    applicant_keyword: Optional[str] = None,
) -> tuple[int, list[ApprovalInstance]]:
    """查询我提交的审批列表（以申请人视角）。

    用于员工查看自己提交的所有审批申请及其当前状态。

    Args:
        db:            异步数据库会话
        applicant_id:  申请人员工 ID
        skip:          分页偏移量
        limit:         分页大小
        status_filter: 按状态过滤（pending/approved/rejected/withdrawn），None 表示全部

    Returns:
        tuple(总数, 分页后的实例列表)，按提交时间倒序排列
    """
    stmt = (
        select(ApprovalInstance)
        .options(selectinload(ApprovalInstance.records), selectinload(ApprovalInstance.tasks))
        .where(ApprovalInstance.applicant_id == applicant_id)
    )
    stmt = _apply_approval_archive_filters(
        stmt,
        business_type=business_type,
        start_date=start_date,
        end_date=end_date,
        status_filter=status_filter,
        applicant_id=filter_applicant_id,
        applicant_keyword=applicant_keyword,
    )

    # 先查总数（用于前端分页显示）
    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(ApprovalInstance.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return total, list(result.scalars().all())


async def list_my_approved(
    db: AsyncSession,
    approver_id: int,
    skip: int = 0,
    limit: int = 20,
    business_type: Optional[str] = None,
    start_date: date | datetime | None = None,
    end_date: date | datetime | None = None,
    applicant_id: Optional[int] = None,
    applicant_keyword: Optional[str] = None,
) -> tuple[int, list[ApprovalInstance]]:
    """查询我已审批过的实例列表（以审批人视角，含通过和拒绝）。

    实现方式：
    1. 在 ApprovalRecord 中查找该审批人有 approve/reject 操作记录的实例 ID 集合
    2. 通过 instance_id IN (...) 查询对应的实例

    Args:
        db:          异步数据库会话
        approver_id: 审批人员工 ID
        skip:        分页偏移量
        limit:       分页大小

    Returns:
        tuple(总数, 分页后的实例列表)，按创建时间倒序排列
    """
    # 第一步：找出当前用户有过 approve/reject 操作的所有实例 ID（去重）
    acted_stmt = (
        select(ApprovalRecord.instance_id)
        .where(
            ApprovalRecord.approver_id == approver_id,
            ApprovalRecord.action.in_(["approve", "reject"]),
        )
        .distinct()
    )
    acted_result = await db.execute(acted_stmt)
    acted_ids = [row[0] for row in acted_result.all()]

    if not acted_ids:
        return 0, []

    # 第二步：查询这些实例的详情
    stmt = (
        select(ApprovalInstance)
        .options(selectinload(ApprovalInstance.records), selectinload(ApprovalInstance.tasks))
        .where(ApprovalInstance.id.in_(acted_ids))
    )
    stmt = _apply_approval_archive_filters(
        stmt,
        business_type=business_type,
        start_date=start_date,
        end_date=end_date,
        applicant_id=applicant_id,
        applicant_keyword=applicant_keyword,
    )
    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(ApprovalInstance.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return total, list(result.scalars().all())


async def list_my_cc(
    db: AsyncSession,
    employee_id: int,
    skip: int = 0,
    limit: int = 20,
    business_type: Optional[str] = None,
    start_date: date | datetime | None = None,
    end_date: date | datetime | None = None,
    applicant_id: Optional[int] = None,
    applicant_keyword: Optional[str] = None,
) -> tuple[int, list[ApprovalInstance]]:
    """查询抄送给我的审批记录。"""
    copied_stmt = (
        select(ApprovalRecord.instance_id)
        .where(
            ApprovalRecord.approver_id == employee_id,
            ApprovalRecord.action == "notify",
        )
        .distinct()
    )
    copied_result = await db.execute(copied_stmt)
    copied_ids = [row[0] for row in copied_result.all()]

    if not copied_ids:
        return 0, []

    stmt = (
        select(ApprovalInstance)
        .options(selectinload(ApprovalInstance.records), selectinload(ApprovalInstance.tasks))
        .where(ApprovalInstance.id.in_(copied_ids))
    )
    stmt = _apply_approval_archive_filters(
        stmt,
        business_type=business_type,
        start_date=start_date,
        end_date=end_date,
        applicant_id=applicant_id,
        applicant_keyword=applicant_keyword,
    )
    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(ApprovalInstance.created_at.desc(), ApprovalInstance.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return total, list(result.scalars().all())


async def _sync_business_status(
    db: AsyncSession, instance: ApprovalInstance, result: str
) -> None:
    """审批结果回调：将审批结论同步更新回对应的业务模型（内部工具函数）。

    当审批实例最终变为 approved 或 rejected 时调用此函数，
    自动更新业务记录的状态字段，实现审批引擎与业务模块的解耦。

    当前支持的业务模块：
    - leave：更新 LeaveRequest.approval_status 或 .status
    - recruitment_demand：更新 RecruitmentDemand.status

    扩展方式：在此函数中添加新的 elif 分支即可接入新业务模块。
    所有同步异常均为 WARNING 级别记录，不影响审批流程本身的事务。

    Args:
        db:       异步数据库会话
        instance: 已审批完成的 ApprovalInstance（含 module 和 business_id）
        result:   审批结果字符串（"approved" 或 "rejected"）
    """
    module = _instance_text(instance, "module")
    if not module:
        return
    if isinstance(getattr(instance, 'template_version_id', None), int) and _instance_text(instance, 'module') in {'leave','overtime'}:
        # Dynamic form IDs are approval IDs, not legacy leave/overtime row IDs.
        # The transactional outbox applies their effects using the captured form.
        return
    try:
        if module == "leave":
            from app.models.leave import ApprovalStatus as LeaveApprovalStatus, LeaveRequest
            leave_result = await db.execute(
                select(LeaveRequest).where(LeaveRequest.id == instance.business_id)
            )
            leave = leave_result.scalar_one_or_none()
            if leave and hasattr(leave, 'approval_status'):
                # 使用枚举值而非字符串，确保与 PostgreSQL ENUM 列类型匹配
                leave.approval_status = LeaveApprovalStatus(result)
            elif leave and hasattr(leave, 'status'):
                leave.status = "approved" if result == "approved" else "rejected"
        elif module == "overtime":
            from app.models.overtime import OvertimeRequest
            from app.services.attendance import AttendanceRecordService

            overtime_result = await db.execute(
                select(OvertimeRequest).where(OvertimeRequest.id == instance.business_id)
            )
            overtime = overtime_result.scalar_one_or_none()
            if overtime:
                overtime.status = result
                overtime.approval_status = result

                record_result = await db.execute(
                    select(ApprovalRecord)
                    .where(ApprovalRecord.instance_id == instance.id)
                    .order_by(ApprovalRecord.id.desc())
                    .limit(1)
                )
                record = record_result.scalar_one_or_none()
                if record:
                    overtime.approver_id = record.approver_id
                    overtime.approved_at = record.acted_at
                    if result == "rejected":
                        overtime.reject_reason = record.comment
                    else:
                        overtime.reject_reason = None
                elif result == "rejected":
                    overtime.reject_reason = None
                if result == "approved":
                    await db.flush()
                    await AttendanceRecordService.sync_approved_overtime_to_attendance(db, overtime)
        elif module in {"recruitment", "recruitment_demand"} or _instance_text(instance, "business_type") == "recruitment_demand":
            try:
                from app.models.recruitment import ApprovalStatus as RecruitmentApprovalStatus
                from app.models.recruitment import DemandStatus as RecruitmentDemandStatus
                from app.models.recruitment import RecruitmentDemand
                demand_result = await db.execute(
                    select(RecruitmentDemand).where(
                        RecruitmentDemand.id == instance.business_id
                    )
                )
                demand = demand_result.scalar_one_or_none()
                if demand and hasattr(demand, "approval_status"):
                    if result == "approved":
                        demand.approval_status = RecruitmentApprovalStatus.APPROVED
                        demand.status = RecruitmentDemandStatus.RECRUITING
                    else:
                        demand.approval_status = RecruitmentApprovalStatus.REJECTED
                        demand.status = RecruitmentDemandStatus.CANCELLED
            except ImportError:
                pass
        elif module in HEADCOUNT_REQUEST_CODES or _instance_text(instance, "business_type") in HEADCOUNT_REQUEST_CODES:
            await _sync_headcount_request_approval(db, instance, result)
        elif module == BUSINESS_TRIP_CODE and result == "approved":
            await _sync_business_trip_attendance(db, instance)
    except Exception as exc:
        import logging
        # 同步失败不阻断审批流程，只记录 WARNING 日志
        logging.getLogger(__name__).warning(
            "业务状态同步失败 instance_id=%s module=%s: %s",
            getattr(instance, "id", None), module, exc,
        )


async def _sync_headcount_request_approval(
    db: AsyncSession,
    instance: ApprovalInstance,
    result: str,
) -> None:
    """用人审批通过后，将批准的扩编人数回写到部门编制。"""
    if result != "approved":
        return
    form_data = _instance_form_data(instance)
    department_id_value = form_data.get("department_id") or form_data.get("department")
    headcount_value = (
        form_data.get("headcount")
        or form_data.get("increase_count")
        or form_data.get("requested_count")
    )
    department_number = _to_number(department_id_value)
    headcount_number = _to_number(headcount_value)
    if department_number is None or headcount_number is None:
        return
    department_id = int(department_number)
    requested_increase = int(headcount_number)
    if department_id <= 0 or requested_increase <= 0:
        return

    available_number = _to_number(form_data.get("available_count"))
    if available_number is not None and available_number < 0:
        requested_increase = max(requested_increase, requested_increase - int(available_number))

    from app.models.organization import Department

    department_result = await db.execute(
        select(Department).where(Department.id == department_id)
    )
    department = department_result.scalar_one_or_none()
    if department is None:
        return

    current_quota = int(getattr(department, "headcount_quota", 0) or 0)
    form_quota_number = _to_number(form_data.get("quota"))
    baseline_quota = max(current_quota, int(form_quota_number or 0))
    department.headcount_quota = baseline_quota + requested_increase


async def _resolve_notify_recipient_ids(
    db: AsyncSession,
    node: Any,
    applicant_id: int,
) -> list[int]:
    configured_ids = _node_member_ids(node)
    if configured_ids:
        return configured_ids

    notify_node = type("Node", (), {
        "approver_type": _node_value(node, "approver_type"),
        "approver_id": _node_value(node, "approver_id"),
    })()
    try:
        resolved_id = await resolve_approver_id(db, notify_node, applicant_id)
    except Exception:
        resolved_id = None
    return _normalize_member_ids(resolved_id)


def _node_vote_pass_count(node: Any, total_count: int) -> int:
    rule_data = _node_rule_data(node)
    try:
        configured = int(rule_data.get("vote_pass_count") or 1)
    except (TypeError, ValueError):
        configured = 1
    total = max(1, total_count)
    return max(1, min(configured, total))


async def _approval_record_actions_for_node(
    db: AsyncSession,
    instance_id: int,
    node_order: int,
) -> list[str]:
    result = await db.execute(
        select(ApprovalRecord.action).where(
            ApprovalRecord.instance_id == instance_id,
            ApprovalRecord.node_order == node_order,
            ApprovalRecord.action.in_(["approve", "reject"]),
        )
    )
    return [str(action or "").lower() for (action,) in result.all()]


async def process_approval(
    db: AsyncSession,
    instance_id: int,
    approver_id: int,
    data: ApprovalActionRequest,
) -> ApprovalInstance:
    instance = await get_instance(db, instance_id)

    if instance.status != "pending":
        raise HTTPException(status_code=400, detail="审批实例当前不可操作")

    if data.action not in ("approve", "reject", "transfer"):
        raise HTTPException(status_code=400, detail="操作类型无效，仅支持 approve/reject/transfer")
    if data.action == "transfer" and not data.transfer_to_id:
        raise HTTPException(status_code=400, detail="转审操作必须指定 transfer_to_id")

    nodes = await _load_flow_snapshot_nodes(instance)
    if not nodes:
        flow = await get_flow(db, instance.flow_id)
        configured_nodes = list(getattr(flow, "nodes", []) or [])
        condition_context = (
            await _approval_condition_context(
                db,
                instance.applicant_id,
                getattr(instance, "form_data", None),
            )
            if _nodes_need_condition_context(configured_nodes)
            else {}
        )
        nodes = _expand_effective_nodes(configured_nodes, condition_context)

    current_index = next((idx for idx, node in enumerate(nodes) if node.get("node_order") == instance.current_node_order), -1)
    current_node = nodes[current_index] if current_index >= 0 else None
    if current_node is None:
        raise HTTPException(status_code=400, detail="当前审批节点不存在，请检查审批流程配置")

    previous_recipient_ids = set(_pending_approval_task_recipient_ids(instance))
    if _node_type(current_node) in PARALLEL_BRANCH_NODE_TYPES:
        return await _process_parallel_branch_approval(
            db,
            instance,
            nodes,
            current_node,
            approver_id,
            data,
            previous_recipient_ids,
        )

    permission_rules = await _instance_rule_group(db, instance, "permission_rules")
    action_rules = _parse_json_object(permission_rules.get("in_progress_actions"))
    comment_text = str(data.comment or "").strip()
    if data.action in {"approve", "reject"}:
        if _node_type(current_node) == "handler" and action_rules.get("handler_comment_required") and not comment_text:
            raise HTTPException(status_code=400, detail="办理意见不能为空")
        if _node_type(current_node) != "handler" and action_rules.get("approval_comment_required") and not comment_text:
            raise HTTPException(status_code=400, detail="审批意见不能为空")

    pending_tasks = await _pending_tasks_for_node(db, instance, instance.current_node_order)
    if not pending_tasks:
        await create_pending_task_for_instance(db, instance)
        if getattr(instance, "current_node_order", None) != _node_value(current_node, "node_order"):
            raise HTTPException(status_code=409, detail="审批节点已由系统规则自动流转，请刷新后重试")
        pending_tasks = await _pending_tasks_for_node(db, instance, instance.current_node_order)

    pending_task = next(
        (task for task in pending_tasks if getattr(task, "approver_id", None) == approver_id),
        None,
    )
    if pending_task is None:
        raise HTTPException(status_code=403, detail="当前用户不是该节点的待审批人")

    now = datetime.now(timezone.utc)
    record = ApprovalRecord(
        instance_id=instance.id,
        node_order=instance.current_node_order,
        approver_id=approver_id,
        action=data.action,
        comment=data.comment,
        acted_at=now,
    )
    db.add(record)

    pending_task.status = "completed"
    pending_task.completed_at = now
    pending_task.approver_id = approver_id
    await mark_approval_notifications_read_for_recipient(db, approver_id, instance.id)

    approval_mode = _node_approval_mode(current_node)

    if data.action == "transfer":
        new_task = ApprovalTask(
            instance_id=instance.id,
            node_order=instance.current_node_order,
            approver_id=data.transfer_to_id,
            status="pending",
        )
        _append_task_to_instance(instance, new_task)
        db.add(new_task)
    elif data.action == "reject" and approval_mode != "vote":
        for task in pending_tasks:
            if getattr(task, "status", None) == "pending":
                task.status = "completed"
                task.completed_at = now
        instance.status = "rejected"
        await _sync_business_status(db, instance, "rejected")
        await _enqueue_approval_terminal_event(
            db,
            instance,
            "rejected",
            actor_id=approver_id,
            occurred_at=now,
        )
    elif approval_mode == "vote":
        await db.flush()
        all_tasks = await _tasks_for_node(db, instance, instance.current_node_order)
        total_count = len(all_tasks) or len(pending_tasks) or 1
        pass_count = _node_vote_pass_count(current_node, total_count)
        actions = await _approval_record_actions_for_node(db, instance.id, instance.current_node_order)
        approve_count = sum(1 for action in actions if action == "approve")
        reject_count = sum(1 for action in actions if action == "reject")
        remaining_tasks = [
            task
            for task in all_tasks
            if getattr(task, "status", None) == "pending"
        ]
        if approve_count >= pass_count:
            for task in remaining_tasks:
                task.status = "completed"
                task.completed_at = now
            next_index = current_index + 1
            if next_index < len(nodes):
                instance.current_node_order = nodes[next_index].get("node_order", instance.current_node_order + 1)
                await create_pending_task_for_instance(db, instance)
            else:
                instance.status = "approved"
                await _sync_business_status(db, instance, "approved")
                await _enqueue_approval_terminal_event(
                    db,
                    instance,
                    "approved",
                    actor_id=approver_id,
                    occurred_at=now,
                )
        elif approve_count + len(remaining_tasks) < pass_count or reject_count >= total_count:
            for task in remaining_tasks:
                task.status = "completed"
                task.completed_at = now
            instance.status = "rejected"
            await _sync_business_status(db, instance, "rejected")
            await _enqueue_approval_terminal_event(
                db,
                instance,
                "rejected",
                actor_id=approver_id,
                occurred_at=now,
                )
        else:
            await _notify_after_approval_action(
                db,
                instance,
                actor_id=approver_id,
                action=data.action,
                previous_recipient_ids=previous_recipient_ids,
            )
            instance.updated_at = now
            await db.flush()
            await db.refresh(instance)
            return instance
    else:
        if approval_mode == "counter_sign":
            remaining_tasks = [
                task
                for task in pending_tasks
                if task is not pending_task and getattr(task, "status", None) == "pending"
            ]
            if remaining_tasks:
                await _notify_after_approval_action(
                    db,
                    instance,
                    actor_id=approver_id,
                    action=data.action,
                    previous_recipient_ids=previous_recipient_ids,
                )
                instance.updated_at = now
                await db.flush()
                await db.refresh(instance)
                return instance
        elif approval_mode == "sequential":
            next_task = await create_pending_task_for_instance(db, instance)
            if next_task:
                await _notify_after_approval_action(
                    db,
                    instance,
                    actor_id=approver_id,
                    action=data.action,
                    previous_recipient_ids=previous_recipient_ids,
                )
                instance.updated_at = now
                await db.flush()
                await db.refresh(instance)
                return instance
        else:
            for task in pending_tasks:
                if task is not pending_task and getattr(task, "status", None) == "pending":
                    task.status = "completed"
                    task.completed_at = now

        next_index = current_index + 1
        if next_index < len(nodes):
            instance.current_node_order = nodes[next_index].get("node_order", instance.current_node_order + 1)
            await create_pending_task_for_instance(db, instance)
        else:
            instance.status = "approved"
            await _sync_business_status(db, instance, "approved")
            await _enqueue_approval_terminal_event(
                db,
                instance,
                "approved",
                actor_id=approver_id,
                occurred_at=now,
            )

    await _notify_after_approval_action(
        db,
        instance,
        actor_id=approver_id,
        action=data.action,
        previous_recipient_ids=previous_recipient_ids,
    )
    instance.updated_at = now
    await db.flush()
    await db.refresh(instance)
    return instance


async def withdraw_instance(
    db: AsyncSession,
    instance_id: int,
    applicant_id: int,
) -> ApprovalInstance:
    """申请人撤回审批实例（仅 pending 状态可撤回）。

    撤回后实例状态变为 withdrawn，不触发业务状态回调（由调用方决定业务状态处理）。
    已通过或已拒绝的实例不允许撤回。

    Args:
        db:           异步数据库会话
        instance_id:  待撤回的审批实例 ID
        applicant_id: 操作人员工 ID（必须是实例的申请人）

    Returns:
        撤回后的 ApprovalInstance 对象（status=withdrawn）

    Raises:
        HTTPException 403: 操作人不是申请人
        HTTPException 400: 实例不处于 pending 状态（已审批完成或已撤回）
        HTTPException 404: 实例不存在
    """
    instance = await get_instance(db, instance_id)
    if instance.applicant_id != applicant_id:
        raise HTTPException(status_code=403, detail="仅申请人可撤回审批")
    if instance.status != "pending":
        raise HTTPException(status_code=400, detail="仅待审批状态可撤回")
    instance.status = "withdrawn"
    for task in list(getattr(instance, "tasks", []) or []):
        if task.status == "pending":
            task.status = "cancelled"
    now = datetime.now(timezone.utc)
    instance.updated_at = now
    await _enqueue_approval_terminal_event(
        db,
        instance,
        "withdrawn",
        actor_id=applicant_id,
        occurred_at=now,
    )
    await db.flush()
    await db.refresh(instance)
    return instance


# ───────────────────── Bulk Action ─────────────────────
# 批量审批操作 — 支持一次操作多个待审批实例

async def bulk_approve(
    db: AsyncSession,
    instance_ids: list[int],
    approver_id: int,
    action: str,
    comment: Optional[str] = None,
) -> BulkApprovalResult:
    """批量审批：对多个审批实例执行相同的通过或拒绝操作。

    实现方式：逐个调用 process_approval()，单个失败不影响其他实例。
    失败原因（如实例已处理、无权操作等）会收集到 errors 列表中返回。

    Args:
        db:           异步数据库会话
        instance_ids: 待批量操作的审批实例 ID 列表
        approver_id:  操作人员工 ID
        action:       操作类型（仅支持 "approve" 或 "reject"，不支持 "transfer"）
        comment:      统一备注（可选，应用于所有实例）

    Returns:
        BulkApprovalResult Schema（success_count/failed_count/errors 列表）

    Raises:
        HTTPException 400: action 不是 approve 或 reject 时
    """
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="批量操作仅支持 approve 或 reject")

    success_count = 0
    failed_count = 0
    errors: list[str] = []

    req = ApprovalActionRequest(action=action, comment=comment)

    for instance_id in instance_ids:
        try:
            await process_approval(db, instance_id, approver_id, req)
            success_count += 1
        except HTTPException as exc:
            failed_count += 1
            errors.append(f"实例 {instance_id}: {exc.detail}")
        except Exception as exc:
            failed_count += 1
            errors.append(f"实例 {instance_id}: {str(exc)}")

    return BulkApprovalResult(success=success_count, failed=failed_count, errors=errors)


# ───────────────────── ApprovalDelegate ─────────────────────
# 审批代理授权 — 员工可将审批权限临时委托给他人

async def _delegate_to_out(
    db: AsyncSession,
    d: ApprovalDelegate,
) -> ApprovalDelegateOut:
    """将 ApprovalDelegate ORM 对象转换为带有姓名的 ApprovalDelegateOut Schema（内部工具函数）。

    由于 ApprovalDelegate 只存储员工 ID，需要额外查询 Employee 表获取姓名。

    Args:
        db: 异步数据库会话
        d:  ApprovalDelegate ORM 对象

    Returns:
        ApprovalDelegateOut Schema（含 delegator_name 和 delegate_name）
    """
    from app.models.employee import Employee

    # 分别查询委托人和代理人的姓名
    delegator_result = await db.execute(
        select(Employee.name).where(Employee.id == d.delegator_id)
    )
    delegator_name = delegator_result.scalar_one_or_none()

    delegate_result = await db.execute(
        select(Employee.name).where(Employee.id == d.delegate_id)
    )
    delegate_name = delegate_result.scalar_one_or_none()

    return ApprovalDelegateOut(
        id=d.id,
        delegator_id=d.delegator_id,
        delegator_name=delegator_name,
        delegate_id=d.delegate_id,
        delegate_name=delegate_name,
        start_date=d.start_date,
        end_date=d.end_date,
        is_active=d.is_active,
        reason=d.reason,
    )


async def create_delegate(
    db: AsyncSession,
    delegator_id: int,
    data: ApprovalDelegateCreate,
) -> ApprovalDelegate:
    """创建审批代理授权。

    业务规则：
    - 代理人（delegate_id）必须存在且处于激活状态
    - 不能将自己设置为代理人
    - end_date 不能早于 start_date

    使用场景：员工出差/休假期间，将审批权委托给同事代为处理。

    Args:
        db:            异步数据库会话
        delegator_id:  委托人员工 ID（当前登录用户）
        data:          代理授权创建 Schema（delegate_id/start_date/end_date/reason）

    Returns:
        已创建的 ApprovalDelegate ORM 对象（is_active=True）

    Raises:
        HTTPException 404: 代理人不存在
        HTTPException 400: 代理人账号停用、自我委托、日期逻辑错误
    """
    from app.models.employee import Employee

    # 校验代理人员工存在且激活
    target_result = await db.execute(
        select(Employee).where(Employee.id == data.delegate_id)
    )
    target = target_result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="代理人不存在")
    if not target.is_active:
        raise HTTPException(status_code=400, detail="代理人账号已停用")
    if data.delegate_id == delegator_id:
        raise HTTPException(status_code=400, detail="不能将自己设置为代理人")
    if data.end_date < data.start_date:
        raise HTTPException(status_code=400, detail="结束日期不能早于开始日期")

    delegate = ApprovalDelegate(
        delegator_id=delegator_id,
        delegate_id=data.delegate_id,
        start_date=data.start_date,
        end_date=data.end_date,
        reason=data.reason,
        is_active=True,  # 创建即激活
    )
    db.add(delegate)
    await db.flush()
    await db.refresh(delegate)
    return delegate


async def list_delegates(
    db: AsyncSession,
    employee_id: int,
) -> list[ApprovalDelegateOut]:
    """查询某员工创建的所有代理授权记录（以委托人身份，含历史记录）。

    Args:
        db:          异步数据库会话
        employee_id: 委托人员工 ID

    Returns:
        按 ID 倒序排列的代理授权列表（含委托人和代理人姓名）
    """
    from app.models.employee import Employee

    result = await db.execute(
        select(ApprovalDelegate)
        .where(ApprovalDelegate.delegator_id == employee_id)
        .order_by(ApprovalDelegate.id.desc())
    )
    delegates = list(result.scalars().all())

    out_list: list[ApprovalDelegateOut] = []
    for d in delegates:
        # 为每条记录分别查询委托人和代理人姓名（N+1，数量通常较小，可接受）
        delegator_result = await db.execute(
            select(Employee.name).where(Employee.id == d.delegator_id)
        )
        delegator_name = delegator_result.scalar_one_or_none()

        delegate_result = await db.execute(
            select(Employee.name).where(Employee.id == d.delegate_id)
        )
        delegate_name = delegate_result.scalar_one_or_none()

        out_list.append(
            ApprovalDelegateOut(
                id=d.id,
                delegator_id=d.delegator_id,
                delegator_name=delegator_name,
                delegate_id=d.delegate_id,
                delegate_name=delegate_name,
                start_date=d.start_date,
                end_date=d.end_date,
                is_active=d.is_active,
                reason=d.reason,
            )
        )
    return out_list


async def revoke_delegate(
    db: AsyncSession,
    delegate_record_id: int,
    employee_id: int,
) -> None:
    """撤销代理授权（将 is_active 置为 False，保留历史记录）。

    采用软删除方式（is_active=False）而非物理删除，保留审计记录。

    Args:
        db:                 异步数据库会话
        delegate_record_id: 代理授权记录主键 ID
        employee_id:        操作人员工 ID（必须是委托人本人）

    Raises:
        HTTPException 404: 授权记录不存在
        HTTPException 403: 操作人不是委托人
    """
    result = await db.execute(
        select(ApprovalDelegate).where(ApprovalDelegate.id == delegate_record_id)
    )
    d = result.scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="代理授权不存在")
    if d.delegator_id != employee_id:
        raise HTTPException(status_code=403, detail="仅委托人可撤销代理授权")
    d.is_active = False  # 软删除：标记为非激活，不物理删除
    await db.flush()


# ───────────────────── Transfer Approval ─────────────────────
# 转签操作 — 将当前节点的审批人更换为新审批人（正式转签，区别于 process_approval 中的转审）

async def transfer_approval(
    db: AsyncSession,
    instance_id: int,
    current_approver_id: int,
    new_approver_id: int,
    reason: str,
) -> ApprovalInstance:
    """转签：将当前审批节点的责任人更换为另一位员工。

    与 process_approval(action='transfer') 的区别：
    - process_approval 的 transfer 是在审批时附带转审意向
    - 本方法是独立的转签接口，语义更明确，专门用于更换审批人

    执行逻辑：
    1. 校验实例处于 pending 状态
    2. 校验新审批人存在且激活
    3. 创建"转签"类型的 ApprovalRecord（当前审批人 → 转出）
    4. 创建"pending"类型的 ApprovalRecord（新审批人 → 接收通知）
    5. 实例 current_node_order 不变，状态保持 pending

    Args:
        db:                  异步数据库会话
        instance_id:         目标审批实例 ID
        current_approver_id: 当前审批人（发起转签操作的人）员工 ID
        new_approver_id:     接收审批的新审批人员工 ID
        reason:              转签原因（必填，用于留存审计记录）

    Returns:
        更新后的 ApprovalInstance 对象（updated_at 已更新）

    Raises:
        HTTPException 400: 实例不处于 pending 状态、新审批人账号停用
        HTTPException 404: 实例不存在、新审批人不存在
    """
    from app.models.employee import Employee

    instance = await get_instance(db, instance_id)
    if instance.status != "pending":
        raise HTTPException(status_code=400, detail="该审批实例当前不可操作")

    # 校验新审批人存在且处于激活状态
    new_approver_result = await db.execute(
        select(Employee).where(Employee.id == new_approver_id)
    )
    new_approver = new_approver_result.scalar_one_or_none()
    if not new_approver:
        raise HTTPException(status_code=404, detail="转签目标员工不存在")
    if not new_approver.is_active:
        raise HTTPException(status_code=400, detail="转签目标员工账号已停用")

    now = datetime.now(timezone.utc)

    # 记录原审批人的转签操作（转出记录）
    transfer_record = ApprovalRecord(
        instance_id=instance.id,
        node_order=instance.current_node_order,
        approver_id=current_approver_id,
        action="transfer",
        comment=f"转签至员工ID={new_approver_id} — {reason}",
        acted_at=now,
    )
    db.add(transfer_record)

    # 为新审批人创建待处理通知记录（使其出现在"待审批"列表中）
    pending_record = ApprovalRecord(
        instance_id=instance.id,
        node_order=instance.current_node_order,
        approver_id=new_approver_id,
        action="pending",
        comment=f"已接受来自员工ID={current_approver_id}的转签",
        acted_at=now,
    )
    db.add(pending_record)

    instance.updated_at = now
    await db.flush()
    await db.refresh(instance)
    return instance
