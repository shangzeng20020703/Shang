"""
模块名称: analytics.py
模块作用: HR 数据分析中心的 Pydantic Schemas — 人才盘点、胜任力、培训与风险的统计报表

业务背景:
    售后管理系统的分析模块聚焦于人才发展维度的深度洞察，与 dashboard.py
    的"运营概览仪表盘"不同：analytics 更偏向战略性人才管理分析。

    覆盖以下六个核心分析场景:
      1. TalentOverview       — 人才总览：全员基础统计（司龄、关键人才占比、匹配率）
      2. CompetencyHeatmapRow — 胜任力热力图：按部门×胜任力项展示能力缺口
      3. TrainingROI          — 培训投资回报：量化培训投入与成效的关键指标
      4. NineBoxDistribution  — 九宫格分布：绩效×潜力的人才矩阵分布
      5. FlightRiskItem       — 离职风险预测：识别高风险员工并列出风险因素
      6. SuccessionCoverage   — 继任覆盖率：关键岗位是否有合格后备人才

数据流向:
    analytics_service.py（聚合查询多个模型）
        → competency / training / talent_review / talent 等模块 ORM
        → 各 Analytics Schema（纯响应 Schema，无对应 ORM 模型）
        → API 端点返回给前端图表渲染

被哪些端点使用:
    - GET /api/v1/analytics/talent-overview       → TalentOverview
    - GET /api/v1/analytics/competency-heatmap    → list[CompetencyHeatmapRow]
    - GET /api/v1/analytics/training-roi          → TrainingROI
    - GET /api/v1/analytics/nine-box              → NineBoxDistribution
    - GET /api/v1/analytics/flight-risk           → list[FlightRiskItem]
    - GET /api/v1/analytics/succession-coverage   → SuccessionCoverage

设计约束:
    1. 所有 Schema 均为只读响应（无对应 Create/Update Schema），字段值由 Service 计算
    2. 百分比字段单位均为 % (0-100)，而非小数 (0-1)，与 dashboard.py 的比率字段有所不同
    3. 默认值均为 0 或空列表，确保前端图表在无数据时不报错
"""

from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# TalentOverview 人才总览
# ============================================================

class TalentOverview(BaseModel):
    """
    人才总览统计 Schema — 用于 GET /api/v1/analytics/talent-overview 接口返回

    提供全公司人才资产的宏观快照，供高管层和 HR BP 了解整体人才现状。

    字段说明及计算来源:
        total_employees          — 当前在职员工总数；来源: employees 表 status='active'
        avg_tenure               — 平均司龄（年）；来源: employees.hire_date 到今天的平均值
        key_talent_count         — 关键人才数量；来源: talent_profiles 中 is_key_talent=True
        key_talent_pct           — 关键人才占比(%)；= key_talent_count / total_employees × 100
        avg_competency_match_rate — 平均胜任力匹配率(%)；来源: employee_competencies 评估等级
                                    与 competency_items 要求等级的对比均值
        training_completion_rate — 培训完成率(%)；来源: training_enrollments 中 status='completed'
                                   占所有 enrollments 的比例

    注意: 所有百分比字段单位为 %（0-100），非小数
    """
    total_employees: int = Field(default=0, description="总员工数")
    avg_tenure: float = Field(default=0, description="平均司龄(年)")
    key_talent_count: int = Field(default=0, description="关键人才数")
    key_talent_pct: float = Field(default=0, description="关键人才占比(%)")
    avg_competency_match_rate: float = Field(
        default=0, description="平均胜任力匹配率(%)"
    )
    # avg_competency_match_rate: 反映全员胜任力整体水平，低于 70% 时建议启动专项培训
    training_completion_rate: float = Field(
        default=0, description="培训完成率(%)"
    )


# ============================================================
# CompetencyHeatmapRow 胜任力热力图行
# ============================================================

class CompetencyHeatmapRow(BaseModel):
    """
    胜任力热力图单行数据 Schema — 用于 GET /api/v1/analytics/competency-heatmap 接口返回

    前端将多行数据渲染为"部门 × 胜任力"二维热力图，颜色深浅代表差距大小。
    每行代表"某部门在某项胜任力上的平均评估结果"。

    字段说明:
        department_name — 部门名称（热力图纵轴）
        competency_name — 胜任力项名称（热力图横轴），如"技术能力/领导力/沟通能力"
        avg_level       — 该部门员工在此胜任力上的平均实际评估等级（通常 1-5 分制）
        gap             — 平均实际等级与岗位要求等级的差距；负数表示低于要求（能力缺口）

    计算来源:
        avg_level = AVG(employee_competencies.assessed_level) GROUP BY department, competency
        gap       = avg_level - competency_items.required_level（岗位要求）
    """
    department_name: str = Field(..., description="部门名称")
    competency_name: str = Field(..., description="胜任力名称")
    avg_level: float = Field(default=0, description="平均评估等级")
    gap: float = Field(default=0, description="与要求的平均差距")
    # gap 解读: 正数=超出要求（能力富余），负数=低于要求（能力缺口，热力图标红）


# ============================================================
# TrainingROI 培训投资回报
# ============================================================

class TrainingROI(BaseModel):
    """
    培训投资回报统计 Schema — 用于 GET /api/v1/analytics/training-roi 接口返回

    量化培训投入（资金+时间）与产出（成绩提升+完成率）的关系，
    用于评估培训计划的有效性和资源分配合理性。

    字段说明及计算来源:
        total_investment      — 培训总投入（元）；来源: training_courses.cost × enrollments 数量
        total_hours           — 培训总课时（小时）；来源: training_courses.duration × enrollments 数量
        avg_score_improvement — 平均成绩提升分数；来源: enrollment 完成前后测评分数对比均值
                                （若无前测，则为最终考试平均分）
        completion_rate       — 培训完成率(%)；= 已完成 enrollment / 全部 enrollment × 100

    注意: 查询时通常需要 start_date/end_date 参数过滤统计周期
    """
    total_investment: float = Field(default=0, description="总投入(元)")
    total_hours: float = Field(default=0, description="总课时(小时)")
    avg_score_improvement: float = Field(
        default=0, description="平均成绩提升"
    )
    # avg_score_improvement: 正值表示培训有效提升了员工成绩
    completion_rate: float = Field(default=0, description="完成率(%)")


