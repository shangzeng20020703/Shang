"""
行政管理模块 - Pydantic Schemas
=================================

模块名称: admin.py
所属系统: 售后管理系统 - 行政管理模块

作用:
    定义行政管理的6个子模块的请求/响应数据结构：

    1. Announcement（公告管理）:
       发布公司通知、政策文件、活动公告等，支持定向推送（全员/部门/地点）
       和多渠道推送（企业微信/APP），可设置置顶和过期时间。

    2. Asset（资产管理）:
       登记公司固定资产（电脑、手机、工服、工牌、工具等），追踪资产状态。
       通过 AssetCheckout（资产出入库）管理资产的领用和归还流程：
         - 领用: AssetCheckoutCreate → 资产状态变为 checked_out
         - 归还: AssetReturnRequest → 记录归还日期和资产状况 → 状态变回 available
         - 维修: 状态变为 maintenance（需手动维护）
         - 报废: 状态变为 retired

    3. Visitor（访客管理）:
       预约登记外来访客，记录来访人信息、接待人、来访事由。
       访客到达时执行"签到"（VisitorCheckInRequest，记录身份证和临时通行证），
       离开时执行"签出"（VisitorCheckOutRequest，记录离开时间）。

    4. BookingRoom（会议室预约）:
       管理会议室的预约申请，防止时间冲突。
       RoomAvailabilityQuery / RoomAvailabilitySlot 用于查询某会议室特定日期的空闲时段，
       service 层会在创建预约前检查时间冲突，冲突时返回 409 错误。

    5. BookingVehicle（用车申请）:
       管理公司车辆或外包司机的用车申请，含审批流程（approved/rejected）。
       申请审批通过后，行政人员指定司机（driver_assigned）。

    注意（架构说明）:
       Admin 模块在 API 层被拆分为 6 个独立路由文件：
         - admin.py（遗留合并文件，保留向后兼容）
         - admin_announcements.py → /admin/announcements/*
         - admin_assets.py        → /admin/assets/*
         - admin_rooms.py         → /admin/rooms/*
         - admin_vehicles.py      → /admin/vehicles/*
         - admin_visitors.py      → /admin/visitors/*
       所有 6 个路由文件共享同一个 service 层（services/admin.py）和此 schema 文件。

数据流向:
    前端表单 → *Create/*Update Schema（请求验证）
         ↓
    service 层（业务逻辑：冲突检查、状态机流转等）
         ↓
    ORM Model 持久化到 PostgreSQL
         ↓
    *Out Schema（响应序列化，from_attributes=True）→ 前端展示

被调用的 API 端点:
    公告:
        POST/GET           /admin/announcements        - 发布/列表
        GET/PUT/DELETE     /admin/announcements/{id}   - 详情/更新/删除
        POST               /admin/announcements/{id}/read - 标记已读
    资产:
        POST/GET           /admin/assets               - 登记/列表
        GET/PUT/DELETE     /admin/assets/{id}          - 详情/更新/删除
        POST               /admin/assets/{id}/checkout - 资产领用
        POST               /admin/assets/{id}/return   - 资产归还
    访客:
        POST/GET           /admin/visitors             - 预约登记/列表
        GET/PUT            /admin/visitors/{id}        - 详情/更新
        POST               /admin/visitors/{id}/check-in  - 访客签到
        POST               /admin/visitors/{id}/check-out - 访客签出
    会议室:
        POST/GET           /admin/rooms                - 预约/列表
        GET/PUT/DELETE     /admin/rooms/{id}           - 详情/更新/取消
        GET                /admin/rooms/availability   - 查询可用时段（时间冲突检查）
    用车:
        POST/GET           /admin/vehicles             - 申请/列表
        GET/PUT            /admin/vehicles/{id}        - 详情/更新
        POST               /admin/vehicles/{id}/approve - 审批用车申请

依赖:
    - pydantic v2: BaseModel, Field
    - Python: datetime as dt（使用别名 dt 以区分 datetime 类型和 datetime 模块）
    - 关联 models/admin.py: Announcement, Asset, AssetCheckout, Visitor,
      MeetingRoom（BookingRoom）, Vehicle（BookingVehicle）
"""

import datetime as dt
from typing import Optional

from pydantic import BaseModel, Field


# ───────── Announcement ─────────

