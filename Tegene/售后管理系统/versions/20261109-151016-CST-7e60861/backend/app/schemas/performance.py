"""
绩效管理模块 - Pydantic Schemas
================================

模块名称: performance.py
所属系统: 售后管理系统 - 绩效管理模块

作用:
    定义绩效管理相关的请求/响应数据结构，包含三层核心业务模型：
      1. PerformanceTemplate（考核模板）— 定义指标体系与权重分配
      2. PerformanceCycle（考核周期）— 管理时间范围与强制分布规则
      3. PerformanceEvaluation（绩效评估）— 记录自评与上级评分，计算最终得分
    此外还包含目标管理（GoalCreate/GoalOut, KPI/OKR）和强制分布计算辅助结构。

评分计算规则（核心公式）:
    weighted_score = self_score × self_eval_weight + superior_score × superior_eval_weight
    默认权重: 自评 30%（0.30），上级评分 70%（0.70）
    加权分数由 service 层计算后写入 PerformanceEvaluation.weighted_score

等级划分与强制分布:
    根据 weighted_score 确定等级 A/B/C/D，各等级比例受 forced_distribution_json 约束：
      - A（优秀）: 通常 ≤ 10%
      - B（良好）: 通常 ≤ 30%
      - C（合格）: 通常 50% 左右
      - D（待改进）: 通常 10% 左右
    具体比例存储在 PerformanceCycle.forced_distribution_json，
    由 ForcedDistributionCheck 验证合规性。

数据流向:
    前端表单 → *Create/*Update Schema（请求验证）
         ↓
    service 层（计算 weighted_score、校验等级分布）
         ↓
    ORM Model 持久化到 PostgreSQL
         ↓
    *Out Schema（响应序列化，from_attributes=True）→ 前端展示

被调用的 API 端点 (api/v1/endpoints/performance.py):
    POST   /performance/templates          - 创建考核模板
    GET    /performance/templates/{id}     - 获取模板详情（含指标列表）
    PUT    /performance/templates/{id}     - 更新模板
    POST   /performance/cycles             - 创建考核周期
    PATCH  /performance/cycles/{id}/status - 推进周期状态
    POST   /performance/evaluations        - 创建绩效评估记录
    POST   /performance/evaluations/{id}/self-eval       - 提交员工自评
    POST   /performance/evaluations/{id}/superior-score  - 提交上级评分
    POST   /performance/evaluations/{id}/interview       - 提交面谈记录
    GET    /performance/cycles/{id}/distribution         - 查看等级分布
    GET    /performance/cycles/{id}/distribution/check   - 强制分布合规检查
    POST   /performance/goals              - 创建 KPI/OKR 目标
    GET    /performance/goals/{id}/summary - 目标完成度汇总

依赖:
    - pydantic v2: BaseModel, Field, ConfigDict
    - 关联 models/performance.py: PerformanceTemplate, PerformanceCycle, PerformanceEvaluation, Goal
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict


# ============================================================
# PerformanceTemplate 考核模板
# ============================================================

class PerformanceIndicatorBase(BaseModel):
    """
    绩效考核指标基础 Schema

    用于定义考核模板中的单个评分维度（指标）。
    每个指标属于某一分类（业绩/能力/态度/安全），并配置权重与满分值。
    多个指标共同构成一个完整的考核模板（PerformanceTemplate）。

    字段说明:
        name:        指标名称，例如 "生产合格率"、"工作态度"
        description: 指标的详细说明，帮助员工理解评分标准
        category:    指标分类，枚举值：业绩/能力/态度/安全（正则校验）
        weight:      该指标在模板中的权重百分比（1-100），所有指标权重之和应为100
        max_score:   单项指标满分，默认100分
        sort_order:  显示排序权重，数字越小越靠前
    """
    name: str = Field(..., max_length=100, description="指标名称")
    description: Optional[str] = Field(default=None, description="指标说明")
    category: str = Field(
        ..., pattern=r"^(业绩|能力|态度|安全)$", description="分类"
    )
    weight: Decimal = Field(..., gt=0, le=100, description="权重百分比")  # 单位：%，如 30 表示占30%
    max_score: Decimal = Field(default=Decimal("100.00"), gt=0, description="满分")  # 默认100分
    sort_order: int = Field(default=0, ge=0)  # 排序，越小越靠前


class PerformanceIndicatorCreate(PerformanceIndicatorBase):
    """
    创建绩效指标的请求体。

    通常不单独调用，而是嵌入在 PerformanceTemplateCreate.indicators 列表中，
    随模板一起创建。字段继承自 PerformanceIndicatorBase，无额外字段。
    """
    pass


class PerformanceIndicatorUpdate(BaseModel):
    """
    更新单个绩效指标的请求体（PATCH 语义，所有字段可选）。

    用于修改已有模板中的某一指标配置，
    例如调整权重分配或修改指标描述，不影响其他指标。
    """
    name: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    category: Optional[str] = Field(default=None, pattern=r"^(业绩|能力|态度|安全)$")
    weight: Optional[Decimal] = Field(default=None, gt=0, le=100)
    max_score: Optional[Decimal] = Field(default=None, gt=0)
    sort_order: Optional[int] = Field(default=None, ge=0)


class PerformanceIndicatorOut(PerformanceIndicatorBase):
    """
    返回给前端的单个绩效指标数据（ORM 序列化）。

    在 PerformanceTemplateOut.indicators 列表中返回，
    包含数据库主键 id 和所属模板外键 template_id。
    from_attributes=True 支持直接从 ORM 对象转换。
    """
    model_config = ConfigDict(from_attributes=True)

    id: int           # 指标数据库主键
    template_id: int  # 所属考核模板的外键 ID


class PerformanceTemplateBase(BaseModel):
    """
    考核模板基础 Schema

    考核模板（PerformanceTemplate）是绩效体系的配置层，定义了：
      - 适用的岗位类型（工厂/技术/管理/通用/试用期）
      - 自评与上级评分的权重比例（默认 3:7）
      - 该模板是否处于激活状态

    一个模板可被多个考核周期（PerformanceCycle）引用，
    一个周期的一次评估（PerformanceEvaluation）会绑定一个模板。

    字段说明:
        name:                 模板名称，例如 "工厂操作工季度考核"
        description:          模板用途描述
        position_type:        适用岗位类型（正则枚举：工厂/技术/管理/通用/试用期）
        self_eval_weight:     员工自评在最终加权分中的占比，0~1之间，默认0.30（30%）
        superior_eval_weight: 上级评分在最终加权分中的占比，0~1之间，默认0.70（70%）
                              注意：self_eval_weight + superior_eval_weight 应 = 1.0
        is_active:            是否启用该模板，停用后不能被新周期选择
    """
    name: str = Field(..., max_length=100, description="模板名称")
    description: Optional[str] = None
    position_type: str = Field(
        ...,
        pattern=r"^(工厂|技术|管理|通用|试用期)$",
        description="适用岗位类型",
    )
    self_eval_weight: Decimal = Field(
        default=Decimal("0.30"), ge=0, le=1, description="自评权重"
    )  # 自评权重，默认30%
    superior_eval_weight: Decimal = Field(
        default=Decimal("0.70"), ge=0, le=1, description="上级评分权重"
    )  # 上级评分权重，默认70%，两者之和应为1.0
    is_active: bool = True


class PerformanceTemplateCreate(PerformanceTemplateBase):
    """
    创建考核模板的请求体。

    对应 API: POST /performance/templates

    在创建模板时，同时传入 indicators（指标列表），
    service 层会将模板和指标一起写入数据库（事务处理）。
    indicators 为空列表时，创建一个无指标的空模板（不推荐）。
    """
    indicators: list[PerformanceIndicatorCreate] = Field(
        default_factory=list, description="考核指标列表"
    )  # 与模板一同创建的指标，通常至少包含一个指标


class PerformanceTemplateUpdate(BaseModel):
    """
    更新考核模板的请求体（PATCH 语义）。

    对应 API: PUT /performance/templates/{id}

    注意：更新模板元数据（名称、权重等），不涉及指标的增删，
    指标的修改通过专用的指标端点进行。
    """
    name: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    position_type: Optional[str] = Field(
        default=None, pattern=r"^(工厂|技术|管理|通用|试用期)$"
    )
    self_eval_weight: Optional[Decimal] = Field(default=None, ge=0, le=1)
    superior_eval_weight: Optional[Decimal] = Field(default=None, ge=0, le=1)
    is_active: Optional[bool] = None


class PerformanceTemplateOut(PerformanceTemplateBase):
    """
    返回给前端的考核模板完整数据（ORM 序列化）。

    对应 API: GET /performance/templates/{id} 的响应体

    字段说明:
        id:              模板数据库主键
        indicators_json: 指标配置的 JSON 字符串（冗余字段，legacy 兼容）
        indicators:      关联的指标对象列表（主要使用此字段）
        created_at:      创建时间（TIMESTAMPTZ）
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    indicators_json: Optional[str] = None  # 旧版 JSON 格式指标，仅作兼容保留
    indicators: list[PerformanceIndicatorOut] = []  # 通过 ORM relationship 关联加载的指标列表
    created_at: datetime


