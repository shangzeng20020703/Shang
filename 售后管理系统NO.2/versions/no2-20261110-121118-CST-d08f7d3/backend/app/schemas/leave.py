"""
模块名称: schemas/leave.py
模块作用: 请假模块（Leave Domain）的所有 Pydantic v2 数据传输对象（DTO）定义

功能覆盖:
  - 假期类型管理: LeaveTypeBase / LeaveTypeCreate / LeaveTypeUpdate / LeaveTypeOut
  - 假期余额查询: LeaveBalanceOut / LeaveBalanceSummary / LeaveBalanceAdjust
  - 年假初始化: AnnualLeaveInitRequest
  - 请假申请: LeaveRequestBase / LeaveRequestCreate / LeaveRequestOut / LeaveRequestQuery
  - 请假审批: LeaveApprovalRequest
  - 批量余额操作: BulkInitBalanceRequest / BulkInitResult / BalanceAdjustRequest
  - 日历视图: CalendarLeaveItem
  - 通用分页: PaginatedResponse

依赖关系:
  - 依赖 app/models/leave.py 中的枚举类型:
    * ApprovalStatus: 审批状态枚举（pending/approved/rejected/cancelled）
    * DeductFrom: 余额扣减来源枚举（指定从哪类假期余额扣减）
    * HalfDay: 半天枚举（am=上午, pm=下午）
    * PolicyRegion: 政策适用地区枚举（all=全国通用）
  - 被 app/api/v1/endpoints/leave.py 所有路由使用
  - 被 app/services/leave_service.py 作为输入/输出类型使用

数据流向:
  HTTP Request Body → *Create/*Update (Pydantic 验证) → Service Layer (ORM)
  ORM Model → *Out (ConfigDict from_attributes=True) → HTTP Response JSON

业务说明:
  - 假期类型（LeaveType）是系统配置，由 HR 管理员维护
  - 假期余额（LeaveBalance）按员工+假期类型+年份维度存储
  - 请假申请（LeaveRequest）提交后需经审批，审批通过后才扣减余额
  - 年假按工龄自动计算，其他类型假期可手动初始化或批量初始化
  - LeaveBalanceSummary 是 HR 管理视图，聚合展示员工各类假期的使用情况
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict

from app.models.leave import ApprovalStatus, DeductFrom, HalfDay, PolicyRegion


# ===================================================================
# LeaveType — 假期类型配置
# ===================================================================

class LeaveTypeBase(BaseModel):
    """
    用途: 假期类型写操作 Schema 的基类
    业务: 定义一种假期的规则配置，是请假申请的基础数据
    字段说明:
      - name: 假期名称，如"年假"、"病假"、"事假"、"婚假"、"产假"、"陪产假"、"调休"
      - code: 假期编码，系统内部标识，如"annual"、"sick"、"personal"、"comp"
      - is_paid: 是否带薪假期（False=无薪，影响薪资计算）
      - max_days_per_year: 每年最大可休天数（None=不限制，如病假）
      - requires_proof: 是否需要证明材料（如病假需病历，婚假需结婚证）
      - deduct_from: 余额扣减来源（见 DeductFrom 枚举，部分假期可从其他类型扣减）
      - policy_region: 政策适用地区（部分假期如陪产假各地政策不同）
      - is_active: False 时该假期类型在前端不可选，但历史记录保留
    """
    name: str = Field(..., max_length=50, description="假期名称")
    code: str = Field(..., max_length=30, description="假期编码")
    is_paid: bool = True                                  # 是否带薪，默认带薪
    max_days_per_year: Optional[Decimal] = Field(None, ge=0)  # 年度上限（None=不限）
    requires_proof: bool = False                          # 是否需要凭证材料
    deduct_from: DeductFrom = DeductFrom.none             # 余额扣减来源
    policy_region: PolicyRegion = PolicyRegion.all        # 政策适用地区
    is_active: bool = True
    description: Optional[str] = Field(None, max_length=2000)
    en_name: Optional[str] = Field(None, max_length=80)
    sort_order: int = Field(99, ge=0, le=999)
    leave_unit: str = Field("day", pattern="^(day|half_day|hour)$")
    time_calc: str = Field("natural_day", pattern="^(natural_day|workday)$")
    hours_per_day: Decimal = Field(Decimal("8.0"), gt=0, le=24)
    rounding_direction: str = Field("none", pattern="^(none|up|down)$")
    rounding_unit: str = Field("none", pattern="^(none|half_hour|hour|half_day|day)$")
    quota_limited: bool = True
    quota_rule_type: str = Field("fixed", max_length=30)
    quota_rule: Optional[Dict[str, Any]] = None
    issuance_rule: str = Field("year_start", max_length=40)
    issuance_month_day: str = Field("01-01", max_length=20)
    validity_type: str = Field("natural_year", max_length=40)
    expire_month_day: Optional[str] = Field(None, max_length=20)
    allow_validity_extension: bool = False
    extension_value: int = Field(0, ge=0, le=120)
    extension_unit: str = Field("day", pattern="^(day|month|year)$")
    expiration_reminder_enabled: bool = False
    reminder_before_value: int = Field(1, ge=1, le=120)
    reminder_before_unit: str = Field("month", pattern="^(day|month)$")
    reminder_notify_employee: bool = True
    reminder_notify_manager: bool = False
    new_employee_rule: str = Field("none", pattern="^(none|after_regularization|after_months)$")
    new_employee_limit_enabled: bool = False
    new_employee_limit_months: int = Field(0, ge=0, le=120)
    scope_type: str = Field("all", max_length=30)
    scope_rules: Optional[Dict[str, Any]] = None
    carry_over_enabled: bool = False
    max_carry_over_days: Decimal = Field(Decimal("0"), ge=0)
    approval_flow: Optional[str] = Field(None, max_length=255)
    leave_color: str = Field("#52c41a", max_length=20)


class LeaveTypeCreate(LeaveTypeBase):
    """
    用途: POST /api/v1/leave-types 新建假期类型的请求体
    业务: 直接继承 LeaveTypeBase，无额外字段
    """
    pass


class LeaveTypeUpdate(BaseModel):
    """
    用途: PATCH /api/v1/leave-types/{id} 更新假期类型的请求体
    业务: 所有字段均为可选，只传需要修改的字段
    注意: code 字段不允许修改（不在此 Schema 中），避免影响历史数据引用
    """
    name: Optional[str] = Field(None, max_length=50)
    is_paid: Optional[bool] = None
    max_days_per_year: Optional[Decimal] = Field(None, ge=0)
    requires_proof: Optional[bool] = None
    deduct_from: Optional[DeductFrom] = None
    policy_region: Optional[PolicyRegion] = None
    is_active: Optional[bool] = None
    description: Optional[str] = Field(None, max_length=2000)
    en_name: Optional[str] = Field(None, max_length=80)
    sort_order: Optional[int] = Field(None, ge=0, le=999)
    leave_unit: Optional[str] = Field(None, pattern="^(day|half_day|hour)$")
    time_calc: Optional[str] = Field(None, pattern="^(natural_day|workday)$")
    hours_per_day: Optional[Decimal] = Field(None, gt=0, le=24)
    rounding_direction: Optional[str] = Field(None, pattern="^(none|up|down)$")
    rounding_unit: Optional[str] = Field(None, pattern="^(none|half_hour|hour|half_day|day)$")
    quota_limited: Optional[bool] = None
    quota_rule_type: Optional[str] = Field(None, max_length=30)
    quota_rule: Optional[Dict[str, Any]] = None
    issuance_rule: Optional[str] = Field(None, max_length=40)
    issuance_month_day: Optional[str] = Field(None, max_length=20)
    validity_type: Optional[str] = Field(None, max_length=40)
    expire_month_day: Optional[str] = Field(None, max_length=20)
    allow_validity_extension: Optional[bool] = None
    extension_value: Optional[int] = Field(None, ge=0, le=120)
    extension_unit: Optional[str] = Field(None, pattern="^(day|month|year)$")
    expiration_reminder_enabled: Optional[bool] = None
    reminder_before_value: Optional[int] = Field(None, ge=1, le=120)
    reminder_before_unit: Optional[str] = Field(None, pattern="^(day|month)$")
    reminder_notify_employee: Optional[bool] = None
    reminder_notify_manager: Optional[bool] = None
    new_employee_rule: Optional[str] = Field(None, pattern="^(none|after_regularization|after_months)$")
    new_employee_limit_enabled: Optional[bool] = None
    new_employee_limit_months: Optional[int] = Field(None, ge=0, le=120)
    scope_type: Optional[str] = Field(None, max_length=30)
    scope_rules: Optional[Dict[str, Any]] = None
    carry_over_enabled: Optional[bool] = None
    max_carry_over_days: Optional[Decimal] = Field(None, ge=0)
    approval_flow: Optional[str] = Field(None, max_length=255)
    leave_color: Optional[str] = Field(None, max_length=20)


class LeaveTypeOut(LeaveTypeBase):
    """
    用途: 假期类型列表/详情接口的响应体，从 ORM LeaveType 模型序列化
    业务: 继承 LeaveTypeBase 所有字段，增加数据库自增 id
    ConfigDict from_attributes=True: 允许从 SQLAlchemy ORM 实例直接构造
    """
    model_config = ConfigDict(from_attributes=True)
    id: int   # 数据库自增主键


# ===================================================================
# LeaveBalance — 假期余额
# ===================================================================

class LeaveBalanceOut(BaseModel):
    """
    用途: GET /api/v1/leave-balances/{employee_id} 员工假期余额查询的响应体
    业务: 描述某员工在某年度某类假期的余额情况
    字段数学关系:
      remaining_days = total_days - used_days - expired_days
    字段说明:
      - total_days: 该年度该假期类型的总额度（含年假工龄奖励等）
      - used_days: 已审批通过并休假的天数
      - remaining_days: 当前可用余额（用于校验申请是否超额）
      - expired_days: 已过期失效的天数（如年假跨年未休的部分）
      - expiry_date: 余额失效日期（过了这天未休的余额自动作废）
    ConfigDict from_attributes=True: 允许从 ORM LeaveBalance 直接构造
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    leave_type_id: int
    year: int
    total_days: Decimal           # 总额度
    used_days: Decimal            # 已使用天数
    remaining_days: Decimal       # 剩余可用天数（= total - used - expired）
    expired_days: Decimal         # 已过期失效天数
    expiry_date: Optional[date] = None  # 余额到期日，None 表示永不过期
    updated_at: datetime


