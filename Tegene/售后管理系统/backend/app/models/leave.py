"""
请假模块数据模型
================
文件路径: app/models/leave.py

表结构总览
----------
- LeaveType       假期类型定义（年假/病假/事假/婚假/产假/调休等）
- LeaveBalance    员工年度假期余额账户（总天数/已用天数/剩余天数）
- LeaveRequest    请假申请（含半天机制、审批状态）

业务说明
--------
本公司的请假体系遵循《劳动法》及各地人事政策，支持多种假期类型。
系统通过三张表协作实现完整的请假生命周期管理：
- LeaveType 定义"哪些假期存在、规则是什么"（由 HR 管理员配置）
- LeaveBalance 维护"每人每年每种假还剩多少天"（余额账本）
- LeaveRequest 记录"员工什么时候申请了什么假、审批结果如何"（申请流水）

数据流
------
1. 年初（或入职时），LeaveService.init_balances() 为每位员工创建当年的
   LeaveBalance 记录（以 LeaveType.max_days_per_year 为 total_days 初始值）。

2. 员工提交请假申请 → 创建 LeaveRequest（approval_status=pending），
   系统预先校验 remaining_days 是否充足（但此时不扣减余额）。

3. 审批人（HR 或直属上级）在审批流程中操作：
   - 批准（approved）：LeaveService.approve_request() 执行：
       LeaveBalance.used_days  += days（请假天数）
       LeaveBalance.remaining_days -= days
     同时记录 approver_id 和 approved_at 时间戳。
   - 驳回（rejected）：余额不变，记录驳回原因（可存 reason 字段）。

4. 已批准的请假若被员工撤销（cancelled）：
   LeaveBalance.used_days  -= days（回滚）
   LeaveBalance.remaining_days += days
   仅允许在请假开始日期前撤销，已开始的假期不可撤销。

5. 考勤模块在月度汇总时读取当月审批通过的 LeaveRequest，
   将对应日期的 AttendanceRecord.status 标记为 on_leave，
   并累加到 AttendanceMonthSummary.leave_days。

6. 年末，HR 执行年假结转操作：
   - 未使用的年假（annual leave）按政策可部分结转到次年
   - 超出结转上限的天数标记为 expired_days，归零 remaining_days
   - 调休假（comp_time）通常有 expiry_date，过期不使用则自动清零

核心业务规则
------------
1. 假期天数核算（半天机制）：
   - 请假可以精确到半天（0.5 天），通过 start_half/end_half 控制
   - 例：2025-01-06(pm) 到 2025-01-08(am) = 周一下午 + 周二全天 + 周三上午 = 2.5 天
   - 半天假需与考勤系统联动：start_half=pm 时当天上午照常打卡，只下午计请假
   - days 字段存储最终核算天数（Decimal，支持 0.5 精度）

2. 带薪/不带薪：
   - LeaveType.is_paid=True 的假期（年假/婚假/产假/病假有薪部分）不扣工资
   - is_paid=False 的假期（如超出 3 天的事假）按日薪扣款，
     SalaryService 从当月 LeaveRequest 汇总中读取不带薪请假天数

3. 证明材料要求：
   - LeaveType.requires_proof=True 时，LeaveRequest.proof_url 必须上传
   - 常见：病假需医院证明、婚假需结婚证、产假需准生证

4. 地区政策差异：
   - PolicyRegion.beijing：按北京市政策，如女职工产假 158 天（含顺产奖励）
   - PolicyRegion.guangdong：按广东省政策，如女职工产假 178 天
   - PolicyRegion.all：全国通用假期

5. 调休假（comp_time）扣减来源：
   - LeaveType.deduct_from=DeductFrom.comp_time 表示申请此假期时
     优先从调休余额中扣减，调休余额不足时才从年假余额扣减
   - deduct_from=DeductFrom.annual_leave 表示直接从年假余额扣减

关键设计决策
------------
- remaining_days 作为冗余字段存储（= total_days - used_days - expired_days），
  避免每次查询都做减法运算。LeaveService 保证三者始终一致，
  并通过数据库约束（CHECK remaining_days >= 0）防止透支。
- LeaveBalance 按年度隔离（year 字段），历史年度余额永久保留，
  支持审计查询"员工 2023 年用了多少天年假"。
- LeaveRequest.days 在提交时由服务层根据 start_date/end_date/start_half/end_half
  自动计算（排除周末和节假日），不允许前端直接传入，防止篡改。
- 所有 DateTime 字段均使用 DateTime(timezone=True)，
  对应 PostgreSQL 的 TIMESTAMPTZ，确保跨时区一致性。
"""