class AnnouncementBase(BaseModel):
    """
    公告基础 Schema

    Announcement 用于 HR/行政向全体员工或特定范围发布通知，
    支持精准推送（按部门或地点）和多渠道推送（企业微信/APP）。

    字段说明:
        title:           公告标题（最多200字）
        content:         公告正文（富文本或纯文本）
        category:        公告分类，枚举:
                           notice（通知）: 日常行政通知
                           policy（制度）: 公司规章制度更新
                           activity（活动）: 团建、福利活动等
        target_type:     推送目标范围，枚举:
                           all（全员）: 向所有在职员工推送
                           department（部门）: 仅推送给指定部门员工
                           location（地点）: 仅推送给指定工作地点的员工
        target_ids_json: 当 target_type 非 all 时，目标部门或地点的 ID 列表（JSON 字符串）
                         示例: '[1, 3, 5]' 表示推送给 ID=1,3,5 的部门
        is_top:          是否置顶（置顶公告在列表最上方显示）
        push_wechat:     是否同步推送到企业微信（True=发送企业微信消息通知）
        push_app:        是否推送到移动端 APP（True=发送 APP 推送通知）
        expire_time:     公告过期时间（超过此时间后公告自动归档，不再在首页显示）
    """
    title: str = Field(..., max_length=200, description="公告标题")
    content: str = Field(..., description="公告内容")  # 支持富文本（HTML/Markdown）
    category: str = Field(
        ..., max_length=20, description="类别: notice/policy/activity"
    )  # notice=通知, policy=制度更新, activity=活动公告
    target_type: str = Field("all", max_length=20, description="目标范围: all/department/location")
    # all=全员, department=指定部门, location=指定地点
    target_ids_json: Optional[str] = Field(None, description="目标ID列表JSON")
    # target_type=department 时，填入部门 ID 列表；target_type=location 时填地点 ID 列表
    is_top: bool = Field(False, description="是否置顶")       # 置顶公告显示在列表最前面
    push_wechat: bool = Field(False, description="是否推送企业微信")  # 企业微信工作通知推送
    push_app: bool = Field(False, description="是否推送APP")          # 移动端 APP 消息推送
    expire_time: Optional[dt.datetime] = Field(None, description="过期时间")
    # 到期后公告不再在"有效公告"列表中显示，但历史记录仍可查询


class AnnouncementCreate(AnnouncementBase):
    """
    发布公告的请求体。

    对应 API: POST /admin/announcements

    publisher_id（发布人）由服务端从 JWT token 自动获取，
    publish_time 默认为当前时间。
    创建后 status 默认为 published（立即发布），
    后续版本可支持定时发布（status=scheduled）。
    """
    pass


class AnnouncementUpdate(BaseModel):
    """
    更新公告的请求体（PATCH 语义）。

    对应 API: PUT /admin/announcements/{id}

    已发布的公告可以修改内容和推送设置。
    注意：修改后不会重新发送通知，避免员工重复收到消息。
    """
    title: Optional[str] = Field(None, max_length=200)
    content: Optional[str] = None
    category: Optional[str] = Field(None, max_length=20)
    target_type: Optional[str] = Field(None, max_length=20)
    target_ids_json: Optional[str] = None
    is_top: Optional[bool] = None
    push_wechat: Optional[bool] = None
    push_app: Optional[bool] = None
    expire_time: Optional[dt.datetime] = None


class AnnouncementOut(AnnouncementBase):
    """
    返回给前端的公告数据（ORM 序列化）。

    字段说明:
        id:           公告主键
        publisher_id: 发布人员工 ID（从 JWT token 记录）
        publish_time: 实际发布时间（创建时自动记录）
        read_count:   已读人数统计（service 层统计）
        status:       公告状态（published=已发布 / expired=已过期 / retracted=已撤回）
        created_at:   记录创建时间
    """
    id: int
    publisher_id: int                        # 发布人员工 ID
    publish_time: Optional[dt.datetime]      # 发布时间（定时发布时为计划时间）
    read_count: int                          # 已读人数（员工点击查看后计入）
    status: str                              # published | expired | retracted
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class AnnouncementPage(BaseModel):
    """公告分页响应。"""
    total: int
    items: list[AnnouncementOut]


# ───────── Asset ─────────

