"""
通用审批引擎模块 - Pydantic Schemas
=====================================

模块名称: approval.py
所属系统: 售后管理系统 - 审批引擎模块

作用:
    定义通用审批流程引擎的请求/响应数据结构。
    审批引擎采用"流程模板 + 实例"设计，可复用于任意业务模块，
    无需为每个业务单独开发审批逻辑。

核心设计 - 三层结构:
    1. ApprovalFlow（审批流程模板）:
       定义流程的整体属性（所属模块、名称、启用状态）。
       一个模块可配置多个流程（如：普通请假流程、超长请假流程）。

    2. ApprovalNode（流程节点）:
       定义每个审批环节的规则：
         - node_order: 节点顺序（1→2→3，逐节点推进）
         - approver_type: 审批人类型（直属上级/HR/部门负责人/指定人员等）
         - node_type: 节点类型（approval=需审批/notify=仅通知/auto=自动通过）
         - auto_approve_hours: 超时自动通过时限（小时）
         - condition_rules: 条件规则（JSON），满足条件才执行此节点

    3. ApprovalInstance（流程实例）:
       每次发起审批时创建一个实例，关联到具体的业务记录：
         - business_type + business_id: 关联任意业务（如 business_type="leave", business_id=42）
         - flow_snapshot: 创建实例时的流程快照（JSON 字符串），
                          防止后期修改审批模板影响历史审批记录
         - current_node_order: 当前正在审批的节点编号
         - status: 实例状态（pending/approved/rejected/cancelled）

    4. ApprovalRecord（审批记录）:
       每个审批人在某节点上的单次操作记录（approve/reject/transfer）。
       一个实例可有多条 ApprovalRecord（对应多个节点）。

业务关联方式（通用引擎）:
    其他模块发起审批时，创建 ApprovalInstance，传入：
      - module: "leave"（请假）/ "recruitment"（招聘）/ "payroll"（薪资）等
      - business_type: 更细粒度的类型（如 "annual_leave"、"sick_leave"）
      - business_id: 对应业务表的主键 ID（如 leave_request_id=42）

    审批引擎根据 module 匹配对应的 ApprovalFlow，
    按节点顺序逐一推进，直到全部节点通过或某节点拒绝。

委托机制（ApprovalDelegate）:
    审批人出差/休假时，可委托他人代为审批。
    委托生效期间，委托人收到的审批任务自动转给受托人。

数据流向:
    业务模块发起审批 → ApprovalInstanceCreate（绑定 business_type + business_id）
         ↓ service 层匹配流程并创建实例
    审批人收到待办 → ApprovalActionRequest（approve/reject/transfer）
         ↓ service 层记录 ApprovalRecord，推进到下一节点
    全部节点通过 → 实例 status=approved，回调通知业务模块
         ↓
    *Out Schema 序列化后返回前端

被调用的 API 端点 (api/v1/endpoints/approval.py):
    流程管理（HR/Admin）:
        POST   /approvals/flows                  - 创建审批流程
        GET    /approvals/flows                  - 列表
        GET    /approvals/flows/{id}             - 详情（含节点）
        PUT    /approvals/flows/{id}             - 更新
        POST   /approvals/flows/{id}/nodes       - 添加节点
        PUT    /approvals/flows/{id}/nodes/{nid} - 更新节点
        DELETE /approvals/flows/{id}/nodes/{nid} - 删除节点
    审批操作:
        POST   /approvals/instances              - 发起审批
        GET    /approvals/instances              - 待审批列表（分页）
        GET    /approvals/instances/{id}         - 实例详情（含历史记录）
        POST   /approvals/instances/{id}/action  - 审批操作（通过/拒绝/转审）
        POST   /approvals/bulk-action            - 批量审批
    委托:
        POST   /approvals/delegates              - 创建委托
        GET    /approvals/delegates              - 委托列表
        DELETE /approvals/delegates/{id}         - 删除委托
    转审:
        POST   /approvals/transfer               - 转移审批人

依赖:
    - pydantic v2: BaseModel, ConfigDict, Field
    - 关联 models/approval.py: ApprovalFlow, ApprovalNode, ApprovalInstance,
      ApprovalRecord, ApprovalDelegate
"""

from datetime import date, datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

