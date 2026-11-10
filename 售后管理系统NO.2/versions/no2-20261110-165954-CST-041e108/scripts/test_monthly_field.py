"""NO.2 cutover and month close acceptance against an independent SQLite database."""

import asyncio
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

scratch = tempfile.TemporaryDirectory()
os.environ.update(
    DATABASE_URL_OVERRIDE="sqlite+aiosqlite:///" + scratch.name + "/test.db",
    ENVIRONMENT="development",
    BACKGROUND_JOBS="false",
    LOCAL_PASSWORDLESS_LOGIN="true",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.bootstrap import initialize, seed
from app.core.database import async_session, engine
from app.core.security import create_access_token
from app.main import app
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.attendance import AttendanceEffect
from app.models.field_service import FieldProject, FieldProjectCatalog, FieldFile
from app.models.monthly_field import MonthlyAbsence, MonthlyPolicy
from app.services import monthly_field as service


async def main():
    await initialize()
    await seed(False)
    clock = datetime(2030, 3, 1, 10)
    service.now = lambda: clock
    async with async_session() as db:
        for number, role in [
            ("001", "employee"),
            ("017", "regional_lead"),
            ("019", "aftersales_manager"),
        ]:
            e = Employee(
                employee_no=number,
                name="模拟" + number,
                phone="19900008" + number,
                hashed_password="test-disabled",
                is_field_staff=True,
                status="在职",
                hire_date=date(2030, 2, 1),
            )
            db.add(e)
            await db.flush()
            db.add(EmployeeRole(employee_id=e.id, role_name=role))
        ids = {e.employee_no: e.id for e in await db.scalars(select(Employee))}
        for i, (code, lat) in enumerate([("A", 39), ("B", 39.1), ("C", 39)]):
            db.add(
                FieldProject(
                    code=code,
                    name="地点" + code,
                    manager_id=ids["001"] if not i else None,
                )
            )
            db.add(
                FieldProjectCatalog(
                    code=code, name="地点" + code, latitude=lat, longitude=116
                )
            )
        db.add(FieldFile(owner_id=ids["001"], path="/uploads/attendance/test.jpg"))
        await db.commit()
    app.state.limiter.enabled = False
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 33111)),
        base_url="http://127.0.0.1",
    ) as client:

        async def req(method, path, who="019", expected=200, **kwargs):
            response = await client.request(
                method,
                "/api/v1" + path,
                headers={
                    "Authorization": "Bearer " + create_access_token(str(ids[who]))
                },
                **kwargs,
            )
            assert response.status_code == expected, (
                path,
                response.status_code,
                response.text[:300],
            )
            return response.json()

        async def own(month="2030-03-01"):
            return (await req("GET", "/monthly/me?month=" + month, "001"))[0]

        async def hit(kind, lat, expected=200, **extra):
            data = dict(
                kind=kind,
                latitude=lat,
                longitude=116,
                request_key=str(uuid4()),
                photo="/uploads/attendance/test.jpg",
            )
            return await req(
                "POST", "/monthly/punch", "001", expected, json={**data, **extra}
            )

        policy = dict(effective="2030-03-01", cutoff=240, minutes=480, photo=True)
        await req("POST", "/monthly/policy", "017", 403, json=policy)
        # Existing version-17 rule retains its 04:00 cutoff and 8-hour target.
        async with async_session() as db:
            db.add(MonthlyPolicy(**{**policy, "effective": date(2030, 3, 1)}))
            await db.commit()
        await req(
            "POST",
            "/monthly/policy",
            expected=422,
            json={**policy, "effective": "2030-04-01"},
        )
        for who in ("001", "017"):
            await req(
                "POST",
                "/auth/management-login",
                who,
                403,
                json={"phone": who, "password": ""},
            )
            await req("GET", "/field/dashboard", who, 403)
            await req("GET", "/monthly/report?month=2030-03-01", who, 403)
        await req("GET", "/rotation/me/entitlement", "001")
        assert {
            r["employee_id"]
            for r in await req(
                "GET",
                "/monthly/me?month=2030-03-01&employee_id=" + str(ids["017"]),
                "001",
            )
        } == {ids["001"]}
        await req(
            "POST",
            "/monthly/rest",
            "001",
            json={"day": "2030-03-05", "reason": "申请额外休息"},
        )
        clock = datetime(2030, 3, 5, 23)
        r = await own()
        assert (
            r["rest_used"] == 4
            and r["days"][-1]["status"] == "waiting"
            and r["absence_days"] == 0
        )
        clock = datetime(2030, 3, 6, 4)
        r = await own()
        assert (
            r["absence_days"] == 1
            and r["days"][-2]["end"] == "2030-03-06T04:00:00"
            and r["days"][-2]["application"]["status"] == "pending"
        )
        rest = (await req("GET", "/monthly/requests"))[0]
        review = f"/monthly/requests/{rest['id']}/review"
        await req("POST", review, "017", 403, json={"approved": True})
        await req("POST", review, json={"approved": True, "note": "同意"})
        await req("POST", review, expected=409, json={"approved": True})
        assert (await own())["absence_days"] == 0
        await hit("in", 39.3, 422)
        await hit("in", 39, 422, photo="")
        key = str(uuid4())
        p = await hit("in", 39, request_key=key)
        assert (
            len(p["matches"]) == 2
            and (await hit("in", 39, request_key=key))["id"] == p["id"]
        )
        assert (await own())["days"][-1]["status"] == "conflict"
        await req(
            "POST",
            "/monthly/confirm",
            json={"punch_id": p["id"], "code": "B"},
            expected=409,
        )
        await req("POST", "/monthly/confirm", json={"punch_id": p["id"], "code": "A"})
        clock = datetime(2030, 3, 7, 2)
        p = await hit("out", 39.1)
        assert (
            p["day"] == "2030-03-06"
            and (await own())["days"][-1]["status"] == "conflict"
        )
        await req("POST", "/monthly/confirm", json={"punch_id": p["id"], "code": "B"})
        await req(
            "PUT",
            "/monthly/places/B",
            json={"latitude": 40, "longitude": 116, "radius": 200, "address": "新位置"},
        )
        assert (await own())["days"][-1]["project"]["latitude"] == 39.1
        clock = datetime(2030, 3, 7, 5)
        await hit("in", 40)
        clock = datetime(2030, 3, 8, 5)
        row = (await own())["days"][-2]
        assert row["status"] == "present" and set(row["anomalies"]) == {
            "缺下班卡",
            "工时不足",
        }
        await req(
            "POST", "/monthly/rest", "001", json={"day": "2030-03-08", "reason": "休息"}
        )
        pending = (await req("GET", "/monthly/requests"))[0]
        await req(
            "POST",
            f"/monthly/requests/{pending['id']}/review",
            json={"approved": False},
        )
        async with async_session() as db:
            db.add(
                AttendanceEffect(
                    employee_id=ids["001"],
                    source_event_id="trip",
                    effect_type="business_trip",
                    effect_status="active",
                    work_date=date(2030, 3, 9),
                    payload_json={"effect_detail": {"full_day": True}},
                )
            )
            from app.models.system_setting import SystemSetting

            db.add(
                SystemSetting(
                    name="legal_holidays_2030",
                    value='{"revision":1,"entries":[{"date":"2030-03-10","holiday_name":"公司节日"}]}',
                )
            )
            await db.commit()
        clock = datetime(2030, 4, 2, 5)
        march = (await own())["days"]
        assert [r["status"] for r in march[7:10]] == ["absent", "trip", "holiday"]
        assert (await own("2030-04-01"))["rest_used"] == 1
        async with async_session() as db:
            await service.settle_closed_months(db)
            await db.commit()
            await service.settle_closed_months(db)
            assert not (
                await db.get(MonthlyAbsence, (ids["001"], date(2030, 3, 5)))
            ).active

        await req(
            "POST",
            "/monthly/policy",
            json=dict(effective="2030-04-03", minutes=720, cutoff=0, photo=True),
        )
        clock = datetime(2030, 4, 3, 8)
        await hit("in", 40, 422)
        await hit("in", 40, shift="day")
        clock = datetime(2030, 4, 3, 14)
        await hit("out", 40)
        clock = datetime(2030, 4, 3, 15)
        await hit("in", 40, 409, shift="night")
        await hit("in", 40)
        clock = datetime(2030, 4, 3, 21)
        await hit("out", 40)
        assert (await own("2030-04-01"))["days"][-1]["hours_status"] == "full"
        clock = datetime(2030, 4, 4, 8)
        await hit("in", 40, shift="day")
        clock = datetime(2030, 4, 4, 19, 59)
        short = await hit("out", 40)
        decision = dict(
            punch_id=short["id"],
            outcome="shortage",
            missing_minutes=1,
            reason="核对工时",
        )
        await req("POST", "/monthly/hours/review", expected=409, json=decision)
        clock = datetime(2030, 4, 5)
        row = (await own("2030-04-01"))["days"][-2]
        assert row["status"] == "pending_hours" and row["suggested_minutes"] == 1
        await req("POST", "/monthly/hours/review", "017", 403, json=decision)
        await req("POST", "/monthly/hours/review", json=decision)
        await req("POST", "/monthly/hours/review", expected=409, json=decision)
        april = await own("2030-04-01")
        assert april["shortage_days"] == april["shortage_minutes"] == 1
        for day in (6, 7):
            clock = datetime(2030, 4, day, 8)
            await hit("in", 40, shift="day")
            clock = datetime(2030, 4, day, 20)
            await hit("out", 40)
        clock = datetime(2030, 4, 29, 20)
        first = await hit("in", 40, shift="night")
        clock = datetime(2030, 4, 30, 19, 59)
        assert (await req("GET", "/monthly/context", "001"))["next_kind"] == "out"
        clock = datetime(2030, 4, 30, 20)
        row = (await own("2030-04-01"))["days"][-2]
        assert row["status"] == "pending_hours" and row["suggested_minutes"] is None
        await req(
            "POST",
            "/monthly/hours/review",
            json=dict(
                punch_id=first["id"],
                outcome="normal",
                missing_minutes=0,
                reason="现场核实满勤",
            ),
        )
        await hit("in", 40, shift="night")
        clock = datetime(2030, 5, 1, 6)
        ctx = await req("GET", "/monthly/context", "001")
        assert ctx["day"] == "2030-04-30" and ctx["next_kind"] == "out"
        assert (await hit("out", 40))["day"] == "2030-04-30"
        assert (await req("GET", "/monthly/context", "001"))[
            "resume_night_day"
        ] == "2030-04-30"
        await hit("in", 40, attendance_day="2030-04-30")
        clock = datetime(2030, 5, 1, 8)
        await hit("out", 40)
        april = await own("2030-04-01")
        assert april["full_days"] == 5 and april["days"][-1]["worked_seconds"] == 43200
        assert (
            april["days"][-2]["status"] == "normal"
            and april["days"][-2]["worked_seconds"] == 0
        )
        assert (await own("2030-05-01"))["days"][-1]["status"] == "waiting"
        clock = datetime(2030, 5, 1, 20)
        assert (await hit("in", 40, shift="night"))["day"] == "2030-05-01"
        clock = datetime(2030, 5, 2, 8)
        assert (await hit("out", 40))["day"] == "2030-05-01"
        clock = datetime(2030, 5, 3, 1)
        may = await own("2030-05-01")
        assert [r["status"] for r in may["days"][:2]] == ["full", "absent"]
        assert april["work_progress"] == 5 and april["rest_balance"] == 0
        assert may["rest_balance"] == 1 and may["work_progress"] == 0
        assert may["missing_punches"] == 2 and may["absence_days"] == 1
        clock = datetime(2030, 5, 3, 7)
        await hit("in", 39, shift="night")
        clock = datetime(2030, 5, 3, 19)
        same_day = await hit("out", 39)
        clock = datetime(2030, 5, 4, 8)
        row = (await own("2030-05-01"))["days"][-2]
        assert not row["project"] and row["anomalies"] == ["打卡日期与班次不符"]
        decisions = (await req("GET", "/monthly/manager-inbox"))["items"]
        assert {t["kind"] for t in decisions if t["id"] == same_day["id"]} == {
            "project",
            "hours",
        }
        await req(
            "POST", "/monthly/confirm", json={"punch_id": same_day["id"], "code": "A"}
        )
        assert (await own("2030-05-01"))["days"][-2]["status"] == "pending_hours"
        clock = datetime(2030, 5, 4, 15)
        await hit("in", 40, shift="day")
        clock = datetime(2030, 5, 5, 3)
        await hit("out", 40, 409, attendance_day="2030-05-04")
        row = (await own("2030-05-01"))["days"][-2]
        assert row["status"] == "pending_hours" and row["worked_seconds"] == 0
        assert row["missing_punches"] == 1
        assert (
            april["days"][3]["missing_punches"] == 0 and april["shortage_minutes"] == 1
        )
        assert (
            april["days"][28]["missing_punches"] == 1
            and april["days"][28]["status"] == "normal"
        )

        await req("GET", "/monthly/manager-inbox", "001", 403)
        await req("GET", "/monthly/manager-inbox?count_only=true", "017", 403)

        async def inbox():
            result = await req("GET", "/monthly/manager-inbox")
            assert (
                (await req("GET", "/monthly/manager-inbox?count_only=true"))["total"]
                == result["total"]
                == len(result["items"])
            )
            assert [t["since"] for t in result["items"]] == sorted(
                t["since"] for t in result["items"]
            )
            return result["items"]

        async def rest(day, mode):
            return await req(
                "POST",
                "/monthly/rest",
                "001",
                json=dict(day=day, mode=mode, reason="休假测试"),
            )

        preview = await req("GET", "/monthly/me/rest-preview?day=2030-05-05", "001")
        assert preview["balance"] == 1 and preview["mode"] == "direct"
        assert (await rest("2030-05-05", "direct"))["status"] == "approved"
        await rest("2030-05-05", "direct")
        await hit("in", 40, 409, shift="day")
        assert (await own("2030-05-01"))["rest_balance"] == 0
        clock = datetime(2030, 5, 6, 8)
        item = await rest("2030-05-06", "request")
        assert (
            item["status"] == "pending"
            and (await own("2030-05-01"))["rest_balance"] == 0
        )
        rows = await inbox()
        task = next(t for t in rows if t["kind"] == "rest" and t["day"] == "2030-05-06")
        assert task["balance"] == 0
        review_url = f"/monthly/requests/{item['id']}/review"
        await req("POST", review_url, json={"approved": True})
        await req("POST", review_url, expected=409, json={"approved": True})
        assert len(await inbox()) == len(rows) - 1
        assert (await own("2030-05-01"))["rest_balance"] == -1
        for day in range(7, 13):
            clock = datetime(2030, 5, day, 8)
            await hit("in", 40, shift="day")
            clock = datetime(2030, 5, day, 20)
            await hit("out", 40)
        may = await own("2030-05-01")
        assert (
            may["rest_balance"] == may["work_progress"] == 0 and may["leave_days"] == 2
        )
        item = await rest("2030-05-02", "request")
        await req(
            "POST", f"/monthly/requests/{item['id']}/review", json={"approved": True}
        )
        after = await own("2030-05-01")
        assert (
            after["rest_balance"] == -1
            and after["missing_punches"] == may["missing_punches"] - 2
        )
        assert after["absence_days"] == may["absence_days"] - 1
        assert (await own())["earned_rest"] is False
        clock = datetime(2030, 5, 13, 8)
        denied = await rest("2030-05-13", "request")
        await req(
            "POST", f"/monthly/requests/{denied['id']}/review", json={"approved": False}
        )
        assert (await own("2030-05-01"))["rest_balance"] == -1
        clock = datetime(2030, 6, 10, 9)
        assert any(
            t["kind"] == "hours" and t["day"] == "2030-05-04" and t["suggested"] is None
            for t in await inbox()
        )

        # Real entry points: a correction is reviewed once, affects the owning
        # month and keeps GPS counts and the earlier hours decision intact.
        clock = datetime(2030, 6, 10, 8)
        original = await hit("in", 40, shift="day")
        clock = datetime(2030, 6, 11, 9)
        begin = "/monthly/me/corrections"

        def payload(day, start=None, end=None, shift="day"):
            return dict(
                day=day,
                check_in=start,
                check_out=end,
                shift=shift,
                project_code="B",
                reason="核对现场签到表",
            )

        def decision(ident):
            return f"/monthly/corrections/{ident}/review"

        one = payload("2030-06-10", end="2030-06-10T20:00")
        await req(
            "POST",
            "/monthly/hours/review",
            json=dict(
                punch_id=original["id"],
                outcome="shortage",
                missing_minutes=120,
                reason="仅有上班卡",
            ),
        )
        before = await own("2030-06-01")
        item = await req("POST", begin, "001", json=one)
        url = decision(item["id"])
        assert any(
            t["kind"] == "correction" and t["id"] == item["id"] for t in await inbox()
        )
        await req("POST", begin, "001", 409, json=one)
        await req("POST", url, "001", 403, json={"approved": True})
        await req("POST", url, json={"approved": True})
        await req("POST", url, expected=409, json={"approved": True})
        accepted = await own("2030-06-01")
        d = next(d for d in accepted["days"] if d["day"] == one["day"])
        assert d["hours_status"] == "full" and d["worked_seconds"] == 12 * 3600
        assert [p["corrected"] for p in d["punches"]] == [False, True]
        assert d["prior_hours_reviews"][0]["missing_minutes"] == 120
        assert accepted["missing_punches"] == before["missing_punches"] - 1
        assert (
            accepted["punch_count"] == before["punch_count"]
            and accepted["corrected_count"] == 1
        )
        assert accepted["work_progress"] == (before["work_progress"] + 1) % 6
        assert not any(
            t["kind"] == "correction" and t["id"] == item["id"] for t in await inbox()
        )

        night = payload("2030-05-31", "2030-05-31T20:00", "2030-06-01T06:00", "night")
        old_may = await own("2030-05-01")
        item = await req("POST", begin, "001", json=night)
        await req(
            "POST", decision(item["id"]), json={"approved": False, "note": "请核实项目"}
        )
        item = await req("POST", begin, "001", json=night)
        await req("POST", decision(item["id"]), json={"approved": True})
        may = await own("2030-05-01")
        d = may["days"][-1]
        assert [c["status"] for c in d["corrections"]] == ["rejected", "approved"]
        assert (
            d["shift"] == "night"
            and d["hours_status"] == "pending_hours"
            and d["suggested_minutes"] == 120
        )
        assert (
            d["project"]["code"] == "B"
            and d["missing_punches"] == 0
            and d["corrected_count"] == 2
        )
        assert (
            may["missing_punches"] == old_may["missing_punches"] - 2
            and may["absence_days"] == old_may["absence_days"] - 1
        )
        assert (await own("2030-06-01"))["corrected_count"] == 1
        await req(
            "POST",
            begin,
            "001",
            409,
            json=payload("2030-06-01", "2030-06-01T05:00", "2030-06-01T17:00"),
        )
        await req(
            "POST",
            begin,
            "001",
            422,
            json=payload("2030-05-12", "2030-05-12T08:00", "2030-05-12T20:00"),
        )
        boundary = await req(
            "POST",
            begin,
            "001",
            json=payload("2030-05-13", "2030-05-13T08:00", "2030-05-13T20:00"),
        )
        clock = datetime(2030, 6, 12, 9)
        await req("POST", decision(boundary["id"]), json={"approved": True})

        # A preserved checkout-only shift can be completed without rewriting
        # that physical checkout or reusing its prior hours decision.
        from app.models.monthly_field import MonthlyShift, MonthlyPunch

        async with async_session() as db:
            eid = ids["001"]
            when = datetime(2030, 6, 11, 20)
            db.add(
                MonthlyShift(employee_id=eid, day=when.date(), kind="day", started=when)
            )
            db.add(
                MonthlyPunch(
                    employee_id=eid,
                    day=when.date(),
                    kind="out",
                    at=when,
                    sequence=1,
                    request_key=str(uuid4()),
                    latitude=40,
                    longitude=116,
                    matches=[dict(code="B", name="地点B")],
                )
            )
            await db.commit()
        item = await req(
            "POST", begin, "001", json=payload("2030-06-11", start="2030-06-11T10:00")
        )
        await req("POST", decision(item["id"]), json={"approved": True})
        result = await own("2030-06-01")
        d = next(d for d in result["days"] if d["day"] == "2030-06-11")
        assert d["missing_punches"] == 0 and d["suggested_minutes"] == 120
        assert [p["corrected"] for p in d["punches"]] == [True, False]
        await req(
            "POST",
            "/monthly/hours/review",
            json=dict(
                punch_id=d["revision"],
                outcome="shortage",
                missing_minutes=120,
                reason="补卡后工时核对",
            ),
        )
    await engine.dispose()
    print(
        "NO.2 monthly attendance, permissions, GPS, overnight, approvals and settlement passed"
    )


if __name__ == "__main__":
    asyncio.run(main())
