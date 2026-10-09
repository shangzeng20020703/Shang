"""
模块名称: schemas/attendance.py
模块作用: 考勤模块（Attendance Domain）的所有 Pydantic v2 数据传输对象（DTO）定义

功能覆盖:
  - 考勤规则: AttendanceRuleBase / AttendanceRuleCreate / AttendanceRuleUpdate / AttendanceRuleOut
  - 班次排班: ShiftScheduleBase / ShiftScheduleCreate / ShiftScheduleBatchCreate /
              ShiftScheduleUpdate / ShiftScheduleOut
  - 打卡记录: ClockInRequest / ClockOutRequest / AttendanceRecordOut / AttendanceRecordQuery
  - 工作日历: WorkCalendarBase / WorkCalendarCreate / WorkCalendarBatchCreate /
              WorkCalendarUpdate / WorkCalendarOut
  - 月度汇总: MonthSummaryOut / MonthSummaryGenerateRequest / MonthSummaryConfirm / MonthSummaryLock
  - 加班申请: OvertimeApplicationCreate / OvertimeApplicationOut
  - 通用分页: PaginatedResponse

依赖关系:
  - 依赖 app/models/attendance.py 中的枚举类型:
    * WorkHourType: 工时类型（固定制/弹性制/排班制等）
    * ShiftType: 班次类型（早班/中班/晚班/白班等）
    * AttendanceStatus: 考勤状态（正常/迟到/早退/缺勤/请假等）
    * CorrectionStatus: 修正状态（pending/approved/rejected）
    * ClockSource: 打卡来源（app/web/硬件打卡机等）
    * DayType: 工作日历日期类型（工作日/法定节假日/调休工作日/周末）
    * MonthlySummaryStatus: 月汇总状态（draft/confirmed/locked）
  - 被 app/api/v1/endpoints/attendance.py 所有路由使用
  - 被 app/services/attendance_service.py 作为输入/输出类型使用

数据流向:
  HTTP Request Body → *Create/*Request (Pydantic 验证) → Service Layer (ORM)
  ORM Model → *Out (ConfigDict from_attributes=True) → HTTP Response JSON

业务说明:
  - 考勤规则（AttendanceRule）: 定义打卡规则，包括上下班时间、弹性分钟、
    加班倍率、GPS/WiFi/拍照等打卡验证方式
  - 班次排班（ShiftSchedule）: 为员工按日期分配班次，支持批量排班
  - 打卡记录（AttendanceRecord）: 员工每日打卡产生，记录打卡时间、位置、状态
  - 工作日历（WorkCalendar）: 维护法定节假日和调休工作日，供考勤计算使用
  - 月度汇总（MonthSummary）: 汇总员工当月考勤数据，锁定后用于薪资计算
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, ConfigDict

from app.models.attendance import (
    WorkHourType,
    ShiftType,
    AttendanceStatus,
    CorrectionStatus,
    ClockSource,
    DayType,
    MonthlySummaryStatus,
    PunchCorrectionStatus,
)


# ===================================================================
# AttendanceRule — 考勤规则
# ===================================================================

class AttendanceRuleBase(BaseModel):
    """
    用途: 考勤规则写操作 Schema 的基类
    业务: 定义一套考勤规则，可绑定到地点或部门，支持 GPS/WiFi/拍照等验证
    字段说明:
      - work_hour_type: 工时制度类型（如固定制/弹性制/排班制），影响迟到计算逻辑
      - location_id / department_id: 规则适用范围（可绑定地点或部门，均为 None 则全局适用）
      - clock_in_time / clock_out_time: 标准上下班打卡时间（弹性制可不填）
      - flexible_minutes: 弹性分钟数，在此范围内打卡不计迟到（固定制为0）
      - work_hours_per_day: 标准工作时长（小时），用于计算加班
      - overtime_weekday_rate: 工作日加班倍率（法定最低1.5倍）
      - overtime_weekend_rate: 周末加班倍率（法定最低2.0倍）
      - overtime_holiday_rate: 法定节假日加班倍率（法定最低3.0倍）
      - require_gps / gps_latitude / gps_longitude / gps_radius_meters:
          GPS 打卡配置，require_gps=True 时强制验证打卡坐标在指定范围内
      - require_wifi / wifi_ssid: WiFi 打卡配置，打卡时必须连接指定 SSID
      - require_photo: 是否要求拍照打卡（人脸识别或现场照片留存）
    """
    name: str = Field(..., max_length=100, description="规则名称")
    work_hour_type: WorkHourType            # 工时制度类型枚举
    location_id: Optional[int] = None       # 适用地点（None=不限地点）
    department_id: Optional[int] = None     # 适用部门（None=不限部门）
    clock_in_time: Optional[time] = None    # 标准上班打卡时间（弹性制可不设）
    clock_out_time: Optional[time] = None   # 标准下班打卡时间
    flexible_minutes: int = Field(0, ge=0, description="弹性分钟数")  # 迟到容忍分钟数
    work_hours_per_day: Decimal = Field(Decimal("8.0"), ge=0, le=24)  # 标准工时（小时）
    overtime_weekday_rate: Decimal = Field(Decimal("1.5"), ge=1)   # 工作日加班倍率（≥1.5）
    overtime_weekend_rate: Decimal = Field(Decimal("2.0"), ge=1)   # 周末加班倍率（≥2.0）
    overtime_holiday_rate: Decimal = Field(Decimal("3.0"), ge=1)   # 节假日加班倍率（≥3.0）
    require_gps: bool = False               # 是否启用 GPS 打卡验证
    require_wifi: bool = False              # 是否启用 WiFi 打卡验证
    require_photo: bool = False             # 是否要求拍照打卡
    gps_latitude: Optional[Decimal] = Field(None, ge=-90, le=90)      # 打卡中心点纬度
    gps_longitude: Optional[Decimal] = Field(None, ge=-180, le=180)   # 打卡中心点经度
    gps_radius_meters: Optional[int] = Field(None, ge=0)              # GPS 有效打卡半径（米）
    wifi_ssid: Optional[str] = Field(None, max_length=100)             # 允许打卡的 WiFi SSID
    priority: int = Field(100, ge=1, le=9999, description="规则优先级（数字越小越优先）")
    effective_date: date = Field(default_factory=date.today, description="规则生效日期")
    extra_config: dict = Field(default_factory=dict, description="规则扩展配置（午休/员工类型/节假日/范围等）")
    is_active: bool = True


class AttendanceRuleCreate(AttendanceRuleBase):
    """
    用途: POST /api/v1/attendance/rules 新建考勤规则的请求体
    业务: 直接继承 AttendanceRuleBase，无额外字段
    """
    pass


class AttendanceRuleUpdate(BaseModel):
    """
    用途: PATCH /api/v1/attendance/rules/{id} 更新考勤规则的请求体
    业务: 所有字段均为可选，只传需要修改的字段（部分更新语义）
    """
    name: Optional[str] = Field(None, max_length=100)
    work_hour_type: Optional[WorkHourType] = None
    location_id: Optional[int] = None
    department_id: Optional[int] = None
    clock_in_time: Optional[time] = None
    clock_out_time: Optional[time] = None
    flexible_minutes: Optional[int] = Field(None, ge=0)
    work_hours_per_day: Optional[Decimal] = Field(None, ge=0, le=24)
    overtime_weekday_rate: Optional[Decimal] = Field(None, ge=1)
    overtime_weekend_rate: Optional[Decimal] = Field(None, ge=1)
    overtime_holiday_rate: Optional[Decimal] = Field(None, ge=1)
    require_gps: Optional[bool] = None
    require_wifi: Optional[bool] = None
    require_photo: Optional[bool] = None
    gps_latitude: Optional[Decimal] = Field(None, ge=-90, le=90)
    gps_longitude: Optional[Decimal] = Field(None, ge=-180, le=180)
    gps_radius_meters: Optional[int] = Field(None, ge=0)
    wifi_ssid: Optional[str] = Field(None, max_length=100)
    priority: Optional[int] = Field(None, ge=1, le=9999)
    effective_date: Optional[date] = None
    extra_config: Optional[dict] = None
    is_active: Optional[bool] = None


class AttendanceRuleOut(AttendanceRuleBase):
    """
    用途: 考勤规则详情/列表接口的响应体，从 ORM AttendanceRule 模型序列化
    业务: 继承 AttendanceRuleBase 所有字段，增加 id 和创建时间
    ConfigDict from_attributes=True: 允许从 SQLAlchemy ORM 实例直接构造
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    updated_at: Optional[datetime] = None
    created_at: datetime


