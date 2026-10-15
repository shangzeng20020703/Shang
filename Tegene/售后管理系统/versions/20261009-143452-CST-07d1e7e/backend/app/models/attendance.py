"""
考勤模块数据模型
================
文件路径: app/models/attendance.py

表结构总览
----------
- AttendanceRule            考勤规则（工时制度、打卡方式、加班费率配置）
- ShiftSchedule             员工排班表（每人每天对应一条班次记录）
- AttendanceRecord          每日打卡记录（上下班时间、GPS/WiFi校验、计算工时）
- AttendancePunchTimeRecord 每次打卡原始流水（不覆盖，用于打卡时间记录报表）
- AttendanceEffect          审批等来源对某员工某日考勤结论的业务影响
- AttendanceRecalcTask      考勤重算脏标记任务
- AttendanceMonthlyReportSnapshot 月报异步任务结果快照
- WorkCalendar              工作日历（节假日/调休/特殊工作日）
- PunchCorrectionRequest    补卡申请（员工主动申请修正漏打卡）
- AttendanceMonthSummary    月度考勤汇总快照（供薪资模块直接读取，避免重复计算）

业务说明
--------
本公司在多个城市设有办公地点，不同地点/部门采用不同工时制度：
- 北京研发中心：弹性工时（flexible），核心工时段内打卡即可，适合研发人员
- 东莞工厂：标准工时（standard），严格固定上下班时间，需 GPS 或门禁打卡
- 驻场外包/销售：综合计算工时（comprehensive），按周期核算总工时而非每日

员工通过手机 App（ClockSource.app）或工厂门禁闸机（ClockSource.gate）打卡；
HR 可代为手工录入（ClockSource.manual）。

数据流
------
1. 员工打卡 → 写入 AttendanceRecord（clock_in_time / clock_out_time）
2. 系统自动对比 ShiftSchedule 中的规定时间，计算 work_hours 和 overtime_hours，
   并根据偏差将 status 标记为 normal / late / early_leave / missed_clock / absent
3. 每月底（或月初结账时），薪资服务调用 AttendanceService.generate_month_summary()
   生成 AttendanceMonthSummary，状态流转：draft → employee_confirmed → locked
4. 薪资计算服务 SalaryService 从 AttendanceMonthSummary 中读取以下字段：
   - overtime_weekday_hours  → 乘以 overtime_weekday_rate (1.5x) 计入加班费
   - overtime_weekend_hours  → 乘以 overtime_weekend_rate (2.0x) 计入加班费
   - overtime_holiday_hours  → 乘以 overtime_holiday_rate (3.0x) 计入加班费
   - absent_count            → 按日薪扣款
   - late_count              → 可按公司政策扣款

核心业务规则
------------
1. 加班工时认定：
   - 标准工时制：clock_out_time 超过 clock_out_time+30min 才开始计加班，
     防止下班后短暂滞留被误计
   - 综合工时制：按月/季度周期结算，超出法定工时的部分才算加班
   - 弹性工时制：不计日加班，只看每日工时是否达标

2. 异常申诉状态（CorrectionStatus）：
   none（无申诉） → pending（员工提交申诉） →
   approved（HR/主管审批通过） / rejected（审批驳回）

3. 模拟定位防刷：is_mock_location=True 的打卡记录不计入正常出勤，
   系统将 status 设为 missed_clock

4. 月度汇总锁定后（locked），薪资已发放，不允许再修改对应月份的任何打卡记录

关键设计决策
------------
- AttendanceRecord 只存原始打卡时间，work_hours/overtime_hours 为计算冗余字段，
  由服务层在写入或按需重算，避免每次读取都做复杂运算
- AttendanceMonthSummary 是面向薪资的聚合快照，锁定后成为薪资计算的唯一数据来源，
  即使后续打卡记录被修正，已锁定的汇总也不变（保证薪资核算可追溯）
- WorkCalendar 支持按 location_id 区分假期安排（NULL = 全国通用），
  满足北京/广东不同法定假日安排的需求
- 所有 DateTime 字段均使用 DateTime(timezone=True)，
  对应 PostgreSQL 的 TIMESTAMPTZ，确保跨时区一致性
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time, timezone
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
    Time,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class WorkHourType(str, enum.Enum):
    """
    工时制度枚举
    -----------
    决定了考勤规则的核心逻辑，影响迟到判定、加班认定和薪资计算方式。

    - flexible      弹性工时制 — 主要用于北京研发中心。
                    员工可在弹性范围内自由选择上下班时间（如 flexible_minutes=60
                    表示可在标准时间前后 60 分钟内打卡），只要每日工时达标即可。
                    适合需要专注不间断工作的研发/产品岗位。

    - standard      标准工时制 — 主要用于东莞工厂及行政岗位。
                    严格固定上下班时间（clock_in_time / clock_out_time），
                    超出弹性分钟数则标记迟到/早退，超时工作计入加班。
                    法定标准：每日 8 小时，每周 40 小时。

    - comprehensive 综合计算工时制 — 主要用于驻场技术支持、销售外勤等岗位。
                    以月/季/年为周期核算总工时，超出法定总工时才认定为加班。
                    经劳动局审批后方可适用，避免频繁出差人员天天被判异常。
    """
    flexible = "flexible"            # 弹性 – 北京研发中心
    standard = "standard"            # 标准 – 东莞工厂
    comprehensive = "comprehensive"  # 综合 – 驻场人员


class ShiftType(str, enum.Enum):
    """
    班次类型枚举
    -----------
    - day_shift    白班（正常工作日，工厂一般为 08:00-17:00）
    - night_shift  夜班（工厂生产需要，一般为 20:00-08:00 次日，加班费率更高）
    - rest         休息班（当天不上班，不产生考勤记录，用于排班表明确标注轮休）
    """
    day_shift = "day_shift"
    night_shift = "night_shift"
    rest = "rest"


class AttendanceStatus(str, enum.Enum):
    """
    每日考勤状态枚举
    ---------------
    由系统在写入 AttendanceRecord 后自动根据打卡时间与排班规则对比计算得出，
    HR 也可手动修正。

    - normal         正常出勤（上下班均在规定时间范围内打卡）
    - late           迟到（上班打卡时间超过规定时间+弹性分钟数）
    - early_leave    早退（下班打卡时间早于规定时间-弹性分钟数）
    - missed_clock   缺卡（只有一次打卡记录，上班或下班打卡缺失；
                          模拟定位被检测时也标记为缺卡）
    - absent         旷工（整天无打卡记录，且无批准的请假/出差）
    - business_trip  出差（该日有审批通过的出差记录，不按旷工处理）
    - on_leave       请假（该日有审批通过的请假申请，不按旷工处理）

    注意：business_trip 和 on_leave 由系统在月度汇总时关联审批记录自动填充，
    不依赖员工打卡。
    """
    normal = "正常"
    late = "迟到"
    early_leave = "早退"
    missed_clock = "缺卡"
    absent = "旷工"
    business_trip = "出差"
    on_leave = "请假"


class CorrectionStatus(str, enum.Enum):
    """
    考勤异常申诉状态枚举
    -------------------------
    描述 AttendanceRecord 中异常申诉流程的当前阶段。

    状态流转：
        none（默认，无申诉）
         └─ 员工发现异常后提交申诉
            ↓
        pending（申诉审批中）
         ├─ 审批通过 → approved
         └─ 审批驳回 → rejected（原打卡记录保持不变）

    - none      无申诉
    - pending   员工已提交申诉，等待 HR 或直属上级审批
    - approved  审批通过
    - rejected  审批驳回
    """
    none = "none"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ClockSource(str, enum.Enum):
    """
    打卡来源枚举
    -----------
    记录打卡数据的来源渠道，用于异常分析和数据质量审计。

    - app     手机 App 打卡（最常见，支持 GPS/WiFi 校验和拍照）
    - gate    工厂/办公室门禁闸机打卡（刷卡或人脸识别，位置固定可信度高）
    - manual  HR 或管理员手工录入（一般用于系统故障补录、出差人员补录等情况，
              manual 来源的记录会在审计日志中特别标注）
    """
    app = "app"
    gate = "gate"
    manual = "manual"


class DayType(str, enum.Enum):
    """
    工作日历日期类型枚举
    -------------------
    用于 WorkCalendar 表，决定某一天是否计入应出勤天数，
    以及该天加班时适用哪种费率。

    - workday          普通工作日（正常出勤，加班按 overtime_weekday_rate 计算）
    - weekend          周末（无需打卡，出勤按 overtime_weekend_rate 计算加班费）
    - holiday          法定节假日（无需打卡，出勤按 overtime_holiday_rate=3.0x 计算加班费）
    - special_workday  调休补班（法定假日前后的补班安排，按 workday 规则出勤，
                                 但通常不产生加班费，视公司政策而定）
    """
    workday = "workday"
    weekend = "weekend"
    holiday = "holiday"
    special_workday = "special_workday"


class MonthlySummaryStatus(str, enum.Enum):
    """
    月度考勤汇总状态枚举
    -------------------
    控制 AttendanceMonthSummary 的生命周期，与薪资发放流程强耦合。

    状态流转：
        draft（HR 生成汇总草稿）
         └─ 员工在 ESS（员工自助平台）确认无误后提交
            ↓
        employee_confirmed（员工已确认，等待 HR 最终锁定）
         └─ HR 确认薪资计算正确后锁定
            ↓
        locked（已锁定，薪资已发放，不允许再修改当月考勤记录）

    - draft               草稿状态，员工和 HR 均可查看，系统可重新生成覆盖
    - employee_confirmed  员工已在 App 上确认，表示对出勤数据无异议
    - locked              HR 锁定，标志着该月考勤核算完毕，成为薪资计算的最终依据
    """
    draft = "draft"
    employee_confirmed = "employee_confirmed"
    locked = "locked"


# ---------------------------------------------------------------------------
# AttendanceRule – 考勤规则
# ---------------------------------------------------------------------------

class AttendanceRule(Base):
    """
    考勤规则表 (attendance_rules)
    ==============================
    表作用
    ------
    存储不同地点/部门的考勤参数配置，是考勤系统的"规则引擎"。
    每条规则定义了一套完整的考勤约束，包括：
    - 工时制度类型（弹性/标准/综合）
    - 打卡时间要求（上下班时间、弹性分钟数）
    - 加班费率（工作日/周末/法定假日三档）
    - 打卡方式约束（是否强制 GPS/WiFi/拍照）

    关键业务规则
    ------------
    1. 规则可绑定到特定 location_id（地点）或 department_id（部门），
       也可两者都不绑定（作为默认兜底规则）。
       优先级：department_id 规则 > location_id 规则 > 全局默认规则。

    2. 加班费率体系（符合《劳动法》要求）：
       - overtime_weekday_rate  = 1.5（工作日延时加班，日薪×1.5）
       - overtime_weekend_rate  = 2.0（周末加班，日薪×2.0）
       - overtime_holiday_rate  = 3.0（法定假日加班，日薪×3.0，不可调休代替）

    3. GPS 校验逻辑：
       require_gps=True 时，打卡 GPS 坐标必须在以
       (gps_latitude, gps_longitude) 为圆心、gps_radius_meters 为半径的范围内，
       否则系统标记为可疑打卡（is_mock_location）。

    4. is_active=False 的规则已停用，不再分配给新员工排班，
       但历史 ShiftSchedule 记录仍保留引用。

    与其他表的关联关系
    ------------------
    - shifts → ShiftSchedule（一个规则可对应多条排班记录）
    - location_id → locations（规则绑定地点）
    - department_id → departments（规则绑定部门）
    """
    __tablename__ = "attendance_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="规则名称")

    work_hour_type: Mapped[WorkHourType] = mapped_column(
        Enum(WorkHourType, name="work_hour_type_enum"),
        nullable=False,
        comment="工时制度: flexible/standard/comprehensive",
    )

    location_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("locations.id"), nullable=True, comment="适用工作地点"
    )
    department_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("departments.id"), nullable=True, comment="适用部门"
    )

    # 标准工时制: 固定上下班时间
    clock_in_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True, comment="上班时间")
    clock_out_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True, comment="下班时间")
    flexible_minutes: Mapped[int] = mapped_column(Integer, default=0, comment="弹性分钟数")
    work_hours_per_day: Mapped[Decimal] = mapped_column(
        Numeric(4, 1), default=Decimal("8.0"), comment="每日工时"
    )

    # 加班费率
    overtime_weekday_rate: Mapped[Decimal] = mapped_column(
        Numeric(3, 1), default=Decimal("1.5"), comment="工作日加班倍率"
    )
    overtime_weekend_rate: Mapped[Decimal] = mapped_column(
        Numeric(3, 1), default=Decimal("2.0"), comment="周末加班倍率"
    )
    overtime_holiday_rate: Mapped[Decimal] = mapped_column(
        Numeric(3, 1), default=Decimal("3.0"), comment="法定假日加班倍率"
    )

    # 定位 / WiFi / 拍照校验
    require_gps: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否需要GPS打卡")
    require_wifi: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否需要WiFi打卡")
    require_photo: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否需要拍照打卡")
    gps_latitude: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 7), nullable=True, comment="GPS中心纬度"
    )
    gps_longitude: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 7), nullable=True, comment="GPS中心经度"
    )
    gps_radius_meters: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="GPS有效半径(米)"
    )
    wifi_ssid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="WiFi SSID")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    priority: Mapped[int] = mapped_column(Integer, default=100, comment="规则优先级（数字越小优先级越高）")
    effective_date: Mapped[date] = mapped_column(Date, default=date.today, comment="规则生效日期")
    extra_config: Mapped[dict] = mapped_column(JSON, default=dict, comment="规则扩展配置（午休/员工类型/节假日方案等）")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="更新时间",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")

    # 关联排班记录（一个规则可被多条排班引用）
    shifts: Mapped[list["ShiftSchedule"]] = relationship(back_populates="rule", lazy="selectin")
    logs: Mapped[list["AttendanceRuleLog"]] = relationship(back_populates="rule", lazy="selectin")


class AttendanceRuleLog(Base):
    """
    考勤规则变更日志表，用于追溯规则被谁在何时如何修改。
    """

    __tablename__ = "attendance_rule_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(Integer, ForeignKey("attendance_rules.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, comment="create/update/toggle/copy/delete")
    operator_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("employees.id"), nullable=True)
    operator_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="变更详情")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="操作时间")

    rule: Mapped["AttendanceRule"] = relationship(back_populates="logs")


# ---------------------------------------------------------------------------
# ShiftSchedule – 排班
# ---------------------------------------------------------------------------

class ShiftSchedule(Base):
    """
    员工排班表 (shift_schedules)
    =============================
    表作用
    ------
    记录每位员工每天的班次安排，是考勤异常判定的基准。
    系统将员工当天的实际打卡时间与对应 ShiftSchedule 中的 start_time/end_time
    对比，结合关联 AttendanceRule 的 flexible_minutes 判断是否迟到/早退。

    关键业务规则
    ------------
    1. 联合唯一约束 (employee_id, date)：每个员工每天只能有一条排班记录，
       防止重复排班导致考勤判断混乱。

    2. shift_type = rest（休息班）时，start_time/end_time 无意义，
       系统不会在该日期检查打卡记录，也不会标记为旷工。

    3. 排班由 HR 或部门主管提前录入（通常按月或按周批量导入），
       也可通过 API 单条创建/修改。未排班员工默认套用其所在地点/部门的
       AttendanceRule 中的 clock_in_time/clock_out_time。

    4. 夜班（night_shift）的 end_time 可能小于 start_time（如 20:00 → 08:00），
       系统在计算 work_hours 时需跨日处理。

    与其他表的关联关系
    ------------------
    - employee_id → employees（确定是哪位员工的排班）
    - rule_id → attendance_rules（关联规则，获取弹性分钟数、加班费率等参数）
    - ix_shift_employee_date 索引：高频查询"某员工某日排班"时走覆盖索引
    """
    __tablename__ = "shift_schedules"
    __table_args__ = (
        # 每位员工每天只能有一条排班，防止重复
        UniqueConstraint("employee_id", "date", name="uq_shift_employee_date"),
        # 高频查询索引：按员工+日期检索排班
        Index("ix_shift_employee_date", "employee_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=False, comment="员工ID"
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, comment="排班日期")
    shift_type: Mapped[ShiftType] = mapped_column(
        Enum(ShiftType, name="shift_type_enum"), nullable=False, comment="班次类型"
    )
    rule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("attendance_rules.id"), nullable=False, comment="考勤规则ID"
    )
    start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True, comment="班次开始时间")
    end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True, comment="班次结束时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")

    # 关联考勤规则（加载排班时同时加载规则，避免 N+1 查询）
    rule: Mapped["AttendanceRule"] = relationship(back_populates="shifts", lazy="selectin")


# ---------------------------------------------------------------------------
# AttendanceRecord – 打卡记录
# ---------------------------------------------------------------------------

class AttendanceRecord(Base):
    """
    每日打卡记录表 (attendance_records)
    =====================================
    表作用
    ------
    存储员工每天的原始打卡数据及系统计算的考勤结果，是考勤模块的核心业务表。
    每条记录对应一位员工的某一天，包含：
    - 上下班打卡的时间戳和地理位置信息
    - 系统计算的实际工时和加班工时
    - 考勤状态（正常/迟到/早退/缺卡/旷工等）
    - 异常申诉状态

    关键业务规则
    ------------
    1. work_hours 计算：
       clock_out_time - clock_in_time - 午休时间（通常 1 小时）= 实际工时。
       若缺少任一打卡记录，work_hours 为 NULL，status 标记为 missed_clock。

    2. overtime_hours 计算（直接影响薪资）：
       - 标准/弹性工时：work_hours - work_hours_per_day（规则中的每日标准工时），
         结果 > 0 则为加班时数，结合 WorkCalendar 的 day_type 决定适用费率：
           * workday  → overtime_weekday_hours 累加
           * weekend  → overtime_weekend_hours 累加
           * holiday  → overtime_holiday_hours 累加
       - 这些分类加班时数最终汇总到 AttendanceMonthSummary，
         再被 SalaryService 读取计算加班费（公式：overtime_hours × 日薪 ÷ 8 × 倍率）

    3. 模拟定位检测：
       is_mock_location=True 表示 App 检测到员工使用虚拟定位软件作弊，
       该打卡记录不计为有效出勤，status 自动设为 missed_clock，
       anomaly_type 填写 "模拟定位" 说明。

    4. 月度汇总锁定后，该月的 AttendanceRecord 不允许再被修改（服务层控制）。

    与其他表的关联关系
    ------------------
    - employee_id → employees（打卡归属员工）
    - ix_attendance_employee_date 索引：按员工+日期查询是最高频的访问模式，
      月度汇总时会全表扫描单个员工的整月记录
    """
    __tablename__ = "attendance_records"
    __table_args__ = (
        # 高频查询索引：考勤汇总、月报生成均按此查询
        Index("ix_attendance_employee_date", "employee_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=False, comment="员工ID"
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, comment="考勤日期")

    # 上班打卡
    clock_in_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="上班打卡时间")
    clock_in_gps_lat: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    clock_in_gps_lng: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    clock_in_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="上班打卡地名/地址")
    clock_in_device: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="上班打卡设备识别信息")
    clock_in_wifi_ssid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    clock_in_photo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # 下班打卡
    clock_out_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="下班打卡时间")
    clock_out_gps_lat: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    clock_out_gps_lng: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    clock_out_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="下班打卡地名/地址")
    clock_out_device: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="下班打卡设备识别信息")
    clock_out_wifi_ssid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    clock_out_photo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    is_mock_location: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否模拟定位")

    # 计算字段（由 AttendanceService 在写入打卡记录后自动填充）
    work_hours: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True, comment="实际工时"
    )
    overtime_hours: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True, comment="加班工时（薪资计算的直接来源）"
    )

    status: Mapped[AttendanceStatus] = mapped_column(
        Enum(AttendanceStatus, name="attendance_status_enum"),
        default=AttendanceStatus.normal,
        comment="考勤状态",
    )
    anomaly_type: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="异常类型描述"
    )

    # 异常申诉
    correction_status: Mapped[CorrectionStatus] = mapped_column(
        Enum(CorrectionStatus, name="correction_status_enum"),
        default=CorrectionStatus.none,
        comment="异常申诉状态",
    )
    correction_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="异常申诉说明")

    source: Mapped[ClockSource] = mapped_column(
        Enum(ClockSource, name="clock_source_enum"),
        default=ClockSource.app,
        comment="打卡来源",
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")


class AttendancePunchTimeRecord(Base):
    """
    每次打卡原始流水表 (attendance_punch_time_records)
    ==================================================
    表作用
    ------
    记录员工每一次实际提交的打卡，不参与覆盖。AttendanceRecord 保存日报/月报使用的
    最终上班卡和下班卡；本表保存全部历史提交，用于企业微信样式的"打卡时间记录"。

    关键业务规则
    ------------
    1. 上班重复打卡：AttendanceRecord 取最早上班卡，本表保留每一次上班打卡。
    2. 下班更新打卡：AttendanceRecord 取最新下班卡，本表保留每一次下班打卡。
    3. 报表展示：按员工和月份展开，日期列中显示当天所有打卡时间。
    """
    __tablename__ = "attendance_punch_time_records"
    __table_args__ = (
        Index("ix_attendance_punch_time_employee_date", "employee_id", "work_date"),
        Index("ix_attendance_punch_time_date", "work_date"),
        Index("ix_attendance_punch_time_time", "punch_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(Integer, ForeignKey("employees.id"), nullable=False, index=True, comment="员工ID")
    attendance_record_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("attendance_records.id"), nullable=True, index=True, comment="关联每日最终记录")
    work_date: Mapped[date] = mapped_column(Date, nullable=False, comment="归属考勤日期")
    punch_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="本次实际打卡时间")
    punch_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="打卡类型: clock_in/clock_out")
    source: Mapped[str] = mapped_column(String(20), default=ClockSource.app.value, comment="打卡来源")
    gps_lat: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    gps_lng: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    wifi_ssid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    device_info: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="本次打卡设备识别信息")
    photo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_mock_location: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否模拟定位")
    remark: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="本次打卡备注")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")

    employee: Mapped["Employee"] = relationship("Employee", lazy="selectin")
    attendance_record: Mapped[Optional["AttendanceRecord"]] = relationship("AttendanceRecord", lazy="selectin")


class AttendanceEffect(Base):
    """
    审批/外部事件对考勤的影响记录。

    effect 表不是原始打卡数据，而是说明“某个来源在某一天如何解释考勤”的事实：
    请假、出差、外出、补卡、加班等审批通过后都会落到这里。这样月度汇总或日结果
    重算时可以做到幂等、可追溯、可撤销。
    """

    __tablename__ = "attendance_effects"
    __table_args__ = (
        UniqueConstraint(
            "source_instance_id",
            "effect_type",
            "employee_id",
            "work_date",
            "start_at",
            "end_at",
            name="uq_attendance_effect_source",
        ),
        Index("idx_attendance_effect_employee_date", "employee_id", "work_date", "effect_status"),
        Index("idx_attendance_effect_source_event", "source_event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_module: Mapped[str] = mapped_column(String(50), nullable=False, default="approval")
    source_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_instance_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    source_business_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source_business_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    employee_id: Mapped[int] = mapped_column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    effect_type: Mapped[str] = mapped_column(String(50), nullable=False)
    effect_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    work_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    pay_policy: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    attendance_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    payload_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    employee: Mapped["Employee"] = relationship("Employee", lazy="selectin")


class AttendanceRecalcTask(Base):
    """
    日考勤重算任务。

    当前阶段作为 dirty 标记使用，消费者写入 effect 后创建任务；后续可由后台任务
    或管理端重放入口按员工+日期批量处理。
    """

    __tablename__ = "attendance_recalc_tasks"
    __table_args__ = (
        Index("idx_attendance_recalc_status", "status", "work_date"),
        Index("idx_attendance_recalc_employee_date", "employee_id", "work_date"),
        Index("idx_attendance_recalc_source_event", "source_event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(Integer, ForeignKey("employees.id"), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(String(50), nullable=False, default="approval_effect")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    source_event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    employee: Mapped["Employee"] = relationship("Employee", lazy="selectin")


# ---------------------------------------------------------------------------
# AttendanceMonthlyReportSnapshot – 月报任务结果快照
# ---------------------------------------------------------------------------

class AttendanceMonthlyReportSnapshot(Base):
    """
    月报异步任务结果快照。

    用于保存“汇总报表-月报汇总”异步任务生成后的正式结果，使页面展示、
    后续导出和短期重复查询使用同一份数据，避免重复全量计算。
    """

    __tablename__ = "attendance_monthly_report_snapshots"
    __table_args__ = (
        Index("idx_attendance_monthly_report_cache", "cache_key", "expires_at"),
        Index("idx_attendance_monthly_report_task", "task_id"),
        Index("idx_attendance_monthly_report_status", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="异步任务ID")
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, comment="归一化查询条件缓存键")
    query_json: Mapped[dict] = mapped_column(JSON, nullable=False, comment="报表查询条件")
    result_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="月报结果快照")
    row_count: Mapped[int] = mapped_column(Integer, default=0, comment="结果员工行数")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="succeeded", comment="快照状态")
    generated_by_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("employees.id"), nullable=True, comment="生成用户")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="失败原因")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="完成时间")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="短期缓存过期时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="更新时间",
    )

    generated_by: Mapped[Optional["Employee"]] = relationship("Employee", lazy="selectin")


# ---------------------------------------------------------------------------
# WorkCalendar – 工作日历
# ---------------------------------------------------------------------------

class WorkCalendar(Base):
    """
    工作日历表 (work_calendars)
    ============================
    表作用
    ------
    维护每个工作地点每一天的日期类型（工作日/周末/节假日/调休补班），
    是加班费率判定和应出勤天数计算的权威数据来源。

    关键业务规则
    ------------
    1. location_id = NULL 表示全国通用日历（默认）；
       填写具体 location_id 表示该地点的特殊安排（如广东省特有假日）。
       系统查询时优先匹配地点专属记录，找不到则回退到全局记录。

    2. 联合唯一约束 (date, location_id)：
       同一地点同一天只能有一条日历记录，防止重复导入。

    3. holiday_name 字段仅用于展示（如"春节"、"国庆节"），
       不参与任何业务逻辑计算。

    4. 每年初 HR 需要根据人社部发布的节假日安排批量导入当年日历，
       未导入的日期系统按周一至周五=workday、周六日=weekend 推算。

    5. special_workday（调休补班）按普通 workday 规则计算出勤，
       员工缺席同样计旷工，但该日加班不享受 2.0x/3.0x 费率，
       仍按 1.5x 工作日加班费率处理。

    与其他表的关联关系
    ------------------
    - location_id → locations（NULL 表示适用所有地点）
    - 被 AttendanceService 在计算 overtime_hours 分类时查询
    - 被 AttendanceMonthSummary 生成时查询 work_days（应出勤天数）
    """
    __tablename__ = "work_calendars"
    __table_args__ = (
        # 同一地点同一日期只能有一条日历记录
        UniqueConstraint("date", "location_id", name="uq_calendar_date_location"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, comment="日期")
    location_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("locations.id"), nullable=True, comment="工作地点(NULL表示全局)"
    )
    day_type: Mapped[DayType] = mapped_column(
        Enum(DayType, name="day_type_enum"), nullable=False, comment="日类型"
    )
    holiday_name: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="节假日名称"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")


# ---------------------------------------------------------------------------
# PunchCorrectionRequest – 补卡申请
# ---------------------------------------------------------------------------

class PunchCorrectionStatus(str, enum.Enum):
    """
    补卡申请状态枚举（用于 PunchCorrectionRequest 表）
    --------------------------------------------------
    注意：此枚举与 AttendanceRecord.correction_status（CorrectionStatus）是两个独立的枚举，
    但业务含义相同。PunchCorrectionRequest 是补卡申请实体，
    CorrectionStatus 是打卡记录上的状态冗余字段，两者状态同步更新。

    - pending   申请已提交，等待 HR 或主管审批
    - approved  审批通过，系统已修正对应的 AttendanceRecord
    - rejected  审批驳回，员工需重新提交或联系 HR 处理
    """
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class PunchCorrectionRequest(Base):
    """
    补卡申请记录表 (punch_correction_requests)
    ===========================================
    表作用
    ------
    当员工发现某天漏打卡（missed_clock）时，通过此表提交补卡申请。
    HR 或直属上级审批通过后，系统自动将申请的 punch_time 写入对应
    AttendanceRecord 的 clock_in_time 或 clock_out_time，并重新计算工时。

    关键业务规则
    ------------
    1. punch_type 取值为 "check_in"（上班打卡补录）或 "check_out"（下班打卡补录）。

    2. 一条 AttendanceRecord 理论上可以有两条补卡申请（分别补上下班），
       但系统不做强制约束，由 HR 在审批时人工判断合理性。

    3. 补卡申请需要在当月月度汇总锁定前完成。汇总锁定（locked）后
       AttendanceService 拒绝处理该月的任何补卡申请。

    4. reviewer_id 记录审批人，review_comment 记录审批意见，
       reviewed_at 记录审批时间，用于审计追溯。

    与其他表的关联关系
    ------------------
    - employee_id → employees（提交补卡的员工）
    - reviewer_id → employees（执行审批的 HR/主管，可为 NULL 即未审批）
    - 审批通过后修改 attendance_records 中对应员工+日期的记录
    """
    __tablename__ = "punch_correction_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False, index=True)
    correction_date: Mapped[date] = mapped_column(Date, nullable=False)
    punch_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    punch_type: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    reviewer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id"), nullable=True)
    review_comment: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    employee: Mapped["Employee"] = relationship("Employee", foreign_keys=[employee_id])
    reviewer: Mapped[Optional["Employee"]] = relationship("Employee", foreign_keys=[reviewer_id])


# ---------------------------------------------------------------------------
# AttendanceMonthSummary – 月度汇总
# ---------------------------------------------------------------------------

class AttendanceMonthSummary(Base):
    """
    月度考勤汇总表 (attendance_month_summaries)
    ============================================
    表作用
    ------
    存储每位员工每月的考勤聚合数据快照，是薪资模块的直接数据来源。
    每月底由 AttendanceService.generate_month_summary() 从 AttendanceRecord
    汇总计算生成，避免薪资计算时实时扫描全量打卡记录。

    核心设计：此表是"读优化的宽表"——将所有薪资计算所需的考勤指标
    预先聚合存储，薪资服务只需做一次简单的主键查询即可获取所有参数。

    关键业务规则
    ------------
    1. 薪资计算公式中用到的字段（SalaryService 直接读取）：
       - overtime_weekday_hours × (日薪 ÷ 8) × overtime_weekday_rate(1.5) = 工作日加班费
       - overtime_weekend_hours × (日薪 ÷ 8) × overtime_weekend_rate(2.0) = 周末加班费
       - overtime_holiday_hours × (日薪 ÷ 8) × overtime_holiday_rate(3.0) = 节假日加班费
       - absent_count × 日薪 = 旷工扣款金额
       - late_count / early_leave_count → 按公司制度可能触发绩效扣分或罚款

    2. work_days（应出勤天数）通过 WorkCalendar 查询当月 workday+special_workday 数量。
       actual_days = work_days - absent_count，用于全勤奖判定。

    3. 状态流转（与 MonthlySummaryStatus 对应）：
       draft → 员工在 ESS 中查看并点击"确认无误" → employee_confirmed →
       HR 在薪资模块发起月结操作 → locked（同时记录 locked_at 时间戳）

    4. locked 状态后：
       - AttendanceService 拒绝修改该月的 AttendanceRecord
       - SalaryService 可以安全读取并生成 SalaryRecord
       - 联合唯一约束保证一个员工同月只有一条汇总记录，防止重复生成

    5. 未出现在 WorkCalendar 中的日期（如年初尚未导入日历）将影响 work_days 准确性，
       HR 应在月底结账前确保当月日历已完整导入。

    与其他表的关联关系
    ------------------
    - employee_id → employees（汇总归属员工）
    - 聚合自 attendance_records（按 employee_id + date 范围查询）
    - 被 salary_records（薪资记录）读取，生成当月薪资单
    - uq_summary_employee_month + ix_summary_employee_month：
      薪资服务按 (employee_id, year, month) 精确定位，走唯一索引
    """
    __tablename__ = "attendance_month_summaries"
    __table_args__ = (
        # 每位员工每月只能有一条汇总记录，薪资计算依赖此约束保证幂等性
        UniqueConstraint("employee_id", "year", "month", name="uq_summary_employee_month"),
        # 薪资服务高频查询索引：按员工+年月精确定位
        Index("ix_summary_employee_month", "employee_id", "year", "month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=False, comment="员工ID"
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, comment="年份")
    month: Mapped[int] = mapped_column(Integer, nullable=False, comment="月份")

    # 统计
    work_days: Mapped[int] = mapped_column(Integer, default=0, comment="应出勤天数")
    actual_days: Mapped[int] = mapped_column(Integer, default=0, comment="实际出勤天数")
    late_count: Mapped[int] = mapped_column(Integer, default=0, comment="迟到次数")
    early_leave_count: Mapped[int] = mapped_column(Integer, default=0, comment="早退次数")
    absent_count: Mapped[int] = mapped_column(Integer, default=0, comment="旷工天数")
    missed_clock_count: Mapped[int] = mapped_column(Integer, default=0, comment="缺卡次数")

    # 三类加班工时（分类存储是为了薪资服务能直接用不同费率计算，无需再查 WorkCalendar）
    overtime_weekday_hours: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), default=Decimal("0"), comment="工作日加班时数"
    )
    overtime_weekend_hours: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), default=Decimal("0"), comment="周末加班时数"
    )
    overtime_holiday_hours: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), default=Decimal("0"), comment="法定假日加班时数"
    )

    leave_days: Mapped[Decimal] = mapped_column(
        Numeric(5, 1), default=Decimal("0"), comment="请假天数"
    )
    business_trip_days: Mapped[Decimal] = mapped_column(
        Numeric(5, 1), default=Decimal("0"), comment="出差天数"
    )
    anomaly_count: Mapped[int] = mapped_column(Integer, default=0, comment="异常次数")

    # 状态
    status: Mapped[MonthlySummaryStatus] = mapped_column(
        Enum(MonthlySummaryStatus, name="monthly_summary_status_enum"),
        default=MonthlySummaryStatus.draft,
        comment="汇总状态",
    )
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="锁定时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")
