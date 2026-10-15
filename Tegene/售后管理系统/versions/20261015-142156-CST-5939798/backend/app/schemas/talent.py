"""
模块名称: talent.py — 人才发展 Pydantic Schemas

作用:
    为人才发展模块提供请求验证和响应序列化 DTO。
    覆盖职业发展通道（CareerPath）、个人发展计划（IDP）、
    员工证书（EmployeeCertification）、员工技能（EmployeeSkill）
    和技能矩阵（SkillMatrix）五大实体。

业务背景:
    本公司采用双通道职业发展体系：
        - 技术通道（technical）：P1（助理工程师）→ P7（首席科学家）
        - 管理通道（management）：M1（组长）→ M5（总裁）
        - 双通道（dual）：技术骨干同时具备管理职责
    员工可以拥有一份或多份 IDP（个人发展计划），每份 IDP 包含若干发展目标（IDPGoal）。
    EmployeeCertification 管理员工的资质证书（安全资质/专业技能/管理认证/行业资质），
    支持到期提醒（expiry_date - today <= reminder_days 时触发告警）。
    EmployeeSkill 是系统评定的技能标签（由 HR/上级录入），
    与 talent_profile.py 中的 SkillItem（员工自填技能）共同构成人才画像的技能维度。

数据实体层级:
    CareerPath（职业通道，定义等级体系）
        └── IDPPlan（员工个人发展计划，绑定通道和目标等级）
                └── IDPGoal（具体发展目标，可关联课程/胜任力项）
    EmployeeCertification（员工证书，独立实体，含到期管理）
    EmployeeSkill（员工技能标签，支持技能矩阵统计）

依赖关系:
    - models/talent.py: CareerPath / IDPPlan / IDPGoal / EmployeeCertification / EmployeeSkill
    - services/talent.py: 发展计划、证书管理、技能管理业务逻辑
    - api/v1/endpoints/talent.py: 路由层调用
    - schemas/talent_profile.py: TalentProfile 聚合时引用证书和技能数据

被以下 API 端点使用:
    POST   /api/v1/talent/career-paths            → CareerPathCreate → CareerPathOut
    GET    /api/v1/talent/career-paths            → list[CareerPathOut]
    POST   /api/v1/talent/idp                     → IDPPlanCreate → IDPPlanOut
    GET    /api/v1/talent/idp/{id}                → IDPPlanOut
    PATCH  /api/v1/talent/idp/{id}/status         → IDPStatusTransition
    POST   /api/v1/talent/idp/{idp_id}/goals      → IDPGoalCreate → IDPGoalOut
    POST   /api/v1/talent/certifications          → EmployeeCertificationCreate → EmployeeCertificationOut
    GET    /api/v1/talent/certifications/expiring → list[EmployeeCertificationOut]（到期提醒）
    POST   /api/v1/talent/skills                  → EmployeeSkillCreate → EmployeeSkillOut
    GET    /api/v1/talent/skills/matrix           → list[SkillMatrixEntry]

数据流:
    前端请求 → *Create/*Update (Pydantic 校验) → Service 层处理 → ORM → DB
    ORM 查询结果 → *Out (from_attributes=True 序列化) → 前端响应
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


# ============================================================
# CareerPath 职业通道
# ============================================================

class CareerPathBase(BaseModel):
    """
    职业发展通道基础字段。
    定义公司的职级体系，是 IDP 计划的参照框架。
    path_type 枚举：
        - technical：技术通道，典型如 P1～P7（助理工程师→首席科学家）
        - management：管理通道，典型如 M1～M5（组长→总裁）
        - dual：双通道，技术骨干同时承担管理职责（如技术总监）
    levels_json：JSON 数组字符串，描述各等级的头衔和要求，
        如 '[{"level":"P1","title":"助理工程师","requirements":"..."},...]'
        供前端在 IDP 填写页以下拉列表展示。
    """
    name: str = Field(..., max_length=100, description="通道名称")  # 如"机器人工程师技术通道"
    path_type: str = Field(
        ...,
        pattern=r"^(technical|management|dual)$",
        description="通道类型: technical/management/dual",
    )  # 严格枚举校验，不符合则返回 422
    description: Optional[str] = Field(default=None, description="通道说明")  # 对该通道的整体描述
    levels_json: Optional[str] = Field(
        default=None,
        description='等级定义JSON数组, 如: [{"level":"P1","title":"初级工程师"}]',
    )  # JSON字符串，各等级的结构化定义，用于 IDP 填写时的等级选择


class CareerPathCreate(CareerPathBase):
    """
    创建职业发展通道的请求体。
    通道创建后，员工可在 IDP 计划中选择该通道作为发展方向。
    对应 API: POST /api/v1/talent/career-paths
    """
    pass


class CareerPathUpdate(BaseModel):
    """
    更新职业发展通道的请求体（PATCH 语义，所有字段可选）。
    支持调整通道名称、类型、说明和等级定义。
    对应 API: PUT /api/v1/talent/career-paths/{id}
    """
    name: Optional[str] = Field(default=None, max_length=100)
    path_type: Optional[str] = Field(
        default=None, pattern=r"^(technical|management|dual)$"
    )
    description: Optional[str] = None
    levels_json: Optional[str] = None  # 更新等级体系结构（如新增等级）


class CareerPathOut(CareerPathBase):
    """
    职业发展通道响应体。
    返回完整字段供 IDP 页面填写时展示通道详情和等级列表。
    对应 API: GET /api/v1/talent/career-paths
              GET /api/v1/talent/career-paths/{id}
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