class AttendanceRuleConflictOut(BaseModel):
    id: int
    name: str
    reason: str


class AttendanceRuleToggle(BaseModel):
    is_active: bool


class AttendanceRuleCopyIn(BaseModel):
    name: Optional[str] = Field(None, max_length=100)


class AttendanceRuleLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    rule_id: int
    action: str
    operator_id: Optional[int] = None
    operator_name: Optional[str] = None
    content: str
    created_at: datetime


# ===================================================================
# ShiftSchedule — 班次排班
# ===================================================================

class ShiftScheduleBase(BaseModel):
    """
    用途: 班次排班写操作 Schema 的基类
    业务: 为特定员工在特定日期分配一个班次
    字段说明:
      - employee_id: 被排班的员工 ID
      - date: 排班日期（YYYY-MM-DD）
      - shift_type: 班次类型（如早班/中班/晚班），影响打卡时间判定
      - rule_id: 关联的考勤规则 ID（决定该班次的打卡要求和加班倍率）
      - start_time / end_time: 该排班的实际上下班时间（可覆盖规则中的默认时间）
    """
    employee_id: int
    date: date
    shift_type: ShiftType      # 班次类型枚举（早班/中班/晚班/白班等）
    rule_id: int               # 关联的考勤规则 ID
    start_time: Optional[time] = None  # 班次开始时间（覆盖规则默认值）
    end_time: Optional[time] = None    # 班次结束时间（覆盖规则默认值）