class LeaveBalanceSummary(BaseModel):
    """
    用途: GET /api/v1/leave-balances/summary HR管理视图中的员工假期余额汇总
    业务: 将一个员工的多条 LeaveBalance 记录聚合为单行，便于 HR 一览
    字段说明:
      - annual_*: 年假相关余额统计（total=总额/used=已用/remaining=剩余）
      - sick_*: 病假相关余额统计
      - personal_used: 事假已使用天数
      - comp_used: 调休已使用天数
      - marriage_remaining: 婚假剩余天数（Optional，如无该假期类型则为 None）
      - maternity_remaining: 产假/陪产假剩余天数
    注意: 此 Schema 不从 ORM 直接映射，由 Service 层聚合计算后填充
    """
    employee_id: int
    employee_name: Optional[str] = None    # 由 Service 层关联查询注入
    employee_no: Optional[str] = None      # 由 Service 层关联查询注入
    gender: Optional[str] = None
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    hire_date: Optional[date] = None
    first_work_date: Optional[date] = None
    probation_end_date: Optional[date] = None
    photo_url: Optional[str] = None
    status: Optional[str] = None
    employment_type: Optional[str] = None
    company: Optional[str] = None
    position: Optional[str] = None
    job_level: Optional[str] = None
    annual_total: float = 0               # 年假总额度（天）
    annual_used: float = 0                # 年假已用天数
    annual_remaining: float = 0           # 年假剩余天数
    sick_total: float = 0                 # 病假总额度
    sick_used: float = 0                  # 病假已用天数
    sick_remaining: float = 0             # 病假剩余天数
    personal_total: float = 0
    personal_used: float = 0              # 事假已用天数（通常无上限）
    personal_remaining: float = 0
    comp_total: float = 0
    comp_used: float = 0                  # 调休已用天数
    comp_remaining: float = 0
    marriage_total: Optional[float] = None
    marriage_remaining: Optional[float] = None    # 婚假剩余（None=无此假期）
    maternity_total: Optional[float] = None
    maternity_remaining: Optional[float] = None   # 产假/陪产假剩余
    updated_at: Optional[datetime] = None
    leave_balances: List[Dict[str, Any]] = Field(default_factory=list)


