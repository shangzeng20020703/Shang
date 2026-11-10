"""
行政管理模型 - Admin Module Models
=====================================

本模块负责售后管理系统中的日常行政事务管理，涵盖内部通知发布、固定资产管理、
访客接待、会议室预约和用车申请五大业务场景。

【模块包含的 7 张核心表】
─────────────────────────────────────────────────────────────────────────
  1. Announcement    公告通知（企业内部通知/政策/活动公告，支持定向推送）
  2. Asset           固定资产台账（电脑、手机、工牌等资产的全生命周期管理）
  3. AssetCheckout   资产领用记录（员工领用与归还的流水台账）
  4. ToolItem        工具台账（低值工具的简表管理与导出）
  5. Visitor         访客管理（来访登记、预约审批、进出时间记录）
  6. BookingRoom     会议室预约（时段预约，防止冲突，含所需设备记录）
  7. BookingVehicle  用车申请（公务用车申请，需审批后分配司机）

【公告通知 Announcement — 定向推送机制】
─────────────────────────────────────────────────────────────────────────
  公告支持三种推送范围（target_type 字段）：

  - all（全员）: target_ids_json = NULL，所有在职员工均可看到
  - department（按部门）: target_ids_json = "[1, 3, 7]"（部门 ID 数组）
                          仅对指定部门的员工可见
  - location（按地点）: target_ids_json = "[2, 5]"（地点 ID 数组）
                        仅对指定办公地点的员工可见（适用于多地办公场景）

  push_wechat / push_app 控制是否同步推送到企业微信或 APP，
  支持定时发布（publish_time 设为未来时间，服务层定时任务触发）。
  is_top=True 使公告置顶显示在公告列表最前端。

  【公告状态流转】
  draft → published（发布，到达 publish_time 时自动发布，或管理员手动发布）
  published → expired（到达 expire_time 时自动过期，或管理员手动下线）

【固定资产 Asset — 全生命周期管理】
─────────────────────────────────────────────────────────────────────────
  资产从采购入库到报废处置的完整流转：

  available（空闲可领用）
    → checked_out（已领用，有员工正在使用，对应 AssetCheckout.status = active）
    → available（员工归还后，AssetCheckout.actual_return_date 写入，状态恢复为 available）
    → maintenance（送修，可能来自归还时发现损坏，或定期保养）
    → available（维修完成，恢复空闲）
    → retired（报废处置，资产到达使用寿命或严重损毁，不可逆操作）

  asset_no（资产编号）全系统唯一，通常由行政部门按规则生成（如 COMP-2024-001）。
  location_id 记录资产当前存放的办公地点（空闲时在仓库，领用中随员工所在地点变化）。

【资产领用记录 AssetCheckout — 流水台账】
─────────────────────────────────────────────────────────────────────────
  每次领用/归还操作均产生一条 AssetCheckout 记录，不覆盖更新（保留完整历史）。

  领用时：
    - checkout_date + employee_id 写入
    - expected_return_date 写入预计归还日期（可选）
    - Asset.status 更新为 checked_out

  归还时：
    - actual_return_date 写入实际归还日期
    - return_condition 记录归还时资产状况（如"良好"/"屏幕有划痕"/"键盘损坏"）
    - AssetCheckout.status 更新为 returned
    - Asset.status 更新为 available（或 maintenance，若发现损坏）

  【AssetCheckout.status 说明】
  - active:   资产仍在员工手中使用中
  - returned: 已归还，本条领用记录结束
  - overdue:  超过 expected_return_date 仍未归还（定时任务自动标记）

【访客管理 Visitor — 预约审批与进出登记】
─────────────────────────────────────────────────────────────────────────
  访客全流程状态机：

  pending（待审批）：
    员工（host_employee_id）提前录入来访预约，或访客临时到达前台登记
    → 前台/行政人员审核预约

  approved（已审批）：
    审核通过，系统生成临时通行证号（temp_pass_no），发送短信通知访客
    → 访客到达时前台核验身份证（id_card_no）并签到

  checked_in（已进入）：
    actual_arrival 写入真实到达时间
    → 访客完成来访事宜后离开时前台办理离场手续

  checked_out（已离开）：
    actual_departure 写入真实离开时间，访客全程结束

  cancelled（已取消）：
    预约方（员工或行政）主动取消，或审批拒绝时进入此状态

  id_card_no 字段：存储访客证件号码（安全考量，建议加密存储或定期脱敏清理）

【会议室预约 BookingRoom — 时段冲突防控】
─────────────────────────────────────────────────────────────────────────
  员工预约会议室时，服务层需检查同一 room_name（相同会议室标识）在同一 date 的
  time slot 是否与已有 confirmed 状态的预约存在时间重叠（start_time/end_time 区间交叉）。
  冲突时返回 409 错误，要求用户选择其他时间段或其他会议室。

  equipment_needed_json 记录所需会议设备，例如：
    '["投影仪", "视频会议系统", "白板"]'
  行政人员可据此提前准备会议室所需设备。

  BookingRoom 默认状态为 confirmed（无需审批，即预即用），
  仅 cancelled 一种终态（员工主动取消或管理员因冲突强制取消）。

【用车申请 BookingVehicle — 审批后分配司机】
─────────────────────────────────────────────────────────────────────────
  公务用车申请需经过审批流程，审批通过后行政人员指派司机：

  pending（待审批）：
    员工提交用车申请，填写用车日期/时间/目的地/人数
    → 直属上级或行政主管审批

  approved（审批通过）：
    approval_status = approved
    行政人员在 driver_assigned 字段填写指派的司机姓名/工号
    → 行程完成

  rejected（审批拒绝）：
    上级拒绝申请，员工可修改后重新提交

  vehicle_type 字段记录所需车型（如"轿车"/"商务车"/"货车"），
  帮助行政人员选择合适车辆。

【数据模型关系图】
─────────────────────────────────────────────────────────────────────────
  Employee (1) ──< (n) Announcement   [发布人]
  Asset (1) ──< (n) AssetCheckout     [领用记录，级联删除]
  Employee (1) ──< (n) AssetCheckout  [领用人]
  Employee (1) ──< (n) Visitor        [接待人]
  Employee (1) ──< (n) BookingRoom    [预约人]
  Employee (1) ──< (n) BookingVehicle [申请人]
  Location (1) ──< (n) Asset          [资产存放地点]
"""