# ============================================================
# IDPPlan 个人发展计划
# ============================================================

class IDPPlanBase(BaseModel):
    """
    个人发展计划（IDP, Individual Development Plan）基础字段。
    IDP 是员工与上级共同制定的阶段性职业发展规划，包含：
        - 当前所在等级（current_level）：如 P2
        - 目标等级（target_level）：如 P3
        - 目标达成时间（target_date）：如 2026-12-31
        - 一组发展目标（IDPGoal）：技能学习/培训/认证/项目实践等
    一名员工可以同时拥有多份 IDP（如技术方向 + 管理方向）。
    """
    employee_id: int = Field(..., description="员工ID")  # 该 IDP 所属员工
    career_path_id: Optional[int] = Field(default=None, description="职业通道ID")  # 关联 CareerPath，None=未指定通道
    current_level: Optional[str] = Field(default=None, max_length=50, description="当前等级")  # 如 "P2"、"M1"
    target_level: Optional[str] = Field(default=None, max_length=50, description="目标等级")   # 如 "P3"、"M2"
    target_date: Optional[date] = Field(default=None, description="目标达成日期")  # 期望达到目标等级的日期


class IDPPlanCreate(IDPPlanBase):
    """
    创建个人发展计划的请求体。
    创建后状态默认为 draft，调用状态流转接口激活。
    发展目标（IDPGoal）在 IDP 创建后通过 /goals 端点单独添加。
    对应 API: POST /api/v1/talent/idp
    """
    pass


class IDPPlanUpdate(BaseModel):
    """
    更新个人发展计划基础信息的请求体（PATCH 语义）。
    不允许通过此接口修改 employee_id（绑定关系不可变）。
    对应 API: PUT /api/v1/talent/idp/{id}
    """
    career_path_id: Optional[int] = None        # 可切换关联的职业通道
    current_level: Optional[str] = Field(default=None, max_length=50)  # 更新当前等级
    target_level: Optional[str] = Field(default=None, max_length=50)   # 调整目标等级
    target_date: Optional[date] = None          # 调整目标达成日期


class IDPStatusTransition(BaseModel):
    """
    IDP 状态流转请求体。
    状态机：draft → active（开始执行）→ completed（目标达成）/ cancelled（放弃）
    Service 层会校验状态流转的合法性（如不允许 completed → active）。
    对应 API: PATCH /api/v1/talent/idp/{id}/status
    """
    target_status: str = Field(
        ...,
        pattern=r"^(active|completed|cancelled)$",
        description="目标状态",
    )  # draft 不在枚举中，创建时已为 draft，无需流转


class IDPGoalBase(BaseModel):
    """
    IDP 发展目标基础字段。
    每个目标是 IDP 的执行单元，可以是：
        - skill：技能提升（如学习 ROS2 机器人框架）
        - competency：胜任力提升（可关联 linked_competency_id）
        - training：参加指定培训课程（可关联 linked_course_id）
        - certification：获取指定证书
        - project：参与特定项目实践
    progress_pct 由员工或 HR 手动更新，或由系统自动同步
    （如关联课程完成后自动将对应目标标记为 completed）。
    """
    goal_type: str = Field(
        ...,
        pattern=r"^(skill|competency|training|certification|project)$",
        description="目标类型",
    )  # 目标类型枚举，决定关联字段（linked_course_id/linked_competency_id）是否有意义
    description: Optional[str] = Field(default=None, description="目标描述")  # 具体目标内容描述
    progress_pct: int = Field(default=0, ge=0, le=100, description="完成进度")  # 0=未开始，100=已完成
    linked_course_id: Optional[int] = Field(default=None, description="关联培训课程ID")  # goal_type=training 时填写
    linked_competency_id: Optional[int] = Field(default=None, description="关联胜任力项ID")  # goal_type=competency 时填写
    status: str = Field(
        default="pending",
        pattern=r"^(pending|in_progress|completed)$",
        description="状态",
    )  # pending=待开始 / in_progress=进行中 / completed=已完成
    due_date: Optional[date] = Field(default=None, description="截止日期")  # 目标完成截止日期


