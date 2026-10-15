"""
Red-phase coverage for the full approval-template PRD.

These tests are intentionally written against the story acceptance criteria.
They should fail until the backend grows the template configuration, versioning,
visibility, submission, and audit behavior described in the PRD.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar.return_value = value
    if isinstance(value, list):
        result.scalars.return_value.all.return_value = value
        result.all.return_value = [(item,) for item in value]
    else:
        result.scalars.return_value.all.return_value = []
        result.all.return_value = []
    return result


def _require_attr(module, name: str):
    value = getattr(module, name, None)
    assert callable(value), f"Missing required approval-template API: {name}"
    return value


def _template(
    *,
    template_id: int,
    business_code: str,
    category: str = "leave",
    is_active: bool = True,
    permission_rules: dict | None = None,
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
        created_at=datetime.now(timezone.utc),
        form_fields=[],
    )
    if permission_rules is not None:
        template.permission_rules = permission_rules
    return template


def _flow_snapshot(node_orders: list[tuple[int, str, str | None]]):
    return json.dumps(
        {
            "flow_id": 501,
            "flow_name": "Approval Template Flow",
            "module": "approval_template",
            "nodes": [
                {
                    "node_order": order,
                    "approver_type": approver_type,
                    "approver_id": approver_id,
                    "node_type": "approval" if approver_type != "auto" else "auto",
                }
                for order, approver_type, approver_id in node_orders
            ],
        },
        ensure_ascii=False,
    )


class TestApprovalTemplateFullContract:
    @pytest.mark.asyncio
    async def test_full_template_config_contract_exposes_versioned_save_api_and_model(self):
        import app.models.approval as approval_models
        import app.services.approval as approval_service

        assert getattr(approval_models, "ApprovalTemplateVersion", None) is not None, (
            "ApprovalTemplateVersion model is required for durable template publishing"
        )
        _require_attr(approval_service, "save_template_config")
        _require_attr(approval_service, "publish_template")

    @pytest.mark.asyncio
    async def test_reject_invalid_template_config_without_leaving_partial_rows(self):
        import app.services.approval as approval_service

        save_template_config = _require_attr(approval_service, "save_template_config")

        db = AsyncMock()
        db.add = MagicMock()
        db.add_all = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        invalid_config = SimpleNamespace(
            name="",
            business_code="approval-template",
            category="leave",
            scope="mobile",
            is_active=True,
            sort_order=1,
            icon="calendar",
            description="",
            permission_rules={"visible_roles": ["hr"]},
            exception_rules=[],
            auto_approval_rule=None,
            fields=[
                {"label": "Reason", "code": "reason", "field_type": "text"},
                {"label": "Reason again", "code": "reason", "field_type": "text"},
            ],
            flow_nodes=[],
        )

        with pytest.raises(HTTPException) as exc_info:
            await save_template_config(db, invalid_config)

        assert exc_info.value.status_code == 400
        assert db.add.call_count == 0
        assert db.add_all.call_count == 0

    @pytest.mark.asyncio
    async def test_employee_template_visibility_filters_disabled_and_hidden_templates(self):
        import app.services.approval as approval_service

        list_approval_templates = _require_attr(approval_service, "list_approval_templates")

        visible = _template(template_id=1, business_code="annual_leave")
        hidden = _template(
            template_id=2,
            business_code="salary_adjust",
            permission_rules={"visible_roles": ["hr"]},
        )
        disabled = _template(template_id=3, business_code="obsolete_leave", is_active=False)

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result([visible, hidden, disabled]))
        employee = SimpleNamespace(id=9, roles=["employee"], is_superuser=False)

        groups = await list_approval_templates(db, employee)

        flattened = []
        for group in groups:
            items = getattr(group, "templates", None) or getattr(group, "items", None) or []
            flattened.extend(items)

        business_codes = {getattr(item, "business_code", None) for item in flattened}

        assert "annual_leave" in business_codes
        assert "obsolete_leave" not in business_codes
        assert "salary_adjust" not in business_codes

    @pytest.mark.asyncio
    async def test_dynamic_submission_binds_template_version_and_persists_snapshot(self):
        import app.services.approval as approval_service
        from app.schemas.approval import ApprovalApplicationSubmit

        submit_dynamic_application = _require_attr(approval_service, "submit_dynamic_application")

        approval_type = _template(template_id=11, business_code="approval_template")
        approval_type.form_fields = [
            SimpleNamespace(code="reason", field_type="text", is_required=True, options_json=None)
        ]
        flow = SimpleNamespace(
            id=501,
            name="Template Flow",
            module="approval_template",
            nodes=[SimpleNamespace(node_order=1, approver_type="specific_user", approver_id=77, node_type="approval")],
        )

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_result(approval_type), _result(None), _result(flow), _result([])])
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        payload = ApprovalApplicationSubmit(
            approval_type_id=11,
            form_data={"reason": "template rollout"},
            summary="Template rollout",
        )

        instance = await submit_dynamic_application(db, payload, applicant_id=99)

        assert instance.status == "pending"
        assert instance.flow_snapshot is not None
        assert getattr(instance, "template_version_id", None) is not None, (
            "dynamic submissions should bind to a template version"
        )

    @pytest.mark.asyncio
    async def test_process_approval_fires_auto_rules_and_writes_audit_history(self):
        import app.services.approval as approval_service
        from app.schemas.approval import ApprovalActionRequest

        process_approval = _require_attr(approval_service, "process_approval")

        pending_task = SimpleNamespace(
            status="pending",
            completed_at=None,
            approver_id=77,
            node_order=1,
        )
        instance = SimpleNamespace(
            id=41,
            status="pending",
            current_node_order=1,
            flow_id=501,
            applicant_id=7,
            flow_snapshot=_flow_snapshot(
                [
                    (1, "specific_user", "77"),
                    (2, "auto", None),
                ]
            ),
            tasks=[pending_task],
            records=[],
        )

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(instance))
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        result = await process_approval(
            db,
            instance_id=41,
            approver_id=77,
            data=ApprovalActionRequest(action="approve", comment="ok"),
        )

        actions = [getattr(call.args[0], "action", None) for call in db.add.call_args_list]
        pending_statuses = [getattr(task, "status", None) for task in result.tasks]

        assert result.status == "approved"
        assert pending_task.status == "completed"
        assert "auto_approve" in actions or "auto_skip" in actions, (
            "auto rules should write an immutable auto audit record"
        )
        assert "pending" not in pending_statuses, "auto rules should not leave a pending task behind"

    @pytest.mark.asyncio
    async def test_published_version_two_keeps_version_one_history_stable(self):
        import app.models.approval as approval_models
        import app.services.approval as approval_service

        assert getattr(approval_models, "ApprovalTemplateVersion", None) is not None, (
            "ApprovalTemplateVersion model is required for historical stability"
        )
        publish_template = _require_attr(approval_service, "publish_template")

        db = AsyncMock()
        db.execute = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with pytest.raises(HTTPException):
            await publish_template(db, approval_type_id=1)

    @pytest.mark.asyncio
    async def test_disabled_template_submission_is_rejected_but_history_stays_readable(self):
        import app.services.approval as approval_service
        from app.schemas.approval import ApprovalApplicationSubmit

        submit_dynamic_application = _require_attr(approval_service, "submit_dynamic_application")
        get_instance = _require_attr(approval_service, "get_instance")

        disabled_type = _template(template_id=21, business_code="approval_template", is_active=False)
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(disabled_type))
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await submit_dynamic_application(
                db,
                ApprovalApplicationSubmit(
                    approval_type_id=21,
                    form_data={"reason": "should fail"},
                    summary="Disabled template",
                ),
                applicant_id=99,
            )
        assert exc_info.value.status_code == 400

        history_instance = SimpleNamespace(
            id=901,
            summary="Historical detail",
            form_data={"reason": "old submission"},
            flow_snapshot=_flow_snapshot([(1, "specific_user", "77")]),
            records=[],
            tasks=[],
        )
        db.execute = AsyncMock(return_value=_result(history_instance))

        detail = await get_instance(db, 901)
        assert detail.form_data == {"reason": "old submission"}
        assert detail.flow_snapshot is not None
        assert getattr(detail, "template_version_id", None) is not None, (
            "historical detail should remain tied to the original template version"
        )