from datetime import date, datetime, time, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Announcement(Base):
    """公告通知 - Company announcements.

    支持按范围定向推送的企业内部公告系统，覆盖通知、政策文件、活动邀请等多种类别。
    通过 target_type + target_ids_json 实现灵活的受众精准定位。
    支持定时发布（publish_time 设为未来时间）和到期自动下线（expire_time）。
    """

    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="公告标题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="公告正文内容（支持富文本）")
    category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="公告类别: notice(通知)/policy(政策文件)/activity(活动邀请)",
    )
    # 受众范围控制：all=全员，department=指定部门，location=指定地点
    target_type: Mapped[str] = mapped_column(
        String(20),
        default="all",
        comment="推送范围: all(全员)/department(指定部门)/location(指定地点)",
    )
    # target_type=all 时此字段为 NULL；否则存储部门 ID 或地点 ID 的 JSON 数组
    # 示例（按部门）："[1, 3, 7]"；示例（按地点）："[2, 5]"
    target_ids_json: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="目标ID列表JSON（target_type=all时为NULL，否则为部门ID或地点ID数组）",
    )
    # 发布人：通常为 HR、行政或管理层员工
    publisher_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id"),
        nullable=False,
        comment="发布人（员工）ID",
    )
    # 定时发布：publish_time 为 NULL 时立即发布；设置为未来时间则由定时任务触发发布
    publish_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="计划发布时间（NULL=立即发布，未来时间=定时发布）",
    )
    # 自动过期：expire_time 到达时，定时任务将 status 更新为 expired
    expire_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="公告过期时间（NULL=永不过期，设置后到期自动下线）",
    )
    # 置顶公告显示在列表最前端，不受发布时间排序影响
    is_top: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否置顶显示")
    # 多渠道推送控制：可同步推送到企业微信和/或移动 APP
    push_wechat: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否同步推送到企业微信"
    )
    push_app: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否同步推送到移动APP")
    # 已读人数：由服务层统计已读记录，用于评估公告触达率
    read_count: Mapped[int] = mapped_column(Integer, default=0, comment="已读人数（由服务层维护）")
    # 状态流转：draft → published → expired
    status: Mapped[str] = mapped_column(
        String(20),
        default="draft",
        comment="发布状态: draft(草稿)/published(已发布)/expired(已过期)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )


class Asset(Base):
    """固定资产台账 - Company assets.

    记录公司全部固定资产的基础信息，是资产管理的主表。
    资产从采购入库（available）→ 领用在途（checked_out）→ 维修（maintenance）→ 报废（retired）
    的全生命周期流转均通过 status 字段反映，并通过 AssetCheckout 记录每次领用/归还明细。

    【资产编号 asset_no 规则建议】
    格式：{类别缩写}-{年份}-{序号}，例如：
      COMP-2024-001（第1台2024年购入的电脑）
      PHONE-2024-002（第2部2024年购入的手机）

    【资产类别 category 常见值】
    电脑 / 手机 / 工牌 / 工服 / 工具 / 办公家具 / 网络设备

    【location_id 字段说明】
    记录资产当前所在的办公地点（外键关联 locations 表）。
    资产空闲时通常指向仓库所在地点，领用后可更新为员工所在地点（可选操作）。
    """

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 资产编号全系统唯一，由行政人员入库时按内部规则生成
    asset_no: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, comment="资产编号（全系统唯一，如 COMP-2024-001）"
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="资产名称（如 MacBook Pro 14寸）")
    category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="资产类别: 电脑/手机/工牌/工服/工具/办公家具/网络设备",
    )
    brand: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="品牌（如 Apple/华为/联想）")
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="型号规格（如 MacBook Pro M3 16GB）")
    supplier: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="供应商")
    major_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="资产大类")
    minor_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="资产小类")
    asset_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="资产说明")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="数量")
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="单位")
    usage_department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="使用部门")
    usage_employee_name_manual: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="手工维护的当前使用人")
    storage_location: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="资产存放地点")
    custodian: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="保管人")
    remark: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="备注")
    purchase_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="采购日期（用于计算折旧年限）"
    )
    acceptance_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="验收日期（优先作为折旧起算日）"
    )
    purchase_price: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="采购价格（元，用于资产价值统计）"
    )
    tax_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="税率（0.13 表示 13%）")
    amount_excluding_tax: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="不含税金额")
    residual_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="残值")
    depreciation_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="折旧总月数")
    monthly_depreciation_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="每月折旧金额")
    accumulated_depreciation_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="累计折旧月份")
    remaining_depreciation_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="剩余折旧月份")
    accumulated_depreciation_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="累计折旧金额")
    remaining_depreciation_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="剩余折旧金额")
    current_month_depreciation_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="当月折旧金额")
    previous_month_accumulated_depreciation: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="上月累计折旧")
    # 资产当前存放地点，NULL 表示地点未录入（如小件资产放在行政前台）
    location_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("locations.id"),
        nullable=True,
        comment="当前存放地点ID（关联 locations 表）",
    )
    dongguan_ledger_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="东莞行政台账编号")
    matched_ledger_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="匹配行政台账最新编码")
    matched_ledger_department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="匹配行政台账最新部门")
    inventory_loss_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否盘亏")
    # 资产生命周期状态，checked_out 时必有对应的 AssetCheckout.status=active 记录
    status: Mapped[str] = mapped_column(
        String(20),
        default="available",
        comment="资产状态: available(空闲可领用)/checked_out(已领出在用)/maintenance(维修中)/retired(已报废)",
    )
    scrap_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="报废时间")
    scrap_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="报废原因")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="资产入库时间",
    )

    # 与领用记录的一对多关系，级联删除（删除资产时同步删除其所有领用历史）
    checkouts: Mapped[list["AssetCheckout"]] = relationship(
        "AssetCheckout",
        back_populates="asset",
        cascade="all, delete-orphan",
    )
    inventory_records: Mapped[list["AssetInventoryRecord"]] = relationship(
        "AssetInventoryRecord",
        back_populates="asset",
        cascade="all, delete-orphan",
    )
    monthly_snapshots: Mapped[list["AssetMonthlySnapshot"]] = relationship(
        "AssetMonthlySnapshot",
        back_populates="asset",
        cascade="all, delete-orphan",
    )

    @property
    def active_checkout(self) -> Optional["AssetCheckout"]:
        for checkout in sorted(
            self.checkouts,
            key=lambda item: (item.checkout_date or date.min, item.id or 0),
            reverse=True,
        ):
            if checkout.status in {"active", "overdue"} and checkout.actual_return_date is None:
                return checkout
        return None

    @property
    def usage_employee_id(self) -> Optional[int]:
        checkout = self.active_checkout
        return checkout.employee_id if checkout else None

    @property
    def usage_employee_name(self) -> Optional[str]:
        checkout = self.active_checkout
        if checkout and checkout.employee:
            return checkout.employee.name
        return self.usage_employee_name_manual

    @property
    def usage_employee_no(self) -> Optional[str]:
        checkout = self.active_checkout
        if not checkout or not checkout.employee:
            return None
        return checkout.employee.employee_no


