"""
移动端审批中心聚合接口契约测试。
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app
from app.schemas.approval import ApprovalMobileCenterOut


def test_mobile_approval_center_route_registered_with_response_model():
    route = next(
        item
        for item in app.routes
        if getattr(item, "path", "") == "/api/v1/approval/mobile/center"
    )
    assert getattr(route, "response_model", None) is ApprovalMobileCenterOut


def test_mobile_approval_center_schema_contains_submitted_bucket():
    now = datetime(2026, 5, 8, 8, 0, tzinfo=timezone.utc)
    approval_item = {
        "id": 1,
        "template_version_id": None,
        "flow_id": 1,
        "applicant_id": 9,
        "module": "leave",
        "business_id": 1,
        "business_type": "leave",
        "summary": "年假申请",
        "form_data": {"reason": "家庭事务"},
        "flow_snapshot": None,
        "current_node_order": 1,
        "current_node_name": "直属主管",
        "current_node_status": "pending",
        "current_node_status_label": "待审批",
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }
    payload = {
        "pending": {"total": 0, "items": []},
        "processed": {"total": 0, "items": []},
        "submitted": {"total": 1, "items": [approval_item]},
        "cc": {"total": 0, "items": []},
        "counts": {"pending": 0, "processed": 0, "submitted": 1, "cc": 0},
        "submitted_status_counts": {
            "all": 1,
            "pending": 1,
            "approved": 0,
            "rejected": 0,
            "withdrawn": 0,
            "cancelled": 0,
        },
    }

    center = ApprovalMobileCenterOut.model_validate(payload)

    assert center.counts["submitted"] == 1
    assert center.submitted.total == 1
    assert center.submitted.items[0].summary == "年假申请"
