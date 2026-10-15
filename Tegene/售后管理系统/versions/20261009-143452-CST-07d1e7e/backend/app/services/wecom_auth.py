"""WeCom identity authentication; roster admission and roles remain local decisions."""

import asyncio
import hashlib
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select, update

from app.core.config import settings
from app.core.security import create_access_token
from app.models.employee import Employee
from app.models.field_service import FieldLoginState, FieldWecomIdentity

ACTIVE_STATUSES = {"在职", "试用"}
_token_cache = ("", 0.0, "")
_token_lock = asyncio.Lock()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def policy():
    return {"mode": settings.LOGIN_MODE,
            "password_enabled": settings.LOGIN_MODE != "wecom_only",
            "wecom_enabled": settings.LOGIN_MODE in {"hybrid", "wecom_only"},
            "configuration_ready": settings.wecom_ready}


def require_config():
    if not policy()["wecom_enabled"] or not settings.wecom_ready:
        raise HTTPException(503, "企业微信登录尚未配置完成，请联系管理员")


async def begin(db, client, environment, challenge):
    require_config()
    now = datetime.utcnow()
    await db.execute(delete(FieldLoginState).where(FieldLoginState.expires_at < now))
    state = secrets.token_urlsafe(32)
    db.add(FieldLoginState(digest=digest(state), challenge=challenge, client=client,
                           expires_at=now + timedelta(minutes=5)))
    redirect = (settings.WECHAT_WEB_REDIRECT_URI if client == "web"
                else settings.WECHAT_MOBILE_REDIRECT_URI)
    params = dict(appid=settings.WECHAT_CORP_ID, agentid=settings.WECHAT_AGENT_ID,
                  redirect_uri=redirect, state=state)
    if environment == "in_app":
        params.update(response_type="code", scope="snsapi_base")
        url = "https://open.weixin.qq.com/connect/oauth2/authorize?" + urlencode(params) + "#wechat_redirect"
    else:
        params["login_type"] = "CorpApp"
        url = "https://login.work.weixin.qq.com/wwlogin/sso/login?" + urlencode(params)
    await db.flush()
    return {"authorize_url": url, "state": state}


async def provider_get(path, params):
    # Fixed official host, no redirects; never include secrets/provider URLs in errors.
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get("https://qyapi.weixin.qq.com/cgi-bin/" + path, params=params)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "企业微信暂时无法连接，请重新登录") from None
    if not isinstance(data, dict) or data.get("errcode", 0) != 0:
        raise HTTPException(403, "企微授权或成员权限校验失败，请检查应用可见范围、可信域名及接口权限")
    return data


async def access_token():
    global _token_cache
    cache_key = digest(str(settings.WECHAT_CORP_ID) + str(settings.WECHAT_SECRET))
    async with _token_lock:
        value, expires, key = _token_cache
        if key == cache_key and expires > time.monotonic():
            return value
        data = await provider_get("gettoken", {"corpid": settings.WECHAT_CORP_ID,
                                               "corpsecret": settings.WECHAT_SECRET})
        value = data.get("access_token")
        if not isinstance(value, str) or not value:
            raise HTTPException(502, "企业微信应用凭证不可用")
        _token_cache = (value, time.monotonic() + max(0, int(data.get("expires_in", 0)) - 120), cache_key)
        return value


async def verify_application_scope(token, userid, member):
    """OAuth base scope can return members outside the app; check app visibility explicitly."""
    application = await provider_get("agent/get", {"access_token": token, "agentid": settings.WECHAT_AGENT_ID})
    if str(application.get("agentid")) != settings.WECHAT_AGENT_ID or application.get("close") != 0:
        raise HTTPException(403, "企微应用未启用或应用身份不匹配")
    allowed_users = application.get("allow_userinfos", {}).get("user", [])
    if any(str(p.get("userid", "")).lower() == userid for p in allowed_users):
        return
    allowed_departments = set(application.get("allow_partys", {}).get("partyid", []))
    for tag in application.get("allow_tags", {}).get("tagid", []):
        members = await provider_get("tag/get", {"access_token": token, "tagid": tag})
        if any(str(p.get("userid", "")).lower() == userid for p in members.get("userlist", [])):
            return
        allowed_departments.update(members.get("partylist", []))
    own_departments = {str(d) for d in member.get("department", [])}
    if "1" in {str(d) for d in allowed_departments}:
        return  # Root department explicitly grants this application's whole enterprise.
    if own_departments & {str(d) for d in allowed_departments}:
        return
    if own_departments:
        for department in allowed_departments:
            branch = await provider_get("department/list", {"access_token": token, "id": department})
            if own_departments & {str(d["id"]) for d in branch.get("department", [])}:
                return
    raise HTTPException(403, "你不在售后管理系统企微应用的可见范围内，请联系管理员")


