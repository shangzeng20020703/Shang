"""
TDD red-phase tests for the approval template configurator story.

These tests are designed from:
docs/story-20260422-approval-frontend-and- backend.md
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.models.approval import (
    ApprovalFlow,
    ApprovalFormField,
    ApprovalNode,
    ApprovalTemplateVersion,
    ApprovalType,
)


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar.return_value = value
    result.scalars.return_value.first.return_value = value if not isinstance(value, list) else None
    if isinstance(value, list):
        result.scalars.return_value.all.return_value = value
        result.all.return_value = [(item,) for item in value]
    else:
        result.scalars.return_value.all.return_value = []
        result.all.return_value = []
    return result


def _fake_db(*execute_results):
    db = AsyncMock()
    added: list[object] = []
    next_ids = {
        ApprovalType: 100,
        ApprovalFlow: 200,
        ApprovalFormField: 300,
        ApprovalNode: 400,
        ApprovalTemplateVersion: 500,
    }

    def _assign_id(obj):
        for cls, next_id in list(next_ids.items()):
            if isinstance(obj, cls) and getattr(obj, "id", None) is None:
                setattr(obj, "id", next_id)
                next_ids[cls] = next_id + 1
                break

    def add(obj):
        _assign_id(obj)
        added.append(obj)

    def add_all(objects):
        for obj in objects:
            _assign_id(obj)
            added.append(obj)

    db.add = MagicMock(side_effect=add)
    db.add_all = MagicMock(side_effect=add_all)
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.added = added
    db.execute = AsyncMock(side_effect=[_result(item) for item in execute_results])
    return db


def _plain_template_payload(*, fields: list[dict], flow_nodes: list[dict] | None = None):
    return SimpleNamespace(
        name="Configurable Travel Approval",
        business_code="configurable_travel",
        category="general",
        scope="mobile",
        is_active=True,
        sort_order=7,
        icon="approval",
        description="Template configured through management UI",
        permission_rules={"visible_roles": ["employee"]},
        exception_rules={},
        auto_approval_rule={"enabled": False},
        fields=fields,
        flow_nodes=flow_nodes
        if flow_nodes is not None
        else [
            {
                "node_order": 1,
                "node_type": "approval",
                "approver_type": "specific_user",
                "approver_id": 12,
                "approval_mode": "or_sign",
                "assignee_source": "specific_user",
                "member_ids": [12],
            }
        ],
    )


def _required_prd_fields() -> list[dict]:
    return [
        {"label": "Title", "code": "title", "field_type": "text", "is_required": True},
        {"label": "Reason", "code": "reason", "field_type": "textarea", "is_required": True},
        {"label": "Start Date", "code": "start_date", "field_type": "date", "is_required": True},
        {"label": "Trip Dates", "code": "trip_dates", "field_type": "date_range", "is_required": True},
        {"label": "Days", "code": "days", "field_type": "number", "is_required": True},
        {"label": "Budget", "code": "budget", "field_type": "amount", "is_required": True},
        {"label": "Receipt", "code": "receipt", "field_type": "attachment", "is_required": False},
        {"label": "Companion", "code": "companion", "field_type": "member", "is_required": False},
        {"label": "Department", "code": "department", "field_type": "department", "is_required": True},
        {"label": "Itinerary", "code": "itinerary", "field_type": "detail", "is_required": False},
        {"label": "Destination", "code": "destination", "field_type": "location", "is_required": True},
    ]


def _jsonish(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _added(db, cls):
    return [obj for obj in db.added if isinstance(obj, cls)]


def _dependency_roles(dependency_call) -> set[str]:
    for cell in dependency_call.__closure__ or ():
        value = cell.cell_contents
        if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
            return set(value)
    return set()


def _template_record(
    *,
    template_id: int,
    business_code: str,
    category: str = "general",
    is_active: bool = True,
    permission_rules: dict | None = None,
    form_fields: list | None = None,
):
    template = SimpleNamespace(
        id=template_id,
        name=f"Template {template_id}",
        business_code=business_code,
        category=category,
        scope="mobile",
        is_active=is_active,
        sort_order=template_id,
        icon="calendar",
        description=f"Template {template_id}",
        form_fields=form_fields or [],
    )
    if permission_rules is not None:
        template.permission_rules = permission_rules
    return template


def _field_record(
    field_id: int,
    code: str,
    label: str,
    field_type: str,
    *,
    approval_type_id: int = 12,
    is_required: bool = False,
    is_readonly: bool = False,
):
    return SimpleNamespace(
        id=field_id,
        approval_type_id=approval_type_id,
        label=label,
        code=code,
        field_type=field_type,
        is_required=is_required,
        default_value=None,
        options_json=None,
        display_condition=None,
        validation_rule=None,
        editable_scope=None,
        print_visible=True,
        placeholder=None,
        is_business_calculation=False,
        is_readonly=is_readonly,
        sort_order=field_id,
        created_at=datetime.now(timezone.utc),
    )


def test_copy_template_config_service_schema_and_route_exist():
    import app.services.approval as approval_service
    from app.api.v1.approval import router
    from app.schemas.approval import ApprovalTemplateCopyRequest

    assert callable(getattr(approval_service, "copy_template_config", None))
    assert ApprovalTemplateCopyRequest.model_fields
    paths = {
        route.path
        for route in router.routes
        if isinstance(route, APIRoute) and "POST" in route.methods
    }
    assert "/types/{approval_type_id}/copy" in paths


def test_template_config_route_and_schema_include_rule_fields():
    from app.api.v1.approval import router
    from app.schemas.approval import ApprovalTemplateConfigSave, ApprovalTemplateCopyRequest

    assert "/template-configs" in {
        route.path
        for route in router.routes
        if isinstance(route, APIRoute) and "POST" in route.methods
    }
    assert {"permission_rules", "exception_rules", "auto_approval_rule"} <= set(
        ApprovalTemplateConfigSave.model_fields
    )
    assert hasattr(ApprovalTemplateCopyRequest, "model_fields")


def test_template_metadata_schema_and_routes_exist():
    from app.api.v1.approval import router
    from app.schemas.approval import (
        ApprovalTemplateBulkDisableRequest,
        ApprovalTemplateConfigSave,
        ApprovalTypeOut,
    )

    assert {"category_key", "icon_key", "icon_tone"} <= set(ApprovalTemplateConfigSave.model_fields)
    assert {"category_key", "icon_key", "icon_tone", "updated_at"} <= set(ApprovalTypeOut.model_fields)
    assert ApprovalTemplateBulkDisableRequest.model_fields
    post_paths = {
        route.path
        for route in router.routes
        if isinstance(route, APIRoute) and "POST" in route.methods
    }
    get_paths = {
        route.path
        for route in router.routes
        if isinstance(route, APIRoute) and "GET" in route.methods
    }
    assert "/types/bulk-disable" in post_paths
    assert "/types/{approval_type_id}/export" in get_paths


def test_approval_template_list_route_exists():
    from app.api.v1.approval import router

    assert "/templates" in {
        route.path
        for route in router.routes
        if isinstance(route, APIRoute) and "GET" in route.methods
    }


@pytest.mark.asyncio
async def test_list_approval_templates_filters_by_visible_roles_and_active_flag():
    import app.services.approval as approval_service

    list_approval_templates = getattr(approval_service, "list_approval_templates", None)
    assert callable(list_approval_templates)
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _result(
                [
                    _template_record(
                        template_id=1,
                        business_code="annual_leave",
                        permission_rules={"visible_roles": ["employee"]},
                    ),
                    _template_record(
                        template_id=2,
                        business_code="salary_adjust",
                        permission_rules={"visible_roles": ["hr"]},
                    ),
                    _template_record(template_id=3, business_code="public_notice", permission_rules=None),
                    _template_record(template_id=4, business_code="disabled_leave", is_active=False),
                ]
            )
        ]
    )
    current_user = SimpleNamespace(id=9, roles=["employee"], is_superuser=False)

    groups = await list_approval_templates(db, current_user)
    flattened = []
    for group in groups:
        flattened.extend(getattr(group, "templates", None) or getattr(group, "items", None) or [])

    business_codes = {item.business_code for item in flattened if getattr(item, "business_code", None)}
    assert business_codes == {"annual_leave", "public_notice"}
    assert "salary_adjust" not in business_codes
    assert "disabled_leave" not in business_codes


@pytest.mark.asyncio
async def test_list_approval_templates_excludes_attendance_rule_binding_templates():
    import app.services.approval as approval_service

    rule_binding_template = _template_record(
        template_id=5,
        business_code="attendance_rule_1_overtime",
        permission_rules={"visible_roles": ["employee"]},
    )
    rule_binding_template.name = "北京总部-加班审批"
    rule_binding_template.scope = "web"

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _result(
                [
                    _template_record(
                        template_id=1,
                        business_code="leave",
                        permission_rules={"visible_roles": ["employee"]},
                    ),
                    _template_record(
                        template_id=2,
                        business_code="business_trip",
                        permission_rules={"visible_roles": ["employee"]},
                    ),
                    rule_binding_template,
                ]
            )
        ]
    )
    current_user = SimpleNamespace(id=9, roles=["employee"], is_superuser=False)

    groups = await approval_service.list_approval_templates(db, current_user, include_inactive=True)
    flattened = [template for group in groups for template in group.templates]
    business_codes = {item.business_code for item in flattened}

    assert business_codes == {"leave", "business_trip"}
    assert all(not item.business_code.startswith("attendance_rule_") for item in flattened)
    assert all("北京总部-加班审批" != item.name for item in flattened)


@pytest.mark.asyncio
async def test_list_approval_templates_excludes_legacy_default_overtime_template():
    import app.services.approval as approval_service

    legacy_overtime_template = _template_record(
        template_id=6,
        business_code="overtime",
        category="hr",
        permission_rules={"visible_roles": ["employee"]},
    )
    legacy_overtime_template.name = "Overtime Request"
    legacy_overtime_template.description = "Overtime requests"

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _result(
                [
                    _template_record(
                        template_id=1,
                        business_code="legal_overtime",
                        category="假勤管理",
                        permission_rules={"visible_roles": ["employee"]},
                    ),
                    legacy_overtime_template,
                ]
            )
        ]
    )
    current_user = SimpleNamespace(id=9, roles=["employee"], is_superuser=False)

    groups = await approval_service.list_approval_templates(db, current_user)
    business_codes = {template.business_code for group in groups for template in group.templates}

    assert business_codes == {"legal_overtime"}
    assert "overtime" not in business_codes


@pytest.mark.asyncio
async def test_list_approval_templates_filters_by_submit_permission_selected_members():
    import app.services.approval as approval_service

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _result(
                [
                    _template_record(
                        template_id=1,
                        business_code="leave",
                        permission_rules={"submit_permission": {"type": "selected_members", "member_ids": [9]}},
                    ),
                    _template_record(
                        template_id=2,
                        business_code="business_trip",
                        permission_rules={"submit_permission": {"type": "selected_members", "member_ids": [12]}},
                    ),
                    _template_record(
                        template_id=3,
                        business_code="outside",
                        permission_rules={"submit_permission": {"type": "all"}},
                    ),
                ]
            )
        ]
    )
    current_user = SimpleNamespace(id=9, roles=["employee"], is_superuser=False)

    groups = await approval_service.list_approval_templates(db, current_user)
    business_codes = {template.business_code for group in groups for template in group.templates}

    assert business_codes == {"leave", "outside"}
    assert "business_trip" not in business_codes


@pytest.mark.asyncio
async def test_publish_template_marks_template_active_for_mobile_visibility():
    import app.services.approval as approval_service

    approval_type = ApprovalType(
        id=7,
        name="林员工A可见模板",
        business_code="lin_employee_a_template",
        category="假勤管理",
        scope="mobile",
        is_active=False,
        status="draft",
        version=1,
    )
    flow = ApprovalFlow(
        id=17,
        name="林员工A可见模板 Flow",
        module="lin_employee_a_template",
        is_active=True,
    )
    db = _fake_db(approval_type, flow)

    result = await approval_service.publish_template(db, approval_type.id)

    assert result.is_active is True
    assert result.status == "enabled"
    assert result.version == 2
    versions = _added(db, ApprovalTemplateVersion)
    assert versions[-1].status == "published"
    assert versions[-1].snapshot_json["is_active"] is True


@pytest.mark.asyncio
async def test_bulk_disable_approval_types_marks_selected_templates_inactive():
    import app.services.approval as approval_service

    template = SimpleNamespace(id=7, is_active=True, status="enabled", updated_at=None)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result([template])])
    db.flush = AsyncMock()

    result = await approval_service.bulk_disable_approval_types(db, [7, 7])

    assert result == {"updated": 1, "ids": [7]}
    assert template.is_active is False
    assert template.status == "disabled"
    assert template.updated_at is not None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_approval_templates_normalizes_legacy_business_trip_expense_fields():
    import app.services.approval as approval_service

    legacy_fields = [
        _field_record(1, "department", "部门", "department", is_required=True),
        _field_record(2, "project_name", "项目名称", "select", is_required=True),
        _field_record(3, "trip_duration", "出差时长", "duration", is_required=True, is_readonly=True),
        _field_record(4, "detail_header", "明细", "static_text", is_readonly=True),
        _field_record(5, "flight_ticket", "机票", "amount", is_required=True),
        _field_record(6, "train_ticket", "火车票", "amount", is_required=True),
        _field_record(7, "lodging", "住宿", "amount", is_required=True),
        _field_record(8, "trip_allowance", "出差补助", "amount", is_required=True),
        _field_record(9, "local_transport", "市内交通", "amount", is_required=True),
        _field_record(10, "attachment", "附件", "attachment"),
    ]
    template = _template_record(
        template_id=12,
        business_code="business_trip",
        category="人事",
        permission_rules={"visible_roles": ["employee"]},
        form_fields=legacy_fields,
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result([template])])
    current_user = SimpleNamespace(id=9, roles=["employee"], is_superuser=False)

    groups = await approval_service.list_approval_templates(db, current_user)
    fields = groups[0].templates[0].form_fields
    field_codes = [field.code for field in fields]

    assert "trip_expense_detail" in field_codes
    assert "detail_header" not in field_codes
    assert "flight_ticket" not in field_codes
    detail_field = next(field for field in fields if field.code == "trip_expense_detail")
    assert detail_field.field_type == "detail"
    assert detail_field.is_business_calculation is True
    assert detail_field.options_json["summary"] == {
        "enabled": True,
        "label": "预计出差费用合计",
        "field_codes": ["flight_ticket", "train_ticket", "lodging", "trip_allowance", "local_transport"],
        "value_type": "amount",
    }
    assert [child["code"] for child in detail_field.options_json["child_fields"]] == [
        "flight_ticket",
        "train_ticket",
        "lodging",
        "trip_allowance",
        "local_transport",
    ]


@pytest.mark.asyncio
async def test_save_template_config_accepts_required_field_types_and_creates_snapshot():
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(fields=_required_prd_fields())
    payload.category_key = "attendance"
    payload.icon_key = "position"
    payload.icon_tone = "blue"

    await approval_service.save_template_config(db, payload)

    approval_types = _added(db, ApprovalType)
    fields = _added(db, ApprovalFormField)
    flows = _added(db, ApprovalFlow)
    nodes = _added(db, ApprovalNode)
    versions = _added(db, ApprovalTemplateVersion)
    assert len(approval_types) == 1
    assert len(fields) == len(payload.fields)
    assert len(flows) == 1
    assert flows[0].is_active is True
    assert len(nodes) == len(payload.flow_nodes)
    assert len(versions) == 1
    assert approval_types[0].category_key == "attendance"
    assert approval_types[0].icon_key == "position"
    assert approval_types[0].icon_tone == "blue"
    assert approval_types[0].updated_at is not None
    assert [field.field_type for field in fields] == [field["field_type"] for field in payload.fields]
    assert [field.code for field in fields] == [field["code"] for field in payload.fields]
    assert [field.sort_order for field in fields] == list(range(1, len(payload.fields) + 1))
    assert {field.approval_type_id for field in fields} == {approval_types[0].id}
    assert {node.flow_id for node in nodes} == {flows[0].id}
    snapshot = versions[0].snapshot_json
    assert snapshot["approval_type_id"] == approval_types[0].id
    assert snapshot["category_key"] == "attendance"
    assert snapshot["icon_key"] == "position"
    assert snapshot["icon_tone"] == "blue"
    assert [field["field_type"] for field in snapshot["fields"]] == [
        field["field_type"] for field in payload.fields
    ]
    assert snapshot["flow"]["flow_id"] == flows[0].id
    assert len(snapshot["flow"]["nodes"]) == len(nodes)
    assert db.flush.await_count >= 3
    db.refresh.assert_awaited_once_with(approval_types[0])


@pytest.mark.asyncio
async def test_save_template_config_persists_structured_rule_settings_to_type_and_snapshot():
    import app.services.approval as approval_service

    permission_rules = {
        "visible_scope": {"type": "departments", "department_ids": [3, 8], "include_sub_departments": True},
        "notify_members": {"enabled": True, "member_ids": [21, 22]},
        "submit_permission": {"type": "roles", "roles": ["employee", "manager"]},
        "view_permission": {"type": "submitter_and_approvers", "extra_member_ids": [31]},
        "field_edit_permissions": {
            "fixed_approver": {"modifiable": False},
            "fixed_cc": {"modifiable": False},
            "fixed_handler": {"modifiable": False},
        },
        "in_progress_actions": {
            "revoke_after_approval": True,
            "add_sign": True,
            "approval_comment_required": True,
            "handler_comment_required": True,
            "signature_required": True,
        },
        "assistant_management": {
            "assistant_admin_ids": [41, 42],
            "management_scope": {"type": "departments", "department_ids": [3]},
            "permissions": {
                "application_records": True,
                "template_settings": True,
                "rule_settings": True,
            },
        },
    }
    exception_rules = {
        "approval_empty_member": {"action": "transfer_to_specific", "member_id": 51},
        "handler_empty_member": {"action": "transfer_to_admin"},
        "approval_member_changed": {"action": "remind_admin_handover"},
        "handler_member_changed": {"action": "auto_transfer", "member_id": 52},
    }
    auto_approval_rule = {
        "duplicate_approver_policy": "continuous_only",
        "description": "Only continuous duplicate approver nodes are auto-approved.",
    }
    db = _fake_db()
    payload = _plain_template_payload(
        fields=[
            {"label": "Reason", "code": "reason", "field_type": "textarea"},
            {"label": "Amount", "code": "amount", "field_type": "amount"},
        ],
    )
    payload.permission_rules = permission_rules
    payload.exception_rules = exception_rules
    payload.auto_approval_rule = auto_approval_rule

    await approval_service.save_template_config(db, payload)

    approval_type = _added(db, ApprovalType)[0]
    snapshot = _added(db, ApprovalTemplateVersion)[0].snapshot_json
    assert approval_type.permission_rules == permission_rules
    assert approval_type.exception_rules == exception_rules
    assert approval_type.auto_approval_rule == auto_approval_rule
    assert snapshot["permission_rules"] == permission_rules
    assert snapshot["exception_rules"] == exception_rules
    assert snapshot["auto_approval_rule"] == auto_approval_rule


@pytest.mark.parametrize(
    "rule_patch, expected_detail",
    [
        (
            {"auto_approval_rule": {"duplicate_approver_policy": "skip_everything_forever"}},
            "duplicate",
        ),
        (
            {"exception_rules": {"approval_empty_member": {"action": "delete_node"}}},
            "exception",
        ),
        (
            {"exception_rules": {"handler_member_changed": {"action": "auto_transfer"}}},
            "member",
        ),
        (
            {"permission_rules": {"submit_permission": {"type": "anyone_with_link"}}},
            "submit",
        ),
        (
            {"permission_rules": {"submit_permission": {"type": "selected_members", "member_ids": []}}},
            "selected",
        ),
        (
            {"permission_rules": {"template_management": {"type": "selected_admins", "admin_ids": []}}},
            "administrator",
        ),
        (
            {"permission_rules": {"field_edit_permissions": {"fixed_approver": {"modifiable": "sometimes"}}}},
            "modifiable",
        ),
        (
            {"permission_rules": {"assistant_management": {"permissions": {"rule_settings": "yes"}}}},
            "assistant",
        ),
    ],
)
@pytest.mark.asyncio
async def test_save_template_config_rejects_invalid_rule_settings_before_writes(rule_patch, expected_detail):
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[
            {"label": "Reason", "code": "reason", "field_type": "textarea"},
            {"label": "Amount", "code": "amount", "field_type": "amount"},
        ],
    )
    for key, value in rule_patch.items():
        setattr(payload, key, value)

    with pytest.raises(HTTPException) as exc_info:
        await approval_service.save_template_config(db, payload)

    assert exc_info.value.status_code == 400
    assert expected_detail in str(exc_info.value.detail).lower()
    assert not _added(db, ApprovalType)
    assert not _added(db, ApprovalFormField)
    assert not _added(db, ApprovalFlow)
    assert not _added(db, ApprovalNode)
    assert not _added(db, ApprovalTemplateVersion)
    db.add.assert_not_called()
    db.add_all.assert_not_called()


@pytest.mark.asyncio
async def test_save_template_config_normalizes_duplicate_approver_policy_aliases():
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[
            {"label": "Reason", "code": "reason", "field_type": "textarea"},
            {"label": "Amount", "code": "amount", "field_type": "amount"},
        ],
    )
    payload.auto_approval_rule = {"duplicate_approver_policy": "skip_all_duplicates"}

    await approval_service.save_template_config(db, payload)

    approval_type = _added(db, ApprovalType)[0]
    snapshot = _added(db, ApprovalTemplateVersion)[0].snapshot_json
    assert approval_type.auto_approval_rule["duplicate_approver_policy"] == "first_only"
    assert snapshot["auto_approval_rule"]["duplicate_approver_policy"] == "first_only"


@pytest.mark.asyncio
async def test_save_template_config_accepts_every_node_required_duplicate_policy():
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[
            {"label": "Reason", "code": "reason", "field_type": "textarea"},
            {"label": "Amount", "code": "amount", "field_type": "amount"},
        ],
    )
    payload.auto_approval_rule = {"duplicate_approver_policy": "every_node_required"}

    await approval_service.save_template_config(db, payload)

    approval_type = _added(db, ApprovalType)[0]
    snapshot = _added(db, ApprovalTemplateVersion)[0].snapshot_json
    assert approval_type.auto_approval_rule["duplicate_approver_policy"] == "every_node_required"
    assert snapshot["auto_approval_rule"]["duplicate_approver_policy"] == "every_node_required"


@pytest.mark.asyncio
async def test_selected_template_administrators_are_enforced_by_backend():
    import app.services.approval as approval_service

    template = SimpleNamespace(
        permission_rules={
            "template_management": {
                "type": "selected_admins",
                "admin_ids": [7],
            },
        }
    )
    selected_hr = SimpleNamespace(id=7, is_superuser=False, roles=["hr"])
    other_hr = SimpleNamespace(id=8, is_superuser=False, roles=["hr"])
    selected_manager = SimpleNamespace(id=7, is_superuser=False, roles=["manager"])

    assert await approval_service.user_can_manage_template(AsyncMock(), template, selected_hr) is True
    assert await approval_service.user_can_manage_template(AsyncMock(), template, other_hr) is False
    assert await approval_service.user_can_manage_template(AsyncMock(), template, selected_manager) is False


@pytest.mark.parametrize(
    "field_patch, expected_detail",
    [
        ({"code": "title"}, "duplicate"),
        ({"code": ""}, "field code"),
        ({"code": "mystery", "field_type": "unsupported_widget"}, "unsupported field"),
    ],
)
@pytest.mark.asyncio
async def test_save_template_config_rejects_invalid_fields_before_writes(field_patch, expected_detail):
    import app.services.approval as approval_service

    db = _fake_db()
    fields = [
        {"label": "Title", "code": "title", "field_type": "text"},
        {"label": "Patched", "code": "patched", "field_type": "textarea"},
    ]
    fields[1].update(field_patch)
    payload = _plain_template_payload(fields=fields)

    with pytest.raises(HTTPException) as exc_info:
        await approval_service.save_template_config(db, payload)

    assert exc_info.value.status_code == 400
    assert expected_detail in str(exc_info.value.detail)
    assert not _added(db, ApprovalType)
    assert not _added(db, ApprovalFormField)
    assert not _added(db, ApprovalFlow)
    assert not _added(db, ApprovalNode)
    assert not _added(db, ApprovalTemplateVersion)
    db.add.assert_not_called()
    db.add_all.assert_not_called()


@pytest.mark.asyncio
async def test_save_template_config_persists_assignee_sources_and_approval_modes():
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[
            {"label": "Amount", "code": "amount", "field_type": "amount"},
            {"label": "Reviewer", "code": "reviewer", "field_type": "member"},
            {"label": "Department", "code": "department", "field_type": "department"},
        ],
        flow_nodes=[
            {
                "node_type": "approval",
                "assignee_source": "specific_user",
                "approver_type": "specific_user",
                "approver_id": 11,
                "member_ids": [11, 12],
                "approval_mode": "or_sign",
            },
            {
                "node_type": "approval",
                "assignee_source": "department_head",
                "approver_type": "department_head",
                "approval_mode": "counter_sign",
            },
            {
                "node_type": "approval",
                "assignee_source": "direct_manager",
                "approver_type": "direct_manager",
                "approval_mode": "or_sign",
            },
            {
                "node_type": "handler",
                "assignee_source": "applicant_select",
                "approver_type": "applicant_select",
                "approval_mode": "counter_sign",
            },
            {
                "node_type": "handler",
                "assignee_source": "related_member_field",
                "approver_type": "related_member_field",
                "related_field_code": "reviewer",
                "approval_mode": "or_sign",
            },
            {
                "node_type": "notify",
                "assignee_source": "role",
                "approver_type": "role",
                "role": "finance",
            },
        ],
    )

    await approval_service.save_template_config(db, payload)

    nodes = _added(db, ApprovalNode)
    assert [node.node_type for node in nodes] == [
        "approval",
        "approval",
        "approval",
        "handler",
        "handler",
        "notify",
    ]
    node_rules = [_jsonish(node.condition_rules) for node in nodes]
    assert [rules["assignee_source"] for rules in node_rules] == [
        "specific_user",
        "department_head",
        "direct_manager",
        "applicant_select",
        "related_member_field",
        "role",
    ]
    assert node_rules[0]["member_ids"] == [11, 12]
    assert node_rules[0]["approval_mode"] == "or_sign"
    assert node_rules[1]["approval_mode"] == "counter_sign"
    assert node_rules[4]["related_field_code"] == "reviewer"
    assert node_rules[5]["role"] == "finance"
    snapshot_nodes = _added(db, ApprovalTemplateVersion)[0].snapshot_json["flow"]["nodes"]
    snapshot_rules = [_jsonish(node["condition_rules"]) for node in snapshot_nodes]
    assert snapshot_rules == node_rules


@pytest.mark.asyncio
async def test_save_template_config_persists_wecom_approver_node_options():
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[
            {"label": "Reason", "code": "reason", "field_type": "textarea"},
        ],
        flow_nodes=[
            {
                "node_type": "approval",
                "assignee_source": "applicant_select",
                "approver_type": "applicant_select",
                "approval_mode": "counter_sign",
                "applicant_select_mode": "multiple",
                "applicant_select_scope": "role",
                "applicant_select_roles": ["manager"],
            },
            {
                "node_type": "approval",
                "assignee_source": "multi_level_manager",
                "approver_type": "multi_level_manager",
                "approval_mode": "sequential",
                "multi_level_end_type": "role",
                "multi_level_role": "manager",
                "multi_level_limit_enabled": True,
                "multi_level_limit_level": 3,
                "empty_action": "auto_approve",
            },
            {
                "node_type": "approval",
                "assignee_source": "department_head",
                "approver_type": "department_head",
                "approval_mode": "vote",
                "vote_pass_count": 2,
                "fallback_to_upper_manager": True,
                "applicant_self_if_head": False,
            },
            {
                "node_type": "approval",
                "assignee_source": "multi_level_manager",
                "approver_type": "multi_level_manager",
                "approval_mode": "sequential",
                "multi_level_end_type": "member",
                "multi_level_member_ids": [88, 99],
                "multi_level_limit_enabled": True,
                "multi_level_limit_level": 4,
            },
        ],
    )

    await approval_service.save_template_config(db, payload)

    node_rules = [_jsonish(node.condition_rules) for node in _added(db, ApprovalNode)]
    assert node_rules[0]["applicant_select_mode"] == "multiple"
    assert node_rules[0]["applicant_select_scope"] == "role"
    assert node_rules[0]["applicant_select_roles"] == ["manager"]
    assert node_rules[1]["multi_level_end_type"] == "role"
    assert node_rules[1]["multi_level_role"] == "manager"
    assert node_rules[1]["multi_level_limit_enabled"] is True
    assert node_rules[1]["multi_level_limit_level"] == 3
    assert node_rules[1]["empty_action"] == "auto_approve"
    assert node_rules[2]["approval_mode"] == "vote"
    assert node_rules[2]["vote_pass_count"] == 2
    assert node_rules[3]["multi_level_end_type"] == "member"
    assert node_rules[3]["multi_level_member_ids"] == [88, 99]
    assert node_rules[3]["multi_level_limit_enabled"] is True
    assert node_rules[3]["multi_level_limit_level"] == 4
    snapshot_nodes = _added(db, ApprovalTemplateVersion)[0].snapshot_json["flow"]["nodes"]
    assert [_jsonish(node["condition_rules"]) for node in snapshot_nodes] == node_rules


@pytest.mark.asyncio
async def test_save_template_config_requires_applicant_select_scope_detail():
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[{"label": "Reason", "code": "reason", "field_type": "textarea"}],
        flow_nodes=[
            {
                "node_type": "approval",
                "assignee_source": "applicant_select",
                "approver_type": "applicant_select",
                "applicant_select_scope": "selected_members",
                "applicant_select_member_ids": [],
            },
        ],
    )

    with pytest.raises(Exception) as exc_info:
        await approval_service.save_template_config(db, payload)

    assert "applicant select members are required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_applicant_select_runtime_rejects_member_outside_scope():
    import app.services.approval as approval_service

    node = {
        "node_type": "approval",
        "approver_type": "applicant_select",
        "condition_rules": {
            "assignee_source": "applicant_select",
            "applicant_select_scope": "selected_members",
            "applicant_select_member_ids": [11],
        },
    }

    with pytest.raises(Exception) as exc_info:
        await approval_service._resolve_node_approver_ids(
            _fake_db(),
            node,
            applicant_id=1,
            form_data={"selected_approver_ids": [12]},
        )

    assert "outside configured scope" in str(exc_info.value)
    assert await approval_service._resolve_node_approver_ids(
        _fake_db(),
        node,
        applicant_id=1,
        form_data={"selected_approver_ids": [11]},
    ) == [11]


@pytest.mark.asyncio
async def test_save_template_config_rejects_more_than_50_specific_approvers():
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[{"label": "Reason", "code": "reason", "field_type": "textarea"}],
        flow_nodes=[
            {
                "node_type": "approval",
                "assignee_source": "specific_user",
                "approver_type": "specific_user",
                "approver_id": 1,
                "member_ids": list(range(1, 52)),
            },
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        await approval_service.save_template_config(db, payload)

    assert exc_info.value.status_code == 400
    assert "50" in str(exc_info.value.detail)
    assert not _added(db, ApprovalNode)


@pytest.mark.parametrize(
    "branches",
    [
        [
            {"node_type": "condition_branch", "is_default_branch": False},
            {"node_type": "condition_branch", "is_default_branch": False},
        ],
        [
            {"node_type": "condition_branch", "is_default_branch": True},
            {"node_type": "condition_branch", "is_default_branch": True},
        ],
    ],
)
@pytest.mark.asyncio
async def test_condition_branch_requires_exactly_one_default_branch(branches):
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[
            {"label": "Reason", "code": "reason", "field_type": "textarea"},
            {"label": "Amount", "code": "amount", "field_type": "amount"},
        ],
        flow_nodes=[
            {
                "node_type": "approval",
                "assignee_source": "direct_manager",
                "approver_type": "direct_manager",
                "approval_mode": "or_sign",
            },
            *branches,
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        await approval_service.save_template_config(db, payload)

    assert exc_info.value.status_code == 400
    assert "default" in str(exc_info.value.detail).lower()
    assert not _added(db, ApprovalType)
    assert not _added(db, ApprovalFormField)
    assert not _added(db, ApprovalFlow)
    assert not _added(db, ApprovalNode)
    assert not _added(db, ApprovalTemplateVersion)


@pytest.mark.asyncio
async def test_nested_condition_branch_requires_exactly_one_default_branch():
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[
            {"label": "Reason", "code": "reason", "field_type": "textarea"},
            {"label": "Amount", "code": "amount", "field_type": "amount"},
        ],
        flow_nodes=[
            {
                "node_type": "condition_branch",
                "branches": [
                    {
                        "label": "大额申请",
                        "condition_groups": [
                            {
                                "condition_combinator": "and",
                                "conditions": [{"field": "amount", "operator": "gte", "value": 1000}],
                            }
                        ],
                        "nodes": [
                            {
                                "node_type": "condition_branch",
                                "branches": [
                                    {
                                        "label": "内层条件",
                                        "condition_groups": [
                                            {
                                                "condition_combinator": "and",
                                                "conditions": [{"field": "amount", "operator": "gte", "value": 5000}],
                                            }
                                        ],
                                        "nodes": [
                                            {
                                                "node_type": "approval",
                                                "assignee_source": "direct_manager",
                                                "approver_type": "direct_manager",
                                                "approval_mode": "or_sign",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                    {"label": "默认条件", "is_default_branch": True, "nodes": []},
                ],
            }
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        await approval_service.save_template_config(db, payload)

    assert exc_info.value.status_code == 400
    assert "default" in str(exc_info.value.detail).lower()
    assert not _added(db, ApprovalType)
    assert not _added(db, ApprovalFormField)
    assert not _added(db, ApprovalFlow)
    assert not _added(db, ApprovalNode)
    assert not _added(db, ApprovalTemplateVersion)


@pytest.mark.asyncio
async def test_condition_branch_with_one_default_stores_and_combined_conditions_in_snapshot():
    import app.services.approval as approval_service

    conditions = [
        {"dimension": "department", "field_code": "department", "operator": "in", "values": [3]},
        {"dimension": "amount", "field_code": "budget", "operator": "gte", "value": 5000},
        {"dimension": "date_range_days", "field_code": "trip_dates", "operator": "gt", "value": 3},
        {"dimension": "member", "field_code": "companion", "operator": "contains", "values": [8]},
        {"dimension": "location", "field_code": "destination", "operator": "in", "values": ["深圳"]},
    ]
    db = _fake_db()
    payload = _plain_template_payload(
        fields=_required_prd_fields(),
        flow_nodes=[
            {
                "node_type": "approval",
                "assignee_source": "direct_manager",
                "approver_type": "direct_manager",
                "approval_mode": "or_sign",
            },
            {
                "node_type": "condition_branch",
                "is_default_branch": False,
                "condition_combinator": "and",
                "conditions": conditions,
            },
            {"node_type": "condition_branch", "is_default_branch": True, "conditions": []},
        ],
    )

    await approval_service.save_template_config(db, payload)

    snapshot_nodes = _added(db, ApprovalTemplateVersion)[0].snapshot_json["flow"]["nodes"]
    branch_rules = [
        _jsonish(node["condition_rules"])
        for node in snapshot_nodes
        if node["node_type"] == "condition_branch" and not _jsonish(node["condition_rules"])["is_default_branch"]
    ]
    assert len(branch_rules) == 1
    assert branch_rules[0]["condition_combinator"] == "and"
    assert branch_rules[0]["conditions"] == conditions


@pytest.mark.asyncio
async def test_condition_branch_can_store_empty_branch_as_pass_through_when_main_flow_continues():
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[
            {"label": "Reason", "code": "reason", "field_type": "textarea"},
            {"label": "Duration", "code": "duration", "field_type": "number"},
        ],
        flow_nodes=[
            {
                "node_type": "condition_branch",
                "branches": [
                    {
                        "label": "短时长",
                        "condition_group_combinator": "or",
                        "condition_groups": [
                            {
                                "condition_combinator": "and",
                                "conditions": [{"field": "duration", "operator": "lte", "value": 1}],
                            }
                        ],
                        "nodes": [],
                    },
                    {
                        "label": "默认分支",
                        "is_default_branch": True,
                        "nodes": [],
                    },
                ],
            },
            {
                "node_type": "approval",
                "assignee_source": "direct_manager",
                "approver_type": "direct_manager",
                "approval_mode": "or_sign",
            },
        ],
    )

    await approval_service.save_template_config(db, payload)

    snapshot_nodes = _added(db, ApprovalTemplateVersion)[0].snapshot_json["flow"]["nodes"]
    branch_rules = _jsonish(snapshot_nodes[0]["condition_rules"])
    assert snapshot_nodes[0]["node_type"] == "condition_branch"
    assert branch_rules["branches"][0]["nodes"] == []
    assert branch_rules["branches"][1]["nodes"] == []
    assert snapshot_nodes[1]["node_type"] == "approval"


@pytest.mark.asyncio
async def test_parallel_branch_stores_branches_in_snapshot():
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[
            {"label": "Reason", "code": "reason", "field_type": "textarea"},
            {"label": "Amount", "code": "amount", "field_type": "amount"},
        ],
        flow_nodes=[
            {
                "node_type": "parallel_branch",
                "branches": [
                    {
                        "label": "总部审批",
                        "nodes": [
                            {
                                "node_type": "approval",
                                "assignee_source": "direct_manager",
                                "approver_type": "direct_manager",
                                "approval_mode": "or_sign",
                            }
                        ],
                    },
                    {
                        "label": "厂房办理",
                        "nodes": [
                            {
                                "node_type": "handler",
                                "assignee_source": "specific_user",
                                "approver_type": "specific_user",
                                "member_ids": [9],
                                "approval_mode": "or_sign",
                            }
                        ],
                    },
                ],
            }
        ],
    )

    await approval_service.save_template_config(db, payload)

    saved_nodes = _added(db, ApprovalNode)
    assert saved_nodes[0].node_type == "parallel_branch"
    assert saved_nodes[0].approver_type == "parallel_branch"
    snapshot_nodes = _added(db, ApprovalTemplateVersion)[0].snapshot_json["flow"]["nodes"]
    branch_rules = _jsonish(snapshot_nodes[0]["condition_rules"])
    assert snapshot_nodes[0]["node_type"] == "parallel_branch"
    assert [branch["label"] for branch in branch_rules["branches"]] == ["总部审批", "厂房办理"]
    assert branch_rules["branches"][0]["nodes"][0]["node_type"] == "approval"
    assert branch_rules["branches"][1]["nodes"][0]["node_type"] == "handler"


@pytest.mark.asyncio
async def test_parallel_branch_requires_two_branches():
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[
            {"label": "Reason", "code": "reason", "field_type": "textarea"},
        ],
        flow_nodes=[
            {
                "node_type": "parallel_branch",
                "branches": [
                    {
                        "label": "总部审批",
                        "nodes": [
                            {
                                "node_type": "approval",
                                "assignee_source": "direct_manager",
                                "approver_type": "direct_manager",
                                "approval_mode": "or_sign",
                            }
                        ],
                    }
                ],
            }
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        await approval_service.save_template_config(db, payload)

    assert exc_info.value.status_code == 400
    assert "parallel" in str(exc_info.value.detail).lower()
    assert not _added(db, ApprovalNode)


@pytest.mark.asyncio
async def test_parallel_branch_rejects_empty_branch():
    import app.services.approval as approval_service

    db = _fake_db()
    payload = _plain_template_payload(
        fields=[
            {"label": "Reason", "code": "reason", "field_type": "textarea"},
        ],
        flow_nodes=[
            {
                "node_type": "parallel_branch",
                "branches": [
                    {"label": "总部审批", "nodes": []},
                    {
                        "label": "厂房审批",
                        "nodes": [
                            {
                                "node_type": "approval",
                                "assignee_source": "direct_manager",
                                "approver_type": "direct_manager",
                                "approval_mode": "or_sign",
                            }
                        ],
                    },
                ],
            }
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        await approval_service.save_template_config(db, payload)

    assert exc_info.value.status_code == 400
    assert "parallel" in str(exc_info.value.detail).lower()
    assert not _added(db, ApprovalNode)


@pytest.mark.asyncio
async def test_copy_template_config_creates_independent_template_fields_flow_and_snapshot():
    import app.services.approval as approval_service
    from app.schemas.approval import ApprovalTemplateCopyRequest

    source_type = ApprovalType(
        id=10,
        name="Source Expense",
        business_code="source_expense",
        category="finance",
        scope="mobile",
        is_active=True,
        sort_order=2,
        icon="money",
        description="Source description",
        permission_rules={"visible_roles": ["employee"]},
        exception_rules=[{"kind": "limit"}],
        auto_approval_rule={"enabled": False},
        version=3,
    )
    source_fields = [
        ApprovalFormField(
            id=21,
            approval_type_id=10,
            label="Amount",
            code="amount",
            field_type="amount",
            is_required=True,
            sort_order=1,
        ),
        ApprovalFormField(
            id=22,
            approval_type_id=10,
            label="Reviewer",
            code="reviewer",
            field_type="member",
            is_required=False,
            sort_order=2,
        ),
    ]
    source_type.form_fields = source_fields
    source_flow = ApprovalFlow(
        id=30,
        name="Source Expense Flow",
        module="source_expense",
        description="Source flow",
        is_active=True,
    )
    source_nodes = [
        ApprovalNode(
            id=41,
            flow_id=30,
            node_order=1,
            node_type="approval",
            approver_type="specific_user",
            approver_id=9,
            condition_rules=json.dumps({"approval_mode": "or_sign", "member_ids": [9]}),
        ),
        ApprovalNode(
            id=42,
            flow_id=30,
            node_order=2,
            node_type="notify",
            approver_type="role",
            approver_id=None,
            condition_rules=json.dumps({"assignee_source": "role", "role": "finance"}),
        ),
    ]
    source_flow.nodes = source_nodes
    db = _fake_db(source_type, source_flow)
    request = ApprovalTemplateCopyRequest(name="Copied Expense", business_code="copied_expense")

    copied = await approval_service.copy_template_config(db, 10, request)

    copied_types = _added(db, ApprovalType)
    copied_fields = _added(db, ApprovalFormField)
    copied_flows = _added(db, ApprovalFlow)
    copied_nodes = _added(db, ApprovalNode)
    versions = _added(db, ApprovalTemplateVersion)
    assert copied is copied_types[0]
    assert len(copied_types) == 1
    assert copied_types[0].id != source_type.id
    assert copied_types[0].name == "Copied Expense"
    assert copied_types[0].business_code == "copied_expense"
    assert [field.id for field in copied_fields] != [field.id for field in source_fields]
    assert [field.code for field in copied_fields] == ["amount", "reviewer"]
    assert {field.approval_type_id for field in copied_fields} == {copied_types[0].id}
    assert len(copied_flows) == 1
    assert copied_flows[0].id != source_flow.id
    assert copied_flows[0].module == "copied_expense"
    assert [node.id for node in copied_nodes] != [node.id for node in source_nodes]
    assert [node.condition_rules for node in copied_nodes] == [node.condition_rules for node in source_nodes]
    assert len(versions) == 1
    assert versions[0].approval_type_id == copied_types[0].id
    copied_fields[0].label = "Mutated Copy Amount"
    copied_nodes[0].approver_id = 99
    assert source_fields[0].label == "Amount"
    assert source_nodes[0].approver_id == 9


def test_template_copy_route_requires_admin_or_hr():
    from app.api.v1.approval import router

    save_route = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == "/template-configs"
        and "POST" in route.methods
    )
    publish_route = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == "/template-configs/{approval_type_id}/publish"
        and "POST" in route.methods
    )
    delete_route = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == "/types/{approval_type_id}"
        and "DELETE" in route.methods
    )
    copy_route = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == "/types/{approval_type_id}/copy"
        and "POST" in route.methods
    )

    for route in (save_route, publish_route, delete_route, copy_route):
        dependency_roles = [_dependency_roles(dep.call) for dep in route.dependant.dependencies]
        assert {"admin", "hr"} in dependency_roles
