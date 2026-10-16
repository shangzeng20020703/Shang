"""
供应商管理模型 - Vendor Management Models
==========================================

本模块管理售后管理系统中与外部供应商的合作关系，涵盖劳务派遣、猎头招募、
外包服务等多种合作形式的全生命周期管理。

【模块包含的 5 张核心表】
─────────────────────────────────────────────────────────────────────────
  1. Vendor              供应商基础档案（公司信息、类型、状态）
  2. VendorContract      供应商合同（合同编号、有效期、费率、金额）
  3. HeadhunterCommission 猎头佣金追踪（分阶段付款、保证期、退款管理）
  4. VendorEvaluation    供应商绩效评分（四维度季度/年度评估）
  5. VendorBlacklist     供应商黑名单（一供应商一条记录，软删除机制）

【供应商类型 vendor_type 说明】
─────────────────────────────────────────────────────────────────────────
  - outsource    外包服务商：承接公司非核心业务（如 IT 外包、物业保洁外包）
  - headhunter   猎头服务商：为公司寻访并推荐中高端候选人，按成功招募收费
  - service      其他服务商：包含劳务派遣机构、培训机构、福利采购等

  Vendor.dispatch_count 字段专用于 service 类型中的劳务派遣场景，
  记录当前通过该供应商派遣在职的员工人数，供 HR 了解派遣规模。

【猎头佣金结算流程 HeadhunterCommission】
─────────────────────────────────────────────────────────────────────────
  行业惯例：猎头佣金通常为候选人年薪的 15%~30%，分两期结算：

  第一期（首付款，offer_accepted 阶段）：
    候选人正式接受 Offer 并入职 → HR 确认入职 → 支付佣金总额的 50%
    first_payment_amount = commission_amount * 0.5
    first_payment_status: pending → paid（付款后更新）
    first_payment_date: 记录实际付款日期

  第二期（尾款，probation_passed 阶段）：
    候选人顺利通过试用期考核（通常 3~6 个月）→ 支付剩余 50% 尾款
    second_payment_amount = commission_amount * 0.5
    second_payment_status: pending → paid
    second_payment_date: 记录实际付款日期

  保证期机制（guarantee_expired 阶段）：
    猎头通常提供 3 个月的候选人质量保证期（guarantee_period_months=3）。
    guarantee_expiry_date = 入职日期 + guarantee_period_months。
    若候选人在保证期内主动离职或因不胜任被辞退，公司有权要求猎头退还已付佣金或
    免费重新推荐等效候选人。
    refund_status 记录退款进展：
      - none:      无退款纠纷（正常情况）
      - requested: 已发起退款请求（候选人在保证期内离职，HR 向猎头提出）
      - completed: 退款已完成（猎头已退还或已补推候选人）

  stage 字段记录该佣金记录当前所处的业务阶段：
    offer_accepted → probation_passed → guarantee_expired

【供应商绩效评估 VendorEvaluation — 四维度评分体系】
─────────────────────────────────────────────────────────────────────────
  每个维度 1~10 分，overall_score 由服务层计算（通常为四维度加权平均）：

  - quality_score    服务质量（10分）: 供应商提供的人员/服务是否达到约定标准
  - delivery_score   交付及时性（10分）: 是否按时交付，到岗是否准时，响应是否迅速
  - price_score      价格合理性（10分）: 费率是否市场竞争力强，性价比是否合理
  - cooperation_score 合作态度（10分）: 沟通是否顺畅，投诉处理是否积极

  eval_quarter=NULL 表示年度评估，eval_quarter=1~4 表示季度评估。
  年度评估的 overall_score 会同步更新到 Vendor.annual_eval_score 字段，
  供供应商列表页快速展示综合评分。

【黑名单机制 VendorBlacklist — 唯一约束设计】
─────────────────────────────────────────────────────────────────────────
  vendor_id 字段设有 UNIQUE 约束，确保每个供应商在黑名单表中最多只有一条记录。
  这样设计避免了同一供应商被重复拉黑产生冗余数据的问题。

  黑名单采用软删除机制（is_active 字段），不物理删除记录：
  - 加入黑名单：创建记录，is_active=True，removed_at=NULL
  - 移出黑名单：is_active=False，removed_at=当前时间

  保留历史记录的目的：
  1. 审计追溯：记录是谁（added_by）、何时、为何（reason）将供应商加入黑名单
  2. 反复加黑：若供应商再次出现问题，可通过更新现有记录（is_active=True，清空 removed_at）
              恢复黑名单状态，无需新增记录（UNIQUE 约束决定只能更新不能新增）

【数据模型关系图】
─────────────────────────────────────────────────────────────────────────
  Vendor (1) ──< (n) VendorContract       [合同，随供应商级联删除]
  Vendor (1) ──< (n) HeadhunterCommission [猎头佣金，随供应商级联删除]
  Vendor (1) ──< (n) VendorEvaluation     [绩效评分，无级联]
  Vendor (1) ──  (1) VendorBlacklist      [黑名单，UNIQUE 保证一对一]
  VendorContract (1) ──< (n) HeadhunterCommission [合同与佣金关联]
"""

