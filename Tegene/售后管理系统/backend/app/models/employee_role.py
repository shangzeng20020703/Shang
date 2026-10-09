"""
员工角色模型 - Employee Role (RBAC)
=====================================

本模块定义轻量级基于角色的访问控制（RBAC）所需的数据表，用于管理系统中各用户
的角色分配，并为 API 层的权限校验提供数据支撑。

业务背景
--------
售后管理系统采用轻量级 RBAC（Role-Based Access Control）模型：
  - 系统预定义有限数量的角色（admin / asset_admin / hr / manager / finance / employee）
  - 每位员工可被授予一个或多个角色（多角色并集取权限）
  - 权限校验在 API 层通过 `Depends(require_roles(...))` 注入实现
  - 角色授予/撤销操作本身会记录到 audit_logs 表

角色体系说明
------------
admin       — 系统管理员，拥有所有操作权限，包括用户管理、系统配置
asset_admin — 行政管理员，可操作固定资产等行政资产管理功能
hr          — 人力资源专员，可查阅/编辑所有员工信息和 HR 业务数据
manager     — 部门经理，可查看和审批其管辖部门下属员工的请求
finance     — 财务人员，可查阅薪资数据和财务相关报表
employee    — 普通员工，仅可查阅和操作自身相关信息（ESS 自助服务）

一员工多角色示例
----------------
一名既担任部门经理又兼任 HR BP 的员工，需要同时拥有 manager 和 hr 两个角色：
  employee_roles 表中该员工有两条记录，分别对应 manager 和 hr。
权限校验取两个角色的并集（OR 逻辑），即满足任意一个角色要求即可访问。

软停用机制
----------
角色记录不直接删除，而是将 is_active 置为 False（软停用）：
  - 保留授权历史，满足审计追溯要求
  - 可随时重新激活，无需重新建立授权记录
  - 在权限校验查询中需要加 WHERE is_active = TRUE 过滤

与认证（Authentication）的关系
-------------------------------
员工登录时，`core/deps.py` 的 `get_current_user` 函数从 JWT 中解析出
employee_id，再查询本表获取该员工的有效角色列表，供 `require_roles` 使用。

包含数据表
----------
- employee_roles      员工角色分配记录
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, func

from app.core.database import Base


class EmployeeRole(Base):
    """
    员工角色分配记录，实现轻量级 RBAC 的核心数据表。

    每条记录表示「某员工拥有某角色」的授权关系。同一员工可有多条记录（多角色），
    同一员工的同一角色在业务层保证唯一性（避免重复授权）。

    字段说明
    --------
    employee_id  — 被授权员工的 ID，外键关联 employees 表
    role_name    — 角色标识符，取值范围：admin/asset_admin/hr/manager/finance/employee
    is_active    — 角色是否生效；False 表示已撤销但保留记录用于审计溯源
    granted_by   — 授权操作人的员工 ID（外键），记录"谁给谁授的权"
    created_at   — 授权时间，与 granted_by 共同构成完整的授权审计链

    授权流程
    --------
    1. 系统管理员（admin 角色）通过 POST /api/v1/auth/roles 接口创建授权记录
    2. 写入本表时 granted_by 自动填入当前操作用户的 employee_id
    3. 撤销授权时执行 PATCH 将 is_active 改为 False，而非 DELETE

    权限校验调用路径
    ----------------
    HTTP 请求 → FastAPI 路由 → Depends(require_roles("admin", "hr"))
    → core/deps.py 查询本表 WHERE employee_id=? AND is_active=TRUE
    → 判断查询结果中是否包含所需角色 → 通过/拒绝

    级联删除
    --------
    员工从系统中删除时（员工表 DELETE），其所有角色记录随之删除（ondelete="CASCADE"）。
    注意：实际业务中员工一般走离职流程（is_active=False），不会物理删除。
    granted_by 引用的授权人离职/删除时，该字段置 NULL（ondelete="SET NULL"），
    保留本条角色记录不受影响。
    """

    __tablename__ = "employee_roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 被授权的员工 ID，员工删除时级联删除其所有角色记录
    employee_id = Column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="员工ID"
    )
    # 角色名称，取值：admin/asset_admin/hr/manager/finance/employee
    role_name = Column(
        String(30), nullable=False, index=True,
        comment="角色: admin/asset_admin/hr/manager/finance/employee"
    )
    # 角色是否生效；撤销授权时置 False 而非删除记录，保留审计轨迹
    is_active = Column(Boolean, nullable=False, default=True)
    # 授权人员工 ID，记录"谁授权的"，用于审计追溯；授权人离职后置 NULL 不影响被授权人
    granted_by = Column(
        Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True,
        comment="授权人ID"
    )
    # 授权时间，与 granted_by 共同构成授权事件的完整记录
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
