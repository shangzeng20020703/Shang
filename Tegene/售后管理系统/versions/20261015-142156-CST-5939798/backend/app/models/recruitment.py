"""
招聘管理模块数据模型 - Recruitment Management Models
=====================================================

本模块包含完整招聘漏斗所需的全部 9 张数据库表：

  1. RecruitmentDemand      — 招聘需求单（业务部门提出，HR 审批）
  2. RecruitmentChannel     — 招聘渠道配置（BOSS直聘、猎聘、内推、猎头等）
  3. Resume                 — 候选人简历（一份简历对应一个需求）
  4. Interview              — 面试安排（支持多轮，每轮一条记录）
  5. InterviewEvaluation    — 面试评价（面试官针对每轮面试出具的评估报告）
  6. Offer                  — 录用通知书（含审批流与背调/体检状态跟踪）
  7. InternalReferral       — 内推记录（绑定简历，两阶段奖金发放）
  8. InternalReferralRule   — 内推奖金规则（按职级/职类配置奖励金额）
  9. OfferTemplate          — Offer 模板（预设岗位薪资结构，提升 HR 发 Offer 效率）
  10. OnboardingRecord      — 入职记录（Offer 接受后创建，完成后关联 Employee）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
完整招聘业务漏斗
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌─────────────────────────────────────────────────────────┐
  │  1. 需求提出                                            │
  │     业务部门提交 RecruitmentDemand，填写职位、人数、     │
  │     紧急程度（紧急/普通/储备）及需求类型（补人/增编）    │
  ├─────────────────────────────────────────────────────────┤
  │  2. 需求审批                                            │
  │     HR 或上级审批 RecruitmentDemand.approval_status：   │
  │       待审批 → 已通过 / 已拒绝                          │
  │     通过后 status 自动变为「招聘中」，可开始收简历       │
  ├─────────────────────────────────────────────────────────┤
  │  3. 职位发布与渠道管理                                  │
  │     HR 选择 RecruitmentChannel（如 BOSS直聘、猎聘）     │
  │     在外部平台发布职位，或通过内推邀请员工推荐候选人      │
  ├─────────────────────────────────────────────────────────┤
  │  4. 简历收集与筛选                                      │
  │     每位候选人创建一条 Resume 记录，关联到对应 demand。  │
  │     HR 进行初步筛选，更新 Resume.screening_status：     │
  │       待筛选 → 推荐 / 不合适                            │
  │     被标记「不合适」但质量较好的简历可设置               │
  │     talent_pool = True 存入人才库，供未来再次激活使用   │
  ├─────────────────────────────────────────────────────────┤
  │  5. 面试安排（多轮）                                    │
  │     筛选通过后，为候选人创建 Interview 记录：           │
  │       第 1 轮：HR初筛（电话/视频）                      │
  │       第 2 轮：技术面（专业能力考核）                   │
  │       第 3 轮：主管面（文化契合、岗位匹配）              │
  │       第 4 轮：总经理面（高级岗位专用）                  │
  │     每轮面试 round 字段递增，状态独立跟踪               │
  ├─────────────────────────────────────────────────────────┤
  │  6. 面试评价                                            │
  │     每轮面试结束后，面试官填写 InterviewEvaluation：    │
  │       专业能力 / 沟通能力 / 文化契合度（各 1~10 分）    │
  │       综合评价：推荐录用 / 待定 / 不推荐               │
  │     全部轮次通过后，HR 发起 Offer 流程                 │
  ├─────────────────────────────────────────────────────────┤
  │  7. Offer 审批与发送                                    │
  │     HR 创建 Offer，填写薪资、试用期、入职日期等，        │
  │     经审批后发送给候选人（可生成 PDF / 电子签）：        │
  │       offer_status: 待发送→已发送→已接受/已拒绝/已过期  │
  │     同步发起背景调查（background_check）和体检          │
  │     （health_check）流程                               │
  ├─────────────────────────────────────────────────────────┤
  │  8. 入职办理（Offer → OnboardingRecord）               │
  │     候选人接受 Offer 后，HR 创建 OnboardingRecord：     │
  │       planned_date：预计入职日期（来自 Offer.start_date）│
  │       actual_date ：实际报到日期                        │
  │       checklist   ：入职清单（签劳动合同/配发设备等）    │
  ├─────────────────────────────────────────────────────────┤
  │  9. 关联员工档案（招聘闭环）                            │
  │     入职流程完成后，HR 在员工模块创建 Employee 记录，    │
  │     并将 employee_id 回写到 OnboardingRecord.employee_id│
  │     至此招聘流程正式关闭（demand.status → 已完成）      │
  └─────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
关于常量类（非 Python enum）的设计说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  本模块的状态/类型常量均采用普通类（class-style constants）而非
  Python enum.Enum，原因如下：

  1. 数据库存储可读性：
     PostgreSQL 列类型为 VARCHAR，直接存储中文字符串（如"待审批"）。
     使用 enum 时，DB 中存储的是英文枚举名（如 PENDING），
     运营人员直接查询数据库时不直观；中文值则一目了然。

  2. 前端展示直接复用：
     API 返回的值就是中文标签，前端无需额外的 i18n 映射表。

  3. 灵活性：
     class-style 常量无需注册到 SQLAlchemy 的 Enum 类型，
     新增常量值不需要迁移数据库，只需更新常量类和业务代码。

  权衡：查询时无法利用数据库枚举类型的约束；
  但对于该系统的数据量（百至千级），性能影响可忽略。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
内推奖金两阶段发放机制（InternalReferral）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  为防止内推人与候选人合谋骗取奖金，采用两阶段发放：
    第一阶段（first_payment）：候选人接受 Offer 后发放，
      金额 = reward_amount × first_payment_ratio（默认 50%）
    第二阶段（second_payment）：候选人通过试用期转正后发放，
      金额 = reward_amount × (1 - first_payment_ratio)（默认 50%）
  两次发放状态独立跟踪（待发放 → 已发放 / 已取消）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Offer → OnboardingRecord → Employee 链路说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Offer（录用意向）与 Employee（在职员工档案）之间通过
  OnboardingRecord 作为过渡桥梁：

    Offer ──(offer_id)──► OnboardingRecord ──(employee_id)──► Employee
      │                          │
      └─ offer_status: 已接受    └─ status: 已完成 时，employee_id 被写入

  OnboardingRecord.employee_id 为 NULL 时表示员工尚未正式建档；
  写入后表示招聘流程完全闭环，RecruitmentDemand 状态同步更新为「已完成」。

  candidate_name / position_name 等字段在 OnboardingRecord 中冗余存储，
  是为了在 Employee 档案创建之前仍能快速检索入职进度，
  无需 JOIN 多张表。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
人才库（talent_pool）机制
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Resume.talent_pool = True 表示该候选人被纳入人才储备库。
  触发场景：
    - 候选人综合评分优秀，但因当前无合适岗位被「婉拒」
    - 候选人主动放弃 Offer，但 HR 评价较高，希望保留联系

  人才库作用：
    - 当新的 RecruitmentDemand 创建时，HR 可优先搜索人才库
    - 直接激活历史简历，无需重新走完整筛选漏斗（节省时间）
    - 避免重复投递导致的信息混乱（一份简历绑定一个 demand，
      人才库简历可被关联到新 demand 上）
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# ---------------------------------------------------------------------------
# 状态/类型常量类（class-style constants，存储为 VARCHAR 中文字符串）
#
# 设计原则：直接将中文标签存入数据库，提升运营可读性，
# 前端展示无需额外 i18n 映射（详见文件头部设计说明）。
# ---------------------------------------------------------------------------

class DemandType:
    """招聘需求类型。

    - REPLACEMENT（补人）：岗位存在空缺，需要找人顶替离职员工
    - NEW_HEADCOUNT（增编）：新增编制，扩大团队规模
    """
    REPLACEMENT = "补人"       # replacement
    NEW_HEADCOUNT = "增编"     # new headcount


class Urgency:
    """招聘紧急程度，影响 HR 优先级排序。

    - URGENT（紧急）：需在 2 周内到岗，优先推进
    - NORMAL（普通）：1~2 个月内完成即可
    - RESERVE（储备）：无明确时间压力，提前储备人才
    """
    URGENT = "紧急"
    NORMAL = "普通"
    RESERVE = "储备"


class ApprovalStatus:
    """需求审批状态（用于 RecruitmentDemand 和 Offer 的审批流）。

    - PENDING（待审批）：已提交，等待审批人处理
    - APPROVED（已通过）：审批通过，可进入下一环节
    - REJECTED（已拒绝）：审批拒绝，需重新提交或作废
    """
    PENDING = "待审批"
    APPROVED = "已通过"
    REJECTED = "已拒绝"


class DemandStatus:
    """招聘需求的业务进展状态。

    - RECRUITING（招聘中）：需求审批通过，正在寻访候选人
    - COMPLETED（已完成）：招聘人数已满，流程关闭
    - CANCELLED（已取消）：因业务调整等原因取消
    - PAUSED（已暂停）：临时搁置（如预算冻结）
    """
    RECRUITING = "招聘中"
    COMPLETED = "已完成"
    CANCELLED = "已取消"
    PAUSED = "已暂停"


class ScreeningStatus:
    """简历筛选状态，HR 对收到的简历进行初筛后更新。

    - PENDING（待筛选）：简历刚收到，尚未处理
    - RECOMMENDED（推荐）：通过初筛，进入面试环节
    - REJECTED（不合适）：不符合要求，若质量尚可可设 talent_pool=True
    """
    PENDING = "待筛选"
    RECOMMENDED = "推荐"
    REJECTED = "不合适"


class InterviewType:
    """面试类型，对应招聘漏斗中的不同评估环节。

    - HR_SCREENING（HR初筛）：第 1 轮，HR 通过电话/视频了解基本情况
    - TECHNICAL（技术面）  ：第 2 轮，技术负责人考察专业能力
    - SUPERVISOR（主管面） ：第 3 轮，用人部门主管评估岗位匹配度
    - GM_FINAL（总经理面） ：第 4 轮，高级岗位（P7+）需总经理终面
    """
    HR_SCREENING = "HR初筛"
    TECHNICAL = "技术面"
    SUPERVISOR = "主管面"
    GM_FINAL = "总经理面"


class InterviewStatus:
    """面试记录的调度与执行状态。

    - UNSCHEDULED（待安排）：面试记录已创建，尚未确定时间
    - SCHEDULED（已安排）  ：已确定时间和面试官，等待面试
    - IN_PROGRESS（进行中）：面试正在进行（实时状态，通常持续时间短）
    - COMPLETED（已完成）  ：面试已完成，等待面试官提交评价
    - CANCELLED（已取消）  ：面试已取消（候选人放弃或临时变化）
    - RESCHEDULED（已改约）：已重新安排时间，原计划作废
    """
    UNSCHEDULED = "待安排"
    SCHEDULED = "已安排"
    IN_PROGRESS = "进行中"
    COMPLETED = "已完成"
    CANCELLED = "已取消"
    RESCHEDULED = "已改约"


class OverallResult:
    """面试评价的综合结论，由面试官在 InterviewEvaluation 中填写。

    - RECOMMEND（推荐录用）：综合评分达标，建议录用
    - PENDING（待定）      ：有疑虑，需与其他轮次面试官对齐后决策
    - NOT_RECOMMEND（不推荐）：明确不适合该岗位
    """
    RECOMMEND = "推荐录用"
    PENDING = "待定"
    NOT_RECOMMEND = "不推荐"


class OfferApprovalStatus:
    """Offer 审批状态，Offer 发送前必须通过内部审批。

    审批通过后 offer_status 方可从「待发送」变为「已发送」。
    """
    PENDING = "待审批"
    APPROVED = "已通过"
    REJECTED = "已拒绝"


class OfferStatus:
    """Offer 的发送与候选人响应状态，跟踪 Offer 全生命周期。

    - DRAFT（待发送）    ：Offer 已创建/已审批，尚未发送给候选人
    - SENT（已发送）     ：已通过邮件/系统发送，等待候选人回复
    - ACCEPTED（已接受） ：候选人明确接受，触发 OnboardingRecord 创建
    - REJECTED（已拒绝） ：候选人拒绝，需重新开始流程或启用人才库
    - EXPIRED（已过期）  ：候选人未在规定时间内答复，Offer 自动失效
    - WITHDRAWN（已撤回）：公司主动撤回（背调不通过、岗位取消等）
    """
    DRAFT = "待发送"
    SENT = "已发送"
    ACCEPTED = "已接受"
    REJECTED = "已拒绝"
    EXPIRED = "已过期"
    WITHDRAWN = "已撤回"


class BackgroundCheckStatus:
    """背景调查状态，针对关键岗位候选人进行入职前核实。

    背调内容通常包括：学历核实、工作经历核实、犯罪记录查询。
    背调不通过时，HR 应撤回 Offer（offer_status → 已撤回）。
    """
    NOT_STARTED = "未开始"
    IN_PROGRESS = "进行中"
    PASSED = "通过"
    FAILED = "未通过"


class HealthCheckStatus:
    """入职体检状态，体检不合格者依法不予录用。

    体检由候选人在指定医院自行完成，结果由 HR 录入系统。
    """
    NOT_STARTED = "未开始"
    IN_PROGRESS = "进行中"
    PASSED = "通过"
    FAILED = "未通过"


class ReferralPaymentStatus:
    """内推奖金发放状态，两阶段（Offer接受 + 试用期转正）各有独立状态。

    - PENDING（待发放）  ：条件已触发（如 Offer 已接受），等待财务走账
    - PAID（已发放）     ：奖金已打入推荐人工资或单独发放
    - CANCELLED（已取消）：候选人离职/试用期未通过，本阶段奖金作废
    """
    PENDING = "待发放"
    PAID = "已发放"
    CANCELLED = "已取消"


# ---------------------------------------------------------------------------
# 数据模型定义
# ---------------------------------------------------------------------------

class RecruitmentDemand(Base):
    """招聘需求单，由业务部门提出、HR 审批后转为正式招聘项目。

    核心业务字段说明：
      demand_type：区分补充离职空缺（补人）与新增扩编（增编），
                   影响 HC 预算审批流程。
      urgency    ：紧急程度决定 HR 处理优先级，「紧急」需 2 周内完成。
      headcount  ：本需求计划招聘的人数，与 Offer 数量对应。

    状态说明：
      approval_status：审批流状态（待审批/已通过/已拒绝）
      status         ：业务进展状态（招聘中/已完成/已取消/已暂停）
      两个状态独立维护，approval_status=已通过 后 status 才有意义。

    关联关系：
      一个需求单对应多份简历（resumes）、多场面试（interviews）
      和多个 Offer（offers），通常只有一个 Offer 最终被接受。
    """

    __tablename__ = "recruitment_demands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(Integer, ForeignKey("departments.id"), index=True, comment="部门ID")
    position_name: Mapped[str] = mapped_column(String(100), comment="职位名称")
    headcount: Mapped[int] = mapped_column(Integer, default=1, comment="招聘人数")
    # 需求类型：补人（替换离职）或增编（新岗位），决定是否需要额外 HC 审批
    demand_type: Mapped[str] = mapped_column(
        String(20), default=DemandType.NEW_HEADCOUNT, comment="需求类型: 补人/增编"
    )
    # 紧急程度影响 HR 排期与资源分配
    urgency: Mapped[str] = mapped_column(
        String(20), default=Urgency.NORMAL, comment="紧急程度: 紧急/普通/储备"
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="招聘原因")
    requirements_desc: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="岗位要求描述")
    # 薪资范围，单位：元/月，用于对外发布 JD 和内部审批参考
    salary_range_min: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True, comment="薪资下限(元)"
    )
    salary_range_max: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True, comment="薪资上限(元)"
    )
    location_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("locations.id"), nullable=True, comment="工作地点ID"
    )
    # 需求提出人（业务 HRBP 或用人部门负责人）
    requester_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id"), comment="需求提出人ID"
    )
    # 审批状态：待审批 → 已通过 / 已拒绝
    approval_status: Mapped[str] = mapped_column(
        String(20), default=ApprovalStatus.PENDING, index=True, comment="审批状态"
    )
    # 业务状态：招聘中 → 已完成 / 已取消 / 已暂停
    status: Mapped[str] = mapped_column(
        String(20), default=DemandStatus.RECRUITING, index=True, comment="需求状态"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    # 该需求下收到的所有简历（含已筛选、已淘汰的）
    resumes: Mapped[list["Resume"]] = relationship(
        "Resume", back_populates="demand", lazy="selectin"
    )
    # 该需求下安排的所有面试（多个候选人、多轮次）
    interviews: Mapped[list["Interview"]] = relationship(
        "Interview", back_populates="demand", lazy="selectin"
    )
    # 该需求下产生的所有 Offer（通常最终只有一个被接受）
    offers: Mapped[list["Offer"]] = relationship(
        "Offer", back_populates="demand", lazy="selectin"
    )


class RecruitmentChannel(Base):
    """招聘渠道配置，维护公司使用的各类招聘来源。

    channel_type 分类：
      - online     ：在线招聘平台（BOSS直聘、猎聘、前程无忧等）
      - headhunter ：猎头公司（中高端岗位，按成功录用收费）
      - internal   ：内部推荐（员工内推，低成本高匹配度）
      - onsite     ：现场招聘（招聘会、校园宣讲）

    config_json 字段存储渠道专属配置，例如：
      - online 渠道：API 密钥、职位 ID 映射
      - headhunter ：猎头公司联系方式、收费比例
    """

    __tablename__ = "recruitment_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(50), unique=True, comment="渠道名称: BOSS直聘/猎聘/内推/猎头/现场招聘"
    )
    # 渠道大类，用于统计各类渠道的简历量与到岗率
    channel_type: Mapped[str] = mapped_column(
        String(20), comment="渠道类型: online/headhunter/internal/onsite"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    # 渠道专属配置，JSON 格式，内容因渠道类型而异
    config_json: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="渠道配置(JSON)"
    )

    # 通过该渠道收到的所有简历
    resumes: Mapped[list["Resume"]] = relationship(
        "Resume", back_populates="channel", lazy="selectin"
    )


class RecruitmentPosition(Base):
    """职位字典配置，维护公司标准岗位库。"""

    __tablename__ = "recruitment_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_name: Mapped[str] = mapped_column(String(100), unique=True, index=True, comment="职位名称")
    job_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="职位类别")
    job_level: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="职位等级")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="岗位描述")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class Resume(Base):
    """候选人简历，是招聘漏斗的核心数据载体。

    设计约定：
      一份简历（Resume）对应一个需求（demand）的一次投递。
      如果同一候选人再次应聘，会创建新的 Resume 记录，
      但可通过 phone 字段识别重复候选人。

    人才库（talent_pool）标志：
      当 screening_status = 不合适 但候选人综合素质较好时，
      HR 可将 talent_pool 置为 True 将其存入人才储备库。
      人才库候选人可在未来新需求出现时被优先激活，
      无需重新经过完整的渠道获取流程（参见文件头部人才库机制说明）。

    内推标志（referrer_id）：
      若候选人由内部员工推荐，referrer_id 指向推荐人；
      同时会创建一条 InternalReferral 记录跟踪奖金发放状态。
      Resume 与 InternalReferral 为一对一关系（uselist=False）。

    简历文件：
      resume_file_url 存储上传至对象存储（OSS）的简历文件地址，
      前端通过该 URL 直接下载查看原始简历文件。
    """

    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 关联的招聘需求，每份简历必须归属到一个具体岗位需求
    demand_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recruitment_demands.id"), index=True, comment="招聘需求ID"
    )
    # 来源渠道（可选，现场投递或邮件投递时可能无渠道信息）
    channel_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("recruitment_channels.id"), nullable=True, comment="渠道ID"
    )
    # 候选人基本信息
    candidate_name: Mapped[str] = mapped_column(String(50), comment="候选人姓名")
    phone: Mapped[str] = mapped_column(String(20), index=True, comment="手机号")
    email: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="电子邮箱")
    gender: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, comment="性别")
    birth_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="出生日期")
    # 学历信息
    education: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="学历")
    school: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="毕业院校")
    major: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="专业")
    # 工作经历摘要
    work_years: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="工作年限")
    current_company: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="当前公司"
    )
    current_position: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="当前职位"
    )
    # 期望薪资，与 Offer.salary_base 对比用于薪资谈判
    expected_salary: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True, comment="期望薪资(元)"
    )
    # 简历文件 URL，存储于对象存储，前端直链下载
    resume_file_url: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="简历文件URL"
    )
    source: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="简历来源备注"
    )
    # HR 初筛状态：待筛选 → 推荐 / 不合适
    screening_status: Mapped[str] = mapped_column(
        String(20), default=ScreeningStatus.PENDING, index=True, comment="筛选状态"
    )
    screening_note: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="筛选备注"
    )
    # 人才库标志：True 表示该候选人被纳入储备，可在未来需求中优先激活
    talent_pool: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否加入人才库"
    )
    # 内推人 ID，非内推来源时为 NULL
    referrer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=True, comment="内推人ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    # 关联的招聘需求
    demand: Mapped["RecruitmentDemand"] = relationship(
        "RecruitmentDemand", back_populates="resumes", lazy="selectin"
    )
    # 来源渠道（可选）
    channel: Mapped[Optional["RecruitmentChannel"]] = relationship(
        "RecruitmentChannel", back_populates="resumes", lazy="selectin"
    )
    # 该候选人经历的所有面试轮次
    interviews: Mapped[list["Interview"]] = relationship(
        "Interview", back_populates="resume", lazy="selectin"
    )
    # 针对该简历发出的 Offer（一份简历最多一个 Offer，uselist=False）
    offer: Mapped[Optional["Offer"]] = relationship(
        "Offer", back_populates="resume", uselist=False, lazy="selectin"
    )
    # 内推记录（仅内推来源的简历有此关联，uselist=False 为一对一）
    referral: Mapped[Optional["InternalReferral"]] = relationship(
        "InternalReferral", back_populates="resume", uselist=False, lazy="selectin"
    )


class Interview(Base):
    """面试安排记录，支持多轮面试（每轮独立一条记录）。

    多轮面试通过 round 字段区分，通常流程为：
      round=1: HR初筛 → round=2: 技术面 → round=3: 主管面 → round=4: 总经理面

    同一个候选人（resume_id 相同）可有多条 Interview 记录（round 不同）。
    每轮面试结束后，面试官填写对应的 InterviewEvaluation，
    评价为「推荐录用」时才会进入下一轮或进入 Offer 阶段。

    线上面试支持：
      meeting_url 存储腾讯会议/钉钉等线上会议链接，
      location 字段存储线下面试地点。两者互斥使用。

    取消/改约：
      cancel_reason 同时用于取消和改约场景，记录操作原因。
      改约后新建一条新的 Interview 记录（round 相同），
      旧记录 status 更新为「已改约」。
    """

    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 关联候选人简历，标识是哪位候选人参加面试
    resume_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("resumes.id"), index=True, comment="简历ID"
    )
    # 关联招聘需求，用于按需求维度统计面试漏斗数据
    demand_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recruitment_demands.id"), index=True, comment="招聘需求ID"
    )
    # 面试轮次，从 1 开始递增；同一 resume_id 不同 round 代表多轮面试
    round: Mapped[int] = mapped_column(
        SmallInteger, default=1, comment="面试轮次(1/2/3)"
    )
    # 面试类型（HR初筛/技术面/主管面/总经理面）
    interview_type: Mapped[str] = mapped_column(
        String(20), comment="面试类型: HR初筛/技术面/主管面/总经理面"
    )
    # 面试官（系统员工），可选（外部猎头面试时可为空）
    interviewer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=True, comment="面试官ID"
    )
    # 计划时间与实际时间分开存储，用于计算面试准时率
    scheduled_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="计划面试时间"
    )
    actual_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="实际面试时间"
    )
    # 线下面试地点（与 meeting_url 二选一）
    location: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="面试地点"
    )
    # 线上面试链接（腾讯会议/钉钉/Zoom 等）
    meeting_url: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="线上面试链接"
    )
    # 面试状态：待安排 → 已安排 → 进行中 → 已完成 / 已取消 / 已改约
    status: Mapped[str] = mapped_column(
        String(20), default=InterviewStatus.UNSCHEDULED, index=True, comment="面试状态"
    )
    # 取消或改约原因（status 变更为「已取消」或「已改约」时必填）
    cancel_reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="取消/改约原因"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )

    # 候选人简历
    resume: Mapped["Resume"] = relationship(
        "Resume", back_populates="interviews", lazy="selectin"
    )
    # 所属招聘需求
    demand: Mapped["RecruitmentDemand"] = relationship(
        "RecruitmentDemand", back_populates="interviews", lazy="selectin"
    )
    # 该场面试的评价报告（一场面试对应一份评价，uselist=False 为一对一）
    evaluation: Mapped[Optional["InterviewEvaluation"]] = relationship(
        "InterviewEvaluation", back_populates="interview", uselist=False, lazy="selectin"
    )


class InterviewEvaluation(Base):
    """面试评价，面试官在面试完成后填写的结构化评估报告。

    与 Interview 为严格一对一关系（interview_id UNIQUE 约束）。

    评分维度（均为 1~10 分）：
      professional_score  ：专业能力（技术知识、行业经验、解决问题能力）
      communication_score ：沟通能力（表达清晰度、倾听能力、逻辑思维）
      culture_fit_score   ：文化契合度（价值观对齐、团队协作意愿、抗压能力）
      overall_score       ：面试官综合打分（并非前三者均值，由面试官主观给出）

    技能测试：
      部分技术岗位面试中会安排笔试或上机测试，
      has_skill_test = True 时，skill_test_score 记录测试成绩。

    综合评价（overall_result）是关键决策字段：
      「推荐录用」：该轮面试通过，可进入下一轮或 Offer 阶段
      「待定」    ：与其他面试官讨论后再决定
      「不推荐」  ：本轮淘汰，流程终止
    """

    __tablename__ = "interview_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # UNIQUE 约束保证每场面试只能有一份评价
    interview_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("interviews.id"), unique=True, comment="面试ID"
    )
    # 评价人（填写评估报告的面试官，通常与 Interview.interviewer_id 相同）
    interviewer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id"), comment="评价人(面试官)ID"
    )
    # 结构化评分维度（1~10 分，允许为 NULL 表示该维度未评估）
    professional_score: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True, comment="专业能力评分(1-10)"
    )
    communication_score: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True, comment="沟通能力评分(1-10)"
    )
    culture_fit_score: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True, comment="文化契合度评分(1-10)"
    )
    # 综合评分（面试官主观打分，非前三项均值）
    overall_score: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True, comment="综合评分(1-10)"
    )
    # 综合评价结论：推荐录用 / 待定 / 不推荐（关键决策字段）
    overall_result: Mapped[str] = mapped_column(
        String(20), default=OverallResult.PENDING, comment="综合评价: 推荐录用/待定/不推荐"
    )
    # 文字评语
    strengths: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="优势")
    weaknesses: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="不足")
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="备注/评语")
    # 技能测试信息（技术岗位专用）
    has_skill_test: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否进行技能测试"
    )
    skill_test_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True, comment="技能测试分数"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )

    # 关联的面试记录
    interview: Mapped["Interview"] = relationship(
        "Interview", back_populates="evaluation", lazy="selectin"
    )


class Offer(Base):
    """录用通知书（Offer），是完成面试后向候选人正式发出录用邀约的凭证。

    Offer 与招聘流程的关键节点关系：
      - 一份 Offer 对应一份简历（resume_id UNIQUE 约束，一人一 Offer）
      - Offer 被接受（offer_status = 已接受）后，触发创建 OnboardingRecord
      - OnboardingRecord 完成、Employee 档案建立后，招聘流程正式关闭

    两级状态字段：
      approval_status：内部审批状态（待审批/已通过/已拒绝），
                       Offer 必须经内部审批通过后才能发送给候选人。
      offer_status   ：Offer 发送与候选人响应状态（详见 OfferStatus 类注释）。

    薪资结构：
      salary_base  ：月度固定基本薪资（单位：元）
      salary_total ：综合年薪，含基本薪资 × 12 + 绩效奖金 + 年终奖估算

    试用期设置：
      probation_months       ：试用期月数（法定上限 6 个月，默认 3 个月）
      probation_salary_ratio ：试用期薪资占正式薪资的比例（默认 80%，即转正前薪资打 8 折）

    背调与体检：
      background_check_required = True 时需完成背景调查（关键岗位必要）；
      两项状态（background_check_status / health_check_status）独立跟踪，
      任一未通过均可触发 Offer 撤回（offer_status → 已撤回）。

    文件支持：
      pdf_url  ：生成的 Offer PDF 文件地址，可发送给候选人
      sign_url ：电子签名完成后的已签 Offer 文件地址
    """

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # UNIQUE 约束确保一份简历最多对应一个 Offer
    resume_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("resumes.id"), unique=True, comment="简历ID"
    )
    # 关联招聘需求，用于在需求维度统计 Offer 接受率
    demand_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recruitment_demands.id"), index=True, comment="招聘需求ID"
    )
    # 录用岗位名称（可与需求单的 position_name 略有不同，以实际谈妥为准）
    position_name: Mapped[str] = mapped_column(String(100), comment="录用职位名称")
    department_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("departments.id"), comment="部门ID"
    )
    location_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("locations.id"), nullable=True, comment="工作地点ID"
    )
    # 薪资信息：月度基本薪资 + 综合年薪
    salary_base: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), comment="基本薪资(元/月)"
    )
    salary_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), comment="综合年薪(元)"
    )
    # 试用期配置：默认 3 个月、试用期薪资 = 正式薪资 × 0.80
    probation_months: Mapped[int] = mapped_column(
        SmallInteger, default=3, comment="试用期月数"
    )
    probation_salary_ratio: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=Decimal("0.80"), comment="试用期薪资比例"
    )
    # 预计入职日期（由候选人与 HR 协商后填写，写入 OnboardingRecord.planned_date）
    start_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="预计入职日期"
    )
    # 内部审批状态：必须通过后才能发送给候选人
    approval_status: Mapped[str] = mapped_column(
        String(20), default=OfferApprovalStatus.PENDING, index=True, comment="审批状态"
    )
    # Offer 与候选人交互状态（详见 OfferStatus 类注释）
    offer_status: Mapped[str] = mapped_column(
        String(20), default=OfferStatus.DRAFT, index=True, comment="Offer状态"
    )
    # Offer 文档：PDF 正文 URL 与电子签名后 URL
    pdf_url: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="Offer PDF文件URL"
    )
    sign_url: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="电子签名URL"
    )
    # 是否需要背景调查（管理类、财务类岗位通常设为 True）
    background_check_required: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否需要背景调查"
    )
    # 背调状态（仅 background_check_required=True 时有意义）
    background_check_status: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, default=None, comment="背调状态"
    )
    # 体检状态（所有录用员工均需完成入职体检）
    health_check_status: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, default=None, comment="体检状态"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )

    # 关联候选人简历（通过简历可溯源整个面试过程）
    resume: Mapped["Resume"] = relationship(
        "Resume", back_populates="offer", lazy="selectin"
    )
    # 关联招聘需求（用于更新需求完成状态）
    demand: Mapped["RecruitmentDemand"] = relationship(
        "RecruitmentDemand", back_populates="offers", lazy="selectin"
    )


class InternalReferral(Base):
    """员工内推记录，绑定推荐人与被推荐简历，实现两阶段奖金发放管控。

    内推奖金两阶段发放机制（详见文件头部说明）：
      第一阶段：候选人接受 Offer 后触发
        金额 = reward_amount × first_payment_ratio（默认 50%）
        状态字段：first_payment_status

      第二阶段：候选人转正后触发（试用期结束）
        金额 = reward_amount × (1 - first_payment_ratio)（默认 50%）
        状态字段：second_payment_status

      两个状态独立跟踪，互不影响：
        例如第一阶段已发放，但候选人试用期未通过，
        第二阶段直接置为「已取消」，无需退款。

    candidate_name / candidate_phone / candidate_email 冗余存储：
      即使关联简历记录被删除（如候选人要求删除个人信息），
      这些字段仍能保留奖金发放所需的最小必要信息，
      确保财务发放流程不受影响。

    candidate_status 字段：
      跟踪被推荐候选人在招聘漏斗中的当前进展（如「简历筛选」→「面试中」→
      「已发 Offer」→「已入职」），方便推荐人查询自己推荐的候选人进度。
    """

    __tablename__ = "internal_referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 推荐人（发起内推的在职员工）
    referrer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id"), index=True, comment="推荐人ID"
    )
    # UNIQUE 约束：一份简历只能绑定一条内推记录，防止重复计奖
    resume_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("resumes.id"), unique=True, comment="简历ID"
    )
    # 适用的奖励规则类型（对应 InternalReferralRule.rule_name，如"P7及以上"）
    reward_rule_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="奖励规则类型"
    )
    # 奖励总额（元），通常从 InternalReferralRule 按职级查表填入
    reward_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True, comment="奖励总额(元)"
    )
    # 第一阶段发放比例（默认 0.50，即奖励总额的 50%）
    # 第一阶段触发条件：候选人接受 Offer（offer_status = 已接受）
    first_payment_ratio: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=Decimal("0.50"), comment="首次发放比例"
    )
    # 第一阶段状态：待发放 → 已发放 / 已取消
    first_payment_status: Mapped[str] = mapped_column(
        String(20), default=ReferralPaymentStatus.PENDING, comment="首次发放状态"
    )
    # 第二阶段状态：触发条件为候选人通过试用期转正
    # 第二阶段金额 = reward_amount × (1 - first_payment_ratio)
    second_payment_status: Mapped[str] = mapped_column(
        String(20), default=ReferralPaymentStatus.PENDING, comment="二次发放状态"
    )
    # 候选人信息冗余存储（即使简历删除也能保留财务发放所需信息）
    candidate_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="候选人姓名（冗余存储）")
    candidate_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="候选人手机号")
    candidate_email: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="候选人邮箱")
    # 候选人当前在招聘漏斗中的进展状态（面向推荐人展示）
    candidate_status: Mapped[str] = mapped_column(String(30), default="简历筛选", comment="候选人当前状态")
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="备注")
    expected_join_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="预计入职日期")
    # 最近一次奖金发放时间（两阶段共用，记录最后操作时间）
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="发放时间")
    # 执行发放操作的 HR 或财务人员 ID
    paid_by: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id"), nullable=True, comment="发放操作人")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )

    # 关联简历（通过简历可溯源到候选人信息与需求）
    resume: Mapped["Resume"] = relationship(
        "Resume", back_populates="referral", lazy="selectin"
    )


class InternalReferralRule(Base):
    """内推奖金规则配置，按职类和职级定义奖励金额，HR 可灵活调整。

    规则查找逻辑：
      创建内推记录时，服务层根据候选人应聘岗位的
      job_category（技术/销售/职能）和 job_level（P5/P6/P7 等）
      匹配最合适的规则，将对应 reward_amount 填入 InternalReferral。

    示例规则配置：
      rule_name="P7及以上技术岗", job_category="技术", job_level="P7", reward_amount=5000
      rule_name="职能通用",        job_category="职能", job_level=NULL,  reward_amount=2000

    is_active = False 的规则不参与匹配，用于下线旧规则而保留历史记录。
    """
    __tablename__ = "internal_referral_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 规则名称，人工可读描述（如"P7及以上技术岗内推奖励"）
    rule_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="规则名称，如P7及以上")
    # 职位类别（技术/销售/职能），NULL 表示适用所有类别（兜底规则）
    job_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="职位类别：技术/销售/职能")
    # 职级（如 P5/P6/P7），NULL 表示适用该类别所有职级
    job_level: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="职级，如P5/P6/P7")
    # 该规则对应的奖励总金额（元），后续按两阶段比例拆分发放
    reward_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("3000"), comment="奖励金额")
    description: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="规则说明")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Offer 模板（OfferTemplate）
# ---------------------------------------------------------------------------

class OfferTemplate(Base):
    """Offer 模板，为常见岗位预设薪资结构，提升 HR 发 Offer 效率。

    使用场景：
      公司针对每个标准岗位（如"Java 研发工程师 P6"）维护一套 Offer 模板，
      其中预设了基本薪资范围、试用期时长、薪资比例等。
      HR 发 Offer 时选择模板，自动填充默认值，只需微调即可生成正式 Offer。

    模板与 Offer 的关系：
      模板仅作为数据填充参考，创建 Offer 时拷贝模板数据到 Offer 记录，
      后续对模板的修改不影响已发出的 Offer（数据独立存储）。
    """

    __tablename__ = "offer_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 模板名称（如"研发工程师 P6 标准 Offer"）
    name: Mapped[str] = mapped_column(String(100), comment="模板名称")
    position_name: Mapped[str] = mapped_column(String(100), comment="岗位名称")
    department_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("departments.id"), nullable=True, comment="部门ID"
    )
    # 参考薪资（模板提供默认值，HR 在实际 Offer 中可按候选人情况调整）
    salary_base: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True, comment="基本薪资(元/月)"
    )
    salary_total: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True, comment="综合年薪(元)"
    )
    # 试用期默认配置
    probation_months: Mapped[int] = mapped_column(
        Integer, default=3, comment="试用期月数"
    )
    probation_salary_ratio: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=Decimal("0.80"), comment="试用期薪资比例"
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="备注")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )


# ---------------------------------------------------------------------------
# 入职状态常量
# ---------------------------------------------------------------------------

class OnboardingStatus:
    """入职记录状态，跟踪候选人从 Offer 接受到正式入职的全过程。

    - PENDING（待入职）   ：OnboardingRecord 已创建，等待候选人报到
    - IN_PROGRESS（入职中）：候选人已报到，正在完成入职清单（签合同/领设备等）
    - COMPLETED（已完成） ：入职清单全部完成，employee_id 已关联，招聘闭环
    - CANCELLED（已取消） ：候选人临时放弃入职（违约），流程终止
    """
    PENDING = "待入职"
    IN_PROGRESS = "入职中"
    COMPLETED = "已完成"
    CANCELLED = "已取消"


# ---------------------------------------------------------------------------
# 入职记录（OnboardingRecord）
# ---------------------------------------------------------------------------

class OnboardingRecord(Base):
    """入职记录，是招聘流程与员工档案管理之间的过渡桥梁。

    创建时机：
      候选人接受 Offer（offer_status = 已接受）后，HR 手动或系统自动
      创建 OnboardingRecord，开始跟踪入职办理进度。

    核心链路（招聘闭环）：
      Offer ──(offer_id)──► OnboardingRecord ──(employee_id)──► Employee
        │                          │
        └─ offer_status = 已接受   └─ status = 已完成时写入 employee_id

      employee_id 在入职记录创建时为 NULL；
      HR 在员工管理模块完成建档后，将 Employee.id 回写此字段，
      同时将 RecruitmentDemand.status 更新为「已完成」，招聘正式关闭。

    入职清单（checklist）：
      JSON 格式存储入职所需完成的各项事务，格式为：
        {"签署劳动合同": true, "配发工卡": false, "开通系统账号": false, ...}
      HR 在候选人报到时逐项勾选，status 变为「已完成」的前提是
      所有必填项均已完成（由服务层校验）。

    冗余存储字段：
      candidate_name / position_name / department_id 在此表冗余存储，
      是为了在 Employee 档案创建之前仍能以高效方式（无需多表 JOIN）
      检索和展示入职进度信息。
    """

    __tablename__ = "onboarding_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 关联 Offer，溯源录用条件（薪资、职位、入职日期等均以 Offer 为准）
    offer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("offers.id"), comment="Offer ID"
    )
    # 候选人姓名（冗余存储，Employee 建档前仍可快速检索）
    candidate_name: Mapped[str] = mapped_column(
        String(50), comment="候选人姓名(冗余存储，便于查询)"
    )
    # 入职岗位名称（冗余存储，与 Offer.position_name 保持一致）
    position_name: Mapped[str] = mapped_column(String(100), comment="岗位名称")
    department_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("departments.id"), nullable=True, comment="部门ID"
    )
    # 预计入职日期（来自 Offer.start_date，候选人承诺的报到日期）
    planned_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="预计入职日期"
    )
    # 实际报到日期（候选人实际来公司办理入职手续的日期）
    actual_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="实际入职日期"
    )
    # 入职状态：待入职 → 入职中 → 已完成 / 已取消
    status: Mapped[str] = mapped_column(
        String(20), default=OnboardingStatus.PENDING, index=True, comment="入职状态"
    )
    # 关联建档后的员工 ID（NULL = 尚未建档；非 NULL = 招聘流程完全关闭）
    employee_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=True, comment="入职后关联员工ID"
    )
    # 入职清单，JSON 格式: {"任务名称": bool, ...}，HR 逐项确认完成
    checklist: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="入职清单(key:bool)"
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="备注")
    # 负责该入职办理的 HR 人员
    hr_owner_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=True, comment="负责HR的员工ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )
