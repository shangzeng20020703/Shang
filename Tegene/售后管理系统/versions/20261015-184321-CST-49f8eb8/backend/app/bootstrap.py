"""Non-destructive first-install schema and explicit local demonstration fixtures."""

import argparse, asyncio, os
from datetime import date, time, timedelta
from sqlalchemy import select, update
from app.core.database import Base, engine, async_session
from app.core.config import settings
import app.models
import app.models.field_service
from app.models.field_service import FieldSchemaVersion
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.organization import Department
from app.models.attendance import AttendanceRule, WorkHourType
from app.models.approval import ApprovalType
from app.models.leave import LeaveType
from app.core.security import get_password_hash
from app.services.approval import save_template_config


def field(code, label, kind="text", required=True, **kw):
    return dict(code=code, label=label, field_type=kind, is_required=required, **kw)


async def initialize():
    # Additive tables: identity, project bindings, participation intervals and punch segments.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_session() as db:
        from app.services.field_membership import migrate_assignments
        await migrate_assignments(db)
        if not await db.get(FieldSchemaVersion, 6):
            # Retire legacy employment labels without unlocking any existing account.
            await db.execute(update(Employee).where(Employee.status.in_(["待入职", "停用"])).values(status="在职"))
            await db.execute(update(Employee).where(Employee.status == "试用期").values(status="试用"))
        for version in (1, 2, 3, 4, 5, 6, 7, 8):
            if not await db.get(FieldSchemaVersion, version):
                db.add(FieldSchemaVersion(version=version))
        await db.commit()


