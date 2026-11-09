#!/bin/sh
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TASK_ROOT"
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/upstream backend/tests/test_field_rules.py backend/tests/test_project_attendance_rule_permissions.py backend/tests/test_pending_project_roster.py backend/tests/test_rotation_weekly_publish.py backend/tests/test_rotation_inflight_rule_change.py backend/tests/test_field_selector_categories.py backend/tests/test_local_short_login.py backend/tests/test_manager_project_configuration.py backend/tests/test_unassigned_local_projects.py -q
backend/.venv/bin/python scripts/test_integration.py
backend/.venv/bin/python scripts/test_auth_roster.py
backend/.venv/bin/python scripts/test_map_settings.py
backend/.venv/bin/python scripts/test_legal_holidays.py
backend/.venv/bin/python scripts/test_rotation.py
backend/.venv/bin/python scripts/test_field_roles.py
backend/.venv/bin/python scripts/test_field_approval_roles.py
backend/.venv/bin/python scripts/test_tegene_projects.py
backend/.venv/bin/python scripts/test_project_membership.py
backend/.venv/bin/python scripts/test_field_dashboard.py
node scripts/test_audit_display.mjs
npm run typecheck --prefix frontend
npm run build --prefix frontend
npm run build --prefix mobile