class LeaveBalanceOptionOut(BaseModel):
    """请假审批发起页使用的假期类型选择项，已合并当前员工余额。"""

    leave_type_id: int
    leave_type_code: Optional[str] = None
    leave_type_name: str
    leave_unit: str = "day"
    time_calc: str = "natural_day"
    hours_per_day: float = 8
    rounding_direction: str = "none"
    rounding_unit: str = "none"
    quota_limited: bool = True
    requires_proof: bool = False
    total_days: float = 0
    used_days: float = 0
    remaining_days: float = 0
    remaining_text: str = ""
    balance_text: str = ""
    option_label: str
    is_available: bool = True
    sort_order: int = 99
    reason: Optional[str] = None


class LeaveEmployeeDateUpdate(BaseModel):
    """假期余额页补录员工关键日期，字段直接同步员工花名册。"""
    hire_date: Optional[date] = None
    first_work_date: Optional[date] = None
    probation_end_date: Optional[date] = None


class LeaveBalanceAdjust(BaseModel):
    """
    用途: POST /api/v1/leave-balances/adjust HR手动调整员工假期余额的请求体
    业务: 用于特殊场景下的余额补录或扣减，如：
      - 补录历史年假（adjustment_days > 0）
      - 扣减错误录入的余额（adjustment_days < 0）
    reason 字段要求至少1个字符，防止无意义调整
    """
    employee_id: int
    leave_type_id: int
    year: int
    adjustment_days: Decimal = Field(..., description="正数增加, 负数减少")
    reason: str = Field(..., min_length=1, max_length=200)  # 调整原因（必填）


