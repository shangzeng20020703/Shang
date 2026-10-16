"""Read Tegene's existing PMS API; keep local responsibility and scheduling here."""
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.field_service import FieldCustomer, FieldProject, FieldTegeneProject
from app.schemas.field_service import ProjectInput
from app.services import field_service


class TegeneProject(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    id: UUID
    projectNo: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=150)
    customer: str = Field(max_length=120)
    status: str = Field(max_length=40)
    operatingStatus: str | None = Field(default=None, max_length=40)
    managerName: str | None = Field(default=None, max_length=150)
    siteAddress: str | None = Field(default=None, max_length=500)
    planStart: str | None = Field(default=None, max_length=32)
    planEnd: str | None = Field(default=None, max_length=32)


def configured():
    return bool(settings.TEGENE_API_BASE_URL and (
        settings.TEGENE_ACCESS_TOKEN or (settings.TEGENE_TOKEN_URL and
        settings.TEGENE_CLIENT_ID and settings.TEGENE_CLIENT_SECRET and settings.TEGENE_RESOURCE)
    ))


def checked_url(value):
    parts = urlsplit(value)
    local_http = (settings.ENVIRONMENT == "development" and parts.scheme == "http"
                  and parts.hostname in {"localhost", "127.0.0.1", "::1"})
    if (not parts.hostname or not (parts.scheme == "https" or local_http)
            or parts.username or parts.password or parts.query or parts.fragment or len(value) > 255):
        raise HTTPException(503, "Tegene 接口地址配置无效：正式接口必须使用 HTTPS")
    return value.rstrip("/")


def response_json(response):
    if response.status_code in {401, 403}:
        # Do not return 401 here: it would log the user out of this independent system.
        raise HTTPException(502, "Tegene 授权失败，请检查接口凭据、项目读取权限和数据范围")
    if response.status_code != 200:
        raise HTTPException(502, f"Tegene 接口暂不可用（HTTP {response.status_code}）")
    try:
        return response.json()
    except ValueError:
        raise HTTPException(502, "Tegene 返回的内容不是有效 JSON") from None


async def fetch_projects():
    if not configured():
        raise HTTPException(503, "尚未配置 Tegene 项目读取凭据，请联系管理员完成接入")
    source = checked_url(settings.TEGENE_API_BASE_URL)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            token = settings.TEGENE_ACCESS_TOKEN
            if not token:
                body = {"grant_type": "client_credentials", "resource": settings.TEGENE_RESOURCE}
                if settings.TEGENE_SCOPE:
                    body["scope"] = settings.TEGENE_SCOPE
                result = response_json(await client.post(
                    checked_url(settings.TEGENE_TOKEN_URL), data=body,
                    auth=(settings.TEGENE_CLIENT_ID, settings.TEGENE_CLIENT_SECRET),
                ))
                token = result.get("access_token") if isinstance(result, dict) else None
                if not isinstance(token, str) or not token:
                    raise HTTPException(502, "Tegene 身份服务未返回有效访问凭据")
            # The identity lookup endpoint is capped at 200. /projects returns the full
            # authorized list; it never requests or bypasses a wider upstream data scope.
            payload = response_json(await client.get(source + "/pms/projects", headers={
                "Authorization": "Bearer " + token, "X-Auth-Dev-Fallback": "0",
            }))
    except httpx.HTTPError:
        raise HTTPException(502, "无法连接 Tegene 项目接口，请稍后重试") from None
    if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(payload.get("data"), list):
        raise HTTPException(502, "Tegene 项目接口响应格式不匹配，未读取或覆盖项目")
    try:
        projects = [TegeneProject.model_validate(row).model_dump(mode="json") for row in payload["data"]]
    except ValidationError:
        raise HTTPException(502, "Tegene 项目字段校验失败，未读取或覆盖项目") from None
    if (len({p["id"] for p in projects}) != len(projects)
            or len({p["projectNo"] for p in projects}) != len(projects)):
        raise HTTPException(502, "Tegene 返回了重复的项目身份，请先核对来源数据")
    return source, projects


async def list_projects(db, user):
    await field_service.require_admin(db, user)
    if not configured():
        return {"configured": False, "projects": []}
    source, projects = await fetch_projects()
    bindings = {r.external_id: r for r in (await db.scalars(
        select(FieldTegeneProject).where(FieldTegeneProject.source_url == source)
    )).all()}
    return {"configured": True, "fetched_at": datetime.now(timezone.utc), "projects": [
        {**p, "local_project_id": bindings[p["id"]].project_id if p["id"] in bindings else None}
        for p in projects
    ]}


async def link_project(db, user, external_id, data):
    await field_service.require_admin(db, user)
    source, projects = await fetch_projects()
    project = next((p for p in projects if p["id"] == str(external_id)), None)
    if project is None:
        raise HTTPException(404, "Tegene 项目不存在或当前凭据无权读取")
    binding = await db.scalar(select(FieldTegeneProject).where(
        FieldTegeneProject.source_url == source, FieldTegeneProject.external_id == str(external_id),
    ))
    existing = await db.scalar(select(FieldProject).where(FieldProject.code == project["projectNo"]))
    if existing and (not binding or existing.id != binding.project_id):
        raise HTTPException(409, "本地已有同编号项目，请先核对档案；不会自动覆盖或合并")
    customer_name = project["customer"] or "Tegene 未填写客户"
    customer = await db.scalar(select(FieldCustomer).where(FieldCustomer.name == customer_name))
    if not customer:
        customer = FieldCustomer(name=customer_name)
        db.add(customer)
        await db.flush()
    if binding:
        local = await db.get(FieldProject, binding.project_id)
        # Refresh source-owned identity only. Local manager, switches, sites and shifts survive.
        local.code, local.name, local.customer_id = project["projectNo"], project["name"], customer.id
    else:
        saved = await field_service.save_project(db, user, ProjectInput(
            code=project["projectNo"], name=project["name"], customer_id=customer.id,
            manager_id=data.manager_id,
        ))
        binding = FieldTegeneProject(source_url=source, external_id=str(external_id), project_id=saved["id"])
        db.add(binding)
    binding.synced_at = datetime.now(timezone.utc)
    db.add(AuditLog(operator_id=user.id, operator_name=user.name, module="field", action="tegene_project_sync",
                    resource_type="field_project", resource_id=binding.project_id,
                    after_data=json.dumps({"source": source, "external_id": str(external_id),
                                           "code": project["projectNo"], "name": project["name"]}, ensure_ascii=False)))
    await db.flush()
    return {"project_id": binding.project_id, "synced_at": binding.synced_at}
