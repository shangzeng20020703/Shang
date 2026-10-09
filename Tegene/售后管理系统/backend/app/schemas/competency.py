"""
模块名称: competency.py — 胜任力管理 Pydantic Schemas

作用:
    为胜任力（Competency）模块提供请求验证和响应序列化 DTO。
    覆盖胜任力模型、胜任力项、岗位胜任力要求、员工胜任力评估和差距分析五大实体。

业务背景:
    本公司采用结构化胜任力体系，将能力分为5个等级：
        1=了解 / 2=初级 / 3=中级 / 4=高级 / 5=专家
    三类胜任力模型：
        - core（通用核心）：全员适用，如沟通协作、执行力
        - role_specific（岗位专属）：针对特定职位，如机械调试、电气安全
        - leadership（领导力）：适用于管理序列员工

数据实体层级（从宏观到微观）:
    CompetencyModel（胜任力模型）
        └── CompetencyItem（胜任力项，每项有5级行为描述）
                ├── PositionCompetency（某岗位对该项的要求等级 + 权重）
                └── EmployeeCompetency（员工该项的评估等级 + 评估依据）
    差距分析（GapAnalysis）：
        CompetencyGapItem.gap = required_level - assessed_level
        正数 = 不达标，需培训提升；负数/0 = 已满足要求

依赖关系:
    - models/competency.py: CompetencyModel / CompetencyItem / PositionCompetency / EmployeeCompetency
    - services/competency.py: 提供胜任力 CRUD 和 gap_analysis 业务逻辑
    - api/v1/endpoints/competency.py: 路由层调用

被以下 API 端点使用:
    POST   /api/v1/competency/models              → CompetencyModelCreate → CompetencyModelOut
    GET    /api/v1/competency/models/{id}         → CompetencyModelOut
    GET    /api/v1/competency/models/brief        → CompetencyModelBrief（下拉列表用）
    POST   /api/v1/competency/positions           → PositionCompetencyCreate → PositionCompetencyOut
    POST   /api/v1/competency/employees           → EmployeeCompetencyCreate → EmployeeCompetencyOut
    GET    /api/v1/competency/gap/{employee_id}   → CompetencyGapAnalysis（差距报告）

数据流:
    前端请求 → *Create/*Update (Pydantic 校验) → Service 层处理 → ORM → DB
    ORM 查询结果 → *Out (from_attributes=True 序列化) → 前端响应
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


# ============================================================
# CompetencyItem 胜任力项
# ============================================================

class CompetencyItemBase(BaseModel):
    """
    胜任力项基础字段。
    一个 CompetencyModel 下可以包含多个 CompetencyItem（具体能力点）。
    每个能力项有1-5级行为描述，存储在 level_definitions 字段（JSON字符串）。
    """
    name: str = Field(..., max_length=100, description="胜任力项名称")  # 如"问题分析与解决"、"安全规范执行"
    category: str = Field(..., max_length=50, description="分类")  # 如"核心能力"、"专业技能"、"领导力"
    description: Optional[str] = Field(default=None, description="能力说明")  # 对该能力项的整体说明
    level_definitions: Optional[str] = Field(
        default=None,
        description='1-5级定义JSON, 如: {"1":"初学","2":"熟悉","3":"熟练","4":"精通","5":"专家"}',
    )  # JSON字符串，每级对应具体行为描述，供评估时参考


class CompetencyItemCreate(CompetencyItemBase):
    """
    创建胜任力项的请求体。
    通常不单独调用，而是嵌套在 CompetencyModelCreate.items 中批量创建。
    直接继承 CompetencyItemBase，无额外字段。
    对应 API: POST /api/v1/competency/models（嵌套在模型创建中）
    """
    pass


class CompetencyItemUpdate(BaseModel):
    """
    更新胜任力项的请求体。
    所有字段均为 Optional，支持部分更新（PATCH 语义）。
    对应 API: PUT /api/v1/competency/items/{id}
    """
    name: Optional[str] = Field(default=None, max_length=100)  # 不填则不更新
    category: Optional[str] = Field(default=None, max_length=50)
    description: Optional[str] = None
    level_definitions: Optional[str] = None


class CompetencyItemOut(CompetencyItemBase):
    """
    胜任力项响应体（ORM → JSON）。
    额外返回 id、所属模型 model_id 和创建时间。
    protected_namespaces=() 避免 Pydantic v2 对 model_ 前缀字段发出警告。
    对应 API: 包含在 CompetencyModelOut.items 列表中返回。
    """
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    # protected_namespaces=() 是为了压制 Pydantic v2 对含 model_ 前缀字段名的警告

    id: int
    model_id: int  # 所属胜任力模型的 ID（外键）
    created_at: datetime


# ============================================================
# CompetencyModel 胜任力模型
# ============================================================

class CompetencyModelBase(BaseModel):
    """
    胜任力模型基础字段。
    模型是胜任力管理的顶层容器，每个模型包含一组相关的胜任力项。
    model_type 决定该模型的适用范围：
        - core: 全员通用核心能力
        - role_specific: 特定岗位/职类专属能力
        - leadership: 管理/领导岗位专属能力
    """
    model_config = ConfigDict(protected_namespaces=())
    # protected_namespaces=() 消除 Pydantic v2 对 model_type 字段名的命名空间警告

    name: str = Field(..., max_length=100, description="模型名称")  # 如"机器人工程师通用胜任力模型"
    model_type: str = Field(
        ...,
        pattern=r"^(core|role_specific|leadership)$",
        description="模型类型: core/role_specific/leadership",
    )  # 严格枚举校验，不符合则返回 422
    description: Optional[str] = Field(default=None, description="模型说明")


class CompetencyModelCreate(CompetencyModelBase):
    """
    创建胜任力模型的请求体，支持同时创建下属胜任力项（嵌套创建）。
    items 为空列表时，仅创建空模型；后续可通过 /items 端点逐项添加。
    对应 API: POST /api/v1/competency/models
    """
    items: list[CompetencyItemCreate] = Field(
        default_factory=list, description="胜任力项列表"
    )  # 可以在创建模型时一次性批量创建所有胜任力项


class CompetencyModelUpdate(BaseModel):
    """
    更新胜任力模型的请求体。
    仅允许更新元数据字段（名称、类型、说明），不允许通过此接口修改 items。
    修改 items 请使用专属的 /items/{id} 端点。
    对应 API: PUT /api/v1/competency/models/{id}
    """
    model_config = ConfigDict(protected_namespaces=())

    name: Optional[str] = Field(default=None, max_length=100)
    model_type: Optional[str] = Field(
        default=None, pattern=r"^(core|role_specific|leadership)$"
    )
    description: Optional[str] = None


class CompetencyModelOut(CompetencyModelBase):
    """
    胜任力模型完整响应体（含下属胜任力项列表）。
    从 ORM 对象序列化，items 关系会被 SQLAlchemy relationship 懒加载。
    对应 API: GET /api/v1/competency/models/{id}
    """
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    items: list[CompetencyItemOut] = []  # 胜任力项列表，关联查询后填充
    created_at: datetime
    updated_at: datetime


class CompetencyModelBrief(BaseModel):
    """
    胜任力模型简要信息（用于下拉列表、外键选择等场景）。
    仅返回 id、名称和类型，不含 items 列表，减少传输量。
    对应 API: GET /api/v1/competency/models（列表接口）
    """
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    name: str
    model_type: str  # core / role_specific / leadership


# ============================================================
# PositionCompetency 岗位胜任力要求
# ============================================================

class PositionCompetencyBase(BaseModel):
    """
    岗位胜任力要求基础字段。
    定义某一岗位（职位名称）对某一胜任力项的"要求等级"和"权重"。
    权重用于差距分析时加权计算总分，各项权重之和建议等于100。
    业务场景：HR 为"机械工程师"岗位设定：
        - 安全规范执行 required_level=3, weight=30
        - 机械结构设计 required_level=4, weight=40
        - 文档编写      required_level=2, weight=30
    """
    position_name: str = Field(..., max_length=100, description="岗位名称")  # 如"机械工程师"、"电气调试工程师"
    department_id: Optional[int] = Field(default=None, description="部门ID")  # 可选，关联到具体部门
    competency_item_id: int = Field(..., description="胜任力项ID")  # 外键，关联 CompetencyItem
    required_level: int = Field(..., ge=1, le=5, description="要求等级(1-5)")  # 1=了解 ~ 5=专家
    weight: int = Field(default=100, ge=0, le=100, description="权重百分比")  # 该项在差距评分中的权重


class PositionCompetencyCreate(PositionCompetencyBase):
    """
    创建岗位胜任力要求的请求体。
    对应 API: POST /api/v1/competency/positions
    """
    pass


class PositionCompetencyUpdate(BaseModel):
    """
    更新岗位胜任力要求的请求体。
    通常用于调整要求等级或权重，岗位名称和能力项一般不变。
    对应 API: PUT /api/v1/competency/positions/{id}
    """
    required_level: Optional[int] = Field(default=None, ge=1, le=5)
    weight: Optional[int] = Field(default=None, ge=0, le=100)


class PositionCompetencyOut(PositionCompetencyBase):
    """
    岗位胜任力要求响应体。
    额外返回 competency_item_name（通过 JOIN 或 property 填充），
    方便前端直接展示能力项名称而无需二次查询。
    对应 API: GET /api/v1/competency/positions
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    competency_item_name: Optional[str] = None  # 冗余字段，由 Service 层从关联表填充，供前端展示
    created_at: datetime


# ============================================================
# EmployeeCompetency 员工胜任力评估
# ============================================================

class EmployeeCompetencyBase(BaseModel):
    """
    员工胜任力评估基础字段。
    记录某员工在某胜任力项上的"评估等级"，由评估人（上级/HR/自评）打分。
    评估结果是差距分析（GapAnalysis）的输入数据之一。
    assessed_level 含义：1=了解 / 2=初级 / 3=中级 / 4=高级 / 5=专家
    """
    employee_id: int = Field(..., description="员工ID")  # 被评估员工的 Employee.id
    competency_item_id: int = Field(..., description="胜任力项ID")  # 被评估的具体能力项
    assessed_level: int = Field(..., ge=1, le=5, description="评估等级(1-5)")  # 1~5，对应五级能力标准
    assessor_id: Optional[int] = Field(default=None, description="评估人ID")  # None 时为自评
    evidence: Optional[str] = Field(default=None, description="评估依据")  # 支撑评分的具体事例或说明


class EmployeeCompetencyCreate(EmployeeCompetencyBase):
    """
    创建员工胜任力评估记录的请求体。
    对应 API: POST /api/v1/competency/employees
    """
    pass


class EmployeeCompetencyUpdate(BaseModel):
    """
    更新员工胜任力评估的请求体。
    评估后可调整等级和依据，assessor_id / employee_id / item_id 不可变更。
    对应 API: PUT /api/v1/competency/employees/{id}
    """
    assessed_level: Optional[int] = Field(default=None, ge=1, le=5)
    evidence: Optional[str] = None


