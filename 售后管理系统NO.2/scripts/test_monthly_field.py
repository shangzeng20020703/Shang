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
from app.models.monthly_field import MonthlyAbsence
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
        await req("POST", "/monthly/policy", json=policy)
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
    await engine.dispose()
    print(
        "NO.2 monthly attendance, permissions, GPS, overnight, approvals and settlement passed"
    )


if __name__ == "__main__":
    asyncio.run(main())