async def complete(db, client, state, verifier, code):
    require_config()
    now = datetime.utcnow()
    claimed = await db.execute(update(FieldLoginState).where(
        FieldLoginState.digest == digest(state), FieldLoginState.challenge == digest(verifier),
        FieldLoginState.client == client, FieldLoginState.used_at.is_(None),
        FieldLoginState.expires_at > now,
    ).values(used_at=now))
    if claimed.rowcount != 1:
        raise HTTPException(400, "登录请求已过期、已使用或不属于当前浏览器，请重新登录")
    # Commit consumption before calling WeCom, so failed authorization cannot replay state.
    await db.commit()
    token = await access_token()
    identity = await provider_get("auth/getuserinfo", {"access_token": token, "code": code})
    userid = identity.get("userid")
    if not isinstance(userid, str) or not userid or "/" in userid:
        raise HTTPException(403, "仅允许本企业的企业微信成员登录")
    userid = userid.lower()
    member = await provider_get("user/get", {"access_token": token, "userid": userid})
    if str(member.get("userid", "")).lower() != userid or member.get("status") != 1:
        raise HTTPException(403, "企业微信成员未激活、已退出或不可访问")
    await verify_application_scope(token, userid, member)
    binding = await db.scalar(select(FieldWecomIdentity).where(
        FieldWecomIdentity.corp_id == settings.WECHAT_CORP_ID,
        FieldWecomIdentity.userid == userid))
    user = await db.get(Employee, binding.employee_id) if binding else None
    if not user or not user.is_active or user.status not in ACTIVE_STATUSES:
        raise HTTPException(403, "未绑定有效在职花名册账号，请联系售后负责人")
    if client == "web":
        from app.core.deps import get_user_role_names
        if not await get_user_role_names(db, user) & {"admin", "hr", "manager"}:
            raise HTTPException(403, "当前账号未开通管理端权限，请使用员工端入口")
    return {"access_token": create_access_token(str(user.id), timedelta(minutes=15), identity={
        "auth_method": "wecom", "corp_id": binding.corp_id, "wecom_userid": binding.userid,
        "binding_id": binding.id}), "token_type": "bearer"}


async def validate_session_identity(db, user, payload):
    method = payload.get("auth_method", "password")
    if settings.LOGIN_MODE == "wecom_only" and method != "wecom":
        raise HTTPException(401, "请使用企业微信重新登录")
    if method == "wecom":
        binding = await db.scalar(select(FieldWecomIdentity).where(
            FieldWecomIdentity.employee_id == user.id,
            FieldWecomIdentity.id == payload.get("binding_id"),
            FieldWecomIdentity.corp_id == payload.get("corp_id"),
            FieldWecomIdentity.userid == payload.get("wecom_userid")))
        if not binding or binding.corp_id != settings.WECHAT_CORP_ID or user.status not in ACTIVE_STATUSES:
            raise HTTPException(401, "企微绑定或花名册状态已变化，请重新登录")


async def bind_member(db, employee_id, userid):
    """Only called after the administrator's target-account permissions were checked."""
    if userid is None:
        return  # Omitted means preserve; explicit empty string unbinds.
    existing = await db.scalar(select(FieldWecomIdentity).where(FieldWecomIdentity.employee_id == employee_id))
    userid = userid.strip().lower()
    if not userid:
        if existing:
            await db.delete(existing)
        return
    if "/" in userid:
        raise HTTPException(400, "只允许绑定本企业成员账号")
    if len(userid.encode("utf-8")) > 64:
        raise HTTPException(400, "企微账号最多 64 字节")
    if not settings.WECHAT_CORP_ID:
        raise HTTPException(400, "请先配置企业 CorpID，再绑定企微成员账号")
    duplicate = await db.scalar(select(FieldWecomIdentity).where(
        FieldWecomIdentity.corp_id == settings.WECHAT_CORP_ID,
        FieldWecomIdentity.userid == userid, FieldWecomIdentity.employee_id != employee_id))
    if duplicate:
        raise HTTPException(409, "此企微成员账号已绑定其他员工，请核对，不能覆盖")
    if existing:
        existing.userid, existing.corp_id = userid, settings.WECHAT_CORP_ID
    else:
        db.add(FieldWecomIdentity(employee_id=employee_id, corp_id=settings.WECHAT_CORP_ID, userid=userid))
    await db.flush()