class ShiftScheduleCreate(ShiftScheduleBase):
    """
    用途: POST /api/v1/attendance/schedules 单条排班的请求体
    业务: 直接继承 ShiftScheduleBase，无额外字段
    """
    pass


class ShiftScheduleBatchCreate(BaseModel):
    """
    用途: POST /api/v1/attendance/schedules/batch 批量排班的请求体
    业务: 一次性为多名员工在指定日期范围内批量创建相同班次排班
    字段说明:
      - employee_ids: 需要排班的员工 ID 列表（至少1个）
      - start_date / end_date: 排班日期范围（含首尾，闭区间）
      - shift_type / rule_id: 应用于该时间段所有日期的班次和规则
      - start_time / end_time: 可选，覆盖规则中的默认上下班时间
    注意: Service 层会为 [start_date, end_date] 范围内的每个日历日
          为每个员工创建一条 ShiftSchedule 记录（可能跳过节假日，取决于业务配置）
    """
    employee_ids: List[int] = Field(..., min_length=1)   # 至少指定一名员工
    start_date: date
    end_date: date
    shift_type: ShiftType
    rule_id: int
    start_time: Optional[time] = None
    end_time: Optional[time] = None


class ShiftScheduleUpdate(BaseModel):
    """
    用途: PATCH /api/v1/attendance/schedules/{id} 更新单条排班的请求体
    业务: 调整员工某天的班次类型、规则或具体时间
    """
    shift_type: Optional[ShiftType] = None
    rule_id: Optional[int] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None