from datetime import date, datetime, timezone
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Vendor(Base):
    """供应商基础档案 - Vendor / outsource partner.

    记录与公司存在合作关系的外部供应商信息，是供应商管理模块的核心主表。
    所有其他供应商相关表（合同、佣金、评估、黑名单）均以 vendor_id 为外键关联到本表。

    【供应商状态流转】
    active → suspended（暂停合作，如供应商资质到期，暂时不接新单）
    active → terminated（终止合作，如严重违约，永久停止合作）
    suspended → active（资质更新后恢复合作）

    【annual_eval_score 字段】
    由服务层在每次完成年度 VendorEvaluation 评估后自动同步写入，
    是最近一次年度综合评分的冗余存储，用于列表页快速展示，无需 JOIN 评估表。

    【associated_project_ids_json 字段】
    用于外包服务商场景，记录该供应商参与的项目 ID 列表（JSON 数组格式），
    例如 "[101, 102, 105]"，用于快速查询某项目的外包供应商。
    猎头/劳务派遣供应商通常不使用此字段。
    """

    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="公司名称（营业执照上的全称）"
    )
    short_name: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="公司简称"
    )
    credit_code: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="统一社会信用代码"
    )
    vendor_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="供应商类型: outsource(外包服务)/headhunter(猎头服务)/service(劳务派遣/培训/其他)",
    )
    contact_name: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="联系人姓名（对接 HR 的主要负责人）"
    )
    contact_phone: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="联系人电话"
    )
    contact_email: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="联系人邮箱"
    )
    address: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="公司地址"
    )
    service_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="服务类型: 劳务派遣/招聘外包/培训机构/猎头服务"
    )
    business_scope: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="业务范围（描述供应商可提供的服务内容）"
    )
    # 资质文件 JSON 格式，例如：[{"name":"营业执照","url":"..."},{"name":"人力资源许可证","url":"..."}]
    qualification_docs_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="资质文件JSON（含文件名和URL的对象数组）"
    )
    # 专用于劳务派遣类供应商，记录当前在职派遣员工人数
    dispatch_count: Mapped[int] = mapped_column(
        Integer, default=0, comment="当前在职派遣人数（劳务派遣类供应商专用）"
    )
    # 记录该供应商参与的外包项目 ID 列表，JSON 数组，例如 "[101, 102]"
    associated_project_ids_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="关联项目ID列表JSON（外包服务商专用）"
    )
    # 最近一次年度评估综合评分，由服务层评估完成后自动同步，无需 JOIN 查询
    annual_eval_score: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="年度综合评估分数（最近一次，由服务层自动同步）"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        comment="合作状态: active(合作中)/suspended(暂停)/terminated(终止)",
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
        comment="更新时间",
    )

    # 与合同的一对多关系，级联删除（删除供应商时同步删除其所有合同）
    contracts: Mapped[list["VendorContract"]] = relationship(
        "VendorContract",
        back_populates="vendor",
        cascade="all, delete-orphan",
    )
    # 与猎头佣金的一对多关系，级联删除
    commissions: Mapped[list["HeadhunterCommission"]] = relationship(
        "HeadhunterCommission",
        back_populates="vendor",
        cascade="all, delete-orphan",
    )
    dispatch_projects: Mapped[list["LaborDispatchProject"]] = relationship(
        "LaborDispatchProject",
        back_populates="vendor",
        cascade="all, delete-orphan",
    )
    dispatch_workers: Mapped[list["LaborDispatchWorker"]] = relationship(
        "LaborDispatchWorker",
        back_populates="vendor",
        cascade="all, delete-orphan",
    )
    labor_settlements: Mapped[list["LaborSettlement"]] = relationship(
        "LaborSettlement",
        back_populates="vendor",
        cascade="all, delete-orphan",
    )