class AssetBase(BaseModel):
    """
    资产基础 Schema

    Asset 记录公司固定资产的基础信息，
    通过 AssetCheckout 跟踪资产的领用和归还历史。

    资产状态流转:
        available（可用）→ checked_out（已领用，通过 AssetCheckout）
        checked_out → available（归还后恢复可用）
        available → maintenance（送修）→ available（维修完成）
        * → retired（报废，终态，不可撤销）

    字段说明:
        asset_no:       资产编号（唯一标识，如 "PC-2024-001"）
        name:           资产名称（如 "MacBook Pro 14"）
        category:       资产类别，枚举: 电脑/手机/工牌/工服/工具
        brand:          品牌（如 "Apple"、"联想"）
        model:          型号（如 "MacBook Pro 14 M3"）
        purchase_date:  购买日期（用于计算折旧）
        purchase_price: 购买价格（元）
        location_id:    资产存放地点 ID（关联 locations 表）
        status:         资产当前状态，枚举:
                          available:   在库可用（未被领用）
                          checked_out: 已被员工领用
                          maintenance: 送修中
                          retired:     已报废
    """
    asset_no: str = Field(..., max_length=50, description="资产编号")  # 唯一编号，如 PC-2024-001
    name: str = Field(..., max_length=100, description="资产名称")
    category: str = Field(
        ..., max_length=20, description="类别: 电脑/手机/工牌/工服/工具"
    )  # 资产类别枚举
    brand: Optional[str] = Field(None, max_length=50, description="品牌")
    model: Optional[str] = Field(None, max_length=100, description="型号")
    supplier: Optional[str] = Field(None, max_length=100, description="供应商")
    major_category: Optional[str] = Field(None, max_length=50, description="资产大类")
    minor_category: Optional[str] = Field(None, max_length=50, description="资产小类")
    asset_description: Optional[str] = Field(None, description="资产说明")
    quantity: int = Field(1, ge=1, description="数量")
    unit: Optional[str] = Field(None, max_length=20, description="单位")
    usage_department: Optional[str] = Field(None, max_length=100, description="使用部门")
    usage_employee_name_manual: Optional[str] = Field(None, max_length=50, description="手工维护的当前使用人")
    storage_location: Optional[str] = Field(None, max_length=100, description="资产存放地点")
    custodian: Optional[str] = Field(None, max_length=50, description="保管人")
    remark: Optional[str] = Field(None, description="备注")
    purchase_date: Optional[dt.date] = Field(None, description="购买日期")  # 采购日期（计算折旧用）
    acceptance_date: Optional[dt.date] = Field(None, description="验收日期")
    purchase_price: Optional[float] = Field(None, ge=0, description="购买价格")  # 采购价格（元）
    tax_rate: Optional[float] = Field(None, ge=0, description="税率")
    amount_excluding_tax: Optional[float] = Field(None, ge=0, description="不含税金额")
    residual_value: Optional[float] = Field(None, ge=0, description="残值")
    depreciation_months: Optional[int] = Field(None, ge=0, description="折旧月份")
    monthly_depreciation_amount: Optional[float] = Field(None, ge=0, description="每月折旧金额")
    accumulated_depreciation_months: Optional[int] = Field(None, ge=0, description="累计折旧月份")
    remaining_depreciation_months: Optional[int] = Field(None, ge=0, description="剩余折旧月份")
    accumulated_depreciation_amount: Optional[float] = Field(None, ge=0, description="累计折旧金额")
    remaining_depreciation_amount: Optional[float] = Field(None, ge=0, description="剩余折旧金额")
    current_month_depreciation_amount: Optional[float] = Field(None, ge=0, description="当月折旧金额")
    previous_month_accumulated_depreciation: Optional[float] = Field(None, ge=0, description="上月累计折旧")
    location_id: Optional[int] = Field(None, description="所在地点ID")  # 资产存放位置
    dongguan_ledger_no: Optional[str] = Field(None, max_length=100, description="东莞行政台账编号")
    matched_ledger_code: Optional[str] = Field(None, max_length=100, description="匹配行政台账最新编码")
    matched_ledger_department: Optional[str] = Field(None, max_length=100, description="匹配行政台账最新部门")
    inventory_loss_flag: bool = Field(False, description="是否盘亏")
    status: str = Field("available", description="状态: available/checked_out/maintenance/retired")
    # available=在库可用, checked_out=已领用, maintenance=维修中, retired=已报废
    scrap_date: Optional[dt.date] = Field(None, description="报废时间")
    scrap_reason: Optional[str] = Field(None, max_length=200, description="报废原因")


class AssetCreate(AssetBase):
    """
    登记新资产的请求体。

    对应 API: POST /admin/assets

    初始状态默认为 available（在库可用），
    待员工通过 /assets/{id}/checkout 领用后状态变为 checked_out。
    """
    pass


class AssetUpdate(BaseModel):
    """
    更新资产信息的请求体（PATCH 语义）。

    对应 API: PUT /admin/assets/{id}

    注意：asset_no 创建后不允许修改（资产编号具有唯一性，修改会影响历史记录追溯）。
    状态修改通常通过专用的 checkout/return 接口进行。
    """
    name: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=20)
    brand: Optional[str] = Field(None, max_length=50)
    model: Optional[str] = Field(None, max_length=100)
    supplier: Optional[str] = Field(None, max_length=100)
    major_category: Optional[str] = Field(None, max_length=50)
    minor_category: Optional[str] = Field(None, max_length=50)
    asset_description: Optional[str] = None
    quantity: Optional[int] = Field(None, ge=1)
    unit: Optional[str] = Field(None, max_length=20)
    usage_department: Optional[str] = Field(None, max_length=100)
    usage_employee_name_manual: Optional[str] = Field(None, max_length=50)
    storage_location: Optional[str] = Field(None, max_length=100)
    custodian: Optional[str] = Field(None, max_length=50)
    remark: Optional[str] = None
    purchase_date: Optional[dt.date] = None
    acceptance_date: Optional[dt.date] = None
    purchase_price: Optional[float] = Field(None, ge=0)
    tax_rate: Optional[float] = Field(None, ge=0)
    amount_excluding_tax: Optional[float] = Field(None, ge=0)
    residual_value: Optional[float] = Field(None, ge=0)
    depreciation_months: Optional[int] = Field(None, ge=0)
    monthly_depreciation_amount: Optional[float] = Field(None, ge=0)
    accumulated_depreciation_months: Optional[int] = Field(None, ge=0)
    remaining_depreciation_months: Optional[int] = Field(None, ge=0)
    accumulated_depreciation_amount: Optional[float] = Field(None, ge=0)
    remaining_depreciation_amount: Optional[float] = Field(None, ge=0)
    current_month_depreciation_amount: Optional[float] = Field(None, ge=0)
    previous_month_accumulated_depreciation: Optional[float] = Field(None, ge=0)
    location_id: Optional[int] = None
    dongguan_ledger_no: Optional[str] = Field(None, max_length=100)
    matched_ledger_code: Optional[str] = Field(None, max_length=100)
    matched_ledger_department: Optional[str] = Field(None, max_length=100)
    inventory_loss_flag: Optional[bool] = None
    status: Optional[str] = None  # 谨慎修改，资产状态变更应使用专用接口（checkout/return）
    scrap_date: Optional[dt.date] = None
    scrap_reason: Optional[str] = Field(None, max_length=200)