class ShiftScheduleOut(ShiftScheduleBase):
    """
    用途: 排班详情/列表接口的响应体，从 ORM ShiftSchedule 模型序列化
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


# ===================================================================
# ClockIn / ClockOut — 打卡请求
# ===================================================================

class ClockInRequest(BaseModel):
    project_context: Optional[str] = Field(None, max_length=64)
    """
    用途: POST /api/v1/attendance/clock-in 员工上班打卡的请求体
    业务: 员工通过 App 或其他终端发起上班打卡，可携带位置/WiFi/照片信息
    字段说明:
      - gps_latitude / gps_longitude: 打卡时的 GPS 坐标（require_gps=True 时必须）
      - wifi_ssid: 打卡时连接的 WiFi 名称（require_wifi=True 时必须）
      - photo_url: 打卡照片的存储 URL（require_photo=True 时必须）
      - is_mock_location: 是否使用虚拟定位（True=异常，Service 层应标记并可能拒绝）
      - source: 打卡来源（app=手机App / web=网页端 / 其他=硬件打卡机等）
    注意: 打卡员工身份从 JWT Token 中解析，不在请求体中传入
    """
    gps_latitude: Optional[Decimal] = Field(None, ge=-90, le=90)
    gps_longitude: Optional[Decimal] = Field(None, ge=-180, le=180)
    location_name: Optional[str] = Field(None, max_length=255, description="上班打卡地点名称（地名）")
    location_address: Optional[str] = Field(None, max_length=255, description="上班打卡地点地址")
    device_name: Optional[str] = Field(None, max_length=100, description="设备名称（如 iPhone 15）")
    device_model: Optional[str] = Field(None, max_length=100, description="设备型号（如 Pixel 8）")
    device_os: Optional[str] = Field(None, max_length=50, description="设备系统（如 Android 14/iOS 18）")
    device_id: Optional[str] = Field(None, max_length=120, description="设备标识（可选）")
    wifi_ssid: Optional[str] = Field(None, max_length=100)
    photo_url: Optional[str] = Field(None, max_length=500)
    is_mock_location: bool = False   # True=检测到虚拟定位，应标记为异常
    source: ClockSource = ClockSource.app


class ClockOutRequest(BaseModel):
    project_context: Optional[str] = Field(None, max_length=64)
    """
    用途: POST /api/v1/attendance/clock-out 员工下班打卡的请求体
    业务: 结构与 ClockInRequest 完全相同，仅语义不同（记录下班时间）
    字段说明: 同 ClockInRequest，所有字段含义一致
    """
    gps_latitude: Optional[Decimal] = Field(None, ge=-90, le=90)
    gps_longitude: Optional[Decimal] = Field(None, ge=-180, le=180)
    location_name: Optional[str] = Field(None, max_length=255, description="下班打卡地点名称（地名）")
    location_address: Optional[str] = Field(None, max_length=255, description="下班打卡地点地址")
    device_name: Optional[str] = Field(None, max_length=100, description="设备名称（如 iPhone 15）")
    device_model: Optional[str] = Field(None, max_length=100, description="设备型号（如 Pixel 8）")
    device_os: Optional[str] = Field(None, max_length=50, description="设备系统（如 Android 14/iOS 18）")
    device_id: Optional[str] = Field(None, max_length=120, description="设备标识（可选）")
    wifi_ssid: Optional[str] = Field(None, max_length=100)
    photo_url: Optional[str] = Field(None, max_length=500)
    is_mock_location: bool = False
    source: ClockSource = ClockSource.app


class ClockValidateRequest(BaseModel):
    """
    用途: POST /api/v1/attendance/clock/validate 打卡前规则校验
    业务: 按当前员工命中的考勤规则预判打卡状态，并校验位置/WiFi/设备等打卡方式
    """
    employee_id: Optional[int] = None
    phase: Optional[Literal["clock_in", "clock_out"]] = None
    target_date: Optional[date] = None
    punch_time: Optional[datetime] = None
    gps_latitude: Optional[Decimal] = Field(None, ge=-90, le=90)
    gps_longitude: Optional[Decimal] = Field(None, ge=-180, le=180)
    wifi_ssid: Optional[str] = Field(None, max_length=100)
    photo_url: Optional[str] = Field(None, max_length=500)
    is_mock_location: bool = False
    source: ClockSource = ClockSource.app


# ===================================================================
# AttendanceRecord — 考勤记录
# ===================================================================

class AttendanceRecordOut(BaseModel):
    project_segments: list[dict] = Field(default_factory=list)
    """
    用途: GET /api/v1/attendance/records 考勤记录查询的响应体
    业务: 描述员工某日的完整打卡记录，由系统在打卡时自动创建
    字段分组:
      - 上班打卡信息: clock_in_time / clock_in_gps_lat / clock_in_gps_lng /
                      clock_in_wifi_ssid / clock_in_photo_url
      - 下班打卡信息: clock_out_time / clock_out_gps_lat / clock_out_gps_lng /
                      clock_out_wifi_ssid / clock_out_photo_url
      - 计算结果: work_hours（实际工时）/ overtime_hours（加班时长）
      - 状态信息:
          status: 考勤结果状态（正常/迟到/早退/缺勤/请假等）
          anomaly_type: 异常类型描述字符串（无异常时为 None）
          correction_status: 该记录是否有修正申请（pending/approved/rejected/none）
          correction_note: 修正申请的说明文字
      - 打卡来源: source（app/web/硬件等）
    ConfigDict from_attributes=True: 允许从 ORM AttendanceRecord 直接构造
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    date: date
    clock_in_time: Optional[datetime] = None        # 上班打卡时间（datetime 含时区）
    clock_out_time: Optional[datetime] = None       # 下班打卡时间
    clock_in_gps_lat: Optional[Decimal] = None      # 上班打卡 GPS 纬度
    clock_in_gps_lng: Optional[Decimal] = None      # 上班打卡 GPS 经度
    clock_in_location: Optional[str] = None         # 上班打卡地点（地名/地址）
    clock_in_device: Optional[str] = None           # 上班打卡设备识别信息
    clock_in_wifi_ssid: Optional[str] = None        # 上班打卡 WiFi SSID
    clock_in_photo_url: Optional[str] = None        # 上班打卡照片 URL
    clock_out_gps_lat: Optional[Decimal] = None     # 下班打卡 GPS 纬度
    clock_out_gps_lng: Optional[Decimal] = None     # 下班打卡 GPS 经度
    clock_out_location: Optional[str] = None        # 下班打卡地点（地名/地址）
    clock_out_device: Optional[str] = None          # 下班打卡设备识别信息
    clock_out_wifi_ssid: Optional[str] = None       # 下班打卡 WiFi SSID
    clock_out_photo_url: Optional[str] = None       # 下班打卡照片 URL
    is_mock_location: bool                          # 是否检测到虚拟定位
    expected_hours: Optional[Decimal] = None        # 应出勤工时（按当日规则上下班时间扣除休息时间计算）
    work_hours: Optional[Decimal] = None            # 实际工作时长（小时）
    overtime_hours: Optional[Decimal] = None        # 加班时长（小时，需提交申请确认）
    status: AttendanceStatus                        # 考勤状态枚举
    display_status: Optional[str] = None            # 展示状态（可组合，如"迟到/早退"）
    clock_in_status: Optional[str] = None            # 上班卡展示状态（正常/迟到/缺卡等）
    clock_out_status: Optional[str] = None           # 下班卡展示状态（正常/早退/缺卡等）
    location_status: Optional[str] = None            # 打卡位置展示状态（范围内/打卡位置异常）
    location_abnormal: bool = False                  # 是否存在打卡位置异常
    clock_in_location_status: Optional[str] = None    # 上班打卡位置状态
    clock_out_location_status: Optional[str] = None   # 下班打卡位置状态
    clock_in_location_abnormal: bool = False          # 上班打卡是否位置异常
    clock_out_location_abnormal: bool = False         # 下班打卡是否位置异常
    anomaly_type: Optional[str] = None              # 异常类型说明（如"迟到30分钟"）
    correction_status: CorrectionStatus             # 修正申请状态
    correction_note: Optional[str] = None           # 修正申请说明
    source: ClockSource                             # 打卡来源
    created_at: datetime


