"""Run an isolated API + SQLite check; upstream HTTPS is replaced by MockTransport."""
import asyncio
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

sandbox = tempfile.TemporaryDirectory(prefix="aftersales-tegene-test-")
os.environ["DATABASE_URL_OVERRIDE"] = "sqlite+aiosqlite:///" + sandbox.name + "/test.db"
os.environ["BACKGROUND_JOBS"] = "false"
os.environ["LOGIN_MODE"] = "password"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from httpx import AsyncClient, ASGITransport, MockTransport, Response
from app.bootstrap import initialize, seed
from app.core.config import settings
from app.core.database import engine, async_session
from app.models.attendance import AttendanceRecord
from app.main import app
from app.services.field_service import today


async def main():
    await initialize()
    await seed(True)
    await initialize()
    passed = []
    rows = [{"id": str(UUID(int=i + 1)), "projectNo": f"TG{i:03}", "name": f"项目{i}",
             "customer": "接口验收客户", "status": "active", "operatingStatus": "building",
             "managerName": "Tegene 项目经理", "siteAddress": "验收现场地址",
             "contractAmount": 99999} for i in range(205)]
    upstream_status, invalid = 200, False
    calls = []

    def upstream(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/oidc/token":
            assert request.method == "POST" and request.headers["authorization"].startswith("Basic ")
            assert b"grant_type=client_credentials" in request.content
            return Response(200, json={"access_token": "test-token"})
        assert request.method == "GET" and request.url.path == "/api/pms/projects"
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.headers["x-auth-dev-fallback"] == "0"
        return Response(upstream_status, json={"code": 0, "data": {} if invalid else rows})

    transport = MockTransport(upstream)
    settings.TEGENE_API_BASE_URL = ""
    settings.TEGENE_ACCESS_TOKEN = ""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tokens = {}
        for i, who in enumerate(["admin", "manager", "staff"], 1):
            res = await client.post("/api/v1/auth/login", json={"phone": f"1990000000{i}", "password": "Demo-AfterSales-2026!"})
            assert res.status_code == 200
            tokens[who] = res.json()["access_token"]

        async def req(method, path, who="admin", status=200, **kwargs):
            response = await client.request(method, "/api/v1/field" + path,
                headers={"Authorization": "Bearer " + tokens[who]} if who else {}, **kwargs)
            assert response.status_code == status, (method, path, response.status_code, response.text[:500])
            return response.json()

        async def rule_req(method, path="", who="admin", status=200, **kwargs):
            response = await client.request(method, "/api/v1/attendance/rules" + path,
                headers={"Authorization": "Bearer " + tokens[who]}, **kwargs)
            assert response.status_code == status, (method, path, response.status_code, response.text[:500])
            return response.json()

        original = await req("GET", "/directory")
        people = {p["employee_no"]: p["id"] for p in original["people"]}
        manager = next(p["manager_id"] for p in original["projects"])
        staff = original["assignments"][0]["employee_id"]
        admin = (await client.get("/api/v1/auth/me", headers={"Authorization":"Bearer "+tokens["admin"]})).json()["id"]
        assert (await req("GET", "/tegene/projects")) == {"configured": False, "projects": []}
        await req("GET", "/tegene/projects", who=None, status=401)
        await req("GET", "/tegene/projects", who="staff", status=403)
        await req("GET", "/tegene/projects", who="manager", status=403)
        passed.append("unconfigured state and local access boundaries")

        settings.TEGENE_API_BASE_URL = "https://tegene.test/api"
        settings.TEGENE_ACCESS_TOKEN = "test-token"
        with patch("app.services.tegene_projects.httpx.AsyncClient", side_effect=lambda **kw: AsyncClient(transport=transport, **kw)):
            listing = await req("GET", "/tegene/projects")
            assert len(listing["projects"]) == 205
            assert "contractAmount" not in listing["projects"][0]
            assert all(not p["local_project_id"] for p in listing["projects"])
            passed.append("all 205 projects returned; only required fields exposed")

            # Project-first flow: source project -> owner/rule -> people -> matched punch rule.
            configure_path = "/tegene/projects/" + rows[2]["id"] + "/configure"
            await req("POST", configure_path, status=422, json={"manager_id": manager, "rule_id": 999999})
            assert len((await req("GET", "/directory"))["projects"]) == len(original["projects"])
            configured = await req("POST", configure_path, json={"manager_id": manager})
            pid = configured["id"]
            future = today() + timedelta(days=40)
            members_data = {"employee_ids": [staff], "start_date": str(future), "end_date": str(future)}
            await req("POST", f"/projects/{pid}/members", who="manager", status=422, json=members_data)
            rule_id = original["rules"][0]["id"]
            await req("PUT", f"/projects/{pid}/configuration", who="staff", status=403, json={"rule_id": rule_id})
            await req("PUT", f"/projects/{pid}/configuration", who="manager", status=403, json={"manager_id": admin})
            await req("PUT", f"/projects/{pid}/configuration", who="manager", json={"rule_id": rule_id})
            count = len((await req("GET", "/directory"))["assignments"])
            await req("POST", f"/projects/{pid}/members", who="manager", status=403,
                      json={**members_data, "employee_ids": [staff, admin]})
            assert len((await req("GET", "/directory"))["assignments"]) == count
            await req("POST", f"/projects/{pid}/members", json={**members_data, "employee_ids": [staff, manager]})
            await req("POST", f"/projects/{pid}/members", status=409, json=members_data)
            await req("POST", f"/projects/{pid}/members", status=422, json={**members_data,"employee_ids":[staff,staff]})
            async def runtime(day):
                response = await client.get("/api/v1/attendance/my/runtime", params={"target_date": str(day)},
                                            headers={"Authorization": "Bearer " + tokens["staff"]})
                assert response.status_code == 200, response.text
                return response.json()
            matched = await runtime(future)
            assert matched["project"]["id"] == pid and matched["rule"]["id"] == rule_id
            passed.append("one project configuration; owner-only changes, atomic bulk members, overlap rejection and automatic punch context")

            payload = {"name": "项目拍照规则", "work_hour_type": "standard", "clock_in_time": "10:00",
                       "clock_out_time": "19:00", "require_photo": True, "effective_date": str(today()),
                       "extra_config": {"scope_mode": "project"}}
            await rule_req("POST", who="staff", status=403, json=payload)
            await rule_req("POST", who="manager", status=403, json=payload)
            new_rule_id = (await rule_req("POST", status=201, json=payload))["id"]
            other_rule_id = (await rule_req("POST", status=201, json={**payload,"name":"其他项目规则"}))["id"]
            assert new_rule_id != other_rule_id
            assert (await runtime(future + timedelta(days=1)))["has_rule"] is False
            copied = await rule_req("POST", f"/{rule_id}/copy", json={"name":"独立项目副本"})
            assert copied["extra_config"]["scope_mode"] == "project"
            await rule_req("PATCH", f"/{new_rule_id}/toggle", json={"is_active": False})
            await req("PUT", f"/projects/{pid}/configuration", who="manager", status=422, json={"rule_id":new_rule_id})
            await rule_req("PATCH", f"/{new_rule_id}/toggle", json={"is_active": True})
            passed.append("central rule creation/copy, multiple project templates, disabled selection and no unrelated employee fallback")
            manager_dir = await req("GET", "/directory", who="manager")
            assert new_rule_id in {r["id"] for r in manager_dir["rules"]}
            await req("PUT", f"/projects/{pid}/configuration", who="manager", json={"rule_id": new_rule_id})
            matched = await runtime(future)
            assert matched["rule"]["id"] == new_rule_id and matched["rule"]["require_photo"]
            assert matched["expected"]["clock_in"] == "10:00"
            await rule_req("PUT", f"/{new_rule_id}", who="manager", status=403, json={"clock_in_time":"11:00"})
            await rule_req("PUT", f"/{new_rule_id}", json={"clock_in_time":"11:00", "clock_out_time":"20:00"})
            matched = await runtime(future)
            assert matched["expected"]["clock_in"] == "11:00" and matched["expected"]["clock_out"] == "20:00"
            assert next(r for r in (await req("GET", "/directory"))["rules"] if r["id"] == new_rule_id)["clock_in_time"] == "11:00:00"
            passed.append("editing the central rule refreshes the same project rule and future punch schedule")
            original_pid = original["projects"][0]["id"]
            old_runtime = await runtime(today())
            await req("PUT", f"/projects/{original_pid}/configuration", who="manager", json={"rule_id": new_rule_id})
            assert (await runtime(today()))["rule"]["id"] == old_runtime["rule"]["id"]
            async with async_session() as db:
                db.add(AttendanceRecord(employee_id=staff,date=future))
                await db.commit()
            await req("PUT", f"/projects/{pid}/configuration", who="manager", status=409, json={"rule_id":rule_id})
            await rule_req("PUT", f"/{new_rule_id}", status=409, json={"clock_in_time":"12:00"})
            assert (await rule_req("GET", f"/{new_rule_id}"))["clock_in_time"] == "11:00:00"
            assert (await runtime(future))["rule"]["id"] == new_rule_id
            passed.append("rule change updates future shifts, keeps today's rule, and refuses to rewrite recorded attendance")

            path = "/tegene/projects/" + rows[0]["id"] + "/link"
            customer_count = len((await req("GET", "/directory"))["customers"])
            await req("POST", path, who="staff", status=403, json={"manager_id": manager})
            await req("POST", path, status=422, json={"manager_id": staff})
            unchanged = await req("GET", "/directory")
            assert len(unchanged["customers"]) == customer_count
            await req("POST", "/tegene/projects/not-a-uuid/link", status=422, json={"manager_id": manager})
            linked = await req("POST", path, json={"manager_id": manager})
            assert (await req("POST", path, json={"manager_id": admin}))["project_id"] == linked["project_id"]
            directory = await req("GET", "/directory")
            local = next(p for p in directory["projects"] if p["id"] == linked["project_id"])
            assert local["source"] == "tegene" and local["manager_id"] == manager
            assert len(directory["projects"]) == len(original["projects"]) + 2
            passed.append("idempotent UUID binding, manager validation and rollback")

            site = await req("POST", "/sites", json={"project_id": local["id"], "name": "API测试现场",
                "address": rows[0]["siteAddress"], "rule_id": original["rules"][0]["id"]})
            day = str(today() + timedelta(days=20))
            assignment_data = {"site_id": site["id"], "employee_id": staff, "start_date": day, "end_date": day}
            assignment = await req("POST", "/assignments", who="manager", json=assignment_data)
            await req("POST", "/assignments", who="manager", status=409, json=assignment_data)
            passed.append("imported project uses existing site, manager and overlap-safe scheduling")

            await req("PUT", f"/projects/{local['id']}", status=409, json={**local, "name": "手工篡改"})
            await req("PUT", f"/projects/{local['id']}", json={**local, "is_active": False})
            rows[0]["name"], rows[0]["projectNo"] = "源系统改名", "TG-RENAMED"
            await req("POST", path, json={"manager_id": admin})
            directory = await req("GET", "/directory")
            refreshed = next(p for p in directory["projects"] if p["id"] == local["id"])
            assert refreshed["name"] == "源系统改名" and refreshed["code"] == "TG-RENAMED"
            assert refreshed["manager_id"] == manager and refreshed["is_active"] is False
            assert any(a["id"] == assignment["id"] and a["site_id"] == site["id"] for a in directory["assignments"])
            passed.append("source refresh preserves identity, local owner, disabled state and existing shifts")

            rows[1]["projectNo"] = original["projects"][0]["code"]
            await req("POST", "/tegene/projects/" + rows[1]["id"] + "/link", status=409, json={"manager_id": manager})
            await req("POST", "/tegene/projects/" + str(UUID(int=9999)) + "/link", status=404, json={"manager_id": manager})
            upstream_status = 401
            await req("GET", "/tegene/projects", status=502)
            await req("GET", "/directory")
            upstream_status, invalid = 200, True
            await req("GET", "/tegene/projects", status=502)
            invalid = False
            rows.append(rows[0])
            await req("GET", "/tegene/projects", status=502)
            rows.pop()
            passed.append("collision and inaccessible-project rejection; upstream errors do not log out local users")

            settings.TEGENE_ACCESS_TOKEN = ""
            settings.TEGENE_TOKEN_URL = "https://identity.test/oidc/token"
            settings.TEGENE_CLIENT_ID, settings.TEGENE_CLIENT_SECRET = "test-client", "test-secret"
            settings.TEGENE_RESOURCE = "https://tegene.test/api"
            assert len((await req("GET", "/tegene/projects"))["projects"]) == 205
            assert ("POST", "/oidc/token") in calls
            settings.TEGENE_API_BASE_URL = "http://insecure.example/api"
            before = len(calls)
            await req("GET", "/tegene/projects", status=503)
            assert len(calls) == before
            passed.append("M2M exchange and HTTPS configuration validation")

    await engine.dispose()
    report = {"passed": passed, "count": len(passed), "upstream": "MockTransport, no production credentials used",
              "source_contract": "tg-AI-codex/tegene-platform@7ec6e0bd1785d4a7f754fd7e4c0b02f5700ca5cf"}
    evidence = Path(__file__).resolve().parents[1] / "evidence/tegene-projects-integration.json"
    evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
