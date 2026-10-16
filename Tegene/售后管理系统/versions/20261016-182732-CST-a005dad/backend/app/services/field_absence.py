"""Freeze each application's daily project attribution at submission time."""

from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.models.field_absence import FieldAbsenceDay

CST = timezone(timedelta(hours=8))


def _calendar_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(CST).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(CST).date() if parsed.tzinfo else parsed.date()
    except ValueError:
        return None


def form_date_range(form: dict) -> tuple[date, date] | None:
    """Accept the published leave/travel form field names, including duration widgets."""
    duration = form.get("trip_duration") if isinstance(form.get("trip_duration"), dict) else {}
    start = next((form.get(key) for key in (
        "start_time", "start_date", "trip_start_time", "trip_start_date",
        "trip_duration_start_time", "trip_duration_start_date",
    ) if form.get(key)), None) or duration.get("start") or duration.get("start_time") or duration.get("start_date")
    end = next((form.get(key) for key in (
        "end_time", "end_date", "trip_end_time", "trip_end_date",
        "trip_duration_end_time", "trip_duration_end_date",
    ) if form.get(key)), None) or duration.get("end") or duration.get("end_time") or duration.get("end_date")
    first, last = _calendar_date(start), _calendar_date(end)
    if first is None or last is None:
        return None
    return first, last


async def capture_absence_days(db, source_type: str, source_id: int,
                               employee_id: int, first: date, last: date) -> list[dict]:
    if last < first or (last - first).days > 3660:
        raise HTTPException(status_code=422, detail="申请日期范围无效")
    existing = (await db.scalars(select(FieldAbsenceDay).where(
        FieldAbsenceDay.source_type == source_type,
        FieldAbsenceDay.source_id == source_id,
    ).order_by(FieldAbsenceDay.work_date))).all()
    if existing:
        return [snapshot(item) for item in existing]

    from app.services.field_membership import project_on

    rows = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        project_row = await project_on(db, employee_id, day)
        site = project_row[2] if project_row else None
        project = project_row[3] if project_row else None
        item = FieldAbsenceDay(
            source_type=source_type, source_id=source_id, employee_id=employee_id,
            work_date=day, project_id=project.id if project else None,
            project_name=project.name if project else None,
            site_id=site.id if site else None, rule_id=site.rule_id if site else None,
        )
        db.add(item)
        rows.append(item)
    await db.flush()
    return [snapshot(item) for item in rows]


def snapshot(item: FieldAbsenceDay) -> dict:
    return {"date": item.work_date.isoformat(), "project_id": item.project_id,
            "project_name": item.project_name, "site_id": item.site_id,
            "rule_id": item.rule_id}