class AssetCheckout(Base):
    """资产领用记录 - Asset checkout records.

    记录每次资产领用和归还的流水台账，构成完整的资产使用历史。
    一条记录对应一次从领用到归还的完整周期，不覆盖更新。

    【领用操作流程（服务层）】
    1. 检查 Asset.status = available（不可重复领用）
    2. 创建 AssetCheckout 记录（status=active）
    3. 更新 Asset.status = checked_out

    【归还操作流程（服务层）】
    1. 填写 actual_return_date + return_condition
    2. 更新 AssetCheckout.status = returned
    3. 根据 return_condition 决定 Asset.status：
       - 资产完好 → Asset.status = available
       - 资产损坏 → Asset.status = maintenance（送修）

    【超期提醒】
    定时任务每日检查 expected_return_date < today 且 status=active 的记录，
    自动将其 status 更新为 overdue，并推送提醒给领用人和行政。
    """

    __tablename__ = "asset_checkouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        comment="领用的资产ID",
    )
    # 领用人：必须是在职员工（服务层校验 is_active=True）
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id"),
        nullable=False,
        comment="领用员工ID",
    )
    checkout_date: Mapped[date] = mapped_column(Date, nullable=False, comment="领用日期")
    # 预计归还日期：选填，用于超期提醒；长期借用的资产可不填
    expected_return_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="预计归还日期（用于超期提醒，长期使用可为NULL）"
    )
    # 实际归还日期：归还时由行政人员填写
    actual_return_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="实际归还日期（NULL表示尚未归还）"
    )
    # 归还时资产状况：行政人员当面检查后填写，如"良好"/"屏幕轻微划痕"/"键盘E键失灵"
    return_condition: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="归还时资产状况描述（如'良好'/'屏幕划痕'/'键盘损坏'）"
    )
    # 领用记录状态流转：active → returned（正常归还）；active → overdue（超期未还）
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        comment="领用状态: active(使用中)/returned(已归还)/overdue(超期未还)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="记录创建时间（即领用时间）",
    )

    # 反向关联到资产主表
    asset: Mapped["Asset"] = relationship("Asset", back_populates="checkouts")
    employee: Mapped["Employee"] = relationship("Employee")


