from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import get_db
from app.core.deps import get_current_user
from app.main import app
from app.models.approval import (
    ApprovalFlow,
    ApprovalFormField,
    ApprovalInstance,
    ApprovalNode,
    ApprovalRecord,
    ApprovalTask,
    ApprovalTemplateVersion,
    ApprovalType,
)
from app.models.attendance import AttendanceMonthSummary, AttendanceRecord, AttendanceStatus
from app.models.notification import Notification
from app.schemas.approval import ApprovalActionRequest
from app.services import approval as approval_service


@pytest.mark.asyncio
async def test_control_library_covers_screenshot_controls_and_validates_common_payloads():
    definitions = approval_service.list_template_control_definitions()
    labels = {item["label"] for item in definitions}
    assert {
        "分栏",
        "单行输入框",
        "多行输入框",
        "数字输入框",
        "金额",
        "公式",
        "日期",
        "日期区间",
        "日期时间",
        "时长",
        "单选框",
        "下拉选择",
        "多选框",
        "级联/分类",
        "成员",
        "部门",
        "公司",
        "地点",
        "图片",
        "附件",
        "手写签名",
        "身份证",
        "外部联系人",
        "行业通讯录部门",
        "省市区",
        "评分",
        "发票",
        "客户",
        "关联申请单",
        "明细",
        "说明文字",
        "电话",
        "收款账户",
        "预算申请",
        "关联合同",
        "工程项目",
    }.issubset(labels)
    select_definition = next(item for item in definitions if item["field_type"] == "select")
    assert select_definition["supports_options"] is True
    assert select_definition["default_options"] == ["选项1", "选项2"]
    detail_definition = next(item for item in definitions if item["field_type"] == "detail")
    assert detail_definition["business_calculation"] is True
    assert detail_definition["default_options"]["summary"]["enabled"] is True

    fields = [
        SimpleNamespace(code="radio_choice", field_type="radio", is_required=True, options_json={"options": ["同意", "拒绝"]}),
        SimpleNamespace(code="project_name", field_type="select", is_required=True, options_json={"options": ["项目一", "项目二"]}),
        SimpleNamespace(code="checkbox_choice", field_type="checkbox", is_required=True, options_json={"options": ["A", "B"]}),
        SimpleNamespace(code="category", field_type="cascade", is_required=True, options_json={"options": ["分类一", "分类二"]}),
        SimpleNamespace(code="open_select", field_type="select", is_required=True, options_json=None),
        SimpleNamespace(code="date_range", field_type="date_range", is_required=True, options_json=None),
        SimpleNamespace(code="company", field_type="company", is_required=True, options_json=None),
        SimpleNamespace(code="identity", field_type="identity_card", is_required=True, options_json=None),
        SimpleNamespace(code="rating", field_type="rating", is_required=True, options_json=None),
    ]

    await approval_service.validate_form_payload(
        fields,
        {
            "radio_choice": "同意",
            "project_name": "项目二",
            "checkbox_choice": ["A", "B"],
            "category": "分类一",
            "open_select": "项目一",
            "date_range": {"start": "2026-04-20", "end": "2026-04-21"},
            "company": 2,
            "identity": "110101199001011234",
            "rating": 5,
        },
    )


