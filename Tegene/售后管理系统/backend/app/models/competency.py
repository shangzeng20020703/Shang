"""
胜任力管理模型 - Competency Management Models
==============================================

本模块定义售后管理系统中胜任力管理相关的4张数据库表：
  - competency_models       胜任力模型（按类型分组）
  - competency_items        胜任力项（具体能力条目，含1-5级行为锚定描述）
  - position_competencies   岗位胜任力要求（岗位能力标准矩阵）
  - employee_competencies   员工胜任力评估（评估记录与差距分析数据源）

完整业务流程
------------
  1. 定义模型: HR配置 CompetencyModel（通用/岗位专属/领导力三类），
               并在其下挂载若干 CompetencyItem，每项包含1-5级行为定义。
  2. 配置岗位要求: 针对每个岗位（position_name），在 PositionCompetency
                   中设置其所需的各项胜任力及要求达到的最低等级（required_level）。
                   这构成该岗位的"能力标准矩阵"。
  3. 员工评估: 由上级或HR对员工逐项打分，写入 EmployeeCompetency.assessed_level。
               可附评估依据（evidence）增强说服力。
  4. 差距分析: 系统对比同岗位的 required_level 与员工的 assessed_level，
               差距（gap = required_level - assessed_level）> 0 即为能力短板。
  5. 培训推荐: 将差距项与 training.TrainingCourseCompetency 表联合查询，
               自动推荐能够弥补该能力差距的培训课程。

模型类型说明（model_type 枚举值）
---------------------------------
  - core          通用胜任力：全员适用，如沟通协作、职业素养、安全意识。
                  公司全体员工均须接受评估，是入职培训和绩效考核的基础维度。
  - role_specific 岗位专属胜任力：特定岗位所需的技术或业务能力，
                  如"机器人调试"仅适用于调试工程师岗，不对行政岗评估。
  - leadership    领导力胜任力：面向 M2 及以上管理层，
                  如"战略思维"、"团队赋能"、"变革管理"。

等级定义（level_definitions 标准参考）
---------------------------------------
  1 = 了解：知道概念，能在他人指导下完成简单任务，尚不能独立应用。
  2 = 初级：具备基础知识，能独立完成常规任务，复杂场景仍需支持。
  3 = 中级：熟练掌握，能独立处理大多数场景，并开始辅导他人。
  4 = 高级：深入理解，能处理复杂/边界情况，在团队中担任专业引领角色。
  5 = 专家：行业领先水平，能制定标准、推动创新，具备跨组织影响力。
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CompetencyModel(Base):
    """
    胜任力模型 — 能力框架的顶层分类容器。

    一个 CompetencyModel 代表一类胜任力框架，例如：
      - "通用胜任力模型"（model_type=core），适用于公司全员。
      - "工程师岗位模型"（model_type=role_specific），适用于技术序列岗位。
      - "管理者领导力模型"（model_type=leadership），适用于 M2+ 管理层。

    每个模型下可挂载多个 CompetencyItem（通过 items 关系访问）。
    删除模型时，其下所有胜任力项会级联删除（cascade="all, delete-orphan"）。

    字段说明：
      name        模型名称，如"公司通用胜任力模型 v2.0"。
      model_type  模型类型，决定适用人群：
                    core          → 全员
                    role_specific → 特定岗位（需配合 position_competencies 使用）
                    leadership    → 管理层
      description 模型背景说明、适用范围、版本历史等补充信息。
    """

    __tablename__ = "competency_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="模型名称，如'通用胜任力模型 v2.0'"
    )
    model_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="模型类型: core(通用胜任力,全员适用) / role_specific(岗位专属) / leadership(领导力,管理层)",
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="模型说明：适用范围、版本历史、设计背景等"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # 该模型下的所有胜任力项；删除模型时级联删除
    items: Mapped[list["CompetencyItem"]] = relationship(
        "CompetencyItem",
        back_populates="model",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class CompetencyItem(Base):
    """
    胜任力项 — 某个模型下的具体能力条目，含1-5级行为锚定描述。

    每个 CompetencyItem 是可独立评估、可关联岗位要求、可与培训课程绑定的最小粒度单元。

    字段说明：
      model_id          所属胜任力模型（外键 competency_models.id，级联删除）。
      name              胜任力项名称，如"工业机器人调试"、"安全风险识别"。
      category          分组标签，便于前端展示和筛选，如：
                          "技术技能"、"安全意识"、"沟通协作"、"问题解决"。
      description       该胜任力的详细定义与行为描述，供评估者参考。
      level_definitions 1-5级分级行为描述，存储为 JSON 字符串，标准格式如下：
                          {
                            "1": "了解：知道相关概念，能在指导下执行基础操作",
                            "2": "初级：能独立完成常规任务，复杂场景需支持",
                            "3": "中级：熟练处理大多数场景，开始辅导他人",
                            "4": "高级：精通并能处理复杂边界情况，担任专业引领",
                            "5": "专家：行业领先，能制定标准并跨组织推动创新"
                          }
                        若为 None，则沿用模型级别的通用五级定义。

    业务关联：
      - PositionCompetency.competency_item_id 引用此表，设定岗位对该项的 required_level。
      - EmployeeCompetency.competency_item_id 引用此表，记录员工的 assessed_level。
      - TrainingCourseCompetency.competency_item_id 引用此表，标注课程能提升该项能力。
    """

    __tablename__ = "competency_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("competency_models.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属胜任力模型ID（级联删除）",
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="胜任力项名称，如'工业机器人调试'、'安全风险识别'"
    )
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="分类标签，如: 技术技能/安全意识/沟通协作/问题解决"
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="能力详细定义与行为描述，供评估人参考"
    )
    level_definitions: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            '1-5级行为锚定描述，存为JSON字符串。标准五级定义：'
            '{"1":"了解：知道概念，能在指导下完成简单任务",'
            ' "2":"初级：能独立完成常规任务，复杂场景需支持",'
            ' "3":"中级：熟练处理大多数场景，开始辅导他人",'
            ' "4":"高级：精通并能处理复杂边界情况，担任专业引领",'
            ' "5":"专家：行业领先，能制定标准并跨组织推动创新"}'
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # 反向关联到所属的胜任力模型
    model: Mapped["CompetencyModel"] = relationship(
        "CompetencyModel", back_populates="items", lazy="selectin"
    )


class PositionCompetency(Base):
    """
    岗位胜任力要求 — 构成岗位能力标准矩阵的核心表。

    本表回答：「某岗位（position_name）对某项胜任力（competency_item_id）
    要求达到几级（required_level）？该项权重是多少？」

    多条记录共同构成该岗位的完整"能力标准矩阵"，是差距分析的基准线。

    典型场景（本公司）：
      岗位"机器人调试工程师"的能力矩阵示例：
        competency_item="工业机器人调试",  required_level=4, weight=30
        competency_item="安全风险识别",    required_level=3, weight=25
        competency_item="故障诊断与排除",  required_level=4, weight=25
        competency_item="技术文档编写",    required_level=2, weight=20
      员工的 assessed_level 与上述 required_level 相减，差值即为能力差距。

    字段说明：
      position_name       岗位名称（字符串，不关联 positions 表）。
                          允许跨部门复用同一岗位名称的能力标准。
      department_id       可选的部门限定。若同一岗位在不同部门有差异化要求，
                          可按部门分别配置；为 None 时视为通用标准。
      competency_item_id  关联的胜任力项（级联删除）。
      required_level      该岗位对此项能力的最低要求等级（1-5），
                          低于此值的员工将被标记为"能力不足"，触发培训推荐。
      weight              该项在综合胜任力评分中的权重（百分比，默认100）。
                          多项权重之和通常为100，用于加权计算岗位匹配度得分。
    """

    __tablename__ = "position_competencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True, comment="岗位名称，如'机器人调试工程师'"
    )
    department_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="所属部门（可选）：为 None 时标准适用所有部门，有值时为部门级定制标准",
    )
    competency_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("competency_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="关联的胜任力项ID（级联删除，项被删除时对应岗位要求一并清除）",
    )
    required_level: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment=(
            "该岗位对此项能力的最低要求等级（1-5）。"
            "员工 assessed_level < required_level 时产生差距，触发培训推荐。"
        ),
    )
    weight: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        comment="权重（百分比，默认100）。多项权重之和=100时可计算加权岗位匹配度得分",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # 关联的胜任力项详情（含名称、分类、各级定义）
    competency_item: Mapped["CompetencyItem"] = relationship(
        "CompetencyItem", lazy="selectin"
    )


class EmployeeCompetency(Base):
    """
    员工胜任力评估记录 — 差距分析的原始数据来源。

    本表存储某员工在某次评估中对某项胜任力的评级结果（assessed_level）。
    结合 PositionCompetency.required_level 即可计算出能力差距：
      gap = required_level - assessed_level
      gap > 0 → 能力不足，需培训
      gap = 0 → 达标
      gap < 0 → 超出要求，可作为内部讲师或晋升候选人信号

    评估方式（本公司实践）：
      - 直线上级评估（最常见，assessor_id = 直属上级员工ID）
      - HR 统一评估（新员工试用期结束）
      - 自我评估（辅助参考，assessor_id = employee_id 本人）
      - 360度评估（可录入多条记录后取均值）

    字段说明：
      employee_id         被评估员工ID（级联删除）。
      competency_item_id  被评估的胜任力项（级联删除）。
      assessed_level      评估结论等级（1-5），对照该项的 level_definitions 打分。
      assessor_id         评估人员工ID（可为 None，表示系统自动或匿名评估）。
                          注意：与 employee_id 可以相同（自评场景）。
      evidence            评估依据说明，如具体工作事件、项目成果等，
                          增强评估结论的可信度与可追溯性。
      assessed_at         本次评估的时间戳。同一员工同一项目可有多条历史记录，
                          最新一条视为当前有效评估结果。

    差距分析查询示例：
      SELECT ci.name, pc.required_level, ec.assessed_level,
             (pc.required_level - ec.assessed_level) AS gap
      FROM employee_competencies ec
      JOIN competency_items ci ON ec.competency_item_id = ci.id
      JOIN position_competencies pc
           ON pc.competency_item_id = ec.competency_item_id
           AND pc.position_name = :employee_position
      WHERE ec.employee_id = :employee_id
      HAVING gap > 0
      ORDER BY gap DESC;
    """

    __tablename__ = "employee_competencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="被评估员工ID（级联删除）",
    )
    competency_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("competency_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="被评估的胜任力项ID（级联删除）",
    )
    assessed_level: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment=(
            "评估结论等级（1-5）。"
            "与 PositionCompetency.required_level 相减得差距值："
            "gap > 0 需培训，gap = 0 达标，gap < 0 可作晋升信号"
        ),
    )
    assessor_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "评估人员工ID（可为 None）。"
            "与 employee_id 相同时表示自评；为 None 时表示系统自动或匿名评估"
        ),
    )
    evidence: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="评估依据/佐证说明，如工作事件描述、项目成果、客观数据等，增强可信度",
    )
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="评估时间。同一员工同一项目可存多条历史记录，最新记录为当前有效评估",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # 被评估的胜任力项详情（含名称、等级定义）
    competency_item: Mapped["CompetencyItem"] = relationship(
        "CompetencyItem", lazy="selectin"
    )
    # 被评估员工（通过 employee_id 关联）
    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", foreign_keys=[employee_id], lazy="selectin"
    )
    # 评估人员工（通过 assessor_id 关联，可为 None）
    assessor: Mapped["Employee | None"] = relationship(  # noqa: F821
        "Employee", foreign_keys=[assessor_id], lazy="selectin"
    )