APPROVAL_NODE_TYPE_PATTERN = r"^(approval|handler|notify|auto|condition|condition_branch|parallel_branch|applicant|end)$"


# ───────── ApprovalFlow ─────────

class ApprovalTypeBase(BaseModel):
    name: str = Field(..., max_length=50)
    business_code: str = Field(..., max_length=100)
    category: str = Field(..., max_length=50)
    category_key: Optional[str] = Field(default=None, max_length=50)
    scope: str = Field(default="mobile", max_length=50)
    is_active: bool = Field(default=True)
    sort_order: int = Field(default=0)
    icon: Optional[str] = None
    icon_key: Optional[str] = Field(default=None, max_length=100)
    icon_tone: Optional[str] = Field(default=None, max_length=30)
    description: Optional[str] = Field(default=None, max_length=100)
    print_format: Optional[Any] = None
    status: str = Field(default="draft", max_length=20)
    version: int = Field(default=1)
    permission_rules: Optional[Any] = None
    exception_rules: Optional[Any] = None
    auto_approval_rule: Optional[Any] = None


class ApprovalTypeCreate(ApprovalTypeBase):
    pass


class ApprovalTypeUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=50)
    business_code: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=50)
    category_key: Optional[str] = Field(None, max_length=50)
    scope: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    icon: Optional[str] = None
    icon_key: Optional[str] = Field(None, max_length=100)
    icon_tone: Optional[str] = Field(None, max_length=30)
    description: Optional[str] = Field(None, max_length=100)
    print_format: Optional[Any] = None
    status: Optional[str] = Field(None, max_length=20)
    version: Optional[int] = None
    permission_rules: Optional[Any] = None
    exception_rules: Optional[Any] = None
    auto_approval_rule: Optional[Any] = None


class ApprovalFormFieldBase(BaseModel):
    approval_type_id: int
    label: str = Field(..., max_length=100)
    code: str = Field(..., max_length=100)
    field_type: str = Field(..., max_length=50)
    is_required: bool = Field(default=False)
    default_value: Optional[Any] = None
    options_json: Optional[Any] = None
    display_condition: Optional[str] = None
    validation_rule: Optional[str] = None
    editable_scope: Optional[str] = Field(None, max_length=50)
    print_visible: bool = Field(default=True)
    placeholder: Optional[str] = Field(None, max_length=200)
    is_business_calculation: bool = Field(default=False)
    is_readonly: bool = Field(default=False)
    sort_order: int = Field(default=0)


class ApprovalFormFieldCreate(ApprovalFormFieldBase):
    pass


class ApprovalFormFieldUpdate(BaseModel):
    approval_type_id: Optional[int] = None
    label: Optional[str] = Field(None, max_length=100)
    code: Optional[str] = Field(None, max_length=100)
    field_type: Optional[str] = Field(None, max_length=50)
    is_required: Optional[bool] = None
    default_value: Optional[Any] = None
    options_json: Optional[Any] = None
    display_condition: Optional[str] = None
    validation_rule: Optional[str] = None
    editable_scope: Optional[str] = Field(None, max_length=50)
    print_visible: Optional[bool] = None
    placeholder: Optional[str] = Field(None, max_length=200)
    is_business_calculation: Optional[bool] = None
    is_readonly: Optional[bool] = None
    sort_order: Optional[int] = None


