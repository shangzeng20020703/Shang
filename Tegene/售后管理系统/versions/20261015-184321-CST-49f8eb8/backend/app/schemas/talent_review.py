"""
模块名称: talent_review.py — 人才盘点 Pydantic Schemas

作用:
    为人才盘点模块提供请求验证和响应序列化 DTO。
    覆盖盘点会议（TalentReviewSession）、盘点条目（TalentReviewEntry）、
    九宫格矩阵（NineBox）和继任计划（SuccessionPlan）四大实体。

业务背景:
    本公司每年进行1~2次人才盘点，通过"九宫格矩阵"对员工进行分类，
    指导组织的人才培养、晋升和接班人规划。

九宫格矩阵（3×3）说明:
    纵轴 = potential_rating（潜力）：1=低 / 2=中 / 3=高
    横轴 = performance_rating（绩效）：1=低 / 2=中 / 3=高
    坐标格式："{performance}-{potential}"（如 "3-3" = 高绩效+高潜力）
    九个象限含义：
        3-3: 明星员工（Star）—— 重点培养、快速晋升
        2-3: 潜力之星（Rising Star）—— 提升绩效，重点投入
        1-3: 待证明（Enigma）—— 潜力高但绩效低，分析原因
        3-2: 核心骨干（Core Player）—— 绩效稳定，适度培养
        2-2: 中坚力量（Effective Core）—— 稳定贡献，维持现状
        1-2: 效能待提升（Under Performer）—— 需关注辅导
        3-1: 专家型员工（Expert）—— 绩效好但潜力有限，稳定发挥
        2-1: 功能型员工（Functional Player）—— 常规管理
        1-1: 问题员工（Problem Employee）—— 需改善计划或考虑调整

继任计划（SuccessionPlan）:
    针对关键岗位（如技术总监、生产主管），提前识别并培养接班人候选人。
    候选人就绪度（readiness）：
        ready_now：可立即接任（通常是副职或已历练够的人）
        ready_1_year：1年内可接任（需补充某项关键经验）
        ready_2_years：2年内可接任（仍在发展阶段）
        developing：长期培养中（有潜力但仍需大量发展）

重要注意事项（Validator 兼容性）:
    数据库中部分枚举值可能以中文存储（历史数据或手动导入），
    normalize_* validator（mode="before"）在从数据库读取时自动做映射：
        flight_risk: 低→low, 中→medium, 高→high
        readiness: 即时就绪→ready_now, 1年内→ready_1_year 等
        criticality: 低→low, 中→medium, 高→high, 关键→critical

依赖关系:
    - models/talent_review.py: TalentReviewSession / TalentReviewEntry / SuccessionPlan / SuccessionCandidate
    - services/talent_review.py: 盘点业务逻辑、九宫格计算、继任计划管理
    - api/v1/endpoints/talent_review.py: 路由层调用
    - schemas/talent_profile.py: ReviewSummary 聚合盘点结果到人才画像

被以下 API 端点使用:
    POST   /api/v1/talent-review/sessions                      → TalentReviewSessionCreate → TalentReviewSessionOut
    PATCH  /api/v1/talent-review/sessions/{id}/status          → TalentReviewSessionStatusTransition
    POST   /api/v1/talent-review/sessions/{id}/entries         → TalentReviewEntryCreate → TalentReviewEntryOut
    PUT    /api/v1/talent-review/entries/{id}                  → TalentReviewEntryUpdate → TalentReviewEntryOut
    GET    /api/v1/talent-review/sessions/{id}/nine-box        → NineBoxData
    POST   /api/v1/talent-review/succession-plans              → SuccessionPlanCreate → SuccessionPlanOut
    POST   /api/v1/talent-review/succession-plans/{id}/candidates → SuccessionCandidateCreate → SuccessionCandidateOut

数据流:
    前端请求 → *Create/*Update (Pydantic 校验) → Service 层处理 → ORM → DB
    ORM 查询结果 → *Out (from_attributes=True 序列化，含 normalize validator) → 前端响应
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict, field_validator


# ============================================================
# TalentReviewSession 盘点会议
# ============================================================

class TalentReviewSessionBase(BaseModel):
    """
    人才盘点会议基础字段。
    一次盘点会议可以覆盖全公司或指定部门范围内的所有员工。
    review_type 枚举：
        - annual：年度盘点（全公司，通常 Q4 进行）
        - mid_year：中期盘点（半年一次，通常 Q2 进行）
        - special：专项盘点（如某部门重组、接班人识别等特定场景）
    scope_department_ids：JSON 数组字符串，限定盘点范围的部门 ID 列表，
        如 '[1, 3, 7]'；None 表示覆盖全公司。
    """
    name: str = Field(..., max_length=200, description="盘点名称")  # 如"2025年度人才盘点"
    review_type: str = Field(
        ...,
        pattern=r"^(annual|mid_year|special)$",
        description="盘点类型",
    )  # 严格枚举校验
    year: int = Field(..., ge=2020, le=2100)  # 盘点年度
    scope_department_ids: Optional[str] = Field(
        default=None, description="范围部门ID列表(JSON数组)"
    )  # JSON字符串，None=全公司盘点
    description: Optional[str] = None  # 本次盘点的背景说明或特殊要求


class TalentReviewSessionCreate(TalentReviewSessionBase):
    """
    创建人才盘点会议的请求体。
    创建后状态默认为 draft，HR 设置好参与员工范围后激活。
    对应 API: POST /api/v1/talent-review/sessions
    """
    pass


class TalentReviewSessionUpdate(BaseModel):
    """
    更新盘点会议基础信息的请求体（PATCH 语义）。
    只允许在 draft 状态下修改，进行中（in_progress）后不可更改范围。
    对应 API: PUT /api/v1/talent-review/sessions/{id}
    """
    name: Optional[str] = Field(default=None, max_length=200)
    review_type: Optional[str] = Field(
        default=None, pattern=r"^(annual|mid_year|special)$"
    )
    year: Optional[int] = Field(default=None, ge=2020, le=2100)
    scope_department_ids: Optional[str] = None  # 调整盘点范围
    description: Optional[str] = None


class TalentReviewSessionStatusTransition(BaseModel):
    """
    盘点会议状态流转请求体。
    状态机：draft → in_progress（开始盘点）→ completed（完成盘点）
    in_progress 阶段：HR 和管理层逐一填写员工的 performance/potential 评分。
    completed 阶段：盘点结果锁定，生成九宫格报告，进入接班人规划。
    对应 API: PATCH /api/v1/talent-review/sessions/{id}/status
    """
    target_status: str = Field(
        ...,
        pattern=r"^(in_progress|completed)$",
        description="目标状态",
    )  # draft 不在此处，因创建时已为 draft


class TalentReviewSessionOut(TalentReviewSessionBase):
    """
    盘点会议响应体。
    entry_count：本次盘点已完成评估的员工数量（由 Service 层统计注入）。
    对应 API: GET /api/v1/talent-review/sessions
              GET /api/v1/talent-review/sessions/{id}
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str                # draft / in_progress / completed
    entry_count: int = 0       # 已评估员工数（非 ORM 字段，Service 层计算后注入）
    created_at: datetime
    updated_at: datetime


