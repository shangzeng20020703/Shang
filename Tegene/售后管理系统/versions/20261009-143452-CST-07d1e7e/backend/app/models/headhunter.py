"""
猎头管理模块 - Headhunter Models
==================================

本模块定义猎头公司档案和猎头推荐记录的数据表，用于管理通过第三方猎头渠道
引入的候选人信息及相关费用跟踪。

业务背景
--------
对于高管岗位或稀缺技能岗位，公司通常委托专业猎头公司（Headhunter）寻访候选人。
猎头管理模块覆盖以下业务链：
  1. 建立合作猎头公司档案（Headhunter），记录联系人、费率和合作状态
  2. 猎头提交候选人推荐（HeadhunterRecommendation），跟踪推荐进展
  3. 候选人录用后，计算和跟踪猎头费用的支付状态

与其他模块的关系
----------------
本模块与招聘模块（recruitment）有以下关联：
  - HeadhunterRecommendation.resume_id → resumes.id
    猎头推荐的候选人可以关联到系统中的简历记录（Resume），
    形成"猎头推荐 → 简历 → 面试 → Offer"的完整招聘链路

注意：HeadhunterCommission（猎头佣金结算）定义在 vendor.py 模块中，
而非本模块。两者的区别：
  - headhunters（本模块）：猎头公司基础档案 + 单次推荐记录
  - HeadhunterCommission（vendor.py）：与外包供应商体系合并管理的佣金合同

付款状态独立性说明
------------------
HeadhunterRecommendation.payment_status 与 Offer 流程的状态是相互独立的：
  - 候选人「已录用」不等于猎头费「已付款」
  - 通常按协议约定在候选人入职满 3 个月或 6 个月后支付猎头费
  - 因此 payment_status 需要 HR 或财务手动更新，不自动联动 Offer 状态

包含数据表
----------
- headhunters                  猎头公司档案
- headhunter_recommendations   猎头推荐记录
"""

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class Headhunter(Base):
    """
    猎头公司档案，记录合作猎头机构的基本信息和合作条款。

    每家猎头公司建立一条记录，包含联系方式、擅长领域和收费费率。
    同一猎头公司可以提交多条候选人推荐（一对多关联 HeadhunterRecommendation）。

    合作状态（status）
    ------------------
    合作中   — 当前正在积极合作，可接受新的候选人推荐
    暂停合作 — 临时暂停（如年度评估期间、服务质量问题）
    已终止   — 合作彻底终止，历史推荐记录保留以备费用追溯

    费率（fee_rate）
    ----------------
    存储百分比数值，如 20.00 表示 20%（候选人年薪的 20% 作为猎头费）。
    具体收费金额在 HeadhunterRecommendation.fee_amount 中按实际情况填写，
    fee_rate 作为参考标准（合同约定）。

    专业领域（specialty_areas）
    ---------------------------
    自由文本字段，记录猎头公司擅长的行业或岗位类型，
    如"IT/机器人/人工智能"、"金融/投资银行"，
    用于 HR 在发布职位时筛选合适的猎头合作方。

    关联关系
    --------
    recommendations — 该猎头公司提交的所有推荐记录（cascade delete，
                      猎头档案删除时所有推荐记录一并删除）
    """

    __tablename__ = "headhunters"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    # 猎头公司全称，如"上海某某猎聘有限公司"
    name: str = Column(String(200), nullable=False, comment="猎头公司名称")
    # 对接联系人姓名，通常是该猎头公司负责本公司业务的顾问
    contact_person: str = Column(String(50), nullable=True, comment="联系人姓名")
    # 联系电话（含区号，如"021-12345678"或手机号）
    phone: str = Column(String(30), nullable=True, comment="联系电话")
    # 联系邮箱，用于发送职位需求和简历传输
    email: str = Column(String(100), nullable=True, comment="邮箱")
    # 猎头专业领域，自由文本，格式如"IT/金融/制造"
    specialty_areas: str = Column(String(500), nullable=True, comment="专业领域（如：IT/金融/制造）")
    # 猎头费率（百分比），如 20.00 表示年薪 20%，Numeric(5,2) 支持最高 999.99%
    fee_rate: float = Column(Numeric(5, 2), nullable=True, comment="猎头费率（%，如：20 表示20%）")
    # 合作状态：合作中/暂停合作/已终止
    status: str = Column(String(20), default="合作中", nullable=False, comment="状态：合作中/暂停合作/已终止")
    remark: str = Column(Text, nullable=True, comment="备注")
    # 使用应用层时区感知 datetime（非 server_default），确保跨时区一致性
    created_at: datetime = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="创建时间",
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="更新时间",
    )

    # 所有推荐记录，级联删除（猎头档案删除时推荐记录随之删除）
    recommendations = relationship(
        "HeadhunterRecommendation", back_populates="headhunter", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Headhunter(id={self.id}, name='{self.name}')>"


class HeadhunterRecommendation(Base):
    """
    猎头推荐记录，跟踪每位被推荐候选人从推荐到录用的全流程状态及费用。

    一条记录代表猎头公司向本公司推荐的一位候选人。记录创建于猎头提交
    简历时，此后随着候选人进入面试、录用流程，HR 更新推荐状态（status）；
    候选人入职后按照付款计划更新付款状态（payment_status）。

    推荐状态（status）流转
    ----------------------
    推荐中   — 猎头已提交候选人简历，HR 正在评估
    面试中   — 候选人已安排面试，等待面试结果
    已录用   — 候选人接受 Offer 并确认入职
    未录用   — 候选人面试不通过或拒绝 Offer
    已放弃   — 中途放弃（候选人撤回、岗位取消等）

    付款状态（payment_status）说明
    --------------------------------
    付款状态与推荐状态相互独立，须手动维护：

    未付款   — 默认状态，候选人录用后仍为此状态直到付款发起
    部分付款 — 分期付款协议下，已支付部分款项
    已付款   — 全款支付完成，该推荐的费用跟踪完毕

    通常的触发时机：
    - 候选人入职满约定考察期（如 3 个月）后，财务人员将状态从"未付款"更新为"已付款"
    - 分期付款时，每次付款后手动更新为"部分付款"

    关联简历（resume_id）
    ----------------------
    resume_id 为可选字段（nullable=True）：
    - 若猎头推荐的候选人已在系统中建立了简历（Resume），则填写关联 ID
    - 若候选人尚未在系统中，resume_id 可为空，候选人姓名用 candidate_name 字段记录
    - 简历删除时此字段置 NULL（ondelete="SET NULL"），推荐记录本身保留

    费用计算
    --------
    fee_amount 字段存储本次推荐的实际猎头费金额（元，如 80000.00），
    可能与 headhunter.fee_rate × 年薪 的计算值存在差异（实际谈判结果）。
    """

    __tablename__ = "headhunter_recommendations"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    # 所属猎头公司 ID，猎头档案删除时此记录一并删除
    headhunter_id: int = Column(
        Integer,
        ForeignKey("headhunters.id", ondelete="CASCADE"),
        nullable=False,
        comment="猎头公司ID",
    )
    # 被推荐候选人姓名（快照字段，即使候选人简历后被删除也能追溯）
    candidate_name: str = Column(String(50), nullable=False, comment="候选人姓名")
    # 候选人被推荐应聘的职位名称，如"首席技术官"
    position: str = Column(String(100), nullable=True, comment="推荐职位")
    # 关联系统中的简历记录（可选）；简历删除后置 NULL 保留推荐记录
    resume_id: int = Column(
        Integer,
        ForeignKey("resumes.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联简历ID（可选）",
    )
    # 猎头提交推荐的日期
    recommend_date: date = Column(Date, nullable=True, comment="推荐日期")
    # 本次推荐协议约定的猎头费金额（元），Numeric(12,2) 支持最高千万级
    fee_amount: float = Column(Numeric(12, 2), nullable=True, comment="猎头费用（元）")
    # 付款状态：未付款/已付款/部分付款（独立于推荐状态，须手动维护）
    payment_status: str = Column(
        String(20), default="未付款", nullable=False, comment="付款状态：未付款/已付款/部分付款"
    )
    # 推荐进展状态：推荐中/面试中/已录用/未录用/已放弃
    status: str = Column(
        String(20), default="推荐中", nullable=False,
        comment="推荐状态：推荐中/面试中/已录用/未录用/已放弃"
    )
    remark: str = Column(Text, nullable=True, comment="备注")
    # 推荐记录创建时间
    created_at: datetime = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="创建时间",
    )

    # 所属猎头公司对象
    headhunter = relationship("Headhunter", back_populates="recommendations")

    def __repr__(self) -> str:
        return (
            f"<HeadhunterRecommendation(id={self.id}, candidate='{self.candidate_name}', "
            f"headhunter_id={self.headhunter_id})>"
        )
