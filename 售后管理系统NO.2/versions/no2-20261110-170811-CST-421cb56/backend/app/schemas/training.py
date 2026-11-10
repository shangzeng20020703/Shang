"""
模块名称: training.py — 培训管理 Pydantic Schemas

作用:
    为培训管理模块提供请求验证和响应序列化 DTO。
    覆盖培训课程、培训计划、报名记录、在线考试、证书、反馈、统计分析和课程推荐八大子模块。

业务背景:
    本公司培训体系以"课程"为基础单元，通过"培训计划"组织多门课程，
    员工"报名"参加计划中的具体课程场次，完成后可参加"在线考试"，
    考试通过（score >= pass_score）自动在 ExamAttempt 中记录证书编号。
    培训课程还可与胜任力项关联（CourseCompetency），支持差距分析后的精准推荐。

培训状态机:
    TrainingEnrollment.status：
        enrolled（已报名）
            → attended（已参加，progress_pct=100，可有 score）
            → absent（缺勤）
    TrainingPlan.status：
        draft（草稿）→ active（进行中）→ completed（已完成）→ cancelled（已取消）
    ExamAttempt：
        多次考试允许（无唯一约束），score >= pass_score 时 passed=True，自动生成 certificate_no

数据实体层级:
    TrainingCourse（课程）
        ├── CourseCompetency（课程关联胜任力项）
        └── TrainingPlanCourse（计划-课程关联，含场次信息）
    TrainingPlan（培训计划）
        └── TrainingPlanCourse → TrainingEnrollment（员工报名记录）
    TrainingExam（考试）→ ExamQuestion（题目）→ ExamAttempt（考试记录）

依赖关系:
    - models/training.py: 全部 ORM 模型
    - services/training.py: 所有培训业务逻辑
    - api/v1/endpoints/training.py: 路由层调用

被以下 API 端点使用:
    POST   /api/v1/training/courses                       → TrainingCourseCreate → TrainingCourseOut
    GET    /api/v1/training/courses/{id}                  → TrainingCourseOut
    GET    /api/v1/training/courses (列表)                 → TrainingCourseBrief
    POST   /api/v1/training/plans                         → TrainingPlanCreate → TrainingPlanOut
    PATCH  /api/v1/training/plans/{id}/status             → TrainingPlanStatusTransition
    POST   /api/v1/training/enrollments                   → TrainingEnrollmentCreate → TrainingEnrollmentOut
    POST   /api/v1/training/enrollments/batch             → TrainingEnrollmentBatchCreate
    POST   /api/v1/training/enrollments/{id}/complete     → EnrollmentComplete
    POST   /api/v1/training/enrollments/{id}/feedback     → EnrollmentFeedback
    POST   /api/v1/training/exams                         → TrainingExamCreate → TrainingExamOut
    POST   /api/v1/training/exams/{id}/attempts           → ExamAttemptCreate → ExamAttemptOut
    POST   /api/v1/training/attempts/{id}/submit          → SubmitAttemptRequest → ExamAttemptOut
    GET    /api/v1/training/stats/overview                → TrainingStatsOverview
    GET    /api/v1/training/recommend/{employee_id}       → list[CourseRecommendation]

数据流:
    前端请求 → *Create/*Update (Pydantic 校验) → Service 层处理 → ORM → DB
    ORM 查询结果 → *Out (from_attributes=True 序列化) → 前端响应
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


# ============================================================
# TrainingCourseCompetency 课程-胜任力关联
# ============================================================

class CourseCompetencyBase(BaseModel):
    """
    课程与胜任力项的关联关系基础字段。
    描述学完本课程后，该胜任力项预计能提升多少等级。
    用于"差距推荐"功能：当员工存在胜任力差距时，
    系统优先推荐能弥补该差距的课程，并计算 relevance_score。
    """
    competency_item_id: int  # 关联的胜任力项 ID（外键 → CompetencyItem）
    target_level_delta: int = Field(default=1, ge=1, le=5, description="预计提升等级数")  # 学完课程后预期提升的等级数（1~5）


class CourseCompetencyCreate(CourseCompetencyBase):
    """
    创建课程-胜任力关联的请求体。
    嵌套在 TrainingCourseCreate.competency_links 中使用，
    随课程一起批量创建。
    对应 API: POST /api/v1/training/courses（嵌套）
    """
    pass


class CourseCompetencyOut(CourseCompetencyBase):
    """
    课程-胜任力关联响应体。
    额外返回 competency_item_name，方便前端展示能力项名称。
    包含在 TrainingCourseOut.competency_links 中返回。
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int                              # 所属课程的 ID
    competency_item_name: Optional[str] = None  # 关联能力项名称，由 Service 层填充


