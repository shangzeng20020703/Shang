"""Review and import the four project columns from a location workbook.

An imported row is a catalog entry, not an attendance rule or a fabricated
customer/leader assignment. Project numbers connect it to existing local and
read-only Tegene projects.
"""

import hashlib
import io
import json
import math
import zipfile
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from openpyxl import load_workbook
from sqlalchemy import select

from app.models.attendance import AttendanceRule
from app.models.audit_log import AuditLog
from app.models.field_service import FieldProject, FieldProjectCatalog, FieldSite
from app.schemas.field_service import ProjectInput
from app.services.field_roles import role_names

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
MAX_ROWS = 5000
HEADERS = ("项目编号", "项目名称", "地理位置", "是否需要定位")
FIELDS = ("name", "address", "latitude", "longitude", "require_gps")


async def require_importer(db, user):
    if not await role_names(db, user) & {"admin", "aftersales_manager"}:
        raise HTTPException(403, "仅管理员和售后经理可导入项目表")


def _text(value):
    return "" if value is None else str(value).strip()


def _coordinates(url):
    """Tencent sheet hyperlinks are parsed offline; no remote URL is opened."""
    if not url:
        return None
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != "docs.qq.com"
            or parts.path != "/scenario/map.html" or parts.username or parts.password):
        return None
    query = parse_qs(parts.query)
    try:
        latitude = float(query["latitude"][0])
        longitude = float(query["longitude"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def parse_workbook(content, filename):
    """Return rows and row errors without executing formulas or following links."""
    if not filename or not filename.lower().endswith(".xlsx"):
        raise HTTPException(422, "请上传 .xlsx 项目位置模板")
    if not content or len(content) > MAX_FILE_BYTES:
        raise HTTPException(422, "项目表为空或超过 5 MB")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise HTTPException(422, "项目表解压后超过 25 MB")
        workbook = load_workbook(io.BytesIO(content), read_only=False, data_only=True)
    except HTTPException:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, KeyError):
        raise HTTPException(422, "项目表不是有效的 .xlsx 文件") from None
    try:
        selected = None
        for sheet in workbook.worksheets:
            for header_row in range(1, min(sheet.max_row, 5) + 1):
                labels = {_text(cell.value): cell.column for cell in sheet[header_row]}
                if all(label in labels for label in HEADERS):
                    selected = (sheet, header_row, labels)
                    break
            if selected:
                break
        if not selected:
            raise HTTPException(422, "找不到项目编号、项目名称、地理位置、是否需要定位四列表头")
        sheet, header_row, labels = selected
        if sheet.max_row - header_row > MAX_ROWS:
            raise HTTPException(422, "一次最多导入 5000 个项目")
        rows = []
        first_by_code = {}
        for row_number in range(header_row + 1, sheet.max_row + 1):
            cells = {label: sheet.cell(row_number, labels[label]) for label in HEADERS}
            values = {label: _text(cell.value) for label, cell in cells.items()}
            if not any(values.values()):
                continue
            raw_code = cells["项目编号"].value
            code = values["项目编号"].upper()
            name = values["项目名称"]
            address = values["地理位置"]
            flag = values["是否需要定位"]
            errors = []
            if not isinstance(raw_code, str) or not code or len(code) > 50:
                errors.append("项目编号须为不超过 50 字的文本")
            if not name or len(name) > 150:
                errors.append("项目名称须为 1–150 字")
            if len(address) > 500:
                errors.append("地理位置不能超过 500 字")
            if flag not in {"是", "否"}:
                errors.append("是否需要定位只能填写“是”或“否”")
            location_cell = cells["地理位置"]
            link = location_cell.hyperlink.target if location_cell.hyperlink else None
            coords = _coordinates(link) if address else None
            warning = ""
            if flag == "是" and not coords:
                warning = "缺少有效坐标；建立定位规则前须手动设置打卡位置"
            elif address and link and not coords:
                warning = "地图链接无效，未导入坐标"
            row = {
                "row": row_number, "code": code, "name": name, "address": address,
                "latitude": coords[0] if coords else None,
                "longitude": coords[1] if coords else None,
                "require_gps": flag == "是", "has_location": bool(coords),
                "warning": warning, "error": "；".join(errors),
            }
            if code:
                previous = first_by_code.get(code)
                if previous is not None:
                    rows[previous]["error"] = "；".join(filter(None, [rows[previous]["error"], "项目编号重复"]))
                    row["error"] = "；".join(filter(None, [row["error"], "项目编号重复"]))
                else:
                    first_by_code[code] = len(rows)
            rows.append(row)
        if not rows:
            raise HTTPException(422, "项目表没有可导入的数据行")
        return rows
    finally:
        workbook.close()


def _snapshot(row):
    return {key: getattr(row, key) for key in ("code", *FIELDS)} if row else None


def _rule_matches_catalog(rule, row, site=None):
    from app.services.attendance import AttendanceRecordService
    config = rule.extra_config or {}
    effective_gps = bool(rule.require_gps) or "GPS" in AttendanceRecordService._parse_text_set(
        config.get("check_methods"))
    if effective_gps != bool(row["require_gps"]):
        return False
    if not row["require_gps"]:
        return True
    if not row["has_location"]:
        return False
    for location in config.get("locations") or []:
        if not isinstance(location, dict):
            continue
        if location.get("source") == "project_catalog":
            try:
                return (float(location.get("latitude")) == row["latitude"]
                        and float(location.get("longitude")) == row["longitude"]
                        and (location.get("name") or "") == row["address"])
            except (TypeError, ValueError):
                return False
    try:
        return (float(rule.gps_latitude) == row["latitude"]
                and float(rule.gps_longitude) == row["longitude"]
                and bool(site) and site.address == row["address"])
    except (TypeError, ValueError):
        return False


async def _state(db, codes=None):
    condition = FieldProjectCatalog.code.in_(codes) if codes is not None else True
    catalog = {row.code: row for row in (await db.scalars(select(FieldProjectCatalog).where(condition))).all()}
    project_condition = FieldProject.code.in_(codes) if codes is not None else True
    projects = {row.code: row for row in (await db.scalars(select(FieldProject).where(project_condition))).all()}
    project_ids = [p.id for p in projects.values()]
    sites = {s.project_id: s for s in (await db.scalars(select(FieldSite).where(
        FieldSite.project_id.in_(project_ids), FieldSite.is_active.is_(True)))).all()}
    rules = {r.id: r for r in (await db.scalars(select(AttendanceRule).where(
        AttendanceRule.id.in_([s.rule_id for s in sites.values()])))).all()}
    return catalog, projects, sites, rules


def _item(row, catalog, projects, sites, rules):
    current = catalog.get(row["code"])
    project = projects.get(row["code"])
    site = sites.get(project.id) if project else None
    rule = rules.get(site.rule_id) if site else None
    old = _snapshot(current) or ({"name": project.name} if project else None)
    changes = []
    for field in FIELDS:
        before = old.get(field) if old and field in old else None
        after = row[field]
        if before != after:
            changes.append({"field": field, "before": before, "after": after})
    # Local identity can have drifted since the last Tegene refresh.
    if project and project.name != row["name"] and not any(c["field"] == "name" for c in changes):
        changes.append({"field": "name", "before": project.name, "after": row["name"]})
    active_rule_change = bool(rule and not _rule_matches_catalog(rule, row, site))
    return {
        **row,
        "status": "error" if row["error"] else ("create" if not current else "update" if changes else "unchanged"),
        "changes": changes,
        "local_project_id": project.id if project else None,
        "active_rule_id": rule.id if rule else None,
        "active_rule_change": active_rule_change,
    }


def _token(rows, items, catalog, projects, sites, rules, content):
    state = {
        "file": hashlib.sha256(content).hexdigest(),
        "rows": rows,
        "catalog": [_snapshot(catalog[c]) for c in sorted(catalog)],
        "projects": [{"id": projects[c].id, "code": c, "name": projects[c].name}
                     for c in sorted(projects)],
        "rules": [{"project_id": pid, "rule_id": site.rule_id,
                   "config": rules[site.rule_id].extra_config,
                   "require_gps": rules[site.rule_id].require_gps,
                   "latitude": str(rules[site.rule_id].gps_latitude),
                   "longitude": str(rules[site.rule_id].gps_longitude)}
                  for pid, site in sorted(sites.items()) if site.rule_id in rules],
    }
    return hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


async def preview_import(db, user, content, filename):
    await require_importer(db, user)
    rows = parse_workbook(content, filename)
    catalog, projects, sites, rules = await _state(db, [r["code"] for r in rows if r["code"]])
    items = [_item(row, catalog, projects, sites, rules) for row in rows]
    summary = {key: sum(item["status"] == key for item in items)
               for key in ("create", "update", "unchanged", "error")}
    summary.update({
        "total": len(items),
        "requires_gps": sum(item["require_gps"] for item in items),
        "missing_location": sum(item["require_gps"] and not item["has_location"] for item in items),
        "all_missing_location": sum(not item["has_location"] for item in items),
        "active_rule_changes": sum(item["active_rule_change"] for item in items),
    })
    return {"preview_token": _token(rows, items, catalog, projects, sites, rules, content),
            "rows": items, "summary": summary}


def catalog_dict(row):
    return {
        "code": row.code, "name": row.name, "address": row.address,
        "latitude": row.latitude, "longitude": row.longitude,
        "require_gps": bool(row.require_gps), "radius_m": 300,
    }


async def catalog_for_project(db, project_id):
    project = await db.get(FieldProject, project_id)
    row = await db.get(FieldProjectCatalog, project.code) if project else None
    return catalog_dict(row) if row else None


async def list_catalog(db, user):
    from app.services.field_service import project_ids
    from app.services.attendance import AttendanceRecordService
    names = await role_names(db, user)
    allowed = await project_ids(db, user)
    rows = (await db.scalars(select(FieldProjectCatalog).order_by(FieldProjectCatalog.code))).all()
    catalog, projects, sites, rules = await _state(db, [row.code for row in rows])
    items = []
    for row in rows:
        project = projects.get(row.code)
        if allowed is not None and (not project or project.id not in allowed):
            continue
        site = sites.get(project.id) if project else None
        rule = rules.get(site.rule_id) if site else None
        data = catalog_dict(row)
        rule_extra = AttendanceRecordService._extra(rule)
        active_rule_requires_gps = bool(rule and (
            rule.require_gps or "GPS" in AttendanceRecordService._parse_text_set(
                rule_extra.get("check_methods"))))
        active_rule_has_valid_geofence = bool(rule and
            AttendanceRecordService._configured_locations(rule, rule_extra))
        items.append({
            **data, "local_project_id": project.id if project else None,
            "status": "staged" if not project else "configured" if rule else "pending_rule",
            "has_location": row.latitude is not None and row.longitude is not None,
            "active_rule_id": rule.id if rule else None,
            "active_rule_requires_gps": active_rule_requires_gps,
            "active_rule_has_valid_geofence": active_rule_has_valid_geofence,
            "rule_updates_pending": bool(rule and not _rule_matches_catalog(rule, {
                **data, "has_location": row.latitude is not None and row.longitude is not None,
            }, site)),
            "imported_at": row.imported_at,
        })
    return {"items": items, "total": len(items)}


async def commit_import(db, user, content, filename, preview_token, confirm_active_rule_project_ids):
    preview = await preview_import(db, user, content, filename)
    if preview["preview_token"] != preview_token:
        raise HTTPException(409, "项目表或项目资料在预览后发生变化，请重新预览")
    if preview["summary"]["error"]:
        raise HTTPException(422, "项目表存在错误行，请修正后重新预览")
    items = preview["rows"]
    approved = set(confirm_active_rule_project_ids)
    eligible = {item["local_project_id"] for item in items
                if item["active_rule_change"] and item["active_rule_id"]}
    if approved - eligible:
        raise HTTPException(422, "确认更新的规则项目不在本次差异中")
    for item in items:
        if item["local_project_id"] in approved and item["require_gps"] and not item["has_location"]:
            raise HTTPException(422, f"{item['code']} 缺少有效坐标，不能更新其定位规则")
    existing, projects, sites, _ = await _state(db, [item["code"] for item in items])
    now = datetime.now(timezone.utc)
    for item in items:
        row = existing.get(item["code"]) or FieldProjectCatalog(code=item["code"])
        for key in FIELDS:
            setattr(row, key, item[key])
        row.imported_at = now
        row.imported_by = user.id
        db.add(row)
        project = projects.get(item["code"])
        if project and project.name != item["name"]:
            old_name = project.name
            project.name = item["name"]
            site = sites.get(project.id)
            if site and site.name == old_name:
                site.name = item["name"]
    await db.flush()
    if approved:
        from app.services.attendance import apply_catalog_location_to_project_rule
        for item in items:
            if item["local_project_id"] in approved:
                catalog = await db.get(FieldProjectCatalog, item["code"])
                await apply_catalog_location_to_project_rule(
                    db, item["local_project_id"], catalog_dict(catalog), user)
    db.add(AuditLog(
        operator_id=user.id, operator_name=user.name, module="field",
        action="project_catalog_import", resource_type="field_project_catalog",
        after_data=json.dumps({"file_sha256": hashlib.sha256(content).hexdigest(),
                               "rows": len(items), "approved_rule_project_ids": sorted(approved)},
                              ensure_ascii=False),
    ))
    await db.flush()
    return {
        "created": preview["summary"]["create"],
        "updated": preview["summary"]["update"],
        "unchanged": preview["summary"]["unchanged"],
        "active_rules_updated": len(approved),
        "items": (await list_catalog(db, user))["items"],
    }


async def promote_catalog_project(db, user, code, customer_id, manager_id):
    await require_importer(db, user)
    code = code.strip().upper()
    catalog = await db.get(FieldProjectCatalog, code)
    if not catalog:
        raise HTTPException(404, "项目表中没有该编号")
    if await db.scalar(select(FieldProject.id).where(FieldProject.code == code)):
        raise HTTPException(409, "项目已进入售后项目库，请刷新页面")
    from app.services.field_service import save_project
    project = await save_project(db, user, ProjectInput(
        code=code, name=catalog.name, customer_id=customer_id,
        manager_id=manager_id,
    ))
    return {"project_id": project["id"], "status": "pending_rule"}