class AssetOut(AssetBase):
    """
    返回给前端的资产数据（ORM 序列化）。

    字段说明:
        id:         资产主键
        created_at: 资产登记时间
    """
    id: int
    created_at: dt.datetime
    usage_employee_id: Optional[int] = None
    usage_employee_name: Optional[str] = None
    usage_employee_no: Optional[str] = None

    model_config = {"from_attributes": True}


class AssetPage(BaseModel):
    """资产分页响应。"""
    total: int
    items: list[AssetOut]


# ───────── AssetCheckout ─────────

class AssetCheckoutBase(BaseModel):
    """
    资产出入库（领用记录）基础 Schema

    AssetCheckout 记录资产从仓库到员工的领用过程，
    以及从员工归还到仓库的流程。
    一条 AssetCheckout 记录对应一次"借出-归还"周期。

    字段说明:
        asset_id:             被领用资产的 ID
        employee_id:          领用员工的 ID（谁拿走了这个资产）
        checkout_date:        领用日期（资产离开仓库的日期）
        expected_return_date: 预计归还日期（超出此日期未归还系统可发送提醒）
    """
    asset_id: int = Field(..., description="资产ID")
    employee_id: int = Field(..., description="领用员工ID")  # 资产使用人
    checkout_date: dt.date = Field(..., description="领用日期")
    expected_return_date: Optional[dt.date] = Field(None, description="预计归还日期")
    # 为 None 表示长期使用（如工作服、工牌等一般不归还的资产）


class AssetCheckoutCreate(AssetCheckoutBase):
    """
    创建资产领用记录的请求体（资产出库）。

    对应 API: POST /admin/assets/{id}/checkout

    提交后：
      1. 创建 AssetCheckout 记录（status=checked_out）
      2. 更新 Asset.status 为 checked_out（不可再被其他人领用）

    注意：只有 status=available 的资产才能领用，
    service 层会校验资产状态，已被领用的资产无法重复领用。
    """
    pass


class AssetReturnRequest(BaseModel):
    """
    资产归还请求体（资产入库）。

    对应 API: POST /admin/assets/{id}/return

    员工归还资产时，行政人员通过此接口记录归还信息。
    提交后：
      1. 更新 AssetCheckout 记录（actual_return_date, return_condition, status=returned）
      2. 更新 Asset.status 为 available（可再次被领用）

    字段说明:
        actual_return_date: 实际归还日期（可与预计归还日期不同）
        return_condition:   归还时的资产状况描述（如 "良好"、"外壳有划痕"、"屏幕损坏"）
    """
    actual_return_date: dt.date = Field(..., description="实际归还日期")
    return_condition: Optional[str] = Field(None, max_length=50, description="归还时资产状况")
    # 描述资产归还状况，为后续报废/维修决策提供依据


class AssetCheckoutOut(AssetCheckoutBase):
    """
    返回给前端的资产领用记录（ORM 序列化）。

    字段说明:
        id:                 领用记录主键
        actual_return_date: 实际归还日期（归还后才有值，未归还为 None）
        return_condition:   归还时的资产状况说明
        status:             记录状态（checked_out=领用中 / returned=已归还）
        created_at:         记录创建时间
    """
    id: int
    actual_return_date: Optional[dt.date]  # 归还日期（未归还时为 None）
    return_condition: Optional[str]        # 归还状况描述
    status: str                            # checked_out | returned
    created_at: dt.datetime
    asset_no: Optional[str] = None
    asset_name: Optional[str] = None
    usage_department: Optional[str] = None
    employee_name: Optional[str] = None
    employee_no: Optional[str] = None
    department_name: Optional[str] = None

    model_config = {"from_attributes": True}