@pytest.mark.asyncio
async def test_template_config_create_then_readback_persists_rules_and_flow(tmp_path: Path):
    db_path = tmp_path / "approval-template-config.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    ApprovalType.__table__.create(bind=engine)
    ApprovalFormField.__table__.create(bind=engine)
    ApprovalFlow.__table__.create(bind=engine)
    ApprovalNode.__table__.create(bind=engine)
    ApprovalTemplateVersion.__table__.create(bind=engine)

    class AsyncLikeSession:
        def __init__(self, sync_session: Session):
            self._sync = sync_session

        def add(self, obj):
            self._sync.add(obj)

        def add_all(self, objects):
            self._sync.add_all(objects)

        async def execute(self, *args, **kwargs):
            return self._sync.execute(*args, **kwargs)

        async def scalar(self, *args, **kwargs):
            return self._sync.scalar(*args, **kwargs)

        async def flush(self):
            self._sync.flush()

        async def refresh(self, obj):
            self._sync.refresh(obj)

        async def delete(self, obj):
            self._sync.delete(obj)

        async def commit(self):
            self._sync.commit()

        async def rollback(self):
            self._sync.rollback()

        async def close(self):
            self._sync.close()

        def __getattr__(self, name):
            return getattr(self._sync, name)

    async def override_get_db():
        session = session_factory()
        wrapped = AsyncLikeSession(session)
        try:
            yield wrapped
            await wrapped.commit()
        except Exception:
            await wrapped.rollback()
            raise
        finally:
            await wrapped.close()

    async def override_get_current_user():
        return SimpleNamespace(id=1, is_superuser=True, is_active=True)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    payload = {
        "name": "可视化请假模板",
        "business_code": "leave_visual",
        "category": "hr",
        "scope": "mobile",
        "is_active": True,
        "sort_order": 1,
        "permission_rules": {
            "visible_scope": {
                "type": "departments",
                "department_ids": [3],
                "include_sub_departments": True,
            },
            "notify_members": {"enabled": True, "member_ids": [21, 22]},
            "submit_permission": {"type": "roles", "roles": ["employee", "manager"]},
            "view_permission": {"type": "submitter_and_approvers"},
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
                "assistant_admin_ids": [61],
                "management_scope": {"type": "departments", "department_ids": [3]},
                "permissions": {
                    "application_records": True,
                    "template_settings": True,
                    "rule_settings": True,
                },
            },
        },
        "exception_rules": {
            "approval_empty_member": {"action": "transfer_to_specific", "member_id": 51},
            "handler_empty_member": {"action": "transfer_to_admin"},
            "approval_member_changed": {"action": "remind_admin_handover"},
            "handler_member_changed": {"action": "auto_transfer", "member_id": 52},
        },
        "auto_approval_rule": {"duplicate_approver_policy": "continuous_only"},
        "fields": [
            {"label": "请假类型", "code": "leave_type", "field_type": "select", "is_required": True},
            {"label": "开始时间", "code": "start_time", "field_type": "date", "is_required": True},
            {"label": "结束时间", "code": "end_time", "field_type": "date", "is_required": True},
        ],
        "flow_nodes": [
            {"node_order": 1, "node_type": "approval", "approver_type": "direct_manager"},
            {"node_order": 2, "node_type": "approval", "approver_type": "hr"},
        ],
    }

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            create_response = await client.post("/api/v1/approval/template-configs", json=payload)
            assert create_response.status_code == 201
            created = create_response.json()
            created_id = created["id"]

            read_response = await client.get(f"/api/v1/approval/types/{created_id}")
            assert read_response.status_code == 200
            readback = read_response.json()
            assert readback["id"] == created_id
            assert readback["permission_rules"]["submit_permission"]["type"] == "roles"
            assert readback["exception_rules"]["handler_member_changed"]["action"] == "auto_transfer"
            assert readback["auto_approval_rule"]["duplicate_approver_policy"] == "continuous_only"
            assert {item["code"] for item in readback["form_fields"]} >= {"leave_type", "start_time", "end_time"}

            duplicate_response = await client.post(
                "/api/v1/approval/template-configs",
                json={**payload, "name": "重复请假模板"},
            )
            assert duplicate_response.status_code == 409

        with session_factory() as verify_session:
            saved_type = verify_session.get(ApprovalType, created_id)
            assert saved_type is not None
            assert saved_type.permission_rules["notify_members"]["member_ids"] == [21, 22]
            assert saved_type.exception_rules["approval_empty_member"]["member_id"] == 51
            assert saved_type.auto_approval_rule["duplicate_approver_policy"] == "continuous_only"

            version_result = verify_session.execute(
                select(ApprovalTemplateVersion).where(ApprovalTemplateVersion.approval_type_id == created_id)
            )
            versions = list(version_result.scalars().all())
            assert versions, "template version snapshot should be created"
            snapshot = versions[-1].snapshot_json
            assert snapshot["permission_rules"]["submit_permission"]["roles"] == ["employee", "manager"]

            flow_result = verify_session.execute(
                select(ApprovalFlow).where(ApprovalFlow.module == "leave_visual")
            )
            flow = flow_result.scalar_one_or_none()
            assert flow is not None

            nodes_result = verify_session.execute(
                select(ApprovalNode).where(ApprovalNode.flow_id == flow.id).order_by(ApprovalNode.node_order.asc())
            )
            nodes = list(nodes_result.scalars().all())
            assert [node.node_order for node in nodes] == [1, 2]
            assert [node.approver_type for node in nodes] == ["direct_manager", "hr"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        engine.dispose()


@pytest.mark.asyncio
async def test_business_trip_template_publishes_and_approved_trip_syncs_attendance(tmp_path: Path):
    db_path = tmp_path / "business-trip-approval.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    for table in (
        ApprovalType.__table__,
        ApprovalFormField.__table__,
        ApprovalFlow.__table__,
        ApprovalNode.__table__,
        ApprovalTemplateVersion.__table__,
        ApprovalInstance.__table__,
        ApprovalRecord.__table__,
        ApprovalTask.__table__,
        Notification.__table__,
        AttendanceRecord.__table__,
        AttendanceMonthSummary.__table__,
    ):
        table.create(bind=engine)

    class AsyncLikeSession:
        def __init__(self, sync_session: Session):
            self._sync = sync_session

        def add(self, obj):
            self._sync.add(obj)

        def add_all(self, objects):
            self._sync.add_all(objects)

        async def execute(self, *args, **kwargs):
            return self._sync.execute(*args, **kwargs)

        async def flush(self):
            self._sync.flush()

        async def refresh(self, obj):
            self._sync.refresh(obj)

        async def delete(self, obj):
            self._sync.delete(obj)

        def __getattr__(self, name):
            return getattr(self._sync, name)

    fields = [
        {"label": "部门", "code": "department", "field_type": "department", "is_required": True, "print_visible": True},
        {
            "label": "项目名称",
            "code": "project_name",
            "field_type": "select",
            "is_required": True,
            "print_visible": True,
            "options_json": {"options": ["项目一", "项目二", "项目三"]},
        },
        {"label": "出差事由", "code": "trip_reason", "field_type": "textarea", "is_required": True, "print_visible": True},
        {"label": "出差地点", "code": "trip_location", "field_type": "location", "is_required": True, "print_visible": True},
        {
            "label": "出差时长",
            "code": "trip_duration",
            "field_type": "duration",
            "is_required": True,
            "print_visible": True,
            "is_business_calculation": True,
            "is_readonly": True,
            "options_json": {"duration_mode": "natural_day", "unit_hours": 24, "attendance_sync": True},
        },
        {
            "label": "明细",
            "code": "trip_expense_detail",
            "field_type": "detail",
            "is_required": True,
            "print_visible": True,
            "is_business_calculation": True,
            "options_json": {
                "child_fields": [
                    {"label": "机票", "code": "flight_ticket", "field_type": "amount", "is_required": True},
                    {"label": "火车票", "code": "train_ticket", "field_type": "amount", "is_required": True},
                    {"label": "住宿", "code": "lodging", "field_type": "amount", "is_required": True},
                    {"label": "出差补助", "code": "trip_allowance", "field_type": "amount", "is_required": True},
                    {"label": "市内交通", "code": "local_transport", "field_type": "amount", "is_required": True},
                ],
                "summary": {
                    "enabled": True,
                    "label": "预计出差费用合计",
                    "field_codes": ["flight_ticket", "train_ticket", "lodging", "trip_allowance", "local_transport"],
                    "value_type": "amount",
                },
            },
        },
    ]

    with session_factory() as sync_session:
        db = AsyncLikeSession(sync_session)
        template = await approval_service.save_template_config(
            db,
            {
                "name": "出差",
                "business_code": "business_trip",
                "category": "人事",
                "scope": "mobile",
                "is_active": True,
                "auto_approval_rule": {
                    "attendance_sync": {"enabled": True, "duration_mode": "natural_day", "status": "business_trip"}
                },
                "fields": fields,
                "flow_nodes": [{"node_order": 1, "node_type": "approval", "approver_type": "hr"}],
            },
        )
        assert template.status == "enabled"
        assert template.business_code == "business_trip"
        trip_duration = next(field for field in template.form_fields if field.code == "trip_duration")
        assert trip_duration.is_business_calculation is True
        assert trip_duration.options_json["duration_mode"] == "natural_day"
        project_name = next(field for field in template.form_fields if field.code == "project_name")
        assert project_name.options_json["options"] == ["项目一", "项目二", "项目三"]
        trip_detail = next(field for field in template.form_fields if field.code == "trip_expense_detail")
        assert trip_detail.is_business_calculation is True
        assert trip_detail.options_json["summary"]["label"] == "预计出差费用合计"
        assert {child["code"] for child in trip_detail.options_json["child_fields"]} >= {
            "flight_ticket",
            "train_ticket",
            "lodging",
            "trip_allowance",
            "local_transport",
        }

        flow = sync_session.execute(select(ApprovalFlow).where(ApprovalFlow.module == "business_trip")).scalar_one()
        instance = ApprovalInstance(
            flow_id=flow.id,
            module="business_trip",
            business_id=0,
            business_type="business_trip",
            summary="出差",
            form_data={
                "trip_duration": 3,
                "trip_duration_start_time": "2026-04-20",
                "trip_duration_end_time": "2026-04-22",
            },
            applicant_id=101,
            flow_snapshot='{"nodes":[{"node_order":1,"approver_type":"hr","node_type":"approval"}]}',
            current_node_order=1,
            status="pending",
        )
        sync_session.add(instance)
        sync_session.flush()
        sync_session.add(
            ApprovalTask(
                instance_id=instance.id,
                node_order=1,
                approver_id=201,
                status="pending",
            )
        )
        sync_session.add(
            AttendanceMonthSummary(
                employee_id=101,
                year=2026,
                month=4,
                absent_count=1,
            )
        )
        sync_session.flush()

        await approval_service.process_approval(
            db,
            instance_id=instance.id,
            approver_id=201,
            data=ApprovalActionRequest(action="approve", comment="同意"),
        )
        sync_session.commit()

        records = list(
            sync_session.execute(
                select(AttendanceRecord).where(AttendanceRecord.employee_id == 101).order_by(AttendanceRecord.date)
            )
            .scalars()
            .all()
        )
        assert [record.date.isoformat() for record in records] == ["2026-04-20", "2026-04-21", "2026-04-22"]
        assert all(record.status == AttendanceStatus.business_trip for record in records)
        summary = sync_session.execute(
            select(AttendanceMonthSummary).where(
                AttendanceMonthSummary.employee_id == 101,
                AttendanceMonthSummary.year == 2026,
                AttendanceMonthSummary.month == 4,
            )
        ).scalar_one()
        assert str(summary.business_trip_days) == "3.0"