class IDPGoalCreate(IDPGoalBase):
    """
    向 IDP 计划添加发展目标的请求体。
    对应 API: POST /api/v1/talent/idp/{idp_id}/goals
    """
    pass


class IDPGoalUpdate(BaseModel):
    """
    更新 IDP 发展目标的请求体（PATCH 语义）。
    支持更新进度、状态、截止日期等，不可变更 goal_type 和 IDP 归属。
    对应 API: PUT /api/v1/talent/idp/{idp_id}/goals/{id}
    """
    goal_type: Optional[str] = Field(
        default=None, pattern=r"^(skill|competency|training|certification|project)$"
    )
    description: Optional[str] = None
    progress_pct: Optional[int] = Field(default=None, ge=0, le=100)   # 更新完成进度
    linked_course_id: Optional[int] = None
    linked_competency_id: Optional[int] = None
    status: Optional[str] = Field(
        default=None, pattern=r"^(pending|in_progress|completed)$"
    )
    due_date: Optional[date] = None


class IDPGoalOut(IDPGoalBase):
    """
    IDP 发展目标响应体。
    包含在 IDPPlanOut.goals 中返回，或单独查询目标时使用。
    对应 API: GET /api/v1/talent/idp/{idp_id}/goals
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    idp_id: int       # 所属 IDP 计划的 ID（外键）
    created_at: datetime


class IDPPlanOut(IDPPlanBase):
    """
    个人发展计划完整响应体（含目标列表和关联名称）。
    goals：该 IDP 下所有发展目标。
    employee_name / career_path_name：由 Service 层关联查询后填充，
        供前端在 IDP 详情页直接展示，无需额外请求。
    对应 API: GET /api/v1/talent/idp/{id}
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str                              # draft / active / completed / cancelled
    goals: list[IDPGoalOut] = []             # 该计划下所有发展目标
    employee_name: Optional[str] = None      # 员工姓名（冗余，Service 填充）
    career_path_name: Optional[str] = None   # 职业通道名称（冗余，Service 填充）
    created_at: datetime
    updated_at: datetime


# ============================================================
# EmployeeCertification 员工证书
# ============================================================

class EmployeeCertificationBase(BaseModel):
    """
    员工证书/资质基础字段。
    管理员工持有的各类证书，包括安全作业证、专业技能证书等。
    到期提醒逻辑：当 (expiry_date - today).days <= reminder_days 时，
        Service 层将该记录纳入"即将到期"列表，HR 可查看并通知员工续期。
    cert_type 枚举（固定四类）：
        - 安全资质：如"特种作业操作证"、"叉车驾驶证"
        - 专业技能：如"焊接工程师资质"、"PLC 编程认证"
        - 管理认证：如"PMP 项目管理认证"
        - 行业资质：如"ISO 9001 内审员证书"
    """
    employee_id: int = Field(..., description="员工ID")  # 持证员工的 Employee.id
    cert_name: str = Field(..., max_length=200, description="证书名称")  # 如"特种作业操作证（高处作业）"
    cert_type: str = Field(
        ...,
        pattern=r"^(安全资质|专业技能|管理认证|行业资质)$",
        description="证书类型",
    )  # 严格枚举校验
    issuing_authority: Optional[str] = Field(default=None, max_length=200, description="颁发机构")  # 如"国家安全生产监督管理局"
    cert_number: Optional[str] = Field(default=None, max_length=100, description="证书编号")  # 官方唯一编号，用于真伪核查
    issue_date: Optional[date] = Field(default=None, description="颁发日期")
    expiry_date: Optional[date] = Field(default=None, description="到期日期")  # None=永久有效
    reminder_days: int = Field(default=30, ge=0, description="到期提前提醒天数")  # 默认提前30天提醒


class EmployeeCertificationCreate(EmployeeCertificationBase):
    """
    录入员工证书的请求体。
    创建后状态默认为 valid（有效）。
    对应 API: POST /api/v1/talent/certifications
    """
    pass