class AttendanceRecordQuery(BaseModel):
    """
    用途: GET /api/v1/attendance/records 考勤记录查询的过滤参数（Query String）
    业务: 支持多维度筛选，常用于 HR 查看部门考勤或员工自查
    字段说明:
      - employee_id: 查询指定员工（None=查所有员工，需有 HR 权限）
      - department_id: 查询指定部门下所有员工的考勤
      - start_date / end_date: 日期范围过滤（含首尾）
      - status: 按单个考勤状态过滤（兼容旧调用）
      - statuses: 按多个考勤状态过滤，和 status 使用同一套 AttendanceStatus 枚举
    分页参数: page 从1开始，page_size 最大1000
    """
    employee_id: Optional[int] = None
    department_id: Optional[int] = None
    keyword: Optional[str] = None
    work_hour_type: Optional[WorkHourType] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[AttendanceStatus] = None
    statuses: Optional[List[AttendanceStatus]] = None
    anomalies_only: bool = False
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=1000)


class CorrectionRequest(BaseModel):
    """
    用途: POST /api/v1/attendance/records/{id}/correct 考勤修正（旧版补卡）申请的请求体
    业务: 员工对某条考勤异常记录提交修正申请，说明实际情况，由 HR 审批
    """
    record_id: int
    correction_note: str = Field(..., min_length=1, max_length=500)


class CorrectionApproval(BaseModel):
    """
    用途: POST /api/v1/attendance/corrections/{id}/approve 考勤修正审批的请求体
    业务: HR 或管理员对员工的考勤修正申请进行审批
    """
    record_id: int
    approved: bool
    note: Optional[str] = Field(None, max_length=500)


class AttendanceAppealCreate(BaseModel):
    """考勤异常申诉提交"""
    record_id: int
    reason: str = Field(..., min_length=1, max_length=500)
    appeal_time: Optional[str] = Field(None, description="实际打卡时间 HH:mm")


class AttendanceAppealReview(BaseModel):
    """考勤异常申诉审批"""
    approved: bool
    note: Optional[str] = Field(None, max_length=500)


class AttendanceOutsideApprovalCreate(BaseModel):
    """
    用途: POST /api/v1/attendance/outside-approval 外出打卡审批的请求体
    业务: 员工提交外出打卡的地址、照片和说明，后端创建通用审批实例
    字段说明:
      - address: 外出打卡的详细地址
      - place_title: 腾讯地点联想返回的标题/地点名
      - latitude / longitude: 当前位置坐标
      - client_name: 拜访客户名称
      - remark: 补充说明
      - photo_url: 水印照片或拍照结果 URL / data URL
      - occurred_at: 业务发生时间，统一使用带时区时间戳
    """
    address: str = Field(..., min_length=1, max_length=500)
    place_title: Optional[str] = Field(None, max_length=200)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    client_name: Optional[str] = Field(None, max_length=100)
    remark: Optional[str] = Field(None, max_length=500)
    photo_url: str = Field(..., min_length=1)
    occurred_at: datetime


class AttendanceOutsideApprovalOut(BaseModel):
    """
    用途: POST /api/v1/attendance/outside-approval 的响应体
    业务: 返回审批实例 ID 及其 durable payload，供移动端和管理端展示
    """
    model_config = ConfigDict(from_attributes=True)

    approval_instance_id: int
    status: str
    summary: str
    address: str
    latitude: float
    longitude: float
    client_name: Optional[str] = None
    remark: Optional[str] = None
    photo_url: str
    created_at: datetime


class AttendanceMobileRuntimeOut(BaseModel):
    """
    用途: GET /api/v1/attendance/my/runtime 移动端考勤运行时数据
    业务: 为移动端页面提供当天考勤规则、今日记录和可打卡状态
    """
    employee_id: int
    employee_name: str
    timezone: str = "Asia/Shanghai"
    date: date
    rule_id: Optional[int] = None
    rule_name: Optional[str] = None
    punch_window_start: Optional[str] = None
    punch_window_end: Optional[str] = None
    clock_in_required: bool = True
    clock_out_required: bool = True
    can_clock_in: bool = True
    can_clock_out: bool = True
    today_stats: dict[str, int] = Field(default_factory=dict)
    today_record: Optional[AttendanceRecordOut] = None
    raw_runtime: dict[str, Any] = Field(default_factory=dict)


class MobileDashboardRangeOut(BaseModel):
    type: Literal["day", "week", "month"]
    target_date: date
    start_date: date
    end_date: date
    year: int
    month: int


class MobileDashboardApplicationItemOut(BaseModel):
    key: str
    label: str
    module: str
    pending: int = 0


class MobileDashboardApplicationsOut(BaseModel):
    items: list[MobileDashboardApplicationItemOut] = Field(default_factory=list)
    record_count: int = 0


class MobileDashboardStatsOut(BaseModel):
    normal_days: int = 0
    abnormal_days: int = 0
    late_count: int = 0
    early_count: int = 0
    missed_count: int = 0
    absent_count: int = 0
    leave_count: int = 0
    correction_count: int = 0
    outside_count: int = 0
    business_trip_count: int = 0
    field_work_count: int = 0
    overtime_minutes: int = 0
    overtime_hours: float = 0


class MobileDashboardMonthlyAbnormalOut(BaseModel):
    pending_days: int = 0
    has_today_abnormal: bool = False
    today_status: str = "未打卡"
    today_anomaly: Optional[str] = None
    latest_date: Optional[date] = None
    latest_status: Optional[str] = None
    latest_anomaly: Optional[str] = None


