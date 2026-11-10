"""Imported projects can be configured without inventing customer or leader data."""

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register all foreign-key targets
from app.core.database import Base
from app.models.employee import Employee
from app.models.field_service import (
    FieldCustomer, FieldProject, FieldProjectCatalog,
    FieldProjectPendingMember, FieldSchemaVersion,
)
import app.bootstrap as bootstrap


@pytest.mark.asyncio
async def test_v16_migrates_old_sqlite_project_table_and_catalog(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enforce_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            # Representative pre-v16 table: both columns may be NOT NULL in an
            # established database, even though the latest ORM made manager optional.
            await connection.exec_driver_sql("""
                CREATE TABLE field_projects (
                    id INTEGER NOT NULL PRIMARY KEY,
                    code VARCHAR(50) NOT NULL UNIQUE,
                    name VARCHAR(150) NOT NULL,
                    customer_id INTEGER NOT NULL,
                    manager_id INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL,
                    FOREIGN KEY(customer_id) REFERENCES field_customers (id),
                    FOREIGN KEY(manager_id) REFERENCES employees (id),
                    CONSTRAINT ck_project_code CHECK (length(code) > 0)
                )
            """)
            await connection.exec_driver_sql(
                "CREATE INDEX idx_field_projects_name ON field_projects (name)")
            await connection.exec_driver_sql("""
                CREATE TRIGGER trg_field_projects_insert AFTER INSERT ON field_projects
                BEGIN SELECT 1; END
            """)
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            db.add(Employee(id=1, employee_no="LEAD", name="旧组长",
                            phone="19900009001", status="在职"))
            db.add(FieldCustomer(id=1, name="原客户"))
            await db.flush()
            db.add(FieldProject(id=17, code="OLD-17", name="原项目", customer_id=1,
                                manager_id=1, description="原说明", is_active=True))
            db.add_all([
                FieldProjectCatalog(code="OLD-17", name="原项目", require_gps=False),
                FieldProjectCatalog(code="NEW-18", name="已导入未配置", require_gps=True),
            ])
            await db.flush()
            db.add(FieldProjectPendingMember(project_id=17, employee_id=1,
                                             created_by=1, is_leader_auto=True))
            await db.commit()

        monkeypatch.setattr(bootstrap, "engine", engine)
        monkeypatch.setattr(bootstrap, "async_session", sessions)
        await bootstrap.initialize()
        await bootstrap.initialize()  # Re-running must not create duplicates.

        async with engine.connect() as connection:
            columns = (await connection.exec_driver_sql("PRAGMA table_info(field_projects)")).all()
            nullable = {row[1]: row[3] == 0 for row in columns}
            assert nullable["customer_id"] is True
            assert nullable["manager_id"] is True
            assert (await connection.exec_driver_sql("PRAGMA foreign_key_check")).all() == []
            assert (await connection.exec_driver_sql("PRAGMA foreign_keys")).scalar_one() == 1
            assert (await connection.exec_driver_sql("""
                SELECT count(*) FROM sqlite_master
                WHERE type='index' AND name='idx_field_projects_name'
            """)).scalar_one() == 1
            assert (await connection.exec_driver_sql("""
                SELECT count(*) FROM sqlite_master
                WHERE type='trigger' AND name='trg_field_projects_insert'
            """)).scalar_one() == 1
            with pytest.raises(IntegrityError):
                await connection.exec_driver_sql("""
                    INSERT INTO field_projects
                        (code, name, customer_id, manager_id, description, is_active)
                    VALUES ('', 'invalid', NULL, NULL, '', 1)
                """)
            await connection.rollback()
        async with sessions() as db:
            projects = (await db.scalars(select(FieldProject).order_by(FieldProject.code))).all()
            assert [(p.code, p.customer_id, p.manager_id) for p in projects] == [
                ("NEW-18", None, None), ("OLD-17", 1, 1)]
            assert next(p for p in projects if p.code == "OLD-17").id == 17
            assert await db.scalar(select(FieldProjectPendingMember.project_id)) == 17
            assert await db.get(FieldSchemaVersion, 16) is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_v16_initializes_fresh_sqlite_schema(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        monkeypatch.setattr(bootstrap, "engine", engine)
        monkeypatch.setattr(bootstrap, "async_session", sessions)
        await bootstrap.initialize()
        async with sessions() as db:
            assert await db.get(FieldSchemaVersion, 16) is not None
            db.add(FieldProject(code="NO-CUSTOMER", name="待配置项目",
                                customer_id=None, manager_id=None))
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_v16_rejects_unknown_sqlite_constraint_without_changing_table(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'custom.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("""
                CREATE TABLE field_projects (
                    id INTEGER NOT NULL PRIMARY KEY,
                    code VARCHAR(50) NOT NULL UNIQUE,
                    name VARCHAR(150) NOT NULL UNIQUE,
                    customer_id INTEGER NOT NULL,
                    manager_id INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL,
                    FOREIGN KEY(customer_id) REFERENCES field_customers (id),
                    FOREIGN KEY(manager_id) REFERENCES employees (id)
                )
            """)
        monkeypatch.setattr(bootstrap, "engine", engine)
        monkeypatch.setattr(bootstrap, "async_session", sessions)
        with pytest.raises(RuntimeError, match="unexpected constraints"):
            await bootstrap.initialize()
        async with engine.connect() as connection:
            columns = (await connection.exec_driver_sql("PRAGMA table_info(field_projects)")).all()
            assert {row[1]: row[3] for row in columns}["customer_id"] == 1
    finally:
        await engine.dispose()
