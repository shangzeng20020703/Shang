"""
审批引擎模型 - Approval Engine Models
======================================

本模块实现了售后管理系统的通用审批引擎，供全系统各业务模块（请假、加班、
薪资调整、离职等）复用，通过解耦设计将审批逻辑与业务逻辑完全分离。

【核心设计思路：通用引擎解耦】
─────────────────────────────────────────────────────────────────────────
  审批流与具体业务表无直接外键关联。
  ApprovalInstance 通过 (business_type + business_id) 两个字段定位业务记录：
    - business_type: 业务类型字符串，例如 "leave_request" / "overtime" / "salary_adjust"
    - business_id:   对应业务表的主键 ID（如 leave_requests.id）
  这样一套审批引擎可以挂载到任意业务模块，新增业务模块时无需修改审批相关表结构。

【完整审批流转流程】
─────────────────────────────────────────────────────────────────────────
  1. 管理员配置阶段（一次性配置）：
       ApprovalFlow（流程定义）
         └─ ApprovalNode（节点配置，按 node_order 排序）
              ├─ 节点类型 (node_type): approval/notify/auto
              ├─ 审批人类型 (approver_type): direct_manager/specific_user/hr/...
              └─ 条件规则 (condition_rules): 可选，用于条件分支

  2. 员工发起审批阶段：
       员工提交业务申请（如请假）→ 服务层创建 ApprovalInstance
         ├─ flow_id: 绑定对应的 ApprovalFlow
         ├─ business_type + business_id: 指向业务记录
         ├─ flow_snapshot: 【快照】复制当前流程配置为 JSON 存入实例
         │   （目的：防止管理员日后修改流程配置影响已在审批中的历史实例）
         ├─ current_node_order: 初始化为 1，表示当前处于第一个节点
         └─ status: 初始为 "pending"

  3. 审批人逐节点审批阶段：
       系统根据 current_node_order 找到当前 ApprovalNode →
         解析 approver_type 确定实际审批人（direct_manager 需动态查员工直属上司）→
       审批人操作：同意/拒绝/转审 → 写入 ApprovalRecord（一个节点一条记录）
         ├─ action = "approve"：current_node_order += 1，继续下一节点
         ├─ action = "reject" ：instance.status = "rejected"，流程终止
         └─ action = "transfer"：将本节点转给其他人，不推进 node_order

  4. 流程结束阶段：
       所有节点通过 → instance.status = "approved" → 服务层回调业务记录（如修改请假单状态）
       任一节点拒绝 → instance.status = "rejected" → 服务层通知申请人
       申请人主动撤回 → instance.status = "withdrawn"（仅限 pending 阶段）
       申请人取消     → instance.status = "cancelled"

【节点类型 node_type 说明】
─────────────────────────────────────────────────────────────────────────
  - approval（审批节点）: 需要审批人明确同意或拒绝，流程在此阻塞等待操作。
                          最常用的节点类型，支持超时自动通过（auto_approve_hours）。
  - notify（通知节点）:   仅发送通知给指定人员，无需操作，系统自动流转到下一节点。
                          用于抄送上级或 HR 知晓，不影响审批进度。
  - auto（自动通过节点）: 满足 condition_rules 条件时系统自动通过本节点。
                          用于简单规则判断，如"请假天数≤1天则直接通过"。

【审批人类型 approver_type 说明】
─────────────────────────────────────────────────────────────────────────
  - direct_manager  : 申请人的直属上级（由系统动态解析 Employee.direct_manager_id）。
                      优点：流程配置一次，适用所有人；缺点：若无上级则需 fallback 处理。
  - project_manager : 项目负责人（适用于项目制团队的加班/外出申请）。
  - hr              : HR 部门（自动路由到 HR 角色的任意一位有效员工）。
  - department_head : 部门负责人（动态解析该员工所在部门的 head_id）。
  - gm              : 总经理（公司最高审批人，通常为最终节点）。
  - specific_user   : 固定指定某人（由 approver_id 字段存储具体员工 ID）。
                      适用于特殊岗位（如财务审核固定为财务总监）。

【委托代理机制 ApprovalDelegate】
─────────────────────────────────────────────────────────────────────────
  当审批人出差、休假等无法及时处理审批时，可创建 ApprovalDelegate 记录，
  将审批权在指定日期范围内委托给代理人。
  系统在解析审批人时，先检查该审批人在当前日期是否有有效委托记录，
  若有则将审批任务路由给代理人，代理人以自己身份操作，记录仍存入 ApprovalRecord。
  委托结束（end_date 过期或手动将 is_active 设为 False）后自动失效。

【数据模型关系图】
─────────────────────────────────────────────────────────────────────────
  ApprovalFlow (1) ──< (n) ApprovalNode       [节点配置，随流程级联删除]
  ApprovalFlow (1) ──< (n) ApprovalInstance   [流程实例，不级联删除]
  ApprovalInstance (1) ──< (n) ApprovalRecord [审批动作，随实例级联删除]
  Employee (1) ──< (n) ApprovalDelegate       [委托记录，双向 FK]
"""

