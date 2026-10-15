"""Read-only dashboard; count people once without changing attendance results."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import re

from fastapi import HTTPException
from sqlalchemy import select, or_

from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.attendance import AttendanceRecord
from app.models.field_service import FieldMembership, FieldAttendanceSegment, FieldAssignmentDay
from app.services.field_membership import utc, segment_view, clock_now

ANOMALY_LABELS = ("迟到", "早退", "缺卡", "定位 / WiFi", "待核对", "旷工", "其他异常")


def anomaly_labels(values):
    labels = set()
    status = values.get("status", "")
    status = getattr(status, "value", status)
    status_names = {"late": "迟到", "early_leave": "早退", "missed_clock": "缺卡", "absent": "旷工"}
    status = status_names.get(status, status)
    if status in ANOMALY_LABELS:
        labels.add(status)
    for part in re.split(r"[;；]", values.get("anomaly_type") or ""):
        if not part.strip():
            continue
        matched = {label for label in ("迟到", "早退", "旷工", "待核对") if label in part}
        if "缺卡" in part or "缺少" in part:
            matched.add("缺卡")
        if any(word in part.lower() for word in ("定位", "围栏", "gps", "wifi", "wi-fi")):
            matched.add("定位 / WiFi")
        labels.update(matched or {"其他异常"})
    if values.get("needs_review"):
        labels.update({"待核对", "缺卡"})
    return [label for label in ANOMALY_LABELS if label in labels]


def summarize(rows):
    people = defaultdict(list)
    abnormal = defaultdict(set)
    for row in rows:
        people[row["employee_id"]].append(row)
        for label in row["anomaly_labels"]:
            abnormal[label].add(row["employee_id"])
    punched = {person for person, parts in people.items() if any(r["clock_in_time"] or r["clock_out_time"] for r in parts)}
    complete = {person for person, parts in people.items() if all(r["clock_in_time"] and r["clock_out_time"] for r in parts)}
    return {
        "people": len(people), "punched": len(punched), "complete": len(complete),
        "partial": len(punched - complete), "unpunched": len(people) - len(punched),
        "abnormal": len(set().union(*abnormal.values())),
        "clocked_in": len({r["employee_id"] for r in rows if r["clock_in_time"]}),
        "clocked_out": len({r["employee_id"] for r in rows if r["clock_out_time"]}),
        "anomalies": [{"name": label, "value": len(abnormal[label])} for label in ANOMALY_LABELS],
    }


async def build_dashboard(db, user, day, project_id=None, include_trend=False):
    from app.services.field_service import directory, is_admin, as_dict, CST
    context = await directory(db, user)
    projects = [{"id": p["id"], "name": p["name"]} for p in context["projects"]]
    if project_id is not None and project_id not in {p["id"] for p in projects}:
        raise HTTPException(404, "项目不存在或无权查看")
    assignments = {a["id"]: a for a in context["assignments"] if project_id is None or a["project_id"] == project_id}
    first_day = day - timedelta(days=6) if include_trend else day
    start = datetime.combine(first_day, datetime.min.time(), CST).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), CST).astimezone(timezone.utc)
    intervals = list((await db.scalars(select(FieldMembership).where(
        FieldMembership.assignment_id.in_(assignments), FieldMembership.started_at < end,
        or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > start),
        or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > FieldMembership.started_at),
    ))).all())
    employee_ids = {m.employee_id for m in intervals}
    # Detect all segments of a daily rollup, including those in an unselected project.
    segments = list((await db.scalars(select(FieldAttendanceSegment).where(
        FieldAttendanceSegment.employee_id.in_(employee_ids),
        FieldAttendanceSegment.work_date.between(first_day, day),
    ))).all())
    segment_map = {(s.membership_id, s.work_date): s for s in segments}
    segmented_days = {(s.employee_id, s.work_date) for s in segments}
    records = list((await db.scalars(select(AttendanceRecord).where(
        AttendanceRecord.employee_id.in_(employee_ids), AttendanceRecord.date.between(first_day, day),
    ))).all())
    record_map = {(r.employee_id, r.date): r for r in records}
    legacy_days = set((await db.execute(select(FieldAssignmentDay.assignment_id, FieldAssignmentDay.work_date).where(
        FieldAssignmentDay.assignment_id.in_(assignments), FieldAssignmentDay.work_date.between(first_day, day),
    ))).all())
    by_day = {}
    for offset in range((day - first_day).days + 1):
        work_day = first_day + timedelta(days=offset)
        day_start = datetime.combine(work_day, datetime.min.time(), CST).astimezone(timezone.utc)
        day_end = day_start + timedelta(days=1)
        rows = []
        for interval in intervals:
            if utc(interval.started_at) >= day_end or (interval.ended_at and utc(interval.ended_at) <= day_start):
                continue
            assignment = assignments[interval.assignment_id]
            segment = segment_map.get((interval.id, work_day))
            legacy = record_map.get((interval.employee_id, work_day)) if (
                (interval.assignment_id, work_day) in legacy_days and (interval.employee_id, work_day) not in segmented_days
            ) else None
            values = segment_view(segment) if segment else as_dict(legacy) if legacy else {}
            labels = anomaly_labels(values)
            rows.append({**assignment, "work_date": work_day,
                "project_name": segment.project_name if segment else assignment["project_name"],
                "clock_in_time": values.get("clock_in_time"), "clock_out_time": values.get("clock_out_time"),
                "status": "待核对" if values.get("needs_review") else values.get("status", "未打卡"),
                "anomaly_type": "; ".join(filter(None, [values.get("anomaly_type"), "项目结束时缺卡，待核对" if values.get("needs_review") else None])) or ("、".join(labels) or None),
                "anomaly_labels": labels, "work_hours": values.get("work_hours"), "rule_name": values.get("rule_name"),
                "record_id": segment.attendance_record_id if segment else legacy.id if legacy else None,
            })
        by_day[work_day] = rows
    rows = by_day[day]
    ordinary_staff = select(Employee.id).where(
        Employee.status.in_(["在职", "试用", "试用期"]), Employee.is_superuser.is_(False),
        ~Employee.id.in_(select(EmployeeRole.employee_id).where(
            EmployeeRole.is_active.is_(True), EmployeeRole.role_name != "employee")),
    )
    roster = ordinary_staff
    if project_id is not None or not await is_admin(db, user):
        # Only current members of visible projects; never expose the whole roster to managers.
        now = clock_now()
        roster = roster.where(Employee.id.in_(select(FieldMembership.employee_id).where(
            FieldMembership.assignment_id.in_(assignments), FieldMembership.started_at <= now,
            or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > now),
        )))
    people_total = len((await db.scalars(roster)).all())
    per_project = defaultdict(list)
    for row in rows:
        per_project[row["project_id"]].append(row)
    project_stats = sorted([
        {"id": pid, "name": parts[0]["project_name"], **summarize(parts)} for pid, parts in per_project.items()
    ], key=lambda p: (-p["people"], p["id"]))
    # Staffing uses effective memberships, rather than attendance participation earlier today.
    snapshot = min(clock_now(), end - timedelta(microseconds=1))
    active_ids = set((await db.scalars(ordinary_staff)).all())
    staffing_members = defaultdict(dict)
    for interval in intervals:
        if interval.employee_id not in active_ids or utc(interval.started_at) > snapshot or (interval.ended_at and utc(interval.ended_at) <= snapshot):
            continue
        assignment = assignments[interval.assignment_id]
        staffing_members[assignment["project_id"]][interval.employee_id] = {"id": interval.employee_id, "name": assignment["employee_name"]}
    staffing = sorted([
        {**project, "members": list(staffing_members[project["id"]].values()), "people": len(staffing_members[project["id"]])}
        for project in projects if project_id is None or project["id"] == project_id
    ], key=lambda project: (-project["people"], project["id"]))
    return {
        "date": day, "people_total": people_total, "summary": summarize(rows),
        "staffing": staffing, "assigned_people": len({member["id"] for project in staffing for member in project["members"]}),
        "projects": projects, "project_stats": project_stats,
        "trend": [{"date": d, **summarize(parts)} for d, parts in by_day.items()] if include_trend else [],
        "updated_at": clock_now(), "rows": rows,
        # Retain the published segment counts for existing mobile clients.
        "total_scheduled": len(rows), "clocked_in": sum(bool(r["clock_in_time"]) for r in rows),
        "clocked_out": sum(bool(r["clock_out_time"]) for r in rows),
        "absent_count": sum(not r["clock_in_time"] for r in rows),
        "late_count": sum("迟到" in r["anomaly_labels"] for r in rows),
        "anomaly_count": sum(bool(r["anomaly_labels"]) for r in rows), "continuous_presence_enabled": False,
    }