class AssetInventoryRecord(Base):
    """资产盘点记录."""

    __tablename__ = "asset_inventory_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="资产ID",
    )
    inventory_date: Mapped[date] = mapped_column(Date, nullable=False, comment="盘点日期")
    inventory_by: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="盘点人员")
    result: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="盘点结果: 正常/丢失/损坏",
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="盘点备注")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )

    asset: Mapped["Asset"] = relationship("Asset", back_populates="inventory_records")


class AssetDepreciationRule(Base):
    """固定资产折旧规则."""

    __tablename__ = "asset_depreciation_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    major_category: Mapped[str] = mapped_column(String(50), nullable=False, comment="资产大类")
    minor_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="资产小类")
    depreciation_months: Mapped[int] = mapped_column(Integer, nullable=False, comment="折旧月份")
    residual_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="残值率")
    remark: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="备注")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="更新时间",
    )


class AssetMonthlySnapshot(Base):
    """固定资产月度快照."""

    __tablename__ = "asset_monthly_snapshots"
    __table_args__ = (
        UniqueConstraint("asset_id", "report_year", "report_month", name="uq_asset_monthly_snapshots_asset_month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        comment="资产ID",
    )
    report_year: Mapped[int] = mapped_column(Integer, nullable=False, comment="报表年份")
    report_month: Mapped[int] = mapped_column(Integer, nullable=False, comment="报表月份")
    report_date: Mapped[date] = mapped_column(Date, nullable=False, comment="报表日期")
    asset_no: Mapped[str] = mapped_column(String(50), nullable=False, comment="资产编号")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="资产名称")
    major_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="资产大类")
    minor_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="资产小类")
    asset_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="资产说明")
    supplier: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="供应商")
    purchase_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="购买金额")
    tax_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="税率")
    amount_excluding_tax: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="不含税金额")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="数量")
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="单位")
    acceptance_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="验收日期")
    custodian: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="保管人")
    usage_department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="所属部门")
    usage_employee_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="使用人")
    storage_location: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="存放地点")
    depreciation_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="折旧月份")
    residual_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="残值")
    monthly_depreciation_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="每月折旧金额")
    accumulated_depreciation_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="累计折旧月份")
    remaining_depreciation_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="剩余折旧月份")
    accumulated_depreciation_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="累计折旧金额")
    remaining_depreciation_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="剩余折旧金额")
    current_month_depreciation_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="当月折旧金额")
    previous_month_accumulated_depreciation: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="上月累计折旧")
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="资产状态")
    matched_ledger_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="匹配行政台账编码")
    matched_ledger_department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="匹配行政台账部门")
    inventory_loss_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否盘亏")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )

    asset: Mapped["Asset"] = relationship("Asset", back_populates="monthly_snapshots")


