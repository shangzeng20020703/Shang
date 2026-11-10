"""
审批流服务单元测试
覆盖: 审批操作验证、撤回权限、转审参数、状态守卫
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from app.services.approval import (
    _build_flow_snapshot,
    _determine_flow,
    _approval_archive_conditions,
    _ensure_template_submit_allowed,
    _flow_module_candidates,
    _sync_business_status,
    approval_instance_step_summary,
    create_pending_task_for_instance,
    _notify_applicant_approval_update,
    _notify_pending_approval_tasks,
    process_approval,
    remind_instance,
    user_can_view_instance,
    withdraw_instance,
)
from app.schemas.approval import ApprovalActionRequest


# ============================================================
# 辅助工具
# ============================================================

def _make_instance(status="pending", applicant_id=1, current_node_order=1, flow_id=1):
    instance = MagicMock()
    instance.status = status
    instance.applicant_id = applicant_id
    instance.current_node_order = current_node_order
    instance.flow_id = flow_id
    instance.id = 1
    instance.updated_at = None
    instance.records = []
    instance.tasks = []
    if status == "pending":
        instance.tasks = [
            SimpleNamespace(
                node_order=current_node_order,
                status="pending",
                approver_id=10,
                completed_at=None,
            )
        ]
    return instance


def _make_flow_with_nodes(max_order=2):
    """创建有N个节点的审批流"""
    flow = MagicMock()
    nodes = []
    for i in range(1, max_order + 1):
        node = MagicMock()
        node.node_order = i
        nodes.append(node)
    flow.nodes = nodes
    flow.id = 1
    flow.name = "测试审批流"
    return flow


def _make_db_for_approval(instance, flow=None):
    """构建支持 get_instance + get_flow 的 mock db"""
    db = AsyncMock()
    execute_count = [0]

    async def mock_execute(stmt):
        result = MagicMock()
        if execute_count[0] == 0:
            # get_instance call
            result.scalar_one_or_none.return_value = instance
        elif execute_count[0] == 1 and flow is not None:
            # get_flow call (selectinload)
            result.scalar_one_or_none.return_value = flow
        else:
            result.scalar_one_or_none.return_value = None
        execute_count[0] += 1
        return result

    db.execute = mock_execute
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


# ============================================================
# process_approval 审批动作
# ============================================================

class TestProcessApproval:

    @pytest.mark.asyncio
    async def test_approve_pending_instance_moves_to_next_node(self):
        """多节点审批流 → 通过后移到下一节点"""
        instance = _make_instance(status="pending", current_node_order=1)
        flow = _make_flow_with_nodes(max_order=2)
        db = _make_db_for_approval(instance, flow)

        data = ApprovalActionRequest(action="approve", comment="同意")
        result = await process_approval(db, 1, approver_id=10, data=data)
        assert result.current_node_order == 2

    @pytest.mark.asyncio
    async def test_approve_last_node_sets_approved_status(self):
        """最后一个节点通过 → 状态变为 approved"""
        instance = _make_instance(status="pending", current_node_order=2)
        flow = _make_flow_with_nodes(max_order=2)
        db = _make_db_for_approval(instance, flow)

        data = ApprovalActionRequest(action="approve", comment="终审通过")
        result = await process_approval(db, 1, approver_id=10, data=data)
        assert result.status == "approved"

    @pytest.mark.asyncio
    async def test_pending_task_notification_targets_current_approver(self):
        """提交审批后，当前节点审批人收到待办通知。"""
        instance = SimpleNamespace(
            id=8,
            status="pending",
            applicant_id=5,
            current_node_order=1,
            summary="补卡申请",
            tasks=[
                SimpleNamespace(node_order=1, status="pending", approver_id=10),
            ],
        )
        db = AsyncMock()

        with (
            patch("app.services.approval._employee_name_by_id", new=AsyncMock(return_value="孙明月")),
            patch("app.services.approval.NotificationService.create", new=AsyncMock()) as create_notification,
        ):
            notified = await _notify_pending_approval_tasks(db, instance, actor_id=5)

        notification = create_notification.await_args.args[1]
        assert notified == 1
        assert notification.recipient_id == 10
        assert notification.sender_id == 5
        assert notification.notif_type == "approval"
        assert notification.ref_type == "approval_instance"
        assert notification.ref_id == 8
        assert "请及时处理" in notification.content

    @pytest.mark.asyncio
    async def test_applicant_receives_approval_result_notification(self):
        """审批人同意后，申请人收到审批进展提醒。"""
        instance = SimpleNamespace(
            id=9,
            status="approved",
            applicant_id=5,
            summary="年假申请",
        )
        db = AsyncMock()

        with (
            patch("app.services.approval._employee_name_by_id", new=AsyncMock(return_value="直属主管")),
            patch("app.services.approval.NotificationService.create", new=AsyncMock()) as create_notification,
        ):
            notified = await _notify_applicant_approval_update(
                db,
                instance,
                actor_id=10,
                action="approve",
            )

        notification = create_notification.await_args.args[1]
        assert notified is True
        assert notification.recipient_id == 5
        assert notification.sender_id == 10
        assert notification.title == "审批已通过"
        assert "已同意" in notification.content

    @pytest.mark.asyncio
    async def test_headcount_request_approval_increases_department_quota(self):
        """用人审批通过后，批准的扩编人数回写部门编制。"""
        department = SimpleNamespace(id=7, headcount_quota=10)
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = department
        db = AsyncMock()
        db.execute = AsyncMock(return_value=query_result)

        instance = SimpleNamespace(
            module="headcount_request",
            business_type="headcount_request",
            business_id=123,
            form_data={
                "department_id": 7,
                "headcount": 2,
                "quota": 10,
                "available_count": 0,
            },
        )

        await _sync_business_status(db, instance, "approved")

        assert department.headcount_quota == 12

    @pytest.mark.asyncio
    async def test_vote_mode_reject_does_not_finish_while_pass_threshold_still_possible(self):
        instance = SimpleNamespace(
            id=1,
            status="pending",
            current_node_order=1,
            flow_id=1,
            module="custom",
            business_id=1,
            applicant_id=5,
            records=[],
            tasks=[
                SimpleNamespace(node_order=1, status="pending", approver_id=10, completed_at=None),
                SimpleNamespace(node_order=1, status="pending", approver_id=20, completed_at=None),
                SimpleNamespace(node_order=1, status="pending", approver_id=30, completed_at=None),
            ],
            flow_snapshot=json.dumps(
                {
                    "nodes": [
                        {
                            "node_order": 1,
                            "node_type": "approval",
                            "approver_type": "specific_user",
                            "approver_id": 10,
                            "condition_rules": json.dumps(
                                {
                                    "assignee_source": "specific_user",
                                    "member_ids": [10, 20, 30],
                                    "approval_mode": "vote",
                                    "vote_pass_count": 2,
                                }
                            ),
                        }
                    ]
                }
            ),
        )
        db = _make_db_for_approval(instance)

        with patch("app.services.approval._approval_record_actions_for_node", new=AsyncMock(return_value=["reject"])):
            result = await process_approval(
                db,
                1,
                approver_id=10,
                data=ApprovalActionRequest(action="reject", comment="不同意"),
            )

        assert result.status == "pending"
        assert result.current_node_order == 1
        assert [task.status for task in result.tasks].count("pending") == 2

    @pytest.mark.asyncio
    async def test_notify_node_after_approvals_is_cc_and_does_not_block(self):
        instance = SimpleNamespace(
            id=1,
            status="pending",
            current_node_order=2,
            flow_id=1,
            module="custom",
            business_id=1,
            applicant_id=5,
            tasks=[
                SimpleNamespace(
                    node_order=2,
                    status="pending",
                    completed_at=None,
                    approver_id=20,
                )
            ],
            records=[],
            flow_snapshot=json.dumps(
                {
                    "nodes": [
                        {"node_order": 1, "node_type": "approval", "approver_type": "direct_manager"},
                        {"node_order": 2, "node_type": "approval", "approver_type": "department_head"},
                        {"node_order": 3, "node_type": "notify", "approver_type": "specific_user", "approver_id": 99},
                    ]
                }
            ),
        )
        db = _make_db_for_approval(instance)

        data = ApprovalActionRequest(action="approve", comment="同意")
        result = await process_approval(db, 1, approver_id=20, data=data)

        added_actions = [getattr(call.args[0], "action", None) for call in db.add.call_args_list]
        assert result.status == "approved"
        assert "notify" in added_actions
        assert "pending" not in [getattr(task, "status", None) for task in result.tasks]

    @pytest.mark.asyncio
    async def test_build_flow_snapshot_selects_company_condition_branch(self):
        flow = SimpleNamespace(
            id=1,
            name="请假审批流",
            module="leave_visual",
            nodes=[
                SimpleNamespace(
                    node_order=1,
                    node_type="condition_branch",
                    approver_type="condition_branch",
                    approver_id=None,
                    auto_approve_hours=None,
                    condition_rules=json.dumps(
                        {
                            "branches": [
                                {
                                    "label": "总公司",
                                    "is_default_branch": False,
                                    "conditions": [
                                        {"field": "applicant.company_id", "operator": "eq", "value": 1}
                                    ],
                                    "nodes": [
                                        {
                                            "node_type": "approval",
                                            "approver_type": "specific_user",
                                            "approver_id": 101,
                                        },
                                        {
                                            "node_type": "notify",
                                            "approver_type": "specific_user",
                                            "approver_id": 201,
                                            "member_ids": [201, 202],
                                        },
                                    ],
                                },
                                {
                                    "label": "分公司",
                                    "is_default_branch": True,
                                    "nodes": [
                                        {
                                            "node_type": "approval",
                                            "approver_type": "specific_user",
                                            "approver_id": 301,
                                        }
                                    ],
                                },
                            ]
                        },
                        ensure_ascii=False,
                    ),
                )
            ],
        )
        employee_result = MagicMock()
        employee_result.scalar_one_or_none.return_value = SimpleNamespace(id=9, company_id=1)
        company_result = MagicMock()
        company_result.scalar_one_or_none.return_value = SimpleNamespace(id=1, name="总公司")
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[employee_result, company_result])

        snapshot = json.loads(await _build_flow_snapshot(db, flow, applicant_id=9, form_data={}))

        assert [node["approver_id"] for node in snapshot["nodes"]] == [101, 201]
        assert snapshot["nodes"][1]["member_ids"] == [201, 202]

    @pytest.mark.asyncio
    async def test_build_flow_snapshot_uses_default_branch_when_condition_misses(self):
        flow = SimpleNamespace(
            id=1,
            name="请假审批流",
            module="leave_visual",
            nodes=[
                SimpleNamespace(
                    node_order=1,
                    node_type="condition_branch",
                    approver_type="condition_branch",
                    approver_id=None,
                    auto_approve_hours=None,
                    condition_rules=json.dumps(
                        {
                            "branches": [
                                {
                                    "label": "总公司",
                                    "conditions": [
                                        {"field": "applicant.company_id", "operator": "eq", "value": 1}
                                    ],
                                    "nodes": [
                                        {"node_type": "approval", "approver_type": "specific_user", "approver_id": 101}
                                    ],
                                },
                                {
                                    "label": "分公司",
                                    "is_default_branch": True,
                                    "nodes": [
                                        {"node_type": "approval", "approver_type": "specific_user", "approver_id": 301}
                                    ],
                                },
                            ]
                        },
                        ensure_ascii=False,
                    ),
                )
            ],
        )
        employee_result = MagicMock()
        employee_result.scalar_one_or_none.return_value = SimpleNamespace(id=9, company_id=2)
        company_result = MagicMock()
        company_result.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[employee_result, company_result])

        snapshot = json.loads(await _build_flow_snapshot(db, flow, applicant_id=9, form_data={}))

        assert [node["approver_id"] for node in snapshot["nodes"]] == [301]

    @pytest.mark.asyncio
    async def test_build_flow_snapshot_infers_leave_duration_condition_from_dates(self):
        flow = SimpleNamespace(
            id=1,
            name="请假审批流",
            module="leave",
            nodes=[
                SimpleNamespace(
                    node_order=1,
                    node_type="condition_branch",
                    approver_type="condition_branch",
                    approver_id=None,
                    auto_approve_hours=None,
                    condition_rules=json.dumps(
                        {
                            "branches": [
                                {
                                    "label": "三天及以上",
                                    "conditions": [
                                        {"field": "duration", "operator": "gte", "value": 3}
                                    ],
                                    "nodes": [
                                        {
                                            "node_type": "approval",
                                            "approver_type": "multi_level_manager",
                                            "assignee_source": "multi_level_manager",
                                            "approval_mode": "sequential",
                                        }
                                    ],
                                },
                                {
                                    "label": "默认",
                                    "is_default_branch": True,
                                    "nodes": [
                                        {
                                            "node_type": "approval",
                                            "approver_type": "direct_manager",
                                            "assignee_source": "direct_manager",
                                        }
                                    ],
                                },
                            ]
                        },
                        ensure_ascii=False,
                    ),
                )
            ],
        )

        long_leave_snapshot = json.loads(
            await _build_flow_snapshot(
                AsyncMock(),
                flow,
                applicant_id=None,
                form_data={"start_time": "2026-05-09", "end_time": "2026-05-13"},
            )
        )
        short_leave_snapshot = json.loads(
            await _build_flow_snapshot(
                AsyncMock(),
                flow,
                applicant_id=None,
                form_data={"start_time": "2026-05-09", "end_time": "2026-05-10"},
            )
        )

        assert long_leave_snapshot["nodes"][0]["approver_type"] == "multi_level_manager"
        assert short_leave_snapshot["nodes"][0]["approver_type"] == "direct_manager"

    @pytest.mark.asyncio
    async def test_notify_node_with_multiple_member_ids_adds_one_record_per_cc(self):
        instance = SimpleNamespace(
            id=1,
            status="pending",
            current_node_order=2,
            flow_id=1,
            module="custom",
            business_id=1,
            applicant_id=5,
            tasks=[
                SimpleNamespace(
                    node_order=2,
                    status="pending",
                    completed_at=None,
                    approver_id=20,
                )
            ],
            records=[],
            flow_snapshot=json.dumps(
                {
                    "nodes": [
                        {"node_order": 1, "node_type": "approval", "approver_type": "direct_manager"},
                        {"node_order": 2, "node_type": "approval", "approver_type": "department_head"},
                        {
                            "node_order": 3,
                            "node_type": "notify",
                            "approver_type": "specific_user",
                            "approver_id": 99,
                            "condition_rules": json.dumps({"member_ids": [99, 100]}),
                        },
                    ]
                }
            ),
        )
        db = _make_db_for_approval(instance)

        data = ApprovalActionRequest(action="approve", comment="同意")
        result = await process_approval(db, 1, approver_id=20, data=data)

        notify_records = [
            call.args[0]
            for call in db.add.call_args_list
            if getattr(call.args[0], "action", None) == "notify"
        ]
        assert result.status == "approved"
        assert [record.approver_id for record in notify_records] == [99, 100]

    @pytest.mark.asyncio
    async def test_reject_sets_rejected_status(self):
        """拒绝审批 → 状态变为 rejected"""
        instance = _make_instance(status="pending", current_node_order=1)
        flow = _make_flow_with_nodes(max_order=2)
        db = _make_db_for_approval(instance, flow)

        data = ApprovalActionRequest(action="reject", comment="不符合条件")
        result = await process_approval(db, 1, approver_id=10, data=data)
        assert result.status == "rejected"

    @pytest.mark.asyncio
    async def test_non_pending_task_owner_cannot_process_approval(self):
        """非当前待办审批人不能审批，防止越权影响员工权益。"""
        instance = _make_instance(status="pending", current_node_order=1)
        flow = _make_flow_with_nodes(max_order=2)
        db = _make_db_for_approval(instance, flow)

        data = ApprovalActionRequest(action="approve", comment="越权审批")
        with pytest.raises(HTTPException) as exc:
            await process_approval(db, 1, approver_id=99, data=data)

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_counter_sign_waits_for_remaining_pending_approver(self):
        """会签节点只完成当前审批人的待办，所有人同意后才推进。"""
        instance = SimpleNamespace(
            id=1,
            status="pending",
            current_node_order=1,
            flow_id=1,
            module="leave",
            business_id=1,
            applicant_id=5,
            updated_at=None,
            tasks=[
                SimpleNamespace(node_order=1, status="pending", approver_id=10, completed_at=None),
                SimpleNamespace(node_order=1, status="pending", approver_id=20, completed_at=None),
            ],
            records=[],
            flow_snapshot=json.dumps(
                {
                    "nodes": [
                        {
                            "node_order": 1,
                            "node_type": "approval",
                            "approver_type": "specific_user",
                            "member_ids": [10, 20],
                            "approval_mode": "counter_sign",
                        },
                        {"node_order": 2, "node_type": "approval", "approver_type": "direct_manager"},
                    ],
                    "permission_rules": {},
                }
            ),
        )
        db = _make_db_for_approval(instance)

        result = await process_approval(
            db,
            1,
            approver_id=10,
            data=ApprovalActionRequest(action="approve", comment="同意"),
        )

        assert result.status == "pending"
        assert result.current_node_order == 1
        assert instance.tasks[0].status == "completed"
        assert instance.tasks[1].status == "pending"

    @pytest.mark.asyncio
    async def test_sequential_approval_creates_next_task_before_advancing(self):
        """依次审批节点只给下一位审批人生成待办，全部完成后才推进。"""
        instance = SimpleNamespace(
            id=1,
            status="pending",
            current_node_order=1,
            flow_id=1,
            module="leave",
            business_id=1,
            applicant_id=5,
            updated_at=None,
            tasks=[
                SimpleNamespace(node_order=1, status="pending", approver_id=10, completed_at=None),
            ],
            records=[],
            flow_snapshot=json.dumps(
                {
                    "nodes": [
                        {
                            "node_order": 1,
                            "node_type": "approval",
                            "approver_type": "specific_user",
                            "member_ids": [10, 20],
                            "approval_mode": "sequential",
                        },
                        {"node_order": 2, "node_type": "approval", "approver_type": "direct_manager"},
                    ],
                    "permission_rules": {},
                }
            ),
        )
        db = _make_db_for_approval(instance)

        result = await process_approval(
            db,
            1,
            approver_id=10,
            data=ApprovalActionRequest(action="approve", comment="同意"),
        )

        assert result.status == "pending"
        assert result.current_node_order == 1
        assert instance.tasks[0].status == "completed"
        assert instance.tasks[1].approver_id == 20
        assert instance.tasks[1].status == "pending"

    @pytest.mark.asyncio
    async def test_act_on_non_pending_instance_raises_400(self):
        """已完成的审批不可再操作"""
        instance = _make_instance(status="approved")
        db = _make_db_for_approval(instance)

        data = ApprovalActionRequest(action="approve", comment="重复审批")
        with pytest.raises(HTTPException) as exc_info:
            await process_approval(db, 1, approver_id=10, data=data)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_act_on_rejected_instance_raises_400(self):
        """已拒绝的审批不可再操作"""
        instance = _make_instance(status="rejected")
        db = _make_db_for_approval(instance)

        data = ApprovalActionRequest(action="approve")
        with pytest.raises(HTTPException) as exc_info:
            await process_approval(db, 1, approver_id=10, data=data)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_action_raises_400(self):
        """无效的操作类型 → 400"""
        instance = _make_instance(status="pending")
        db = _make_db_for_approval(instance)

        data = ApprovalActionRequest(action="invalid_action")
        with pytest.raises(HTTPException) as exc_info:
            await process_approval(db, 1, approver_id=10, data=data)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_transfer_without_target_raises_400(self):
        """转审操作必须指定 transfer_to_id"""
        instance = _make_instance(status="pending")
        db = _make_db_for_approval(instance)

        data = ApprovalActionRequest(action="transfer", transfer_to_id=None)
        with pytest.raises(HTTPException) as exc_info:
            await process_approval(db, 1, approver_id=10, data=data)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_transfer_with_target_succeeds(self):
        """转审操作指定 transfer_to_id → 成功"""
        instance = _make_instance(status="pending")
        flow = _make_flow_with_nodes(max_order=2)
        db = _make_db_for_approval(instance, flow)

        data = ApprovalActionRequest(action="transfer", transfer_to_id=99, comment="转审给部门总监")
        # transfer doesn't raise, but also doesn't change node/status
        result = await process_approval(db, 1, approver_id=10, data=data)
        # Status should still be pending (transfer just records)
        assert result.status == "pending"


class TestApprovalTaskCreation:

    @pytest.mark.asyncio
    async def test_specific_member_node_creates_one_pending_task_per_member(self):
        """指定多人审批时，每个审批人都要拿到独立待办。"""
        instance = SimpleNamespace(
            id=1,
            status="pending",
            current_node_order=1,
            flow_id=1,
            applicant_id=5,
            form_data={},
            tasks=[],
            flow_snapshot=json.dumps(
                {
                    "nodes": [
                        {
                            "node_order": 1,
                            "node_type": "approval",
                            "approver_type": "specific_user",
                            "member_ids": [11, 12],
                            "approval_mode": "counter_sign",
                        }
                    ]
                }
            ),
        )
        empty_tasks_result = MagicMock()
        empty_tasks_result.scalars.return_value.all.return_value = []
        db = AsyncMock()
        db.execute = AsyncMock(return_value=empty_tasks_result)
        db.add = MagicMock()
        db.flush = AsyncMock()

        task = await create_pending_task_for_instance(db, instance)

        assert task.approver_id == 11
        assert [item.approver_id for item in instance.tasks] == [11, 12]
        assert [item.status for item in instance.tasks] == ["pending", "pending"]
        assert db.add.call_count == 2

    @pytest.mark.asyncio
    async def test_parallel_branch_creates_pending_task_for_each_branch(self):
        """并行分支到达时，两条支线要同时产生待办。"""
        instance = SimpleNamespace(
            id=1,
            status="pending",
            current_node_order=1,
            flow_id=1,
            applicant_id=5,
            form_data={},
            tasks=[],
            flow_snapshot=json.dumps(
                {
                    "nodes": [
                        {
                            "node_order": 1,
                            "node_type": "parallel_branch",
                            "condition_rules": json.dumps(
                                {
                                    "branches": [
                                        {
                                            "label": "总部审批",
                                            "nodes": [
                                                {
                                                    "node_order": 1,
                                                    "node_type": "approval",
                                                    "approver_type": "specific_user",
                                                    "member_ids": [11],
                                                }
                                            ],
                                        },
                                        {
                                            "label": "厂房审批",
                                            "nodes": [
                                                {
                                                    "node_order": 1,
                                                    "node_type": "approval",
                                                    "approver_type": "specific_user",
                                                    "member_ids": [12],
                                                }
                                            ],
                                        },
                                    ]
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )
        db = AsyncMock()
        empty_tasks_result = MagicMock()
        empty_tasks_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=empty_tasks_result)
        db.add = MagicMock()
        db.flush = AsyncMock()

        task = await create_pending_task_for_instance(db, instance)

        assert task.approver_id == 11
        assert [item.approver_id for item in instance.tasks] == [11, 12]
        assert [item.branch_key for item in instance.tasks] == ["branch_1", "branch_2"]
        assert [item.branch_node_order for item in instance.tasks] == [1, 1]
        assert [item.branch_label for item in instance.tasks] == ["总部审批", "厂房审批"]
        assert db.add.call_count == 2

    @pytest.mark.asyncio
    async def test_parallel_branch_waits_all_branches_before_merge(self):
        """一条支线通过后仍等待另一条支线，全部通过后再汇合完成。"""
        instance = SimpleNamespace(
            id=1,
            status="pending",
            current_node_order=1,
            flow_id=1,
            applicant_id=5,
            form_data={},
            template_version_id=None,
            tasks=[
                SimpleNamespace(
                    node_order=1,
                    status="pending",
                    approver_id=11,
                    completed_at=None,
                    branch_key="branch_1",
                    branch_label="总部审批",
                    branch_node_order=1,
                ),
                SimpleNamespace(
                    node_order=1,
                    status="pending",
                    approver_id=12,
                    completed_at=None,
                    branch_key="branch_2",
                    branch_label="厂房审批",
                    branch_node_order=1,
                ),
            ],
            flow_snapshot=json.dumps(
                {
                    "nodes": [
                        {
                            "node_order": 1,
                            "node_type": "parallel_branch",
                            "condition_rules": json.dumps(
                                {
                                    "branches": [
                                        {
                                            "label": "总部审批",
                                            "nodes": [
                                                {
                                                    "node_order": 1,
                                                    "node_type": "approval",
                                                    "approver_type": "specific_user",
                                                    "member_ids": [11],
                                                }
                                            ],
                                        },
                                        {
                                            "label": "厂房审批",
                                            "nodes": [
                                                {
                                                    "node_order": 1,
                                                    "node_type": "approval",
                                                    "approver_type": "specific_user",
                                                    "member_ids": [12],
                                                }
                                            ],
                                        },
                                    ]
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = instance
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with (
            patch("app.services.approval.mark_approval_notifications_read_for_recipient", new=AsyncMock()),
            patch("app.services.approval._notify_after_approval_action", new=AsyncMock()),
            patch("app.services.approval._sync_business_status", new=AsyncMock()),
            patch("app.services.approval._enqueue_approval_terminal_event", new=AsyncMock()),
        ):
            first = await process_approval(
                db,
                1,
                approver_id=11,
                data=ApprovalActionRequest(action="approve", comment="总部同意"),
            )
            assert first.status == "pending"
            assert instance.tasks[0].status == "completed"
            assert instance.tasks[1].status == "pending"

            final = await process_approval(
                db,
                1,
                approver_id=12,
                data=ApprovalActionRequest(action="approve", comment="厂房同意"),
            )

        assert final.status == "approved"
        assert all(item.status == "completed" for item in instance.tasks)


class TestApprovalSubmitPermission:

    @pytest.mark.asyncio
    async def test_selected_member_submit_permission_rejects_unlisted_applicant(self):
        approval_type = SimpleNamespace(
            permission_rules=json.dumps(
                {
                    "submit_permission": {
                        "type": "selected_members",
                        "member_ids": [7, 8],
                    }
                }
            )
        )

        with pytest.raises(HTTPException) as exc_info:
            await _ensure_template_submit_allowed(AsyncMock(), approval_type, applicant_id=9)

        assert exc_info.value.status_code == 403


# ============================================================
# withdraw_instance 撤回审批
# ============================================================

class TestWithdrawInstance:

    def _make_db_single(self, instance):
        db = AsyncMock()
        execute_count = [0]

        async def mock_execute(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = instance
            return result

        db.execute = mock_execute
        db.flush = AsyncMock()
        db.refresh = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_applicant_can_withdraw_pending_instance(self):
        """申请人撤回待审批实例 → 成功"""
        instance = _make_instance(status="pending", applicant_id=5)
        db = self._make_db_single(instance)
        result = await withdraw_instance(db, 1, applicant_id=5)
        assert result.status == "withdrawn"

    @pytest.mark.asyncio
    async def test_non_applicant_cannot_withdraw_raises_403(self):
        """非申请人尝试撤回 → 403"""
        instance = _make_instance(status="pending", applicant_id=5)
        db = self._make_db_single(instance)
        with pytest.raises(HTTPException) as exc_info:
            await withdraw_instance(db, 1, applicant_id=99)  # wrong person
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_cannot_withdraw_approved_instance_raises_400(self):
        """已通过的审批不可撤回"""
        instance = _make_instance(status="approved", applicant_id=5)
        db = self._make_db_single(instance)
        with pytest.raises(HTTPException) as exc_info:
            await withdraw_instance(db, 1, applicant_id=5)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_cannot_withdraw_rejected_instance_raises_400(self):
        """已拒绝的审批不可撤回"""
        instance = _make_instance(status="rejected", applicant_id=5)
        db = self._make_db_single(instance)
        with pytest.raises(HTTPException) as exc_info:
            await withdraw_instance(db, 1, applicant_id=5)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_cannot_withdraw_already_withdrawn_raises_400(self):
        """已撤回的审批不可再次撤回"""
        instance = _make_instance(status="withdrawn", applicant_id=5)
        db = self._make_db_single(instance)
        with pytest.raises(HTTPException) as exc_info:
            await withdraw_instance(db, 1, applicant_id=5)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_withdraw_nonexistent_instance_raises_404(self):
        """不存在的审批实例 → 404"""
        db = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=scalar_result)
        with pytest.raises(HTTPException) as exc_info:
            await withdraw_instance(db, 999, applicant_id=5)
        assert exc_info.value.status_code == 404


class TestApprovalReminder:

    @pytest.mark.asyncio
    async def test_applicant_can_remind_current_pending_approver(self):
        instance = SimpleNamespace(
            id=88,
            status="pending",
            applicant_id=5,
            current_node_order=2,
            summary="彭照峰的对外付款",
            tasks=[
                SimpleNamespace(node_order=2, approver_id=20, status="pending"),
                SimpleNamespace(node_order=1, approver_id=19, status="completed"),
            ],
            records=[],
        )
        instance_result = MagicMock()
        instance_result.scalar_one_or_none.return_value = instance
        requester_result = MagicMock()
        requester_result.scalar_one_or_none.return_value = SimpleNamespace(name="李梦")
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[instance_result, requester_result])
        db.flush = AsyncMock()

        with patch("app.services.approval.NotificationService.create", new=AsyncMock()) as create_notification:
            result = await remind_instance(db, 88, requester_id=5)

        assert result.notified_count == 1
        assert result.notified_approver_ids == [20]
        notification = create_notification.await_args.args[1]
        assert notification.recipient_id == 20
        assert notification.sender_id == 5
        assert notification.notif_type == "approval"
        assert notification.ref_type == "approval_instance"
        assert notification.ref_id == 88
        assert "李梦" in notification.content
        assert "彭照峰的对外付款" in notification.content

    @pytest.mark.asyncio
    async def test_non_applicant_cannot_remind_approval(self):
        instance = SimpleNamespace(
            id=88,
            status="pending",
            applicant_id=5,
            current_node_order=1,
            summary="请假审批",
            tasks=[SimpleNamespace(node_order=1, approver_id=20, status="pending")],
            records=[],
        )
        instance_result = MagicMock()
        instance_result.scalar_one_or_none.return_value = instance
        db = AsyncMock()
        db.execute = AsyncMock(return_value=instance_result)

        with pytest.raises(HTTPException) as exc_info:
            await remind_instance(db, 88, requester_id=6)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_cannot_remind_finished_approval(self):
        instance = SimpleNamespace(
            id=88,
            status="approved",
            applicant_id=5,
            current_node_order=1,
            summary="请假审批",
            tasks=[],
            records=[],
        )
        instance_result = MagicMock()
        instance_result.scalar_one_or_none.return_value = instance
        db = AsyncMock()
        db.execute = AsyncMock(return_value=instance_result)

        with pytest.raises(HTTPException) as exc_info:
            await remind_instance(db, 88, requester_id=5)

        assert exc_info.value.status_code == 400


class TestApprovalArchive:
    def test_archive_conditions_include_applicant_filters(self):
        conditions = _approval_archive_conditions(
            applicant_id=5,
            applicant_keyword="EMP001",
        )

        rendered = " ".join(str(condition) for condition in conditions)
        assert "approval_instances.applicant_id" in rendered
        assert "employees" in rendered

    def test_pending_instance_step_summary_uses_current_node(self):
        instance = SimpleNamespace(
            status="pending",
            current_node_order=2,
            flow_snapshot=json.dumps(
                {
                    "nodes": [
                        {"node_order": 1, "node_type": "approval"},
                        {"node_order": 2, "node_type": "approval"},
                    ]
                }
            ),
            records=[],
        )

        summary = approval_instance_step_summary(instance)

        assert summary["current_node_name"] == "二级审批人"
        assert summary["current_node_status"] == "pending"
        assert summary["current_node_status_label"] == "待审批"

    def test_finished_instance_step_summary_marks_flow_end(self):
        instance = SimpleNamespace(
            status="approved",
            current_node_order=3,
            flow_snapshot=json.dumps({"nodes": []}),
            records=[],
        )

        summary = approval_instance_step_summary(instance)

        assert summary["current_node_name"] == "流程结束"
        assert summary["current_node_status_label"] == "已通过"

    @pytest.mark.asyncio
    async def test_related_approver_or_cc_can_view_instance(self):
        instance = SimpleNamespace(
            id=10,
            applicant_id=1,
            records=[
                SimpleNamespace(approver_id=20, action="approve"),
                SimpleNamespace(approver_id=30, action="notify"),
            ],
            tasks=[],
        )
        db = AsyncMock()

        assert await user_can_view_instance(db, instance, 20) is True
        assert await user_can_view_instance(db, instance, 30) is True
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_pending_task_owner_can_view_instance(self):
        instance = SimpleNamespace(
            id=10,
            applicant_id=1,
            records=[],
            tasks=[SimpleNamespace(approver_id=40, status="pending")],
        )
        db = AsyncMock()

        assert await user_can_view_instance(db, instance, 40) is True
        db.execute.assert_not_called()


# ============================================================
# 审批流模块无流程时创建实例
# ============================================================

class TestApprovalInstanceCreation:

    @pytest.mark.asyncio
    async def test_create_instance_with_no_active_flow_raises_400(self):
        from app.services.approval import create_instance
        from app.schemas.approval import ApprovalInstanceCreate

        db = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None  # No active flow
        db.execute = AsyncMock(return_value=scalar_result)

        data = ApprovalInstanceCreate(
            module="leave",
            business_id=1,
            business_type="leave_request",
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_instance(db, data, applicant_id=1)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_instance_with_flow_no_nodes_raises_400(self):
        from app.services.approval import create_instance
        from app.schemas.approval import ApprovalInstanceCreate

        flow = MagicMock()
        flow.id = 1
        flow.name = "空节点流程"
        flow.nodes = []  # No nodes configured

        db = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = flow
        db.execute = AsyncMock(return_value=scalar_result)

        data = ApprovalInstanceCreate(
            module="leave",
            business_id=1,
            business_type="leave_request",
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_instance(db, data, applicant_id=1)
        assert exc_info.value.status_code == 400


# ============================================================
# resolve_approver_id 审批人解析
# ============================================================


class TestResolveApproverId:

    def _make_node(self, approver_type, approver_id=None):
        node = MagicMock()
        node.approver_type = approver_type
        node.approver_id = approver_id
        return node

    @pytest.mark.asyncio
    async def test_specific_user_returns_node_approver_id(self):
        from app.services.approval import resolve_approver_id
        node = self._make_node("specific_user", approver_id=42)
        db = AsyncMock()
        result = await resolve_approver_id(db, node, applicant_id=1)
        assert result == 42

    @pytest.mark.asyncio
    async def test_direct_manager_returns_manager_id(self):
        from app.services.approval import resolve_approver_id
        node = self._make_node("direct_manager")
        scalar_r = MagicMock()
        scalar_r.scalar_one_or_none.return_value = 99
        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar_r)
        result = await resolve_approver_id(db, node, applicant_id=1)
        assert result == 99

    @pytest.mark.asyncio
    async def test_direct_manager_no_manager_returns_none(self):
        from app.services.approval import resolve_approver_id
        node = self._make_node("direct_manager")
        scalar_r = MagicMock()
        scalar_r.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar_r)
        result = await resolve_approver_id(db, node, applicant_id=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_gm_does_not_use_superuser_as_business_approver(self):
        from app.services.approval import resolve_approver_id
        node = self._make_node("gm")
        scalar_r = MagicMock()
        scalar_r.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar_r)
        result = await resolve_approver_id(db, node, applicant_id=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_type_falls_back_to_approver_id(self):
        from app.services.approval import resolve_approver_id
        node = self._make_node("unknown_type", approver_id=55)
        db = AsyncMock()
        result = await resolve_approver_id(db, node, applicant_id=1)
        assert result == 55


# ============================================================
# _sync_business_status 业务状态同步
# ============================================================


class TestSyncBusinessStatus:

    def _make_instance(self, module, business_id=1):
        inst = MagicMock()
        inst.id = 1
        inst.module = module
        inst.business_id = business_id
        return inst

    @pytest.mark.asyncio
    async def test_leave_module_sets_approval_status(self):
        from app.services.approval import _sync_business_status
        leave = MagicMock()
        leave.approval_status = None

        scalar_r = MagicMock()
        scalar_r.scalar_one_or_none.return_value = leave
        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar_r)

        instance = self._make_instance("leave")
        await _sync_business_status(db, instance, "approved")
        assert leave.approval_status == "approved"

    @pytest.mark.asyncio
    async def test_leave_rejection_sets_approval_status(self):
        from app.services.approval import _sync_business_status
        leave = MagicMock()
        leave.approval_status = None

        scalar_r = MagicMock()
        scalar_r.scalar_one_or_none.return_value = leave
        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar_r)

        instance = self._make_instance("leave")
        await _sync_business_status(db, instance, "rejected")
        assert leave.approval_status == "rejected"

    @pytest.mark.asyncio
    async def test_unknown_module_does_not_raise(self):
        from app.services.approval import _sync_business_status
        db = AsyncMock()
        instance = self._make_instance("unknown_module")
        # Should complete without raising
        await _sync_business_status(db, instance, "approved")

    @pytest.mark.asyncio
    async def test_leave_not_found_does_not_raise(self):
        from app.services.approval import _sync_business_status
        scalar_r = MagicMock()
        scalar_r.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar_r)

        instance = self._make_instance("leave")
        # Should not raise even if leave record not found
        await _sync_business_status(db, instance, "approved")

    @pytest.mark.asyncio
    async def test_overtime_approval_refreshes_attendance_and_comp_time(self):
        from app.services.approval import _sync_business_status

        overtime = MagicMock()
        overtime.status = "pending"
        approval_record = MagicMock()
        approval_record.approver_id = 8
        approval_record.acted_at = None

        overtime_result = MagicMock()
        overtime_result.scalar_one_or_none.return_value = overtime
        record_result = MagicMock()
        record_result.scalar_one_or_none.return_value = approval_record
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[overtime_result, record_result])

        instance = self._make_instance("overtime")
        with patch(
            "app.services.attendance.AttendanceRecordService.sync_approved_overtime_to_attendance",
            new=AsyncMock(),
        ) as sync_attendance:
            await _sync_business_status(db, instance, "approved")

        assert overtime.status == "approved"
        assert overtime.approval_status == "approved"
        assert overtime.approver_id == 8
        sync_attendance.assert_awaited_once_with(db, overtime)

    @pytest.mark.asyncio
    async def test_db_exception_is_swallowed(self):
        from app.services.approval import _sync_business_status
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("DB connection lost"))

        instance = self._make_instance("leave")
        # Exception must be caught and logged, not propagated
        await _sync_business_status(db, instance, "approved")

# ============================================================
# list_my_pending 我的待审批
# ============================================================


class TestListMyPending:

    def _make_approver(self, is_superuser=False, department_id=None):
        emp = MagicMock()
        emp.id = 1
        emp.is_superuser = is_superuser
        emp.department_id = department_id
        return emp

    @pytest.mark.asyncio
    async def test_returns_empty_for_nonexistent_approver(self):
        from app.services.approval import list_my_pending
        scalar_r = MagicMock()
        scalar_r.scalar_one_or_none.return_value = None  # approver not found
        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar_r)
        total, items = await list_my_pending(db, approver_id=999)
        assert total == 0
        assert items == []

    @pytest.mark.asyncio
    async def test_no_pending_instances_returns_empty(self):
        from app.services.approval import list_my_pending
        approver = self._make_approver()
        call_count = [0]

        async def mock_execute(stmt):
            r = MagicMock()
            if call_count[0] == 0:
                r.scalar_one_or_none.return_value = approver
            elif call_count[0] == 1:
                # roles query
                r.all.return_value = []
            else:
                # pending instances query
                r.scalars.return_value.all.return_value = []
            call_count[0] += 1
            return r

        db = AsyncMock()
        db.execute = mock_execute
        total, items = await list_my_pending(db, approver_id=1)
        assert total == 0
        assert items == []

    @pytest.mark.asyncio
    async def test_pagination_skip_limit(self):
        from app.services.approval import list_my_pending
        approver = self._make_approver()
        call_count = [0]

        async def mock_execute(stmt):
            r = MagicMock()
            if call_count[0] == 0:
                r.scalar_one_or_none.return_value = approver
            elif call_count[0] == 1:
                r.all.return_value = []
            else:
                r.scalars.return_value.all.return_value = []
            call_count[0] += 1
            return r

        db = AsyncMock()
        db.execute = mock_execute
        total, items = await list_my_pending(db, approver_id=1, skip=10, limit=5)
        assert isinstance(total, int)
        assert isinstance(items, list)

    @pytest.mark.asyncio
    async def test_returns_tuple_of_int_and_list(self):
        from app.services.approval import list_my_pending
        scalar_r = MagicMock()
        scalar_r.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar_r)
        result = await list_my_pending(db, approver_id=1)
        assert isinstance(result, tuple)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_fallback_skips_instances_with_pending_tasks_for_other_approvers(self):
        from app.services.approval import list_my_pending

        approver = self._make_approver()
        instance = SimpleNamespace(
            id=100,
            status="pending",
            flow_id=1,
            current_node_order=1,
            applicant_id=5,
            records=[],
            tasks=[SimpleNamespace(node_order=1, status="pending", approver_id=2)],
        )
        node = SimpleNamespace(flow_id=1, node_order=1, approver_type="direct_manager", approver_id=None)

        approver_result = MagicMock()
        approver_result.scalar_one_or_none.return_value = approver
        no_user_task_result = MagicMock()
        no_user_task_result.scalars.return_value.all.return_value = []
        roles_result = MagicMock()
        roles_result.all.return_value = []
        pending_result = MagicMock()
        pending_result.scalars.return_value.all.return_value = [instance]
        nodes_result = MagicMock()
        nodes_result.scalars.return_value.all.return_value = [node]
        employee_result = MagicMock()
        employee_result.all.return_value = [(5, 1, None)]

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            approver_result,
            no_user_task_result,
            roles_result,
            pending_result,
            nodes_result,
            employee_result,
        ])

        total, items = await list_my_pending(db, approver_id=1)

        assert total == 0
        assert items == []

    @pytest.mark.asyncio
    async def test_fallback_keeps_legacy_instances_without_task_rows(self):
        from app.services.approval import list_my_pending

        approver = self._make_approver()
        instance = SimpleNamespace(
            id=101,
            status="pending",
            flow_id=1,
            current_node_order=1,
            applicant_id=5,
            records=[],
            tasks=[],
        )
        node = SimpleNamespace(flow_id=1, node_order=1, approver_type="direct_manager", approver_id=None)

        approver_result = MagicMock()
        approver_result.scalar_one_or_none.return_value = approver
        no_user_task_result = MagicMock()
        no_user_task_result.scalars.return_value.all.return_value = []
        roles_result = MagicMock()
        roles_result.all.return_value = []
        pending_result = MagicMock()
        pending_result.scalars.return_value.all.return_value = [instance]
        nodes_result = MagicMock()
        nodes_result.scalars.return_value.all.return_value = [node]
        employee_result = MagicMock()
        employee_result.all.return_value = [(5, 1, None)]

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            approver_result,
            no_user_task_result,
            roles_result,
            pending_result,
            nodes_result,
            employee_result,
        ])

        total, items = await list_my_pending(db, approver_id=1)

        assert total == 1
        assert items == [instance]


class TestFlowModuleAliases:

    def test_flow_module_candidates_support_legacy_custom_alias(self):
        assert _flow_module_candidates("custom_template") == ("custom_template", "custom")
        assert _flow_module_candidates("custom") == ("custom", "custom_template")

    @pytest.mark.asyncio
    async def test_determine_flow_accepts_custom_template_alias(self):
        flow = SimpleNamespace(id=7, name="自定义模板流程", nodes=[SimpleNamespace(node_order=1)])
        captured = {}

        async def mock_execute(stmt):
            captured["params"] = stmt.compile().params
            result = MagicMock()
            result.scalar_one_or_none.return_value = flow
            return result

        db = AsyncMock()
        db.execute = mock_execute

        resolved = await _determine_flow(db, "custom_template")

        assert resolved is flow
        assert captured["params"]["module_1"] == ["custom_template", "custom"]