class VendorContract(Base):
    """供应商合同 - Vendor contract.

    记录与供应商签订的正式合同信息，包括合同编号、有效期、费率结构和合同总额。
    一个供应商可以签多份合同（如每年续签一份，历史合同保留）。

    【合同类型 contract_type 说明】
    - outsource_dispatch: 劳务派遣合同，规定派遣服务费率（通常按人头/月计费）
    - headhunter_service: 猎头服务合同，规定佣金比例（通常按年薪百分比）
    - other:              其他类型合同（如培训合同、一次性服务合同）

    【fee_rate_json 字段结构示例】
    猎头合同：{"rate": 0.20, "base": "annual_salary"}   → 年薪的 20%
    派遣合同：{"management_fee": 500, "unit": "per_person_per_month"}  → 每人每月 500 元管理费
    按项目：  {"fixed_fee": 50000, "currency": "CNY"}  → 固定费用 5 万元

    【合同状态流转】
    active → expired（合同到期，系统或定时任务自动更新）
    active → terminated（提前终止，双方协商解除或单方违约）
    """

    __tablename__ = "vendor_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        comment="供应商ID",
    )
    # 合同编号全局唯一，通常由 HR 或法务部门按内部规则生成（如 HT-2024-001）
    contract_no: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, comment="合同编号（全系统唯一）"
    )
    contract_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="合同类型: outsource_dispatch(劳务派遣)/headhunter_service(猎头服务)/other(其他)",
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, comment="合同开始日期")
    end_date: Mapped[date] = mapped_column(Date, nullable=False, comment="合同结束日期")
    # 费率结构 JSON，不同合同类型格式不同（见类文档）
    fee_rate_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="费率结构JSON（合同类型不同，格式各异）"
    )
    total_amount: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="合同总金额（元），框架合同可为 NULL"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        comment="合同状态: active(有效)/expired(到期)/terminated(提前终止)",
    )
    # 合同扫描件或电子档 URL，存储到对象存储后记录访问地址
    file_url: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="合同文件URL（上传至对象存储后的访问地址）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )

    # 反向关联到供应商
    vendor: Mapped["Vendor"] = relationship("Vendor", back_populates="contracts")
    # 一份猎头合同可以关联多条佣金记录（每推荐成功一位候选人产生一条）
    commissions: Mapped[list["HeadhunterCommission"]] = relationship(
        "HeadhunterCommission",
        back_populates="contract",
    )