class ToolItem(Base):
    """工具管理台账 - Tool register."""

    __tablename__ = "tool_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_no: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="工具编号（对应 Excel 工具编号）",
    )
    usage_department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="使用部门")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="工具名称")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="工具说明")
    brand: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="品牌")
    purchase_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="购买金额")
    acceptance_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="验收日期")
    custodian: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="保管人")
    is_in_use: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否使用中：true=是 false=否",
    )
    discard_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="废弃时间")
    discard_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="废弃原因")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="更新时间",
    )


class Visitor(Base):
    """访客管理 - Visitor management.

    处理外部人员到公司来访的全流程管理，包括预约登记、安全审批、进出时间记录
    和临时通行证管理，保障公司安全同时提升来访接待体验。

    【访客来访两种模式】
    1. 预约来访：员工提前在系统中登记来访预约（status=pending），
       行政审批通过后（status=approved）生成临时通行证号，提前告知访客。
    2. 临时到访：访客直接到前台，行政人员现场登记，直接进入 approved 状态。

    【完整状态流转】
    pending（待审批）→ approved（审批通过，生成通行证）
                    → cancelled（审批拒绝或预约取消）
    approved → checked_in（访客到达，前台核验身份证并签到，actual_arrival 写入）
    checked_in → checked_out（访客离开，前台办理离场，actual_departure 写入）

    【安全相关字段说明】
    id_card_no（身份证号）: 到访时前台核验身份证并录入，建议服务层进行加密存储，
    并设置定期脱敏清理策略（如 30 天后自动清除）。
    temp_pass_no（临时通行证号）: 系统或行政生成的一次性通行证编号，
    访客凭此通过门禁，离场后自动失效。

    【expected vs actual 时间字段】
    expected_arrival/departure 是预约时填写的预计时间（Time 类型，仅时分）。
    actual_arrival/departure 是实际到离时间（DateTime 类型，含日期和时区），
    用于精确记录门禁进出时间，支持与门禁系统数据比对。
    """

    __tablename__ = "visitors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    visitor_name: Mapped[str] = mapped_column(String(50), nullable=False, comment="访客姓名")
    visitor_company: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="访客所属单位（个人来访可为NULL）"
    )
    visitor_phone: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="访客联系电话"
    )
    # 接待人：公司内部员工，负责迎接访客并为其担保
    host_employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id"),
        nullable=False,
        comment="接待人（公司内部员工）ID",
    )
    visit_purpose: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="来访事由（如'商务洽谈'/'面试'/'设备维护'）"
    )
    visit_date: Mapped[date] = mapped_column(Date, nullable=False, comment="预约来访日期")
    # 预计到达/离开时间（仅时分，来自预约时员工填写）
    expected_arrival: Mapped[Optional[time]] = mapped_column(
        Time, nullable=True, comment="预计到达时间（仅时分，预约时填写）"
    )
    expected_departure: Mapped[Optional[time]] = mapped_column(
        Time, nullable=True, comment="预计离开时间（仅时分，预约时填写）"
    )
    # 实际到达/离开时间（含日期和时区，前台确认时写入）
    actual_arrival: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="实际到达时间（前台签到时写入）"
    )
    actual_departure: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="实际离开时间（前台签退时写入）"
    )
    # 安全核验：前台核验访客身份证后录入，建议加密存储
    id_card_no: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True, comment="访客身份证号（安全核验用，建议加密存储）"
    )
    # 临时通行证：审批通过后生成，访客凭此通过门禁
    temp_pass_no: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True, comment="临时通行证号（审批通过后系统/行政生成）"
    )
    # 访客全流程状态机（见类文档）
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        comment="访客状态: pending(待审批)/approved(已批准)/checked_in(已进入)/checked_out(已离开)/cancelled(已取消)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="预约登记时间",
    )


