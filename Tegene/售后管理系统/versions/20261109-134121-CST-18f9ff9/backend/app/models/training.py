"""
培训管理模型 - Training Management Models
==========================================

本模块定义售后管理系统中培训全流程涉及的8张数据库表：
  - training_courses            培训课程（课程库，含必修标记与目标岗位）
  - training_plans              培训计划（年度/季度/入职批次）
  - training_plan_courses       计划排课（将课程编排进具体计划，设定讲师/时间/地点）
  - training_enrollments        报名与参训记录（含进度、得分与满意度反馈）
  - training_course_competencies 课程-胜任力关联（打通培训与能力提升的关键桥梁）
  - training_exams              课程考试（及格线、时限配置）
  - exam_questions              考试题目（单选/多选/判断，含选项与正确答案）
  - exam_attempts               考试作答记录（提交后自动出分，通过后生成合格证）

完整业务流程
------------
  1. 课程定义: HR 在 TrainingCourse 中创建课程，配置分类、授课方式、课时、
               是否必修及目标岗位，并通过 TrainingCourseCompetency 标注
               本课程能提升哪些胜任力项（以及预计提升几个等级）。
  2. 培训计划: 每年/每季度制定 TrainingPlan，状态从 draft → active 后生效，
               completed/cancelled 归档。
  3. 排课: 将课程加入计划，生成 TrainingPlanCourse 记录，指定讲师、日期、地点
           及最大参训人数，完成"课表"编排。
  4. 报名: 员工或 HR 创建 TrainingEnrollment（关联具体计划课程或直接关联课程），
           初始状态为 enrolled。
  5. 参训: 课程开始后更新状态为 in_progress，系统或讲师实时更新 progress_pct。
           缺席则置为 absent，中途退出置为 cancelled。
  6. 考试: 完成学习后参加 TrainingExam，系统创建 ExamAttempt 记录作答，
           提交（submitted_at 有值）后自动计分，score >= pass_score 则 passed=True。
  7. 发证: 通过考试后 certificate_no 自动生成，写入 ExamAttempt。
           该证书编号可同步写入 talent.EmployeeCertification 进行统一证书管理。
  8. 胜任力提升: 通过的课程触发服务层更新 competency.EmployeeCompetency，
                  将对应胜任力项的 assessed_level 按 target_level_delta 提升，
                  完成"培训→胜任力提升"的闭环。

必修课程说明（is_mandatory 业务含义）
--------------------------------------
  工厂岗位安全培训（如电气安全、高空作业、叉车操作等）设置 is_mandatory=True，
  代表该课程属于强制要求：
    - 新员工入职前必须完成，否则不得上岗操作（影响入职流程审批）。
    - 证书到期后需重修，逾期可依据安全管理制度追责（HR 可导出未完成名单）。
    - 年度必修课若员工 enrolled 记录缺失，绩效模块可将"培训完成率"扣分。

课程与胜任力的关联（TrainingCourseCompetency 的关键作用）
-----------------------------------------------------------
  这是打通"培训推荐"与"差距分析"的核心桥梁：
    competency 模块发现员工在"工业机器人调试"项差距 = 2 级
    → 查询 training_course_competencies 找到关联该项且 target_level_delta >= 1 的课程
    → 按 delta 排序推荐最优补差课程
    → 员工完成课程+通过考试 → assessed_level 自动更新 → 差距缩小
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
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


class QuestionType(str, enum.Enum):
    """
    考试题型枚举。

    single    单选题：选项 {"A":..., "B":..., "C":..., "D":...}，correct_answer = "A"。
    multiple  多选题：correct_answer = "A,C"（逗号分隔，顺序无关）。
    truefalse 判断题：选项固定为 {"A":"正确", "B":"错误"}，correct_answer = "A" 或 "B"。
    """
    single = "single"       # 单选
    multiple = "multiple"   # 多选
    truefalse = "truefalse" # 判断


class TrainingCourse(Base):
    """
    培训课程 — 课程库的基础单元，可被多个培训计划复用。

    课程本身不含排期信息（排期在 TrainingPlanCourse 中设定），
    设计上遵循"课程复用"原则：同一课程可在不同计划中多次排课。

    字段说明：
      name              课程名称，如"工业机器人安全操作规程"、"新员工入职引导"。
      category          课程分类，决定培训体系归属：
                          入职  → 所有新员工须在试用期内完成
                          岗位  → 特定岗位的专业技能课程
                          安全  → 与安全资质挂钩，is_mandatory 通常为 True
                          管理  → 面向 M2+ 管理层的领导力课程
                          技术  → 工程技术系列课程（如 ROS/PLC/焊接工艺等）
      delivery_method   授课方式：
                          online  → 线上自学（e-learning 系统或录播视频）
                          offline → 线下面授（需安排讲师与场地）
                          blended → 混合式（线上预习 + 线下实操）
      duration_hours    标准课时（小时，精度0.1）。用于统计年度人均培训学时。
      is_mandatory      是否必修。工厂安全培训（电气/高空/叉车/消防等）须设为 True。
                        必修课程缺席或逾期未完成，HR 可依规追责，并通知直属上级。
      target_positions  定向推送目标岗位，JSON 字符串格式，如：
                          '["电气调试工程师", "机械安装工程师"]'
                        为 None 时表示全员通用；服务层据此过滤推送对象。
      is_active         是否启用。下架（False）的课程不再对员工展示，
                        但历史报名记录保留。
      competency_links  该课程能提升的胜任力项列表（TrainingCourseCompetency 关系），
                        是智能培训推荐的核心依据。
    """

    __tablename__ = "training_courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="课程名称，如'工业机器人安全操作规程'"
    )
    category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="课程分类: 入职/岗位/安全/管理/技术",
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="课程说明：内容摘要、学习目标、适用对象等"
    )
    delivery_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="offline",
        comment="授课方式: online(线上自学) / offline(线下面授) / blended(混合式)",
    )
    duration_hours: Mapped[Decimal] = mapped_column(
        Numeric(5, 1), nullable=False, default=Decimal("1.0"), comment="标准课时（小时，精度0.1），用于统计人均培训学时"
    )
    is_mandatory: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment=(
            "是否必修。工厂安全培训（电气/高空/叉车/消防等）须设为 True。"
            "必修课缺席或逾期未完成，HR 可依安全管理制度追责并通知直属上级"
        ),
    )
    target_positions: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment='定向推送目标岗位，JSON 字符串，如: ["电气调试工程师","机械安装工程师"]。为 None 表示全员通用',
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否启用。下架（False）的课程不再展示给员工，但历史报名记录保留",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # 该课程关联的胜任力提升项（智能培训推荐的核心数据）
    competency_links: Mapped[list["TrainingCourseCompetency"]] = relationship(
        "TrainingCourseCompetency",
        back_populates="course",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TrainingPlan(Base):
    """
    培训计划 — 以时间维度（年度/季度/入职批次）组织培训活动的顶层容器。

    一个计划可包含多门课程（通过 TrainingPlanCourse 排课），
    并设有独立的预算管控。

    状态流转（status 状态机）：
      draft     → active     HR 审批通过后启动计划，开放员工报名
      active    → completed  计划周期结束，所有课程归档
      active    → cancelled  因故取消（预算削减、重组等），已报名员工需另行安排
      draft     → cancelled  计划未启动即取消

    字段说明：
      name        计划名称，如"2026年度安全培训计划"、"Q1技术提升培训"。
      plan_type   计划类型：
                    annual      → 年度计划（覆盖全年，quarter=None）
                    quarterly   → 季度计划（quarter=1/2/3/4）
                    onboarding  → 入职批次计划（按新员工入职时间批次创建）
      year        年度（如 2026）。
      quarter     季度（1-4，仅 quarterly 类型有效，annual/onboarding 为 None）。
      budget      计划预算（元，Decimal 精度）。服务层可汇总实际培训支出与此对比。
      status      计划状态（见上方状态机说明）。
      plan_courses 该计划下的所有排课记录（TrainingPlanCourse 关系）。
    """

    __tablename__ = "training_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="计划名称，如'2026年度安全培训计划'"
    )
    plan_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="计划类型: annual(年度) / quarterly(季度) / onboarding(入职批次)",
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="年度，如2026")
    quarter: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="季度（1-4），仅 quarterly 类型有效；年度计划为 None"
    )
    budget: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="预算（元）。服务层可汇总实际支出与此对比进行预算管控"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
        index=True,
        comment=(
            "计划状态: "
            "draft(草稿,待审批) → active(进行中,开放报名) → "
            "completed(已完成,归档) / cancelled(已取消)"
        ),
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="计划说明：培训目标、覆盖范围、考核方式等"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # 该计划下的所有排课记录（级联删除）
    plan_courses: Mapped[list["TrainingPlanCourse"]] = relationship(
        "TrainingPlanCourse",
        back_populates="plan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TrainingPlanCourse(Base):
    """
    培训计划排课 — 将具体课程编排进培训计划，形成可操作的"课表"。

    一个培训计划（TrainingPlan）可包含多个排课记录，每条记录对应：
    「在某个计划中，某门课程的具体开课信息（讲师/时间/地点/容量）」。

    同一课程可在不同计划中多次排课（课程复用）。
    员工报名时，TrainingEnrollment.plan_course_id 关联到本表，
    实现报名与具体排课场次的精确绑定。

    字段说明：
      plan_id           所属培训计划（级联删除）。
      course_id         排期的培训课程（级联删除）。
      scheduled_date    计划开课日期（Date，不含时间，精确到天）。
      trainer_name      讲师姓名（字符串，兼容外聘讲师）。
      trainer_id        内部讲师员工ID（可为 None，外聘时只填 trainer_name）。
      location          培训地点，如"公司工厂一楼培训室"、"线上Zoom会议"。
      max_participants  最大参训人数（超额时报名需等待或另开班次，None=不限）。
    """

    __tablename__ = "training_plan_courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("training_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属培训计划ID（级联删除）",
    )
    course_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("training_courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="排期的培训课程ID（同一课程可在不同计划中多次排课）",
    )
    scheduled_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="计划开课日期（精确到天，为 None 表示待定）"
    )
    trainer_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="讲师姓名（字符串，兼容外聘讲师；内部讲师同时填 trainer_id）"
    )
    trainer_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        comment="内部讲师的员工ID（可为 None，外聘讲师时仅填 trainer_name）",
    )
    location: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="培训地点，如'公司工厂一楼培训室'、'线上Zoom会议'"
    )
    max_participants: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="最大参训人数（为 None 表示不限；超额时报名服务层应提示或候补）"
    )

    # 所属培训计划
    plan: Mapped["TrainingPlan"] = relationship(
        "TrainingPlan", back_populates="plan_courses", lazy="selectin"
    )
    # 本次排课对应的课程详情
    course: Mapped["TrainingCourse"] = relationship(
        "TrainingCourse", lazy="selectin"
    )


class TrainingEnrollment(Base):
    """
    培训报名与参训记录 — 员工与课程之间的多对多关系表，同时记录参训全过程状态。

    每条记录代表「某员工参加某门课程（某次排课）的完整生命周期」。

    状态机（status 流转）：
      enrolled    → in_progress   课程开始，员工正式参训（progress_pct > 0）
      in_progress → completed     完成全部学习内容（progress_pct = 100，completed_at 有值）
      in_progress → absent        课程结束时员工未参加（出勤核查后标记）
      enrolled    → cancelled     课程前主动退出或被取消资格
      in_progress → cancelled     中途退出

    注意：must-attend 必修课（is_mandatory=True）的 absent 记录会触发 HR 警告通知。

    字段说明：
      employee_id     报名员工ID（级联删除）。
      course_id       报名课程ID（冗余存储，便于不通过 plan_course 直接查询）。
      plan_course_id  关联的具体排课场次（可为 None，表示计划外自学报名）。
      status          参训状态（见上方状态机说明）。
      progress_pct    完成进度（0-100）。线上课程由 e-learning 系统实时更新；
                      线下课程在完成时统一置为 100。
      score           课程考核得分（Decimal，精度5.2；无考试时为 None）。
      feedback_score  员工对课程的满意度评分（1-5星）。1=极差，5=优秀。
      feedback_comment 员工文字反馈，可用于课程质量改进。
      completed_at    完成时间（status 变为 completed 时写入）。
    """

    __tablename__ = "training_enrollments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="报名员工ID（级联删除）",
    )
    course_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("training_courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="报名课程ID（冗余字段，便于不通过排课记录直接查询员工课程完成情况）",
    )
    plan_course_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("training_plan_courses.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联的具体排课场次ID（为 None 表示计划外自学报名，不绑定具体排期）",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="enrolled",
        index=True,
        comment=(
            "参训状态机: "
            "enrolled(已报名) → in_progress(参训中) → completed(已完成) / "
            "absent(缺席,必修课缺席触发HR告警) / cancelled(已取消)"
        ),
    )
    progress_pct: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="完成进度（0-100）。线上课程实时更新；线下课程完成时统一置为100",
    )
    score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True, comment="课程考核得分（精度5.2）；无考试环节时为 None"
    )
    feedback_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="员工满意度评分（1-5星）：1=极差，3=一般，5=优秀"
    )
    feedback_comment: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="员工文字反馈，用于课程质量持续改进"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="完成时间（status 变为 completed 时写入）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # 参训员工详情
    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", lazy="selectin"
    )
    # 所报名课程详情
    course: Mapped["TrainingCourse"] = relationship(
        "TrainingCourse", lazy="selectin"
    )


class TrainingCourseCompetency(Base):
    """
    课程-胜任力关联 — 打通"培训模块"与"胜任力模块"的关键桥梁表。

    本表回答：「完成某门课程，能将哪项胜任力提升几个等级？」

    业务价值：
      1. 智能培训推荐：查找能弥补员工能力差距的最优课程。
         差距分析发现员工在"故障诊断"差2级
         → 查此表找到 target_level_delta >= 1 的相关课程
         → 按 delta 从大到小排序推荐
      2. 培训效果量化：课程完成后，服务层可据此
         自动更新 employee_competencies.assessed_level（+= target_level_delta）。
      3. 课程价值评估：HR 可统计各课程对胜任力提升的贡献度。

    字段说明：
      course_id            关联的培训课程（级联删除）。
      competency_item_id   该课程能提升的胜任力项（级联删除）。
      target_level_delta   完成本课程后预计提升的等级数（通常为 +1，
                           高强度专项培训可为 +2）。
                           注意：这是预期值，实际提升以 ExamAttempt 通过为准。

    一门课程可关联多个胜任力项（多行记录），表达"综合培训课程"的价值。
    """

    __tablename__ = "training_course_competencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("training_courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="关联的培训课程ID（级联删除）",
    )
    competency_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("competency_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="该课程能提升的胜任力项ID（级联删除）",
    )
    target_level_delta: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=(
            "完成本课程后预计提升的等级数（通常为+1，高强度专项培训可为+2）。"
            "课程通过后服务层以此值更新员工的 assessed_level"
        ),
    )

    # 关联的培训课程（反向引用 competency_links）
    course: Mapped["TrainingCourse"] = relationship(
        "TrainingCourse", back_populates="competency_links", lazy="selectin"
    )
    # 被提升的胜任力项详情
    competency_item: Mapped["CompetencyItem"] = relationship(  # noqa: F821
        "CompetencyItem", lazy="selectin"
    )


class TrainingExam(Base):
    """
    培训考试 — 与课程绑定的考核配置，支持及格线与时限设定。

    一门课程（TrainingCourse）可配置一个或多个考试（如"初次考试"和"补考"），
    但通常每门课程对应一个有效考试（is_active=True）。

    字段说明：
      course_id    所属培训课程（级联删除）。
      title        考试标题，如"工业机器人安全操作考试"。
      pass_score   及格分（0-100，默认60）。ExamAttempt.score >= pass_score 时 passed=True。
      time_limit   考试时限（分钟，默认 None=不限时）。
                   线上考试建议设置时限防止作弊；线下考试由监考人工控制。
      is_active    是否启用。旧版考试下线后设为 False，保留历史作答记录。
      questions    该考试下的所有题目（ExamQuestion 关系，级联删除）。
    """

    __tablename__ = "training_exams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("training_courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属培训课程ID（级联删除）",
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="考试标题，如'工业机器人安全操作考试'")
    pass_score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
        comment="及格分（0-100，默认60）。ExamAttempt.score >= pass_score 则标记为通过",
    )
    time_limit: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="考试时限（分钟，None=不限时）。线上考试建议设置以防止长时间作弊",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否启用（False=已下线，仅保留历史记录，不允许新的作答）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # 关联的课程信息
    course: Mapped["TrainingCourse"] = relationship("TrainingCourse", lazy="selectin")
    # 该考试下的所有题目（级联删除）
    questions: Mapped[List["ExamQuestion"]] = relationship(
        "ExamQuestion", back_populates="exam", cascade="all, delete-orphan", lazy="selectin"
    )


class ExamQuestion(Base):
    """
    考试题目 — 支持单选、多选、判断三种题型，含选项定义与标准答案。

    字段说明：
      exam_id        所属考试（级联删除）。
      question_text  题目正文，支持富文本描述（如含操作步骤的安全题目）。
      question_type  题型（参考 QuestionType 枚举）：
                       single    → 单选题
                       multiple  → 多选题（答案为逗号分隔多个选项键）
                       truefalse → 判断题（选项固定为 A=正确 / B=错误）
      options        选项字典（JSON），标准格式：
                       单选/多选: {"A": "选项文字", "B": "...", "C": "...", "D": "..."}
                       判断题:    {"A": "正确", "B": "错误"}
                     建议至少4个选项（A-D），提高区分度。
      correct_answer 正确答案字符串：
                       单选/判断: "A"（单个选项键）
                       多选:      "A,C"（逗号分隔，字母顺序排列）
                     服务层评分时需处理顺序无关的多选比较。
      score          本题分值（默认10分）。所有题目分值之和建议等于100，
                     否则 pass_score 设置需相应调整。
      order          题目显示顺序（升序，0起始）。便于前端按序渲染题目列表。
    """

    __tablename__ = "exam_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("training_exams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属考试ID（级联删除）",
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False, comment="题目正文内容")
    question_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="single",
        comment="题型: single(单选) / multiple(多选) / truefalse(判断)",
    )
    options: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        comment=(
            '选项字典（JSON）。'
            '单选/多选标准格式: {"A":"选项文字","B":"...","C":"...","D":"..."}；'
            '判断题固定格式: {"A":"正确","B":"错误"}'
        ),
    )
    correct_answer: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            '正确答案字符串。'
            '单选/判断: "A"（单个键）；'
            '多选: "A,C"（逗号分隔，字母升序）。'
            '服务层多选评分需做无序集合比较'
        ),
    )
    score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
        comment="本题分值（默认10分）。建议全卷各题分值之和等于100以对齐 pass_score 语义",
    )
    order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="题目显示顺序（升序，0起始），前端按此字段渲染题目列表"
    )

    # 所属考试（反向关联）
    exam: Mapped["TrainingExam"] = relationship("TrainingExam", back_populates="questions")


class ExamAttempt(Base):
    """
    考试作答记录 — 记录员工某次考试的完整作答过程与结果。

    生命周期：
      1. 员工开始答题 → 创建记录（started_at=now，submitted_at=None，passed=False）
      2. 员工提交答卷 → 服务层自动计分：
           - 遍历 answers，对比 ExamQuestion.correct_answer
           - 累加各题 score 得到总分
           - score >= pass_score → passed=True
      3. 通过后自动发证 → 服务层生成 certificate_no（格式如 CERT-2026-001234）
           并写入本字段，同步创建 talent.EmployeeCertification 记录。

    字段说明：
      exam_id       所属考试（级联删除）。
      employee_id   参考员工（级联删除）。
      answers       作答字典（JSON），格式：
                      {question_id: "A", question_id: "A,C", ...}
                    键为 ExamQuestion.id 的字符串形式，值为选项键字符串。
      score         最终得分（服务层提交时计算写入，默认0）。
      passed        是否通过（score >= pass_score 时为 True）。
      started_at    开始答题时间（创建时自动写入）。
      submitted_at  提交时间（员工点击"提交"时写入）。
                    为 None 表示尚未提交（中途退出或超时未提交）。
                    一旦提交，结果不可更改（需新建 ExamAttempt 记录重考）。
      certificate_no 合格证编号（passed=True 时由服务层自动生成，如 CERT-2026-001234）。
                    为 None 表示未通过或尚未提交。此编号可作为正式资质证明存档。
    """

    __tablename__ = "exam_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("training_exams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属考试ID（级联删除）",
    )
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="参考员工ID（级联删除）",
    )
    answers: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        comment='{question_id: "A" 或 "A,C"} 格式的作答字典，键为 ExamQuestion.id 字符串',
    )
    score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="最终得分（提交后由服务层自动计算写入，默认0）",
    )
    passed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否通过（score >= TrainingExam.pass_score 时为 True）",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="开始答题时间（记录创建时自动写入）",
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "提交时间（员工点击'提交'时写入）。"
            "为 None 表示尚未提交；一旦提交结果不可更改，重考需新建记录"
        ),
    )
    certificate_no: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment=(
            "合格证编号（passed=True 时由服务层自动生成，格式如 CERT-2026-001234）。"
            "为 None 表示未通过或尚未提交。此编号可同步写入 employee_certifications 表存档"
        ),
    )

    # 关联的考试配置（含及格线信息）
    exam: Mapped["TrainingExam"] = relationship("TrainingExam", lazy="selectin")
    # 参考员工信息
    employee: Mapped["Employee"] = relationship("Employee", lazy="selectin")  # noqa: F821