from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    Float,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class ApprovalType(Base):
    __tablename__ = "approval_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    business_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    category_key: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(50), nullable=False, default="mobile", server_default="mobile")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    icon: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    icon_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    icon_tone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    print_format: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    permission_rules: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    exception_rules: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    auto_approval_rule: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    form_fields: Mapped[list["ApprovalFormField"]] = relationship(
        "ApprovalFormField",
        back_populates="approval_type",
        cascade="all, delete-orphan",
        order_by="ApprovalFormField.sort_order",
    )
    versions: Mapped[list["ApprovalTemplateVersion"]] = relationship(
        "ApprovalTemplateVersion",
        back_populates="approval_type",
        cascade="all, delete-orphan",
        order_by="ApprovalTemplateVersion.version",
    )


class ApprovalTemplateVersion(Base):
    __tablename__ = "approval_template_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    approval_type_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("approval_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="published", server_default="published")
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    approval_type: Mapped["ApprovalType"] = relationship(
        "ApprovalType",
        back_populates="versions",
    )


class ApprovalFormField(Base):
    __tablename__ = "approval_form_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    approval_type_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("approval_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    field_type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    default_value: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    options_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    display_condition: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    validation_rule: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    editable_scope: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    print_visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    placeholder: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    is_business_calculation: Mapped[bool] = mapped_column(Boolean, default=False)
    is_readonly: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    approval_type: Mapped["ApprovalType"] = relationship(
        "ApprovalType",
        back_populates="form_fields",
    )


class ApprovalFlow(Base):
    """审批流程定义 - Approval flow definition.

    代表一套可复用的审批流程模板，由管理员在系统后台配置。
    一个流程包含多个有序节点（ApprovalNode），节点按 node_order 升序依次触发。

    每种业务类型通常对应一条 ApprovalFlow 记录，例如：
      - "请假审批流程"（module=leave）
      - "加班审批流程"（module=overtime）
      - "薪资调整审批流程"（module=salary_adjust）

    is_active=False 可停用流程，停用后新提交的申请将无法发起该流程，
    但已在途的 ApprovalInstance 不受影响（因为已有 flow_snapshot 快照）。
    """

    __tablename__ = "approval_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="流程名称")
    module: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="所属模块: leave/overtime/recruitment/payroll/salary_adjust/offboard",
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="流程描述")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )

    # 级联关系：删除流程时同步删除所有节点配置（orphan 自动清理）
    nodes: Mapped[list["ApprovalNode"]] = relationship(
        "ApprovalNode",
        back_populates="flow",
        cascade="all, delete-orphan",
        order_by="ApprovalNode.node_order",  # 按节点顺序排列，确保审批依序流转
    )
    # 不级联删除实例：历史审批实例须长期保留用于审计
    instances: Mapped[list["ApprovalInstance"]] = relationship(
        "ApprovalInstance",
        back_populates="flow",
    )


class ApprovalNode(Base):
    """审批节点配置 - Approval node within a flow.

    定义一个流程中某一步骤的审批规则，包括：由谁审批（approver_type/approver_id）、
    节点类型（是否需要人工操作）、以及超时策略。

    节点与流程通过 CASCADE 外键关联，删除流程时节点随之删除。
    节点本身不存储"当前是否激活"状态，流程实例的 current_node_order 指针决定哪个节点生效。

    【condition_rules 用法示例】
    存储为 JSON 字符串，服务层解析后判断是否触发该节点：
      '{"field": "days", "op": "gt", "value": 3}'  → 请假天数 > 3 天时触发
      '{"field": "amount", "op": "gte", "value": 10000}'  → 金额 >= 10000 时触发
    条件不满足则跳过该节点，直接推进到下一节点。
    """

    __tablename__ = "approval_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flow_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("approval_flows.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属流程ID",
    )
    # 节点执行顺序：从 1 开始递增，流程实例按此顺序依次推进
    node_order: Mapped[int] = mapped_column(Integer, nullable=False, comment="节点顺序")
    approver_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="审批人类型: direct_manager/project_manager/hr/department_head/gm/specific_user",
        # direct_manager: 系统动态解析申请人的直属上级，灵活适配各层级
        # specific_user:  固定指定某位员工（需同时填写 approver_id）
    )
    # 仅当 approver_type = "specific_user" 时有值，存储被指定审批人的员工 ID
    approver_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="指定审批人ID（approver_type=specific_user时使用）",
    )
    # 超时自动通过：若审批人在规定小时内未操作，系统自动视为同意并推进流程
    # 设为 NULL 表示无超时机制，流程将无限期等待审批人操作
    auto_approve_hours: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="超时自动通过时间（小时），NULL表示不自动通过",
    )
    node_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="approval",
        server_default="approval",
        comment="节点类型: approval(需人工审批)/notify(仅通知无需操作)/auto(满足条件自动通过)",
    )
    condition_rules: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="条件规则JSON: {'field':'days','op':'gt','value':3}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )

    # 反向关联到所属流程
    flow: Mapped["ApprovalFlow"] = relationship("ApprovalFlow", back_populates="nodes")


