"""v21: approved manual entries have no GPS observation."""

import re

from sqlalchemy import inspect


async def allow_unlocated_corrections(engine):
    table = "field_monthly_punches"
    async with engine.connect() as conn:
        columns = await conn.run_sync(lambda c: inspect(c).get_columns(table))
        required = [
            c
            for c in columns
            if c["name"] in {"latitude", "longitude"} and not c["nullable"]
        ]
        if not required:
            return
        if conn.dialect.name != "sqlite":
            for c in required:
                name = c["name"]
                if conn.dialect.name == "mysql":
                    spec = c["type"].compile(dialect=conn.dialect)
                    change = f"MODIFY COLUMN {name} {spec} NULL"
                else:
                    change = f"ALTER COLUMN {name} DROP NOT NULL"
                await conn.exec_driver_sql(f"ALTER TABLE {table} {change}")
            await conn.commit()
            return
        await conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        await conn.commit()
        try:
            await conn.exec_driver_sql("BEGIN IMMEDIATE")
            ddl = (
                await conn.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                )
            ).scalar_one()
            ddl = ddl.replace(table, "_monthly_punches_v21", 1)
            for c in required:
                ddl, n = re.subn(
                    r"\b(" + c["name"] + r"\s+\w+)\s+NOT NULL\b", r"\1", ddl, flags=re.I
                )
                if n != 1:
                    raise RuntimeError("Unrecognized GPS column definition")
            objects = (
                (
                    await conn.exec_driver_sql(
                        "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
                        (table,),
                    )
                )
                .scalars()
                .all()
            )
            await conn.exec_driver_sql(ddl)
            await conn.exec_driver_sql(
                f"INSERT INTO _monthly_punches_v21 SELECT * FROM {table}"
            )
            await conn.exec_driver_sql(f"DROP TABLE {table}")
            await conn.exec_driver_sql(
                f"ALTER TABLE _monthly_punches_v21 RENAME TO {table}"
            )
            for sql in objects:
                await conn.exec_driver_sql(sql)
            if (await conn.exec_driver_sql("PRAGMA foreign_key_check")).first():
                raise RuntimeError("Foreign key check failed during v21 migration")
            await conn.commit()
        finally:
            await conn.rollback()
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            await conn.commit()