class AnnualLeaveInitRequest(BaseModel):
    """
    用途: POST /api/v1/leave-balances/init-annual 年假初始化接口的请求体
    业务: 按工龄规则自动计算并初始化指定年度的年假余额
          法定规则: 1年以下=0天，1-10年=5天，10-20年=10天，20年以上=15天（规则在 Service 层定义）
    字段说明:
      - year: 初始化的年度，范围 2020-2100
      - employee_ids: 指定员工列表（None=全员初始化），适用于新入职员工补录
    """
    year: int = Field(..., ge=2020, le=2100)
    employee_ids: Optional[List[int]] = Field(None, description="指定员工, 为空则全员")


# ===================================================================
# LeaveRequest — 请假申请
# ===================================================================

class LeaveRequestBase(BaseModel):
    """
    用途: 请假申请写操作 Schema 的基类
    业务: 描述一次请假申请的核心信息
    字段说明:
      - leave_type_id: 假期类型的外键 ID
      - start_date / end_date: 请假起止日期（含首尾两天）
      - start_half: 开始当天的时段（am=上午开始, pm=下午开始）
        例如下午请假则当天只算0.5天
      - end_half: 结束当天的时段（am=上午结束, pm=下午结束）
        例如上午请假则当天只算0.5天
      - reason: 请假原因（可选，部分假期类型强制填写由业务层校验）
      - proof_url: 证明材料文件 URL（requires_proof=True 的假期类型时需要）
    实际请假天数（days）在 LeaveRequestOut 中体现，由 Service 层计算
    """
    leave_type_id: int
    start_date: date
    end_date: date
    start_half: HalfDay = HalfDay.am    # 开始时段：am=全天开始 / pm=下午开始（当天算0.5天）
    end_half: HalfDay = HalfDay.pm      # 结束时段：pm=全天结束 / am=上午结束（当天算0.5天）
    reason: Optional[str] = Field(None, max_length=500)
    proof_url: Optional[str] = Field(None, max_length=500)  # 证明材料（如病历、结婚证等）