# ============================================================
# NineBoxDistribution 九宫格分布
# ============================================================

class NineBoxCell(BaseModel):
    """
    九宫格单格数据 Schema — NineBoxDistribution 的子项

    九宫格（9-Box Grid）是战略人才管理的核心工具，以"绩效"为横轴、"潜力"为纵轴
    将员工分为9个象限，帮助识别高潜人才和绩效风险。

    字段说明:
        position — 象限位置标签，格式通常为 "绩效_潜力" 或象限编号，例如:
                   "high_high"（明星员工）/ "low_low"（需关注员工）
        count    — 该象限的员工人数
        pct      — 该象限员工占全部参与盘点员工的比例(%)
    """
    position: str = Field(..., description="位置标签")
    # position 约定值: high_high/high_mid/high_low/mid_high/mid_mid/mid_low/low_high/low_mid/low_low
    count: int = Field(default=0, description="人数")
    pct: float = Field(default=0, description="占比(%)")


class NineBoxDistribution(BaseModel):
    """
    九宫格分布统计 Schema — 用于 GET /api/v1/analytics/nine-box 接口返回

    聚合一次人才盘点周期（TalentReviewSession）内所有员工的九宫格分布结果。
    前端渲染为3×3的热力格子图，每格显示人数和占比。

    字段说明:
        period — 本次盘点的周期标识，如 "2025-H2"（2025年下半年）；
                 来源: talent_review_sessions.period 字段
        boxes  — 九个象限的分布数据列表，长度应为 9（若某象限无人则 count=0）

    数据来源: talent_review_results 表中 performance_rating × potential_rating 的交叉统计
    """
    period: str = Field(default="", description="盘点期间")
    # period: 对应 TalentReviewSession.period，如 "2025-H2"、"2026-Q1"
    boxes: list[NineBoxCell] = Field(
        default_factory=list, description="九宫格分布"
    )
    # boxes: 前端遍历此列表，按 position 标签渲染到对应格子


# ============================================================
# FlightRiskItem 离职风险项
# ============================================================

class FlightRiskItem(BaseModel):
    """
    离职风险预测单员工数据 Schema — 用于 GET /api/v1/analytics/flight-risk 接口返回

    基于多维度因素（薪资竞争力、绩效趋势、司龄、近期请假频率等）
    为每位员工生成离职风险等级，帮助 HR 提前介入留才。

    字段说明:
        employee_id     — 员工 ID（用于前端跳转到员工详情页）
        employee_name   — 员工姓名（列表展示）
        position        — 当前岗位名称
        department_name — 所在部门名称
        risk_level      — 离职风险等级: low / medium / high
                          high 表示近期离职概率较高，建议 HR 主动沟通
        risk_factors    — 风险因素列表，说明判断依据，例如:
                          ["连续3个月绩效下滑", "近6个月请假天数>15天", "薪资低于市场20%"]

    注意: 此数据涉及员工隐私，API 应限制只有 hr/admin 角色可访问
    """
    employee_id: int = Field(..., description="员工ID")
    employee_name: str = Field(..., description="员工姓名")
    position: Optional[str] = Field(default=None, description="岗位")
    department_name: Optional[str] = Field(default=None, description="部门名称")
    risk_level: str = Field(..., description="风险等级: low/medium/high")
    # risk_level 枚举: low（低风险）/ medium（中等风险，建议关注）/ high（高风险，建议立即介入）
    risk_factors: list[str] = Field(
        default_factory=list, description="风险因素列表"
    )
    # risk_factors: 前端展示为标签列表或 tooltip，帮助 HR 了解判断依据


# ============================================================
# SuccessionCoverage 继任覆盖率
# ============================================================

class SuccessionCoverage(BaseModel):
    """
    继任覆盖率统计 Schema — 用于 GET /api/v1/analytics/succession-coverage 接口返回

    评估关键岗位（critical positions）是否有合格的后备人才（succession candidates），
    帮助识别人才断层风险，支撑组织韧性管理。

    字段说明及计算来源:
        total_key_positions  — 公司定义的关键岗位总数；
                               来源: career_paths 中标记为关键岗位的记录数
        covered_positions    — 已有至少一位合格继任候选人的关键岗位数；
                               来源: idp_plans 或 talent_review_results 中有继任计划的岗位
        coverage_rate        — 继任覆盖率(%)；= covered_positions / total_key_positions × 100；
                               建议目标: >80%；低于 50% 时应预警
        uncovered_positions  — 尚无继任候选人的关键岗位名称列表；
                               前端展示为需要重点关注的空白岗位

    注意: 此统计通常配合九宫格使用，从九宫格高潜人才中选拔继任候选人
    """
    total_key_positions: int = Field(default=0, description="关键岗位总数")
    covered_positions: int = Field(default=0, description="已覆盖岗位数")
    coverage_rate: float = Field(default=0, description="覆盖率(%)")
    # coverage_rate: 低于50%建议预警；目标值通常设为80%以上
    uncovered_positions: list[str] = Field(
        default_factory=list, description="未覆盖的关键岗位列表"
    )
    # uncovered_positions: 岗位名称字符串列表，前端可渲染为红色标签提醒
