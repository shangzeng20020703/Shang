"""Manager-reviewed additions to the NO.2 punch timeline."""

from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.monthly_field import (
    MonthlyCorrection,
    MonthlyPunch,
    MonthlyRest,
    MonthlyShift,
    ProjectConfirmation,
)
from app.services import monthly_field as monthly, monthly_rest, monthly_shifts

DAY = timedelta(days=1)


class CorrectionInput(BaseModel):
    day: date
    shift: Literal["day", "night"]
    project_code: str | None = Field(default=None, max_length=50)
    check_in: datetime | None = None
    check_out: datetime | None = None
    reason: str = Field(min_length=1, max_length=500)


def record(row):
    return {
        k: getattr(row, k)
        for k in (
            "id",
            "day",
            "shift",
            "check_in",
            "check_out",
            "original",
            "projects",
            "reason",
            "status",
            "created",
            "manager_id",
            "reviewed",
            "note",
        )
    }


def missing_slots(row, shift_rule, stamp):
    if not shift_rule or stamp < row["end"]:
        return []
    if row["status"] in {"holiday", "leave", "trip", "quota_rest", "approved_rest"}:
        return []
    logs = row["punches"]
    slots = []
    if not logs or logs[0]["kind"] == "out":
        slots.append("in")
    if not logs or logs[-1]["kind"] == "in":
        slots.append("out")
    return slots


async def timeline(db, eid, day):
    return list(
        await db.scalars(
            select(MonthlyPunch)
            .where(MonthlyPunch.employee_id == eid, MonthlyPunch.day == day)
            .order_by(MonthlyPunch.at, MonthlyPunch.id)
            .with_for_update()
        )
    )


async def project_choices(db):
    return {
        p["code"]: {"code": p["code"], "name": p["name"]}
        for p in await monthly.locations(db)
    }


async def validate(db, eid, body, *, submitting):
    stamp = monthly.now()
    if submitting and not stamp.date() - 29 * DAY <= body.day <= stamp.date():
        raise HTTPException(422, "仅可申请最近30个自然日的缺卡")
    report = await monthly.month_report(db, body.day, eid, stamp)
    row = next((d for r in report for d in r["days"] if d["day"] == body.day), None)
    if not row or not row["missing_slots"]:
        raise HTTPException(409, "该日未结算、免卡或没有可补的卡")
    additions = {
        k: v
        for k, v in (("in", body.check_in), ("out", body.check_out))
        if v is not None
    }
    if set(additions) != set(row["missing_slots"]):
        raise HTTPException(409, "请补齐当日缺少的卡，已有打卡不能重复补录")
    if any(t.tzinfo is not None or t > stamp for t in additions.values()):
        raise HTTPException(422, "请填写已发生的北京时间")
    if row.get("shift") and row["shift"] != body.shift:
        raise HTTPException(409, "请沿用当天班次")
    logs = await timeline(db, eid, body.day)
    approved_rest = await db.scalar(
        select(MonthlyRest.id)
        .where(
            MonthlyRest.employee_id == eid,
            MonthlyRest.day == body.day,
            MonthlyRest.status == "approved",
        )
        .with_for_update()
    )
    if approved_rest:
        raise HTTPException(409, "当日已批准整天休假")
    current_slots = set()
    if not logs or logs[0].kind == "out":
        current_slots.add("in")
    if not logs or logs[-1].kind == "in":
        current_slots.add("out")
    if set(additions) != current_slots:
        raise HTTPException(409, "缺卡情况已变化，请刷新后核对")
    events = sorted(
        [(p.at, p.kind) for p in logs] + [(v, k) for k, v in additions.items()]
    )
    first, last = events[0][0], events[-1][0]
    if any(
        kind != ("in" if i % 2 == 0 else "out") or (i and when <= events[i - 1][0])
        for i, (when, kind) in enumerate(events)
    ):
        raise HTTPException(422, "补录时间与已有打卡顺序或时段冲突")
    if first.date() != body.day or (body.shift == "day" and last.date() != body.day):
        raise HTTPException(422, "白班限当日，夜班归属首次上班日期")
    if body.shift == "night" and (last.date() != body.day + DAY or last > first + DAY):
        raise HTTPException(422, "夜班下班须在次日且不超过上班后24小时")
    if body.shift == "night" and stamp < first + DAY:
        raise HTTPException(409, "夜班尚未到结算时间")
    # Adjacent attendance days may have a night shift across midnight.
    for adjacent in (body.day - DAY, body.day + DAY):
        others = await timeline(db, eid, adjacent)
        if not others:
            continue
        other_shift = await db.get(MonthlyShift, (eid, adjacent))
        for i, p in enumerate(others):
            if p.kind != "in":
                if any(a[0] < p.at < b[0] for a, b in zip(events[::2], events[1::2])):
                    raise HTTPException(409, "与相邻考勤日的打卡重叠")
                continue
            finish = (
                others[i + 1].at
                if i + 1 < len(others)
                else monthly_shifts.deadline(other_shift) if other_shift else p.at
            )
            if any(
                a[0] < finish and p.at < b[0] for a, b in zip(events[::2], events[1::2])
            ):
                raise HTTPException(409, "与相邻考勤日的出勤时段重叠")
    if logs:
        projects = [row["project"]] if row["project"] else row["projects"]
        projects = [{"code": p["code"], "name": p["name"]} for p in projects]
    elif submitting:
        project = (await project_choices(db)).get(body.project_code)
        if not project:
            raise HTTPException(422, "请选择申报项目")
        projects = [project]
    else:
        projects = body.projects
    return logs, projects