class AssetInventoryRecordBase(BaseModel):
    asset_id: int = Field(..., description="资产ID")
    inventory_date: dt.date = Field(..., description="盘点时间")
    inventory_by: Optional[str] = Field(None, max_length=50, description="盘点人员")
    result: str = Field(..., description="盘点结果: 正常/丢失/损坏")
    notes: Optional[str] = Field(None, description="盘点备注")


class AssetInventoryRecordCreate(AssetInventoryRecordBase):
    pass


class AssetInventoryRecordOut(AssetInventoryRecordBase):
    id: int
    asset_no: str = ""
    asset_name: str = ""
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class AssetInventoryRecordPage(BaseModel):
    total: int
    items: list[AssetInventoryRecordOut]


class AssetStatsOut(BaseModel):
    total_assets: int
    total_value: float
    total_net_value: float = 0
    total_current_month_depreciation: float = 0
    in_use_count: int
    idle_count: int
    maintenance_count: int
    retired_count: int
    by_category: list[dict]
    by_department: list[dict]


class AssetDepreciationRuleBase(BaseModel):
    major_category: str = Field(..., max_length=50, description="资产大类")
    minor_category: Optional[str] = Field(None, max_length=50, description="资产小类")
    depreciation_months: int = Field(..., ge=1, description="折旧月份")
    residual_rate: Optional[float] = Field(None, ge=0, le=1, description="残值率")
    remark: Optional[str] = Field(None, max_length=200, description="备注")
    is_active: bool = Field(True, description="是否启用")


class AssetDepreciationRuleCreate(AssetDepreciationRuleBase):
    pass


class AssetDepreciationRuleUpdate(BaseModel):
    major_category: Optional[str] = Field(None, max_length=50)
    minor_category: Optional[str] = Field(None, max_length=50)
    depreciation_months: Optional[int] = Field(None, ge=1)
    residual_rate: Optional[float] = Field(None, ge=0, le=1)
    remark: Optional[str] = Field(None, max_length=200)
    is_active: Optional[bool] = None


class AssetDepreciationRuleOut(AssetDepreciationRuleBase):
    id: int
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}


class AssetDepreciationRulePage(BaseModel):
    total: int
    items: list[AssetDepreciationRuleOut]


class AssetMonthlySnapshotGenerateRequest(BaseModel):
    report_year: int = Field(..., ge=2000, le=2100, description="报表年份")
    report_month: int = Field(..., ge=1, le=12, description="报表月份")
    overwrite: bool = Field(False, description="是否覆盖已存在快照")


class AssetMonthlySnapshotOut(BaseModel):
    id: int
    asset_id: int
    report_year: int
    report_month: int
    report_date: dt.date
    asset_no: str
    name: str
    major_category: Optional[str] = None
    minor_category: Optional[str] = None
    asset_description: Optional[str] = None
    supplier: Optional[str] = None
    purchase_price: Optional[float] = None
    tax_rate: Optional[float] = None
    amount_excluding_tax: Optional[float] = None
    quantity: int = 1
    unit: Optional[str] = None
    acceptance_date: Optional[dt.date] = None
    custodian: Optional[str] = None
    usage_department: Optional[str] = None
    usage_employee_name: Optional[str] = None
    storage_location: Optional[str] = None
    depreciation_months: Optional[int] = None
    residual_value: Optional[float] = None
    monthly_depreciation_amount: Optional[float] = None
    accumulated_depreciation_months: int = 0
    remaining_depreciation_months: Optional[int] = None
    accumulated_depreciation_amount: Optional[float] = None
    remaining_depreciation_amount: Optional[float] = None
    current_month_depreciation_amount: Optional[float] = None
    previous_month_accumulated_depreciation: Optional[float] = None
    status: Optional[str] = None
    matched_ledger_code: Optional[str] = None
    matched_ledger_department: Optional[str] = None
    inventory_loss_flag: bool = False
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class AssetMonthlySnapshotPage(BaseModel):
    total: int
    items: list[AssetMonthlySnapshotOut]


class AssetImportResult(BaseModel):
    imported_assets: int
    imported_rules: int
    updated_assets: int
    errors: list[str] = Field(default_factory=list)


class ToolItemBase(BaseModel):
    tool_no: str = Field(..., max_length=50, description="工具编号")
    usage_department: Optional[str] = Field(None, max_length=100, description="使用部门")
    name: str = Field(..., max_length=100, description="工具名称")
    description: Optional[str] = Field(None, description="工具说明")
    brand: Optional[str] = Field(None, max_length=100, description="品牌")
    purchase_price: Optional[float] = Field(None, ge=0, description="购买金额")
    acceptance_date: Optional[dt.date] = Field(None, description="验收日期")
    custodian: Optional[str] = Field(None, max_length=50, description="保管人")
    is_in_use: bool = Field(True, description="是否使用中")
    discard_date: Optional[dt.date] = Field(None, description="废弃时间")
    discard_reason: Optional[str] = Field(None, max_length=200, description="废弃原因")


