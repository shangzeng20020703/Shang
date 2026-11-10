"""
TDD red-phase tests for approval template organization selectors.

These tests are designed from docs/story-20260420-approval-organization.md.
They assert the backend contract and write-side side effects before production
code is changed for the story.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.approval import (
    ApprovalFlow,
    ApprovalFormField,
    ApprovalInstance,
    ApprovalNode,
    ApprovalTemplateVersion,
    ApprovalType,
)
from app.schemas.approval import ApprovalApplicationSubmit


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


def _fake_db():
    db = AsyncMock()
    added: list[object] = []
    next_ids = {
        ApprovalType: 100,
        ApprovalFlow: 200,
        ApprovalFormField: 300,
        ApprovalNode: 400,
        ApprovalTemplateVersion: 500,
        ApprovalInstance: 600,
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
    return db


def _valid_template_payload(*, fields: list[dict], flow_nodes: list[dict] | None = None):
    return SimpleNamespace(
        name="Organization Approval",
        business_code="approval_organization",
        category="general",
        scope="mobile",
        is_active=True,
        sort_order=1,
        icon="approval",
        description="Template that uses organization selectors",
        permission_rules={"visible_roles": ["employee"]},
        exception_rules={},
        auto_approval_rule=None,
        fields=fields,
        flow_nodes=flow_nodes
        if flow_nodes is not None
        else [
            {
                "node_order": 1,
                "node_type": "approval",
                "approver_type": "specific_user",
                "approver_id": 12,
            }
        ],
    )


def _organization_fields():
    return [
        SimpleNamespace(code="member_id", field_type="member", is_required=True, options_json=None),
        SimpleNamespace(code="department_id", field_type="department", is_required=True, options_json=None),
        SimpleNamespace(code="company", field_type="company", is_required=True, options_json=None),
        SimpleNamespace(code="separator", field_type="static_text", is_required=False, options_json=None),
    ]


class TestApprovalOrganizationTemplateSave:
    @pytest.mark.asyncio
    async def test_save_reordered_fields_persists_contiguous_order_from_array_order(self):
        import app.services.approval as approval_service

        db = _fake_db()
        payload = _valid_template_payload(
            fields=[
                {
                    "label": "Department",
                    "code": "department_id",
                    "field_type": "department",
                    "is_required": True,
                    "sort_order": 30,
                },
                {
                    "label": "Member",
                    "code": "member_id",
                    "field_type": "member",
                    "is_required": True,
                    "sort_order": 10,
                },
                {
                    "label": "Reason",
                    "code": "reason",
                    "field_type": "textarea",
                    "is_required": False,
                    "sort_order": 20,
                },
            ]
        )

        await approval_service.save_template_config(db, payload)

        saved_fields = [obj for obj in db.added if isinstance(obj, ApprovalFormField)]
        assert [field.code for field in saved_fields] == ["department_id", "member_id", "reason"]
        assert [field.sort_order for field in saved_fields] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_save_organization_controls_and_specific_approver_ids(self):
        import app.services.approval as approval_service

        db = _fake_db()
        payload = _valid_template_payload(
            fields=[
                {"label": "同行人", "code": "member_id", "field_type": "member", "is_required": True},
                {"label": "所属部门", "code": "department_id", "field_type": "department", "is_required": True},
                {"label": "所在公司", "code": "company", "field_type": "company", "is_required": True},
                {"label": "", "code": "separator", "field_type": "static_text", "is_required": False},
            ],
            flow_nodes=[
                {
                    "node_order": 1,
                    "node_type": "approval",
                    "approver_type": "specific_user",
                    "approver_id": 12,
                }
            ],
        )

        await approval_service.save_template_config(db, payload)

        saved_fields = [obj for obj in db.added if isinstance(obj, ApprovalFormField)]
        saved_nodes = [obj for obj in db.added if isinstance(obj, ApprovalNode)]
        assert {field.field_type for field in saved_fields} >= {"member", "department", "company", "static_text"}
        separator = next(field for field in saved_fields if field.field_type == "static_text")
        assert separator.is_required is False
        assert [node.approver_id for node in saved_nodes] == [12]

    @pytest.mark.parametrize("node_patch", [{"approver_id": None}, {"approver_id": ""}, {}])
    @pytest.mark.asyncio
    async def test_reject_specific_user_without_approver_before_writing_partial_rows(self, node_patch):
        import app.services.approval as approval_service

        db = _fake_db()
        node = {
            "node_order": 1,
            "node_type": "approval",
            "approver_type": "specific_user",
        }
        node.update(node_patch)
        payload = _valid_template_payload(
            fields=[{"label": "Reason", "code": "reason", "field_type": "textarea"}],
            flow_nodes=[node],
        )

        with pytest.raises(HTTPException) as exc_info:
            await approval_service.save_template_config(db, payload)

        assert exc_info.value.status_code == 400
        assert db.add.call_count == 0
        assert db.add_all.call_count == 0


class TestApprovalOrganizationSubmission:
    @pytest.mark.asyncio
    async def test_reject_missing_required_member_before_creating_instance(self):
        import app.services.approval as approval_service

        db = _fake_db()
        approval_type = SimpleNamespace(
            id=10,
            name="Organization Approval",
            business_code="approval_organization",
            is_active=True,
            form_fields=_organization_fields(),
            versions=[],
            version=1,
        )
        db.execute = AsyncMock(return_value=_result(approval_type))
        payload = ApprovalApplicationSubmit(
            approval_type_id=10,
            form_data={"department_id": 3, "company": 2},
            summary="Missing required member",
        )

        with pytest.raises(HTTPException) as exc_info:
            await approval_service.submit_dynamic_application(db, payload, applicant_id=9)

        assert exc_info.value.status_code == 400
        assert not any(isinstance(obj, ApprovalInstance) for obj in db.added)

    @pytest.mark.asyncio
    async def test_submit_member_and_department_ids_persists_instance_form_data(self):
        import app.services.approval as approval_service

        db = _fake_db()
        approval_type = SimpleNamespace(
            id=10,
            name="Organization Approval",
            business_code="approval_organization",
            is_active=True,
            form_fields=_organization_fields(),
            versions=[],
            version=1,
            form_fields_loaded=True,
        )
        flow = SimpleNamespace(
            id=20,
            name="Organization Approval Flow",
            module="approval_organization",
            is_active=True,
            nodes=[
                SimpleNamespace(
                    node_order=1,
                    node_type="approval",
                    approver_type="specific_user",
                    approver_id=12,
                    condition_rules=None,
                    auto_approve_hours=None,
                )
            ],
        )
        db.execute = AsyncMock(
            side_effect=[
                _result(approval_type),
                _result(None),
                _result(flow),
                _result([]),
            ]
        )
        payload = ApprovalApplicationSubmit(
            approval_type_id=10,
            form_data={"member_id": 12, "department_id": 3, "company": 2},
            summary="Organization-backed approval",
        )

        instance = await approval_service.submit_dynamic_application(db, payload, applicant_id=9)

        saved_instances = [obj for obj in db.added if isinstance(obj, ApprovalInstance)]
        assert saved_instances
        assert instance.form_data == {"member_id": 12, "department_id": 3, "company": 2}
        assert saved_instances[-1].form_data["member_id"] == 12
        assert saved_instances[-1].form_data["department_id"] == 3
        assert saved_instances[-1].form_data["company"] == 2