# ============================================================
# TrainingCourse 培训课程
# ============================================================

class TrainingCourseBase(BaseModel):
    """
    培训课程基础字段。
    课程是培训体系最小单位，可以加入一个或多个培训计划中。
    category 枚举：入职/岗位/安全/管理/技术（固定5类，匹配前端下拉）。
    delivery_method 枚举：
        - online：纯线上自学（配套考试）
        - offline：线下集中授课（有签到）
        - blended：混合式（线上+线下）
    target_positions：JSON数组字符串，存储适用岗位列表，
        如 '["电气调试工程师","机械工程师"]'。
    """
    name: str = Field(..., max_length=200, description="课程名称")  # 如"工业机器人安全操作规程"
    category: str = Field(
        ...,
        pattern=r"^(入职|岗位|安全|管理|技术)$",
        description="课程分类",
    )  # 严格枚举校验，不符合则返回 422
    description: Optional[str] = None                               # 课程简介
    delivery_method: str = Field(
        default="offline",
        pattern=r"^(online|offline|blended)$",
        description="授课方式",
    )  # 默认线下
    duration_hours: Decimal = Field(
        default=Decimal("1.0"), gt=0, description="课时(小时)"
    )  # 用 Decimal 避免浮点精度问题，影响培训总学时统计
    is_mandatory: bool = Field(default=False, description="是否必修")  # 必修课未完成时影响员工合规状态
    target_positions: Optional[str] = Field(
        default=None,
        description='目标岗位JSON, 如: ["电气调试","机械安装"]',
    )  # JSON数组字符串，用于课程推荐时按岗位过滤
    is_active: bool = True  # 下架后（False）不再对员工展示和推荐


class TrainingCourseCreate(TrainingCourseBase):
    """
    创建培训课程的请求体，支持同时关联胜任力项（嵌套创建）。
    competency_links 为空时，创建纯课程（无胜任力关联）；
    填写后，完课可自动触发胜任力提升计算。
    对应 API: POST /api/v1/training/courses
    """
    competency_links: list[CourseCompetencyCreate] = Field(
        default_factory=list, description="关联胜任力项"
    )  # 课程与胜任力项的多对多关系，创建时一并建立


class TrainingCourseUpdate(BaseModel):
    """
    更新培训课程的请求体（PATCH 语义，所有字段可选）。
    不允许通过此接口修改 competency_links，需使用专属端点。
    对应 API: PUT /api/v1/training/courses/{id}
    """
    name: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(
        default=None, pattern=r"^(入职|岗位|安全|管理|技术)$"
    )
    description: Optional[str] = None
    delivery_method: Optional[str] = Field(
        default=None, pattern=r"^(online|offline|blended)$"
    )
    duration_hours: Optional[Decimal] = Field(default=None, gt=0)
    is_mandatory: Optional[bool] = None
    target_positions: Optional[str] = None
    is_active: Optional[bool] = None  # 设为 False 即下架课程


class TrainingCourseOut(TrainingCourseBase):
    """
    培训课程完整响应体（含胜任力关联列表）。
    从 ORM 对象序列化，competency_links 由 SQLAlchemy relationship 填充。
    对应 API: GET /api/v1/training/courses/{id}
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    competency_links: list[CourseCompetencyOut] = []  # 该课程关联的胜任力项列表
    created_at: datetime
    updated_at: datetime


class TrainingCourseBrief(BaseModel):
    """
    培训课程简要信息（用于列表展示、下拉选择、报名确认页等轻量场景）。
    不含详细说明和胜任力关联列表，减少传输量。
    对应 API: GET /api/v1/training/courses（列表接口）
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str
    is_mandatory: bool  # 前端列表中用于标注"必修"角标


