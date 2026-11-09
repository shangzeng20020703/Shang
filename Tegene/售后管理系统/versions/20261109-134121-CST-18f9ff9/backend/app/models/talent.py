"""
人才发展模型 - Talent Development Models
==========================================

本模块定义售后管理系统中人才发展相关的4张数据库表：
  - career_paths            职业通道（技术/管理/双通道及各级别定义）
  - idp_plans               个人发展计划（IDP，员工职业发展规划的顶层容器）
  - idp_goals               IDP目标（发展计划中的具体可执行目标项）
  - employee_certifications 员工证书（资质证书全生命周期管理与到期提醒）

完整业务流程（职业发展闭环）
------------------------------
  1. 职业通道定义: HR 配置 CareerPath，定义公司的职级体系：
                    技术通道 P1-P7，管理通道 M1-M5，或双通道并行。
                    每个通道的各级别包含职级标题、薪资范围参考、能力要求。
  2. IDP 计划创建: 员工与直线上级在绩效面谈或年度发展谈话中制定 IDPPlan，
                    明确当前职级（current_level）、目标职级（target_level）
                    及预计达成时间（target_date）。
                    一名员工可有多份历史 IDP，status=active 的为当前生效计划。
  3. 目标设定与追踪: 在 IDP 框架下拆解具体可操作目标（IDPGoal），
                     每个目标有类型（技能/胜任力/培训/证书/项目）、
                     截止日期和进度百分比。HR/上级定期 review 进度。
  4. 证书管理: 员工考取专业证书、安全资质后录入 EmployeeCertification，
               系统在 expiry_date - reminder_days 日自动推送提醒，
               确保安全资质到期前完成续证，避免影响上岗资格。

职业通道设计原则（本公司适配）
------------------------------------
  技术通道 (technical, P1-P7)：
    P1  初级工程师    — 应届/1-2年，执行具体任务
    P2  工程师        — 3-5年，独立承担模块工作
    P3  高级工程师    — 5-8年，主导技术方案设计
    P4  专家工程师    — 8-12年，跨团队技术引领
    P5  资深专家      — 12+年，公司级技术决策
    P6  首席专家      — 行业影响力，战略技术方向
    P7  技术院士      — 最高级，行业/学术权威（本公司最高技术荣誉）

  管理通道 (management, M1-M5)：
    M1  基层管理者    — 带3-8人的小组/班组长
    M2  中层管理者    — 部门经理，带团队承担业务线
    M3  高级管理者    — 多部门总监，战略执行
    M4  副总裁级      — 业务板块负责人
    M5  C-Level       — 公司核心决策层

  双通道 (dual)：
    允许技术专家与管理岗并行发展，不强制走管理路线，
    保留高级技术人才的专业发展空间。
"""

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CareerPath(Base):
    """
    职业通道 — 定义员工职业发展的路径框架与各级别标准。

    每条记录代表一条职业通道，如"技术通道"或"管理通道"。
    通道内的各级别定义存储在 levels_json 字段中，
    供 IDP 计划使用（员工选择 current_level 和 target_level）。

    字段说明：
      name        通道名称，如"技术通道（P序列）"、"管理通道（M序列）"。
      path_type   通道类型枚举值：
                    technical   → 技术通道（P1-P7），面向工程师群体。
                                  本公司技术序列覆盖机器人、软件、电气、机械等方向。
                    management  → 管理通道（M1-M5），面向具有管理意愿的员工。
                                  进入 M2+ 需通过管理能力评估。
                    dual        → 双通道，允许技术专家（P4+）同时具备有限管理职责，
                                  不强制切换到管理序列，保留专业发展空间。
      description 通道的业务背景说明、适用范围、晋升原则等。
      levels_json 各级别的结构化定义，JSON 数组字符串，标准格式如下：
                    [
                      {
                        "level": "P1",
                        "title": "初级工程师",
                        "min_years": 0,
                        "max_years": 2,
                        "description": "执行具体任务，需持续学习"
                      },
                      {
                        "level": "P2",
                        "title": "工程师",
                        "min_years": 3,
                        "max_years": 5,
                        "description": "独立承担模块工作，能指导P1"
                      }
                    ]
                  为 None 时表示通道未配置等级标准（仍在规划中）。
    """

    __tablename__ = "career_paths"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="通道名称，如'技术通道（P序列）'、'管理通道（M序列）'"
    )
    path_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment=(
            "通道类型: "
            "technical(技术通道 P1-P7，面向工程师) / "
            "management(管理通道 M1-M5，面向管理者) / "
            "dual(双通道，P4+技术专家可兼具管理职责)"
        ),
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="通道说明：业务背景、适用范围、晋升原则等"
    )
    levels_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            '各级别结构化定义，JSON 数组字符串。标准格式示例：'
            '[{"level":"P1","title":"初级工程师","min_years":0,"max_years":2,"description":"..."},'
            ' {"level":"P2","title":"工程师","min_years":3,"max_years":5,"description":"..."}]'
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IDPPlan(Base):
    """
    个人发展计划（IDP） — 员工职业发展规划的顶层容器。

    IDP（Individual Development Plan）是员工与上级共同制定的
    职业发展路线图，明确从当前职级晋升至目标职级的方向与时间节点。

    多 IDP 说明：
      一名员工在职业生涯中可拥有多份 IDPPlan（历史记录保留）。
      status=active 的记录为当前生效计划，通常同时只有一份。
      历史计划 status=completed 或 cancelled，供发展轨迹回溯。
      服务层查询当前计划时应过滤 status='active'。

    状态机（status 流转）：
      draft     → active     员工与上级确认计划，正式启动
      active    → completed  目标达成（current_level 升至 target_level）
      active    → cancelled  因组织变动、员工离职等原因中止
      draft     → cancelled  计划草稿放弃

    字段说明：
      employee_id    归属员工（级联删除）。
      career_path_id 关联的职业通道（可为 None，表示尚未确定发展方向）。
                     通道变更时更新此字段，不新建 IDP。
      current_level  员工当前职级（字符串，如 "P2"、"M1"）。
                     与 career_paths.levels_json 中的 level 值对应。
      target_level   本期 IDP 的目标职级（如 "P3"）。
                     跨两级的晋升目标建议拆分为两期 IDP 管理。
      target_date    预计达成目标职级的日期（Date，精确到天）。
                     HR 可按此日期设置定期 review 提醒。
      status         IDP 状态（见上方状态机说明）。
      goals          本期 IDP 下的所有发展目标（IDPGoal 关系，级联删除）。
    """

    __tablename__ = "idp_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="归属员工ID（级联删除）。同一员工可有多份 IDP，status=active 为当前生效计划",
    )
    career_path_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("career_paths.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联职业通道ID（可为 None 表示方向未定；删除通道时置 NULL，不影响 IDP 历史记录）",
    )
    current_level: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="员工当前职级（如 'P2'、'M1'），与 career_paths.levels_json 中的 level 值对应",
    )
    target_level: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="本期 IDP 目标职级（如 'P3'）。跨两级目标建议拆分为两期 IDP 管理",
    )
    target_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="预计达成目标职级的日期（精确到天）。HR 可按此日期配置定期 review 提醒",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
        index=True,
        comment=(
            "IDP状态机: "
            "draft(草稿,待确认) → active(生效中,当前发展计划) → "
            "completed(目标已达成) / cancelled(已中止)"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # 归属员工详情
    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", lazy="selectin"
    )
    # 关联的职业通道（含等级定义）
    career_path: Mapped["CareerPath | None"] = relationship(
        "CareerPath", lazy="selectin"
    )
    # 本期 IDP 下的所有发展目标（级联删除）
    goals: Mapped[list["IDPGoal"]] = relationship(
        "IDPGoal",
        back_populates="idp_plan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class IDPGoal(Base):
    """
    IDP 目标 — 个人发展计划中的具体可执行目标项。

    每个 IDPGoal 是 IDPPlan 的组成部分，代表员工为达成目标职级
    需要完成的一项具体任务。通过 linked_course_id 和 linked_competency_id
    实现与培训模块、胜任力模块的三向联动。

    目标类型说明（goal_type 枚举值）：
      skill         技能目标：掌握某项具体技术技能（如"学会 ROS2 导航框架"）。
                    进度由员工自评或实操考核结果更新。
      competency    胜任力目标：将某项胜任力评级提升至指定等级。
                    关联 linked_competency_id，完成时由服务层更新 EmployeeCompetency。
      training      培训目标：完成特定培训课程。
                    关联 linked_course_id，员工 TrainingEnrollment 状态变为
                    completed 时自动将本目标进度更新至100%。
      certification 证书目标：考取特定资质证书（如"考取电工操作证"）。
                    完成时对应创建 EmployeeCertification 记录。
      project       项目实践目标：通过参与具体项目积累经验（如"主导一个机械臂集成项目"）。
                    进度由上级确认并手动更新。

    字段说明：
      idp_id              所属 IDP 计划（级联删除）。
      goal_type           目标类型（见上方说明）。
      description         目标描述，包含具体的可衡量完成标准（SMART 原则）。
      progress_pct        完成进度（0-100）。训练/证书类目标可系统自动更新；
                          其他类型由员工或上级手动更新。
      linked_course_id    关联培训课程（goal_type=training 时填写，可为 None）。
                          员工报名并完成该课程时触发进度自动更新。
      linked_competency_id 关联胜任力项（goal_type=competency 时填写，可为 None）。
                           胜任力评估更新时可联动更新本目标进度。
      status              目标状态：
                            pending     → 尚未开始（计划中）
                            in_progress → 正在推进（progress_pct > 0 且 < 100）
                            completed   → 已完成（progress_pct = 100）
      due_date            目标截止日期（为 None 表示无硬性截止，跟随 IDP target_date）。
    """

    __tablename__ = "idp_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idp_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("idp_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属 IDP 计划ID（级联删除）",
    )
    goal_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment=(
            "目标类型: "
            "skill(技能学习) / "
            "competency(胜任力提升,关联 linked_competency_id) / "
            "training(培训完成,关联 linked_course_id) / "
            "certification(证书考取) / "
            "project(项目实践)"
        ),
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="目标描述，建议包含具体可衡量的完成标准（SMART 原则）"
    )
    progress_pct: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="完成进度（0-100）。训练/证书类目标可系统自动更新；其他类型由员工或上级手动更新",
    )
    linked_course_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("training_courses.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联培训课程ID（goal_type=training 时填写）。员工完成该课程后自动触发进度更新",
    )
    linked_competency_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("competency_items.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联胜任力项ID（goal_type=competency 时填写）。胜任力评估更新时可联动更新本目标进度",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        comment=(
            "目标状态: "
            "pending(计划中,尚未开始) → "
            "in_progress(进行中,0<progress<100) → "
            "completed(已完成,progress=100)"
        ),
    )
    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="目标截止日期（精确到天）。为 None 表示无硬性截止，跟随 IDP.target_date",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # 所属 IDP 计划（反向关联）
    idp_plan: Mapped["IDPPlan"] = relationship(
        "IDPPlan", back_populates="goals", lazy="selectin"
    )