# ============================================================
# TalentReviewEntry 盘点条目
# ============================================================

class TalentReviewEntryBase(BaseModel):
    """
    单名员工的盘点评估记录基础字段（九宫格打分）。
    每名员工在一次盘点会议中只有一条评估记录（session_id + employee_id 唯一约束）。
    performance_rating（绩效维度）：1=低 / 2=中 / 3=高
        通常参考最近一次绩效考核结果填写。
    potential_rating（潜力维度）：1=低 / 2=中 / 3=高
        由管理层主观评定，结合历史表现、学习能力、领导力等综合判断。
    flight_risk（离职风险）：low / medium / high
        high 风险员工需要 HR 重点关注和挽留。
    is_key_talent（是否关键人才）：True 的员工会被纳入继任计划候选池。
    """
    session_id: int   # 所属盘点会议的 ID（外键）
    employee_id: int  # 被评估员工的 ID
    performance_rating: int = Field(..., ge=1, le=3, description="绩效维度: 1=低/2=中/3=高")
    potential_rating: int = Field(..., ge=1, le=3, description="潜力维度: 1=低/2=中/3=高")
    flight_risk: str = Field(
        default="low",
        pattern=r"^(low|medium|high)$",
        description="离职风险",
    )  # 离职风险评估：low/medium/high
    is_key_talent: bool = False             # 是否标记为关键人才（影响继任计划候选推荐）
    development_suggestion: Optional[str] = None  # 对该员工的发展建议（自由文本）


