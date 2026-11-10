from datetime import date
from typing import Literal
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.employee import Employee
from app.models.field_service import FieldFile, FieldProject, FieldProjectCatalog
from app.models.monthly_field import (
    MonthlyPolicy,
    MonthlyShift,
    MonthlyHoursReview,
    MonthlyPunch,
    MonthlyRest,
    ProjectConfirmation,
    ProjectRadius,
)
from app.services import monthly_field as monthly
from app.services import monthly_shifts as shifts
from app.services.attendance import AttendanceRecordService
from app.services.field_roles import is_aftersales_person, role_names

router = APIRouter()


async def manager(user=Depends(get_current_user), db=Depends(get_db)):
    if "aftersales_manager" not in await role_names(db, user):
        raise HTTPException(403, "仅售后经理可管理月度考勤")
    return user


async def employee(user=Depends(get_current_user), db=Depends(get_db)):
    if not await is_aftersales_person(db, user.id) or await role_names(db, user) & {
        "admin",
        "aftersales_manager",
    }:
        raise HTTPException(403, "仅售后员工可使用本人考勤")
    return user


class PolicyInput(BaseModel):
    effective: date
    cutoff: int = Field(ge=0, lt=1440)
    minutes: int = Field(gt=0, le=1440)
    photo: bool