class MobileDashboardDailyOut(BaseModel):
    clock_in_time: Optional[datetime] = None
    clock_out_time: Optional[datetime] = None
    status: str
    is_abnormal: bool = False
    anomaly_type: Optional[str] = None
    clock_in_status: Optional[str] = None
    clock_out_status: Optional[str] = None
    location_status: Optional[str] = None
    location_abnormal: bool = False
    work_minutes: int = 0
    expected_minutes: int = 0


class MobileDashboardAttendanceRecordDetailOut(BaseModel):
    id: Optional[int] = None
    date: date
    status: str
    display_status: str
    anomaly_type: Optional[str] = None
    is_abnormal: bool = False
    is_rest_day: bool = False
    day_type: str = "workday"
    day_type_label: str = "工作日"
    clock_in_time: Optional[datetime] = None
    clock_out_time: Optional[datetime] = None
    clock_in_status: Optional[str] = None
    clock_out_status: Optional[str] = None
    clock_in_location: Optional[str] = None
    clock_out_location: Optional[str] = None
    clock_in_gps_lat: Optional[Decimal] = None
    clock_in_gps_lng: Optional[Decimal] = None
    clock_out_gps_lat: Optional[Decimal] = None
    clock_out_gps_lng: Optional[Decimal] = None
    clock_in_device: Optional[str] = None
    clock_out_device: Optional[str] = None
    correction_status: Optional[str] = None
    correction_note: Optional[str] = None
    work_minutes: int = 0
    overtime_minutes: int = 0
    source: Optional[str] = None


class MobileDashboardLeaveDetailOut(BaseModel):
    id: str
    date: date
    type: str
    label: str
    status: str = "active"
    minutes: int = 0
    days: float = 0
    source_module: Optional[str] = None
    source_business_id: Optional[int] = None
    source_instance_id: Optional[int] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    reason: Optional[str] = None
    punch_type: Optional[str] = None
    punch_type_label: Optional[str] = None
    punch_time: Optional[datetime] = None


class MobileDashboardOvertimeDetailOut(BaseModel):
    id: str
    date: date
    display_date_label: Optional[str] = None
    date_type: str = "workday"
    date_type_label: str = "工作日"
    minutes: int = 0
    hours: float = 0
    settlement: str = "none"
    settlement_label: str = "无核算方式"
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    status: str = "approved"
    reason: Optional[str] = None
    source: Optional[str] = None
    source_note: Optional[str] = None


class MobileDashboardOvertimeMonthOut(BaseModel):
    id: str
    year: int
    month: int
    label: str
    minutes: int = 0
    hours: float = 0
    source_count: int = 0


class MobileDashboardDetailCountsOut(BaseModel):
    attendance: dict[str, int] = Field(default_factory=dict)
    leave: dict[str, int] = Field(default_factory=dict)
    overtime_date_type: dict[str, int] = Field(default_factory=dict)
    overtime_settlement: dict[str, int] = Field(default_factory=dict)


class MobileDashboardDetailsOut(BaseModel):
    attendance_records: list[MobileDashboardAttendanceRecordDetailOut] = Field(default_factory=list)
    leave_items: list[MobileDashboardLeaveDetailOut] = Field(default_factory=list)
    overtime_items: list[MobileDashboardOvertimeDetailOut] = Field(default_factory=list)
    overtime_month_items: list[MobileDashboardOvertimeMonthOut] = Field(default_factory=list)
    counts: MobileDashboardDetailCountsOut = Field(default_factory=MobileDashboardDetailCountsOut)


class MobileDashboardOut(BaseModel):
    """
    用途: GET /api/v1/attendance/mobile/dashboard 移动端考勤看板
    业务: 返回当前用户在指定范围内的真实统计、待办申请入口、月异常和当日状态。
    """
    range: MobileDashboardRangeOut
    applications: MobileDashboardApplicationsOut
    stats: MobileDashboardStatsOut
    monthly_abnormal: MobileDashboardMonthlyAbnormalOut
    daily: MobileDashboardDailyOut
    details: MobileDashboardDetailsOut = Field(default_factory=MobileDashboardDetailsOut)


# ===================================================================
# PunchCorrectionEligibility — 补卡可用性内部查询
# ===================================================================

class PunchCorrectionEligibilityRequest(BaseModel):
    """
    用途: POST /api/v1/attendance/internal/punch-correction/eligibility
    业务: 审批模块发起补卡审批前，只读查询指定员工和日期是否允许补卡。
    """
    employee_id: int = Field(..., ge=1)
    target_date: str = Field(..., min_length=8, max_length=10, description="YYYY-MM-DD 或 YYYYMMDD")
    punch_types: list[Literal["check_in", "check_out"]] = Field(default_factory=list)
    include_window_days: bool = True
    timezone: str = Field("Asia/Shanghai", min_length=1, max_length=64)
    request_at: Optional[datetime] = None
    approval_context: dict[str, Any] = Field(default_factory=dict)