class ToolItemCreate(ToolItemBase):
    pass


class ToolItemUpdate(BaseModel):
    usage_department: Optional[str] = Field(None, max_length=100)
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    brand: Optional[str] = Field(None, max_length=100)
    purchase_price: Optional[float] = Field(None, ge=0)
    acceptance_date: Optional[dt.date] = None
    custodian: Optional[str] = Field(None, max_length=50)
    is_in_use: Optional[bool] = None
    discard_date: Optional[dt.date] = None
    discard_reason: Optional[str] = Field(None, max_length=200)


class ToolItemOut(ToolItemBase):
    id: int
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}


class ToolItemPage(BaseModel):
    total: int
    items: list[ToolItemOut]


# ───────── Visitor ─────────

class VisitorBase(BaseModel):
    """
    访客基础 Schema

    Visitor 管理外来访客的预约登记和来访记录，
    实现"来访有记录、出入有凭证"的安全管理要求。

    来访流程:
        预约登记（VisitorCreate）→ 访客到达签到（VisitorCheckInRequest）→ 访客离开签出（VisitorCheckOutRequest）

    字段说明:
        visitor_name:        访客姓名
        visitor_company:     访客所属单位/公司
        visitor_phone:       访客联系电话
        host_employee_id:    接待人的员工 ID（访客来访的内部联系人）
        visit_purpose:       来访事由（如 "商务洽谈"、"产品演示"、"应聘面试"）
        visit_date:          来访日期
        expected_arrival:    预计到达时间（time 类型，用于接待准备）
        expected_departure:  预计离开时间
        id_card_no:          身份证号（部分安保要求严格的场合预填）
    """
    visitor_name: str = Field(..., max_length=50, description="访客姓名")
    visitor_company: Optional[str] = Field(None, max_length=100, description="访客所属单位")
    visitor_phone: Optional[str] = Field(None, max_length=20, description="访客电话")
    host_employee_id: int = Field(..., description="接待人ID")  # 公司内部负责接待的员工
    visit_purpose: Optional[str] = Field(None, max_length=200, description="来访事由")
    visit_date: dt.date = Field(..., description="来访日期")
    expected_arrival: Optional[dt.time] = Field(None, description="预计到达时间")    # 时间（不含日期）
    expected_departure: Optional[dt.time] = Field(None, description="预计离开时间")  # 时间（不含日期）
    id_card_no: Optional[str] = Field(None, max_length=30, description="身份证号")   # 可选的证件信息


class VisitorCreate(VisitorBase):
    """
    预约登记访客的请求体。

    对应 API: POST /admin/visitors

    创建后访客状态为 expected（预约中），
    访客到达时执行签到操作（VisitorCheckInRequest），状态变为 checked_in。
    """
    pass


class VisitorUpdate(BaseModel):
    """
    更新访客预约信息的请求体（PATCH 语义）。

    对应 API: PUT /admin/visitors/{id}

    适用于访客来访前修改预约信息（如推迟来访时间）。
    访客已签到后不建议修改信息。
    """
    visitor_name: Optional[str] = Field(None, max_length=50)
    visitor_company: Optional[str] = Field(None, max_length=100)
    visitor_phone: Optional[str] = Field(None, max_length=20)
    visit_purpose: Optional[str] = Field(None, max_length=200)
    visit_date: Optional[dt.date] = None
    expected_arrival: Optional[dt.time] = None
    expected_departure: Optional[dt.time] = None


class VisitorCheckInRequest(BaseModel):
    """
    访客签到请求体（访客到达时由前台操作）。

    对应 API: POST /admin/visitors/{id}/check-in

    访客到达时，前台扫描/录入身份信息，系统记录实际到达时间。
    签到后访客状态变为 checked_in，actual_arrival 被设置为当前时间。

    字段说明:
        id_card_no:    访客身份证号（前台现场核验后填写，必要时用于安全记录）
        temp_pass_no:  临时通行证编号（如发放了访客门禁卡，记录卡号方便归还管理）
    """
    id_card_no: Optional[str] = Field(None, max_length=30, description="身份证号")
    temp_pass_no: Optional[str] = Field(None, max_length=30, description="临时通行证号")
    # 发放的临时门禁卡/通行证编号，离开时核对归还


class VisitorCheckOutRequest(BaseModel):
    """
    访客签出请求体（访客离开时由前台操作）。

    对应 API: POST /admin/visitors/{id}/check-out

    访客离开时，系统记录实际离开时间，状态变为 checked_out。
    如有临时通行证，前台核对归还后再执行签出。
    请求体为空（无需传入额外参数，服务端自动记录当前时间）。
    """
    pass  # 签出无需额外参数，actual_departure 由 service 层自动设置为当前时间