class LocationInput(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius: int = Field(default=300, ge=1, le=10000)
    address: str = Field(max_length=500)


class PunchInput(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    shift: Literal["day", "night"] | None = None
    attendance_day: date | None = None
    request_key: UUID
    kind: str = Field(pattern="^(in|out)$")
    photo: str = Field(default="", max_length=500)


class HoursDecision(BaseModel):
    punch_id: int
    outcome: Literal["normal", "shortage"]
    missing_minutes: int = Field(ge=0, le=720)
    reason: str = Field(min_length=1, max_length=500)


class RestInput(BaseModel):
    day: date
    reason: str = Field(min_length=1, max_length=500)


class Review(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=500)


class Resolution(BaseModel):
    punch_id: int
    code: str


@router.get("/context")
async def context(user=Depends(get_current_user), db=Depends(get_db)):
    rules = await monthly.policies(db)
    stamp = monthly.now()
    day = monthly.work_day(rules, stamp)
    rule = monthly.rule_for(rules, day)
    names = await role_names(db, user)
    shift = previous = resume = None
    if rule and rule.shifts:
        day, shift, previous, resume = await shifts.punch_context(db, user.id, stamp)
        rule = monthly.rule_for(rules, day)
    return dict(
        shifts=bool(rule and rule.shifts),
        shift=shift.kind if shift else None,
        next_kind="out" if previous and previous.kind == "in" else "in",
        resume_night_day=resume,
        day=day,
        active=bool(rule),
        manager="aftersales_manager" in names,
        field_employee=await is_aftersales_person(db, user.id)
        and not names & {"admin", "aftersales_manager"},
        minutes=rule.minutes if rule else None,
        photo=rule.photo if rule else False,
        policies=[
            dict(
                **PolicyInput.model_validate(r, from_attributes=True).model_dump(),
                shifts=r.shifts,
            )
            for r in rules
        ],
    )


@router.post("/policy")
async def save_policy(body: PolicyInput, user=Depends(manager), db=Depends(get_db)):
    today = monthly.now().date()
    rules = await monthly.policies(db)
    if body.effective < today or (rules and body.effective <= today):
        raise HTTPException(422, "首份规则可从今天开始，后续修改请选择未来日期")
    if any(r.effective == body.effective for r in rules):
        raise HTTPException(409, "该日已有规则")
    if body.minutes != shifts.REQUIRED_MINUTES or body.cutoff != 0:
        raise HTTPException(422, "白班和夜班均应出勤12小时，按北京时间上班日期归属")
    if any(r.effective > body.effective and not r.shifts for r in rules):
        raise HTTPException(409, "请在已安排的旧规则之后启用班次")
    db.add(MonthlyPolicy(**body.model_dump(), shifts=True))
    await db.flush()
    return body


@router.get("/places")
async def places(user=Depends(manager), db=Depends(get_db)):
    return await monthly.locations(db)


@router.put("/places/{code}")
async def save_place(
    code: str, body: LocationInput, user=Depends(manager), db=Depends(get_db)
):
    project = await db.scalar(
        select(FieldProject)
        .where(FieldProject.code == code, FieldProject.is_active.is_(True))
        .with_for_update()
    )
    if not project:
        raise HTTPException(404, "项目不存在")
    row = await db.get(FieldProjectCatalog, code) or FieldProjectCatalog(
        code=code, name=project.name
    )
    row.address, row.latitude, row.longitude = (
        body.address,
        body.latitude,
        body.longitude,
    )
    row.imported_by = user.id
    db.add(row)
    await db.merge(ProjectRadius(code=code, meters=body.radius))
    return {"code": code}


@router.post("/punch")
async def punch(body: PunchInput, user=Depends(employee), db=Depends(get_db)):
    await db.scalar(select(Employee.id).where(Employee.id == user.id).with_for_update())
    key = str(body.request_key)
    old = await db.scalar(
        select(MonthlyPunch).where(
            MonthlyPunch.employee_id == user.id, MonthlyPunch.request_key == key
        )
    )
    if old:
        return dict(id=old.id, day=old.day, matches=old.matches)
    stamp = monthly.now()
    rules = await monthly.policies(db)
    day = monthly.work_day(rules, stamp)
    rule = monthly.rule_for(rules, day)
    if not rule or (user.hire_date and day < user.hire_date):
        raise HTTPException(409, "尚未进入有效考勤日")
    shift = None
    if rule.shifts:
        day, shift, previous, _ = await shifts.punch_context(
            db, user.id, stamp, body.attendance_day
        )
        if body.attendance_day and body.attendance_day != day:
            raise HTTPException(409, "请先完成当前班次的下班卡")
        if not shift and not body.shift:
            raise HTTPException(422, "上班打卡前请选择白班或夜班")
        if shift and body.shift and shift.kind != body.shift:
            raise HTTPException(409, "本考勤日班次已锁定")
        rule = monthly.rule_for(rules, day)
    else:
        previous = await shifts.last_punch(db, user.id, day)
    if rule.photo or body.photo:
        file = await db.scalar(
            select(FieldFile.id).where(
                FieldFile.owner_id == user.id, FieldFile.path == body.photo
            )
        )
        if (
            not file
            or not body.photo.startswith("/uploads/attendance/")
            or PurePosixPath(body.photo).suffix.lower()
            not in {".jpg", ".jpeg", ".png", ".webp"}
        ):
            raise HTTPException(422, "请上传本人的打卡照片")
    matches = []
    for place in await monthly.locations(db):
        if place["latitude"] is None or place["longitude"] is None:
            continue
        distance = AttendanceRecordService._haversine_meters(
            body.latitude, body.longitude, place["latitude"], place["longitude"]
        )
        if distance <= place["radius"]:
            matches.append(dict(**place, distance=round(distance, 1)))
    # Multiple fences belonging to the same project count as one project.
    matches = list(
        {
            p["code"]: p
            for p in sorted(matches, key=lambda p: p["distance"], reverse=True)
        }.values()
    )
    if not matches:
        raise HTTPException(422, "不在有效项目范围内，请重新定位")
    expected = "out" if previous and previous.kind == "in" else "in"
    if body.kind != expected:
        raise HTTPException(409, "打卡状态已变化，请刷新后重试")
    if rule.shifts and shift is None:
        db.add(
            MonthlyShift(employee_id=user.id, day=day, started=stamp, kind=body.shift)
        )
    item = MonthlyPunch(
        employee_id=user.id,
        day=day,
        at=stamp,
        matches=matches,
        sequence=previous.sequence + 1 if previous else 1,
        **body.model_dump(exclude={"request_key", "shift", "attendance_day"}),
        request_key=key,
    )
    db.add(item)
    await db.flush()
    return dict(id=item.id, day=day, matches=matches)


@router.post("/hours/review")
async def review_hours(body: HoursDecision, user=Depends(manager), db=Depends(get_db)):
    target = await db.get(MonthlyPunch, body.punch_id)
    if not target:
        raise HTTPException(404, "打卡记录不存在")
    await db.scalar(
        select(Employee.id).where(Employee.id == target.employee_id).with_for_update()
    )
    shift = await db.get(MonthlyShift, (target.employee_id, target.day))
    logs = list(
        await db.scalars(
            select(MonthlyPunch)
            .where(
                MonthlyPunch.employee_id == target.employee_id,
                MonthlyPunch.day == target.day,
            )
            .order_by(MonthlyPunch.sequence)
        )
    )
    if not shift or logs[-1].id != target.id:
        raise HTTPException(409, "请刷新当日班次和打卡记录")
    existing = await db.get(MonthlyHoursReview, target.id)
    if (
        existing
        or shifts.evaluate(shift, logs, monthly.now(), None)["hours_status"]
        != "pending_hours"
    ):
        raise HTTPException(409, "当前没有待审批的工时异常")
    if not body.reason.strip() or (body.outcome == "shortage") != (
        body.missing_minutes > 0
    ):
        raise HTTPException(422, "请填写原因，并为缺工时填入有效分钟数")
    db.add(
        MonthlyHoursReview(
            punch_id=target.id,
            manager_id=user.id,
            at=monthly.now(),
            outcome=body.outcome,
            missing_minutes=body.missing_minutes,
            reason=body.reason.strip(),
        )
    )
    await db.flush()
    return {"outcome": body.outcome}


@router.get("/me")
async def self_report(month: date, user=Depends(employee), db=Depends(get_db)):
    return await monthly.month_report(db, month, user.id, persist=True)


@router.get("/report")
async def report(month: date, user=Depends(manager), db=Depends(get_db)):
    return await monthly.month_report(db, month, persist=True)


@router.post("/confirm")
async def confirm(body: Resolution, user=Depends(manager), db=Depends(get_db)):
    target = await db.get(MonthlyPunch, body.punch_id)
    if not target:
        raise HTTPException(404, "打卡记录不存在")
    await db.scalar(
        select(Employee.id).where(Employee.id == target.employee_id).with_for_update()
    )
    logs = list(
        await db.scalars(
            select(MonthlyPunch)
            .where(
                MonthlyPunch.employee_id == target.employee_id,
                MonthlyPunch.day == target.day,
            )
            .order_by(MonthlyPunch.id)
        )
    )
    codes = {p["code"] for log in logs for p in log.matches}
    if logs[-1].id != target.id or body.code not in codes or len(codes) < 2:
        raise HTTPException(409, "请刷新打卡明细并选择当日命中的项目")
    existing = await db.scalar(
        select(ProjectConfirmation).where(ProjectConfirmation.punch_id == target.id)
    )
    if existing and existing.code != body.code:
        raise HTTPException(409, "此记录已确认")
    if not existing:
        db.add(
            ProjectConfirmation(
                punch_id=target.id, code=body.code, manager_id=user.id, at=monthly.now()
            )
        )
    return {"code": body.code}


@router.post("/rest")
async def apply_rest(body: RestInput, user=Depends(employee), db=Depends(get_db)):
    rules = await monthly.policies(db)
    if (
        not monthly.rule_for(rules, body.day)
        or body.day < max(user.hire_date or body.day, monthly.opened_date(user))
        or not body.reason.strip()
    ):
        raise HTTPException(422, "请选择有效考勤日期并填写原因")
    row = MonthlyRest(
        employee_id=user.id,
        day=body.day,
        reason=body.reason.strip(),
        created=monthly.now(),
    )
    db.add(row)
    await db.flush()
    return {"id": row.id}


@router.get("/requests")
async def requests(user=Depends(manager), db=Depends(get_db)):
    rows = await db.execute(
        select(MonthlyRest, Employee.name)
        .join(Employee, Employee.id == MonthlyRest.employee_id)
        .order_by(MonthlyRest.created.desc())
    )
    return [
        dict(
            id=r.id, day=r.day, name=name, reason=r.reason, status=r.status, note=r.note
        )
        for r, name in rows
    ]


@router.post("/requests/{item_id}/review")
async def review(item_id: int, body: Review, user=Depends(manager), db=Depends(get_db)):
    row = await db.get(MonthlyRest, item_id)
    if not row:
        raise HTTPException(404, "申请不存在")
    result = await db.execute(
        update(MonthlyRest)
        .where(MonthlyRest.id == item_id, MonthlyRest.status == "pending")
        .values(
            status="approved" if body.approved else "rejected",
            manager_id=user.id,
            reviewed=monthly.now(),
            note=body.note,
        )
    )
    if result.rowcount != 1:
        raise HTTPException(409, "该申请已处理")
    await monthly.month_report(db, row.day, row.employee_id, persist=True)
    return {"id": item_id}


@router.get("/me/requests")
async def own_requests(user=Depends(employee), db=Depends(get_db)):
    rows = await db.scalars(
        select(MonthlyRest)
        .where(MonthlyRest.employee_id == user.id)
        .order_by(MonthlyRest.day.desc())
    )
    return [
        dict(day=r.day, reason=r.reason, status=r.status, note=r.note) for r in rows
    ]


@router.get("/history")
async def history(
    month: date,
    employee_id: int | None = None,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    """Read the old project clock, shifts and ledgers with the new scope."""
    from calendar import monthrange
    from datetime import datetime, timedelta
    from app.models.attendance import AttendanceRecord
    from app.models.rotation import RotationDay, RotationLedger
    from app.models.rotation_entitlement import RotationEntitlementLedger
    from app.services.field_roles import aftersales_person_ids

    ids = aftersales_person_ids()
    if "aftersales_manager" not in await role_names(db, user):
        if employee_id not in (None, user.id):
            raise HTTPException(403, "仅可查看本人历史记录")
        ids = ids.where(Employee.id == user.id)
    elif employee_id is not None:
        ids = ids.where(Employee.id == employee_id)
    first = month.replace(day=1)
    stop = month.replace(day=monthrange(month.year, month.month)[1]) + timedelta(days=1)
    rows = []
    for model, day_column, purpose in [
        (AttendanceRecord, AttendanceRecord.date, "打卡"),
        (RotationDay, RotationDay.work_date, "排班"),
        (RotationLedger, RotationLedger.created_at, "轮休流水"),
        (RotationEntitlementLedger, RotationEntitlementLedger.created_at, "额度流水"),
    ]:
        for record, name in await db.execute(
            select(model, Employee.name)
            .join(Employee, Employee.id == model.employee_id)
            .where(model.employee_id.in_(ids), day_column >= first, day_column < stop)
        ):
            when = getattr(record, day_column.key)
            if isinstance(record, AttendanceRecord):
                detail = f"{record.clock_in_time or '缺上班卡'} / {record.clock_out_time or '缺下班卡'}"
            elif isinstance(record, RotationDay):
                detail = f"{record.kind} · {record.reason}"
            else:
                detail = f"{record.action} · {record.delta_units / 2:g} 天"
            rows.append(
                dict(
                    day=when.date() if isinstance(when, datetime) else when,
                    name=name,
                    kind=purpose,
                    detail=detail,
                )
            )
    return sorted(rows, key=lambda r: r["day"], reverse=True)