async def seed(demo=False):
    async with async_session() as db:
        if demo and settings.ENVIRONMENT == "production":
            raise ValueError("Demo accounts are disabled in production")
        phone = os.getenv("ADMIN_PHONE", "19900000001" if demo else ("admin" if settings.ENVIRONMENT == "development" else ""))
        password = os.getenv("ADMIN_PASSWORD", "Demo-AfterSales-2026!" if demo else ("admin123" if settings.ENVIRONMENT == "development" else ""))
        if not phone or len(password) < 8:
            raise ValueError(
                "Set ADMIN_PHONE and ADMIN_PASSWORD (at least 8 characters), or use --demo locally"
            )
        admin = await db.scalar(select(Employee).where(Employee.phone == phone))
        if not admin:
            if await db.scalar(select(Employee.id).where(Employee.employee_no == "ADMIN")):
                raise ValueError("Administrator already exists under another account; seed does not replace it")
            admin = Employee(
                employee_no="ADMIN",
                name="售后管理员",
                phone=phone,
                hashed_password=get_password_hash(password),
                is_superuser=True,
                is_active=True,
                status="在职",
            )
            db.add(admin)
            await db.flush()
            db.add(
                EmployeeRole(
                    employee_id=admin.id, role_name="admin", granted_by=admin.id
                )
            )
        if os.getenv("ADMIN_WECOM_USERID"):
            from app.services.wecom_auth import bind_member
            await bind_member(db, admin.id, os.environ["ADMIN_WECOM_USERID"])
        staff = None
        if demo:
            dept = await db.scalar(
                select(Department).where(Department.code == "SERVICE")
            )
            if not dept:
                dept = Department(code="SERVICE", name="售后服务部")
                db.add(dept)
                await db.flush()
            manager = await db.scalar(
                select(Employee).where(Employee.employee_no == "DEMO-MANAGER")
            )
            if not manager:
                manager = Employee(
                    employee_no="DEMO-MANAGER",
                    name="演示项目经理",
                    phone="19900000002",
                    hashed_password=get_password_hash(password),
                    is_active=True,
                    status="在职",
                    department_id=dept.id,
                    position="项目经理",
                )
                db.add(manager)
                await db.flush()
                db.add(
                    EmployeeRole(
                        employee_id=manager.id, role_name="manager", granted_by=admin.id
                    )
                )
            dept.manager_id = manager.id
            staff = await db.scalar(
                select(Employee).where(Employee.employee_no == "DEMO-STAFF")
            )
            if not staff:
                staff = Employee(
                    employee_no="DEMO-STAFF",
                    name="演示售后工程师",
                    phone="19900000003",
                    hashed_password=get_password_hash(password),
                    is_active=True,
                    status="在职",
                    department_id=dept.id,
                    direct_manager_id=manager.id,
                    position="售后工程师",
                    is_field_staff=True,
                )
                db.add(staff)
                await db.flush()
                db.add(
                    EmployeeRole(
                        employee_id=staff.id, role_name="employee", granted_by=admin.id
                    )
                )
        else:
            manager = admin
        for code, name in [("personal", "事假"), ("sick", "病假")]:
            if not await db.scalar(select(LeaveType.id).where(LeaveType.code == code)):
                db.add(
                    LeaveType(
                        name=name,
                        code=code,
                        quota_limited=False,
                        is_paid=code == "sick",
                        leave_unit="hour",
                        time_calc="workday",
                        is_active=True,
                    )
                )
        dates = [
            field("start_time", "开始时间", "datetime"),
            field("end_time", "结束时间", "datetime"),
            field("duration", "时长（小时）", "number"),
        ]
        reason = field("reason", "申请说明", "textarea")
        templates = [
            (
                "leave",
                "请假申请",
                [
                    field(
                        "leave_type",
                        "请假类型",
                        "select",
                        options_json={"options": ["事假", "病假"]},
                    )
                ]
                + dates
                + [reason],
            ),
            ("overtime", "加班申请", dates + [reason]),
            ("outside", "临时外出", dates + [field("destination", "外出地点"), reason]),
            (
                "punch_correction",
                "补卡申请",
                [
                    field("correction_date", "补卡日期", "date"),
                    field(
                        "punch_type",
                        "补卡类型",
                        "select",
                        options_json={"options": ["clock_in", "clock_out"]},
                    ),
                    field("punch_time", "补卡时间", "datetime"),
                    reason,
                ],
            ),
            (
                "business_trip",
                "出差申请",
                [
                    field("department", "部门"),
                    field("project_name", "项目名称"),
                    field("trip_reason", "出差说明", "textarea"),
                    field("trip_location", "出差地点"),
                    field("trip_duration", "出差时长", "duration"),
                ]
                + dates,
            ),
            (
                "field_daily_report",
                "陪产日报",
                [
                    field("work_date", "工作日期", "date"),
                    field("project_name", "项目名称"),
                    field("work_summary", "当日工作", "textarea"),
                    field("issues", "现场问题", "textarea", False),
                ],
            ),
        ]
        for index, (code, name, fields) in enumerate(templates):
            existing = await db.scalar(
                select(ApprovalType).where(ApprovalType.business_code == code)
            )
            if existing:
                # Upgrade only the first local demo's incorrectly named correction fields.
                if code == "punch_correction":
                    from app.services.approval import export_template_config

                    saved = await export_template_config(db, existing.id)
                    old_fields = saved.get("fields", [])
                    if any(
                        f.get("code") in {"correction_type", "correction_time"}
                        for f in old_fields
                    ):
                        for f in old_fields:
                            f["code"] = {
                                "correction_type": "punch_type",
                                "correction_time": "punch_time",
                            }.get(f.get("code"), f.get("code"))
                        saved["id"] = existing.id
                        await save_template_config(db, saved, admin)
                continue
            await save_template_config(
                db,
                dict(
                    name=name,
                    business_code=code,
                    category="售后现场",
                    scope="mobile",
                    is_active=True,
                    status="enabled",
                    sort_order=index,
                    fields=fields,
                    flow_nodes=[
                        dict(
                            node_order=1,
                            node_type="approval",
                            approver_type="direct_manager",
                            assignee_source="direct_manager",
                            empty_action="transfer_to_specific",
                            empty_member_id=admin.id,
                            approval_mode="or_sign",
                        )
                    ],
                ),
                admin,
            )
        if demo:
            from app.services import field_service as svc
            from app.models.field_service import (
                FieldCustomer,
                FieldProject,
                FieldSite,
                FieldAssignment,
            )
            from app.schemas.field_service import (
                CustomerInput,
                ProjectInput,
                SiteInput,
                AssignmentInput,
            )

            rule = await db.scalar(
                select(AttendanceRule).where(AttendanceRule.name == "演示现场标准班")
            )
            if not rule:
                rule = AttendanceRule(
                    name="演示现场标准班",
                    work_hour_type=WorkHourType.standard,
                    clock_in_time=time(8),
                    clock_out_time=time(17),
                    work_hours_per_day=8,
                    require_gps=False,
                    require_photo=False,
                    effective_date=date(2026, 1, 1),
                    extra_config={
                        "rule_type": "fixed",
                        "rest_periods": [{"start": "12:00", "end": "13:00"}],
                    },
                )
                db.add(rule)
                await db.flush()
            customer = await db.scalar(
                select(FieldCustomer).where(FieldCustomer.name == "演示客户")
            )
            if not customer:
                item = await svc.save_customer(
                    db, admin, CustomerInput(name="演示客户")
                )
                customer = await db.get(FieldCustomer, item["id"])
            project = await db.scalar(
                select(FieldProject).where(FieldProject.code == "DEMO-001")
            )
            if not project:
                item = await svc.save_project(
                    db,
                    admin,
                    ProjectInput(
                        code="DEMO-001",
                        name="演示生产线陪产",
                        customer_id=customer.id,
                        manager_id=manager.id,
                    ),
                )
                project = await db.get(FieldProject, item["id"])
            site = await db.scalar(
                select(FieldSite).where(FieldSite.project_id == project.id)
            )
            if not site:
                item = await svc.save_site(
                    db,
                    admin,
                    SiteInput(
                        project_id=project.id,
                        name="演示一号现场",
                        address="待配置实际地址及定位范围",
                        rule_id=rule.id,
                    ),
                )
                site = await db.get(FieldSite, item["id"])
            if not await db.scalar(
                select(FieldAssignment.id).where(
                    FieldAssignment.employee_id == staff.id
                )
            ):
                await svc.create_assignment(
                    db,
                    admin,
                    AssignmentInput(
                        site_id=site.id,
                        employee_id=staff.id,
                        start_date=svc.today(),
                        end_date=svc.today() + timedelta(days=6),
                    ),
                )
        await db.commit()


async def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--admin", action="store_true")
    args = parser.parse_args()
    await initialize()
    if args.demo or args.admin:
        await seed(args.demo)
    await engine.dispose()
    print("Database initialized; existing data preserved.")


if __name__ == "__main__":
    asyncio.run(run())
