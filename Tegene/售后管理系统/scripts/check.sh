#!/bin/sh
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TASK_ROOT"
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/upstream backend/tests/test_field_rules.py -q
backend/.venv/bin/python scripts/test_integration.py
backend/.venv/bin/python scripts/test_auth_roster.py
backend/.venv/bin/python scripts/test_map_settings.py
backend/.venv/bin/python scripts/test_tegene_projects.py
backend/.venv/bin/python scripts/test_project_membership.py
backend/.venv/bin/python scripts/test_field_dashboard.py
node scripts/test_audit_display.mjs
npm run typecheck --prefix frontend
npm run build --prefix frontend
npm run build --prefix mobile
