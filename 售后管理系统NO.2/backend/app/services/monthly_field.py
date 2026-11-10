"""Beijing attendance days are half-open intervals between consecutive cutoffs."""

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from app.models.attendance import AttendanceEffect
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.field_service import FieldProject, FieldProjectCatalog
from app.models.monthly_field import (
    MonthlyAbsence,
    MonthlyEligibility,
    MonthlyPolicy,
    MonthlyShift,
    MonthlyHoursReview,
    MonthlyPunch,
    MonthlyRest,
    MonthlyRestFact,
    ProjectConfirmation,
    ProjectRadius,
)
from app.services import monthly_rest as earned_rest
from app.services.monthly_shifts import evaluate
from app.services.field_roles import aftersales_person_ids
from app.services.legal_holidays import LegalHolidayService

DAY = timedelta(days=1)


def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


def boundary(day, rule):
    return datetime.combine(day, datetime.min.time()) + timedelta(minutes=rule.cutoff)


def rule_for(rules, day):
    return next((rule for rule in reversed(rules) if rule.effective <= day), None)


def work_day(rules, stamp):
    rule = rule_for(rules, stamp.date())
    if rule and stamp < boundary(stamp.date(), rule):
        return stamp.date() - DAY
    return stamp.date()


async def policies(db):
    return list(
        await db.scalars(select(MonthlyPolicy).order_by(MonthlyPolicy.effective))
    )


async def active(db):
    rules = await policies(db)
    return bool(rules and work_day(rules, now()) >= rules[0].effective)


async def locations(db):
    result = await db.execute(
        select(FieldProject, FieldProjectCatalog, ProjectRadius)
        .outerjoin(FieldProjectCatalog, FieldProjectCatalog.code == FieldProject.code)
        .outerjoin(ProjectRadius, ProjectRadius.code == FieldProject.code)
        .where(FieldProject.is_active.is_(True))
        .order_by(FieldProject.code)
    )
    places = [
        dict(
            code=p.code,
            name=c.name if c else p.name,
            address=c.address if c else "",
            latitude=c.latitude if c else None,
            longitude=c.longitude if c else None,
            radius=r.meters if r else 300,
        )
        for p, c, r in result
    ]
    # Retain valid manual fences already linked to project rules at cutover.
    from app.models.field_service import FieldSite
    from app.models.attendance import AttendanceRule
    from app.services.attendance import (
        AttendanceRecordService,
        _preserved_manual_locations,
    )

    by_code = {p["code"]: p for p in places}
    manual = await db.execute(
        select(FieldProject.code, AttendanceRule)
        .join(FieldSite, FieldSite.project_id == FieldProject.id)
        .join(AttendanceRule, AttendanceRule.id == FieldSite.rule_id)
        .where(FieldProject.is_active.is_(True), FieldSite.is_active.is_(True))
    )
    for code, rule in manual:
        place = by_code[code]
        fences = AttendanceRecordService._configured_locations(
            rule, rule.extra_config or {}
        )
        if place["latitude"] is not None:
            retained = _preserved_manual_locations(rule)

            def coords(item):
                return (str(item.get("latitude")), str(item.get("longitude")))

            allowed = {coords(item) for item in retained}
            fences = [f for f in fences if coords(f) in allowed]
        for fence in fences:
            extra = dict(
                place,
                address=fence["address"],
                latitude=float(fence["latitude"]),
                longitude=float(fence["longitude"]),
                radius=int(fence["radius"]),
            )
            if place["latitude"] is None:
                place.update(extra)
            elif not any(
                (p["code"], p["latitude"], p["longitude"])
                == (code, extra["latitude"], extra["longitude"])
                for p in places
            ):
                places.append(extra)
    return places