class EmployeeCertificationUpdate(BaseModel):
    """
    更新员工证书信息的请求体（PATCH 语义）。
    支持更新证书有效期（续期）、状态（撤销）和提醒设置。
    employee_id 不可更改（证书归属关系不可变）。
    对应 API: PUT /api/v1/talent/certifications/{id}
    """
    cert_name: Optional[str] = Field(default=None, max_length=200)
    cert_type: Optional[str] = Field(
        default=None, pattern=r"^(安全资质|专业技能|管理认证|行业资质)$"
    )
    issuing_authority: Optional[str] = Field(default=None, max_length=200)
    cert_number: Optional[str] = Field(default=None, max_length=100)
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None       # 更新到期日（证书续期场景）
    reminder_days: Optional[int] = Field(default=None, ge=0)
    status: Optional[str] = Field(
        default=None, pattern=r"^(valid|expired|revoked)$"
    )  # valid=有效 / expired=已过期 / revoked=已撤销（注销/作废）


class EmployeeCertificationOut(EmployeeCertificationBase):
    """
    员工证书响应体。
    status：由 Service 层根据 expiry_date 自动判断（到期自动变 expired）。
    employee_name：关联查询后填充，供列表页直接展示。
    对应 API: GET /api/v1/talent/certifications/{id}
              GET /api/v1/talent/certifications/expiring（即将到期列表）
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str                              # valid / expired / revoked
    employee_name: Optional[str] = None      # 员工姓名（冗余，Service 填充）
    created_at: datetime


# ============================================================
# EmployeeSkill 员工技能
# ============================================================

class EmployeeSkillBase(BaseModel):
    """
    员工技能标签基础字段（系统录入，由 HR/上级评定）。
    注意区别：
        - EmployeeSkill（本类）：由 HR/上级在后台评定，是"系统认证"技能
        - EmployeePersonalSkill（在 talent_profile.py）：员工自填的个人技能
    skill_category 枚举（固定四类）：
        - 技术：如"Python 编程"、"机器人调试"、"PLC 编程"
        - 管理：如"项目管理"、"团队建设"
        - 语言：如"英语（CET-6）"、"日语（N2）"
        - 工具：如"SolidWorks"、"AutoCAD"、"Ansys"
    proficiency_level：1=了解 / 2=初级 / 3=中级 / 4=高级 / 5=专家
        （与胜任力五级对齐，方便统一展示）
    """
    employee_id: int = Field(..., description="员工ID")
    skill_name: str = Field(..., max_length=100, description="技能名称")  # 如"ROS2 机器人开发"
    skill_category: str = Field(
        ...,
        pattern=r"^(技术|管理|语言|工具)$",
        description="技能分类",
    )  # 严格枚举，决定技能矩阵的分类维度
    proficiency_level: int = Field(..., ge=1, le=5, description="熟练度(1-5)")  # 1=了解 ~ 5=专家


class EmployeeSkillCreate(EmployeeSkillBase):
    """
    为员工录入技能标签的请求体。
    对应 API: POST /api/v1/talent/skills
    """
    pass


class EmployeeSkillUpdate(BaseModel):
    """
    更新员工技能熟练度的请求体（PATCH 语义）。
    对应 API: PUT /api/v1/talent/skills/{id}
    """
    skill_name: Optional[str] = Field(default=None, max_length=100)
    skill_category: Optional[str] = Field(
        default=None, pattern=r"^(技术|管理|语言|工具)$"
    )
    proficiency_level: Optional[int] = Field(default=None, ge=1, le=5)  # 更新技能熟练度


class EmployeeSkillOut(EmployeeSkillBase):
    """
    员工技能响应体。
    employee_name：关联查询填充，供技能列表页展示。
    对应 API: GET /api/v1/talent/skills?employee_id={id}
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_name: Optional[str] = None  # 员工姓名（冗余，Service 填充）
    created_at: datetime
    updated_at: datetime


# ============================================================
# SkillMatrix 技能矩阵
# ============================================================

class SkillMatrixSkillItem(BaseModel):
    """
    技能矩阵中单项技能的统计数据。
    描述某部门内某项技能的覆盖情况和平均水平。
    employee_count：该部门持有该技能的员工人数。
    avg_proficiency：该部门持有该技能的员工的平均熟练度（1~5）。
    """
    skill_name: str
    skill_category: str      # 技术 / 管理 / 语言 / 工具
    employee_count: int = 0  # 持有该技能的员工人数（人才储备密度）
    avg_proficiency: float = 0  # 平均熟练度（技能整体水平）


class SkillMatrixEntry(BaseModel):
    """
    技能矩阵中单个部门的完整数据。
    由多个 SkillMatrixSkillItem 组成，展示该部门的技能分布全貌。
    用于管理驾驶舱"技能矩阵"图表，横轴=部门，纵轴=技能项，颜色=平均熟练度。
    对应 API: GET /api/v1/talent/skills/matrix
    """
    department_id: int
    department_name: str
    skills: list[SkillMatrixSkillItem] = []  # 该部门所有已记录技能的统计列表
