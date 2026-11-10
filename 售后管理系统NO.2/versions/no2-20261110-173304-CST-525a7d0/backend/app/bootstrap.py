"""Non-destructive first-install schema and explicit local demonstration fixtures."""

import argparse, asyncio, os
from datetime import date, time, timedelta
from sqlalchemy import inspect, select, update
from app.core.database import Base, engine, async_session
from app.core.config import settings
import app.models
import app.models.monthly_field
import app.models.field_service
from app.models.field_service import FieldProject, FieldProjectCatalog, FieldSchemaVersion
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


async def _project_columns(connection):
    return {column["name"]: column for column in await connection.run_sync(
        lambda sync_connection: inspect(sync_connection).get_columns("field_projects")
    )}


async def _migrate_project_nullable_columns():
    """Allow catalog projects to exist before a customer or leader is assigned.

    This is deliberately separate from create_all: create_all never changes an
    existing column. MySQL DDL is not transactional, so each step is inspected
    before execution and can be retried after an interrupted bootstrap.
    """
    async with engine.connect() as connection:
        columns = await _project_columns(connection)
        await connection.commit()
        pending = [name for name in ("customer_id", "manager_id")
                   if not columns[name]["nullable"]]
        if not pending:
            return
        dialect = connection.dialect.name
        if dialect == "sqlite":
            expected = {"id", "code", "name", "customer_id", "manager_id",
                        "description", "is_active"}
            if set(columns) != expected:
                raise RuntimeError("field_projects has unexpected columns; inspect before SQLite v16 migration")
            def inspect_constraints(sync_connection):
                inspector = inspect(sync_connection)
                return (inspector.get_pk_constraint("field_projects"),
                        inspector.get_unique_constraints("field_projects"),
                        inspector.get_foreign_keys("field_projects"),
                        inspector.get_check_constraints("field_projects"))

            primary, unique, foreign, checks = await connection.run_sync(inspect_constraints)
            unique_indexes = (await connection.exec_driver_sql(
                "PRAGMA index_list('field_projects')")).all()
            for index in unique_indexes:
                if index[2]:
                    escaped_name = str(index[1]).replace("'", "''")
                    index_columns = (await connection.exec_driver_sql(
                        f"PRAGMA index_info('{escaped_name}')")).all()
                    if [column[2] for column in index_columns] != ["code"]:
                        raise RuntimeError("field_projects has unexpected constraints; inspect before SQLite v16 migration")
            expected_foreign = {
                (("customer_id",), "field_customers", ("id",)),
                (("manager_id",), "employees", ("id",)),
            }
            actual_foreign = {
                (tuple(row["constrained_columns"]), row["referred_table"],
                 tuple(row["referred_columns"])) for row in foreign
            }
            if (primary["constrained_columns"] != ["id"]
                    or any(row["column_names"] != ["code"] for row in unique)
                    or actual_foreign != expected_foreign
                    or any(row.get("options") for row in foreign)
                    or any(column.get("default") is not None for column in columns.values())
                    or any(not row.get("sqltext") for row in checks)):
                raise RuntimeError("field_projects has unexpected constraints; inspect before SQLite v16 migration")
            quote = connection.dialect.identifier_preparer.quote
            check_ddl = [
                f"{('CONSTRAINT ' + quote(row['name']) + ' ') if row.get('name') else ''}"
                f"CHECK ({row['sqltext']})"
                for row in checks
            ]
            new_table_ddl = "CREATE TABLE _field_projects_v16 (\n" + ",\n".join([
                "id INTEGER NOT NULL PRIMARY KEY",
                "code VARCHAR(50) NOT NULL UNIQUE",
                "name VARCHAR(150) NOT NULL",
                "customer_id INTEGER",
                "manager_id INTEGER",
                "description TEXT NOT NULL",
                "is_active BOOLEAN NOT NULL",
                "FOREIGN KEY(customer_id) REFERENCES field_customers (id)",
                "FOREIGN KEY(manager_id) REFERENCES employees (id)",
                *check_ddl,
            ]) + "\n)"
            # SQLite cannot DROP NOT NULL. Rebuild under a real write transaction;
            # foreign keys must be disabled before it starts because child tables
            # already reference field_projects. IDs and the unique code survive.
            await connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            await connection.commit()
            enabled = (await connection.exec_driver_sql("PRAGMA foreign_keys")).scalar_one()
            await connection.commit()
            if enabled:
                raise RuntimeError("Could not suspend SQLite foreign keys for v16 migration")
            try:
                async with connection.begin():
                    await connection.exec_driver_sql("BEGIN IMMEDIATE")
                    # SQLite drops table-owned indexes and triggers with the
                    # old table. Recreate explicit ones after the swap;
                    # automatic UNIQUE(code) is recreated by the new DDL.
                    custom_objects = (await connection.exec_driver_sql("""
                        SELECT sql FROM sqlite_master
                        WHERE tbl_name = 'field_projects'
                          AND type IN ('index', 'trigger') AND sql IS NOT NULL
                    """)).scalars().all()
                    await connection.exec_driver_sql(new_table_ddl)
                    await connection.exec_driver_sql("""
                        INSERT INTO _field_projects_v16
                            (id, code, name, customer_id, manager_id, description, is_active)
                        SELECT id, code, name, customer_id, manager_id, description, is_active
                        FROM field_projects
                    """)
                    await connection.exec_driver_sql("DROP TABLE field_projects")
                    await connection.exec_driver_sql(
                        "ALTER TABLE _field_projects_v16 RENAME TO field_projects")
                    for ddl in custom_objects:
                        await connection.exec_driver_sql(ddl)
                    violations = (await connection.exec_driver_sql("PRAGMA foreign_key_check")).all()
                    if violations:
                        raise RuntimeError(f"SQLite foreign key check failed after v16 migration: {violations[:3]}")
            finally:
                await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                await connection.commit()
            enabled = (await connection.exec_driver_sql("PRAGMA foreign_keys")).scalar_one()
            violations = (await connection.exec_driver_sql("PRAGMA foreign_key_check")).all()
            await connection.commit()
            if not enabled or violations:
                raise RuntimeError("SQLite foreign keys could not be verified after v16 migration")
        elif dialect == "mysql":
            quote = connection.dialect.identifier_preparer.quote
            for name in pending:
                sql_type = columns[name]["type"].compile(dialect=connection.dialect)
                await connection.exec_driver_sql(
                    f"ALTER TABLE {quote('field_projects')} MODIFY COLUMN {quote(name)} {sql_type} NULL")
                await connection.commit()
        elif dialect == "postgresql":
            for name in pending:
                await connection.exec_driver_sql(
                    f'ALTER TABLE field_projects ALTER COLUMN "{name}" DROP NOT NULL')
                await connection.commit()
        else:
            raise RuntimeError(f"Unsupported database for field schema v16: {dialect}")
        result = await _project_columns(connection)
        if any(not result[name]["nullable"] for name in ("customer_id", "manager_id")):
            raise RuntimeError("field_projects nullable migration did not complete")
        await connection.commit()