def day_result(
    day, person_punches, decision, rest, holiday, trip, start, end, stamp, minutes, used
):
    hits = {item["code"]: item for p in person_punches for item in p.matches}
    anomalies = []
    project = None
    if person_punches:
        latest = person_punches[-1]
        if len(hits) == 1:
            project = next(iter(hits.values()))
        elif decision and decision.punch_id == latest.id:
            project = hits.get(decision.code)
        status = "present" if project else "conflict"
        worked = sum(
            (b.at - a.at).total_seconds()
            for a, b in zip(person_punches, person_punches[1:])
            if a.kind == "in" and b.kind == "out"
        )
        if stamp >= end or latest.kind == "out":
            if latest.kind == "in":
                anomalies.append("缺下班卡")
            if worked < minutes * 60:
                anomalies.append("工时不足")
    elif trip:
        status = "trip"
    elif holiday:
        status = "holiday"
    elif rest and rest.status == "approved":
        status = "approved_rest"
    elif stamp < end:
        status = "waiting"
    else:
        status = "rest" if used < 4 else "absent"
    return dict(
        day=day,
        start=start,
        end=end,
        status=status,
        project=project,
        anomalies=anomalies,
        holiday=holiday,
        projects=list(hits.values()),
        confirmation=decision.code if decision else None,
        punches=[
            dict(id=p.id, at=p.at, kind=p.kind, matches=p.matches, photo=p.photo)
            for p in person_punches
        ],
        application=dict(
            id=rest.id,
            status=rest.status,
            reason=rest.reason,
            note=rest.note,
            reviewed=rest.reviewed,
            manager_id=rest.manager_id,
        )
        if rest
        else None,
    )


def full_day_trip(effect):
    from app.services.attendance_effects import (
        _attendance_binding,
        trip_covers_date,
        trip_period,
    )

    payload = effect.payload_json or {}
    marked = payload.get("effect_detail", {}).get("full_day")
    if marked is not None:
        return marked is True
    original = payload.get("approval_event")
    if original:
        start, end = trip_period(original, _attendance_binding(original))
        return trip_covers_date(start, end, effect.work_date)
    return bool(
        effect.start_at
        and effect.end_at
        and (effect.end_at - effect.start_at).total_seconds() >= 86399
    )


def opened_date(person):
    # Employee.created_at is UTC, including naive SQLite datetimes.
    from datetime import timezone

    stamp = person.created_at
    return (
        (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc))
        .astimezone(ZoneInfo("Asia/Shanghai"))
        .date()
    )