class VisitorOut(VisitorBase):
    """
    返回给前端的访客记录（ORM 序列化）。

    字段说明:
        id:               访客记录主键
        actual_arrival:   实际到达时间（签到后设置，datetime 类型含日期和时间）
        actual_departure: 实际离开时间（签出后设置）
        temp_pass_no:     发放的临时通行证号
        status:           访客状态（expected=预约待来/checked_in=已签到/checked_out=已离开）
        created_at:       预约记录创建时间
    """
    id: int
    actual_arrival: Optional[dt.datetime]    # 实际到达时间（签到时自动记录）
    actual_departure: Optional[dt.datetime]  # 实际离开时间（签出时自动记录）
    temp_pass_no: Optional[str]              # 临时门禁卡编号
    status: str                              # expected | checked_in | checked_out
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class VisitorPage(BaseModel):
    """访客记录分页响应。"""
    total: int
    items: list[VisitorOut]


# ───────── BookingRoom ─────────

class BookingRoomBase(BaseModel):
    """
    会议室预约基础 Schema

    BookingRoom 管理会议室的预约申请，
    service 层在创建预约时会检查时间冲突（同一会议室同一时段不能重复预约）。

    时间冲突检查逻辑:
        当 room_name + date + [start_time, end_time] 与已有 status=confirmed 的预约重叠时，
        service 层返回 409 Conflict 错误，提示时间段已被占用。
        使用 RoomAvailabilityQuery 可以提前查询可用时段，避免提交时才发现冲突。

    字段说明:
        room_name:            会议室名称（如 "A栋3楼小会议室"）
        room_location:        会议室位置说明（楼栋/楼层信息）
        date:                 预约日期
        start_time:           会议开始时间（time 类型，如 09:30）
        end_time:             会议结束时间（time 类型，如 11:00）
                              注意：end_time 必须大于 start_time（service 层校验）
        subject:              会议主题（如 "Q3绩效评审会"）
        attendee_count:       参会人数（用于匹配会议室容量，≥1）
        equipment_needed_json: 所需设备列表（JSON 字符串）
                               示例: '["投影仪", "视频会议系统", "白板"]'
    """
    room_name: str = Field(..., max_length=50, description="会议室名称")
    room_location: Optional[str] = Field(None, max_length=100, description="会议室位置")
    date: dt.date = Field(..., description="预约日期")
    start_time: dt.time = Field(..., description="开始时间")   # 如 09:30
    end_time: dt.time = Field(..., description="结束时间")     # 如 11:00，必须 > start_time
    subject: Optional[str] = Field(None, max_length=200, description="会议主题")
    attendee_count: Optional[int] = Field(None, ge=1, description="参会人数")  # 最少1人
    equipment_needed_json: Optional[str] = Field(None, description="所需设备JSON")
    # 格式: '["投影仪", "视频会议系统"]'，行政根据此字段提前准备设备


class BookingRoomCreate(BookingRoomBase):
    """
    创建会议室预约的请求体。

    对应 API: POST /admin/rooms

    booker_id 由服务端从 JWT token 自动获取。
    service 层创建前会检查该会议室在该时段是否已有其他预约，
    冲突时返回 409 错误，不创建预约。
    创建成功后 status 默认为 confirmed（已确认）。
    """
    pass


class BookingRoomUpdate(BaseModel):
    """
    更新会议室预约的请求体（PATCH 语义）。

    对应 API: PUT /admin/rooms/{id}

    修改时间时，service 层同样会检查新时段是否有冲突。
    注意：room_name 不可修改（会议室变更应取消重新预约）。
    """
    date: Optional[dt.date] = None
    start_time: Optional[dt.time] = None
    end_time: Optional[dt.time] = None
    subject: Optional[str] = Field(None, max_length=200)
    attendee_count: Optional[int] = Field(None, ge=1)
    equipment_needed_json: Optional[str] = None


class BookingRoomOut(BookingRoomBase):
    """
    返回给前端的会议室预约数据（ORM 序列化）。

    字段说明:
        id:        预约记录主键
        booker_id: 预约人员工 ID（JWT 自动记录）
        status:    预约状态（confirmed=已确认 / cancelled=已取消）
        created_at: 预约创建时间
    """
    id: int
    booker_id: int    # 预约人员工 ID
    status: str       # confirmed | cancelled
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class BookingRoomPage(BaseModel):
    """会议室预约分页响应。"""
    total: int
    items: list[BookingRoomOut]


class RoomAvailabilityQuery(BaseModel):
    """
    会议室可用时段查询参数。

    对应 API: GET /admin/rooms/availability（查询参数）

    在预约前，前端通过此查询提前了解会议室的空闲时段，
    避免提交预约时才发现时间冲突（提升用户体验）。

    字段说明:
        room_name: 要查询的会议室名称
        date:      要查询的日期
    """
    room_name: str = Field(..., description="会议室名称")  # 指定查询哪个会议室
    date: dt.date = Field(..., description="查询日期")      # 指定查询哪天的时段