# ============================================================
# TrainingPlanCourse 计划-课程关联
# ============================================================

class PlanCourseBase(BaseModel):
    """
    培训计划与具体课程场次的关联基础字段。
    同一课程可加入多个计划，也可在同一计划中安排多个场次（不同日期/讲师/地点）。
    trainer_id：内部讲师（员工ID）；trainer_name：也可填外部讲师姓名。
    max_participants：到上限后报名接口自动拒绝（返回 409）。
    """
    course_id: int                                                       # 关联的课程 ID
    scheduled_date: Optional[date] = None                                # 计划开课日期，None 表示待定
    trainer_name: Optional[str] = Field(default=None, max_length=100)   # 讲师姓名（内/外部均可）
    trainer_id: Optional[int] = None                                     # 内部讲师的员工 ID（可选）
    location: Optional[str] = Field(default=None, max_length=200)        # 培训地点/教室/线上链接
    max_participants: Optional[int] = Field(default=None, ge=1)          # 最大参与人数，None=不限


class PlanCourseCreate(PlanCourseBase):
    """
    向培训计划添加课程场次的请求体。
    对应 API: POST /api/v1/training/plans/{plan_id}/courses
    """
    pass


class PlanCourseUpdate(BaseModel):
    """
    更新培训计划内课程场次信息的请求体。
    课程本身（course_id）不可更改，只能调整场次安排。
    对应 API: PUT /api/v1/training/plans/{plan_id}/courses/{id}
    """
    scheduled_date: Optional[date] = None
    trainer_name: Optional[str] = Field(default=None, max_length=100)
    trainer_id: Optional[int] = None
    location: Optional[str] = None
    max_participants: Optional[int] = Field(default=None, ge=1)


class PlanCourseOut(PlanCourseBase):
    """
    培训计划课程场次响应体。
    额外返回 course_name 和 course_category，
    方便前端在计划详情页直接展示课程信息。
    包含在 TrainingPlanOut.plan_courses 中返回。
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_id: int                            # 所属培训计划 ID
    course_name: Optional[str] = None       # 课程名称（冗余字段，由 Service 填充）
    course_category: Optional[str] = None   # 课程分类（冗余字段，由 Service 填充）


# ============================================================
# TrainingPlan 培训计划
# ============================================================

class TrainingPlanBase(BaseModel):
    """
    培训计划基础字段。
    计划是对一段时间内培训安排的整体规划。
    plan_type 枚举：
        - annual：年度培训计划（year 必填，quarter 为 None）
        - quarterly：季度培训计划（year + quarter 必填）
        - onboarding：入职培训计划（通常与 HR 入职流程联动）
    budget：培训预算总额（Decimal 避免精度问题），用于财务报表。
    """
    name: str = Field(..., max_length=200, description="计划名称")  # 如"2025年度安全培训计划"
    plan_type: str = Field(
        ...,
        pattern=r"^(annual|quarterly|onboarding)$",
        description="计划类型",
    )  # 决定 quarter 字段是否有意义
    year: int = Field(..., ge=2020, le=2100)                        # 计划年度
    quarter: Optional[int] = Field(default=None, ge=1, le=4)       # 季度（仅 quarterly 类型使用）
    budget: Optional[Decimal] = Field(default=None, ge=0)          # 计划预算，None=未设置预算
    description: Optional[str] = None


class TrainingPlanCreate(TrainingPlanBase):
    """
    创建培训计划的请求体。
    创建后状态为 draft（草稿），需手动激活（PATCH /status）。
    对应 API: POST /api/v1/training/plans
    """
    pass


class TrainingPlanUpdate(BaseModel):
    """
    更新培训计划基础信息的请求体（仅元数据，不包含状态和课程）。
    plan_type / year / quarter 不可更改（业务约束）。
    对应 API: PUT /api/v1/training/plans/{id}
    """
    name: Optional[str] = Field(default=None, max_length=200)
    budget: Optional[Decimal] = Field(default=None, ge=0)
    description: Optional[str] = None


class TrainingPlanStatusTransition(BaseModel):
    """
    培训计划状态流转请求体。
    状态只允许单向流转，不可回退：
        draft → active：开始执行计划，允许员工报名
        active → completed：计划执行结束，统计完成情况
        active/draft → cancelled：取消计划（需撤销所有报名）
    对应 API: PATCH /api/v1/training/plans/{id}/status
    """
    target_status: str = Field(
        ...,
        pattern=r"^(active|completed|cancelled)$",
        description="目标状态",
    )  # draft 不在此枚举中，因为创建时即为 draft，无需流转到 draft


class TrainingPlanOut(TrainingPlanBase):
    """
    培训计划完整响应体（含课程场次列表）。
    status 由 Service 层维护，从 ORM 对象直接读取。
    对应 API: GET /api/v1/training/plans/{id}
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str                          # draft / active / completed / cancelled
    plan_courses: list[PlanCourseOut] = []  # 本计划包含的所有课程场次
    created_at: datetime
    updated_at: datetime


