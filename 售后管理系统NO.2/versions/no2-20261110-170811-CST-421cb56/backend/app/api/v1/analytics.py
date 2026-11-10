"""
HR 分析中心模块 API 路由 — Analytics
========================================

本模块负责售后管理系统中「HR 分析中心」相关的所有 HTTP 接口，挂载前缀为
``/api/v1/analytics``（由 router.py 注册时指定）。

功能定位
--------
Analytics 是**人才管理数据的综合分析层**，与 Dashboard 的区别：
- Dashboard ：运营型指标，关注实时状态（今日出勤率、当前待审批数等）
- Analytics ：分析型指标，关注趋势洞察（胜任力热力图、九宫格分布、离职风险等）

Analytics 的数据主要来自「人才发展」「人才盘点」「胜任力」「培训」等模块，
是这些模块数据的二次加工与聚合。

功能分区
--------
1. **人才总览（TalentOverview）**
   - GET /analytics/talent-overview
   - 汇总关键人才指标：总员工数、平均司龄、关键人才数、胜任力匹配率、培训完成率

2. **胜任力热力图（CompetencyHeatmap）**
   - GET /analytics/competency-heatmap
   - 按「部门 × 胜任力维度」展示平均等级及与岗位要求的差距
   - 用于前端 ECharts 热力图（HeatmapChart）渲染

3. **培训投资回报（TrainingROI）**
   - GET /analytics/training-roi
   - 统计培训总投入、总课时、平均成绩、完成率等指标
   - 帮助 HR 评估培训项目的效果与性价比

4. **九宫格分布（NineBoxDistribution）**
   - GET /analytics/nine-box-distribution
   - 按盘点会话统计人才在九宫格中的分布（3×3，9个格子各有多少人）
   - 可选 session_id，不传则统计全部盘点数据的汇总分布

5. **离职风险看板（FlightRiskDashboard）**
   - GET /analytics/flight-risk-dashboard
   - 列出当前标记为「中等」或「高」离职风险的员工
   - 帮助 HR 优先关注和干预高流失风险人才

6. **继任覆盖率（SuccessionCoverage）**
   - GET /analytics/succession-coverage
   - 统计已有继任计划覆盖的关键岗位比例及候选人就绪度
   - 用于评估组织关键岗位的风险应对能力

权限说明
--------
- 所有接口均需登录（``Depends(get_current_user)``），无 JWT 返回 401。
- 分析数据属于敏感信息，建议仅 HR 管理员 / 公司高层访问，当前未做角色细分。

服务层对应
----------
- ``app.services.analytics.AnalyticsService``（全部为静态方法，无需实例化）
  - talent_overview(db)
  - competency_heatmap(db)
  - training_roi(db)
  - nine_box_distribution(db, session_id)
  - flight_risk_dashboard(db)
  - succession_coverage(db)
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.schemas.analytics import (
    TalentOverview,
    CompetencyHeatmapRow,
    TrainingROI,
    NineBoxDistribution,
    FlightRiskItem,
    SuccessionCoverage,
)
from app.services.analytics import AnalyticsService

# 路由器标签用于 Swagger UI 分组展示
router = APIRouter(tags=["HR分析中心"])


# ============================================================
# GET /analytics/talent-overview 人才总览
# ============================================================

@router.get("/talent-overview", response_model=TalentOverview)
async def get_talent_overview(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
) -> TalentOverview:
    """
    GET /analytics/talent-overview — 人才总览指标

    用途
    ----
    提供人才管理模块的全局概览指标，是「HR 分析中心」首页的汇总卡片数据。
    汇集来自员工、胜任力、培训多个模块的关键数字。

    返回指标说明
    -----------
    - total_employees        : int   — 当前在职员工总数
    - avg_tenure_months      : float — 全员平均司龄（月）
    - key_talent_count       : int   — 被标记为关键人才的员工数
    - key_talent_ratio       : float — 关键人才占比（%）
    - competency_match_rate  : float — 胜任力达标率，即实际等级≥岗位要求等级的员工占比（%）
    - training_completion_rate: float — 当前培训计划完成率（%）

    响应 (200 OK)
    -------------
    TalentOverview — 包含上述所有汇总指标

    权限
    ----
    已登录用户（get_current_user），建议 HR 管理员 / 公司高层查看

    对应服务
    --------
    AnalyticsService.talent_overview(db)
    """
    return await AnalyticsService.talent_overview(db)


# ============================================================
# GET /analytics/competency-heatmap 胜任力热力图
# ============================================================

@router.get("/competency-heatmap", response_model=list[CompetencyHeatmapRow])
async def get_competency_heatmap(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
) -> list[CompetencyHeatmapRow]:
    """
    GET /analytics/competency-heatmap — 胜任力热力图

    用途
    ----
    按「部门 × 胜任力维度」统计各部门在每个胜任力项上的平均评估等级，
    并与岗位要求等级对比，计算差距（gap）。
    用于前端 ECharts 热力图渲染，帮助管理者识别各部门的能力强弱区域。

    读取数据来源
    -----------
    - EmployeeCompetency : 员工胜任力评估记录（评估等级）
    - CompetencyItem     : 胜任力维度定义（维度名称、岗位要求等级）
    - Department         : 部门信息

    响应 (200 OK)
    -------------
    list[CompetencyHeatmapRow] — 热力图行数据列表，每条代表一个「部门-胜任力」组合：
      - department_name    : str   — 部门名称（热力图 Y 轴标签）
      - competency_name    : str   — 胜任力维度名称（热力图 X 轴标签）
      - avg_score          : float — 该部门在此胜任力上的平均评估等级（1-5）
      - required_score     : float — 岗位要求等级（1-5）
      - gap                : float — 差距 = avg_score - required_score
                                     负值表示不达标，正值表示超出要求

    颜色映射建议
    -----------
    前端热力图颜色方案：gap < -1 → 红色（严重不足），gap ∈ [-1,0) → 橙色（轻微不足），
    gap ≥ 0 → 绿色（达标或超出）

    权限
    ----
    已登录用户（get_current_user），建议 HR 管理员查看

    对应服务
    --------
    AnalyticsService.competency_heatmap(db)
    """
    return await AnalyticsService.competency_heatmap(db)


# ============================================================
# GET /analytics/training-roi 培训ROI
# ============================================================

@router.get("/training-roi", response_model=TrainingROI)
async def get_training_roi(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
) -> TrainingROI:
    """
    GET /analytics/training-roi — 培训投资回报分析

    用途
    ----
    统计培训投入产出关键指标，帮助 HR 评估培训项目效果与资金利用效率。
    ROI（Return on Investment）衡量培训投入是否转化为员工绩效/技能提升。

    返回指标说明
    -----------
    - total_investment      : float — 培训总费用投入（元）
    - total_hours           : float — 培训总课时（小时）
    - avg_score             : float — 学员平均测评成绩（0-100分）
    - completion_rate       : float — 培训计划整体完成率（%）
    - courses_count         : int   — 统计期内开展的课程总数
    - enrollments_count     : int   — 总报名人次
    - cost_per_hour         : float — 人均每课时培训成本（元/小时）

    响应 (200 OK)
    -------------
    TrainingROI — 包含上述所有培训效益指标

    权限
    ----
    已登录用户（get_current_user），建议 HR 培训专员 / 管理层查看

    对应服务
    --------
    AnalyticsService.training_roi(db)
    """
    return await AnalyticsService.training_roi(db)


# ============================================================
# GET /analytics/nine-box-distribution 九宫格分布
# ============================================================

@router.get("/nine-box-distribution", response_model=NineBoxDistribution)
async def get_nine_box_distribution(
    session_id: Optional[int] = Query(
        default=None, description="盘点会话ID(不传则统计全部)"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
) -> NineBoxDistribution:
    """
    GET /analytics/nine-box-distribution — 九宫格人才分布统计

    用途
    ----
    统计人才在「九宫格」3×3 矩阵中的分布情况（各格人数与占比），
    用于分析公司整体或某次盘点的人才结构。

    与 /talent-review/nine-box/{session_id} 的区别
    ------------------------------------------------
    - /talent-review/nine-box/{session_id} : 盘点视角，返回单次盘点的详细格子数据
      （含每格员工名单），属于 talent_review 模块
    - /analytics/nine-box-distribution     : 分析视角，聚合统计各格人数和占比，
      可跨多次盘点汇总，不返回员工名单，属于 analytics 模块

    查询参数
    --------
    - session_id : int (可选) — 指定盘点会话 ID（只统计该次盘点）；
                                不传则统计所有盘点数据的汇总分布

    响应 (200 OK)
    -------------
    NineBoxDistribution — 九宫格分布统计，包含：
      - boxes    : list[dict] — 9个格子的数据
        - position : str   — 格子位置标识（如"3-3"表示高绩效+高潜力）
        - label    : str   — 人才分类标签（如"明日之星"）
        - count    : int   — 该格人数
        - ratio    : float — 占总人数的百分比（%）
      - total    : int        — 参与统计的总人数

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    AnalyticsService.nine_box_distribution(db, session_id)
    """
    return await AnalyticsService.nine_box_distribution(db, session_id)


# ============================================================
# GET /analytics/flight-risk-dashboard 离职风险看板
# ============================================================

@router.get("/flight-risk-dashboard", response_model=list[FlightRiskItem])
async def get_flight_risk_dashboard(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
) -> list[FlightRiskItem]:
    """
    GET /analytics/flight-risk-dashboard — 离职风险看板

    用途
    ----
    列出当前标记为「中等」（medium）或「高」（high / very_high）离职风险的员工，
    帮助 HR 提前介入，优先关注和挽留关键人才，降低非预期流失。
    离职风险来源于人才盘点（TalentReviewEntry.flight_risk）字段。

    返回范围
    --------
    只返回 flight_risk IN ('medium', 'high', 'very_high') 的员工，
    按风险等级从高到低排序，同等风险按最近一次盘点时间排序（最新的在前）。

    响应 (200 OK)
    -------------
    list[FlightRiskItem] — 风险员工列表，每条包含：
      - employee_id     : int   — 员工 ID
      - name            : str   — 员工姓名
      - position        : str   — 当前岗位
      - department_name : str   — 所在部门
      - flight_risk     : str   — 风险等级（medium / high / very_high）
      - risk_factors    : str   — 风险因素说明（来自盘点备注，可选）
      - last_review_date: date  — 最近一次盘点日期

    权限
    ----
    已登录用户（get_current_user），建议仅 HR 管理员 / 直接管理者查看
    （离职风险属于敏感人事信息）

    对应服务
    --------
    AnalyticsService.flight_risk_dashboard(db)
    """
    return await AnalyticsService.flight_risk_dashboard(db)


# ============================================================
# GET /analytics/succession-coverage 继任覆盖率
# ============================================================

@router.get("/succession-coverage", response_model=SuccessionCoverage)
async def get_succession_coverage(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
) -> SuccessionCoverage:
    """
    GET /analytics/succession-coverage — 继任覆盖率分析

    用途
    ----
    统计关键岗位的继任计划覆盖情况，评估组织对关键人才流失的应对能力。
    覆盖率 = 已有继任计划的关键岗位数 / 关键岗位总数。

    返回指标说明
    -----------
    - total_critical_positions : int   — 定义的关键岗位总数（criticality = critical/high）
    - covered_positions        : int   — 已有至少一名候选人的岗位数
    - coverage_rate            : float — 整体覆盖率（%）
    - ready_now_count          : int   — 「即刻可接替（ready_now）」候选人数
    - ready_1_2yr_count        : int   — 「1-2年内可接替」候选人数
    - ready_3_5yr_count        : int   — 「3-5年内可接替」候选人数
    - uncovered_positions      : list  — 尚无继任计划的关键岗位名称列表

    响应 (200 OK)
    -------------
    SuccessionCoverage — 包含上述所有继任覆盖率指标

    权限
    ----
    已登录用户（get_current_user），建议 HR 管理员 / 公司高层查看

    对应服务
    --------
    AnalyticsService.succession_coverage(db)
    """
    return await AnalyticsService.succession_coverage(db)
