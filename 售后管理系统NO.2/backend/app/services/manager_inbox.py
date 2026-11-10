"""Outstanding decisions for the NO.2 aftersales manager."""

from collections import defaultdict
from datetime import timezone
from zoneinfo import ZoneInfo
from sqlalchemy import func, or_, select, tuple_

from app.models.approval import ApprovalInstance
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.leave import ApprovalStatus, LeaveRequest
from app.models.monthly_field import (
    MonthlyCorrection,
    MonthlyHoursReview,
    MonthlyPunch,
    MonthlyPunchTodo,
    MonthlyRest,
    MonthlyShift,
    ProjectConfirmation,
)
from app.services import approval, monthly_field, monthly_rest, monthly_shifts
from app.services.field_roles import aftersales_person_ids


def local_time(value):
    return (
        (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value)
        .astimezone(ZoneInfo("Asia/Shanghai"))
        .replace(tzinfo=None)
    )


async def sync_punch(db, employee_id, day):
    key = employee_id, day
    logs = list(
        await db.scalars(
            select(MonthlyPunch)
            .where(MonthlyPunch.employee_id == employee_id, MonthlyPunch.day == day)
            .order_by(MonthlyPunch.sequence)
            .with_for_update()
        )
    )
    latest = max(logs, key=lambda p: p.id)
    shift = await db.get(MonthlyShift, key)
    hours_due = project_due = None
    if shift and not await db.get(MonthlyHoursReview, latest.id, with_for_update=True):
        deadline = monthly_shifts.deadline(shift)
        if (
            monthly_shifts.evaluate(shift, logs, deadline, None)["hours_status"]
            == "pending_hours"
        ):
            hours_due = deadline
    confirmed = set(
        await db.scalars(
            select(ProjectConfirmation.punch_id)
            .where(ProjectConfirmation.punch_id.in_([p.id for p in logs]))
            .with_for_update()
        )
    )
    codes = set()
    for p in sorted(logs, key=lambda p: p.id):
        codes.update(m["code"] for m in p.matches)
        if p.id in confirmed:
            project_due = None
        elif len(codes) > 1 and project_due is None:
            project_due = p.at
    row = await db.get(MonthlyPunchTodo, key, with_for_update=True)
    if hours_due or project_due:
        if row is None:
            row = MonthlyPunchTodo(employee_id=employee_id, day=day)
            db.add(row)
        row.punch_id, row.hours_due, row.project_due = latest.id, hours_due, project_due
    elif row:
        await db.delete(row)


async def backfill(db):
    pairs = await db.execute(
        select(MonthlyPunch.employee_id, MonthlyPunch.day).distinct()
    )
    for eid, day in pairs:
        await sync_punch(db, eid, day)


def item(kind, ident, name, number, since, **detail):
    return dict(
        key=f"{kind}:{ident}",
        kind=kind,
        id=ident,
        name=name,
        number=number,
        since=since,
        **detail,
    )


