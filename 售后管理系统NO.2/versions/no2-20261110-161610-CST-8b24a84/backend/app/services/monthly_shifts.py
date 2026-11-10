"""Shift ownership and 12-hour attendance at Beijing local wall time."""

from datetime import datetime, time, timedelta
from math import ceil

from fastapi import HTTPException
from sqlalchemy import select

from app.models.monthly_field import MonthlyPunch, MonthlyShift

REQUIRED_MINUTES = 720
DAY = timedelta(days=1)


def deadline(shift):
    if shift.kind == "night":
        return shift.started + DAY
    return datetime.combine(shift.day + DAY, time.min)


async def last_punch(db, employee_id, day):
    return await db.scalar(
        select(MonthlyPunch)
        .where(MonthlyPunch.employee_id == employee_id, MonthlyPunch.day == day)
        .order_by(MonthlyPunch.sequence.desc())
        .limit(1)
    )


async def punch_context(db, employee_id, stamp, requested_day=None):
    today = stamp.date()
    current = await db.get(MonthlyShift, (employee_id, today))
    resume = None
    if not current:
        yesterday = await db.get(MonthlyShift, (employee_id, today - DAY))
        if yesterday and yesterday.kind == "night" and stamp < deadline(yesterday):
            previous = await last_punch(db, employee_id, yesterday.day)
            if previous.kind == "in" or requested_day == yesterday.day:
                return yesterday.day, yesterday, previous, None
            resume = yesterday.day
    if requested_day is not None and requested_day != today:
        raise HTTPException(409, "该班次已经结算或已有新班次，请刷新")
    previous = await last_punch(db, employee_id, today) if current else None
    return today, current, previous, resume


def evaluate(shift, punches, stamp, review):
    end = deadline(shift)
    worked = sum(
        (min(checkout.at, end) - checkin.at).total_seconds()
        for checkin, checkout in zip(punches, punches[1:])
        if checkin.kind == "in" and checkout.kind == "out" and checkin.at < end
    )
    missing_card = punches[-1].kind != "out"
    crosses_midnight = punches[-1].at.date() > shift.day
    reasons = []
    if missing_card:
        reasons.append("缺下班卡")
    if worked < REQUIRED_MINUTES * 60:
        reasons.append("工时不足")
    if (shift.kind == "night") != crosses_midnight:
        reasons.append("打卡日期与班次不符")
    short = max(0, ceil((REQUIRED_MINUTES * 60 - worked) / 60))
    closed = stamp >= end
    if not reasons:
        state = "full"
    elif not closed:
        state = "in_progress"
    else:
        state = review.outcome if review else "pending_hours"
    return dict(
        shift=shift.kind,
        end=end,
        worked_seconds=int(worked),
        hours_status=state,
        anomalies=reasons if closed else [],
        remaining_minutes=short,
        suggested_minutes=None if missing_card else short,
        review=dict(
            outcome=review.outcome,
            missing_minutes=review.missing_minutes,
            reason=review.reason,
            manager_id=review.manager_id,
            at=review.at,
        )
        if review
        else None,
    )