# ============================================================
# PerformanceCycle 考核周期
# ============================================================

class PerformanceCycleBase(BaseModel):
    """
    考核周期基础 Schema

    考核周期（PerformanceCycle）是绩效评估的时间容器，定义了：
      - 周期的时间范围（start_date ~ end_date）
      - 周期类型（季度/半年/年度）
      - 强制分布规则（JSON 配置 A/B/C/D 各等级比例上限）

    一个周期可包含多个员工的 PerformanceEvaluation 记录。
    周期状态流转: draft → self_eval → superior_eval → completed
      - draft:        管理员创建，等待员工自评
      - self_eval:    员工填写自评分数
      - superior_eval: 上级填写评分
      - completed:    全部完成，等级分布锁定

    字段说明:
        name:                    周期名称，例如 "2024年Q3季度绩效考核"
        cycle_type:              考核频率，枚举：quarterly（季度）/semi_annual（半年）/annual（年度）
        year:                    所属年份
        quarter:                 季度（仅 cycle_type=quarterly 时填写，1-4）
        start_date/end_date:     考核周期的起止日期
        forced_distribution_json: 强制分布配置 JSON，
                                   格式示例: {"A":10,"B":30,"C":50,"D":10}
                                   表示 A ≤ 10%，B ≤ 30%，C 约 50%，D 约 10%
    """
    name: str = Field(..., max_length=100, description="周期名称")
    cycle_type: str = Field(
        ...,
        pattern=r"^(quarterly|semi_annual|annual)$",
        description="周期类型",
    )  # quarterly=季度考核, semi_annual=半年度, annual=年度
    year: int = Field(..., ge=2020, le=2100)  # 考核所属年份
    quarter: Optional[int] = Field(default=None, ge=1, le=4)  # 季度，仅季度考核有效
    start_date: date   # 考核周期开始日期
    end_date: date     # 考核周期结束日期（包含）
    forced_distribution_json: Optional[str] = Field(
        default=None, description='强制分布, 如: {"A":10,"B":30,"C":50,"D":10}'
    )  # 强制分布配置，值为各等级最高占比百分比，A≤10% B≤30% 为常见设置
    config_json: Optional[dict[str, Any]] = Field(
        default=None,
        description="前端扩展配置（模板、展示类型、阶段节点、参与范围等）",
    )