class TalentReviewEntryCreate(TalentReviewEntryBase):
    """
    为盘点会议添加员工评估记录的请求体。
    Session 状态必须为 in_progress，否则 Service 层返回 400。
    对应 API: POST /api/v1/talent-review/sessions/{session_id}/entries
    """
    pass


class TalentReviewEntryUpdate(BaseModel):
    """
    更新员工盘点评估记录的请求体（PATCH 语义）。
    在盘点会议 completed 之前可以反复修改，完成后锁定。
    对应 API: PUT /api/v1/talent-review/entries/{id}
    """
    performance_rating: Optional[int] = Field(default=None, ge=1, le=3)
    potential_rating: Optional[int] = Field(default=None, ge=1, le=3)
    flight_risk: Optional[str] = Field(
        default=None, pattern=r"^(low|medium|high)$"
    )
    is_key_talent: Optional[bool] = None
    development_suggestion: Optional[str] = None


class TalentReviewEntryOut(TalentReviewEntryBase):
    """
    员工盘点评估记录响应体。
    nine_box_position：由 Service 层根据 performance_rating 和 potential_rating 自动计算，
        格式为 "{performance_rating}-{potential_rating}"，如 "3-3"（明星员工）。
    employee_name：关联查询后填充，供盘点结果列表页直接展示。

    包含两个 validator 处理历史数据兼容性：
    normalize_flight_risk (mode="before")：
        将数据库中可能存在的中文枚举值（低/中/高）映射为英文枚举值（low/medium/high）。
        之所以用 mode="before"，是因为需要在 Pydantic 校验 pattern 之前完成转换。
    对应 API: GET /api/v1/talent-review/sessions/{session_id}/entries
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    nine_box_position: Optional[str] = None  # 如 "3-3"、"2-1"，由 Service 计算后注入
    employee_name: Optional[str] = None       # 员工姓名（冗余，Service 填充）
    created_at: datetime

    @field_validator("flight_risk", mode="before")
    @classmethod
    def normalize_flight_risk(cls, v: str) -> str:
        """
        将数据库中存储的中文离职风险值映射为 API 标准英文枚举值。
        历史数据迁移或手动导入时可能存在中文值，此处兼容处理。
        映射规则: 低→low, 中→medium, 高→high
        mode="before" 确保在 pattern 校验之前完成转换，避免校验失败。
        """
        mapping = {"低": "low", "中": "medium", "高": "high"}
        return mapping.get(v, v)  # 找不到映射则原值返回（英文值直接通过）


# ============================================================
# NineBox 九宫格
# ============================================================

class NineBoxCell(BaseModel):
    """
    九宫格矩阵中单个象限的数据。
    position 格式："{performance_rating}-{potential_rating}"
        如 "3-3" 表示高绩效+高潜力（明星员工），"1-1" 表示低绩效+低潜力（问题员工）。
    employee_names：该象限内所有员工的姓名列表（简短呈现用，详情另查）。
    九个位置的业务含义参见模块文档字符串中的"九宫格矩阵说明"。
    """
    position: str = Field(..., description="位置如'3-3','1-2'")  # 格式："{performance}-{potential}"
    count: int = 0                                               # 该象限员工总数
    employee_names: list[str] = Field(default_factory=list)     # 该象限员工姓名列表


class NineBoxData(BaseModel):
    """
    某次盘点会议的完整九宫格矩阵数据。
    boxes 包含最多9个 NineBoxCell，无员工的象限通常仍返回（count=0）。
    供前端渲染九宫格图表（热力图/散点图）使用。
    对应 API: GET /api/v1/talent-review/sessions/{id}/nine-box
    """
    session_id: int
    boxes: list[NineBoxCell] = Field(default_factory=list)  # 9个象限的数据（可能少于9个，有员工的才有记录）


# ============================================================
# SuccessionPlan 继任计划
# ============================================================

class SuccessionPlanBase(BaseModel):
    """
    关键岗位继任计划基础字段。
    为关键岗位（如技术总监、生产主管）提前识别并储备接班人候选人，
    确保关键岗位发生空缺时能快速填补，降低组织风险。
    criticality（关键程度）枚举：
        - low：一般岗位，有人才库备选即可
        - medium：重要岗位，需提前6个月准备
        - high：关键岗位，需常态化维护候选人
        - critical：最高优先级岗位（如 CEO、CTO），必须随时就绪
    current_holder_id：当前任职人员的员工 ID，None 表示该岗位空缺。
    """
    key_position: str = Field(..., max_length=100, description="关键岗位名称")  # 如"研发总监"、"生产主管"
    department_id: Optional[int] = None         # 所属部门（可选，跨部门岗位可为 None）
    current_holder_id: Optional[int] = None     # 当前任职者的员工 ID（None=岗位空缺）
    criticality: str = Field(
        default="high",
        pattern=r"^(low|medium|high|critical)$",
        description="关键程度",
    )  # 岗位重要程度，影响继任计划优先级排序
    status: str = Field(default="active")  # active=正在执行 / archived=已归档
    notes: Optional[str] = None  # 继任计划背景说明


class SuccessionPlanCreate(SuccessionPlanBase):
    """
    创建关键岗位继任计划的请求体。
    创建后，通过 /candidates 端点逐一添加候选人。
    对应 API: POST /api/v1/talent-review/succession-plans
    """
    pass


class SuccessionPlanUpdate(BaseModel):
    """
    更新继任计划基础信息的请求体（PATCH 语义）。
    current_holder_id 可更新（任职人员变动）。
    对应 API: PUT /api/v1/talent-review/succession-plans/{id}
    """
    key_position: Optional[str] = Field(default=None, max_length=100)
    department_id: Optional[int] = None
    current_holder_id: Optional[int] = None   # 更新现任职人员（如晋升后需更新）
    criticality: Optional[str] = Field(
        default=None, pattern=r"^(low|medium|high|critical)$"
    )
    status: Optional[str] = None              # 归档继任计划（archived）
    notes: Optional[str] = None


class SuccessionCandidateBase(BaseModel):
    """
    继任候选人基础字段。
    一个继任计划可以有多名候选人，按 readiness（就绪度）区分梯次。
    readiness 枚举（就绪度）：
        - ready_now：立即可接任（无需额外准备）
        - ready_1_year：1年内可接任（需补充若干关键经验）
        - ready_2_years：2年内可接任（仍在培养阶段）
        - developing：长期培养中（有潜力但距离接任仍需数年）
    development_actions：JSON 格式的发展行动计划，
        如 '[{"action":"轮岗至海外工厂","timeline":"2025Q2"},...]'
    """
    candidate_id: int  # 候选人的员工 ID（通常来自人才盘点中 is_key_talent=True 的员工）
    readiness: str = Field(
        ...,
        pattern=r"^(ready_now|ready_1_year|ready_2_years|developing)$",
        description="就绪度",
    )  # 候选人距离可接任的时间评估
    development_actions: Optional[str] = Field(
        default=None, description="发展行动(JSON)"
    )  # JSON字符串，记录需要完成的发展行动清单
    notes: Optional[str] = None  # 对该候选人的评估说明


class SuccessionCandidateCreate(SuccessionCandidateBase):
    """
    向继任计划添加候选人的请求体。
    对应 API: POST /api/v1/talent-review/succession-plans/{plan_id}/candidates
    """
    pass


class SuccessionCandidateUpdate(BaseModel):
    """
    更新继任候选人信息的请求体（PATCH 语义）。
    随候选人发展进度更新就绪度（如从 ready_2_years 升级为 ready_1_year）。
    对应 API: PUT /api/v1/talent-review/succession-plans/{plan_id}/candidates/{id}
    """
    readiness: Optional[str] = Field(
        default=None,
        pattern=r"^(ready_now|ready_1_year|ready_2_years|developing)$",
    )  # 更新就绪度（候选人培养进展）
    development_actions: Optional[str] = None  # 更新发展行动计划
    notes: Optional[str] = None


class SuccessionCandidateOut(SuccessionCandidateBase):
    """
    继任候选人响应体。
    candidate_name：关联查询后填充，供继任计划详情页直接展示。

    包含 normalize_readiness validator（mode="before"）：
        处理数据库中可能存在的中文就绪度值（历史数据或手动导入），
        映射规则：即时就绪/立即→ready_now, 1年内/1-2年内→ready_1_year,
                  2年内/2-3年内→ready_2_years, 3年以上/培养中→developing
    对应 API: GET /api/v1/talent-review/succession-plans/{plan_id}/candidates
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_id: int                              # 所属继任计划 ID（外键）
    candidate_name: Optional[str] = None     # 候选人姓名（冗余，Service 填充）
    created_at: datetime

    @field_validator("readiness", mode="before")
    @classmethod
    def normalize_readiness(cls, v: str) -> str:
        """
        将数据库中存储的中文就绪度值映射为 API 标准英文枚举值。
        历史数据和手动导入可能使用中文描述，此处统一转换。
        mode="before" 确保在 pattern 校验前完成转换。
        """
        mapping = {
            "即时就绪": "ready_now",
            "立即": "ready_now",
            "1年内": "ready_1_year",
            "1-2年内": "ready_1_year",
            "2年内": "ready_2_years",
            "2-3年内": "ready_2_years",
            "3年以上": "developing",
            "培养中": "developing",
        }
        return mapping.get(v, v)  # 找不到映射则原值返回（英文值直接通过）