class EmployeeCompetencyOut(EmployeeCompetencyBase):
    """
    员工胜任力评估响应体。
    额外返回员工姓名、评估人姓名、胜任力项名称（由 Service 层填充），
    供前端在评估详情页直接展示，无需额外请求。
    对应 API: GET /api/v1/competency/employees/{employee_id}
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    competency_item_name: Optional[str] = None  # 冗余字段，关联 CompetencyItem.name
    employee_name: Optional[str] = None          # 冗余字段，关联 Employee.name
    assessor_name: Optional[str] = None          # 冗余字段，关联评估人的 Employee.name
    assessed_at: datetime                        # 评估发生时间（数据库写入时间）
    created_at: datetime


# ============================================================
# Gap Analysis 差距分析
# ============================================================

class CompetencyGapItem(BaseModel):
    """
    单条胜任力差距明细。
    gap = required_level - assessed_level：
        - 正数（gap > 0）：员工不达标，数值越大差距越大，需要培训
        - 零（gap = 0）：恰好达标
        - 负数（gap < 0）：员工能力超过岗位要求（潜力人才标志）
    该数据用于推荐培训课程（training.CourseRecommendation）和 IDP 目标制定。
    """
    competency_item_id: int
    competency_name: str      # 胜任力项名称，直接展示给用户
    category: str             # 所属分类（如"专业技能"、"核心能力"）
    required_level: int       # 岗位要求等级（来自 PositionCompetency）
    assessed_level: int       # 员工实际评估等级（来自 EmployeeCompetency）
    gap: int = Field(description="差距(required - assessed), 正数表示不达标")
    weight: int               # 该项权重，用于加权计算总差距分


class CompetencyGapAnalysis(BaseModel):
    """
    某员工对应某岗位的完整胜任力差距分析报告。
    由 GET /api/v1/competency/gap/{employee_id}?position_name=xxx 返回。
    业务流程：
        1. Service 层查询该员工的所有 EmployeeCompetency 记录（evaluated）
        2. 查询目标岗位的所有 PositionCompetency 要求（required）
        3. 逐项计算 gap，汇总 total_gap_score 和 match_rate
        4. match_rate 供管理驾驶舱展示人才匹配度概览
    """
    employee_id: int
    employee_name: str
    position_name: str        # 对标的岗位名称（非员工当前岗位，而是分析目标岗位）
    items: list[CompetencyGapItem] = []  # 逐项差距明细
    total_gap_score: float = Field(
        default=0, description="加权差距总分(越大越需关注)"
    )  # sum(gap_i * weight_i / 100)，用于优先级排序
    match_rate: float = Field(
        default=0, description="胜任力匹配率(%)"
    )  # 已达标项权重之和 / 总权重 * 100，范围 0~100