class PerformanceCycleCreate(PerformanceCycleBase):
    """
    创建考核周期的请求体。

    对应 API: POST /performance/cycles

    创建后周期状态默认为 draft，需要 HR 推进状态才能让员工自评。
    """
    pass


class PerformanceCycleUpdate(BaseModel):
    """
    更新考核周期的请求体（PATCH 语义）。

    注意：cycle_type/year/quarter 等不可修改，
    仅允许更新名称、日期范围和强制分布配置。
    周期状态修改请使用 PerformanceCycleStatusTransition。
    """
    name: Optional[str] = Field(default=None, max_length=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    forced_distribution_json: Optional[str] = None  # 可随时调整等级分布比例
    config_json: Optional[dict[str, Any]] = None


class PerformanceCycleStatusTransition(BaseModel):
    """
    考核周期状态流转请求体。

    对应 API: PATCH /performance/cycles/{id}/status

    周期状态机（单向推进，不可回退）:
      draft → self_eval:      开放员工自评入口
      self_eval → superior_eval: 关闭自评，开放上级评分
      superior_eval → completed:  锁定所有评分，触发等级计算与分布检查

    字段说明:
        target_status: 目标状态，只能是 self_eval / superior_eval / completed 之一
    """
    target_status: str = Field(
        ...,
        pattern=r"^(self_eval|superior_eval|completed)$",
        description="目标状态",
    )  # 推进到的目标状态，不可跳步


class PerformanceCycleOut(PerformanceCycleBase):
    """
    返回给前端的考核周期数据（ORM 序列化）。

    字段说明:
        id:         周期数据库主键
        status:     当前周期状态（draft/self_eval/superior_eval/completed）
        created_at: 创建时间
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str   # 当前状态：draft | self_eval | superior_eval | completed
    created_at: datetime


# ============================================================
# PerformanceEvaluation 绩效评估
# ============================================================

class PerformanceEvaluationCreate(BaseModel):
    """
    创建绩效评估记录的请求体。

    对应 API: POST /performance/evaluations

    一条评估记录对应：一名员工 × 一个考核周期 × 一个考核模板。
    通常由 HR 在周期启动时批量创建，不需要员工手动提交。

    字段说明:
        employee_id:  被考核员工的 ID
        cycle_id:     所属考核周期 ID
        template_id:  使用的考核模板 ID（为 None 时 service 自动匹配）
        evaluator_id: 上级评分人 ID；对驻场外包员工，通常为项目经理
    """
    employee_id: int
    cycle_id: int
    template_id: Optional[int] = None       # 为 None 时由 service 根据员工岗位类型自动选择模板
    evaluator_id: Optional[int] = Field(
        default=None, description="评估人ID(驻场人员为项目经理)"
    )  # 上级评分人，通常为直属主管或项目经理（驻场外包场景）


class SelfEvalSubmit(BaseModel):
    """
    员工自评提交请求体。

    对应 API: POST /performance/evaluations/{id}/self-eval
    触发条件：考核周期状态为 self_eval

    字段说明:
        self_scores_json: 各指标的自评分数，以 JSON 字符串格式传递。
                          key 为指标 ID（字符串形式），value 为该指标得分（数字）。
                          示例: '{"1": 85, "2": 90, "3": 78}'
                          表示指标1得85分，指标2得90分，指标3得78分。
                          service 层会根据模板中各指标的 weight 计算加权自评总分。
    """
    self_scores_json: str = Field(
        ..., description='自评分数JSON, 如: {"1": 85, "2": 90}'
    )  # 格式：{"指标ID": 分数}，指标ID为字符串，分数为整数或浮点数


class SuperiorScoreSubmit(BaseModel):
    """
    上级评分提交请求体。

    对应 API: POST /performance/evaluations/{id}/superior-score
    触发条件：考核周期状态为 superior_eval，且当前登录用户为该员工的 evaluator_id

    字段说明:
        superior_scores_json: 上级对各指标的评分 JSON，格式同 self_scores_json。
                              示例: '{"1": 80, "2": 88}'
                              service 层提交后将计算最终加权分:
                              weighted_score = self × 0.30 + superior × 0.70
                              并根据分数区间自动判定等级 A/B/C/D。
    """
    superior_scores_json: str = Field(
        ..., description='上级评分JSON, 如: {"1": 80, "2": 88}'
    )  # 提交后 service 层触发 weighted_score 计算与等级判定


class InterviewRecordSubmit(BaseModel):
    """
    绩效面谈记录提交请求体。

    对应 API: POST /performance/evaluations/{id}/interview
    触发条件：评估状态为上级评分完成后，HR 或上级进行面谈

    面谈是绩效流程的收尾环节，记录面谈内容与改进计划。
    面谈完成后，该条评估记录状态更新为 interview_done。

    字段说明:
        interview_record: 面谈内容文字记录（1-5000字）
        interview_date:   面谈实际发生日期
    """
    interview_record: str = Field(..., min_length=1, max_length=5000)  # 面谈内容，最少1字
    interview_date: date   # 面谈日期


class PerformanceEvaluationOut(BaseModel):
    """
    返回给前端的绩效评估完整数据（ORM 序列化）。

    对应 API: GET /performance/evaluations/{id}

    字段说明:
        id:                       评估记录主键
        employee_id:              被考核员工 ID
        cycle_id:                 所属考核周期 ID
        template_id:              使用的模板 ID
        evaluator_id:             上级评分人 ID
        self_scores_json:         员工自评各指标分数（JSON 字符串）
        superior_scores_json:     上级评分各指标分数（JSON 字符串）
        weighted_score:           最终加权分 = 自评×0.3 + 上级×0.7
        grade:                    绩效等级 A/B/C/D（由 service 根据分数区间判定）
        performance_coefficient:  绩效系数（用于薪资计算，A=1.2, B=1.0, C=0.8, D=0.6）
        interview_record:         面谈记录文字内容
        interview_date:           面谈日期
        status:                   评估状态（pending/self_eval/superior_eval/interview_done）
        created_at/updated_at:    创建/更新时间
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    cycle_id: int
    template_id: Optional[int]
    evaluator_id: Optional[int]
    self_scores_json: Optional[str]            # 自评分数 JSON，提交后才有值
    superior_scores_json: Optional[str]        # 上级评分 JSON，提交后才有值
    weighted_score: Optional[Decimal]          # 加权总分 = 自评×weight + 上级×weight
    grade: Optional[str]                       # 等级：A（优秀）/ B（良好）/ C（合格）/ D（待改进）
    performance_coefficient: Optional[Decimal] # 绩效系数，影响薪资计算（如 A=1.2）
    interview_record: Optional[str]
    interview_date: Optional[date]
    status: str
    created_at: datetime
    updated_at: datetime


class PerformanceEvaluationDetail(PerformanceEvaluationOut):
    """
    绩效评估详情（含关联名称信息，供前端展示）。

    在 PerformanceEvaluationOut 基础上补充了 JOIN 查询得到的名称信息，
    由 service 层查询后注入，避免前端再次发起多次 API 请求。

    字段说明:
        employee_name:  被考核员工姓名
        evaluator_name: 上级评分人姓名
        cycle_name:     考核周期名称
        template_name:  使用的模板名称
    """
    employee_name: Optional[str] = None    # 从 Employee 表 JOIN 得到
    evaluator_name: Optional[str] = None   # 从 Employee 表 JOIN 得到（evaluator_id 对应）
    cycle_name: Optional[str] = None       # 从 PerformanceCycle 表 JOIN 得到
    template_name: Optional[str] = None   # 从 PerformanceTemplate 表 JOIN 得到


# ============================================================
# 360 Feedback
# ============================================================

class PerformanceFeedback360Base(BaseModel):
    target_employee_id: int
    work_ability: int = Field(..., ge=1, le=10)
    collaboration: int = Field(..., ge=1, le=10)
    initiative: int = Field(..., ge=1, le=10)
    attitude: int = Field(..., ge=1, le=10)
    strengths: Optional[str] = Field(default=None, max_length=2000)
    improvements: Optional[str] = Field(default=None, max_length=2000)


class PerformanceFeedback360Create(PerformanceFeedback360Base):
    pass


class PerformanceFeedback360Out(PerformanceFeedback360Base):
    model_config = ConfigDict(from_attributes=True)

    id: int
    giver_employee_id: int
    average_score: Decimal
    created_at: datetime
    giver_employee_name: Optional[str] = None
    target_employee_name: Optional[str] = None


# ============================================================
# Grade Distribution 等级分布
# ============================================================

class GradeDistribution(BaseModel):
    """
    某考核周期的绩效等级分布统计。

    对应 API: GET /performance/cycles/{id}/distribution

    记录一个考核周期内所有员工的等级分布情况（人数与百分比），
    用于 HR 判断是否满足强制分布要求（A≤10%，B≤30%）。

    字段说明:
        cycle_id/cycle_name:      所属周期的 ID 和名称
        grade_a/b/c/d_count:      各等级人数
        total_count:              本周期参与考核的总人数
        grade_a/b/c/d_pct:        各等级实际占比（%），service 层计算
    """
    cycle_id: int
    cycle_name: str
    grade_a_count: int = 0    # 等级 A（优秀）人数
    grade_b_count: int = 0    # 等级 B（良好）人数
    grade_c_count: int = 0    # 等级 C（合格）人数
    grade_d_count: int = 0    # 等级 D（待改进）人数
    total_count: int = 0      # 总参评人数
    grade_a_pct: Decimal = Decimal("0")  # A 等级占比 %（= grade_a_count / total_count × 100）
    grade_b_pct: Decimal = Decimal("0")  # B 等级占比 %
    grade_c_pct: Decimal = Decimal("0")  # C 等级占比 %
    grade_d_pct: Decimal = Decimal("0")  # D 等级占比 %


class ForcedDistributionCheck(BaseModel):
    """
    强制分布合规性校验结果。

    对应 API: GET /performance/cycles/{id}/distribution/check

    检查当前等级分布是否满足 PerformanceCycle.forced_distribution_json 中配置的限制。
    例如: 若 A ≤ 10% 配置，但实际 A 等级占 15%，则 is_compliant=False，
          violations 中记录 "A等级占比15.0%，超过限制10%"。

    字段说明:
        cycle_id:             被检查的考核周期 ID
        is_compliant:         是否合规（True = 所有等级均在限制内）
        violations:           违反限制的说明列表（每条一个违规等级的描述）
        current_distribution: 当前实际分布统计（GradeDistribution 对象）
    """
    cycle_id: int
    is_compliant: bool                                  # 是否满足强制分布要求
    violations: list[str] = Field(default_factory=list, description="违反的等级及百分比")  # 超标说明
    current_distribution: GradeDistribution             # 当前实际分布数据


# ============================================================
# PerformanceGoal KPI/OKR 目标设定
# ============================================================

class GoalCreate(BaseModel):
    """
    创建员工绩效目标的请求体（支持 KPI 和 OKR 两种模式）。

    对应 API: POST /performance/goals

    KPI 模式: 单层目标，每个目标有明确的量化指标（target_value）和权重
    OKR 模式: 层级目标，Objective（父目标）下挂 Key Results（子目标），
              通过 parent_id 建立层级关系

    字段说明:
        employee_id:  目标所属员工 ID
        cycle_id:     关联的考核周期 ID（可选，不关联周期则为长期目标）
        goal_type:    目标类型，枚举：kpi / okr
        title:        目标标题，例如 "Q3生产良品率达到99.5%"
        description:  详细说明
        weight:       该目标在所有目标中的权重百分比（0-100）
        target_value: 量化目标值，例如 "99.5%"、"120件/天"（字符串格式）
        due_date:     目标截止日期
        parent_id:    父目标 ID（OKR 模式下，Key Result 填此字段指向 Objective）
    """
    employee_id: int
    cycle_id: Optional[int] = None     # 关联周期，长期目标可为空
    goal_type: str = Field(default="kpi", pattern=r"^(kpi|okr)$", description="目标类型")
    title: str = Field(..., max_length=200, description="目标标题")
    description: Optional[str] = None
    weight: float = Field(default=1.0, ge=0, le=100, description="权重(百分比)")  # OKR场景通常不填权重
    target_value: Optional[str] = Field(default=None, max_length=200, description="目标值")  # 量化目标
    due_date: Optional[date] = None
    parent_id: Optional[int] = Field(default=None, description="父目标ID(OKR模式)")  # 建立OKR层级关系


class GoalUpdate(BaseModel):
    """
    更新员工绩效目标的请求体（PATCH 语义）。

    对应 API: PUT /performance/goals/{id}

    字段说明:
        actual_value: 实际完成值（目标执行后填写），例如 "99.8%"
        score:        目标得分（0-100，由上级评定）
        status:       目标状态，枚举: draft / active / completed / cancelled
    """
    title: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    weight: Optional[float] = Field(default=None, ge=0, le=100)
    target_value: Optional[str] = Field(default=None, max_length=200)
    actual_value: Optional[str] = Field(default=None, max_length=200)  # 实际完成结果
    score: Optional[float] = Field(default=None, ge=0, le=100)         # 目标得分
    status: Optional[str] = Field(default=None, pattern=r"^(draft|active|completed|cancelled)$")
    due_date: Optional[date] = None


class GoalOut(BaseModel):
    """
    返回给前端的员工目标数据（ORM 序列化，支持树形结构）。

    对应 API: GET /performance/goals/{id}

    支持自引用嵌套：sub_goals 字段包含子目标列表（OKR 的 Key Results），
    使用 GoalOut.model_rebuild() 解决前向引用问题。

    字段说明:
        actual_value:  实际完成值（执行后填写）
        score:         目标评分
        status:        draft（草稿）/ active（执行中）/ completed（完成）/ cancelled（取消）
        employee_name: 目标所属员工姓名（service 层 JOIN 注入）
        sub_goals:     子目标列表（OKR 模式的 Key Results），递归结构
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    cycle_id: Optional[int]
    goal_type: str              # kpi 或 okr
    title: str
    description: Optional[str]
    weight: float
    target_value: Optional[str]
    actual_value: Optional[str]  # 实际完成值，由员工或上级填写
    score: Optional[float]       # 该目标的评分（0-100）
    status: str                  # draft | active | completed | cancelled
    due_date: Optional[date]
    parent_id: Optional[int]     # 父目标 ID（OKR 层级结构）
    created_at: datetime
    updated_at: datetime
    employee_name: Optional[str] = None  # 关联员工姓名，service 层注入
    sub_goals: List["GoalOut"] = []      # 子目标（OKR 的 Key Results，递归嵌套）


# 解决 GoalOut 中 sub_goals: List["GoalOut"] 的前向自引用，Pydantic v2 需要显式 rebuild
GoalOut.model_rebuild()


class GoalSummaryItem(BaseModel):
    """
    单个员工的目标完成度摘要。

    作为 GoalSummary.items 列表的元素，
    用于前端展示团队整体目标进度概览。

    字段说明:
        total_goals:     该员工本周期的目标总数
        completed_goals: 已完成（status=completed）的目标数
        completion_rate: 完成率 = completed_goals / total_goals × 100
        weighted_score:  各目标加权平均分（按 weight 加权，service 层计算）
    """
    employee_id: int
    employee_name: str
    total_goals: int
    completed_goals: int
    completion_rate: float      # 完成率（0-100）
    weighted_score: Optional[float]  # 加权得分，未评分时为 None


class GoalSummary(BaseModel):
    """
    考核周期内所有员工的目标汇总。

    对应 API: GET /performance/goals/{cycle_id}/summary

    字段说明:
        cycle_id: 所属考核周期 ID
        items:    每个员工的目标完成度摘要列表
    """
    cycle_id: int
    items: List[GoalSummaryItem]


# ============================================================
# Force Distribution 强制分布计算
# ============================================================

class DistributionConfig(BaseModel):
    """
    强制分布计算配置（用于手动触发分布计算的请求体）。

    对应 API: POST /performance/cycles/{id}/distribution/calculate

    允许 HR 临时指定各等级的目标比例，让 service 层计算出
    在当前人员规模下，各等级应分配的名额，并给出调整建议。

    注意：这是计算辅助配置，不会修改 PerformanceCycle.forced_distribution_json。
    要修改强制分布规则，请使用 PerformanceCycleUpdate。

    字段说明:
        excellent_pct: 优秀（A）目标比例，默认 20%
        good_pct:      良好（B）目标比例，默认 30%
        average_pct:   一般（C）目标比例，默认 40%
        poor_pct:      待改进（D）目标比例，默认 10%
    """
    excellent_pct: float = Field(default=20.0, ge=0, le=100, description="优秀比例 %")  # A 等级目标占比
    good_pct: float = Field(default=30.0, ge=0, le=100, description="良好比例 %")       # B 等级目标占比
    average_pct: float = Field(default=40.0, ge=0, le=100, description="一般比例 %")    # C 等级目标占比
    poor_pct: float = Field(default=10.0, ge=0, le=100, description="待改进比例 %")     # D 等级目标占比


class ForceDistributionResult(BaseModel):
    """
    强制分布计算结果（响应体）。

    对应 API: POST /performance/cycles/{id}/distribution/calculate 的响应

    字段说明:
        cycle_id:               所属考核周期 ID
        total:                  参评总人数
        distribution:           计算结果，按等级（A/B/C/D）分组，
                                 每组是一个包含员工信息的 dict 列表，
                                 格式: {"A": [{"employee_id": 1, "name": "张三", "score": 95}, ...]}
        adjustment_suggestions: 调整建议列表，当当前分布不符合配置时，
                                 给出需要升降级的员工建议，
                                 格式: [{"employee_id": 2, "suggestion": "建议从B调整至C", ...}]
    """
    cycle_id: int
    total: int                              # 参评总人数
    distribution: Dict[str, List[dict]]     # 按等级分组的员工列表
    adjustment_suggestions: List[dict]      # 调整建议（等级临界员工的升降级建议）