class EmployeeCertification(Base):
    """
    员工证书 — 资质证书全生命周期管理与到期预警。

    本表统一管理员工持有的所有资质证书，包括：
      - 安全资质：电工操作证、高空作业证、叉车证、特种设备操作证等
        （到期未续证影响上岗资格，属于安全生产合规要求）
      - 专业技能：焊接技师证、工业机器人操作员证、质量检测员证等
      - 管理认证：PMP、HRCP、六西格玛绿带/黑带等
      - 行业资质：ISO 内审员、功能安全工程师（FS Engineer）等

    到期预警机制（reminder_days 的业务含义）：
      系统定时任务（每日运行）查询满足以下条件的证书记录：
        expiry_date IS NOT NULL
        AND status = 'valid'
        AND expiry_date <= CURRENT_DATE + reminder_days
      并向员工本人和 HR 发送提醒通知。

      reminder_days 默认值 30（提前30天提醒），安全资质建议设为60甚至90天，
      为续证手续（培训→考试→报批）预留足够时间。

      安全资质到期若未及时处理：
        1. HR 将员工状态临时标记为"不可上岗"
        2. 生产主管需调整班组排班
        3. 安全管理部门依据内控流程追责

    字段说明：
      employee_id        持证员工（级联删除）。
      cert_name          证书名称，如"叉车操作证"、"PMP项目管理专业人士认证"。
      cert_type          证书类型分类：
                           安全资质  → is_mandatory 性质，到期影响上岗
                           专业技能  → 提升岗位竞争力
                           管理认证  → 管理序列晋升参考依据
                           行业资质  → 特定业务领域的准入要求
      issuing_authority  颁发机构，如"国家应急管理部"、"PMI"、"TÜV"。
      cert_number        证书编号（唯一标识，用于查询真伪及存档）。
      issue_date         颁发日期（获证时间）。
      expiry_date        到期日期（为 None 表示永久有效，如学历证书）。
      reminder_days      到期前提前提醒天数（默认30天，安全资质建议60-90天）。
      status             证书当前状态：
                           valid    → 在有效期内，可正常使用
                           expired  → 已过期（expiry_date < today），需续证
                           revoked  → 已被颁发机构撤销（违规等原因，不可续证）
    """

    __tablename__ = "employee_certifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="持证员工ID（级联删除）",
    )
    cert_name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="证书名称，如'叉车操作证'、'PMP项目管理专业人士认证'"
    )
    cert_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "证书类型: "
            "安全资质(到期影响上岗，属安全合规硬要求) / "
            "专业技能(提升岗位竞争力) / "
            "管理认证(管理晋升参考) / "
            "行业资质(特定业务准入要求)"
        ),
    )
    issuing_authority: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="颁发机构，如'国家应急管理部'、'PMI'、'TÜV'"
    )
    cert_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="证书编号（唯一标识，用于查询真伪及正式存档）"
    )
    issue_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="颁发日期（获证时间，精确到天）"
    )
    expiry_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="到期日期（精确到天）。为 None 表示永久有效（如学历证书）；到期后 status 应更新为 expired",
    )
    reminder_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=30,
        comment=(
            "到期前提前提醒天数（默认30天）。"
            "系统每日检查 expiry_date <= today + reminder_days 的有效证书并推送通知。"
            "安全资质建议设为60-90天，为培训→考试→报批流程预留足够时间"
        ),
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="valid",
        comment=(
            "证书状态: "
            "valid(有效,在有效期内) / "
            "expired(已过期,expiry_date < today，需续证) / "
            "revoked(已撤销,被颁发机构吊销，不可续证)"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # 持证员工详情
    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", lazy="selectin"
    )
