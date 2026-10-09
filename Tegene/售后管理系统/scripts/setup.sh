#!/bin/sh
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TASK_ROOT"
if command -v uv >/dev/null 2>&1; then
  [ -d backend/.venv ] || uv venv --python 3.11 backend/.venv
  uv pip install --python backend/.venv/bin/python -r backend/requirements.txt
else
  [ -d backend/.venv ] || python3 -m venv backend/.venv
  backend/.venv/bin/pip install -r backend/requirements.txt
fi
npm ci --prefix frontend
npm ci --prefix mobile
PYTHONPATH=backend backend/.venv/bin/python -m app.bootstrap "$@"
printf '%s\n' '安装完成。运行 python3 scripts/dev.py start 启动本地系统。'