import enum
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class HalfDay(str, enum.Enum):
    """
    半天标记枚举
    -----------
    用于 LeaveRequest 的 start_half 和 end_half 字段，
    支持请假精确到上午或下午，实现 0.5 天精度的假期核算。

    使用示例（半天请假逻辑）：
    - 仅请一个下午：start_date=end_date=2025-01-06, start_half=pm, end_half=pm → days=0.5
    - 仅请一个上午：start_date=end_date=2025-01-06, start_half=am, end_half=am → days=0.5
    - 请完整一天：start_date=end_date=2025-01-06, start_half=am, end_half=pm → days=1.0
    - 跨天：start_date=2025-01-06(pm), end_date=2025-01-08(am) → days=2.5
      （周一下午 0.5 + 周二全天 1.0 + 周三上午 0.5）

    - am  上午（从当天上班开始到午休结束，通常 09:00-12:00）
    - pm  下午（从午休结束到下班，通常 13:00-18:00）
    """
    am = "am"
    pm = "pm"


class ApprovalStatus(str, enum.Enum):
    """
    请假审批状态枚举
    ---------------
    描述 LeaveRequest 在审批流程中的当前状态。

    状态流转：
        pending（员工提交申请）
         ├─ 审批人批准 → approved（系统同步扣减 LeaveBalance.used_days）
         ├─ 审批人驳回 → rejected（余额不变，员工可修改后重新提交）
         └─ 员工主动撤销（仅限请假开始前）→ cancelled（若已扣减则回滚余额）

    - pending    待审批（刚提交，等待直属上级或 HR 审批）
    - approved   已批准（余额已扣减，考勤系统已标记对应日期为请假状态）
    - rejected   已驳回（员工可查看驳回原因，修改后重新提交）
    - cancelled  已撤销（员工主动取消，仅限请假开始前；已开始的假期不可撤销）
    """
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class DeductFrom(str, enum.Enum):
    """
    余额扣减来源枚举
    ---------------
    决定某种假期申请时应从哪个余额账户中扣减天数，
    支持调休优先、年假优先等灵活策略。

    - none          不扣减任何余额（适用于带薪法定假，如婚假/产假，
                    这类假期有独立的法定天数，不占用年假额度）
    - annual_leave  从年假（annual_leave）余额中扣减
                    （适用于事假等需要消耗年假的情形）
    - comp_time     优先从调休假（comp_time）余额中扣减，
                    调休余额不足时再从年假余额补足
                    （鼓励员工先消耗调休以避免过期）
    """
    none = "none"
    annual_leave = "annual_leave"
    comp_time = "comp_time"


class PolicyRegion(str, enum.Enum):
    """
    政策适用区域枚举
    ---------------
    部分假期类型（尤其是产假/陪产假）的天数因地方政策不同而有差异。
    LeaveType 通过此字段区分各地政策版本。

    - all         全国通用政策（适用于大多数假期，如年假/病假/婚假）
    - beijing     北京市政策
                  （例：顺产产假 158 天 = 国家 98 天 + 北京 60 天生育奖励假）
    - guangdong   广东省政策
                  （例：顺产产假 178 天 = 国家 98 天 + 广东 80 天奖励假）

    LeaveService 在为员工创建 LeaveBalance 时，根据员工所在 location 的
    省份自动匹配对应区域的 LeaveType，确保适用正确的假期天数。
    """
    all = "all"
    beijing = "beijing"
    guangdong = "guangdong"