class PunchCorrectionEligibilityResponse(BaseModel):
    """
    用途: 补卡可用性内部查询的统一业务响应外壳。
    业务是否允许补卡由 biz_success / biz_code / data.can_apply 表达。
    """
    request_id: str
    biz_success: bool
    biz_code: str
    message: str
    data: Optional[dict[str, Any]] = None


# ===================================================================
# WorkCalendar — 工作日历
# ===================================================================

class WorkCalendarBase(BaseModel):
    """
    用途: 工作日历写操作 Schema 的基类
    业务: 维护年度工作日历，标记节假日和调休工作日，供考勤和薪资计算使用
    字段说明:
      - date: 日历日期
      - location_id: 关联的工作地点（None=适用所有地点，某些地方性节假日需指定地点）
      - day_type: 日期类型（工作日/周末/法定节假日/调休工作日），影响加班倍率计算
      - holiday_name: 节假日名称（如"元旦"、"春节"），day_type=holiday 时应填写
    """
    date: date
    location_id: Optional[int] = None    # 地点限定（None=所有地点通用）
    day_type: DayType                    # 日期类型枚举（工作日/节假日/调休等）
    holiday_name: Optional[str] = Field(None, max_length=100)  # 节假日名称


class WorkCalendarCreate(WorkCalendarBase):
    """
    用途: POST /api/v1/attendance/calendar 创建单条工作日历条目的请求体
    """
    pass


class WorkCalendarBatchCreate(BaseModel):
    """
    用途: POST /api/v1/attendance/calendar/batch 批量创建工作日历条目的请求体
    业务: HR 在年初一次性导入全年日历（含所有法定节假日和调休工作日）
    entries: 日历条目列表（至少1条）
    """
    entries: List[WorkCalendarCreate] = Field(..., min_length=1)


class WorkCalendarUpdate(BaseModel):
    """
    用途: PATCH /api/v1/attendance/calendar/{id} 更新工作日历条目的请求体
    业务: 修正日历条目的类型或节假日名称（如政策调整导致调休日期变更）
    """
    day_type: Optional[DayType] = None
    holiday_name: Optional[str] = Field(None, max_length=100)


class WorkCalendarOut(WorkCalendarBase):
    """
    用途: 工作日历查询接口的响应体，从 ORM WorkCalendar 模型序列化
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


# ===================================================================
# AttendanceMonthSummary — 月度考勤汇总
# ===================================================================

class MonthSummaryOut(BaseModel):
    """
    用途: GET /api/v1/attendance/summary 月度考勤汇总查询的响应体
    业务: 员工某月的考勤数据汇总，是薪资计算的重要输入数据
    字段说明（各计数/时长字段含义）:
      - work_days: 该月应出勤工作日天数（不含节假日/周末）
      - actual_days: 实际出勤天数
      - late_count: 迟到次数
      - early_leave_count: 早退次数
      - absent_count: 缺勤天数
      - missed_clock_count: 漏打卡次数（未打卡但非缺勤）
      - overtime_weekday_hours: 工作日加班合计小时数（倍率1.5）
      - overtime_weekend_hours: 周末加班合计小时数（倍率2.0）
      - overtime_holiday_hours: 法定节假日加班合计小时数（倍率3.0）
      - leave_days: 当月请假合计天数（含各类假期）
      - business_trip_days: 出差天数
      - anomaly_count: 异常记录条数（含迟到/早退/漏打卡等所有异常）
      - status: 汇总状态（draft=草稿/confirmed=员工确认/locked=HR锁定不可改）
      - locked_at: 汇总锁定时间（锁定后薪资计算可引用此数据）
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    year: int
    month: int
    work_days: int                                # 应出勤工作日天数
    actual_days: int                              # 实际出勤天数
    late_count: int                               # 迟到次数
    early_leave_count: int                        # 早退次数
    absent_count: int                             # 缺勤天数
    missed_clock_count: int                       # 漏打卡次数
    overtime_weekday_hours: Decimal               # 工作日加班（倍率1.5）
    overtime_weekend_hours: Decimal               # 周末加班（倍率2.0）
    overtime_holiday_hours: Decimal               # 节假日加班（倍率3.0）
    leave_days: Decimal                           # 请假总天数
    business_trip_days: Decimal                   # 出差天数
    anomaly_count: int                            # 总异常次数
    status: MonthlySummaryStatus                  # 汇总状态枚举
    locked_at: Optional[datetime] = None          # 锁定时间（None=未锁定）
    created_at: datetime


class MonthSummaryGenerateRequest(BaseModel):
    """
    用途: POST /api/v1/attendance/summary/generate 触发月度汇总生成的请求体
    业务: HR 手动触发（或定时任务调用），统计指定月份的考勤数据并生成汇总记录
    字段说明:
      - year / month: 汇总的目标年月（2020-2100）
      - employee_ids: 指定员工列表（None=全员汇总），适用于补算个别员工
    """
    year: int = Field(..., ge=2020, le=2100)
    month: int = Field(..., ge=1, le=12)
    employee_ids: Optional[List[int]] = Field(None, description="指定员工, 为空则全员")