class HeadhunterCommission(Base):
    """猎头佣金追踪 - Headhunter commission tracking.

    记录每一位通过猎头渠道成功招募的候选人的佣金计算与分期付款情况。
    每推荐成功一位候选人入职，产生一条 HeadhunterCommission 记录。

    【佣金计算规则】
    commission_amount = 候选人年薪 × commission_rate（通常合同约定 15%~30%）
    服务层在候选人正式入职时，根据 Offer 中的年薪自动计算并写入。

    【分期付款设计原因】
    业界采用分期付款有两个目的：
    1. 风险控制：若候选人入职后短期离职，公司可拒付尾款或追索首付款
    2. 激励猎头：猎头有动力持续跟进候选人，确保其顺利通过试用期

    【保证期 guarantee_period_months 说明】
    标准行业惯例为 3 个月（也有 6 个月的高端职位）。
    guarantee_expiry_date = offer_accepted_date + guarantee_period_months
    保证期内候选人离职，公司可主动设置 refund_status = "requested"，
    启动与猎头的退款/换人协商流程。
    保证期满且无纠纷，stage 可更新为 "guarantee_expired" 标记佣金结算完结。

    【resume_id 字段】
    关联到招聘模块的 Resume 表（可选），用于从简历档案反查猎头佣金。
    若候选人简历未录入系统，resume_id 为 NULL，仅凭 candidate_name 追踪。
    """

    __tablename__ = "headhunter_commissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        comment="猎头供应商ID",
    )
    contract_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("vendor_contracts.id"),
        nullable=False,
        comment="关联合同ID（决定佣金比例和结算条款）",
    )
    # 关联招聘模块简历表，NULL 表示候选人未录入简历系统
    resume_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="关联招聘简历ID（可选，用于与招聘模块联动）"
    )
    candidate_name: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="候选人姓名"
    )
    position_name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="推荐职位名称"
    )
    # 佣金比例，通常来源于合同约定，例如 0.20 表示年薪的 20%
    commission_rate: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="佣金比例（如 0.20 表示年薪20%）"
    )
    # 佣金总额 = 年薪 × commission_rate，由服务层计算后写入
    commission_amount: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="佣金总额（元），由服务层根据年薪和比例计算"
    )
    # 当前阶段：offer_accepted（已入职）→ probation_passed（通过试用期）→ guarantee_expired（保证期满）
    stage: Mapped[str] = mapped_column(
        String(30),
        default="offer_accepted",
        comment="结算阶段: offer_accepted(已入职)/probation_passed(通过试用)/guarantee_expired(保证期满)",
    )

    # ── 第一期付款（首付款，入职确认后支付，通常为总额的50%）──────────────────
    first_payment_amount: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="首次付款金额（通常为总额50%）"
    )
    first_payment_status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        comment="首次付款状态: pending(待付)/paid(已付)",
    )
    # 记录实际付款日期，NULL 表示尚未付款
    first_payment_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="首次付款实际日期"
    )

    # ── 第二期付款（尾款，通过试用期后支付，通常为总额的50%）────────────────────
    second_payment_amount: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="第二次付款金额（通常为总额50%，即尾款）"
    )
    second_payment_status: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="第二次付款状态: pending(待付)/paid(已付)"
    )
    second_payment_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="第二次付款实际日期"
    )

    # ── 保证期管理 ──────────────────────────────────────────────────────────
    # 标准猎头合同保证期为 3 个月，高端职位可达 6 个月
    guarantee_period_months: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="保证期（月），通常为3或6个月"
    )
    # guarantee_expiry_date = 入职日期 + guarantee_period_months，由服务层计算后写入
    guarantee_expiry_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="保证期到期日（到期前离职可申请退款）"
    )
    # 退款状态：候选人在保证期内离职时，HR 将此字段更新为 requested 以启动退款流程
    refund_status: Mapped[str] = mapped_column(
        String(20),
        default="none",
        comment="退款状态: none(无纠纷)/requested(已申请退款)/completed(退款完成)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )

    # 反向关联
    vendor: Mapped["Vendor"] = relationship("Vendor", back_populates="commissions")
    contract: Mapped["VendorContract"] = relationship(
        "VendorContract", back_populates="commissions"
    )


class VendorEvaluation(Base):
    """供应商绩效评分 - Vendor performance evaluation.

    记录 HR 或业务部门对供应商服务质量的定期评估，支持季度评估和年度评估两种频率。
    评估结果作为续签合同、调整合作规模或终止合作的重要参考依据。

    【四维度评分体系（各 1~10 分）】
    - quality_score    (服务质量): 供应商人员/服务是否达到约定标准和公司预期
    - delivery_score   (交付及时): 到岗速度、响应时效、交付节点是否按时履行
    - price_score      (价格合理): 费率竞争力、性价比、是否存在乱收费
    - cooperation_score (合作态度): 沟通顺畅度、投诉处理及时性、配合意愿

    overall_score 由服务层在保存时自动计算（通常为四维度等权平均），
    并同步写入 Vendor.annual_eval_score（仅年度评估时触发同步）。

    【评估频率区分】
    eval_quarter = NULL  → 年度综合评估（全年一次，触发 Vendor.annual_eval_score 更新）
    eval_quarter = 1~4  → 季度评估（每季度一次，不触发年度分数同步）
    """

    __tablename__ = "vendor_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vendors.id"), nullable=False, index=True,
        comment="被评估的供应商ID"
    )
    # 评估人通常是该供应商的 HR 业务对接人或相关业务部门负责人
    evaluator_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=False,
        comment="评估人（员工）ID"
    )
    eval_year: Mapped[int] = mapped_column(Integer, nullable=False, comment="评估年份（如 2024）")
    # NULL 表示年度评估；1~4 分别表示第一至第四季度
    eval_quarter: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="评估季度（1-4），NULL=年度综合评估"
    )
    # ── 四维度评分（1-10分，默认 5 分为中等水平）────────────────────────────────
    quality_score: Mapped[float] = mapped_column(Float, default=5.0, comment="服务质量评分（1-10分）")
    delivery_score: Mapped[float] = mapped_column(Float, default=5.0, comment="交付及时性评分（1-10分）")
    price_score: Mapped[float] = mapped_column(Float, default=5.0, comment="价格合理性评分（1-10分）")
    cooperation_score: Mapped[float] = mapped_column(Float, default=5.0, comment="合作态度评分（1-10分）")
    # 综合得分由服务层计算写入（通常 = 四维度等权平均），年度评估时同步到 Vendor.annual_eval_score
    overall_score: Mapped[float] = mapped_column(Float, default=5.0, comment="综合得分（服务层自动计算，四维度等权平均）")
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="评估意见（定性描述）")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="创建时间",
    )

    # 反向关联
    vendor: Mapped["Vendor"] = relationship("Vendor")
    evaluator: Mapped["Employee"] = relationship("Employee", foreign_keys=[evaluator_id])


