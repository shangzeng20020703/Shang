"""
Configurable approval platform red-phase tests.

These tests are written from the story acceptance criteria and intentionally
expect behavior that is not yet implemented in the current backend.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.leave import HalfDay


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = value if isinstance(value, list) else []
    result.all.return_value = value if isinstance(value, list) else []
    return result


def _require_attr(module, name: str):
    value = getattr(module, name, None)
    assert callable(value), f"Missing required approval-platform API: {name}"
    return value


def _make_flow(flow_id: int = 101, category: str = "leave", node_count: int = 1):
    flow = MagicMock()
    flow.id = flow_id
    flow.name = "Configurable Leave Flow"
    flow.module = category
    flow.is_active = True
    flow.nodes = [_make_node(i + 1) for i in range(node_count)]
    return flow


def _make_node(order: int, approver_type: str = "specific_user", approver_id: int = 99):
    node = MagicMock()
    node.node_order = order
    node.approver_type = approver_type
    node.approver_id = approver_id
    node.node_type = "approval"
    return node


def _make_submission_data():
    return SimpleNamespace(
        module="leave",
        business_id=9001,
        business_type="annual_leave",
    )


def _make_leave_request_data():
    return SimpleNamespace(
        leave_type_id=1,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 2),
        start_half=HalfDay.am,
        end_half=HalfDay.pm,
        reason="medical leave",
        proof_url=None,
    )


def _added_class_names(db):
    names = []
    for call in db.add.call_args_list:
        obj = call.args[0]
        names.append(obj.__class__.__name__)
    return names


class TestApprovalPlatformTemplates:
    @pytest.mark.asyncio
    async def test_enabled_templates_hide_disabled_types_and_group_by_category(self):
        import app.models.approval as approval_models
        import app.services.approval as approval_service

        assert getattr(approval_models, "ApprovalType", None) is not None, (
            "ApprovalType model is required for configurable approval templates"
        )
        assert getattr(approval_models, "ApprovalFormField", None) is not None, (
            "ApprovalFormField model is required for dynamic approval forms"
        )

        list_approval_templates = _require_attr(approval_service, "list_approval_templates")

        active = SimpleNamespace(
            id=1,
            name="Annual Leave",
            business_code="annual_leave",
            category="leave",
            scope="mobile",
            is_active=True,
            sort_order=10,
            icon="calendar",
            description="Annual leave",
        )
        inactive = SimpleNamespace(
            id=2,
            name="Outdated Leave",
            business_code="outdated_leave",
            category="leave",
            scope="mobile",
            is_active=False,
            sort_order=20,
            icon="calendar",
            description="Disabled template",
        )

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result([active, inactive]))
        current_user = SimpleNamespace(id=7, is_superuser=True)

        groups = await list_approval_templates(db, current_user)

        flattened = []
        for group in groups:
            if isinstance(group, dict):
                category = group.get("category")
                items = group.get("templates") or group.get("items") or []
                flattened.extend((category, item) for item in items)
            else:
                category = getattr(group, "category", None)
                items = getattr(group, "templates", None) or getattr(group, "items", None) or []
                flattened.extend((category, item) for item in items)

        codes = {getattr(item, "business_code", getattr(item, "code", None)) for _, item in flattened}
        categories = {category for category, _ in flattened}

        assert "outdated_leave" not in codes
        assert "annual_leave" in codes
        assert categories == {"leave"}


class TestApprovalPlatformValidation:
    @pytest.mark.asyncio
    async def test_dynamic_field_validation_rejects_missing_required_fields(self):
        import app.services.approval as approval_service

        validate_form_payload = _require_attr(approval_service, "validate_form_payload")

        fields = [
            {"code": "reason", "field_type": "text", "is_required": True},
            {"code": "days", "field_type": "number", "is_required": True},
        ]
        payload = {"days": 2}

        with pytest.raises(HTTPException) as exc_info:
            result = validate_form_payload(fields, payload)
            if hasattr(result, "__await__"):
                await result
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_dynamic_field_validation_rejects_type_and_option_errors(self):
        import app.services.approval as approval_service

        validate_form_payload = _require_attr(approval_service, "validate_form_payload")

        fields = [
            {"code": "days", "field_type": "number", "is_required": True},
            {
                "code": "shift",
                "field_type": "select",
                "is_required": True,
                "options_json": ["morning", "afternoon"],
            },
        ]
        payload = {"days": "abc", "shift": "night"}

        with pytest.raises(HTTPException) as exc_info:
            result = validate_form_payload(fields, payload)
            if hasattr(result, "__await__"):
                await result
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_punch_correction_slot_accepts_dynamic_attendance_option(self):
        import app.services.approval as approval_service

        validate_form_payload = _require_attr(approval_service, "validate_form_payload")

        fields = [
            {"code": "date_1", "label": "补卡日期", "field_type": "date", "is_required": True},
            {
                "code": "select_2",
                "label": "补卡班次",
                "field_type": "select",
                "is_required": True,
                "options_json": {"options": ["选项1", "选项2"]},
            },
            {"code": "datetime_3", "label": "补卡时间", "field_type": "datetime", "is_required": True},
            {"code": "textarea_4", "label": "补卡事由", "field_type": "textarea", "is_required": True},
        ]
        payload = {
            "date_1": "2026-04-24",
            "select_2": "工作时段1 · 下班卡",
            "datetime_3": "2026-04-24T18:30",
            "textarea_4": "忘打卡",
            "punch_type": "check_out",
            "correction_date": "2026-04-24",
        }

        result = validate_form_payload(fields, payload, business_code="punch_correction")
        if hasattr(result, "__await__"):
            await result

    @pytest.mark.asyncio
    async def test_non_punch_select_still_rejects_unknown_option(self):
        import app.services.approval as approval_service

        validate_form_payload = _require_attr(approval_service, "validate_form_payload")

        fields = [
            {
                "code": "select_2",
                "label": "普通选项",
                "field_type": "select",
                "is_required": True,
                "options_json": {"options": ["选项1", "选项2"]},
            }
        ]

        with pytest.raises(HTTPException) as exc_info:
            result = validate_form_payload(
                fields,
                {"select_2": "工作时段1 · 下班卡"},
                business_code="custom",
            )
            if hasattr(result, "__await__"):
                await result
        assert exc_info.value.status_code == 400


class TestConfiguredSubmission:
    @pytest.mark.asyncio
    async def test_configured_submission_creates_snapshot_task_and_record_state(self):
        import app.services.approval as approval_service

        create_instance = _require_attr(approval_service, "create_instance")

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(_make_flow(node_count=2)))
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        instance = await create_instance(db, _make_submission_data(), applicant_id=7)

        assert instance.status == "pending"
        assert instance.current_node_order == 1
        assert instance.flow_snapshot is not None, "approval flow snapshot must be stored durably"
        assert getattr(instance, "tasks", None), "first pending approval task must be created"
        assert len(_added_class_names(db)) >= 2, "instance creation should persist more than the business row"


class TestApprovalProcessing:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "action,expected_status",
        [
            ("approve", "approved"),
            ("reject", "rejected"),
        ],
    )
    async def test_approve_or_reject_completes_task_and_writes_record(self, action, expected_status):
        import app.services.approval as approval_service
        from app.schemas.approval import ApprovalActionRequest

        process_approval = _require_attr(approval_service, "process_approval")

        pending_task = SimpleNamespace(
            status="pending",
            completed_at=None,
            approver_id=10,
            node_order=1,
        )
        instance = MagicMock()
        instance.status = "pending"
        instance.current_node_order = 1
        instance.flow_id = 1
        instance.id = 11
        instance.applicant_id = 5
        instance.records = []
        instance.tasks = [pending_task]

        flow = _make_flow(flow_id=1, node_count=1)

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(instance),
                _result(flow),
                _result(None),
                _result(None),
                _result(None),
            ]
        )
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        data = ApprovalActionRequest(action=action, comment="looks good" if action == "approve" else "not acceptable")
        result = await process_approval(db, 1, approver_id=10, data=data)

        assert result.status == expected_status
        assert pending_task.status == "completed"
        assert pending_task.completed_at is not None

        record_objects = [call.args[0] for call in db.add.call_args_list]
        actions = [getattr(obj, "action", None) for obj in record_objects]
        assert action in actions


class TestSubmittedDetail:
    @pytest.mark.asyncio
    async def test_submitted_detail_exposes_business_summary_and_record_trail(self):
        from app.api.v1.approval import get_instance as get_instance_route

        record = SimpleNamespace(
            id=77,
            instance_id=11,
            node_order=1,
            approver_id=10,
            action="approve",
            comment="approved",
            acted_at=datetime(2026, 4, 2, 9, 30, tzinfo=timezone.utc),
            created_at=datetime(2026, 4, 2, 9, 30, tzinfo=timezone.utc),
        )
        instance = MagicMock()
        instance.id = 11
        instance.flow_id = 1
        instance.applicant_id = 5
        instance.module = "leave"
        instance.business_id = 9001
        instance.business_type = "annual_leave"
        instance.status = "approved"
        instance.current_node_order = 2
        instance.created_at = datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc)
        instance.updated_at = datetime(2026, 4, 2, 9, 0, tzinfo=timezone.utc)
        instance.records = [record]
        instance.summary = "Annual leave"
        instance.form_data = {"days": 2, "reason": "medical leave"}

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(instance))

        detail = await get_instance_route(11, db=db, current_user=SimpleNamespace(id=7, is_superuser=True))

        assert detail.status == "approved"
        assert len(detail.records) == 1
        assert detail.records[0].action == "approve"
        assert detail.records[0].comment == "approved"
        assert hasattr(detail, "summary"), "submitted detail should expose a business summary"
        assert hasattr(detail, "form_data"), "submitted detail should expose dynamic form data"


class TestLeaveAndOvertimeReuse:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["leave", "overtime"])
    async def test_leave_and_overtime_reuse_common_engine_artifacts(self, path):
        import app.services.approval as approval_service

        maybe_create_approval_instance = _require_attr(approval_service, "maybe_create_approval_instance")
        flow = _make_flow(category=path, node_count=1)

        if path == "leave":
            from app.services.leave import LeaveRequestService

            leave_type = SimpleNamespace(
                id=1,
                name="Annual Leave",
                code="annual",
                is_active=True,
                max_days_per_year=None,
                requires_proof=False,
            )
            db = AsyncMock()
            employee = SimpleNamespace(
                id=7,
                hire_date=date(2026, 1, 1),
                department_id=None,
                location_id=None,
            )

            def execute_side_effect(stmt, *args, **kwargs):
                query = str(stmt).lower()
                if "employees" in query:
                    return _result(employee)
                if "attendance_rules" in query or "approval_tasks" in query:
                    return _result([])
                return _result(flow)

            db.execute = AsyncMock(side_effect=execute_side_effect)
            db.add = MagicMock()
            db.flush = AsyncMock()
            db.refresh = AsyncMock()
            with patch("app.services.leave.LeaveTypeService.get", new=AsyncMock(return_value=leave_type)):
                with patch("app.services.leave.maybe_create_approval_instance", new=maybe_create_approval_instance):
                    await LeaveRequestService.create(db, 7, _make_leave_request_data())
        else:
            from app.api.v1.overtime import OvertimeCreateRequest, create_overtime

            db = AsyncMock()
            db.execute = AsyncMock(return_value=_result(flow))
            db.add = MagicMock()
            db.flush = AsyncMock()
            db.refresh = AsyncMock()
            current_user = SimpleNamespace(id=7, is_superuser=False)
            data = OvertimeCreateRequest(
                overtime_date=date(2026, 4, 1),
                start_time="18:00",
                end_time="21:00",
                hours=3,
                overtime_type="weekday",
                reason="month end support",
            )
            with patch("app.api.v1.overtime.maybe_create_approval_instance", new=maybe_create_approval_instance):
                await create_overtime(data, db=db, current_user=current_user)

        added_names = _added_class_names(db)
        assert any(name == "ApprovalInstance" for name in added_names)
        assert any(name == "ApprovalTask" for name in added_names), (
            "leave and overtime submissions should create shared approval tasks"
        )
        assert any(name == "ApprovalRecord" for name in added_names), (
            "leave and overtime submissions should write shared approval records"
        )