class MonthSummaryConfirm(BaseModel):
    """
    用途: POST /api/v1/attendance/summary/confirm 员工确认月度汇总的请求体
    业务: 员工对自己的月度考勤汇总进行确认，状态从 draft 变为 confirmed
    确认后 HR 才可以锁定汇总进行薪资计算
    """
    summary_id: int    # 需要确认的月度汇总记录 ID


class MonthSummaryLock(BaseModel):
    """
    用途: POST /api/v1/attendance/summary/lock HR锁定月度汇总的请求体
    业务: 汇总锁定后不可修改，薪资计算模块可以安全引用此数据
    锁定后如需修改需要先解锁（需管理员权限），再重新汇总和锁定
    """
    year: int = Field(..., ge=2020, le=2100)
    month: int = Field(..., ge=1, le=12)


class MonthlyReportTaskCreate(BaseModel):
    """
    用途: POST /api/v1/attendance/monthly-report/tasks 创建月报异步生成任务
    业务: 查询条件不包含分页，任务一次生成完整月报结果，页面翻页和导出复用该结果。
    """
    start_date: Optional[date] = Field(None, description="开始日期，默认当月1日")
    end_date: Optional[date] = Field(None, description="结束日期，默认今天")
    department_id: Optional[int] = Field(None, description="部门ID")
    keyword: Optional[str] = Field(None, description="姓名/工号关键词")
    status: Optional[str] = Field(None, description="打卡状态")
    rule_id: Optional[int] = Field(None, description="规则ID")
    include_recent_left: bool = Field(True, description="是否包含离职90天内成员")
    force_regenerate: bool = Field(False, description="是否跳过短期缓存并重新生成")


# ===================================================================
# Overtime — 加班申请
# ===================================================================

class OvertimeApplicationCreate(BaseModel):
    """
    用途: POST /api/v1/attendance/overtime 提交加班申请的请求体
    业务: 员工预先或事后申请加班，需 HR/管理员审批确认后加班时长才计入薪资
    字段说明:
      - date: 加班日期
      - start_time / end_time: 加班时间段（当天内，不跨日）
      - reason: 加班原因说明（必填）
      - overtime_type: 加班类型，决定加班倍率：
          "weekday": 工作日加班（1.5倍）
          "weekend": 周末加班（2.0倍）
          "holiday": 法定节假日加班（3.0倍）
    加班时长由 Service 层根据 start_time 和 end_time 计算
    """
    date: date
    start_time: time
    end_time: time
    reason: str = Field(..., min_length=1, max_length=500)
    overtime_type: str = Field(
        ..., pattern="^(weekday|weekend|holiday)$", description="加班类型"
    )  # 正则限制只允许三个合法值


class OvertimeApplicationOut(BaseModel):
    """
    用途: 加班申请列表/详情接口的响应体，从 ORM OvertimeApplication 模型序列化
    业务: 包含申请的完整信息和审批状态
    hours: 申请加班时长（由 Service 层计算 end_time - start_time）
    approval_status: 审批状态（pending/approved/rejected）
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    employee_id: int
    date: date
    start_time: time
    end_time: time
    hours: Decimal                # 加班时长（小时，如1.5=一小时半）
    reason: str
    overtime_type: str            # weekday/weekend/holiday
    approval_status: str          # 审批状态字符串
    created_at: datetime


# ===================================================================
# PunchCorrectionRequest — 补卡申请（新版）
# ===================================================================

class PunchCorrectionCreate(BaseModel):
    """
    用途: POST /api/v1/attendance/punch-corrections 提交补卡申请的请求体
    业务: 员工漏打卡后提交补卡申请，经审批后系统补录打卡记录
    """
    employee_id: int
    correction_date: date
    punch_time: datetime
    punch_type: str = Field(
        ..., pattern="^(check_in|check_out)$", description="check_in / check_out"
    )
    reason: str = Field(..., min_length=1, max_length=500)


class PunchCorrectionOut(BaseModel):
    """
    用途: 补卡申请列表/详情接口的响应体，从 ORM PunchCorrectionRequest 模型序列化
    业务: 包含补卡申请的完整信息和审批结果
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    employee_name: Optional[str] = None
    correction_date: date
    punch_time: datetime
    punch_type: str
    reason: str
    status: str
    reviewer_id: Optional[int] = None
    review_comment: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime


class PunchCorrectionReview(BaseModel):
    """
    用途: POST /api/v1/attendance/punch-corrections/{id}/review 补卡审批操作的请求体
    业务: HR 或管理员对员工的补卡申请进行审批
    """
    status: PunchCorrectionStatus = Field(..., description="approved / rejected")
    review_comment: Optional[str] = Field(None, max_length=500)


# ===================================================================
# Common pagination response
# ===================================================================

class PaginatedResponse(BaseModel):
    """
    用途: 所有分页接口的通用响应包装体
    业务: 统一分页响应结构，前端据此计算总页数和渲染分页控件
    total_pages = ceil(total / page_size)
    items 字段使用 list（无具体类型），实际内容类型由调用方动态确定
    """
    total: int        # 符合查询条件的总记录数
    page: int         # 当前页码（从1开始）
    page_size: int    # 每页记录数
    items: list       # 当前页数据
