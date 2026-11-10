"""Effective-dated earned leave for NO.2. Work and leave are dated by attendance day."""

from datetime import date

from sqlalchemy import func, select, update
from app.models.employee import Employee
from app.models.monthly_field import (
    MonthlyAbsence,
    MonthlyRestFact,
    MonthlyPunch,
    MonthlyShift,
)
from app.models.system_setting import SystemSetting
from app.services.monthly_shifts import evaluate

START = "monthly_earned_rest_start"


async def effective(db):
    setting = await db.get(SystemSetting, START)
    return date.fromisoformat(setting.value) if setting else None


async def enable(db, day):
    if not await effective(db):
        db.add(SystemSetting(name=START, value=day.isoformat()))


async def lock_employee(db, employee_id):
    # SQLite ignores SELECT FOR UPDATE. Reserve its writer before checking facts.
    if db.bind.dialect.name == "sqlite":
        await db.execute(
            update(MonthlyAbsence).where(False).values(active=MonthlyAbsence.active)
        )
    await db.scalar(
        select(Employee.id).where(Employee.id == employee_id).with_for_update()
    )


async def sync_work(db, punch, stamp, review=None):
    start = await effective(db)
    if not start or punch.day < start:
        return
    key = (punch.employee_id, punch.day)
    shift = await db.get(MonthlyShift, key)
    logs = list(
        await db.scalars(
            select(MonthlyPunch)
            .where(
                MonthlyPunch.employee_id == punch.employee_id,
                MonthlyPunch.day == punch.day,
            )
            .order_by(MonthlyPunch.sequence)
        )
    )
    full = evaluate(shift, logs, stamp, review)["hours_status"] in {"full", "normal"}
    fact = await db.get(MonthlyRestFact, key)
    if full:
        if fact is None:
            fact = MonthlyRestFact(employee_id=punch.employee_id, day=punch.day)
            db.add(fact)
        fact.punch_id = punch.id
    elif fact and fact.punch_id:
        await db.delete(fact)


async def totals(db, ids, start, day):
    if not start or day < start or not ids:
        return {}
    query = (
        select(
            MonthlyRestFact.employee_id,
            func.count(MonthlyRestFact.punch_id),
            func.count(MonthlyRestFact.rest_id),
        )
        .where(
            MonthlyRestFact.employee_id.in_(ids),
            MonthlyRestFact.day.between(start, day),
        )
        .group_by(MonthlyRestFact.employee_id)
    )
    return {
        eid: (work // 6 - used, work % 6) for eid, work, used in await db.execute(query)
    }


async def personal_balance(db, employee_id, start, day, *, locking):
    if not locking:
        balances = await totals(db, [employee_id], start, day)
        return balances.get(employee_id, (0, 0))
    # InnoDB locking reads see the latest facts after the employee lock was acquired.
    facts = await db.execute(
        select(MonthlyRestFact.punch_id, MonthlyRestFact.rest_id)
        .where(
            MonthlyRestFact.employee_id == employee_id,
            MonthlyRestFact.day.between(start, day),
        )
        .with_for_update()
    )
    worked = used = 0
    for punch_id, rest_id in facts:
        worked += punch_id is not None
        used += rest_id is not None
    return worked // 6 - used, worked % 6
