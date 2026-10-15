"""Explicit extracted module boundary: missing core modules must fail startup."""
from importlib import import_module
from fastapi import APIRouter
router = APIRouter()
for name, prefix, title in [
    ('auth','/auth','认证'),('organizations','/organizations','组织'),
    ('departments','/departments','部门'),('locations','/locations','地点'),
    ('employees','/employees','人员'),('rbac','/rbac','权限'),
    ('attendance','/attendance','考勤'),('leave','/leave','假期'),
    ('overtime','/overtime','加班'),('approval','/approval','审批'),
    ('notifications','/notifications','通知'),('upload','/upload','文件'),
    ('audit_log','/audit-logs','审计'),('event_monitor','/event-monitor','业务事件'),
    ('field_service','/field','售后现场'),
    ('system_settings','/system-settings','系统设置')]:
    router.include_router(import_module('app.api.v1.'+name).router,prefix=prefix,tags=[title])
# The source designer calls company directory under /payroll; expose only directory routes.
from app.api.v1.payroll import router as payroll_router
for route in payroll_router.routes:
    if route.path == '/companies' or route.path.startswith('/companies/'):
        clone = APIRouter()
        clone.routes.append(route)
        router.include_router(clone, prefix='/payroll',tags=['公司目录'])

# The original mobile home/profile use ESS; expose only their self-service reads
# and profile update. Leave applications keep using the published approval form.
from app.api.v1.ess import router as ess_router
for route in ess_router.routes:
    if route.path in {'/profile', '/leave/balance', '/leave/requests'}:
        clone = APIRouter()
        clone.routes.append(route)
        router.include_router(clone, prefix='/ess', tags=['员工自助'])