class SuccessionPlanOut(SuccessionPlanBase):
    """
    继任计划完整响应体（含所有候选人列表）。
    current_holder_name：当前任职人员姓名（冗余，Service 填充）。
    candidates：该岗位所有候选人列表，按就绪度排序
        （ready_now 排最前，developing 排最后）。

    包含 normalize_criticality validator（mode="before"）：
        处理数据库中可能存在的中文关键程度值，
        映射规则：低→low, 中→medium, 高→high, 关键→critical
    对应 API: GET /api/v1/talent-review/succession-plans
              GET /api/v1/talent-review/succession-plans/{id}
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    current_holder_name: Optional[str] = None    # 当前任职者姓名（冗余，Service 填充）
    candidates: list[SuccessionCandidateOut] = Field(default_factory=list)  # 所有候选人
    created_at: datetime
    updated_at: datetime

    @field_validator("criticality", mode="before")
    @classmethod
    def normalize_criticality(cls, v: str) -> str:
        """
        将数据库中存储的中文关键程度值映射为 API 标准英文枚举值。
        mode="before" 确保在 pattern 校验前完成转换。
        映射规则: 低→low, 中→medium, 高→high, 关键→critical
        """
        mapping = {
            "低": "low",
            "中": "medium",
            "高": "high",
            "关键": "critical",
        }
        return mapping.get(v, v)  # 找不到映射则原值返回（英文值直接通过）