class BookingRoom(Base):
    """会议室预约 - Meeting room booking.

    员工可预约公司会议室用于内部会议、培训或面试等场景。
    服务层负责检查时段冲突（同一会议室在同一日期的时间段不允许重叠），
    避免多人预约同一会议室同一时段导致冲突。

    【冲突检测逻辑（服务层实现）】
    查询条件：room_name = 目标会议室 AND date = 目标日期 AND status = "confirmed"
    时段重叠判断：新预约的 [start_time, end_time) 与已有预约的时间段是否有交集
    冲突时返回 HTTP 409，提示用户选择其他时间或其他会议室。

    【equipment_needed_json 结构示例】
    '["投影仪", "视频会议系统", "白板", "麦克风"]'
    行政人员可在会议开始前根据此信息提前布置会议室。

    【默认状态为 confirmed 的设计原因】
    会议室预约通常不需要审批（即预即用），因此默认直接进入 confirmed 状态，
    区别于 BookingVehicle（用车需审批，默认 pending）。
    """

    __tablename__ = "booking_rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 会议室标识名称（如"A栋3楼大会议室"），用于冲突检测时的匹配键
    room_name: Mapped[str] = mapped_column(String(50), nullable=False, comment="会议室名称（用于冲突检测的标识）")
    room_location: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="会议室详细位置描述（如 A栋3楼北侧）"
    )
    booker_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id"),
        nullable=False,
        comment="预约人（员工）ID",
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, comment="预约日期")
    # 时段：服务层在保存前校验 start_time < end_time
    start_time: Mapped[time] = mapped_column(Time, nullable=False, comment="预约开始时间")
    end_time: Mapped[time] = mapped_column(Time, nullable=False, comment="预约结束时间（须大于开始时间）")
    subject: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="会议主题（用于日历展示）"
    )
    attendee_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="预计参会人数（用于确认会议室容量是否足够）"
    )
    # 所需设备 JSON 数组，行政人员提前布置，如 '["投影仪","白板"]'
    equipment_needed_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="所需设备列表JSON（如 '[\"投影仪\",\"白板\"]'）"
    )
    # 会议室预约无需审批，直接 confirmed（区别于用车申请的 pending 初始状态）
    status: Mapped[str] = mapped_column(
        String(20),
        default="confirmed",
        comment="预约状态: confirmed(已预约)/cancelled(已取消)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )


class BookingVehicle(Base):
    """用车申请 - Vehicle booking.

    员工申请使用公司公务车辆，需经上级或行政主管审批后方可使用，
    审批通过后由行政人员在系统中指定司机。

    【完整审批流程】
    1. 员工填写申请（日期/时段/目的地/用途/人数/车型需求）→ 提交
    2. 直属上级或行政主管收到审批通知 → 审批操作
       - 同意：approval_status = approved，通知行政安排车辆
       - 拒绝：approval_status = rejected，通知员工
    3. 行政人员查看已批准的用车申请 → 指派司机（写入 driver_assigned）
    4. 行程结束，本条记录归档

    【vehicle_type 常见值】
    轿车（适合 1~3 人）/ 商务车（5~7 人）/ 中巴（8~15 人）/ 货车（运输物资）

    【与 BookingRoom 的主要区别】
    - BookingVehicle 需要审批（初始 approval_status=pending），BookingRoom 无需审批（直接 confirmed）
    - BookingVehicle 有 driver_assigned（行政指派），BookingRoom 无需人员指派
    - 字段命名：BookingVehicle 用 applicant_id + approval_status，体现审批流特征
    """

    __tablename__ = "booking_vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    applicant_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id"),
        nullable=False,
        comment="申请人（员工）ID",
    )
    # 所需车型，帮助行政选择合适车辆；NULL 表示不限车型
    vehicle_type: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True, comment="所需车型（如轿车/商务车/中巴），NULL=不限"
    )
    purpose: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="用车事由（如'客户拜访'/'机场接送'/'物资运输'）"
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, comment="用车日期")
    start_time: Mapped[time] = mapped_column(Time, nullable=False, comment="用车开始时间")
    end_time: Mapped[time] = mapped_column(Time, nullable=False, comment="用车结束时间（预估）")
    destination: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="目的地（如'深圳宝安机场'/'客户公司：XX科技'）"
    )
    passenger_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="乘车人数（含申请人，用于确认车型是否匹配）"
    )
    # 审批状态（注意：字段命名为 approval_status 而非 status，明确区分审批语义）
    approval_status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        comment="审批状态: pending(待审批)/approved(已批准)/rejected(已拒绝)",
    )
    # 行政人员审批通过后填写，格式建议为"姓名/工号"（如"张伟/EMP001"）
    driver_assigned: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="指派司机（格式建议：姓名/工号，审批通过后由行政填写）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="申请提交时间",
    )
