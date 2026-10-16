"""
模块名称: audit_log.py
模块作用: 审计日志 Pydantic Schemas — 记录系统中所有写操作的完整轨迹

数据流向:
    HTTP 请求/系统内部
        → AuditLogCreate（Service 层主动写入）
        → models/audit_log.py（AuditLog ORM）
        → PostgreSQL audit_logs 表
        → AuditLogOut（API 响应，前端展示）

被哪些端点使用:
    - GET  /api/v1/audit-logs          → 返回 AuditLogOut 列表（分页）
    - POST /api/v1/audit-logs（内部）  → 由 Service 层调用，不对外暴露

设计约束:
    1. 审计日志为追加写（append-only），一旦写入不可修改/删除 → 无对应的 AuditLogUpdate schema
    2. operator_id=None 表示系统自动操作（如定时任务、初始化脚本）
    3. before_data / after_data 存储 JSON 字符串快照，记录操作前后的数据状态，
       便于回溯和合规审查
    4. 所有写操作（create/update/delete）均应通过 Service 层触发日志写入
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AuditLogOut(BaseModel):
    """
    审计日志响应 Schema — 用于 GET /api/v1/audit-logs 接口的返回值

    每条记录代表系统中一次写操作的完整快照，供管理员查阅操作历史、
    进行合规审查或事故溯源。

    字段说明:
        id             — 日志主键，自增
        operator_id    — 操作人员工 ID；None 表示系统/定时任务自动操作
        operator_name  — 操作人姓名（冗余存储，防止员工离职后姓名查不到）
        action         — 操作类型，约定值: create / update / delete / approve / reject 等
        module         — 所属业务模块，如 "employee" / "leave" / "payroll"
        resource_id    — 被操作资源的主键 ID（如员工ID、合同ID）
        resource_type  — 被操作资源的类型名称，如 "Employee" / "LeaveRequest"
        before_data    — 操作前数据快照（JSON 字符串）；create 操作时为 None
        after_data     — 操作后数据快照（JSON 字符串）；delete 操作时为 None
        ip_address     — 操作人的客户端 IP 地址（用于安全审计）
        created_at     — 日志写入时间（PostgreSQL TIMESTAMPTZ，带时区）
    """
    id: int
    operator_id: Optional[int] = None       # None = 系统自动操作（无人工介入）
    operator_name: Optional[str] = None     # 冗余字段，防止员工数据被删后无法追溯操作人
    action: str                             # 操作动词: create/update/delete/approve 等
    module: str                             # 业务模块名，与 models/ 目录下模块名对应
    resource_id: Optional[int] = None      # 被操作资源主键；部分批量操作可能为 None
    resource_type: Optional[str] = None    # 资源类名，如 "Employee"、"Contract"
    before_data: Optional[str] = None      # 操作前的 JSON 快照；create 时为 None
    after_data: Optional[str] = None       # 操作后的 JSON 快照；delete 时为 None
    ip_address: Optional[str] = None       # 客户端 IP，来自 HTTP 请求头 X-Forwarded-For
    created_at: datetime                   # 写入时间（带时区）

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    # from_attributes=True: 支持从 SQLAlchemy ORM 对象直接序列化
    # protected_namespaces=(): 消除 Pydantic v2 对 "model_" 前缀字段的命名空间警告


class AuditLogCreate(BaseModel):
    """
    审计日志创建 Schema — 由 Service 层在每次写操作完成后调用

    注意: 此 Schema 不对外暴露为 HTTP 请求体，而是在业务 Service 内部
    构造后写入数据库。调用示例:
        await audit_service.log(AuditLogCreate(
            operator_id=current_user.id,
            action="update",
            module="employee",
            resource_id=employee.id,
            resource_type="Employee",
            before_data=json.dumps(old_data),
            after_data=json.dumps(new_data),
        ))

    字段与 AuditLogOut 一致，但不包含 id 和 created_at（由数据库自动生成）。
    无对应 AuditLogUpdate — 审计日志是不可变的（append-only），禁止修改。
    """
    operator_id: Optional[int] = None       # None = 系统自动操作
    operator_name: Optional[str] = None     # 操作人姓名，建议与 operator_id 同时传入
    action: str                             # 操作类型: create / update / delete 等
    module: str                             # 业务模块标识
    resource_id: Optional[int] = None      # 被操作资源 ID
    resource_type: Optional[str] = None    # 被操作资源类型
    before_data: Optional[str] = None      # 操作前 JSON 快照（建议用 json.dumps() 序列化）
    after_data: Optional[str] = None       # 操作后 JSON 快照（建议用 json.dumps() 序列化）
    ip_address: Optional[str] = None       # 客户端 IP，从 Request 对象中提取
