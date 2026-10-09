"""Project-scoped basic roster and attendance, without any continuous presence inference."""

import json
import inspect
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import select, delete, func
from app.models.field_service import (
    FieldCustomer,
    FieldProject,
    FieldSite,
    FieldAssignment,
    FieldAssignmentDay,
    FieldTegeneProject,
)
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.organization import Location
from app.models.attendance import (
    AttendanceRule,
    ShiftSchedule,
    ShiftType,
    AttendanceRecord,
    AttendanceMonthSummary,
    MonthlySummaryStatus,
)
from app.models.audit_log import AuditLog

CST = timezone(timedelta(hours=8))


def today():
    return datetime.now(CST).date()


def as_dict(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


async def roles(db, user):
    if user.is_superuser:
        return {"admin"}
    return set(
        (
            await db.scalars(
                select(EmployeeRole.role_name).where(
                    EmployeeRole.employee_id == user.id,
                    EmployeeRole.is_active.is_(True),
                )
            )
        ).all()
    )


async def is_admin(db, user):
    return bool(await roles(db, user) & {"admin", "hr"})


async def require_admin(db, user):
    if not await is_admin(db, user):
        raise HTTPException(403, "仅售后管理员可维护基础档案")


async def project_ids(db, user):
    if await is_admin(db, user):
        return None
    own = set(
        (
            await db.scalars(
                select(FieldProject.id).where(FieldProject.manager_id == user.id)
            )
        ).all()
    )
    assigned = (
        await db.scalars(
            select(FieldSite.project_id)
            .join(FieldAssignment, FieldAssignment.site_id == FieldSite.id)
            .where(FieldAssignment.employee_id == user.id)
        )
    ).all()
    return own | set(assigned)


async def require_project_manager(db, user, project_id):
    project = await db.get(FieldProject, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    if not await is_admin(db, user) and project.manager_id != user.id:
        raise HTTPException(403, "无权管理该项目")
    return project


async def directory(db, user):
    admin = await is_admin(db, user)
    allowed = await project_ids(db, user)
    ps = select(FieldProject).order_by(FieldProject.id.desc())
    if allowed is not None:
        ps = ps.where(FieldProject.id.in_(allowed))
    projects = list((await db.scalars(ps)).all())
    pids = {p.id for p in projects}
    sites = list(
        (
            await db.scalars(
                select(FieldSite)
                .where(FieldSite.project_id.in_(pids))
                .order_by(FieldSite.id)
            )
        ).all()
    )
    cs = select(FieldCustomer).order_by(FieldCustomer.id)
    if allowed is not None:
        cs = cs.where(FieldCustomer.id.in_({p.customer_id for p in projects}))
    customers = list((await db.scalars(cs)).all())
    stmt = (
        select(FieldAssignment)
        .where(FieldAssignment.site_id.in_([s.id for s in sites]))
        .order_by(FieldAssignment.start_date.desc(), FieldAssignment.id.desc())
    )
    if not admin:
        owned = {p.id for p in projects if p.manager_id == user.id}
        owned_sites = {s.id for s in sites if s.project_id in owned}
        stmt = stmt.where(
            (FieldAssignment.employee_id == user.id)
            | (FieldAssignment.site_id.in_(owned_sites))
        )
    assignments = list((await db.scalars(stmt)).all())
    es = select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.name)
    if not admin and not any(p.manager_id == user.id for p in projects):
        es = es.where(
            (Employee.direct_manager_id == user.id)
            | (
                Employee.id.in_(
                    {user.id}
                    | {a.employee_id for a in assignments}
                    | {p.manager_id for p in projects}
                )
            )
        )
    es = es.where(Employee.status.in_(["在职", "试用"]))
    people = list((await db.scalars(es)).unique().all())
    from app.models.field_service import FieldMembership
    from app.services.field_membership import utc, clock_now
    intervals = {m.assignment_id: m for m in (await db.scalars(select(FieldMembership).where(FieldMembership.assignment_id.in_([a.id for a in assignments])))).all()}
    now = clock_now()
    def active(a):
        m = intervals.get(a.id)
        return bool(m and utc(m.started_at) <= now and (m.ended_at is None or utc(m.ended_at) > now))
    historical = (
        await db.execute(
            select(Employee.id, Employee.name).where(
                Employee.id.in_(
                    {a.employee_id for a in assignments}
                    | {p.manager_id for p in projects}
                )
            )
        )
    ).all()
    names = {**{p.id: p.name for p in people}, **dict(historical)}
    site_map = {s.id: s for s in sites}
    project_map = {p.id: p for p in projects}
    customer_map = {c.id: c for c in customers}
    rule_stmt = select(AttendanceRule).order_by(AttendanceRule.name)
    if not admin and not any(p.manager_id == user.id for p in projects):
        rule_stmt = rule_stmt.where(AttendanceRule.id.in_({s.rule_id for s in sites}))
    rules = list((await db.scalars(rule_stmt)).all())
    rule_names = {r.id: r.name for r in rules}
    active_sites = {s.project_id: s for s in sites if s.is_active}
    manager_ids = set((await db.scalars(select(EmployeeRole.employee_id).where(
        EmployeeRole.is_active.is_(True), EmployeeRole.role_name.in_(["admin", "hr", "manager"])
    ))).all()) if admin else set()
    tegene = {r.project_id: r for r in (await db.scalars(
        select(FieldTegeneProject).where(FieldTegeneProject.project_id.in_(pids))
    )).all()}
    return {
        "customers": [as_dict(c) for c in customers],
        "projects": [
            {
                **as_dict(p),
                "customer_name": customer_map[p.customer_id].name,
                "manager_name": names.get(p.manager_id, ""),
                "source": "tegene" if p.id in tegene else "local",
                "synced_at": tegene[p.id].synced_at if p.id in tegene else None,
                "rule_id": active_sites[p.id].rule_id if p.id in active_sites else None,
                "rule_name": rule_names.get(active_sites[p.id].rule_id, "") if p.id in active_sites else "",
                "member_count": len({a.employee_id for a in assignments if active(a) and site_map[a.site_id].project_id == p.id}),
            }
            for p in projects
        ],
        "sites": [
            {**as_dict(s), "project_name": project_map[s.project_id].name}
            for s in sites
        ],
        "assignments": [
            {
                **as_dict(a),
                "is_current": active(a),
                "started_at": intervals[a.id].started_at if a.id in intervals else None,
                "ended_at": intervals[a.id].ended_at if a.id in intervals else None,
                "employee_name": names.get(a.employee_id, ""),
                "site_name": site_map[a.site_id].name,
                "project_id": site_map[a.site_id].project_id,
                "project_name": project_map[site_map[a.site_id].project_id].name,
            }
            for a in assignments
        ],
        "people": [
            {
                "id": p.id,
                "name": p.name,
                "employee_no": p.employee_no,
                "phone": p.phone,
                "department_id": p.department_id,
                "position": p.position,
                "is_active": p.is_active,
                "can_be_manager": p.is_superuser or p.id in manager_ids,
            }
            for p in people
        ],
        "rules": [
            {"id": r.id, "name": r.name, "is_active": r.is_active,
             "clock_in_time": r.clock_in_time, "clock_out_time": r.clock_out_time,
             "require_gps": r.require_gps, "require_wifi": r.require_wifi,
             "require_photo": r.require_photo, "effective_date": r.effective_date,
             "check_method_mode": (r.extra_config or {}).get("check_method_mode", "any")}
            for r in rules
        ],
        "can_manage": admin,
        "managed_project_ids": [p.id for p in projects if p.manager_id == user.id],
    }


async def save_customer(db, user, data, item_id=None):
    await require_admin(db, user)
    obj = await db.get(FieldCustomer, item_id) if item_id else FieldCustomer()
    if obj is None:
        raise HTTPException(404, "客户不存在")
    for key, value in data.model_dump().items():
        setattr(obj, key, value.strip() if isinstance(value, str) else value)
    if not obj.name:
        raise HTTPException(422, "名称不能为空")
    db.add(obj)
    await db.flush()
    return as_dict(obj)


async def save_project(db, user, data, item_id=None):
    await require_admin(db, user)
    customer = await db.get(FieldCustomer, data.customer_id)
    manager = await db.get(Employee, data.manager_id)
    if not customer or not customer.is_active or not manager or not manager.is_active:
        raise HTTPException(422, "客户或负责人无效")
    manager_roles = await roles(db, manager)
    if not manager_roles & {"manager", "hr", "admin"}:
        raise HTTPException(422, "项目负责人需具有负责人或管理员角色")
    obj = await db.get(FieldProject, item_id) if item_id else FieldProject()
    if obj is None:
        raise HTTPException(404, "项目不存在")
    if item_id and await db.scalar(select(FieldTegeneProject.id).where(FieldTegeneProject.project_id == item_id)):
        if (data.code.strip(), data.name.strip(), data.customer_id) != (obj.code, obj.name, obj.customer_id):
            raise HTTPException(409, "Tegene 项目的编号、名称和客户请在源系统维护，再同步资料")
    for key, value in data.model_dump().items():
        setattr(obj, key, value.strip() if isinstance(value, str) else value)
    if not obj.name or not obj.code:
        raise HTTPException(422, "名称和编号不能为空")
    db.add(obj)
    await db.flush()
    return as_dict(obj)


async def save_site(db, user, data, item_id=None):
    project = await require_project_manager(db, user, data.project_id)
    rule = await db.get(AttendanceRule, data.rule_id)
    if not project or not project.is_active or not rule or not rule.is_active:
        raise HTTPException(422, "项目或考勤规则无效")
    obj = await db.get(FieldSite, item_id) if item_id else None
    if item_id and obj is None:
        raise HTTPException(404, "现场不存在")
    if obj is not None and obj.project_id != data.project_id:
        raise HTTPException(409, "已有现场不能更改所属项目，请新建现场")
    if obj is not None and obj.rule_id != data.rule_id:
        # Already-punched days and today's shift retain their original rule.
        future = (await db.scalars(select(ShiftSchedule)
            .join(FieldAssignmentDay, FieldAssignmentDay.shift_id == ShiftSchedule.id)
            .join(FieldAssignment, FieldAssignment.id == FieldAssignmentDay.assignment_id)
            .where(FieldAssignment.site_id == obj.id, FieldAssignment.status == "active",
                   ShiftSchedule.date > today()))).all()
        await update_project_shifts(db, rule, future)
    if obj is None:
        location = Location(name=f"{project.code} / {data.name}", address=data.address)
        db.add(location)
        await db.flush()
        obj = FieldSite(location_id=location.id)
    location = await db.get(Location, obj.location_id)
    location.name = f"{project.code} / {data.name}"[:100]
    location.address = data.address
    location.is_active = data.is_active
    for key, value in data.model_dump().items():
        setattr(obj, key, value)
    db.add(obj)
    await db.flush()
    return as_dict(obj)


async def update_project_shifts(db, rule, shifts=None):
    if shifts is None:
        shifts = (await db.scalars(select(ShiftSchedule)
            .join(FieldAssignmentDay, FieldAssignmentDay.shift_id == ShiftSchedule.id)
            .join(FieldAssignment, FieldAssignment.id == FieldAssignmentDay.assignment_id)
            .where(ShiftSchedule.rule_id == rule.id, FieldAssignment.status == "active",
                   ShiftSchedule.date > today()))).all()
    for shift in shifts:
        if rule.effective_date > shift.date:
            raise HTTPException(422, "新规则尚未在人员参与日期生效")
        record = await db.scalar(select(AttendanceRecord.id).where(
            AttendanceRecord.employee_id == shift.employee_id, AttendanceRecord.date == shift.date))
        locked = await db.scalar(select(AttendanceMonthSummary.id).where(
            AttendanceMonthSummary.employee_id == shift.employee_id,
            AttendanceMonthSummary.year == shift.date.year, AttendanceMonthSummary.month == shift.date.month,
            AttendanceMonthSummary.status == MonthlySummaryStatus.locked))
        if record or locked:
            raise HTTPException(409, "后续日期已有考勤记录或月份已锁定，请复制规则后安排新的参与日期")
        for key, value in project_shift_values(rule, shift.date).items():
            setattr(shift, key, value)


def project_shift_values(rule, day):
    from app.services.attendance import AttendanceRecordService
    runtime = AttendanceRecordService._build_runtime(rule, None, day)
    start, end = runtime["expected_in"], runtime["expected_out"]
    if runtime["rule_type"] != "free" and (not start or not end):
        raise HTTPException(422, "所选规则缺少上下班时间，请先完善规则")
    return {"rule_id": rule.id, "start_time": start, "end_time": end,
            "shift_type": ShiftType.night_shift if start and end and end <= start else ShiftType.day_shift}


async def project_site(db, project_id):
    sites = list((await db.scalars(select(FieldSite).where(
        FieldSite.project_id == project_id, FieldSite.is_active.is_(True)))).all())
    if len(sites) > 1:
        raise HTTPException(409, "项目存在多套历史配置，需先合并后再设置项目规则")
    return sites[0] if sites else None


async def configure_project(db, user, project_id, data):
    from app.schemas.field_service import SiteInput
    project = await require_project_manager(db, user, project_id)
    if data.manager_id is not None and data.manager_id != project.manager_id:
        await require_admin(db, user)
        manager = await db.get(Employee, data.manager_id)
        if not manager or not manager.is_active or not await roles(db, manager) & {"admin", "hr", "manager"}:
            raise HTTPException(422, "请选择有效的项目负责人")
        project.manager_id = data.manager_id
    if data.rule_id is not None:
        site = await project_site(db, project.id)
        await save_site(db, user, SiteInput(project_id=project.id, name=project.name,
                        rule_id=data.rule_id, address=site.address if site else ""), site.id if site else None)
    await db.flush()
    return {"id": project.id, "manager_id": project.manager_id, "rule_id": data.rule_id}


async def add_project_members(db, user, project_id, data):
    if data.start_date is None:
        from app.services.field_membership import add_members
        return await add_members(db, user, project_id, data.employee_ids)
    from app.schemas.field_service import AssignmentInput
    await require_project_manager(db, user, project_id)
    site = await project_site(db, project_id)
    if not site:
        raise HTTPException(422, "请先为项目选择打卡规则")
    if data.start_date < today():
        raise HTTPException(422, "参与日期不能早于今天")
    members = []
    for employee_id in data.employee_ids:
        members.append(await create_assignment(db, user, AssignmentInput(
            site_id=site.id, employee_id=employee_id, start_date=data.start_date, end_date=data.end_date)))
    return {"members": members}


async def create_assignment(db, user, data):
    site = await db.get(FieldSite, data.site_id)
    if not site or not site.is_active:
        raise HTTPException(422, "项目尚未配置有效的打卡规则")
    project = await require_project_manager(db, user, site.project_id)
    from app.services.field_membership import lock_person
    from app.models.field_service import FieldMembership
    person = await lock_person(db, data.employee_id)
    overlap = await db.scalar(select(FieldMembership.id).where(
        FieldMembership.employee_id == data.employee_id,
        FieldMembership.started_at < datetime.combine(data.end_date + timedelta(days=1), datetime.min.time(), CST),
        (FieldMembership.ended_at.is_(None)) | (FieldMembership.ended_at > datetime.combine(data.start_date, datetime.min.time(), CST))).limit(1))
    if overlap:
        raise HTTPException(409, "人员在所选期间已有项目，请使用调动功能")
    rule = await db.get(AttendanceRule, site.rule_id)
    if (
        not project.is_active
        or not person
        or not person.is_active
        or not rule
        or not rule.is_active
    ):
        raise HTTPException(422, "项目、人员或规则无效")
    if (
        not await is_admin(db, user)
        and person.id != user.id
        and person.direct_manager_id != user.id
    ):
        existing = await db.scalar(
            select(FieldAssignment.id)
            .join(FieldSite, FieldSite.id == FieldAssignment.site_id)
            .join(FieldProject, FieldProject.id == FieldSite.project_id)
            .where(
                FieldAssignment.employee_id == person.id,
                FieldProject.manager_id == user.id,
            )
            .limit(1)
        )
        if not existing:
            raise HTTPException(403, "只能安排本人、直属人员或本项目已有人员")
    if rule.effective_date > data.start_date:
        raise HTTPException(422, "参与开始日期早于考勤规则生效日期")
    conflicts = await db.scalar(
        select(func.count())
        .select_from(ShiftSchedule)
        .where(
            ShiftSchedule.employee_id == data.employee_id,
            ShiftSchedule.date.between(data.start_date, data.end_date),
        )
    )
    if conflicts:
        raise HTTPException(409, f"{person.name}在所选期间已有项目或班次，请调整参与日期")
    locked = await db.scalars(
        select(AttendanceMonthSummary).where(
            AttendanceMonthSummary.employee_id == data.employee_id,
            AttendanceMonthSummary.status == MonthlySummaryStatus.locked,
        )
    )
    month_keys = {(x.year, x.month) for x in locked}
    days = [
        data.start_date + timedelta(days=i)
        for i in range((data.end_date - data.start_date).days + 1)
    ]
    if any((d.year, d.month) in month_keys for d in days):
        raise HTTPException(409, "参与日期包含已锁定考勤月份")
    assignment = FieldAssignment(**data.model_dump(), created_by=user.id)
    db.add(assignment)
    await db.flush()
    for day in days:
        shift = ShiftSchedule(
            employee_id=person.id,
            date=day,
            **project_shift_values(rule, day),
        )
        db.add(shift)
        await db.flush()
        db.add(
            FieldAssignmentDay(
                assignment_id=assignment.id,
                employee_id=person.id,
                work_date=day,
                shift_id=shift.id,
            )
        )
    from app.services.field_membership import migrate_assignments
    await migrate_assignments(db)
    from app.services.notification import NotificationService
    from app.schemas.notification import NotificationCreate

    await NotificationService.create(
        db,
        NotificationCreate(
            recipient_id=person.id,
            sender_id=user.id,
            title="你有新的项目安排",
            content=f"{project.name}，{data.start_date} 至 {data.end_date}，打卡规则：{rule.name}",
            notif_type="system",
            ref_type="field_assignment",
            ref_id=assignment.id,
        ),
    )
    await db.flush()
    return as_dict(assignment)


async def cancel_assignment(db, user, item_id):
    a = await db.get(FieldAssignment, item_id)
    if not a:
        raise HTTPException(404, "项目人员记录不存在")
    site = await db.get(FieldSite, a.site_id)
    await require_project_manager(db, user, site.project_id)
    if a.status == "cancelled":
        return as_dict(a)
    rows = list(
        (
            await db.scalars(
                select(FieldAssignmentDay).where(
                    FieldAssignmentDay.assignment_id == a.id,
                    FieldAssignmentDay.work_date >= today(),
                )
            )
        ).all()
    )
    dates = [r.work_date for r in rows]
    if dates:
        records = await db.scalar(
            select(func.count())
            .select_from(AttendanceRecord)
            .where(
                AttendanceRecord.employee_id == a.employee_id,
                AttendanceRecord.date.in_(dates),
            )
        )
        if records:
            raise HTTPException(409, "待取消日期已有打卡记录，不能直接取消历史事实")
        for row in rows:
            shift = await db.get(ShiftSchedule, row.shift_id)
            await db.delete(row)
            await db.flush()
            if shift:
                await db.delete(shift)
    from app.models.field_service import FieldMembership
    membership = await db.scalar(select(FieldMembership).where(FieldMembership.assignment_id == a.id))
    if membership:
        membership.ended_at = datetime.now(timezone.utc)
        membership.active_employee_id = None
    a.status = "cancelled"
    await db.flush()
    return as_dict(a)


async def dashboard(db, user, day, project_id=None, include_trend=False):
    from app.services.field_dashboard import build_dashboard
    return await build_dashboard(db, user, day, project_id, include_trend)


async def team_attendance(db, user, year, month):
    """Supply the original mobile team cards with project-scoped monthly data."""
    if not await roles(db, user) & {"admin", "hr", "manager"}:
        raise HTTPException(403, "仅管理人员可查看团队考勤")
    from app.services.attendance import MonthSummaryService
    from app.schemas.attendance import MonthSummaryGenerateRequest
    from app.models.leave import LeaveRequest
    from app.models.approval import ApprovalInstance
    from app.models.organization import Department

    context = await directory(db, user)
    allowed_ids = {p["id"] for p in context["people"]}
    if not await is_admin(db, user):
        direct_ids = set((await db.scalars(select(Employee.id).where(
            Employee.direct_manager_id == user.id
        ))).all())
        managed_ids = {a["employee_id"] for a in context["assignments"]
                       if a["project_id"] in context["managed_project_ids"]}
        allowed_ids &= direct_ids | managed_ids | {user.id}
    summaries = list((await db.scalars(select(AttendanceMonthSummary).where(
        AttendanceMonthSummary.employee_id.in_(allowed_ids),
        AttendanceMonthSummary.year == year, AttendanceMonthSummary.month == month,
    ))).all())
    missing_ids = allowed_ids - {s.employee_id for s in summaries}
    if missing_ids:
        await MonthSummaryService.generate(db, MonthSummaryGenerateRequest(
            year=year, month=month, employee_ids=sorted(missing_ids)
        ))
        summaries = list((await db.scalars(select(AttendanceMonthSummary).where(
            AttendanceMonthSummary.employee_id.in_(allowed_ids),
            AttendanceMonthSummary.year == year, AttendanceMonthSummary.month == month,
        ))).all())
    summary_map = {s.employee_id: as_dict(s) for s in summaries}
    departments = dict((await db.execute(select(Department.id, Department.name))).all())
    # Legacy requests and published-template instances are separate sources;
    # only template-backed instances are counted here, preventing duplicates.
    pending_legacy = await db.scalar(select(func.count(LeaveRequest.id)).where(
        LeaveRequest.employee_id.in_(allowed_ids), LeaveRequest.approval_status == "pending"
    ))
    pending_dynamic = await db.scalar(select(func.count(ApprovalInstance.id)).where(
        ApprovalInstance.applicant_id.in_(allowed_ids),
        ApprovalInstance.template_version_id.is_not(None),
        ApprovalInstance.status == "pending", ApprovalInstance.module == "leave",
    ))
    return {
        "people": [{**p, "department_name": departments.get(p["department_id"], ""),
                    "summary": summary_map.get(p["id"])}
                   for p in context["people"] if p["id"] in allowed_ids],
        "today": await dashboard(db, user, today()),
        "pending_leave_count": (pending_legacy or 0) + (pending_dynamic or 0),
    }


async def list_people(db, user):
    await require_admin(db, user)
    from app.models.field_service import FieldWecomIdentity
    bindings = {b.employee_id: b for b in (await db.scalars(select(FieldWecomIdentity))).all()}
    rows = (await db.scalars(select(Employee).order_by(Employee.id))).all()
    from app.models.field_service import FieldRosterProfile, FieldMembership
    from app.models.organization import Department
    from app.schemas.field_roster import RosterFields, RosterDetails
    from app.services.field_membership import clock_now
    profiles = {p.employee_id: p.details for p in (await db.scalars(select(FieldRosterProfile))).all()}
    departments = dict((await db.execute(select(Department.id, Department.name))).all())
    now = clock_now()
    current_projects = dict((await db.execute(select(FieldMembership.employee_id, FieldProject.name)
        .join(FieldAssignment, FieldAssignment.id == FieldMembership.assignment_id)
        .join(FieldSite, FieldSite.id == FieldAssignment.site_id)
        .join(FieldProject, FieldProject.id == FieldSite.project_id)
        .where(FieldMembership.started_at <= now,
               (FieldMembership.ended_at.is_(None)) | (FieldMembership.ended_at > now)))).all())
    managed_people = set((await db.scalars(select(FieldMembership.employee_id))).all())
    role_rows = (
        await db.execute(
            select(EmployeeRole.employee_id, EmployeeRole.role_name).where(
                EmployeeRole.is_active.is_(True)
            )
        )
    ).all()
    mapping = {}
    for employee_id, role in role_rows:
        mapping.setdefault(employee_id, []).append(role)
    return [
        dict(
            **{key: getattr(p, key) for key in RosterFields.model_fields if key not in RosterDetails.model_fields and key != "current_project"},
            **{key: profiles.get(p.id, {}).get(key) for key in RosterDetails.model_fields if key != "department_name"},
            department_name=departments.get(p.department_id) or profiles.get(p.id, {}).get("department_name"),
            current_project=current_projects.get(p.id, "") if p.id in managed_people else p.current_project,
            project_managed=p.id in managed_people,
            id=p.id,
            employee_no=p.employee_no,
            name=p.name,
            phone=p.phone,
            position=p.position,
            department_id=p.department_id,
            direct_manager_id=p.direct_manager_id,
            is_active=p.is_active,
            status=p.status,
            wecom_userid=bindings[p.id].userid if p.id in bindings else "",
            wecom_corp_id=bindings[p.id].corp_id if p.id in bindings else "",
            role="admin"
            if p.is_superuser
            else next(
                (
                    r
                    for r in ["admin", "hr", "manager", "employee"]
                    if r in mapping.get(p.id, [])
                ),
                "employee",
            ),
        )
        for p in rows
    ]


async def save_person(db, user, data, item_id=None):
    await require_admin(db, user)
    from app.core.security import DEFAULT_INITIAL_PASSWORD, get_password_hash
    from app.services.wecom_auth import bind_member
    from app.schemas.field_roster import RosterFields
    from app.services.field_roster import save_roster_fields

    import re
    from app.services.field_membership import lock_person, stop_person
    row = await lock_person(db, item_id) if item_id else None
    if not re.fullmatch(r"1[3-9]\d{9}", data.phone.strip()) and not (row and row.phone == data.phone):
        raise HTTPException(422, "手机号须为 11 位有效号码")
    if await db.scalar(select(Employee.id).where(Employee.phone == data.phone.strip(), Employee.id != (item_id or 0))):
        raise HTTPException(409, "手机号已在花名册中，请编辑现有人员")
    if data.status not in {"在职", "试用"}:
        data.is_active = False
    if item_id and not row:
        raise HTTPException(404, "人员不存在")
    if (
        row
        and row.id == user.id
        and (not data.is_active or data.role not in await roles(db, user))
    ):
        raise HTTPException(409, "不能停用自己或移除自己的管理权限")
    if row and row.is_superuser and not user.is_superuser:
        raise HTTPException(403, "仅系统管理员可编辑系统管理员")
    if (
        data.role == "admin" or (row and "admin" in await roles(db, row))
    ) and "admin" not in await roles(db, user):
        raise HTTPException(403, "仅系统管理员可分配管理员权限")
    if data.direct_manager_id:
        manager = await db.get(Employee, data.direct_manager_id)
        if not manager or not manager.is_active or data.direct_manager_id == item_id:
            raise HTTPException(400, "直属主管不可用")
        seen = {item_id} if item_id else set()
        while manager:
            if manager.id in seen:
                raise HTTPException(400, "主管链不能形成循环")
            seen.add(manager.id)
            manager = (
                await db.get(Employee, manager.direct_manager_id)
                if manager.direct_manager_id
                else None
            )
    if data.department_id:
        from app.models.organization import Department
        department = await db.get(Department, data.department_id)
        if not department or not department.is_active:
            raise HTTPException(422, "请选择有效部门")
        data.department_name = department.name
    number = data.employee_no.strip() or (row.employee_no if row else "AF-" + data.phone.strip())
    if await db.scalar(select(Employee.id).where(Employee.employee_no == number, Employee.id != (item_id or 0))):
        raise HTTPException(409, "工号已被其他员工使用")
    if row is None:
        row = Employee(status="在职", is_field_staff=True,
                       hashed_password=get_password_hash(data.password or DEFAULT_INITIAL_PASSWORD))
        db.add(row)
    for key, value in data.model_dump(exclude={"password", "role", "wecom_userid", *RosterFields.model_fields}).items():
        setattr(row, key, value.strip() if isinstance(value, str) else value)
    row.employee_no = number
    if data.password:
        row.hashed_password = get_password_hash(data.password)
    await db.flush()
    await save_roster_fields(db, row, data.model_dump(include=set(RosterFields.model_fields), exclude_unset=True))
    if not row.is_active:
        await stop_person(db, row.id)
    await bind_member(db, row.id, data.wecom_userid)
    await db.execute(delete(EmployeeRole).where(EmployeeRole.employee_id == row.id))
    db.add(EmployeeRole(employee_id=row.id, role_name=data.role, granted_by=user.id))
    await db.flush()
    return {"id": row.id, "name": row.name}


def audited(action, operation):
    async def run(db, user, *args, **kwargs):
        params = inspect.signature(operation).bind(db, user, *args, **kwargs).arguments
        data = params.get("data")
        models = {"save_person": Employee, "save_customer": FieldCustomer,
                  "save_project": FieldProject, "save_site": FieldSite,
                  "cancel_assignment": FieldAssignment, "remove_or_transfer": FieldAssignment}
        model = models.get(operation.__name__)
        item_id = params.get("item_id") or params.get("assignment_id")
        async def snapshot():
            if operation.__name__ == "configure_project":
                project = await db.get(FieldProject, params["project_id"])
                if project is None:
                    return None
                site = await project_site(db, params["project_id"])
                return {"manager_id": project.manager_id, "rule_id": site.rule_id if site else None}
            row = await db.get(model, item_id) if model and item_id else None
            if row is None:
                return None
            keys = data.model_dump(exclude={"password", "role", "wecom_userid"}) if data else {"employee_id": None, "site_id": None, "status": None}
            values = {key: getattr(row, key, None) for key in keys}
            if model is Employee:
                from app.models.field_service import FieldRosterProfile
                from app.schemas.field_roster import RosterDetails
                profile = await db.get(FieldRosterProfile, row.id)
                for key in RosterDetails.model_fields:
                    if key in values:
                        values[key] = (profile.details if profile else {}).get(key)
                from app.models.field_service import FieldWecomIdentity
                values["role"] = sorted(await roles(db, row))
                values["wecom_userid"] = await db.scalar(select(FieldWecomIdentity.userid).where(FieldWecomIdentity.employee_id == row.id))
            return values
        before = await snapshot()
        result = await operation(db, user, *args, **kwargs)
        if result.get("preview"):
            return result
        item_id = item_id or result.get("id")
        after = await snapshot()
        if after is None:
            after = dict(result)
        elif operation.__name__ == "remove_or_transfer":
            after.update(result)
        if data and getattr(data, "password", None):
            after["password_changed"] = True
        db.add(
            AuditLog(
                operator_id=user.id,
                operator_name=user.name,
                action=action,
                module="field",
                resource_id=result.get("id"),
                resource_type=operation.__name__,
                before_data=json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
                after_data=json.dumps(after, ensure_ascii=False, default=str),
            )
        )
        await db.flush()
        return result

    return run


save_customer = audited("save", save_customer)
save_project = audited("save", save_project)
save_site = audited("save", save_site)
create_assignment = audited("create", create_assignment)
cancel_assignment = audited("cancel", cancel_assignment)
save_person = audited("save", save_person)
configure_project = audited("configure", configure_project)
add_project_members = audited("add-members", add_project_members)