class VendorBlacklist(Base):
    """供应商黑名单 - Vendor blacklist.

    记录被列入黑名单的供应商信息，采用软删除（is_active 字段）保留历史记录。
    vendor_id 具有 UNIQUE 约束，确保每个供应商在黑名单中有且仅有一条记录。

    【UNIQUE 约束的设计意义】
    vendor_id UNIQUE 意味着同一供应商无法被重复加入黑名单（防止数据混乱）。
    当供应商被移出黑名单后（is_active=False），若再次出现问题需要重新拉黑，
    只能通过更新现有记录（将 is_active 重置为 True）来实现，而无法新增记录。
    这种强制单记录设计确保了黑名单逻辑的清晰性和数据一致性。

    【软删除 vs 物理删除的选择原因】
    1. 审计追溯：记录谁（added_by）在什么时间（added_at）因何原因（reason）拉黑供应商
    2. 解除记录：removed_at 保留了供应商从黑名单移除的时间点
    3. 合规要求：供应商管理通常需要保留完整操作日志

    【常见拉黑原因 reason 示例】
    - "多次虚报费用，经核实后确认存在欺诈行为"
    - "连续3次未按时交付，已发律师函"
    - "推荐候选人存在简历造假，损害公司利益"
    """

    __tablename__ = "vendor_blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # UNIQUE 约束：每个供应商在黑名单表中只能有一条记录，保证数据唯一性
    vendor_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("vendors.id"),
        nullable=False,
        unique=True,   # 关键约束：一个供应商只能有一条黑名单记录
        index=True,
        comment="供应商ID（UNIQUE，每供应商仅一条黑名单记录）",
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False, comment="拉黑原因（需具体说明）")
    added_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=False,
        comment="操作人ID（执行拉黑操作的员工）"
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="加入黑名单时间",
    )
    # 移除黑名单时写入此字段，NULL 表示仍在黑名单中
    removed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="移出黑名单时间，NULL表示仍在黑名单中",
    )
    # 软删除标志：False 表示已移出黑名单，True 表示仍在黑名单中
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否仍在黑名单（软删除标志）")

    # 反向关联
    vendor: Mapped["Vendor"] = relationship("Vendor")
    added_by_employee: Mapped["Employee"] = relationship(
        "Employee", foreign_keys=[added_by]
    )