class ApprovalFormFieldOut(ApprovalFormFieldBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApprovalTypeOut(ApprovalTypeBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    form_fields: list["ApprovalFormFieldOut"] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ApprovalTemplateVersionOut(BaseModel):
    id: int
    approval_type_id: int
    version: int
    status: str = "published"
    published_at: datetime
    snapshot_json: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class ApprovalTemplateGroup(BaseModel):
    category: str
    templates: list[ApprovalTypeOut] = Field(default_factory=list)


class ApprovalTemplateConfigSave(BaseModel):
    id: Optional[int] = None
    name: str = Field(..., max_length=50)
    business_code: str = Field(..., max_length=100)
    category: str = Field(default="default", max_length=50)
    category_key: Optional[str] = Field(default=None, max_length=50)
    scope: str = Field(default="mobile", max_length=50)
    is_active: bool = Field(default=True)
    sort_order: int = Field(default=0)
    icon: Optional[str] = None
    icon_key: Optional[str] = Field(default=None, max_length=100)
    icon_tone: Optional[str] = Field(default=None, max_length=30)
    description: Optional[str] = Field(None, max_length=100)
    print_format: Optional[Any] = None
    status: Optional[str] = Field(default=None, max_length=20)
    permission_rules: Optional[Any] = None
    exception_rules: Optional[Any] = None
    auto_approval_rule: Optional[Any] = None
    fields: list[dict[str, Any]] = Field(default_factory=list)
    flow_nodes: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalTemplateCopyRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    business_code: Optional[str] = Field(None, max_length=100)


class ApprovalTemplateBulkDisableRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)


class ApprovalTemplateBulkDisableResult(BaseModel):
    updated: int = 0
    ids: list[int] = Field(default_factory=list)


class ApprovalFlowBase(BaseModel):
    """
    审批流程模板基础 Schema

    ApprovalFlow 定义了某个业务模块使用的审批流程规则，
    是审批引擎的"蓝图"。每个 module 可以配置多个流程（按条件匹配使用）。

    字段说明:
        name:        流程名称，例如 "3天以内请假审批"、"薪资调整审批"
        module:      所属业务模块，枚举:
                       leave（请假）/ overtime（加班）/ recruitment（招聘）/
                       payroll（薪资）/ salary_adjust（调薪）/ offboard（离职）
        description: 流程用途说明
        is_active:   是否启用，停用后新提交的审批不再匹配此流程
    """
    name: str = Field(..., max_length=100, description="流程名称")
    module: str = Field(
        ...,
        max_length=50,
        description="所属模块: leave/overtime/recruitment/payroll/salary_adjust/offboard",
    )  # 模块标识，与 ApprovalInstance.module 匹配
    description: Optional[str] = Field(None, description="流程描述")
    is_active: bool = Field(True, description="是否启用")  # False 时不再接受新实例


class ApprovalFlowCreate(ApprovalFlowBase):
    """
    创建审批流程模板的请求体。

    对应 API: POST /approvals/flows

    创建流程后，还需通过 /flows/{id}/nodes 添加审批节点。
    没有节点的流程不能正常运行（service 层校验）。
    """
    pass


class ApprovalFlowUpdate(BaseModel):
    """
    更新审批流程模板的请求体（PATCH 语义）。

    对应 API: PUT /approvals/flows/{id}

    注意：修改流程配置不影响已创建的历史实例（历史实例使用 flow_snapshot 快照）。
    """
    name: Optional[str] = Field(None, max_length=100)
    module: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None
    is_active: Optional[bool] = None  # False=停用，已运行的实例不受影响


class ApprovalFlowOut(ApprovalFlowBase):
    """
    返回给前端的审批流程数据（ORM 序列化）。

    字段说明:
        id:         流程模板主键
        created_at: 创建时间
    """
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ───────── ApprovalNode ─────────

class ApprovalNodeBase(BaseModel):
    """
    审批节点基础 Schema

    ApprovalNode 是审批流程中的单个审批环节，
    多个节点按 node_order 顺序构成完整的审批链。

    节点类型（node_type）:
        approval: 普通审批节点，审批人需主动操作（通过/拒绝）
        notify:   通知节点，仅向相关人员发送通知，自动通过，不阻塞流程
        auto:     自动节点，满足条件时自动通过（如金额低于阈值时跳过审批）

    审批人类型（approver_type）:
        direct_manager:    申请人的直属上级（自动从员工档案获取）
        project_manager:   申请人的项目经理（适用于驻场外包员工）
        hr:                HR 部门（匹配 role=hr 的用户）
        department_head:   部门负责人（从部门信息获取负责人）
        gm:                总经理（匹配最高级别管理员）
        specific_user:     指定具体人员（需填写 approver_id）

    字段说明:
        flow_id:             所属流程模板 ID
        node_order:          节点执行顺序（1=第一步，2=第二步，依此类推）
        approver_type:       审批人来源规则
        approver_id:         指定审批人的员工 ID（仅 approver_type=specific_user 时有效）
        auto_approve_hours:  超时自动通过时限（小时）；为 None 时不自动通过
        node_type:           节点类型（approval/notify/auto）
        condition_rules:     触发条件规则（JSON 字符串），为 None 时无条件执行
                             示例: '{"field": "days", "operator": ">", "value": 3}'
                             表示请假天数>3天时才执行此节点
    """
    flow_id: int = Field(..., description="所属流程ID")
    node_order: int = Field(..., ge=1, description="节点顺序")  # 从1开始，按顺序逐步推进
    approver_type: str = Field(
        ...,
        max_length=50,
        description="审批人类型: direct_manager/project_manager/hr/department_head/gm/specific_user",
    )
    approver_id: Optional[int] = Field(
        None, description="指定审批人ID（approver_type=specific_user时必填）"
    )  # 仅 specific_user 类型需要填写具体员工 ID
    auto_approve_hours: Optional[float] = Field(
        None, ge=0, description="超时自动通过时间（小时）"
    )  # 如 24.0 表示24小时未操作则自动通过，None=永不自动通过
    node_type: str = Field(default="approval", pattern=APPROVAL_NODE_TYPE_PATTERN)
    condition_rules: Optional[str] = Field(default=None, description="条件规则JSON字符串")
    # 条件规则 JSON，满足条件才执行此节点；不满足则跳过此节点继续推进


class ApprovalNodeCreate(ApprovalNodeBase):
    """
    创建审批节点的请求体。

    对应 API: POST /approvals/flows/{id}/nodes

    节点创建后即时生效，但不影响已运行的审批实例（历史实例使用快照）。
    """
    pass


class ApprovalNodeUpdate(BaseModel):
    """
    更新审批节点的请求体（PATCH 语义）。

    对应 API: PUT /approvals/flows/{id}/nodes/{node_id}
    """
    node_order: Optional[int] = Field(None, ge=1)
    approver_type: Optional[str] = Field(None, max_length=50)
    approver_id: Optional[int] = None
    auto_approve_hours: Optional[float] = Field(None, ge=0)
    node_type: Optional[str] = Field(None, pattern=APPROVAL_NODE_TYPE_PATTERN)
    condition_rules: Optional[str] = None


class ApprovalNodeOut(ApprovalNodeBase):
    """
    返回给前端的审批节点数据（ORM 序列化）。

    字段说明:
        id:         节点数据库主键
        created_at: 节点创建时间
    """
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ApprovalFlowWithNodes(ApprovalFlowOut):
    """
    包含节点列表的审批流程详情（嵌套结构）。

    对应 API: GET /approvals/flows/{id} 的响应

    在 ApprovalFlowOut 基础上，附带该流程所有节点的详细信息。
    nodes 列表按 node_order 升序排列。

    字段说明:
        nodes: 该流程的所有审批节点，按顺序排列
    """
    nodes: list[ApprovalNodeOut] = []  # 流程节点列表，按 node_order 升序


class ApprovalFlowNodePreview(BaseModel):
    node_order: int
    node_name: str
    node_type: str
    node_type_label: str
    approver_type: Optional[str] = None
    approver_id: Optional[int] = None
    approver_name: Optional[str] = None
    approver_display: str
    assignee_source: Optional[str] = None
    approval_mode: Optional[str] = None
    allow_applicant_select: bool = False
    include_applicant_self: bool = False
    applicant_select_mode: Optional[str] = None
    applicant_select_scope: Optional[str] = None
    applicant_select_member_ids: list[int] = Field(default_factory=list)
    applicant_select_roles: list[str] = Field(default_factory=list)
    requires_applicant_select: bool = False
    is_resolved: bool = False
    resolve_message: Optional[str] = None
    status: str = "preview"
    status_label: str = "待提交"
    action: Optional[str] = None
    comment: Optional[str] = None
    acted_at: Optional[datetime] = None
    arrived_at: Optional[datetime] = None
    approver_department_name: Optional[str] = None


class ApprovalFlowPreviewOut(BaseModel):
    approval_type_id: int
    flow_id: Optional[int] = None
    flow_name: Optional[str] = None
    has_flow: bool
    message: str
    nodes: list[ApprovalFlowNodePreview] = Field(default_factory=list)


class ApprovalFlowPreviewRequest(BaseModel):
    form_data: dict[str, Any] = Field(default_factory=dict, description="用于当前申请表单条件分支预览的上下文")


class ApprovalDraftApplicantPreviewRequest(BaseModel):
    department_id: Optional[int] = Field(default=None, description="草稿员工所属部门ID")
    direct_manager_id: Optional[int] = Field(default=None, description="草稿员工直属上级ID")
    role_names: list[str] = Field(default_factory=list, description="草稿员工入职后角色")
    form_data: dict[str, Any] = Field(default_factory=dict, description="用于条件分支预览的表单上下文")


# ───────── ApprovalInstance ─────────

class ApprovalInstanceBase(BaseModel):
    """
    审批实例基础 Schema

    ApprovalInstance 是审批流程的一次执行记录，
    每次业务发起审批时创建一个实例，关联到具体的业务数据。

    "通用引擎"的关键设计：
        通过 business_type + business_id 组合，引擎可以管理任意业务的审批，
        无需修改引擎代码。业务模块只需在适当时机创建实例，
        审批完成后引擎回调通知业务模块更新状态。

    字段说明:
        module:        业务所属大模块（与 ApprovalFlow.module 匹配，用于查找对应流程）
                       例如: "leave"、"payroll"、"recruitment"
        business_id:   业务记录的主键 ID（如请假申请的 ID）
        business_type: 业务类型（比 module 更细粒度的分类）
                       例如: module="leave", business_type="annual_leave" 表示年假申请
    """
    module: str = Field(..., max_length=50, description="业务模块")
    business_id: int = Field(..., description="业务记录ID")
    business_type: str = Field(..., max_length=50, description="业务类型")
    summary: Optional[str] = None
    form_data: Optional[dict[str, Any]] = None


class ApprovalInstanceCreate(ApprovalInstanceBase):
    """
    发起审批请求体（业务模块调用审批引擎时使用）。

    对应 API: POST /approvals/instances

    applicant_id（申请人）由服务端从 JWT token 中自动获取当前登录用户，
    不需要客户端传入，防止伪造申请人身份。

    service 层会根据 module 自动查找匹配的 ApprovalFlow，
    并拍摄 flow_snapshot（流程快照）存入实例记录，
    确保后续修改流程模板不影响当前运行中的实例。
    """
    pass  # applicant_id 通过 token 自动获取，无需手动传入


class ApprovalApplicationSubmit(BaseModel):
    approval_type_id: int
    form_data: dict[str, Any] = Field(default_factory=dict)
    summary: Optional[str] = None


class ApprovalInstanceOut(ApprovalInstanceBase):
    """
    返回给前端的审批实例数据（ORM 序列化）。

    字段说明:
        id:                   实例主键
        flow_id:              匹配使用的审批流程模板 ID
        applicant_id:         申请人员工 ID
        flow_snapshot:        流程快照（JSON 字符串）
                              记录了实例创建时的完整流程+节点信息，
                              保证历史审批记录不受模板修改影响，可追溯
        current_node_order:   当前正在等待审批的节点编号
                              （等于最后已完成节点编号+1；全部通过时超出最大节点数）
        status:               实例状态: pending（审批中）/ approved（已通过）/
                              rejected（已拒绝）/ cancelled（已撤回）
        created_at:           发起时间
        updated_at:           最后更新时间
    """
    id: int
    template_version_id: Optional[int] = None
    flow_id: int              # 关联的审批流程模板 ID
    applicant_id: int         # 申请人员工 ID
    applicant_name: Optional[str] = None
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    company_id: Optional[int] = None
    company_name: Optional[str] = None
    approval_type_name: Optional[str] = None
    form_field_labels: dict[str, str] = Field(default_factory=dict)
    form_field_types: dict[str, str] = Field(default_factory=dict)
    form_field_display_values: dict[str, str] = Field(default_factory=dict)
    flow_snapshot: Optional[str] = None   # 流程配置快照（JSON），防止回溯时受模板变更影响
    current_node_order: int               # 当前待审批节点编号
    current_node_name: Optional[str] = None
    current_node_status: Optional[str] = None
    current_node_status_label: Optional[str] = None
    status: str               # pending | approved | rejected | cancelled
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApprovalInstanceDetail(ApprovalInstanceOut):
    """
    审批实例详情（含完整审批历史记录）。

    对应 API: GET /approvals/instances/{id} 的响应

    在 ApprovalInstanceOut 基础上，附带所有历史审批操作记录，
    前端可以展示完整的审批流水（谁在什么时间做了什么操作）。

    字段说明:
        records: 按时间顺序排列的审批操作记录列表
    """
    records: list["ApprovalRecordOut"] = []  # 历史审批操作记录，按时间正序排列
    progress_nodes: list[ApprovalFlowNodePreview] = Field(default_factory=list)


class ApprovalReminderResult(BaseModel):
    instance_id: int
    node_order: int
    notified_count: int
    notified_approver_ids: list[int] = Field(default_factory=list)
    message: str = "催办提醒已发送"


# ───────── ApprovalRecord ─────────

class ApprovalRecordBase(BaseModel):
    """
    审批操作基础 Schema

    ApprovalRecord 是某个审批人在某个节点上的单次操作记录。
    每次调用审批接口都会生成一条记录（approve/reject/transfer）。

    字段说明:
        action:  操作类型，枚举:
                   approve  = 通过（推进到下一节点或完成审批）
                   reject   = 拒绝（实例状态变为 rejected，流程终止）
                   transfer = 转审（将当前节点转给另一个审批人）
        comment: 审批意见/备注（通过时可填写"同意"，拒绝时必须填写原因）
    """
    action: str = Field(
        ..., max_length=20, description="操作: approve/reject/transfer"
    )  # approve=通过, reject=拒绝, transfer=转审（将任务转给他人）
    comment: Optional[str] = Field(None, description="审批意见")


class ApprovalActionRequest(ApprovalRecordBase):
    """
    审批人执行审批操作的请求体。

    对应 API: POST /approvals/instances/{id}/action

    审批人通过此接口执行：通过（approve）、拒绝（reject）或转审（transfer）。

    字段说明:
        action:          操作类型（继承自 ApprovalRecordBase）
        comment:         审批意见
        transfer_to_id:  转审目标人员工 ID，仅当 action=transfer 时必填。
                         转审后，当前节点的审批人变更为目标人，
                         原审批人不再需要操作此节点。
    """
    transfer_to_id: Optional[int] = Field(
        None, description="转审目标人ID（action=transfer时必填）"
    )  # action=transfer 时必须指定转审对象，否则 service 层报错


class ApprovalRecordOut(BaseModel):
    """
    返回给前端的审批操作记录（ORM 序列化）。

    通常作为 ApprovalInstanceDetail.records 列表的元素，
    展示审批实例的完整操作历史。

    字段说明:
        id:           记录主键
        instance_id:  所属审批实例 ID
        node_order:   操作发生在哪个节点（对应 ApprovalNode.node_order）
        approver_id:  执行操作的审批人员工 ID
        action:       执行的操作（approve/reject/transfer）
        comment:      审批意见
        acted_at:     审批操作时间（精确到秒）
        created_at:   记录创建时间
    """
    id: int
    instance_id: int    # 所属实例 ID
    node_order: int     # 操作所在节点编号
    branch_key: Optional[str] = None
    branch_label: Optional[str] = None
    branch_node_order: Optional[int] = None
    approver_id: int    # 审批人员工 ID
    action: str         # approve | reject | transfer
    comment: Optional[str]
    acted_at: datetime  # 实际审批时间（可能与 created_at 不同，用于记录延时场景）
    created_at: datetime

    model_config = {"from_attributes": True}


# ───────── Pagination ─────────

class ApprovalTaskOut(BaseModel):
    id: int
    instance_id: int
    node_order: int
    branch_key: Optional[str] = None
    branch_label: Optional[str] = None
    branch_node_order: Optional[int] = None
    approver_id: Optional[int] = None
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ApprovalInstancePage(BaseModel):
    """
    审批实例分页响应。

    对应 API: GET /approvals/instances 的响应

    字段说明:
        total: 符合条件的实例总数（用于前端计算总页数）
        items: 当前页的实例列表
    """
    total: int                        # 符合条件的总记录数
    items: list[ApprovalInstanceOut]  # 当前页数据


class ApprovalCenterBucket(BaseModel):
    """移动端审批中心单个分类的数据桶。"""
    total: int
    items: list[ApprovalInstanceOut] = Field(default_factory=list)


class ApprovalMobileCenterOut(BaseModel):
    """移动端审批中心聚合响应。"""
    pending: ApprovalCenterBucket
    processed: ApprovalCenterBucket
    submitted: ApprovalCenterBucket
    cc: ApprovalCenterBucket
    counts: dict[str, int] = Field(default_factory=dict)
    submitted_status_counts: dict[str, int] = Field(default_factory=dict)


# ───────── Bulk Action ─────────

class BulkApprovalRequest(BaseModel):
    """
    批量审批请求体（一次性批量通过或拒绝多个实例）。

    对应 API: POST /approvals/bulk-action

    适用场景：HR 或管理层一键批量处理积压的审批任务。
    注意：批量操作只支持 approve 和 reject，不支持 transfer（转审）。

    字段说明:
        instance_ids: 要批量操作的审批实例 ID 列表
        action:       操作类型，"approve"=批量通过，"reject"=批量拒绝
        comment:      统一的审批意见（适用于所有被操作的实例）
    """
    instance_ids: List[int]               # 批量操作的实例 ID 列表
    action: str                           # "approve" 或 "reject"
    comment: Optional[str] = None         # 统一审批意见


class BulkApprovalResult(BaseModel):
    """
    批量审批操作结果。

    字段说明:
        success: 成功处理的实例数
        failed:  失败的实例数（如实例已完成、无权限等）
        errors:  失败详情列表（每条描述一个失败的原因）
    """
    success: int          # 成功处理数
    failed: int           # 失败数
    errors: List[str]     # 失败详情（如 "实例 #42 已完成，无法重复审批"）


# ───────── ApprovalDelegate ─────────

class ApprovalDelegateCreate(BaseModel):
    """
    创建审批委托的请求体（授权他人代为审批）。

    对应 API: POST /approvals/delegates

    当审批人需要出差/休假时，可委托同事在指定时间段内代为处理审批任务。
    委托生效期间，委托人的待审批任务会转给受托人处理。

    字段说明:
        delegate_id: 受托人（代理审批人）的员工 ID
        start_date:  委托生效开始日期（含）
        end_date:    委托结束日期（含）
        reason:      委托原因（如 "出差"、"年假"）
    """
    delegate_id: int       # 受托人员工 ID（代为处理审批的人）
    start_date: date       # 委托生效日期
    end_date: date         # 委托结束日期（委托人在此日期后恢复审批权）
    reason: Optional[str] = None  # 委托原因说明


class ApprovalDelegateOut(BaseModel):
    """
    返回给前端的审批委托数据（ORM 序列化）。

    字段说明:
        id:             委托记录主键
        delegator_id:   委托人员工 ID（授权他人代审的人）
        delegator_name: 委托人姓名（service 层 JOIN 注入）
        delegate_id:    受托人员工 ID
        delegate_name:  受托人姓名（service 层 JOIN 注入）
        start_date:     委托开始日期
        end_date:       委托结束日期
        is_active:      当前是否处于委托有效期内（service 计算）
        reason:         委托原因
    """
    id: int
    delegator_id: int                        # 委托人 ID
    delegator_name: Optional[str] = None     # 委托人姓名（JOIN 注入）
    delegate_id: int                         # 受托人 ID
    delegate_name: Optional[str] = None      # 受托人姓名（JOIN 注入）
    start_date: date
    end_date: date
    is_active: bool     # True = 当前委托有效（当前日期在 start_date~end_date 之间）
    reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ───────── Transfer ─────────

class TransferApprovalRequest(BaseModel):
    """
    审批转移请求体（将某实例的审批权转给另一人）。

    对应 API: POST /approvals/transfer

    与 ApprovalActionRequest（action=transfer）不同，
    此接口由管理员主动操作，可将任意实例的当前节点审批人变更，
    通常用于紧急处理卡在某节点无法推进的审批。

    字段说明:
        instance_id:     要转移的审批实例 ID
        new_approver_id: 新的审批人员工 ID
        reason:          转移原因（必填，用于审计追踪）
    """
    instance_id: int         # 要转移审批权的实例 ID
    new_approver_id: int     # 新审批人员工 ID
    reason: str              # 转移原因（必填，便于审计追踪）