# ============================================================
# TrainingEnrollment 培训报名/记录
# ============================================================

class TrainingEnrollmentBase(BaseModel):
    """
    培训报名记录基础字段。
    一条报名记录绑定：员工 + 课程 + 可选的计划场次。
    plan_course_id 为 None 时，表示员工自行报名某课程（非计划内）。
    """
    employee_id: int        # 报名员工的 ID
    course_id: int          # 报名的课程 ID
    plan_course_id: Optional[int] = None  # 所属计划场次 ID（None=自由报名）


class TrainingEnrollmentCreate(TrainingEnrollmentBase):
    """
    单人课程报名请求体。
    创建后状态为 enrolled（已报名），参加后由 Service 更新为 attended/absent。
    对应 API: POST /api/v1/training/enrollments
    """
    pass


class TrainingEnrollmentBatchCreate(BaseModel):
    """
    批量报名请求体（为多名员工同时报名同一课程场次）。
    Service 层循环创建每条 TrainingEnrollment 记录，逐一校验 max_participants 上限。
    对应 API: POST /api/v1/training/enrollments/batch
    """
    employee_ids: list[int] = Field(..., min_length=1)  # 至少1名员工，最多由业务限制
    course_id: int
    plan_course_id: Optional[int] = None


class EnrollmentComplete(BaseModel):
    """
    标记课程完成的请求体（签到完成或线上完课）。
    score 为可选项：线下课程可能不打分（None），线上考试通过则填分数。
    Service 层收到后将 status 更新为 attended，progress_pct 设为 100。
    对应 API: POST /api/v1/training/enrollments/{id}/complete
    """
    score: Optional[Decimal] = Field(default=None, ge=0, le=100)  # 完课分数，范围 0~100


class EnrollmentFeedback(BaseModel):
    """
    员工对课程提交满意度反馈的请求体。
    feedback_score：1=非常不满意 / 2=不满意 / 3=一般 / 4=满意 / 5=非常满意
    仅允许已完成（attended）的报名提交反馈（Service 层校验状态）。
    对应 API: POST /api/v1/training/enrollments/{id}/feedback
    """
    feedback_score: int = Field(..., ge=1, le=5, description="满意度(1-5)")
    feedback_comment: Optional[str] = None  # 文字反馈，可为空