class ApprovalInstance(Base):
    """审批流程实例 - Approval instance for a specific business request.

    每当员工提交一条需要审批的业务申请时，系统创建一个 ApprovalInstance。
    实例是"流程运行时"的概念，记录了该次审批的当前进度与最终结果。

    【关键字段：业务解耦设计】
    business_type + business_id 共同定位具体业务记录，例如：
      business_type="leave_request", business_id=42  → 指向 leave_requests 表 id=42 的记录
    这种设计避免了审批表与业务表之间的直接外键依赖，
    使审批引擎可以跨模块通用，新增业务模块无需修改审批表结构。

    【关键字段：flow_snapshot 快照机制】
    实例创建时，将当前 ApprovalFlow + ApprovalNode 配置序列化为 JSON 存入 flow_snapshot。
    这样做的核心目的是：防止管理员修改审批流程配置（如增减节点、更换审批人）
    对已在途的历史审批实例产生影响。
    审批进行中时，服务层优先读取 flow_snapshot 而非实时查询 ApprovalNode 表。
    快照内容示例：
      {
        "flow_id": 1,
        "flow_name": "请假审批流程",
        "nodes": [
          {"node_order": 1, "approver_type": "direct_manager", "node_type": "approval"},
          {"node_order": 2, "approver_type": "hr", "node_type": "notify"}
        ]
      }

    【状态机流转】
    pending → approved    （所有节点审批通过）
    pending → rejected    （任一节点被拒绝）
    pending → withdrawn   （申请人主动撤回，仅允许在 pending 阶段）
    pending → cancelled   （业务侧主动取消，如招聘需求关闭）
    """

    __tablename__ = "approval_instances"

    template_version_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("approval_template_versions.id"),
        nullable=True,
        index=True,
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flow_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("approval_flows.id"),
        nullable=False,
        comment="审批流程ID",
    )
    # 业务模块标识，与 ApprovalFlow.module 保持一致，用于快速筛选某模块的全部实例
    module: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="业务模块",
    )
    # 以下两个字段共同构成对业务记录的逻辑外键（无物理 FK 约束，保持解耦）
    business_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="业务记录ID（对应业务表主键）",
    )
    business_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="业务类型（如 leave_request / overtime / salary_adjust）",
    )
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    form_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    applicant_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id"),
        nullable=False,
        comment="申请人ID",
    )
    # 快照：实例创建时将流程配置序列化存储，防止后续流程变更影响历史审批进度
    flow_snapshot: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="流程快照JSON — 创建实例时保存当时的流程配置，防止流程变更影响历史实例",
    )
    # 当前正在等待操作的节点顺序号（从 1 开始），服务层通过此字段确定推送给谁审批
    current_node_order: Mapped[int] = mapped_column(
        Integer,
        default=1,
        comment="当前审批节点顺序（从1开始，每次节点通过后 +1）",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        comment="状态: pending(审批中)/approved(通过)/rejected(拒绝)/cancelled(取消)/withdrawn(撤回)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="更新时间（每次节点推进时自动更新）",
    )

    # 反向关联到流程定义
    flow: Mapped["ApprovalFlow"] = relationship("ApprovalFlow", back_populates="instances")
    # 级联删除：实例删除时同步清除所有审批动作记录
    records: Mapped[list["ApprovalRecord"]] = relationship(
        "ApprovalRecord",
        back_populates="instance",
        cascade="all, delete-orphan",
        order_by="ApprovalRecord.node_order",  # 按节点顺序排列，便于前端展示审批链路
    )
    tasks: Mapped[list["ApprovalTask"]] = relationship(
        "ApprovalTask",
        back_populates="instance",
        cascade="all, delete-orphan",
        order_by="ApprovalTask.node_order",
    )
    template_version: Mapped[Optional["ApprovalTemplateVersion"]] = relationship(
        "ApprovalTemplateVersion",
    )