async def initialize():
    # Additive tables: identity, project bindings, participation intervals and punch segments.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        columns = await connection.run_sync(
            lambda c: inspect(c).get_columns("field_monthly_policies"))
        if not any(col["name"] == "shifts" for col in columns):
            await connection.exec_driver_sql(
                "ALTER TABLE field_monthly_policies ADD COLUMN shifts BOOLEAN NOT NULL DEFAULT FALSE")
    from app.monthly_schema import allow_unlocated_corrections
    await allow_unlocated_corrections(engine)
    await _migrate_project_nullable_columns()
    async with async_session() as db:
        from app.services.field_membership import migrate_assignments
        await migrate_assignments(db)
        if not await db.get(FieldSchemaVersion, 6):
            # Retire legacy employment labels without unlocking any existing account.
            await db.execute(update(Employee).where(Employee.status.in_(["待入职", "停用"])).values(status="在职"))
            await db.execute(update(Employee).where(Employee.status == "试用期").values(status="试用"))
        # Earlier imports may have left catalog-only rows without a project ID.
        # Backfill before stamping v16 so regional assignment and personnel
        # confirmation work immediately after an upgrade.
        catalog_rows = (await db.scalars(select(FieldProjectCatalog))).all()
        existing_codes = set((await db.scalars(select(FieldProject.code))).all())
        for row in catalog_rows:
            if row.code not in existing_codes:
                db.add(FieldProject(code=row.code, name=row.name,
                                    customer_id=None, manager_id=None))
                existing_codes.add(row.code)
        await db.flush()
        if not await db.get(FieldSchemaVersion, 20):
            from app.services.manager_inbox import backfill
            await backfill(db)
        for version in range(1, 22):
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
