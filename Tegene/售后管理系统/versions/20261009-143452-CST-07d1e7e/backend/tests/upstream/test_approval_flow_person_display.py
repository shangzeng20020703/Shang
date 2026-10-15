"""
Requirement tests for mobile approval flow person display.

These tests are written from docs/story-20260421-approval-fix.md. They assert
that backend preview/progress responses expose concrete employee names instead
of bare IDs, and that mobile pages continue rendering the returned display text.
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


def _row_result(*values):
    result = MagicMock()
    result.first.return_value = values
    result.scalar_one_or_none.return_value = values[0] if values else None
    result.scalar.return_value = values[0] if values else None
    result.scalars.return_value.all.return_value = []
    result.all.return_value = []
    return result


def _employee(employee_id: int, name: str, **extra):
    return SimpleNamespace(id=employee_id, name=name, is_active=True, **extra)


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
    approver_type: str,
    approver_id: int | None = None,
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
            "flow_name": "Mobile Trip Flow",
            "module": "mobile_trip",
            "nodes": nodes,
        },
        ensure_ascii=False,
    )


def _read_source(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


class TestApprovalFlowPersonPreview:
    @pytest.mark.asyncio
    async def test_notify_specific_user_preview_returns_employee_name(self):
        import app.services.approval as approval_service

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(
                    _flow(
                        [
                            _node(
                                1,
                                node_type="notify",
                                approver_type="specific_user",
                                approver_id=77,
                                node_name="抄送 HR",
                            )
                        ]
                    )
                ),
                _result(_employee(77, "测试移动端HR")),
            ]
        )

        preview = await approval_service.build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        node = preview.nodes[0]
        assert node.approver_id == 77
        assert node.approver_name == "测试移动端HR"
        assert "测试移动端HR" in node.approver_display
        assert node.approver_display != "抄送人"
        assert "员工 #77" not in node.approver_display

    @pytest.mark.asyncio
    async def test_approval_specific_user_preview_returns_employee_name_not_bare_id(self):
        import app.services.approval as approval_service

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(
                    _flow(
                        [
                            _node(
                                1,
                                approver_type="specific_user",
                                approver_id=88,
                                node_name="指定审批人",
                            )
                        ]
                    )
                ),
                _result(_employee(88, "指定审批人张三")),
            ]
        )

        preview = await approval_service.build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        node = preview.nodes[0]
        assert node.approver_id == 88
        assert node.approver_name == "指定审批人张三"
        assert "指定审批人张三" in node.approver_display
        assert "员工 #88" not in node.approver_display

    @pytest.mark.asyncio
    async def test_direct_manager_preview_returns_applicant_manager_name(self):
        import app.services.approval as approval_service

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(_flow([_node(1, approver_type="direct_manager", node_name="直属上级审批")])),
                _result(98),
                _result(_employee(98, "直属主管")),
            ]
        )

        preview = await approval_service.build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        node = preview.nodes[0]
        assert node.approver_id == 98
        assert node.approver_name == "直属主管"
        assert "直属主管" in node.approver_display

    @pytest.mark.asyncio
    async def test_missing_direct_manager_preview_returns_clear_reason(self):
        import app.services.approval as approval_service

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(_flow([_node(1, approver_type="direct_manager", node_name="直属上级审批")])),
                _result(None),
            ]
        )

        preview = await approval_service.build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        node = preview.nodes[0]
        assert node.approver_id is None
        assert node.approver_name is None
        assert node.is_resolved is False
        assert node.resolve_message == "未找到直属上级"
        assert node.approver_display == "未找到直属上级"

    @pytest.mark.asyncio
    async def test_multi_level_manager_preview_resolves_first_level_manager_name(self):
        import app.services.approval as approval_service

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(_flow([_node(1, approver_type="multi_level_manager", node_name="多级主管审批")])),
                _result(10),
                _row_result(66, None, False),
                _result(_employee(66, "一级上级")),
            ]
        )

        preview = await approval_service.build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        node = preview.nodes[0]
        assert node.approver_id == 66
        assert node.approver_name == "一级上级"
        assert "一级上级" in node.approver_display
        assert node.approver_display != "提交后自动匹配"

    @pytest.mark.asyncio
    async def test_multi_level_manager_preview_displays_configured_manager_chain(self):
        import app.services.approval as approval_service

        flow_node = _node(1, approver_type="multi_level_manager", node_name="连续多级主管审批")
        flow_node.condition_rules = json.dumps(
            {
                "assignee_source": "multi_level_manager",
                "manager_level": 2,
                "approval_mode": "sequential",
            }
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(_flow([flow_node])),
                _result(10),
                _row_result(66, 20, False),
                _row_result(77, None, False),
                _result(_employee(66, "一级上级")),
                _result(_employee(77, "二级上级")),
            ]
        )

        preview = await approval_service.build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        node = preview.nodes[0]
        assert node.approver_id == 66
        assert node.approver_name == "一级上级、二级上级"
        assert node.approver_display == "一级上级、二级上级"
        assert node.approval_mode == "sequential"

    @pytest.mark.asyncio
    async def test_multi_level_manager_member_endpoint_resolves_until_selected_manager(self):
        import app.services.approval as approval_service

        flow_node = _node(1, approver_type="multi_level_manager", node_name="连续多级主管审批")
        flow_node.condition_rules = json.dumps(
            {
                "assignee_source": "multi_level_manager",
                "multi_level_end_type": "member",
                "multi_level_member_ids": [77],
                "approval_mode": "sequential",
            }
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(_flow([flow_node])),
                _result(10),
                _row_result(66, 20, False),
                _row_result(77, None, False),
                _result(_employee(66, "一级上级")),
                _result(_employee(77, "二级上级")),
            ]
        )

        preview = await approval_service.build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        node = preview.nodes[0]
        assert node.approver_id == 66
        assert node.approver_name == "一级上级、二级上级"
        assert node.approver_display == "一级上级、二级上级"

    @pytest.mark.asyncio
    async def test_multi_level_manager_uses_department_manager_line_not_cross_department_direct_chain(self):
        import app.services.approval as approval_service

        flow_node = _node(1, approver_type="multi_level_manager", node_name="连续多级主管审批")
        flow_node.condition_rules = json.dumps(
            {
                "assignee_source": "multi_level_manager",
                "multi_level_level": "highest",
                "approval_mode": "sequential",
            }
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(_flow([flow_node])),
                _result(10),
                _row_result(66, 20, False),
                _row_result(77, 30, False),
                _row_result(88, None, True),
                _result(_employee(66, "本部门主管")),
                _result(_employee(77, "上级部门主管")),
            ]
        )

        preview = await approval_service.build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        node = preview.nodes[0]
        assert node.approver_id == 66
        assert node.approver_display == "本部门主管、上级部门主管"
        assert "其他业务部主管" not in node.approver_display
        assert "管理员" not in node.approver_display

    @pytest.mark.asyncio
    async def test_notify_preview_combines_configured_sources(self):
        import app.services.approval as approval_service

        flow_node = _node(1, approver_type="specific_user", node_type="notify", node_name="抄送人事")
        flow_node.condition_rules = json.dumps(
            {
                "assignee_source": "specific_user",
                "notify_sources": ["specific_user", "applicant_self"],
                "member_ids": [88],
            }
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(_flow([flow_node])),
                _result(_employee(88, "固定抄送人")),
                _result(_employee(9, "发起人")),
            ]
        )

        preview = await approval_service.build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        node = preview.nodes[0]
        assert node.node_type == "notify"
        assert node.approver_id == 88
        assert node.approver_name == "固定抄送人"
        assert node.approver_display == "固定抄送人"

    @pytest.mark.asyncio
    async def test_notify_preview_hides_default_applicant_self_only(self):
        import app.services.approval as approval_service

        flow_node = _node(1, approver_type="applicant_self", node_type="notify", node_name="抄送人")
        flow_node.condition_rules = json.dumps(
            {
                "assignee_source": "applicant_self",
                "notify_sources": ["applicant_self"],
            }
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(_flow([flow_node])),
            ]
        )

        preview = await approval_service.build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        node = preview.nodes[0]
        assert node.node_type == "notify"
        assert node.is_resolved is True
        assert node.approver_id is None
        assert node.approver_display == ""

    @pytest.mark.asyncio
    async def test_dynamic_role_unresolved_reasons_are_clear(self, monkeypatch):
        import app.services.approval as approval_service

        monkeypatch.setattr(approval_service, "resolve_approver_id", AsyncMock(return_value=None), raising=False)

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_approval_type()),
                _result(
                    _flow(
                        [
                            _node(1, approver_type="hr", node_name="HR 审批"),
                            _node(2, approver_type="department_head", node_name="部门负责人审批"),
                            _node(3, approver_type="gm", node_name="总经理审批"),
                        ]
                    )
                ),
            ]
        )

        preview = await approval_service.build_template_flow_preview(db, approval_type_id=11, applicant_id=9)

        by_type = {node.approver_type: node for node in preview.nodes}
        assert by_type["hr"].resolve_message == "未找到 HR 审批人"
        assert by_type["hr"].approver_display == "未找到 HR 审批人"
        assert by_type["department_head"].resolve_message == "未配置部门负责人"
        assert by_type["department_head"].approver_display == "未配置部门负责人"
        assert by_type["gm"].resolve_message == "未找到总经理"
        assert by_type["gm"].approver_display == "未找到总经理"


class TestApprovalFlowPersonProgress:
    @pytest.mark.asyncio
    async def test_instance_progress_resolves_record_and_task_approver_names(self):
        import app.services.approval as approval_service

        approved_at = datetime(2026, 4, 21, 9, 30, tzinfo=timezone.utc)
        instance = SimpleNamespace(
            id=31,
            status="pending",
            current_node_order=2,
            flow_id=21,
            applicant_id=9,
            flow_snapshot=_snapshot(
                [
                    {
                        "node_order": 1,
                        "node_name": "主管审批",
                        "node_type": "approval",
                        "approver_type": "direct_manager",
                    },
                    {
                        "node_order": 2,
                        "node_name": "HR 复核",
                        "node_type": "approval",
                        "approver_type": "hr",
                    },
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
            tasks=[SimpleNamespace(node_order=2, approver_id=88, status="pending", completed_at=None)],
        )
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(_employee(77, "已审批主管")),
                _result(_employee(88, "待审批HR")),
            ]
        )

        nodes = await approval_service.build_instance_progress(db, instance)

        assert nodes[0].approver_id == 77
        assert nodes[0].approver_name == "已审批主管"
        assert "已审批主管" in nodes[0].approver_display
        assert "员工 #77" not in nodes[0].approver_display
        assert nodes[1].approver_id == 88
        assert nodes[1].approver_name == "待审批HR"
        assert "待审批HR" in nodes[1].approver_display
        assert "员工 #88" not in nodes[1].approver_display

    @pytest.mark.asyncio
    async def test_instance_progress_resolves_pending_task_name_without_records(self):
        import app.services.approval as approval_service

        instance = SimpleNamespace(
            id=32,
            status="pending",
            current_node_order=1,
            flow_id=21,
            applicant_id=9,
            flow_snapshot=_snapshot(
                [
                    {
                        "node_order": 1,
                        "node_name": "HR 复核",
                        "node_type": "approval",
                        "approver_type": "hr",
                    },
                ]
            ),
            records=[],
            tasks=[SimpleNamespace(node_order=1, approver_id=88, status="pending", completed_at=None)],
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(_employee(88, "待审批HR")))

        nodes = await approval_service.build_instance_progress(db, instance)

        assert nodes[0].approver_id == 88
        assert nodes[0].approver_name == "待审批HR"
        assert "待审批HR" in nodes[0].approver_display
        assert "员工 #88" not in nodes[0].approver_display


class TestApprovalFlowPersonPendingList:
    @pytest.mark.asyncio
    async def test_my_pending_uses_pending_task_for_multi_level_manager(self):
        import app.services.approval as approval_service

        approver = SimpleNamespace(id=66, name="测试产研负责人", is_superuser=False, department_id=58)
        instance = SimpleNamespace(
            id=3,
            flow_id=2,
            applicant_id=62,
            status="pending",
            current_node_order=1,
            created_at=datetime(2026, 4, 21, 6, 9, tzinfo=timezone.utc),
        )
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _result(approver),
                _result([instance]),
            ]
        )

        total, items = await approval_service.list_my_pending(db, approver_id=66)

        assert total == 1
        assert items == [instance]


class TestApprovalFlowPersonMobileSourceContract:
    def test_mobile_start_page_renders_backend_preview_display(self):
        source = _read_source("mobile/src/pages/ApprovalStartPage.vue")

        assert "flowPreview" in source
        assert "visibleFlowPreviewNodes" in source
        assert "node.approver_display || node.resolve_message" in source
        assert "flow-preview" in source

    def test_mobile_detail_page_renders_backend_progress_display(self):
        source = _read_source("mobile/src/pages/ApprovalDetailPage.vue")

        assert "progressNodes" in source
        assert "progress_nodes" in source
        assert "node.approver_display || node.resolve_message" in source
