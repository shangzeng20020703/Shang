"""
模块名称: 组织架构数据模型
文件路径: app/models/organization.py

================================================================================
表结构总览 (Table Overview)
================================================================================
本模块定义以下 2 张数据库表：

  1. locations    — 工作地点表，记录本公司各物理办公/生产地点的基本信息
  2. departments  — 部门表，支持任意层级的树形部门结构（自引用外键）

================================================================================
业务说明 (Business Context)
================================================================================
本模块是售后管理系统的「组织架构基础数据层」，为员工分配提供
「工作地点」和「所属部门」两个核心维度，是员工档案、考勤、薪资等模块
进行多维度分析的基准。

本公司业务布局（截至当前）：
  工作地点（locations）：
    - 北京研发中心   — 产品研发与软件工程团队
    - 东莞凇湖工厂   — 机器人本体生产基地
    - 东莞茵茵工厂   — 精密零部件加工基地

  法人主体（companies，位于 payroll 模块）：
    - 本公司总部
    - 东莞旋光等子公司
    注意：Company 模型定义在 app/models/payroll.py，不在本文件

  部门结构（departments）：
    支持多级树形结构，通过 parent_id 自引用实现任意层级的部门层级，
    典型层级为 3 级：公司级 → 事业部/中心 → 业务组/班组

================================================================================
数据流 (Data Flow)
================================================================================
写入方：
  - HR 管理员通过「组织架构」模块维护地点和部门数据
  - 系统初始化时通过 seed_dev.py 脚本导入初始地点和部门数据
  - 部门负责人信息通过 manager_id 关联到 employees 表

读取方（使用地点 / 部门数据的模块）：
  - employee 模块：employees.location_id / employees.department_id 关联此表
  - attendance 模块：按 location_id 匹配打卡规则；按部门汇总出勤数据
  - payroll 模块：按 location_id 确定社保城市基准；部门维度薪资汇总
  - performance 模块：按部门和地点维度统计绩效分布
  - training 模块：课程可按部门/地点范围指定受众
  - recruitment 模块：招聘需求关联目标部门
  - dashboard 模块：组织架构人员分布图（地点/部门两个维度）
  - analytics 模块：人力结构分析报表中的部门/地点维度过滤
  - talent 模块：人才地图中的部门维度组织视图

================================================================================
核心关联关系 (Key Relationships)
================================================================================

  locations (1) ←→ (N) departments
      一个工作地点可包含多个部门（如东莞凇湖工厂有多个生产部门）
      departments.location_id → locations.id（SET NULL on delete）

  locations (1) ←→ (N) employees
      一个工作地点有多名员工
      employees.location_id → locations.id（SET NULL on delete）

  departments (自引用树形结构)
      parent_id → departments.id（SET NULL on delete）
      顶级部门（一级部门）的 parent_id = NULL，level = 1
      子部门通过 parent 关系向上追溯，通过 children 关系向下展开
      API 层将此树形结构序列化为嵌套 JSON 返回给前端（/api/v1/departments/tree）

  departments (N) ←→ (1) employees（负责人）
      departments.manager_id → employees.id（SET NULL on delete）
      表示该部门的正式负责人；与 employees.department_id 不同，
      employees.department_id 表示「该员工所在部门」，
      departments.manager_id 表示「部门的负责人是哪位员工」

  departments (1) ←→ (N) employees（成员）
      departments.id ← employees.department_id
      即所有 department_id 指向该部门的员工都属于此部门

================================================================================
关键设计决策 (Design Decisions)
================================================================================

  1. 部门树的懒加载策略：
     parent / children / location / manager / employees 均使用 lazy="selectin"，
     SQLAlchemy 会在加载 Department 时自动发出额外 SELECT 查询填充这些关联。
     这在查询单个部门详情时很便捷，但在全量查询大量部门时可能产生 N+1 问题。
     全量树形接口应在 service 层手动构建树（一次性加载所有部门后在内存中组装），
     而非依赖 ORM 递归加载。

  2. 部门 code 全局唯一约束（uq_department_code）：
     部门编码用于外部系统集成（如考勤机、财务系统）时的唯一标识，
     必须在全表唯一，不允许不同层级部门使用相同编码。

  3. location 表无 created_at / updated_at：
     地点数据相对稳定，变更极少，当前设计省略了时间戳字段。
     如业务需要审计地点变更，后续可通过 migration 补充。

================================================================================
被哪些模块使用 (Consumers)
================================================================================
  - app/services/organization.py   — 部门树/地点 CRUD 主服务
  - app/services/employee.py       — 员工创建/更新时校验 department_id / location_id
  - app/services/attendance.py     — 按地点匹配打卡规则，按部门汇总出勤
  - app/services/payroll.py        — 按地点/部门维度薪资统计
  - app/services/dashboard.py      — 组织人力分布看板
  - app/services/analytics.py      — 人力结构分析，部门维度离职率等
  - app/services/recruitment.py    — 招聘需求的目标部门
  - app/services/training.py       — 培训计划的适用部门范围
  - app/services/talent.py         — 人才发展中的部门维度分析
  - app/services/talent_profile.py — 人才画像的部门归属信息
  - app/api/v1/organizations.py    — 部门树/地点 REST 接口（含 /departments/tree）
  - app/api/v1/locations.py        — 地点管理 REST 接口

"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class Location(Base):
    """
    工作地点表 (locations)

    表作用：
      定义本公司的物理工作地点，是员工组织归属的地理维度。
      目前已配置地点（初始数据）：
        - 北京研发中心
        - 东莞凇湖工厂
        - 东莞茵茵工厂

      工作地点的主要作用：
        1. 员工档案的「工作地点」字段（employees.location_id）
        2. 考勤规则绑定到地点（不同地点可配置不同打卡时间/设备）
        3. 社保城市关联（不同地点的员工适用不同城市的社保基数标准）
        4. 组织架构树中的地点维度过滤
        5. 管理驾驶舱中的「按地点分布」统计图表

    关键字段说明：
      name         — 地点名称，全局唯一（unique=True）；前端下拉菜单和报表中的
                     显示名称，应与内部习惯称呼一致（如「东莞凇湖」而非「凇湖工厂」）
      city         — 所在城市名，用于匹配社保/公积金缴纳城市基准表；
                     与 employees.social_insurance_city 字段配合使用
      contact_phone — 该地点的前台/行政联系电话，用于访客和快递接收
      is_active    — 软删除标志；地点停用后（如工厂迁移）应设为 False 而非物理删除，
                     以保留历史员工记录的归属关系

    业务规则：
      1. 地点不应物理删除，应通过 is_active=False 禁用；
         禁用后该地点不再出现在新员工入职的地点下拉列表中
      2. 地点与部门是多对多的逻辑关联（一个地点有多个部门，
         一个部门理论上只属于一个地点，但通过 departments.location_id 体现）

    关联关系：
      departments — 一对多，该地点下的所有部门列表
                    （注意：部门删除时 location_id 置 NULL，不影响地点记录）
      employees   — 一对多，在该地点工作的所有员工列表
                    （注意：员工离职后 location_id 保留历史值，可通过 status 过滤）
    """

    __tablename__ = "locations"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    name: str = Column(String(100), unique=True, nullable=False, comment="地点名称")
    address: str = Column(String(500), nullable=True, comment="详细地址")
    city: str = Column(String(50), nullable=True, comment="所在城市")
    contact_phone: str = Column(String(20), nullable=True, comment="联系电话")
    is_active: bool = Column(Boolean, default=True, nullable=False, comment="是否启用")

    # 关联：该地点下辖的所有部门；前端组织架构树按地点分组展示部门列表
    departments = relationship("Department", back_populates="location", lazy="selectin")

    # 关联：在该地点工作的所有员工；用于按地点统计人数、考勤归属等
    employees = relationship("Employee", back_populates="location", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Location(id={self.id}, name='{self.name}')>"


class Department(Base):
    """
    部门表 (departments) — 支持任意层级的树形组织架构。

    表作用：
      定义本公司的部门体系，通过 parent_id 自引用实现树形结构。
      典型层级为 3 级：
        Level 1（顶级）：公司级部门，如「研发中心」「生产事业部」「职能部门」
        Level 2（二级）：事业部/中心，如「软件研发部」「硬件研发部」
        Level 3（三级）：业务组/班组，如「算法组」「前端组」「冲压班组」

      部门树的完整结构通过 /api/v1/departments/tree 接口返回给前端，
      前端渲染为可折叠的组织架构树形图。

    关键字段说明：
      code         — 部门编码，全局唯一（uq_department_code 约束）；
                     是与外部系统（考勤机、ERP、财务）对接的唯一标识符；
                     一旦分配后不建议修改，会影响外部系统映射关系
      parent_id    — 上级部门 ID（自引用外键）；
                     parent_id = NULL 表示顶级部门（level=1）；
                     ondelete="SET NULL"：上级部门删除时子部门不被级联删除，
                     而是变为孤立的顶级部门（需 HR 手动重新归属）
      manager_id   — 部门负责人的 employees.id；
                     注意区分：这是「部门负责人」，与部门成员（employees 关联）不同；
                     负责人同时也是该部门成员（employees.department_id 指向此部门）
      level        — 层级深度（1 为顶级），在构建树形结构时作为辅助字段使用；
                     应与实际 parent_id 链路保持一致，由 service 层在创建/移动部门时维护
      sort_order   — 同级部门的显示排序（数值越小越靠前）；
                     children 关联按此字段升序排列，影响前端组织架构树的展示顺序
      is_active    — 软删除标志；部门撤销时设为 False，历史员工数据保留

    业务规则：
      1. 部门不应物理删除，应通过 is_active=False 禁用；
         禁用后该部门不再出现在新员工入职/调岗的部门下拉列表中，
         但历史在该部门的员工 department_id 保留（可查历史）
      2. 部门负责人（manager_id）应同时是该部门的成员；
         如有人员变动，需同步更新 manager_id
      3. 全量树形加载时，service 层应一次性查出所有部门记录，
         在内存中按 parent_id 构建树，避免 ORM 递归懒加载的 N+1 问题

    自引用树形关系：
      parent   — 多对一，获取当前部门的直接上级部门；
                 remote_side 声明 Department.id 是「一端」（父），
                 back_populates="children" 与子部门关系互相引用
      children — 一对多，获取当前部门的所有直接子部门列表；
                 order_by=sort_order 确保前端展示顺序与 HR 配置一致

    关联关系：
      location  — 多对一，该部门所属的工作地点
      manager   — 多对一，指向负责人的 Employee 对象
      employees — 一对多，所有 department_id 指向此部门的员工列表
    """

    __tablename__ = "departments"
    __table_args__ = (
        # 部门编码全局唯一约束，用于外部系统对接
        UniqueConstraint("code", name="uq_department_code"),
    )

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    name: str = Column(String(100), nullable=False, comment="部门名称")
    code: str = Column(String(50), nullable=False, comment="部门编码")
    parent_id: int = Column(
        Integer,
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        comment="上级部门ID（自引用）",
    )
    location_id: int = Column(
        Integer,
        ForeignKey("locations.id", ondelete="SET NULL"),
        nullable=True,
        comment="所属工作地点",
    )
    company_id: int = Column(
        Integer,
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="所属公司主体ID",
    )
    manager_id: int = Column(
        Integer,
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        comment="部门负责人",
    )
    headcount_quota: int = Column(Integer, default=0, nullable=False, comment="部门编制人数")
    level: int = Column(Integer, default=1, nullable=False, comment="层级深度（1=顶级）")
    sort_order: int = Column(Integer, default=0, nullable=False, comment="同级排序")
    is_active: bool = Column(Boolean, default=True, nullable=False, comment="是否启用")
    created_at: datetime = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, comment="创建时间"
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="更新时间",
    )

    # 自引用关系 - 父子部门

    # 上级部门（多对一自引用）
    # remote_side="Department.id" 表明 Department.id 是树中的「父节点端」，
    # 即当前部门的 parent_id 指向另一个部门的 id；
    # back_populates="children" 与下方 children 关系互为反向
    parent = relationship(
        "Department",
        remote_side="Department.id",
        back_populates="children",
        lazy="selectin",
    )

    # 子部门列表（一对多自引用）
    # 加载当前部门时自动获取所有直接子部门，order_by=sort_order 保证展示顺序；
    # 注意：这是「直接子部门」而非「所有后代部门」，递归展开需在 service 层处理
    children = relationship(
        "Department",
        back_populates="parent",
        lazy="selectin",
        order_by="Department.sort_order",
    )

    # 外键关系

    # 所属工作地点 — 用于地点维度的部门分组和人员统计
    location = relationship("Location", back_populates="departments", lazy="selectin")

    # 所属公司主体 — 用于构建「公司 -> 部门」组织森林
    company = relationship("Company", lazy="selectin")

    # 部门负责人 — 用于展示部门详情页和审批流中的「部门负责人」节点
    # 注意：manager 只是 Employee 到 Department 的单向引用，Employee 侧无 back_populates
    manager = relationship(
        "Employee",
        foreign_keys=[manager_id],
        lazy="selectin",
    )

    # 部门成员列表 — 所有 department_id 指向本部门的员工
    # foreign_keys 明确指定用于消除 SQLAlchemy 多外键路径的歧义
    # （Department 同时通过 manager_id 和 employees.department_id 与 Employee 关联）
    employees = relationship(
        "Employee",
        back_populates="department",
        foreign_keys="Employee.department_id",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Department(id={self.id}, name='{self.name}', code='{self.code}')>"


class OrgCompanyLayout(Base):
    """
    组织架构中的公司节点布局配置（独立于公司主数据）。

    说明：
      - company_id 绑定真实公司主体（companies.id）
      - parent_company_id 仅用于组织架构展示中的上级关系覆写
      - is_visible=False 表示在组织架构中隐藏该公司节点（不删除主数据）
    """

    __tablename__ = "org_company_layouts"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_org_company_layout_company_id"),
    )

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    company_id: int = Column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="公司ID（对应主公司表）",
    )
    parent_company_id: int = Column(
        Integer,
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="组织架构展示中的上级公司ID（覆写主数据）",
    )
    is_visible: bool = Column(Boolean, default=True, nullable=False, comment="是否在组织架构中显示")
    created_at: datetime = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, comment="创建时间"
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="更新时间",
    )