# ---------------------------------------------------------------------------
# LeaveType – 假期类型
# ---------------------------------------------------------------------------

class LeaveType(Base):
    """
    假期类型表 (leave_types)
    =========================
    表作用
    ------
    定义公司支持的所有假期种类及其规则参数，由 HR 管理员维护。
    每种假期类型通过唯一的 code 标识，服务层通过 code 做逻辑判断
    而不依赖 id（因为 id 可能因环境不同而异）。

    常见假期类型（code 值）：
    +-----------+----------+----------+--------------------------------------+
    | code      | 名称     | is_paid  | 说明                                 |
    +-----------+----------+----------+--------------------------------------+
    | annual    | 年假     | True     | 工龄满 1 年起享有，5-15 天           |
    | sick      | 病假     | True     | 需医院证明，带薪天数有上限            |
    | personal  | 事假     | False    | 不带薪，扣日薪                        |
    | marriage  | 婚假     | True     | 国家 3 天，各地可有奖励假            |
    | maternity | 产假     | True     | 因地区政策不同天数有差异              |
    | comp_time | 调休     | True     | 加班后换来的补休，有 expiry_date     |
    +-----------+----------+----------+--------------------------------------+

    关键业务规则
    ------------
    1. code 字段在整个系统中唯一，LeaveService 通过
       `await session.execute(select(LeaveType).where(LeaveType.code == "annual"))`
       查询年假类型，勿直接硬编码 id。

    2. max_days_per_year=NULL 表示天数不限（如病假在特定情况下可无限延伸），
       LeaveService 在校验余额时跳过上限检查。

    3. requires_proof=True 时，LeaveRequest 提交时必须携带 proof_url，
       否则服务层抛出 400 错误（"该假期需要上传证明材料"）。

    4. deduct_from 控制余额扣减逻辑，详见 DeductFrom 枚举说明。

    5. is_active=False 的假期类型已停用，不出现在前端选择列表中，
       但历史 LeaveRequest 仍保留引用。

    与其他表的关联关系
    ------------------
    - balances → LeaveBalance（一对多，每种假期对应所有员工的余额记录）
    - requests → LeaveRequest（一对多，该类型下所有请假申请）
    """
    __tablename__ = "leave_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="假期名称")
    code: Mapped[str] = mapped_column(
        String(30), unique=True, nullable=False, comment="假期编码(如 annual/sick/personal)"
    )

    is_paid: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否带薪")
    max_days_per_year: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 1), nullable=True, comment="每年最大天数(NULL表示不限)"
    )
    requires_proof: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否需要证明材料")

    deduct_from: Mapped[DeductFrom] = mapped_column(
        Enum(DeductFrom, name="deduct_from_enum"),
        default=DeductFrom.none,
        comment="扣减来源",
    )

    policy_region: Mapped[PolicyRegion] = mapped_column(
        Enum(PolicyRegion, name="policy_region_enum"),
        default=PolicyRegion.all,
        comment="适用区域",
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="假期说明")

    # 企业微信式假期规则配置。旧的 max_days_per_year/is_paid/requires_proof 继续作为
    # 核心兼容字段；下列字段承载后台配置页的完整规则，用于额度发放、申请校验和考勤对接。
    en_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, comment="英文名称")
    sort_order: Mapped[int] = mapped_column(Integer, default=99, comment="展示排序")
    leave_unit: Mapped[str] = mapped_column(String(20), default="day", comment="请假单位: day/half_day/hour")
    time_calc: Mapped[str] = mapped_column(String(20), default="natural_day", comment="时长计算: natural_day/workday")
    hours_per_day: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=Decimal("8.0"), comment="1天折算小时数")
    rounding_direction: Mapped[str] = mapped_column(String(20), default="none", comment="请假时长取整方向: none/up/down")
    rounding_unit: Mapped[str] = mapped_column(String(20), default="none", comment="请假时长取整粒度: none/half_hour/hour/half_day/day")
    quota_limited: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否限额")
    quota_rule_type: Mapped[str] = mapped_column(String(30), default="fixed", comment="额度计算规则")
    quota_rule: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="额度计算规则JSON")
    issuance_rule: Mapped[str] = mapped_column(String(40), default="year_start", comment="发放方式")
    issuance_month_day: Mapped[str] = mapped_column(String(20), default="01-01", comment="发放日期")
    validity_type: Mapped[str] = mapped_column(String(40), default="natural_year", comment="有效期类型")
    expire_month_day: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="固定失效日期")
    allow_validity_extension: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否允许延长有效期")
    extension_value: Mapped[int] = mapped_column(Integer, default=0, comment="延长有效期数值")
    extension_unit: Mapped[str] = mapped_column(String(10), default="day", comment="延长有效期单位")
    expiration_reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=False, comment="假期过期提醒开关")
    reminder_before_value: Mapped[int] = mapped_column(Integer, default=1, comment="过期前提醒数值")
    reminder_before_unit: Mapped[str] = mapped_column(String(10), default="month", comment="过期前提醒单位")
    reminder_notify_employee: Mapped[bool] = mapped_column(Boolean, default=True, comment="过期提醒通知员工")
    reminder_notify_manager: Mapped[bool] = mapped_column(Boolean, default=False, comment="过期提醒通知部门主管")
    new_employee_rule: Mapped[str] = mapped_column(String(30), default="none", comment="新员工请假规则: none/after_regularization/after_months")
    new_employee_limit_enabled: Mapped[bool] = mapped_column(Boolean, default=False, comment="新员工请假限制开关")
    new_employee_limit_months: Mapped[int] = mapped_column(Integer, default=0, comment="入职未满N个月不可申请")
    scope_type: Mapped[str] = mapped_column(String(30), default="all", comment="适用范围类型")
    scope_rules: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="适用范围规则JSON")
    carry_over_enabled: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否允许结转")
    max_carry_over_days: Mapped[Decimal] = mapped_column(Numeric(5, 1), default=Decimal("0"), comment="最大结转天数")
    approval_flow: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="审批流程展示")
    leave_color: Mapped[str] = mapped_column(String(20), default="#52c41a", comment="假期颜色")

    # 该假期类型下所有员工的余额记录（一对多）
    balances: Mapped[list["LeaveBalance"]] = relationship(back_populates="leave_type", lazy="selectin")
    # 该假期类型下所有请假申请（一对多）
    requests: Mapped[list["LeaveRequest"]] = relationship(back_populates="leave_type", lazy="selectin")