async def month_report(db, first, employee_id=None, stamp=None, persist=False):
    stamp = stamp or now()
    first = first.replace(day=1)
    last = first.replace(day=monthrange(first.year, first.month)[1])
    rules = await policies(db)
    if not rules:
        return []
    last = min(last, work_day(rules, stamp))
    if last < first:
        return []
    managers = select(EmployeeRole.employee_id).where(
        EmployeeRole.is_active.is_(True),
        EmployeeRole.role_name.in_(("aftersales_manager", "admin")),
    )
    current = (
        Employee.id.in_(aftersales_person_ids())
        & Employee.id.not_in(managers)
        & Employee.is_superuser.is_(False)
        & Employee.is_active.is_(True)
        & Employee.status.in_(("在职", "试用"))
    )
    people = (
        select(Employee)
        .where(
            current
            | Employee.id.in_(select(MonthlyEligibility.employee_id))
            | (
                (Employee.leave_date > first)
                & Employee.id.in_(aftersales_person_ids())
                & Employee.id.not_in(managers)
                & Employee.is_superuser.is_(False)
            )
        )
        .order_by(Employee.employee_no)
    )
    if employee_id is not None:
        people = people.where(Employee.id == employee_id)
    roster = list(await db.scalars(people))
    ids = [p.id for p in roster]

    earned_from = await earned_rest.effective(db)
    balances = await earned_rest.totals(db, ids, earned_from, last)

    def scope(model):
        return select(model).where(
            model.employee_id.in_(ids), model.day.between(first, last)
        )

    spent = {
        (f.employee_id, f.day)
        for f in await db.scalars(
            scope(MonthlyRestFact).where(MonthlyRestFact.rest_id.is_not(None))
        )
    }
    punches = defaultdict(list)
    for p in await db.scalars(
        scope(MonthlyPunch).order_by(MonthlyPunch.at, MonthlyPunch.id)
    ):
        punches[p.employee_id, p.day].append(p)
    shifts = {(s.employee_id, s.day): s for s in await db.scalars(scope(MonthlyShift))}
    hours_reviews = {
        r.punch_id: r
        for r in await db.scalars(
            select(MonthlyHoursReview).where(
                MonthlyHoursReview.punch_id.in_(
                    scope(MonthlyPunch).with_only_columns(MonthlyPunch.id)
                )
            )
        )
    }
    rests = {(r.employee_id, r.day): r for r in await db.scalars(scope(MonthlyRest))}
    confirmations = {
        r.punch_id: r
        for r in await db.scalars(
            select(ProjectConfirmation).where(
                ProjectConfirmation.punch_id.in_(
                    scope(MonthlyPunch).with_only_columns(MonthlyPunch.id)
                )
            )
        )
    }
    effects = await db.scalars(
        select(AttendanceEffect).where(
            AttendanceEffect.employee_id.in_(ids),
            AttendanceEffect.work_date.between(first, last),
            AttendanceEffect.effect_status == "active",
            AttendanceEffect.effect_type.in_(("leave", "business_trip")),
        )
    )
    trips, leave_minutes = set(), defaultdict(int)
    for effect in effects:
        key = (effect.employee_id, effect.work_date)
        if effect.effect_type == "leave":
            leave_minutes[key] += effect.minutes or 0
        elif full_day_trip(effect):
            trips.add(key)
    holidays = {
        entry.date: entry.holiday_name
        for entry in (await LegalHolidayService.get_year(db, first.year)).entries
    }
    absences = (
        {(a.employee_id, a.day): a for a in await db.scalars(scope(MonthlyAbsence))}
        if persist
        else {}
    )
    transitions = defaultdict(list)
    for transition in await db.scalars(
        select(MonthlyEligibility)
        .where(MonthlyEligibility.employee_id.in_(ids))
        .order_by(MonthlyEligibility.day)
    ):
        transitions[transition.employee_id].append(transition)
    results = []
    for person in roster:
        day = max(
            first,
            rules[0].effective,
            person.hire_date or first,
            opened_date(person) if person.created_at else first,
        )
        days, used, absent = [], 0, 0
        while day <= last and (not person.leave_date or day < person.leave_date):
            changes = transitions[person.id]
            if changes and not next(
                (e.eligible for e in reversed(changes) if e.day <= day), False
            ):
                day += DAY
                continue
            rule = rule_for(rules, day)
            start, end = (
                boundary(day, rule),
                boundary(day + DAY, rule_for(rules, day + DAY)),
            )
            key = (person.id, day)
            logs = punches[key]
            row = day_result(
                day,
                logs,
                confirmations.get(logs[-1].id) if logs else None,
                rests.get(key),
                holidays.get(day),
                key in trips,
                start,
                end,
                stamp,
                rule.minutes,
                used,
            )
            if (
                not logs
                and leave_minutes[key] >= rule.minutes
                and not (holidays.get(day) or key in trips)
            ):
                row["status"] = "leave"
            if shift := shifts.get(key):
                row.update(evaluate(shift, logs, stamp, hours_reviews.get(logs[-1].id)))
                if row["project"] or row["hours_status"] in {
                    "pending_hours",
                    "shortage",
                }:
                    row["status"] = row["hours_status"]
            accrual = earned_from is not None and day >= earned_from
            if accrual and row["status"] == "rest":
                row["status"] = "absent"
            if key in spent:
                row["status"] = "quota_rest"
            row["punch_count"] = len(logs)
            row["missing_punches"] = (
                2
                if row["status"] == "absent"
                else int(bool(logs and logs[-1].kind == "in" and stamp >= row["end"]))
            )
            used += row["status"] in {"rest", "quota_rest"}
            absent += row["status"] == "absent"
            if persist:
                existing = absences.get(key)
                if existing:
                    existing.active = row["status"] == "absent"
                elif row["status"] == "absent":
                    # Two dashboard reads or a settlement job may discover the same day.
                    from sqlalchemy.exc import IntegrityError

                    try:
                        async with db.begin_nested():
                            db.add(
                                MonthlyAbsence(
                                    employee_id=person.id, day=day, start=start, end=end
                                )
                            )
                            await db.flush()
                    except IntegrityError:
                        if not await db.get(MonthlyAbsence, key):
                            raise
            row.update(rest_used=used, absence_days=absent)
            days.append(row)
            day += DAY
        if days:
            results.append(
                dict(
                    employee_id=person.id,
                    employee_no=person.employee_no,
                    name=person.name,
                    earned_rest=bool(earned_from and last >= earned_from),
                    rest_balance=balances.get(person.id, (0, 0))[0],
                    work_progress=balances.get(person.id, (0, 0))[1],
                    punch_count=sum(r["punch_count"] for r in days),
                    missing_punches=sum(r["missing_punches"] for r in days),
                    leave_days=sum(
                        r["status"] in {"quota_rest", "rest", "approved_rest", "leave"}
                        for r in days
                    ),
                    pending_estimate_minutes=sum(
                        r.get("suggested_minutes") or 0
                        for r in days
                        if r.get("hours_status") == "pending_hours"
                    ),
                    pending_unknown_days=sum(
                        r.get("suggested_minutes") is None
                        for r in days
                        if r.get("hours_status") == "pending_hours"
                    ),
                    pending_hours_days=sum(
                        r.get("hours_status") == "pending_hours" for r in days
                    ),
                    rest_used=used,
                    absence_days=absent,
                    full_days=sum(
                        r.get("hours_status") in {"full", "normal"} for r in days
                    ),
                    shortage_days=sum(
                        r.get("hours_status") == "shortage" for r in days
                    ),
                    shortage_minutes=sum(
                        r["review"]["missing_minutes"]
                        for r in days
                        if r.get("hours_status") == "shortage"
                    ),
                    days=days,
                )
            )
    return results