async def inbox(db, manager_id, serialize_workflow, count_only=False):
    stamp = monthly_field.now()
    pending_corrections = select(MonthlyCorrection).where(
        MonthlyCorrection.status == "pending"
    )
    pending_rest = select(MonthlyRest).where(MonthlyRest.status == "pending")
    # Independent leave records with workflow links follow the current workflow node.
    linked = (
        select(ApprovalInstance.id)
        .where(
            ApprovalInstance.module == "leave",
            ApprovalInstance.business_type == "leave_request",
            ApprovalInstance.business_id == LeaveRequest.id,
            ApprovalInstance.template_version_id.is_(None),
        )
        .exists()
    )
    managers = select(EmployeeRole.employee_id).where(
        EmployeeRole.role_name == "aftersales_manager", EmployeeRole.is_active.is_(True)
    )
    standalone = select(LeaveRequest).where(
        LeaveRequest.approval_status == ApprovalStatus.pending,
        LeaveRequest.employee_id.in_(aftersales_person_ids()),
        LeaveRequest.employee_id != manager_id,
        LeaveRequest.employee_id.not_in(managers),
        ~linked,
    )
    hours = MonthlyPunchTodo.hours_due <= stamp
    projects = MonthlyPunchTodo.project_due <= stamp
    flow_total, flows = await approval.list_my_pending(
        db, manager_id, limit=0 if count_only else 2**31 - 1, include_legacy=True
    )
    if count_only:
        total = flow_total
        for query in (
            pending_corrections,
            pending_rest,
            standalone,
            select(MonthlyPunchTodo).where(hours),
            select(MonthlyPunchTodo).where(projects),
        ):
            total += await db.scalar(select(func.count()).select_from(query.subquery()))
        return dict(total=total)

    rows = []
    corrections = list(await db.scalars(pending_corrections))
    pending = list(await db.scalars(pending_rest))
    start = await monthly_rest.effective(db)
    balances = await monthly_rest.totals(
        db, [r.employee_id for r in pending], start, stamp.date()
    )
    leaves = list(await db.scalars(standalone))
    todos = list(await db.scalars(select(MonthlyPunchTodo).where(or_(hours, projects))))
    ids = {r.employee_id for r in [*corrections, *pending, *leaves, *todos]} | {
        f.applicant_id for f in flows
    }
    people = {
        e.id: (e.name, e.employee_no)
        for e in await db.scalars(select(Employee).where(Employee.id.in_(ids)))
    }
    for c in corrections:
        fields = [
            dict(
                label="原始打卡",
                value="、".join(
                    ("上班 " if p["kind"] == "in" else "下班 ")
                    + p["at"].replace("T", " ")
                    for p in c.original
                )
                or "全天无卡",
            )
        ]
        for label, stamp_value in [
            ("申请补上班卡", c.check_in),
            ("申请补下班卡", c.check_out),
        ]:
            if stamp_value:
                fields.append(
                    dict(
                        label=label,
                        value=stamp_value.isoformat(" ", timespec="minutes"),
                    )
                )
        rows.append(
            item(
                "correction",
                c.id,
                *people[c.employee_id],
                c.created,
                title="补卡申请",
                day=c.day,
                reason=c.reason,
                fields=fields,
                period=("夜班" if c.shift == "night" else "白班")
                + " · "
                + ("员工申报项目 " if not c.original else "打卡项目 ")
                + " / ".join(p["name"] for p in c.projects),
            )
        )
    for r in pending:
        balance = balances.get(r.employee_id, (0, 0))[0]
        uses_balance = start and min(r.created.date(), r.day) >= start
        rows.append(
            item(
                "rest",
                r.id,
                *people[r.employee_id],
                r.created,
                day=r.day,
                reason=r.reason,
                title="事后补休" if r.day < r.created.date() else "整天休假",
                balance=balance if uses_balance else None,
            )
        )
    for r in leaves:
        rows.append(
            item(
                "leave",
                r.id,
                *people[r.employee_id],
                local_time(r.created_at),
                day=r.start_date,
                title="请假申请",
                reason=r.reason,
                period=f"{r.start_date} 至 {r.end_date}（{r.days} 天）",
                node="售后经理",
            )
        )
    leave_ids = [
        f.business_id
        for f in flows
        if f.module == "leave"
        and f.business_type == "leave_request"
        and not f.template_version_id
    ]
    linked_leaves = {
        r.id: r
        for r in await db.scalars(
            select(LeaveRequest).where(LeaveRequest.id.in_(leave_ids))
        )
    }
    for f in flows:
        out = await serialize_workflow(db, f)
        leave = (
            linked_leaves.get(f.business_id)
            if f.module == "leave"
            and f.business_type == "leave_request"
            and not f.template_version_id
            else None
        )
        fields = []
        labels = {
            "start_time": "开始时间",
            "end_time": "结束时间",
            "reason": "申请原因",
        }
        for key, value in (out.form_data or {}).items():
            if key.startswith("_"):
                continue
            fields.append(
                dict(
                    label=out.form_field_labels.get(key, labels.get(key, key)),
                    value=out.form_field_display_values.get(key, str(value)),
                )
            )
        waiting_since = min(
            (
                t.created_at
                for t in f.tasks
                if t.status == "pending"
                and t.node_order == f.current_node_order
                and t.approver_id == manager_id
            ),
            default=f.updated_at or f.created_at,
        )
        rows.append(
            item(
                "flow",
                f.id,
                *people[f.applicant_id],
                local_time(waiting_since),
                title=out.approval_type_name or f.summary or f.business_type,
                reason=(leave.reason if leave else f.summary),
                node=out.current_node_name or f"第 {f.current_node_order} 个节点",
                day=leave.start_date if leave else None,
                period=(
                    f"{leave.start_date} 至 {leave.end_date}（{leave.days} 天）"
                    if leave
                    else None
                ),
                order=f.current_node_order,
                fields=fields,
            )
        )
    grouped = defaultdict(list)
    keys = [(t.employee_id, t.day) for t in todos]
    for offset in range(0, len(keys), 400):
        batch = keys[offset : offset + 400]
        for p in await db.scalars(
            select(MonthlyPunch)
            .where(tuple_(MonthlyPunch.employee_id, MonthlyPunch.day).in_(batch))
            .order_by(MonthlyPunch.sequence)
        ):
            grouped[p.employee_id, p.day].append(p)
    for t in todos:
        logs = grouped[t.employee_id, t.day]
        punches = [
            dict(kind=p.kind, at=p.at, projects=[m["name"] for m in p.matches])
            for p in logs
        ]
        common = dict(day=t.day, punches=punches)
        if t.hours_due and t.hours_due <= stamp:
            shift = await db.get(MonthlyShift, (t.employee_id, t.day))
            result = monthly_shifts.evaluate(shift, logs, stamp, None)
            rows.append(
                item(
                    "hours",
                    t.punch_id,
                    *people[t.employee_id],
                    t.hours_due,
                    title="工时异常",
                    shift=shift.kind,
                    worked_seconds=result["worked_seconds"],
                    suggested=result["suggested_minutes"],
                    reason="、".join(result["anomalies"]),
                    **common,
                )
            )
        if t.project_due and t.project_due <= stamp:
            choices = {m["code"]: m["name"] for p in logs for m in p.matches}
            rows.append(
                item(
                    "project",
                    t.punch_id,
                    *people[t.employee_id],
                    t.project_due,
                    title="打卡项目待确认",
                    choices=choices,
                    **common,
                )
            )
    rows.sort(key=lambda r: (r["since"], r["key"]))
    return dict(total=len(rows), items=rows)