async def apply(db, eid, body):
    await monthly_rest.lock_employee(db, eid)
    if not body.reason.strip():
        raise HTTPException(422, "请填写实际补卡原因")
    pending = await db.scalar(
        select(MonthlyCorrection.id)
        .where(
            MonthlyCorrection.employee_id == eid,
            MonthlyCorrection.day == body.day,
            MonthlyCorrection.status == "pending",
        )
        .with_for_update()
    )
    if pending:
        raise HTTPException(409, "该日已有待审批的补卡申请")
    logs, projects = await validate(db, eid, body, submitting=True)
    item = MonthlyCorrection(
        employee_id=eid,
        day=body.day,
        shift=body.shift,
        check_in=body.check_in,
        check_out=body.check_out,
        reason=body.reason.strip(),
        projects=projects,
        original=[{"kind": p.kind, "at": p.at.isoformat()} for p in logs],
        created=monthly.now(),
        status="pending",
    )
    db.add(item)
    await db.flush()
    return record(item)


async def review(db, ident, manager_id, approved, note):
    eid = await db.scalar(
        select(MonthlyCorrection.employee_id).where(MonthlyCorrection.id == ident)
    )
    if eid is None:
        raise HTTPException(404, "补卡申请不存在")
    await monthly_rest.lock_employee(db, eid)
    item = await db.get(MonthlyCorrection, ident, with_for_update=True)
    if item.status != "pending":
        raise HTTPException(409, "该事项已处理")
    if not approved and not note.strip():
        raise HTTPException(422, "请填写驳回原因")
    stamp = monthly.now()
    if approved:
        logs, projects = await validate(db, eid, item, submitting=False)
        if item.original != [{"kind": p.kind, "at": p.at.isoformat()} for p in logs]:
            raise HTTPException(409, "申请后打卡记录已变化，请核对后重新申请")
        if not logs:
            db.add(
                MonthlyShift(
                    employee_id=eid,
                    day=item.day,
                    kind=item.shift,
                    started=item.check_in,
                )
            )
        elif item.check_in:
            shift = await db.get(MonthlyShift, (eid, item.day))
            shift.started = item.check_in
        prior = (
            await db.get(ProjectConfirmation, max(p.id for p in logs)) if logs else None
        )
        for kind, when, seq in (
            ("in", item.check_in, logs[0].sequence - 1 if logs else 1),
            ("out", item.check_out, logs[-1].sequence + 1 if logs else 2),
        ):
            if when is None:
                continue
            latest = MonthlyPunch(
                employee_id=eid,
                day=item.day,
                at=when,
                kind=kind,
                sequence=seq,
                request_key=f"correction:{ident}:{kind}",
                matches=projects,
                latitude=None,
                longitude=None,
                photo="",
            )
            db.add(latest)
            await db.flush()
        if prior:
            db.add(
                ProjectConfirmation(
                    punch_id=latest.id,
                    code=prior.code,
                    manager_id=prior.manager_id,
                    at=prior.at,
                )
            )
        await db.flush()
        from app.services.manager_inbox import sync_punch

        await monthly_rest.sync_work(db, latest, stamp)
        await sync_punch(db, eid, item.day)
    item.status = "approved" if approved else "rejected"
    item.manager_id, item.reviewed, item.note = manager_id, stamp, note.strip()
    await db.flush()
    if approved:
        await monthly.month_report(db, item.day, eid, stamp, persist=True)
    return record(item)