class TrainingEnrollmentOut(BaseModel):
    """
    培训报名记录响应体。
    status 值：enrolled / attended / absent
    progress_pct：在线课程可阶段性更新，0~100；线下课完课后直接设为 100。
    employee_name / course_name：由 Service 层通过关联查询填充，供前端直接展示。
    对应 API: GET /api/v1/training/enrollments/{id}
                GET /api/v1/training/employees/{employee_id}/enrollments
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    course_id: int
    plan_course_id: Optional[int]       # None 表示自由报名（非计划场次）
    status: str                         # enrolled / attended / absent
    progress_pct: int                   # 完成进度 0~100（线上课程有意义）
    score: Optional[Decimal]            # 完课分数（可为空）
    feedback_score: Optional[int]       # 满意度评分（1~5，完课后才可填）
    feedback_comment: Optional[str]     # 满意度文字说明
    completed_at: Optional[datetime]    # 完课时间（attended 后填入）
    created_at: datetime                # 报名时间
    employee_name: Optional[str] = None # 员工姓名（冗余，由 Service 填充）
    course_name: Optional[str] = None   # 课程名称（冗余，由 Service 填充）


# ============================================================
# Training Stats 培训统计
# ============================================================

class TrainingStatsOverview(BaseModel):
    """
    培训整体统计概览数据。
    用于管理驾驶舱首页培训模块的 KPI 卡片展示。
    completion_rate = completed_enrollments / total_enrollments * 100
    avg_feedback_score：所有已提交反馈的平均分（1~5），反映培训满意度。
    total_training_hours：所有已完成报名的课时总和（课时 × 完成人次）。
    对应 API: GET /api/v1/training/stats/overview
    """
    total_courses: int = 0             # 系统内活跃课程总数
    active_plans: int = 0              # 当前进行中的培训计划数
    total_enrollments: int = 0         # 所有报名记录总数
    completed_enrollments: int = 0     # 已完成（attended）的报名数
    completion_rate: float = 0         # 完课率（%）
    avg_feedback_score: float = 0      # 平均满意度（1~5）
    total_training_hours: float = 0    # 总培训人次学时


class DepartmentTrainingStat(BaseModel):
    """
    按部门统计的培训完成情况。
    用于管理驾驶舱中的部门培训完成率对比图表。
    avg_score：部门内所有有分数的报名记录的平均分。
    对应 API: GET /api/v1/training/stats/departments
    """
    department_id: int
    department_name: str
    enrollment_count: int = 0      # 部门总报名人次
    completed_count: int = 0       # 部门已完成人次
    completion_rate: float = 0     # 部门完课率（%）
    avg_score: float = 0           # 部门平均成绩


# ============================================================
# Training Recommendation 基于胜任力差距的课程推荐
# ============================================================

class CourseRecommendation(BaseModel):
    """
    基于胜任力差距分析生成的课程推荐结果。
    推荐逻辑（在 services/training.py 中实现）：
        1. 获取员工胜任力差距（competency.gap_analysis）
        2. 查找与差距项有关联的课程（CourseCompetency）
        3. 按 relevance_score 降序排列（分数越高越推荐）
    relevance_score 计算：gap * weight / target_level_delta（综合考量差距大小和提升效率）
    对应 API: GET /api/v1/training/recommend/{employee_id}
    """
    course_id: int
    course_name: str
    category: str                  # 课程分类（入职/岗位/安全/管理/技术）
    gap_competency_name: str       # 该推荐针对的胜任力项名称
    current_level: int             # 员工当前评估等级（1~5）
    required_level: int            # 岗位要求等级（1~5）
    gap: int                       # 差距值 = required_level - current_level（正数=不达标）
    target_level_delta: int        # 该课程预期提升等级数
    relevance_score: float = Field(description="推荐相关度")  # 综合推荐优先级分数，越大越优先


# ============================================================
# TrainingExam 在线考试
# ============================================================

class ExamQuestionCreate(BaseModel):
    """
    创建考试题目的请求体。
    question_type 枚举：
        - single：单选题（correct_answer 为单个字母，如 "A"）
        - multiple：多选题（correct_answer 为逗号分隔，如 "A,C"）
        - truefalse：判断题（correct_answer 为 "A"=正确 或 "B"=错误）
    options 格式：{"A":"选项A文本","B":"选项B文本",...}
    score：该题分值，考试总分 = 所有题目分值之和。
    对应 API: POST /api/v1/training/exams/{exam_id}/questions
    """
    question_text: str = Field(..., description="题目内容")
    question_type: str = Field(default="single", pattern=r"^(single|multiple|truefalse)$", description="题型")
    options: dict = Field(..., description='选项 {"A":"...","B":"...",...}')
    correct_answer: str = Field(..., max_length=50, description='正确答案 "A" 或 "A,B"')  # 多选用逗号分隔
    score: int = Field(default=10, ge=1, description="该题分值")  # 默认每题10分
    order: int = Field(default=0, ge=0, description="显示顺序")  # 数字越小越靠前


class ExamQuestionOut(BaseModel):
    """
    考试题目响应体（不含正确答案）。
    考试进行中向员工展示时，故意隐藏 correct_answer，
    避免泄题。批改/管理员查看请使用 ExamQuestionWithAnswer。
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    exam_id: int
    question_text: str
    question_type: str
    options: dict
    score: int
    order: int
    # correct_answer 字段在此故意省略，不暴露给考生端


