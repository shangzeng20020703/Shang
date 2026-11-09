"""Create a source-only handoff archive without local data or credentials."""

import hashlib
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deliverables"
OUT.mkdir(exist_ok=True)
archive = OUT / f"售后管理系统-源码-{datetime.now().strftime('%Y%m%d')}.zip"

source_roots = {"backend", "frontend", "mobile", "deploy", "docs", "scripts"}
source_files = {"AGENTS.md", "README.md", "SOURCE.md", ".gitignore", ".dockerignore"}
excluded_dirs = {
    ".git", ".runtime", ".playwright-cli", ".pytest_cache", ".ruff_cache",
    "__pycache__", "node_modules", ".venv", "dist", "deliverables",
    "test-results", "playwright-report",
}
excluded_suffixes = (".pyc", ".log", ".tsbuildinfo", ".db", ".sqlite", ".sqlite3")


def allowed_file(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if path.is_symlink() or any(part in excluded_dirs for part in relative.parts):
        return False
    if relative.parts[:2] in {("backend", "data"), ("backend", "uploads")}:
        return False
    if path.name.startswith(".env") and path.name != ".env.example":
        return False
    if path.name == ".DS_Store" or path.name.endswith(excluded_suffixes):
        return False
    return True


files: list[Path] = []
for name in sorted(source_roots):
    base = ROOT / name
    if not base.is_dir():
        raise FileNotFoundError(base)
    for current, dirs, names in os.walk(base):
        dirs[:] = [item for item in dirs if item not in excluded_dirs and not (name == "backend" and Path(current) == base and item in {"data", "uploads"})]
        for filename in names:
            path = Path(current) / filename
            if allowed_file(path):
                files.append(path)
for name in sorted(source_files):
    path = ROOT / name
    if not path.is_file():
        raise FileNotFoundError(path)
    files.append(path)
files.append(ROOT / "evidence" / ".gitkeep")

with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
    for path in sorted(files):
        bundle.write(path, Path("售后管理系统") / path.relative_to(ROOT))

with zipfile.ZipFile(archive) as bundle:
    if bundle.testzip() is not None:
        raise RuntimeError("源码包 CRC 校验失败")
    names = bundle.namelist()
    if len(names) != len(set(names)):
        raise RuntimeError("源码包包含重复路径")
    if any(
        part in excluded_dirs or (part.startswith(".env") and part != ".env.example")
        for name in names
        for part in Path(name).parts
    ) or any("/backend/data/" in name or "/backend/uploads/" in name for name in names):
        raise RuntimeError("源码包包含运行数据或凭据路径")

with archive.open("rb") as stream:
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
report = {
    "archive": archive.name,
    "files": len(files),
    "bytes": archive.stat().st_size,
    "sha256": digest,
    "excludes": ["database", "uploads", "runtime", "credentials", "test evidence", "dependencies", "build output"],
    "verified_zip_crc": True,
}
(OUT / "源码包校验.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report, ensure_ascii=False, indent=2))