class ApprovalRecord(Base):
    """审批动作记录 - Individual approval action record.

    每次审批人在某个节点上执行操作（同意/拒绝/转审），都会产生一条 ApprovalRecord。
    记录是不可变的审计日志，一旦写入不允许修改（通过接口层限制）。

    【与 ApprovalInstance 的关系】
    一个 ApprovalInstance 包含多条 ApprovalRecord（一个节点对应一条，
    但 transfer 操作可能产生多条同 node_order 的记录）。
    审批链路的完整历史可通过 instance_id 查询所有关联记录还原。

    【action 字段说明】
    - approve:  审批通过，instance.current_node_order += 1，继续下一节点
    - reject:   审批拒绝，instance.status = "rejected"，终止流程
    - transfer: 转审给他人，本节点由新指定人重新审批（不推进 node_order）

    【acted_at vs created_at】
    acted_at 记录审批人点击操作按钮的时间（业务语义），
    created_at 记录数据库记录创建时间（技术语义），两者通常相同但区分语义清晰。
    """

    __tablename__ = "approval_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("approval_instances.id", ondelete="CASCADE"),
        nullable=False,
        comment="审批实例ID",
    )
    # 记录该动作发生时对应的节点顺序，用于还原审批历史时定位节点位置
    node_order: Mapped[int] = mapped_column(Integer, nullable=False, comment="节点顺序")
    branch_key: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="并行分支键",
    )
    branch_label: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="并行分支名称",
    )
    branch_node_order: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="并行分支内节点顺序",
    )
    approver_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id"),
        nullable=False,
        comment="实际操作的审批人ID（委托代理时为代理人ID，非委托人ID）",
    )
    action: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="操作: approve(通过)/reject(拒绝)/transfer(转审)",
    )
    # 审批意见：拒绝时建议必填，用于向申请人说明原因
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="审批意见")
    acted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="审批操作时间",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="记录创建时间",
    )

    # 反向关联到所属实例
    instance: Mapped["ApprovalInstance"] = relationship(
        "ApprovalInstance", back_populates="records"
    )


class ApprovalTask(Base):
    __tablename__ = "approval_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("approval_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_order: Mapped[int] = mapped_column(Integer, nullable=False)
    branch_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    branch_label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    branch_node_order: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    approver_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        server_default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    instance: Mapped["ApprovalInstance"] = relationship(
        "ApprovalInstance", back_populates="tasks"
    )

class ApprovalDelegate(Base):
    """审批委托代理授权 - Approval delegation record.

    当审批人（委托人）因出差、休假或其他原因在一段时间内无法及时处理审批时，
    可通过此表授权给指定代理人代为审批。

    【委托代理的工作机制】
    系统在解析某节点的审批人时（例如 approver_type=direct_manager，解析出员工A），
    会先检查员工A在当前日期是否存在有效的 ApprovalDelegate 记录（is_active=True
    且 start_date <= today <= end_date），若存在则将任务路由给 delegate_id 对应的代理人。
    代理人以自己的身份操作，ApprovalRecord.approver_id 记录的是代理人 ID。

    【委托有效性条件】
    同时满足以下条件时委托有效：
    1. is_active = True（未被手动撤销）
    2. start_date <= 当前日期 <= end_date（在委托日期范围内）
    一个委托人同一时段内建议只有一条有效委托记录（业务层面约束，数据库层未做唯一索引）。

    【重要限制】
    委托代理仅限于审批操作，不影响被委托人作为申请人的权限。
    代理期结束后，委托记录建议保留（is_active 设为 False）以供审计追溯，不建议物理删除。
    """

    __tablename__ = "approval_delegates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # delegator_id：发起委托的人（即原本应该审批的那个人，如直属上级）
    delegator_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"),
        nullable=False,
        index=True,  # 系统检查委托时按 delegator_id 查询，需要索引
        comment="委托人ID（将审批权委托出去的员工）",
    )
    # delegate_id：被委托代理审批的人（代理人）
    delegate_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"),
        nullable=False,
        index=True,  # 代理人查看自己的代理任务时按此字段查询
        comment="代理人ID（代为执行审批操作的员工）",
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, comment="委托开始日期（含）")
    end_date: Mapped[date] = mapped_column(Date, nullable=False, comment="委托结束日期（含）")
    # is_active=False 可提前终止委托，无需删除记录（保留审计痕迹）
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="委托是否仍有效（可手动提前终止）")
    reason: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="委托原因（如：出差/休假/培训）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="创建时间",
    )

    # 双向 Employee 关联，需通过 foreign_keys 消除 SQLAlchemy 的 FK 歧义
    delegator: Mapped["Employee"] = relationship(  # type: ignore[name-defined]
        "Employee", foreign_keys=[delegator_id]
    )
    delegate: Mapped["Employee"] = relationship(  # type: ignore[name-defined]
        "Employee", foreign_keys=[delegate_id]
    )