# ---------------------------------------------------------------------------
# LeaveBalance – 假期余额
# ---------------------------------------------------------------------------

class LeaveBalance(Base):
    """
    假期余额账户表 (leave_balances)
    ================================
    表作用
    ------
    记录每位员工每年每种假期的余额状况，相当于假期的"银行账户"。
    三个核心字段构成余额等式：
        remaining_days = total_days - used_days - expired_days

    系统保证此等式始终成立。LeaveService 在每次审批或撤销操作后
    同步更新这三个字段，保持数据一致性。

    关键业务规则
    ------------
    1. 三字段含义：
       - total_days      年度总额度（由 HR 年初根据员工工龄、职级等分配）
       - used_days       已实际使用天数（审批通过后累加，撤销后回滚）
       - remaining_days  剩余可用天数（冗余字段，= total_days - used_days - expired_days）
       - expired_days    已过期天数（年末结转时超出结转上限的部分）

    2. remaining_days 约束：
       - 提交请假申请时，LeaveService 验证 remaining_days >= 申请天数，
         不满足则拒绝（HTTP 400 "年假余额不足"）。
       - approved 时：used_days += days，remaining_days -= days
       - cancelled 时：used_days -= days，remaining_days += days（余额回滚）

    3. 调休假（comp_time）的 expiry_date：
       - 通常设置为加班日期后的 6 个月或当年 12 月 31 日
       - 系统定时任务（每日凌晨）检查 expiry_date <= today 的余额，
         将剩余 remaining_days 移入 expired_days，清零 remaining_days

    4. 联合唯一约束 (employee_id, leave_type_id, year)：
       每位员工每种假期每年只有一个余额账户，防止重复初始化。

    5. 年度隔离：
       不同年度（year 字段不同）的余额独立管理，年假不自动跨年累积，
       需 HR 手动执行年末结转操作（生成次年的 total_days）。

    与其他表的关联关系
    ------------------
    - employee_id → employees（余额归属员工）
    - leave_type_id → leave_types（余额归属假期类型）
    - 被 LeaveService.approve_request() 修改（扣减 used_days）
    - 被 LeaveService.cancel_request() 修改（回滚 used_days）
    - ix_balance_employee_year 索引：ESS 页面展示员工当年所有假期余额时
      按 (employee_id, year) 查询，走覆盖索引
    """
    __tablename__ = "leave_balances"
    __table_args__ = (
        # 每位员工每种假期每年只能有一条余额记录，防止重复初始化
        UniqueConstraint("employee_id", "leave_type_id", "year", name="uq_balance_employee_type_year"),
        # 高频查询索引：员工自助平台展示当年所有假期余额
        Index("ix_balance_employee_year", "employee_id", "year"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=False, comment="员工ID"
    )
    leave_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("leave_types.id"), nullable=False, comment="假期类型ID"
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, comment="年度")

    # 余额三要素（remaining_days = total_days - used_days - expired_days）
    total_days: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), default=Decimal("0"), comment="总天数（年度额度）"
    )
    used_days: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), default=Decimal("0"), comment="已用天数（审批通过后累加）"
    )
    remaining_days: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), default=Decimal("0"), comment="剩余天数（冗余字段，保证>=0）"
    )
    expired_days: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), default=Decimal("0"), comment="过期天数（年末结转时超出上限部分）"
    )
    expiry_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="过期日期(调休用，过期后剩余天数自动清零)"
    )

    # onupdate 保证每次余额变动时自动更新时间戳，用于审计
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), comment="更新时间"
    )

    # 关联假期类型（加载余额时同时加载类型信息，避免 N+1 查询）
    leave_type: Mapped["LeaveType"] = relationship(back_populates="balances", lazy="selectin")


