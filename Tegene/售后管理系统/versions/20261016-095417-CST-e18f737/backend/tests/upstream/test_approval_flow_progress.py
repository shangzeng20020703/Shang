"""
Red-phase tests for approval flow preview and progress.

These tests are written from docs/story-20260421-approval-flow.md. They assert
the backend/mobile contract before production code is changed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


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
    assert callable(value), f"Missing required approval-flow API: {name}"
    return value


def _approval_type(type_id: int = 11, business_code: str = "mobile_trip"):
    return SimpleNamespace(
        id=type_id,
        name="Mobile Trip",
        business_code=business_code,
        category="general",
        scope="mobile",
        is_active=True,
    )


def _node(
    order: int,
    *,
    approver_type: str = "specific_user",
    approver_id: int | None = 99,
    node_type: str = "approval",
    node_name: str | None = None,
):
    return SimpleNamespace(
        node_order=order,
        node_name=node_name or f"节点 {order}",
        approver_type=approver_type,
        approver_id=approver_id,
        node_type=node_type,
        condition_rules=None,
        auto_approve_hours=None,
    )


def _flow(nodes: list[object], *, flow_id: int = 21, module: str = "mobile_trip"):
    return SimpleNamespace(
        id=flow_id,
        name="Mobile Trip Flow",
        module=module,
        is_active=True,
        nodes=nodes,
    )


def _snapshot(nodes: list[dict]):
    return json.dumps(
        {
            "flow_id": 21,
            "flow_name": "Original Snapshot Flow",
            "module": "mobile_trip",
            "nodes": nodes,
        },
        ensure_ascii=False,
    )


def _read_source(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


class TestApprovalFlowStructuralContract:
    def test_backend_contract_exports_required_schemas_services_and_route(self):
        import app.api.v1.approval as approval_api
        import app.schemas.approval as approval_schemas
        import app.services.approval as approval_service

        _require_attr(approval_service, "build_template_flow_preview")
        _require_attr(approval_service, "build_instance_progress")

        node_schema = getattr(approval_schemas, "ApprovalFlowNodePreview", None)
        preview_schema = getattr(approval_schemas, "ApprovalFlowPreviewOut", None)
        assert node_schema is not None, "ApprovalFlowNodePreview schema is required"
        assert preview_schema is not None, "ApprovalFlowPreviewOut schema is required"

        node_fields = set(node_schema.model_fields)
        assert {
            "node_order",
            "node_name",
            "node_type",
            "node_type_label",
            "approver_type",
            "approver_id",
            "approver_name",
            "approver_display",
            "is_resolved",
            "resolve_message",
            "status",
            "status_label",
            "action",
            "comment",
            "acted_at",
            "arrived_at",
            "approver_department_name",
        } <= node_fields

        preview_fields = set(preview_schema.model_fields)
        assert {"approval_type_id", "flow_id", "flow_name", "has_flow", "message", "nodes"} <= preview_fields
        assert "progress_nodes" in approval_schemas.ApprovalInstanceDetail.model_fields

        route_paths = {route.path for route in approval_api.router.routes}
        assert "/types/{approval_type_id}/flow-preview" in route_paths

    def test_mobile_sources_render_preview_and_progress_contracts(self):
        start_source = _read_source("mobile/src/pages/ApprovalStartPage.vue")
        detail_source = _read_source("mobile/src/pages/ApprovalDetailPage.vue")

        assert "审批流程" in start_source
        assert "/approval/types/" in start_source and "flow-preview" in start_source
        assert "selectTemplate" in start_source and "flowPreview" in start_source

        assert "审批进度" in detail_source
        assert "progress_nodes" in detail_source


class TestTemplateFlowPreview:
    @pytest.mark.asyncio
    async def test_preview_returns_ordered_nodes_with_display_labels(self):
        import app.services.approval as approval_service

        build_template_flow_preview = _require_attr(approval_service, "build_template_flow_preview")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(
                    _flow(
                        [
                            _node(2, approver_type="hr", approver_id=None, node_name="HR 复核"),
                            _node(1, approver_type="specific_user", approver_id=77, node_name="主管审批"),
                        ]
                    )
                ),
            ]
        )

        preview = await build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        assert preview.approval_type_id == 11
        assert preview.has_flow is True
        assert preview.flow_id == 21
        assert [node.node_order for node in preview.nodes] == [1, 2]
        assert [node.node_name for node in preview.nodes] == ["主管审批", "HR 复核"]
        assert all(node.node_type_label for node in preview.nodes)
        assert all(node.approver_display for node in preview.nodes)
        assert all(node.status == "preview" for node in preview.nodes)

    @pytest.mark.asyncio
    async def test_missing_flow_returns_clear_message_without_nodes(self):
        import app.services.approval as approval_service

        build_template_flow_preview = _require_attr(approval_service, "build_template_flow_preview")

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_result(_approval_type()), _result(None)])

        preview = await build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        assert preview.has_flow is False
        assert preview.nodes == []
        assert preview.message == "当前模板未配置审批流程，请联系 HR 或管理员"

    @pytest.mark.asyncio
    async def test_unresolved_approver_returns_reason_never_blank_display(self, monkeypatch):
        import app.services.approval as approval_service

        build_template_flow_preview = _require_attr(approval_service, "build_template_flow_preview")
        monkeypatch.setattr(approval_service, "resolve_approver_id", AsyncMock(return_value=None), raising=False)

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(_flow([_node(1, approver_type="direct_manager", approver_id=None, node_name="直属上级审批")])),
            ]
        )

        preview = await build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        assert preview.has_flow is True
        assert len(preview.nodes) == 1
        node = preview.nodes[0]
        assert node.is_resolved is False
        assert node.approver_display
        assert node.resolve_message in {
            "未找到直属上级",
            "未配置部门负责人",
            "未找到 HR 审批人",
            "提交后自动匹配",
        }


class TestInstanceProgress:
    @pytest.mark.asyncio
    async def test_progress_contains_completed_pending_and_future_snapshot_nodes(self):
        import app.services.approval as approval_service

        build_instance_progress = _require_attr(approval_service, "build_instance_progress")
        approved_at = datetime(2026, 4, 21, 9, 30, tzinfo=timezone.utc)
        pending_arrived_at = datetime(2026, 4, 21, 9, 45, tzinfo=timezone.utc)
        instance = SimpleNamespace(
            id=31,
            status="pending",
            current_node_order=2,
            flow_id=21,
            applicant_id=9,
            flow_snapshot=_snapshot(
                [
                    {"node_order": 1, "node_name": "直属上级审批", "node_type": "approval", "approver_type": "direct_manager"},
                    {"node_order": 2, "node_name": "HR 复核", "node_type": "approval", "approver_type": "hr"},
                    {"node_order": 3, "node_name": "总经理审批", "node_type": "approval", "approver_type": "gm"},
                ]
            ),
            records=[
                SimpleNamespace(
                    node_order=1,
                    approver_id=77,
                    action="approve",
                    comment="同意",
                    acted_at=approved_at,
                )
            ],
            tasks=[
                SimpleNamespace(
                    node_order=2,
                    approver_id=88,
                    status="pending",
                    created_at=pending_arrived_at,
                    completed_at=None,
                )
            ],
        )

        nodes = await build_instance_progress(AsyncMock(), instance)

        assert [node.node_order for node in nodes] == [1, 2, 3]
        assert [node.status for node in nodes] == ["completed", "pending", "not_started"]
        assert nodes[0].approver_id == 77
        assert nodes[0].action == "approve"
        assert nodes[0].comment == "同意"
        assert nodes[0].acted_at == approved_at
        assert nodes[1].approver_id == 88
        assert nodes[1].arrived_at == pending_arrived_at

    @pytest.mark.asyncio
    async def test_progress_prefers_historical_snapshot_over_current_flow_nodes(self):
        import app.services.approval as approval_service

        build_instance_progress = _require_attr(approval_service, "build_instance_progress")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(SimpleNamespace(id=77, name="当前审批人", is_active=True)))
        instance = SimpleNamespace(
            id=32,
            status="pending",
            current_node_order=1,
            flow_id=21,
            applicant_id=9,
            flow_snapshot=_snapshot(
                [
                    {"node_order": 1, "node_name": "Original Manager", "node_type": "approval", "approver_type": "direct_manager"},
                    {"node_order": 2, "node_name": "Original HR", "node_type": "approval", "approver_type": "hr"},
                ]
            ),
            records=[],
            tasks=[SimpleNamespace(node_order=1, approver_id=77, status="pending", completed_at=None)],
        )

        nodes = await build_instance_progress(db, instance)

        assert [node.node_name for node in nodes] == ["Original Manager", "Original HR"]
        assert all(node.node_name != "Edited Node" for node in nodes)
        assert db.execute.call_count == 1, "snapshot progress may resolve approver names but must not read edited template nodes"

    @pytest.mark.asyncio
    async def test_rejected_progress_keeps_reject_details_and_terminates_later_nodes(self):
        import app.services.approval as approval_service

        build_instance_progress = _require_attr(approval_service, "build_instance_progress")
        rejected_at = datetime(2026, 4, 21, 10, 15, tzinfo=timezone.utc)
        instance = SimpleNamespace(
            id=33,
            status="rejected",
            current_node_order=2,
            flow_id=21,
            applicant_id=9,
            flow_snapshot=_snapshot(
                [
                    {"node_order": 1, "node_name": "主管审批", "node_type": "approval", "approver_type": "direct_manager"},
                    {"node_order": 2, "node_name": "HR 复核", "node_type": "approval", "approver_type": "hr"},
                    {"node_order": 3, "node_name": "总经理审批", "node_type": "approval", "approver_type": "gm"},
                ]
            ),
            records=[
                SimpleNamespace(node_order=1, approver_id=77, action="approve", comment="同意", acted_at=rejected_at),
                SimpleNamespace(node_order=2, approver_id=88, action="reject", comment="资料不完整", acted_at=rejected_at),
            ],
            tasks=[],
        )

        nodes = await build_instance_progress(AsyncMock(), instance)

        assert [node.status for node in nodes] == ["completed", "rejected", "skipped"]
        assert nodes[1].approver_id == 88
        assert nodes[1].action == "reject"
        assert nodes[1].comment == "资料不完整"
        assert nodes[1].acted_at == rejected_at
        assert nodes[1].status_label == "已拒绝"
