"""Shared admission checks for attendance-related dynamic forms."""

from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select
from app.models.employee import Employee
from app.models.approval import ApprovalInstance
from app.schemas.attendance import PunchCorrectionEligibilityRequest


async def validate_correction(db, applicant_id, form_data):
    from app.services.attendance_effects import _dynamic_punch_correction_data
    from app.services.attendance import PunchCorrectionEligibilityService

    parsed = _dynamic_punch_correction_data(
        {"applicant_id": applicant_id, "form_data": form_data}, {}
    )
    if not parsed:
        raise HTTPException(422, "补卡表单必须包含日期、上/下班类型和补卡时间")
    if parsed["employee_id"] != applicant_id:
        raise HTTPException(403, "只能为本人申请补卡")
    employee = await db.get(Employee, applicant_id)
    result = await PunchCorrectionEligibilityService.calculate(
        db,
        PunchCorrectionEligibilityRequest(
            employee_id=applicant_id,
            target_date=parsed["work_date"].isoformat(),
            punch_types=[parsed["punch_type"]],
            include_window_days=False,
        ),
        target_date=parsed["work_date"],
        target_employee=employee,
    )
    if not result.get("data", {}).get("can_apply"):
        raise HTTPException(400, result.get("message") or "当前不能补卡")
    slot = next(
        (
            p
            for day in result["data"]["days"]
            for shift in day["shifts"]
            for p in shift["patchable_punches"]
            if p["punch_type"] == parsed["punch_type"] and p["can_apply"]
        ),
        None,
    )
    if not slot:
        raise HTTPException(400, "该上下班卡不允许补卡")
    window = slot.get("allowed_punch_time") or {}
    at = parsed["punch_time"]
    for key, valid in [
        ("start", lambda bound: at >= bound),
        ("end", lambda bound: at <= bound),
    ]:
        if window.get(key) and not valid(datetime.fromisoformat(window[key])):
            raise HTTPException(400, "补卡时间超出班次允许范围")
    # The original eligibility calculator only reads legacy corrections; include dynamic ones.
    pending = (
        await db.scalars(
            select(ApprovalInstance).where(
                ApprovalInstance.applicant_id == applicant_id,
                ApprovalInstance.status.in_(["pending", "approved"]),
                ApprovalInstance.module.in_(
                    ["punch_correction", "attendance_punch_correction"]
                ),
            )
        )
    ).all()
    dynamic_month_count = 0
    for instance in pending:
        old = _dynamic_punch_correction_data(
            {"applicant_id": applicant_id, "form_data": instance.form_data or {}}, {}
        )
        if old and (old["work_date"].year, old["work_date"].month) == (
            parsed["work_date"].year,
            parsed["work_date"].month,
        ):
            dynamic_month_count += 1
        if (
            instance.status == "pending"
            and old
            and old["work_date"] == parsed["work_date"]
            and old["punch_type"] == parsed["punch_type"]
        ):
            raise HTTPException(409, "该日期卡点已有待审批的补卡申请")
    remaining = result["data"]["policy"].get("remaining_count_in_month")
    if remaining is not None and dynamic_month_count >= remaining:
        raise HTTPException(400, "本月补卡次数已用完")
