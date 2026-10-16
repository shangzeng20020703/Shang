"""
模块名称: talent_profile.py — 人才画像 Pydantic Schemas

作用:
    为人才画像模块提供响应序列化 DTO。
    聚合员工多个维度的数据，形成360度"人才画像"：
        绩效历史 + 胜任力雷达 + 培训概况 + IDP发展计划 + 证书 + 技能 + 盘点结论

业务背景:
    本公司的人才画像是 HR 和管理层进行人才决策的核心工具，整合了：
        - 绩效（performance）：近4次考核周期的等级和得分
        - 胜任力（competency）：雷达图数据（评估值 vs 岗位要求值）
        - 培训（training）：参与课程数、完成率、平均成绩
        - IDP（个人发展计划）：计划数、活跃目标、目标完成率
        - 证书（certification）：当前持有的有效证书列表
        - 技能（skill）：系统评定的技能标签（及可选的自填技能）
        - 盘点（review）：最近一次盘点的九宫格位置、关键人才标记、离职风险
        - 时间线（timeline）：入职→培训→晋升→认证的成长历程

重要区分（两类技能来源）:
    - EmployeeSkill（talent.py）：HR/上级在后台录入，系统认证，有标准分类和熟练度
    - EmployeePersonalSkill（本文件 SkillItem 的前身）：员工自填，未经验证，
      在人才画像聚合时两者均会呈现
    - TalentProfile.skills 当前聚合的是 EmployeeSkill（系统评定），
      自填技能可后续在此扩展

人才搜索（/talent-profile/search）:
    TalentSearchResult 是搜索结果的摘要，仅返回基础信息和匹配维度，
    点击后再调用单员工画像 API 获取完整 TalentProfile。

数据实体说明:
    TalentProfileSnapshot（画像快照）：
        定期将 TalentProfile 序列化后存储快照，用于历史对比和趋势分析。
        profile_data_json：完整画像的 JSON 序列化字符串。
    CompetencyRadarItem（雷达图数据项）：
        每个胜任力项的评估等级 vs 岗位要求等级，供前端绘制雷达图。
    TimelineEvent（成长时间线事件）：
        记录员工职业生涯关键节点，event_type 枚举：
            hire / training / performance / certification / promotion
    TalentProfile（完整画像）：
        by Service 层从多个模块汇聚数据后组装，不直接映射 ORM 对象。
    TalentSearchResult（搜索结果摘要）：
        match_fields 列出匹配的维度标识（如 ["skill:Python", "cert:PMP"]），
        供前端高亮显示匹配项。

依赖关系:
    - services/talent_profile.py: 画像聚合逻辑（跨模块查询）
    - api/v1/endpoints/talent_profile.py: 路由层调用
    - schemas/competency.py: 胜任力数据来源
    - schemas/training.py: 培训数据来源
    - schemas/talent.py: IDP / 证书 / 技能数据来源
    - schemas/talent_review.py: 盘点数据来源

被以下 API 端点使用:
    GET  /api/v1/talent-profile/{employee_id}      → TalentProfile（完整画像）
    GET  /api/v1/talent-profile/search             → list[TalentSearchResult]（人才搜索）
    GET  /api/v1/talent-profile/{employee_id}/snapshot → TalentProfileSnapshot（快照）

数据流:
    Service 层并发查询多个模块 → 组装 TalentProfile → 直接返回（无 from_attributes，纯聚合）
    TalentProfileSnapshot 存储时：TalentProfile.model_dump_json() → profile_data_json
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


# ============================================================
# TalentProfileSnapshot 人才画像快照
# ============================================================

class TalentProfileSnapshot(BaseModel):
    """
    人才画像历史快照（存储在数据库中，用于纵向对比）。
    系统定期（如每月1日）将员工最新画像序列化后存储为快照，
    支持查看员工某时间点的画像状态，分析成长趋势。
    profile_data_json：完整 TalentProfile 的 JSON 字符串，
        前端可反序列化后展示历史画像。
    overall_score：综合评分（0~100），由 Service 层根据绩效/胜任力/培训等加权计算。
    talent_tags：人才标签 JSON 数组字符串，如 '["核心人才","高潜力","技术专家"]'。
    对应 API: GET /api/v1/talent-profile/{employee_id}/snapshot
    """
    model_config = ConfigDict(from_attributes=True)

    employee_id: int = Field(..., description="员工ID")
    profile_data_json: str = Field(..., description="完整画像数据JSON")  # 序列化的 TalentProfile JSON 字符串
    overall_score: float = Field(default=0, description="综合评分")  # 0~100，多维度加权综合得分
    talent_tags: str = Field(default="[]", description="人才标签JSON")  # JSON数组，如 '["明星员工","关键人才"]'
    snapshot_date: datetime = Field(..., description="快照日期")  # 快照生成时间


# ============================================================
# CompetencyRadarItem 胜任力雷达项
# ============================================================

class CompetencyRadarItem(BaseModel):
    """
    胜任力雷达图中单个维度的数据点。
    供前端绘制雷达图（spider chart）使用：
        - assessed_level（蓝线）：员工实际评估等级
        - required_level（红线）：岗位要求等级
    两线差距即为胜任力缺口（gap），视觉化后一目了然。
    等级值：0=未评估 / 1=了解 / 2=初级 / 3=中级 / 4=高级 / 5=专家
    assessed_level=0 表示该能力项尚未进行评估，前端可特殊标注。
    包含在 TalentProfile.competency_radar 列表中返回。
    """
    competency_name: str = Field(..., description="胜任力名称")   # 雷达图轴标签
    category: str = Field(..., description="分类")                # 如"专业技能"、"核心能力"、"领导力"
    assessed_level: int = Field(default=0, ge=0, le=5, description="评估等级")  # 员工当前等级（0=未评估）
    required_level: int = Field(default=0, ge=0, le=5, description="要求等级")  # 岗位要求等级（0=未设要求）


# ============================================================
# TimelineEvent 成长时间线事件
# ============================================================

class TimelineEvent(BaseModel):
    """
    员工职业生涯成长时间线中的单个事件。
    按时间顺序展示员工从入职至今的关键节点，供画像页面渲染时间轴组件。
    event_type 含义：
        - hire：入职（每位员工只有一条）
        - training：重要培训课程完成
        - performance：绩效考核结果（如"2024年度：A级"）
        - certification：获得重要资质证书
        - promotion：晋升（岗位/等级变更）
    event_date 格式：YYYY-MM-DD（字符串，非 date 对象，便于 JSON 序列化）
    description：事件的补充说明（如培训成绩、晋升原因等）。
    包含在 TalentProfile.timeline 列表中，按 event_date 升序排列。
    """
    event_type: str = Field(..., description="事件类型: hire/training/performance/certification/promotion")
    event_date: str = Field(..., description="事件日期 YYYY-MM-DD")  # 字符串格式，便于前端直接展示
    title: str = Field(..., description="事件标题")         # 如"完成《工业机器人安全规范》培训"
    description: str = Field(default="", description="事件描述")  # 补充说明，空字符串=无补充


# ============================================================
# TalentProfile 人才画像完整数据（子模块）
# ============================================================

class PerformanceSummaryItem(BaseModel):
    """
    单个绩效考核周期的摘要数据（用于画像中的绩效历史栏）。
    TalentProfile.performance_summary 最多返回最近4个考核周期，
    按时间倒序排列（最新在前）。
    grade：绩效等级（如 A/B/C/D 或 优秀/良好/合格/待改进），None=未定等级。
    score：该周期的加权总分，None=暂未有综合评分。
    包含在 TalentProfile.performance_summary 列表中。
    """
    cycle_name: str = Field(..., description="考核周期名称")  # 如"2025年度绩效考核"、"2024年Q4季度考核"
    grade: Optional[str] = Field(default=None, description="绩效等级")  # 如 "A+"、"良好"
    score: Optional[float] = Field(default=None, description="加权总分")  # 0~100 的综合加权分


class TrainingSummary(BaseModel):
    """
    员工培训情况概要（用于画像中的培训模块摘要栏）。
    提供"总课程/已完成/平均成绩"三个核心 KPI，无需展示每门课程详情。
    前端可通过这三个数据渲染培训概况卡片，详情链接跳转至培训模块。
    包含在 TalentProfile.training_summary 中。
    """
    total_courses: int = Field(default=0, description="总课程数")  # 该员工的总报名课程数（含未完成）
    completed: int = Field(default=0, description="已完成数")      # 状态为 attended 的课程数
    avg_score: float = Field(default=0, description="平均成绩")    # 所有有分数报名的平均分（0~100）


class IDPSummary(BaseModel):
    """
    员工个人发展计划（IDP）概要（用于画像中的发展计划摘要栏）。
    提供"计划数/活跃目标/目标完成率"三个核心 KPI。
    completion_rate = 所有 IDP 中 status=completed 的目标数 / 总目标数 * 100
    包含在 TalentProfile.idp_summary 中。
    """
    plan_count: int = Field(default=0, description="IDP计划数")       # 该员工的总 IDP 计划数
    active_goals: int = Field(default=0, description="进行中的目标数") # status=in_progress 的目标数
    completion_rate: float = Field(default=0, description="目标完成率(%)")  # 0~100


class CertificationItem(BaseModel):
    """
    员工证书简要信息（用于画像中的证书列表栏）。
    仅展示名称、到期日和状态，不含编号等详情。
    expiry_date：字符串格式（YYYY-MM-DD），None=永久有效。
    status：valid / expired / revoked（从 EmployeeCertification 原样传递）。
    包含在 TalentProfile.certifications 列表中。
    """
    cert_name: str = Field(..., description="证书名称")                        # 如"特种作业操作证（高处作业）"
    expiry_date: Optional[str] = Field(default=None, description="到期日期")   # None=永久有效，字符串格式
    status: Optional[str] = Field(default=None, description="状态")            # valid / expired / revoked


class SkillItem(BaseModel):
    """
    员工技能简要信息（用于画像中的技能标签栏）。
    聚合自 EmployeeSkill（系统评定技能），展示技能名称和熟练度。
    proficiency_level：1=了解 / 2=初级 / 3=中级 / 4=高级 / 5=专家（None=未评定）
    前端可据此渲染技能标签云（颜色深浅对应熟练度）。
    包含在 TalentProfile.skills 列表中。
    """
    skill_name: str = Field(..., description="技能名称")                              # 如"ROS2"、"Python"
    proficiency_level: Optional[int] = Field(default=None, description="熟练度等级")  # 1~5，None=未评定


class ReviewSummary(BaseModel):
    """
    员工最近一次人才盘点结论摘要（用于画像中的盘点结果栏）。
    从最近一次已完成（completed）的 TalentReviewSession 中提取。
    nine_box_position：如 "3-3"（明星员工）、"1-2"（效能待提升）
        格式 "{performance_rating}-{potential_rating}"
    is_key_talent：True=已被标记为关键人才，影响继任计划候选推荐。
    flight_risk：low / medium / high，HR 重点关注 high 风险员工。
    包含在 TalentProfile.review_summary 中。
    """
    nine_box_position: Optional[str] = Field(default=None, description="九宫格位置")  # 如 "3-3"（明星员工）
    is_key_talent: Optional[bool] = Field(default=None, description="是否关键人才")   # True=关键人才
    flight_risk: Optional[str] = Field(default=None, description="离职风险等级")       # low / medium / high


class TalentProfile(BaseModel):
    """
    员工人才画像完整数据（多模块聚合，非直接 ORM 映射）。
    由 services/talent_profile.py 的 build_profile() 方法跨模块查询后组装：
        1. 查询员工基础信息（Employee 表）→ 姓名/岗位/部门/入职日期/司龄
        2. 查询近4次绩效考核（performance 模块）→ performance_summary
        3. 查询胜任力评估和岗位要求（competency 模块）→ competency_radar
        4. 查询培训报名记录（training 模块）→ training_summary
        5. 查询 IDP 计划和目标（talent 模块）→ idp_summary
        6. 查询有效证书（talent 模块）→ certifications
        7. 查询技能标签（talent 模块）→ skills
        8. 查询最近盘点结果（talent_review 模块）→ review_summary
        9. 构建成长时间线（多模块汇总）→ timeline
       10. 生成人才标签（规则引擎）→ overall_tags
    对应 API: GET /api/v1/talent-profile/{employee_id}
    """
    employee_id: int = Field(..., description="员工ID")
    employee_name: str = Field(..., description="员工姓名")
    position: Optional[str] = Field(default=None, description="岗位")           # 当前岗位名称
    department_name: Optional[str] = Field(default=None, description="部门名称") # 所属部门名称
    hire_date: Optional[str] = Field(default=None, description="入职日期")       # YYYY-MM-DD 字符串
    tenure_years: float = Field(default=0, description="司龄(年)")               # 精确到小数点后1位

    # --- 各维度数据 ---
    performance_summary: list[PerformanceSummaryItem] = Field(
        default_factory=list, description="绩效历史(最近4次)"
    )  # 最近4个考核周期的绩效摘要，按时间倒序
    competency_radar: list[CompetencyRadarItem] = Field(
        default_factory=list, description="胜任力雷达数据"
    )  # 雷达图数据，每项=一个胜任力维度
    training_summary: TrainingSummary = Field(
        default_factory=TrainingSummary, description="培训概况"
    )  # 培训三项 KPI：总课程数/已完成/平均成绩
    idp_summary: IDPSummary = Field(
        default_factory=IDPSummary, description="IDP概况"
    )  # IDP 三项 KPI：计划数/活跃目标/完成率
    certifications: list[CertificationItem] = Field(
        default_factory=list, description="证书列表"
    )  # 员工持有的所有证书（含状态）
    skills: list[SkillItem] = Field(
        default_factory=list, description="技能列表"
    )  # 系统评定的技能标签列表
    review_summary: ReviewSummary = Field(
        default_factory=ReviewSummary, description="人才盘点概况"
    )  # 最近一次盘点的三项结论
    timeline: list[TimelineEvent] = Field(
        default_factory=list, description="成长时间线"
    )  # 职业生涯关键节点，按时间升序排列
    overall_tags: list[str] = Field(
        default_factory=list, description="人才标签"
    )  # 系统自动生成的人才标签，如 ["明星员工", "高潜力", "技术专家", "留存风险"]


# ============================================================
# TalentSearchResult 人才搜索结果
# ============================================================

class TalentSearchResult(BaseModel):
    """
    人才库搜索结果摘要（轻量级，仅含基础信息和匹配维度）。
    对应 API: GET /api/v1/talent-profile/search?skill=Python&cert=PMP&...
    搜索逻辑（在 services/talent_profile.py 中实现）：
        支持按以下维度过滤：技能名称、胜任力等级、证书类型、部门、九宫格位置等
    match_fields 格式：匹配的维度标识字符串列表，
        如 ["skill:Python(熟练)", "cert:PMP", "position:3-3(明星)"]
        供前端在搜索结果列表中高亮显示命中的匹配项。
    """
    employee_id: int = Field(..., description="员工ID")
    employee_name: str = Field(..., description="员工姓名")
    position: Optional[str] = Field(default=None, description="岗位")           # 当前岗位
    department_name: Optional[str] = Field(default=None, description="部门名称") # 所属部门
    match_fields: list[str] = Field(
        default_factory=list, description="匹配的字段列表"
    )  # 命中的搜索维度标识列表，前端用于高亮展示