class ExamQuestionWithAnswer(ExamQuestionOut):
    """
    含正确答案的考试题目响应体（仅用于批改/管理端）。
    继承自 ExamQuestionOut，补充 correct_answer 字段。
    Service 层批改时（submit_attempt）内部使用，不对考生端开放。
    """
    correct_answer: str  # 批改用，单选="A"，多选="A,B"，判断="A"或"B"


class TrainingExamCreate(BaseModel):
    """
    创建在线考试的请求体。
    pass_score：及格分（0~100），ExamAttempt 提交后自动判断 passed=True/False。
    time_limit：考试时限（分钟），None=不限时；前端根据此值显示倒计时。
    考试与课程1对1绑定（course_id），一门课程最多一套活跃考试。
    对应 API: POST /api/v1/training/exams
    """
    course_id: int
    title: str = Field(..., max_length=200, description="考试标题")
    pass_score: int = Field(default=60, ge=0, le=100, description="及格分")  # 默认60分及格
    time_limit: Optional[int] = Field(default=None, ge=1, description="时限(分钟),空=不限")


class TrainingExamOut(BaseModel):
    """
    在线考试响应体。
    questions_count：题目总数（由 Service 层统计后填充）。
    is_active：只有 True 的考试才对员工可见；下线后设为 False。
    对应 API: GET /api/v1/training/exams/{id}
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    title: str
    pass_score: int              # 及格分
    time_limit: Optional[int]   # 时限（分钟），None=不限时
    is_active: bool
    created_at: datetime
    questions_count: int = 0    # 题目数量（非 ORM 字段，Service 层计算后注入）
    course_name: Optional[str] = None  # 关联课程名称（冗余，Service 填充）


class ExamAttemptCreate(BaseModel):
    """
    开始一次考试的请求体（创建考试记录，记录开始时间）。
    Service 层创建 ExamAttempt 后返回题目列表（不含答案），
    员工在前端作答，最终调用 submit 提交。
    对应 API: POST /api/v1/training/exams/{exam_id}/attempts
    """
    exam_id: int  # 要参加的考试 ID


class SubmitAttemptRequest(BaseModel):
    """
    提交考试答案的请求体。
    answers 格式：{question_id（字符串或整数）: answer_str}
    Service 层遍历 answers 与 ExamQuestion.correct_answer 逐题比对，
    计算总分，判断是否及格，及格则自动生成 certificate_no。
    对应 API: POST /api/v1/training/attempts/{attempt_id}/submit
    """
    answers: dict  # {question_id: "A"} 或 {question_id: "A,B"}（多选）


class ScoreDetail(BaseModel):
    """
    单道题目的批改结果明细。
    包含在 ExamAttemptOut.score_detail 中返回，
    供员工考后查看答错了哪些题及正确答案。
    """
    question_id: int
    your_answer: str      # 员工的作答
    correct_answer: str   # 正确答案
    correct: bool         # 是否答对


class ExamAttemptOut(BaseModel):
    """
    考试记录响应体。
    submitted_at 为 None 时表示考试还未提交（进行中）。
    passed=True 且 certificate_no 不为 None 时，表示已自动生成证书编号。
    score_detail 仅在提交后（submitted_at 不为 None）才有值，
    展示逐题批改结果供员工复盘。
    对应 API: GET /api/v1/training/attempts/{id}
              POST /api/v1/training/attempts/{id}/submit（提交时返回）
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    exam_id: int
    employee_id: int
    answers: dict                              # 员工所有作答（JSON存储）
    score: int                                 # 总得分
    passed: bool                               # 是否及格（score >= pass_score）
    started_at: datetime                       # 开始时间
    submitted_at: Optional[datetime]           # 提交时间（None=未提交）
    certificate_no: Optional[str]             # 证书编号（及格后自动生成）
    employee_name: Optional[str] = None       # 员工姓名（冗余，Service 填充）
    exam_title: Optional[str] = None          # 考试标题（冗余，Service 填充）
    score_detail: Optional[list[ScoreDetail]] = None  # 逐题批改结果（提交后才有）