class LaborDispatchProject(Base):
    """劳务派遣项目."""

    __tablename__ = "labor_dispatch_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="劳务公司ID",
    )
    project_name: Mapped[str] = mapped_column(String(200), nullable=False, comment="项目名称")
    client_company: Mapped[str] = mapped_column(String(200), nullable=False, comment="客户公司")
    department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="用工部门")
    owner: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="项目负责人")
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="项目开始日期")
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="项目结束日期")
    status: Mapped[str] = mapped_column(
        String(20),
        default="未开始",
        comment="项目状态: 未开始/进行中/已结束/已暂停",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="更新时间",
    )

    vendor: Mapped["Vendor"] = relationship("Vendor", back_populates="dispatch_projects")
    workers: Mapped[list["LaborDispatchWorker"]] = relationship(
        "LaborDispatchWorker",
        back_populates="project",
    )
    settlements: Mapped[list["LaborSettlement"]] = relationship(
        "LaborSettlement",
        back_populates="project",
    )


class LaborDispatchWorker(Base):
    """劳务派遣人员."""

    __tablename__ = "labor_dispatch_workers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属劳务公司ID",
    )
    project_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("labor_dispatch_projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="派遣项目ID",
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="姓名")
    id_card: Mapped[str] = mapped_column(String(18), nullable=False, index=True, comment="身份证号")
    phone: Mapped[str] = mapped_column(String(20), nullable=False, comment="手机号")
    position: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="岗位")
    entry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="入场日期")
    exit_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="离场日期")
    status: Mapped[str] = mapped_column(
        String(20),
        default="在岗",
        comment="状态: 在岗/离岗",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="更新时间",
    )

    vendor: Mapped["Vendor"] = relationship("Vendor", back_populates="dispatch_workers")
    project: Mapped[Optional["LaborDispatchProject"]] = relationship(
        "LaborDispatchProject",
        back_populates="workers",
    )


class LaborSettlement(Base):
    """劳务费用结算单."""

    __tablename__ = "labor_settlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="劳务公司ID",
    )
    project_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("labor_dispatch_projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="项目ID",
    )
    settlement_month: Mapped[str] = mapped_column(
        String(7),
        nullable=False,
        index=True,
        comment="结算月份 YYYY-MM",
    )
    method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="结算方式: 按人头/按工时/按项目",
    )
    dispatch_count: Mapped[int] = mapped_column(Integer, default=0, comment="派遣人数")
    work_hours: Mapped[float] = mapped_column(Float, default=0, comment="工时")
    service_fee: Mapped[float] = mapped_column(Float, default=0, comment="服务费单价或金额")
    payable_amount: Mapped[float] = mapped_column(Float, default=0, comment="应付金额")
    status: Mapped[str] = mapped_column(
        String(20),
        default="草稿",
        comment="状态: 草稿/待确认/待审批/已审批/已付款/已作废",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="更新时间",
    )

    vendor: Mapped["Vendor"] = relationship("Vendor", back_populates="labor_settlements")
    project: Mapped[Optional["LaborDispatchProject"]] = relationship(
        "LaborDispatchProject",
        back_populates="settlements",
    )