class RoomAvailabilitySlot(BaseModel):
    """
    会议室某时间段的可用性状态（RoomAvailabilityQuery 响应的单个时段）。

    service 层将一天划分为若干时间段（如每30分钟一个格），
    对每个时间段返回是否可用以及占用该时段的预约 ID。

    字段说明:
        start_time:   时段开始时间
        end_time:     时段结束时间
        is_available: 该时段是否可用（True=空闲，False=已被预约占用）
        booking_id:   占用此时段的预约 ID（is_available=False 时有值，可用于显示预约详情）
    """
    start_time: dt.time               # 时段开始时间
    end_time: dt.time                 # 时段结束时间
    is_available: bool                # True=可预约，False=已占用
    booking_id: Optional[int] = None  # 占用此时段的预约ID（空闲时为 None）


# ───────── BookingVehicle ─────────

class BookingVehicleBase(BaseModel):
    """
    用车申请基础 Schema

    BookingVehicle 管理员工的用车申请，
    行政审批后分配司机，支持公务出行的统一管理。

    审批流程:
        员工提交申请（status=pending）→ 行政审批（approved/rejected）→
        approved 时行政指定司机（driver_assigned）→ 用车完成

    字段说明:
        vehicle_type:    所需车型（如 "商务车"、"轿车"、"货车"），
                         也可以为 None（由行政根据情况调配）
        purpose:         用车事由（如 "客户接送"、"物料采购"、"商务出行"）
        date:            用车日期
        start_time:      开始用车时间（time 类型）
        end_time:        预计结束时间（time 类型）
        destination:     目的地（如 "深圳宝安机场"、"广州客户公司"）
        passenger_count: 乘车人数（≥1，用于选择合适车型）
    """
    vehicle_type: Optional[str] = Field(None, max_length=30, description="车辆类型")
    # 如 "商务车"、"轿车"，None 表示由行政根据实际情况调配
    purpose: Optional[str] = Field(None, max_length=200, description="用车事由")  # 用车原因说明
    date: dt.date = Field(..., description="用车日期")
    start_time: dt.time = Field(..., description="开始时间")    # 出发时间
    end_time: dt.time = Field(..., description="结束时间")      # 预计返回时间
    destination: Optional[str] = Field(None, max_length=200, description="目的地")
    passenger_count: Optional[int] = Field(None, ge=1, description="乘车人数")  # ≥1人


class BookingVehicleCreate(BookingVehicleBase):
    """
    创建用车申请的请求体。

    对应 API: POST /admin/vehicles

    applicant_id 由服务端从 JWT token 自动获取。
    创建后 approval_status 默认为 pending（待审批）。
    """
    pass


class BookingVehicleUpdate(BaseModel):
    """
    更新用车申请的请求体（PATCH 语义）。

    对应 API: PUT /admin/vehicles/{id}

    仅允许在审批前（status=pending）修改申请内容。
    审批后如需变更，应取消原申请后重新提交。
    """
    vehicle_type: Optional[str] = Field(None, max_length=30)
    purpose: Optional[str] = Field(None, max_length=200)
    date: Optional[dt.date] = None
    start_time: Optional[dt.time] = None
    end_time: Optional[dt.time] = None
    destination: Optional[str] = Field(None, max_length=200)
    passenger_count: Optional[int] = Field(None, ge=1)


class BookingVehicleApproval(BaseModel):
    """
    用车申请审批请求体（行政人员操作）。

    对应 API: POST /admin/vehicles/{id}/approve

    行政审批用车申请，通过时指定司机。
    拒绝时 driver_assigned 可为空。

    字段说明:
        approval_status: 审批结果，枚举: "approved"（通过）/ "rejected"（拒绝）
        driver_assigned: 指定司机姓名（approved 时填写，如 "张师傅"）
    """
    approval_status: str = Field(
        ..., description="审批状态: approved/rejected"
    )  # approved=批准用车，rejected=拒绝申请
    driver_assigned: Optional[str] = Field(None, max_length=50, description="指派司机")
    # 批准后填写司机姓名，便于员工知道谁来接送


class BookingVehicleOut(BookingVehicleBase):
    """
    返回给前端的用车申请数据（ORM 序列化）。

    字段说明:
        id:              申请记录主键
        applicant_id:    申请人员工 ID（JWT 自动记录）
        approval_status: 审批状态（pending=待审批 / approved=已批准 / rejected=已拒绝）
        driver_assigned: 指派的司机姓名（审批通过后由行政填写）
        created_at:      申请提交时间
    """
    id: int
    applicant_id: int                      # 申请人员工 ID
    approval_status: str                   # pending | approved | rejected
    driver_assigned: Optional[str]         # 指派司机姓名（审批通过后有值）
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class BookingVehiclePage(BaseModel):
    """用车申请分页响应。"""
    total: int
    items: list[BookingVehicleOut]