# ---------------------------------------------------------------------------
# LeaveRequest – 请假申请
# ---------------------------------------------------------------------------

class LeaveRequest(Base):
    """
    请假申请表 (leave_requests)
    ============================
    表作用
    ------
    记录员工每一次请假申请的完整信息，包括请假时间范围、天数、
    证明材料、审批状态和审批人信息。是请假模块的核心业务流水表。

    关键业务规则
    ------------
    1. 半天机制（start_half / end_half）详解：
       LeaveService 在计算 days 时执行以下逻辑：
       a) 枚举 start_date 到 end_date 之间的所有日期
       b) 过滤掉周末和工作日历中的节假日
       c) 第一天：start_half=am → 计 1.0 天；start_half=pm → 计 0.5 天
       d) 最后一天：end_half=pm → 计 1.0 天；end_half=am → 计 0.5 天
       e) 中间日期每天计 1.0 天
       f) 最终 days = 上述之和，保存到 LeaveRequest.days

       示例：请假 2025-01-13(周一 pm) 到 2025-01-15(周三 am)
       = 0.5（周一下午）+ 1.0（周二全天）+ 0.5（周三上午）= 2.0 天

    2. 审批与余额联动（LeaveService.approve_request()）：
       - 检查 approval_status 当前必须为 pending（防止重复审批）
       - 检查 LeaveBalance.remaining_days >= days（余额充足性验证）
       - 更新 LeaveBalance：
             used_days      += days
             remaining_days -= days
       - 更新 LeaveRequest：
             approval_status = approved
             approver_id     = 审批人 employee_id
             approved_at     = datetime.now(timezone.utc)

    3. 撤销逻辑（LeaveService.cancel_request()）：
       - 仅允许撤销 status=approved 且 start_date > today 的申请
       - 已开始的假期不允许撤销（需联系 HR 特殊处理）
       - 执行余额回滚：used_days -= days，remaining_days += days

    4. 证明材料校验：
       - 若关联 LeaveType.requires_proof=True，提交时 proof_url 不能为空
       - proof_url 存储文件服务器上传后的公开 URL，一般为 OSS/MinIO 路径

    5. 不带薪假期的薪资扣款：
       - LeaveType.is_paid=False 的申请（status=approved）在月度薪资计算时被读取
       - SalaryService 统计当月不带薪请假天数，按"日薪 × 天数"从工资中扣除

    6. 请假与考勤联动：
       - 月度考勤汇总生成时，系统查询该员工当月 status=approved 的 LeaveRequest，
         将对应日期的 AttendanceRecord.status 设为 on_leave，
         并将 days 累加到 AttendanceMonthSummary.leave_days

    与其他表的关联关系
    ------------------
    - employee_id → employees（申请人）
    - leave_type_id → leave_types（申请的假期类型）
    - approver_id → employees（审批人，可为 NULL 即尚未审批）
    - 审批通过后修改 leave_balances 中对应员工+假期类型+年度的余额记录
    - ix_leave_request_employee：员工查询个人请假历史时走此索引
    - ix_leave_request_status：HR 查询待审批列表时走此索引
    """
    __tablename__ = "leave_requests"
    __table_args__ = (
        # 按员工查询请假历史（ESS 员工自助、HR 员工档案均高频使用）
        Index("ix_leave_request_employee", "employee_id"),
        # 按审批状态查询（HR 审批列表页：WHERE approval_status='pending'）
        Index("ix_leave_request_status", "approval_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=False, comment="申请人ID"
    )
    leave_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("leave_types.id"), nullable=False, comment="假期类型ID"
    )

    # 请假时间范围（含半天精度控制）
    start_date: Mapped[date] = mapped_column(Date, nullable=False, comment="开始日期")
    end_date: Mapped[date] = mapped_column(Date, nullable=False, comment="结束日期")
    # 开始半天：am=从上午开始（计1天），pm=从下午开始（首日仅计0.5天）
    start_half: Mapped[HalfDay] = mapped_column(
        Enum(HalfDay, name="half_day_enum"), default=HalfDay.am, comment="开始半天"
    )
    # 结束半天：pm=到下午结束（计1天），am=到上午结束（末日仅计0.5天）
    end_half: Mapped[HalfDay] = mapped_column(
        Enum(HalfDay, name="end_half_day_enum"), default=HalfDay.pm, comment="结束半天"
    )

    # 核算天数（由服务层根据 start/end + half_day + 工作日历自动计算，不接受前端传入）
    days: Mapped[Decimal] = mapped_column(
        Numeric(5, 1), nullable=False, comment="请假天数（服务层自动计算，支持0.5精度）"
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="请假原因")
    proof_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="证明材料URL（requires_proof=True时必填）")

    # 审批信息
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="leave_approval_status_enum"),
        default=ApprovalStatus.pending,
        comment="审批状态（pending→approved/rejected/cancelled）",
    )
    # 审批人 ID（pending 时为 NULL，审批操作完成后填入）
    approver_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=True, comment="审批人ID"
    )
    # 审批时间（仅 approved/rejected 时有值，cancelled 无此时间）
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="审批时间"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")

    # 关联假期类型（加载申请时同时加载类型信息，避免 N+1 查询）
    leave_type: Mapped["LeaveType"] = relationship(back_populates="requests", lazy="selectin")