async def enforce_routes(request, db, user):
    """Apply the same cutover to bookmarks, old clients and existing tokens."""
    path = request.url.path.removeprefix("/api/v1/")
    family, _, sub = path.partition("/")
    if family not in {
        "field",
        "rotation",
        "attendance",
        "approval",
        "rbac",
        "leave",
        "overtime",
        "auth",
    }:
        return
    if not await active(db):
        return
    from app.services.field_roles import role_names

    roles = await role_names(db, user)
    manages = "aftersales_manager" in roles
    admin = "admin" in roles
    read = request.method == "GET"
    if family == "auth" and sub == "management-session" and not (manages or admin):
        raise HTTPException(403, "请使用员工入口")
    if family == "field":
        allowed = (manages or admin) and (
            read
            or sub.startswith(("people", "positions", "roster/", "projects/import/"))
        )
    elif family == "rotation":
        allowed = read and (manages or admin or sub == "me/entitlement")
    elif family == "attendance":
        allowed = (read and (manages or admin or sub.startswith("maps/"))) or (
            admin and sub.startswith("legal-holidays/")
        )
    elif family == "approval" and (
        sub.endswith("/process")
        or sub in {"bulk-action", "transfer", "delegates"}
        and not read
    ):
        allowed = manages
    elif family in {"leave", "overtime"} and "approve" in sub and not read:
        allowed = manages
    elif (
        family == "rbac"
        and read
        and sub.startswith("employees/")
        and not (admin or manages)
    ):
        allowed = sub == f"employees/{user.id}/roles"
    else:
        return
    if not allowed:
        raise HTTPException(403, "当前业务使用月度考勤，请通过对应角色入口操作")


async def settle_closed_months(db):
    from app.models.system_setting import SystemSetting

    rules = await policies(db)
    if not rules:
        return
    last = work_day(rules, now()) - DAY
    cursor = await db.get(SystemSetting, "monthly_field_last_settled")
    first = date.fromisoformat(cursor.value) + DAY if cursor else rules[0].effective
    if first > last:
        return
    while first <= last:
        await month_report(db, first, persist=True)
        first = first.replace(day=monthrange(first.year, first.month)[1]) + DAY
    if not cursor:
        cursor = SystemSetting(
            name="monthly_field_last_settled", value=last.isoformat()
        )
        db.add(cursor)
    cursor.value = last.isoformat()


async def eligibility_change(db, employee, was_eligible, eligible):
    if was_eligible == eligible:
        return
    rules = await policies(db)
    if not rules:
        return
    day = max(rules[0].effective, work_day(rules, now()))
    if was_eligible and not await db.scalar(
        select(MonthlyEligibility.employee_id)
        .where(MonthlyEligibility.employee_id == employee.id)
        .limit(1)
    ):
        first = max(
            rules[0].effective,
            employee.hire_date or rules[0].effective,
            opened_date(employee),
        )
        if first < day:
            db.add(
                MonthlyEligibility(employee_id=employee.id, day=first, eligible=True)
            )
    await db.merge(
        MonthlyEligibility(employee_id=employee.id, day=day, eligible=eligible)
    )
