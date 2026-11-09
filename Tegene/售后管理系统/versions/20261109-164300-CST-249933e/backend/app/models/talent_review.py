"""
人才盘点模型 - Talent Review Models
====================================

本模块定义人才盘点体系的四张核心数据表，覆盖"盘点会议 → 九宫格评估 → 继任计划 → 候选人管理"完整链路。

业务背景
--------
人才盘点（Talent Review）是人力资源规划的核心环节，通常由 HR 发起、管理层参与：
1. 建立盘点会议（TalentReviewSession），确定范围和周期（年度/中期/专项）。
2. 对范围内员工逐一填写九宫格评估（TalentReviewEntry），从绩效和潜力两个维度定位人才。
3. 针对高管/关键岗位制定继任计划（SuccessionPlan），提前储备接班人。
4. 在继任计划下登记候选人（SuccessionCandidate），并标注候选人的就绪时间线。

九宫格（Nine-Box Grid）说明
---------------------------
九宫格是常见的人才评估工具，以「绩效 × 潜力」两轴构成 3×3 共 9 个象限：

    潜力\绩效 |  1(低)   |  2(中)   |  3(高)
    --------- | -------- | -------- | --------
    3(高)     |  1-3     |  2-3     |  3-3 ★ 明星员工
    2(中)     |  1-2     |  2-2     |  3-2
    1(低)     |  1-1 ✗   |  2-1     |  3-1

nine_box_position 格式："{performance}-{potential}"，如"3-3"表示高绩效高潜力（明星员工）；
"1-1"表示低绩效低潜力（待改善员工）。

数据库存储与 Schema 层转换说明
-------------------------------
flight_risk（离职风险）和 criticality（关键程度）字段：
  - 数据库中存储中文值，如：低/中/高/关键
  - Schema（Pydantic）层做中英文映射，API 层统一使用英文枚举值（low/medium/high/critical）

readiness（就绪度）字段：
  - 数据库中存储中文值，如：即时就绪/1-2年内/3年以上
  - Schema 层映射到英文枚举：ready_now/ready_1_year/ready_2_years/developing

包含数据表
----------
- talent_review_sessions      盘点会议/周期
- talent_review_entries       盘点条目（九宫格评估结果，每会议每人一条）
- succession_plans            关键岗位继任计划
- succession_candidates       继任候选人
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class TalentReviewSession(Base):
    """
    人才盘点会议/周期。

    每次人才盘点以一个 Session 为单位管理。HR 建立 Session 后，将其状态推进为
    in_progress，并逐步为范围内员工填写评估条目（TalentReviewEntry）；
    全部评估完成后将状态置为 completed，Session 进入只读状态。

    盘点类型（review_type）：
        annual      — 年度盘点，通常每年末或年初开展，全公司或大范围覆盖
        mid_year    — 中期盘点，年中对关键岗位/高潜人才的跟进评估
        special     — 专项盘点，针对特定业务事件（并购、组织变革等）触发

    状态流转：
        draft → in_progress → completed

    scope_department_ids 字段存储 JSON 数组，如 "[1, 3, 7]"，表示本次盘点
    仅覆盖 ID 为 1、3、7 的部门。为 NULL 表示全公司范围。

    级联删除：删除 Session 时，所有关联的 TalentReviewEntry 一并删除。
    """

    __tablename__ = "talent_review_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="盘点名称"
    )
    review_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="盘点类型: annual/mid_year/special",
    )
    # 盘点所属年份，用于前端按年份筛选（如：2024年度盘点）
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # JSON 数组字符串，存储参与盘点的部门 ID 列表；NULL 表示不限部门（全公司）
    scope_department_ids: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="范围部门ID列表(JSON数组)"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
        index=True,
        comment="状态: draft/in_progress/completed",
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="盘点说明"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # 关联的所有评估条目（一对多），级联删除
    entries: Mapped[list["TalentReviewEntry"]] = relationship(
        "TalentReviewEntry",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TalentReviewEntry(Base):
    """
    人才盘点评估条目，每次盘点中每位员工对应一条记录。

    本表是九宫格评估的核心数据载体。每条记录记录了某次盘点会议（session）
    对某位员工（employee）在两个维度的评分：绩效（performance）和潜力（potential），
    并由此自动计算出该员工在九宫格中的位置（nine_box_position）。

    九宫格位置编码规则
    ------------------
    nine_box_position = "{performance_rating}-{potential_rating}"
    常见位置含义：
        "3-3"  — 高绩效+高潜力 = 明星员工（Star），优先晋升/保留
        "3-2"  — 高绩效+中潜力 = 高效能者（High Performer）
        "2-3"  — 中绩效+高潜力 = 高潜力员工（High Potential），需要培养
        "1-1"  — 低绩效+低潜力 = 待改善员工，需要 PIP 或转岗
        "2-2"  — 中绩效+中潜力 = 核心中坚（Core Player）

    离职风险（flight_risk）
    -----------------------
    数据库存储中文值：低 / 中 / 高
    API 层映射到英文：low / medium / high
    HR 在评估时填写，用于触发保留措施（如加薪、晋升、轮岗等）。

    唯一约束：同一 session 内同一 employee 只能有一条评估记录（业务层保证）。
    """

    __tablename__ = "talent_review_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 所属盘点会议 ID，级联删除（会议删除时本条记录也随之删除）
    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("talent_review_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 被评估员工 ID
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 绩效维度评分：1=低（未达标）/ 2=中（达标）/ 3=高（超预期）
    performance_rating: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="绩效维度: 1=低/2=中/3=高"
    )
    # 潜力维度评分：1=低（现有岗位）/ 2=中（可晋升一级）/ 3=高（高管潜力）
    potential_rating: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="潜力维度: 1=低/2=中/3=高"
    )
    # 九宫格坐标，由 performance_rating 和 potential_rating 拼接而成，格式如"3-3"
    nine_box_position: Mapped[str | None] = mapped_column(
        String(10), nullable=True, comment="九宫格位置如'3-3','1-2'等"
    )
    # 离职风险评估，数据库存中文（低/中/高），Schema 层转换为英文枚举
    flight_risk: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="low",
        comment="离职风险: low/medium/high",
    )
    # 是否被标记为关键人才，影响保留预算分配和特殊激励计划
    is_key_talent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否关键人才"
    )
    # HR 或管理者对该员工的发展建议，如"建议调任技术管理序列"
    development_suggestion: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="发展建议"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # 关联员工对象（selectin 加载，避免 N+1 查询）
    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", lazy="selectin"
    )
    # 关联所属盘点会议
    session: Mapped["TalentReviewSession"] = relationship(
        "TalentReviewSession", back_populates="entries", lazy="selectin"
    )


class SuccessionPlan(Base):
    """
    关键岗位继任计划。

    继任计划（Succession Plan）用于识别和培养公司关键岗位的潜在接班人，确保关键
    岗位空缺时有足够储备。通常在年度人才盘点后，由 HR 与业务负责人共同制定。

    关键程度（criticality）
    -----------------------
    数据库存储中文值：低 / 中 / 高 / 关键
    API 层映射为英文：low / medium / high / critical

    "critical"（关键）一般对应高管岗位（VP 及以上），一旦出现空缺对公司运营影响
    极大，需要保证至少 2-3 名候选人处于不同就绪阶段。

    岗位与人的关系
    --------------
    - key_position：记录岗位名称字符串（非外键），灵活处理岗位架构调整
    - department_id：岗位所在部门，用于部门级筛选
    - current_holder_id：当前在任者（员工外键），可为空（如尚未设岗的预留职位）

    状态（status）
    --------------
    active    — 继任计划生效中，持续跟踪候选人进展
    inactive  — 临时暂停（如当前岗位即将裁撤），保留历史数据

    级联删除：删除继任计划时，所有关联候选人记录一并删除。
    """

    __tablename__ = "succession_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 关键岗位名称，如"首席技术官"、"华南区销售总监"，存字符串而非职位外键
    key_position: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="关键岗位名称"
    )
    # 岗位归属部门，用于按部门查询继任计划；部门撤销时置 NULL
    department_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 当前在职者员工 ID；员工离职时置 NULL，表示岗位空缺
    current_holder_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 关键程度，数据库存中文（低/中/高/关键），Schema 层转英文枚举
    criticality: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="high",
        comment="关键程度: low/medium/high/critical",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", comment="状态"
    )
    notes: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="备注"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # 当前在任者员工对象（通过 foreign_keys 消歧，因 Employee 有多处外键引用）
    current_holder: Mapped["Employee | None"] = relationship(  # noqa: F821
        "Employee", foreign_keys=[current_holder_id], lazy="selectin"
    )
    # 该岗位下所有候选人列表，级联删除
    candidates: Mapped[list["SuccessionCandidate"]] = relationship(
        "SuccessionCandidate",
        back_populates="plan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class SuccessionCandidate(Base):
    """
    关键岗位继任候选人。

    每条记录表示某个员工（candidate）被纳入某个继任计划（plan）的候选池，
    并标注其预计接任时间线（readiness）和需要补充的发展行动。

    就绪度（readiness）时间线
    -------------------------
    数据库存储中文值，Schema 层映射到英文枚举，对应关系如下：

        即时就绪    → ready_now        (0-1 年内可接任，能力已基本具备)
        1-2年内     → ready_1_year     (1-2 年内可就绪，有一定能力缺口)
        3年以上     → ready_2_years    (2-3 年内可就绪，需要系统培养)
        developing  → developing       (3 年以上，长期储备培养)

    就绪度直接影响继任风险评估：若一个 critical 岗位没有任何 ready_now 候选人，
    则该岗位存在"空白窗口"风险，需 HR 向管理层预警。

    发展行动（development_actions）
    --------------------------------
    存储 JSON 数组，每个元素描述一项具体行动，例如：
        [
            {"action": "轮岗至海外市场部", "deadline": "2025-06"},
            {"action": "参加外部高管领导力培训", "deadline": "2025-03"}
        ]
    由 HR 和候选人的直属上级共同制定，定期在盘点会议中回顾进展。
    """

    __tablename__ = "succession_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 所属继任计划 ID，级联删除（计划删除时候选人记录一并删除）
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("succession_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 候选员工 ID；员工离职后级联删除此候选人记录
    candidate_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 就绪度，数据库存中文（即时就绪/1-2年内/3年以上），Schema 层转英文枚举
    readiness: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="就绪度: ready_now/ready_1_year/ready_2_years/developing",
    )
    # 发展行动计划，JSON 数组，记录需要完成的具体培养措施及截止日期
    development_actions: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="发展行动(JSON)"
    )
    notes: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="备注"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # 候选人员工对象
    candidate: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", lazy="selectin"
    )
    # 所属继任计划对象
    plan: Mapped["SuccessionPlan"] = relationship(
        "SuccessionPlan", back_populates="candidates", lazy="selectin"
    )
