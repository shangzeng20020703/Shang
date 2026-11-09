"""
绩效管理模块数据模型 - Performance Management Models
======================================================

本模块包含绩效考核体系所需的全部 5 张数据库表：

  1. PerformanceTemplate   — 考核模板（按岗位类型预设指标与权重）
  2. PerformanceCycle      — 考核周期（季度 / 半年 / 年度）
  3. PerformanceIndicator  — 考核指标（从属于模板，构成评分维度）
  4. PerformanceEvaluation — 绩效评估记录（每人每周期一条，存储自评与上级评分）
  5. PerformanceGoal       — KPI/OKR 目标（支持树形结构）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
完整绩效评估业务流程
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌─────────────────────────────────────────────────────────┐
  │  1. 模板配置                                            │
  │     HR 在 PerformanceTemplate 中为不同岗位类型           │
  │     （工厂 / 技术 / 管理 / 通用 / 试用期）创建考核模板，  │
  │     并在 PerformanceIndicator 中配置各维度指标与权重      │
  ├─────────────────────────────────────────────────────────┤
  │  2. 周期创建                                            │
  │     HR 在 PerformanceCycle 中创建本次考核周期（如         │
  │     2026Q1），设置开始/结束日期及强制分布比例             │
  ├─────────────────────────────────────────────────────────┤
  │  3. 员工自评（status: pending_self）                    │
  │     系统批量生成 PerformanceEvaluation 记录，员工在      │
  │     self_scores_json 字段中填写各指标自评分              │
  ├─────────────────────────────────────────────────────────┤
  │  4. 上级评分（status: pending_superior）                │
  │     直属上级（evaluator_id）在 superior_scores_json     │
  │     字段中对各指标打分                                   │
  ├─────────────────────────────────────────────────────────┤
  │  5. 加权计算（系统自动）                                 │
  │     weighted_score = self_score × 0.30                 │
  │                    + superior_score × 0.70             │
  │     （权重比例存储在模板中，默认 3:7）                    │
  ├─────────────────────────────────────────────────────────┤
  │  6. 等级映射（强制分布约束）                             │
  │     按全部门 weighted_score 排名强制划定等级：           │
  │       A 级：前 10%（优秀）                              │
  │       B 级：次 30%（良好）                              │
  │       C 级：中间 50%（达标）                            │
  │       D 级：末 10%（待改进）                            │
  │     强制分布比例存储在 PerformanceCycle.forced_distribution_json │
  ├─────────────────────────────────────────────────────────┤
  │  7. 绩效系数映射                                        │
  │     等级对应绩效工资系数（影响当季薪资计算）：             │
  │       A → 1.2（绩效工资 × 120%）                       │
  │       B → 1.0（绩效工资 × 100%，不奖不罚）              │
  │       C → 0.8（绩效工资 × 80%）                        │
  │       D → 0.6（绩效工资 × 60%）                        │
  │     系数存储在 PerformanceEvaluation.performance_coefficient │
  ├─────────────────────────────────────────────────────────┤
  │  8. 绩效面谈（status: completed）                       │
  │     上级与员工进行绩效面谈，结果记录到                    │
  │     interview_record / interview_date                  │
  ├─────────────────────────────────────────────────────────┤
  │  9. 影响薪资                                            │
  │     季度考核完成后，薪资服务读取 performance_coefficient  │
  │     计算该季度绩效工资实发金额                            │
  └─────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OKR 目标树结构（PerformanceGoal.parent_id 自引用）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  OKR 模式下，目标分为两个层次：
    - Objective（目标）：parent_id 为 NULL，goal_type = "okr"
    - Key Result（关键结果）：parent_id 指向所属 Objective

  示例树：
    Objective: "提升研发团队交付效率"  (id=10, parent_id=NULL)
      ├── KR1: "Q1 迭代交付率 ≥ 90%"  (id=11, parent_id=10)
      └── KR2: "线上 Bug 数量下降 30%" (id=12, parent_id=10)

  KPI 模式下所有目标 parent_id 均为 NULL，为平铺结构。
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# ---------------------------------------------------------------------------
# 枚举类型 — 目标类型与状态
# ---------------------------------------------------------------------------

class GoalType(str, enum.Enum):
    """目标类型枚举。

    - kpi：关键绩效指标，量化考核，适用于工厂、销售等可量化岗位
    - okr：目标与关键结果，适用于研发、管理等需要定性描述的岗位
    """
    kpi = "kpi"
    okr = "okr"


class GoalStatus(str, enum.Enum):
    """目标状态枚举，反映目标在生命周期中的当前阶段。

    - draft：草稿，目标刚创建尚未激活，可自由修改
    - active：进行中，目标已生效，员工正在执行
    - completed：已完成，目标周期结束并已评分
    - cancelled：已取消，因业务调整或人员变动而作废
    """
    draft = "draft"
    active = "active"
    completed = "completed"
    cancelled = "cancelled"


# ---------------------------------------------------------------------------
# 考核模板 — PerformanceTemplate
# ---------------------------------------------------------------------------

class PerformanceTemplate(Base):
    """考核模板，按岗位类型（工厂 / 技术 / 管理 / 通用 / 试用期）预设考核指标与评分权重。

    一张模板对应一类岗位的考核方案，包含：
      - 自评与上级评分的权重比（默认 3:7，即自评 30%、上级 70%）
      - 若干 PerformanceIndicator 指标条目（通过 indicators 关联）

    HR 可以在系统中维护多套模板（例如工厂模板侧重安全与产量，
    技术模板侧重代码质量与交付），并在周期发起时为每位员工选择合适的模板。

    indicators_json 字段提供额外的 JSON 格式指标配置，主要用于批量导入场景；
    正式运行时以关联的 PerformanceIndicator 记录为准。
    """

    __tablename__ = "performance_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="模板名称"
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="模板说明"
    )
    position_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="适用岗位类型: 工厂/技术/管理/通用/试用期",
    )
    indicators_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="指标配置JSON(兼容批量导入)"
    )
    # 自评与上级评分的权重，两者之和必须等于 1.00（即 100%）
    # 系统默认配置: 自评 0.30、上级 0.70
    # 加权公式: weighted_score = self_score × self_eval_weight
    #                           + superior_score × superior_eval_weight
    self_eval_weight: Mapped[Decimal] = mapped_column(
        Numeric(4, 2),
        nullable=False,
        default=Decimal("0.30"),
        comment="自评权重(如0.30表示30%)",
    )
    superior_eval_weight: Mapped[Decimal] = mapped_column(
        Numeric(4, 2),
        nullable=False,
        default=Decimal("0.70"),
        comment="上级评分权重(如0.70表示70%)",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否启用"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # 该模板包含的所有考核指标，按 sort_order 升序排列
    # cascade="all, delete-orphan"：删除模板时自动级联删除所有指标
    indicators: Mapped[list["PerformanceIndicator"]] = relationship(
        "PerformanceIndicator",
        back_populates="template",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PerformanceIndicator.sort_order",
    )


# ---------------------------------------------------------------------------
# 考核周期 — PerformanceCycle
# ---------------------------------------------------------------------------

class PerformanceCycle(Base):
    """考核周期，代表一次完整的绩效考核活动（如 2026年Q1 季度考核）。

    周期类型支持：
      - quarterly   ：季度考核（最常用，与绩效工资直接挂钩）
      - semi_annual ：半年度考核
      - annual      ：年度考核（通常用于晋升、调薪参考）

    状态流转：
      not_started → self_eval → superior_eval → completed

      1. not_started：周期已创建，尚未开放员工自评
      2. self_eval  ：员工自评阶段开放（PerformanceEvaluation.status = pending_self）
      3. superior_eval：自评截止，开放上级评分（status = pending_superior）
      4. completed  ：全部评分完成，加权分数与等级已确定

    强制分布（forced_distribution_json）：
      存储各等级的人数比例上限，格式示例：
        {"A": 10, "B": 30, "C": 50, "D": 10}
      含义：A 级不超过 10%，B 级不超过 30%，C 级约 50%，D 级至少 10%。
      服务层在计算等级时读取该配置，按全量 weighted_score 排名后进行强制划档。
    """

    __tablename__ = "performance_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="周期名称 (如: 2026年Q1)"
    )
    cycle_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="周期类型: quarterly/semi_annual/annual",
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # 季度字段仅在 cycle_type = quarterly 时有效（取值 1~4）
    # 年度考核（annual）此字段存 NULL
    quarter: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="季度(1-4), 年度考核为null"
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, comment="开始日期")
    end_date: Mapped[date] = mapped_column(Date, nullable=False, comment="结束日期")
    # 状态流转: not_started → self_eval → superior_eval → completed
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="not_started",
        index=True,
        comment="状态: not_started/self_eval/superior_eval/completed",
    )
    # 强制分布配置，JSON 格式，例如 {"A":10,"B":30,"C":50,"D":10}
    # 各数字表示该等级人数占参与考核总人数的百分比上限
    forced_distribution_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment='强制分布JSON, 如: {"A":10,"B":30,"C":50,"D":10}',
    )
    config_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="前端扩展配置JSON（模板、展示类型、阶段配置、参与范围等）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # 该周期下所有员工的绩效评估记录
    evaluations: Mapped[list["PerformanceEvaluation"]] = relationship(
        "PerformanceEvaluation", back_populates="cycle", lazy="selectin"
    )


# ---------------------------------------------------------------------------
# 考核指标 — PerformanceIndicator
# ---------------------------------------------------------------------------

class PerformanceIndicator(Base):
    """考核指标，从属于某个 PerformanceTemplate 模板。

    每条指标定义了一个具体的评分维度，例如：
      - "月度生产合格率"（业绩类，权重 40%，满分 100 分）
      - "代码审查通过率"（业绩类，权重 30%，满分 100 分）
      - "团队协作主动性"（态度类，权重 20%，满分 100 分）

    category 分类字段取值：
      - 业绩：可量化的工作产出指标
      - 能力：专业技能、学习成长等能力维度
      - 态度：工作态度、纪律遵守、团队合作
      - 安全：生产安全操作规范（工厂类模板专用）

    weight 字段为百分比数值（如 40.00 表示该指标占模板总分的 40%），
    同一模板下所有指标的 weight 之和应等于 100。

    sort_order 控制指标在前端页面上的展示顺序（升序排列）。
    """

    __tablename__ = "performance_indicators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 外键关联到所属模板，ON DELETE CASCADE 确保模板删除时指标同步清除
    template_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("performance_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="指标名称"
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="指标说明"
    )
    # 指标所属分类，影响报表汇总维度
    category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="分类: 业绩/能力/态度/安全",
    )
    # 该指标在模板中的权重百分比（例如 40.00 代表占总分 40%）
    weight: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, comment="权重百分比"
    )
    # 该指标的满分值，默认 100 分；评分时不得超过此值
    max_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("100.00"), comment="满分"
    )
    # 前端展示排序序号，数值越小越靠前
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="排序序号"
    )

    # 反向关联到所属模板
    template: Mapped["PerformanceTemplate"] = relationship(
        "PerformanceTemplate", back_populates="indicators", lazy="selectin"
    )


# ---------------------------------------------------------------------------
# 绩效评估记录 — PerformanceEvaluation
# ---------------------------------------------------------------------------

class PerformanceEvaluation(Base):
    """绩效评估记录，每位员工在每个考核周期内有且仅有一条记录。

    本表是绩效考核的核心数据表，完整记录了一次考核从发起到最终结果的全过程：

    评分数据存储方式：
      self_scores_json 与 superior_scores_json 均为 JSON 字符串，格式为：
        {"<indicator_id>": <score_float>, ...}
      示例：{"3": 85.0, "4": 92.5, "5": 78.0}

      这种设计避免了为每个指标单独建行，查询效率高，
      且支持模板变更后历史数据的向后兼容。

    加权总分计算（weighted_score）：
      服务层在上级评分完成后自动触发计算：
        weighted_score = avg(self_scores) × self_eval_weight
                       + avg(superior_scores) × superior_eval_weight
      默认权重：自评 30%，上级 70%（从关联的 PerformanceTemplate 读取）

    等级映射规则（强制分布）：
      服务层读取 PerformanceCycle.forced_distribution_json，
      将本周期所有员工按 weighted_score 降序排列后，
      按比例强制划定等级：
        A（前 10%）：绩效系数 1.2，绩效工资乘以 120%
        B（次 30%）：绩效系数 1.0，绩效工资不变
        C（中 50%）：绩效系数 0.8，绩效工资乘以 80%
        D（末 10%）：绩效系数 0.6，绩效工资乘以 60%
      等级存入 grade 字段，系数存入 performance_coefficient 字段。

    驻场员工特殊处理：
      evaluator_id 通常指向员工的直属上级，
      但驻场在客户现场的员工其 evaluator 为项目经理。

    状态流转：
      pending_self → pending_superior → completed
    """

    __tablename__ = "performance_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 被考核员工，ON DELETE CASCADE：员工离职删除时评估记录同步清除
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 所属考核周期
    cycle_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("performance_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 本次考核使用的模板（SET NULL：模板被删除后保留历史评估记录，仅解除关联）
    template_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("performance_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 评估人（直属上级或项目经理），SET NULL：评估人离职后保留历史记录
    evaluator_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        comment="评估人(驻场人员为项目经理)",
    )

    # --- 评分数据 ---
    # 员工自评分数，JSON 格式: {"<indicator_id>": <score>, ...}
    # 员工在 pending_self 阶段填写，每个指标分别打分
    self_scores_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment='自评分数JSON, 如: {"indicator_id": score, ...}'
    )
    # 上级评分，JSON 格式同上，在 pending_superior 阶段由 evaluator 填写
    superior_scores_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment='上级评分JSON, 如: {"indicator_id": score, ...}'
    )
    # 加权总分，由服务层在上级评分完成后自动计算写入
    # 公式: weighted_score = mean(self) × 0.30 + mean(superior) × 0.70
    weighted_score: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2), nullable=True, comment="加权总分"
    )
    # 绩效等级: A / B / C / D，由服务层根据强制分布规则映射
    grade: Mapped[str | None] = mapped_column(
        String(2), nullable=True, comment="等级: A/B/C/D"
    )
    # 绩效系数，由等级决定，直接被薪资模块引用：
    #   A → 1.2, B → 1.0, C → 0.8, D → 0.6
    # 季度考核完成后，payroll_service 读取此字段计算绩效工资实发额
    performance_coefficient: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2), nullable=True, comment="绩效系数(季度考核影响绩效工资)"
    )

    # --- 绩效面谈 ---
    # 考核等级确定后，上级与员工进行一对一面谈，记录沟通要点与改进建议
    interview_record: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="面谈记录"
    )
    interview_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="面谈日期"
    )

    # --- 评估状态 ---
    # pending_self    ：等待员工完成自评
    # pending_superior：自评已提交，等待上级打分
    # completed       ：全流程结束（含面谈），等级与系数已生成
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending_self",
        index=True,
        comment="状态: pending_self/pending_superior/completed",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # 被考核员工（外键为 employee_id，通过 foreign_keys 消除多外键歧义）
    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", foreign_keys=[employee_id], lazy="selectin"
    )
    # 评估人（外键为 evaluator_id）
    evaluator: Mapped["Employee | None"] = relationship(  # noqa: F821
        "Employee", foreign_keys=[evaluator_id], lazy="selectin"
    )
    # 所属考核周期
    cycle: Mapped["PerformanceCycle"] = relationship(
        "PerformanceCycle", back_populates="evaluations", lazy="selectin"
    )
    # 使用的考核模板（可为 None，模板被删除后历史数据仍保留）
    template: Mapped["PerformanceTemplate | None"] = relationship(
        "PerformanceTemplate", lazy="selectin"
    )


# ---------------------------------------------------------------------------
# 绩效目标 — PerformanceGoal（KPI / OKR 树形结构）
# ---------------------------------------------------------------------------

class PerformanceGoal(Base):
    """KPI 或 OKR 目标设定，支持通过 parent_id 自引用构建 OKR 树形结构。

    支持两种目标管理模式：

    1. KPI 模式（goal_type = "kpi"）：
       所有目标 parent_id 均为 NULL，为平铺结构。
       每个目标有 target_value（目标值）和 actual_value（实际值）。
       适合生产、销售等可量化考核场景。

    2. OKR 模式（goal_type = "okr"）：
       使用 parent_id 自引用构建两级树：
         - Objective（O，目标）：parent_id = NULL，描述战略方向
         - Key Result（KR，关键结果）：parent_id 指向所属 Objective

       示例结构：
         O: "提升研发团队交付效率"         (id=10, parent_id=NULL)
           KR1: "Q1 迭代按时交付率 ≥ 90%"  (id=11, parent_id=10)
           KR2: "线上 Bug 数量下降 30%"     (id=12, parent_id=10)

       目前仅支持两层（O-KR），不支持 KR 下再挂子节点。

    关键字段说明：
      weight      ：该目标在所有平级目标中的权重占比（百分比，如 30.0）
      target_value：目标值，允许字符串格式以支持定性描述（如"完成年度审计"）
      actual_value：实际完成值，由员工或上级在评估时填写
      score       ：该目标的完成得分，汇入加权总分计算

    status 流转：
      draft → active → completed / cancelled
    """

    __tablename__ = "performance_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 目标所属员工
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 关联到考核周期（可选，SET NULL：周期删除后目标记录保留供历史查阅）
    cycle_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("performance_cycles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 目标类型：kpi（平铺）或 okr（树形）
    goal_type: Mapped[str] = mapped_column(String(10), nullable=False, default="kpi", comment="目标类型: kpi/okr")
    # 目标标题，KPI 简短描述；OKR 中 Objective 为方向性语句，KR 为可量化描述
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="目标标题")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="目标说明")
    # 该目标在所有同级目标中的权重占比（百分比，如 40.0 表示占总分 40%）
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, comment="权重(百分比)")
    # 目标值：支持字符串，既可存数字（"90"），也可存定性描述（"完成培训体系搭建"）
    target_value: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="目标值(字符串，支持定性)")
    # 实际完成值，由员工在自评阶段填写
    actual_value: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="实际值")
    # 完成得分（0~100），参与绩效加权总分计算
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="完成得分")
    # 状态: draft（草稿）/ active（进行中）/ completed（已完成）/ cancelled（已取消）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", comment="状态: draft/active/completed/cancelled")
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="截止日期")
    # OKR 树形结构的父节点 ID：
    #   - Objective（一级目标）：parent_id = NULL
    #   - Key Result（关键结果）：parent_id = 所属 Objective 的 id
    # SET NULL：父目标被删除时，子目标 parent_id 置为 NULL（变为独立目标）
    parent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("performance_goals.id", ondelete="SET NULL"), nullable=True, comment="OKR父目标ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # 目标所属员工（通过 foreign_keys 显式指定，避免与 parent_id 产生外键歧义）
    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", foreign_keys=[employee_id], lazy="selectin"
    )
    # 子目标列表（OKR 场景下：Objective → 其所有 Key Result）
    # overlaps 参数消除 SQLAlchemy 双向自引用关系的 SAWarning
    sub_goals: Mapped[List["PerformanceGoal"]] = relationship(
        "PerformanceGoal",
        back_populates="parent_goal",
        foreign_keys=[parent_id],
        lazy="selectin",
        overlaps="parent_goal",
    )
    # 父目标（OKR 场景下：Key Result → 所属 Objective）
    # remote_side=[id] 表示 id 列是"远端"，使关系方向正确（多对一）
    parent_goal: Mapped[Optional["PerformanceGoal"]] = relationship(
        "PerformanceGoal",
        back_populates="sub_goals",
        remote_side=[id],
        foreign_keys=[parent_id],
        lazy="selectin",
        overlaps="sub_goals",
    )


# ---------------------------------------------------------------------------
# 360反馈 — PerformanceFeedback360
# ---------------------------------------------------------------------------

class PerformanceFeedback360(Base):
    """360反馈记录。

    用于记录员工之间的多维反馈，当前版本聚焦四个维度：
    工作能力、协作沟通、主动性、职业态度。

    一条记录代表：
      - giver_employee_id  : 反馈提交人
      - target_employee_id : 被反馈人
      - 四项 1~10 分评分
      - 优势亮点 / 改进建议

    平均分 average_score 在写入时由服务层计算，便于后续分析中心直接聚合。
    """

    __tablename__ = "performance_feedback_360"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    giver_employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_ability: Mapped[int] = mapped_column(Integer, nullable=False, comment="工作能力评分(1-10)")
    collaboration: Mapped[int] = mapped_column(Integer, nullable=False, comment="协作沟通评分(1-10)")
    initiative: Mapped[int] = mapped_column(Integer, nullable=False, comment="主动性评分(1-10)")
    attitude: Mapped[int] = mapped_column(Integer, nullable=False, comment="职业态度评分(1-10)")
    strengths: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="优势亮点")
    improvements: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="改进建议")
    average_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.00"), comment="平均评分"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    giver_employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", foreign_keys=[giver_employee_id], lazy="selectin"
    )
    target_employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", foreign_keys=[target_employee_id], lazy="selectin"
    )