class LeaveRequestCreate(LeaveRequestBase):
    """
    用途: POST /api/v1/leave-requests 提交请假申请的请求体
    业务: 直接继承 LeaveRequestBase，无额外字段
    注意: employee_id 不在此 Schema 中，从 JWT Token 中解析当前用户
    """
    pass


class LeaveRequestOut(BaseModel):
    """
    用途: 请假申请详情/列表接口的响应体，从 ORM LeaveRequest 模型序列化
    业务: 包含申请的完整信息，含审批状态和审批结果
    字段说明:
      - days: 实际请假天数（由 Service 层根据日历工作日计算，含半天处理）
      - approval_status: 当前审批状态（pending=待审批/approved=已批准/rejected=已拒绝/cancelled=已撤销）
      - approver_id: 审批人的员工 ID（待审批时为 None）
      - approved_at: 审批完成时间（待审批时为 None）
    ConfigDict from_attributes=True: 允许从 ORM LeaveRequest 直接构造
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    leave_type_id: int
    start_date: date
    end_date: date
    start_half: HalfDay
    end_half: HalfDay
    days: Decimal                          # 实际请假天数（含半天折算）
    reason: Optional[str] = None
    proof_url: Optional[str] = None
    approval_status: ApprovalStatus        # 审批状态枚举
    approver_id: Optional[int] = None      # 审批人 ID（通过后填入）
    approved_at: Optional[datetime] = None # 审批完成时间
    created_at: datetime
    leave_type_name: Optional[str] = None
    approval_instance_id: Optional[int] = None
    approval_instance_status: Optional[str] = None
    approval_records: List[dict] = Field(default_factory=list)


class LeaveRequestQuery(BaseModel):
    """
    用途: GET /api/v1/leave-requests 请假记录查询的过滤参数（Query String）
    业务: 支持多维度筛选，所有参数均可选（不传则不过滤该维度）
    常见使用场景:
      - 员工自查: employee_id=<自己的ID>
      - HR 查所有待审批: approval_status=pending
      - 按日期范围查询: start_date + end_date
    分页参数: page 和 page_size，结果封装在 PaginatedResponse 中返回
    """
    employee_id: Optional[int] = None
    leave_type_id: Optional[int] = None
    department_id: Optional[int] = None
    keyword: Optional[str] = None
    approval_status: Optional[ApprovalStatus] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    overdue_only: bool = False
    page: int = Field(1, ge=1)                        # 当前页码，从1开始
    page_size: int = Field(20, ge=1, le=1000)         # 每页数量，最大1000


class LeaveApprovalRequest(BaseModel):
    """
    用途: POST /api/v1/leave-requests/{id}/approve 请假审批操作的请求体
    业务: HR 或上级审批人对一条请假申请进行审批
    字段说明:
      - approved: True=批准, False=拒绝
      - note: 审批备注（拒绝时建议填写拒绝原因，最大500字）
    注意: 审批人 ID 从 JWT Token 中解析，不需要在请求体中传入
    """
    approved: bool                                     # True=批准, False=拒绝
    note: Optional[str] = Field(None, max_length=500)  # 审批备注


class LeaveBatchApprovalRequest(BaseModel):
    request_ids: List[int] = Field(..., min_length=1, max_length=200)
    approved: bool
    note: Optional[str] = Field(None, max_length=500)


class LeaveStatsOut(BaseModel):
    pending: int
    pending_delta: int
    month_leave_count: int
    month_leave_mom_pct: float
    avg_annual_remaining: float
    avg_annual_yoy_delta: float
    overdue_pending: int
    type_distribution: List[dict]


# ===================================================================
# 批量初始化 & 手动调整
# ===================================================================

class BulkInitBalanceRequest(BaseModel):
    """
    用途: POST /api/v1/leave-balances/bulk-init 批量初始化假期余额的请求体
    业务: HR 在每年年初执行，为所有在职员工初始化当年各类假期余额
    字段说明:
      - year: 初始化的年度
      - employee_ids: 指定员工列表（None=全员初始化）
      - reset_existing: True=重置已有余额（谨慎使用），False=跳过已初始化的员工
    注意: reset_existing=True 会覆盖员工已有余额，建议在年初未使用任何假期前执行
    """
    year: int = Field(..., ge=2020, le=2100)
    employee_ids: Optional[List[int]] = Field(None, description="None表示全员")
    reset_existing: bool = Field(False, description="是否重置已有余额")


class BulkInitResult(BaseModel):
    """
    用途: 批量初始化假期余额接口的响应体
    业务: 汇总本次批量操作的成功/跳过/错误数量
    字段说明:
      - success: 成功初始化的员工数
      - skipped: 因已有记录且 reset_existing=False 而跳过的数量
      - errors: 初始化失败的错误信息列表（每条为字符串描述）
    """
    success: int           # 成功初始化数量
    skipped: int           # 跳过数量（余额已存在）
    errors: List[str]      # 错误描述列表（如"员工ID 42 初始化失败：..."）


class BalanceAdjustRequest(BaseModel):
    """
    用途: POST /api/v1/leave-balances/adjust 手动调整假期余额的请求体
    业务: 与 LeaveBalanceAdjust 功能相同，但 adjustment 为 float 类型（兼容旧接口）
    正数增加余额，负数减少余额
    reason 字段为必填（至少1个字符），记录调整原因备查
    """
    employee_id: int
    leave_type_id: int
    adjustment: float = Field(..., description="正数增加，负数减少")
    reason: str = Field(..., min_length=1, max_length=200)  # 调整原因（审计留存）


# ===================================================================
# 日历视图
# ===================================================================

class CalendarLeaveItem(BaseModel):
    """
    用途: GET /api/v1/leave-requests/calendar 日历视图中的单条请假记录
    业务: 在日历视图中，展示某天某员工的请假信息
    使用场景: 部门考勤日历、HR 看板中的请假排期图
    days 字段: 该员工在该日历区间内的请假天数（用于标注强度）
    """
    employee_id: int
    employee_name: str    # 展示用姓名（由 Service 层关联查询注入）
    leave_type: str       # 假期类型名称，如"年假"、"病假"
    days: float           # 请假天数（含半天折算，如 0.5 = 半天）


# ===================================================================
# Common pagination
# ===================================================================

class PaginatedResponse(BaseModel):
    """
    用途: 所有分页接口的通用响应包装体
    业务: 统一分页响应结构，前端据此计算总页数
    items 字段使用 list（而非 List[T]），实际内容类型由调用方动态确定
    注意: 若需要类型安全，应在 API 层用具体类型覆盖 items 字段
          或使用 Generic 泛型版本，当前简化处理以减少代码复杂度
    """
    total: int        # 符合查询条件的总记录数
    page: int         # 当前页码（从1开始）
    page_size: int    # 每页记录数
    items: list       # 当前页数据（具体类型由调用方确定）
