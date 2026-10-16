"""
模块名称: dashboard.py
模块作用: 管理驾驶舱（运营概览仪表盘）的 Pydantic Schemas — 汇聚各模块核心 KPI

业务背景:
    管理驾驶舱是 HR 系统的首页大屏，面向 HR 管理员和高管提供实时运营指标。
    与 analytics.py（战略人才分析）不同，dashboard.py 偏向日常运营监控，
    覆盖以下10个核心视图:

      1. OverviewResponse        — 综合概览：全量 KPI 卡片（总人数/入职/离职/出勤率等）
      2. HeadcountResponse       — 人员分布：按地点/部门/项目的人数分布
      3. HRCostResponse          — HR 成本趋势：薪资+社保+公积金+福利的月度趋势及同环比
      4. AttendanceRateResponse  — 出勤率：整体及按地点/部门的出勤率统计
      5. ProjectManpowerResponse — 项目人力配置：各项目需求人数 vs 实际配置及缺口
      6. TurnoverResponse        — 离职率：整体及按部门离职率 + 月度趋势曲线
      7. RecruitmentFunnelResponse — 招聘漏斗：简历→面试→Offer→入职的各阶段转化率
      8. RecentActivityResponse  — 近期动态：最新入职/离职/审批等事件流
      9. PendingItemsResponse    — 待办事项：当前用户待处理的审批列表
     10. Drill-down Schemas      — 钻取接口：点击图表后展开的明细数据

数据流向:
    各业务模块 Service（attendance/payroll/employee/approval 等）
        → dashboard_service.py（聚合多模块数据）
        → Dashboard Schemas（纯响应 Schema，无对应 ORM 模型）
        → API 端点（GET /api/v1/dashboard/xxx）
        → 前端 Vue 组件渲染 ECharts 图表

被哪些端点使用（均为 GET，无请求体）:
    - GET /api/v1/dashboard/overview             → OverviewResponse
    - GET /api/v1/dashboard/headcount            → HeadcountResponse（支持 DimensionFilter 查询参数）
    - GET /api/v1/dashboard/hr-cost              → HRCostResponse（支持 DimensionFilter 查询参数）
    - GET /api/v1/dashboard/attendance-rate      → AttendanceRateResponse
    - GET /api/v1/dashboard/project-manpower     → ProjectManpowerResponse
    - GET /api/v1/dashboard/turnover             → TurnoverResponse
    - GET /api/v1/dashboard/recruitment-funnel   → RecruitmentFunnelResponse
    - GET /api/v1/dashboard/recent-activity      → RecentActivityResponse
    - GET /api/v1/dashboard/pending-items        → PendingItemsResponse
    - GET /api/v1/dashboard/drill/*              → 各 Drill* Schema（钻取明细）

设计约束:
    1. 所有 Schema 均为只读响应（纯输出），字段值由 Service 层计算后填充
    2. 比率字段单位为小数 0-1（如 attendance_rate=0.95 表示 95%），
       与 analytics.py 中使用 % 单位（0-100）有所不同，注意区分
    3. DimensionFilter 为查询参数 Schema（非请求体），通过 Query() 依赖注入
    4. 所有响应字段均设有默认值，确保无数据时前端不报 KeyError
"""

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Common / shared — 通用基础 Schema
# ---------------------------------------------------------------------------

class DimensionFilter(BaseModel):
    """
    仪表盘通用查询参数 Schema — 用于多个 dashboard 接口的筛选维度

    通过 FastAPI Query 参数传递（非请求体 JSON），支持对仪表盘数据
    按不同维度过滤，以及按时间范围筛选。

    字段说明:
        dimension  — 统计维度，决定数据的分组方式:
                       "region"     — 按地区（对应 locations 表的地区字段）
                       "project"    — 按项目（机器人项目现场）
                       "department" — 按部门（默认维度）
                       None         — 不过滤，返回全量汇总
        start_date — 统计起始日期（含）；None 表示不限起始
        end_date   — 统计截止日期（含）；None 表示不限截止

    使用示例（FastAPI Query 依赖注入）:
        @router.get("/headcount")
        async def get_headcount(filters: DimensionFilter = Depends()):
            ...
    """
    dimension: Optional[str] = Field(
        None,
        description="统计维度: region / project / department",
        examples=["region", "project", "department"],
    )
    start_date: Optional[date] = Field(None, description="起始日期")
    end_date: Optional[date] = Field(None, description="截止日期")


class TrendPoint(BaseModel):
    """
    时间序列趋势点 Schema — 通用单点数据，用于渲染折线图/柱状图

    多个 TrendPoint 组成一条趋势线，前端 ECharts 直接消费。

    字段说明:
        period — 时间段标签，格式由 API 决定:
                   月粒度: "2026-01"
                   季度粒度: "2026-Q1"
                   年粒度: "2026"
        value  — 该时间段对应的指标值（具体含义由使用方的上下文决定）
    """
    period: str = Field(..., description="时间段标签, e.g. '2026-01', '2026-Q1'")
    value: float
    # value 含义依使用场景而定: 在 TurnoverResponse.trend 中为离职率(0-1)


class DistributionItem(BaseModel):
    """
    分布统计单项 Schema — 通用分类数据，用于饼图/条形图

    多个 DistributionItem 组合表示某维度的全量分布，
    如"按部门的人数分布"或"按地点的人数分布"。

    字段说明:
        label — 分类标签，如部门名称"研发部"、地点名称"北京"
        count — 该分类下的人数
        ratio — 该分类占总数的比例，范围 0-1；
                None 表示 Service 未计算（前端自行计算占比时使用）
    """
    label: str = Field(..., description="分类标签")
    count: int = Field(0, description="人数")
    ratio: Optional[float] = Field(None, description="占比 (0-1)")
    # ratio: 0-1 小数制，如 0.25 表示 25%；前端展示时乘以 100 并加 %


# ---------------------------------------------------------------------------
# 1. Headcount (人员分布)
# ---------------------------------------------------------------------------

class HeadcountBreakdown(BaseModel):
    """
    单维度人员分布 Schema — HeadcountResponse 的子组件

    代表按某一维度（地点/部门/项目）分解后的人员分布完整数据。

    字段说明:
        dimension — 当前分布的维度名称，如 "location" / "department" / "project"
        items     — 各分类的人数分布列表
        total     — 该维度下的总人数（应与各 items.count 之和相等）
    """
    dimension: str          # 维度标识，如 "location"/"department"/"project"
    items: list[DistributionItem]
    total: int


class HeadcountResponse(BaseModel):
    """
    人员分布响应 Schema — 用于 GET /api/v1/dashboard/headcount 接口返回

    同时提供按地点、按部门、按项目三个维度的人员分布，
    前端可切换 Tab 展示不同视图，也可同时渲染多个饼图/条形图。

    字段说明及计算来源:
        by_location    — 按工作地点分布；来源: employees LEFT JOIN locations GROUP BY location
        by_department  — 按部门分布；来源: employees LEFT JOIN departments GROUP BY department
        by_project     — 按项目分布（外派/驻场人员）；来源: employees 中的 project 字段
        total_headcount — 当前人员总人数；来源: employees WHERE is_active=True AND status!='离职' COUNT

    注意: 地点和部门维度会包含“未分配”桶，确保与 total_headcount 对齐。
    """
    by_location: list[DistributionItem] = Field(default_factory=list, description="按地点分布")
    by_department: list[DistributionItem] = Field(default_factory=list, description="按部门分布")
    by_project: list[DistributionItem] = Field(default_factory=list, description="按项目分布")
    total_headcount: int = Field(0, description="总人数")


# ---------------------------------------------------------------------------
# 2. HR Cost (HR成本趋势)
# ---------------------------------------------------------------------------

class CostComponent(BaseModel):
    """
    单月度 HR 成本明细 Schema — HRCostResponse.trend 的子项

    拆解某一时间段的人力成本构成，支持"成本瀑布图"或"堆叠柱状图"渲染。

    字段说明及计算来源:
        period           — 时间段标签，如 "2026-01"
        salary           — 工资总额（税前）；来源: salary_records.gross_salary SUM
        social_insurance — 企业社保缴纳总额；来源: salary_records.employer_social_insurance SUM
        housing_fund     — 企业公积金缴纳总额；来源: salary_records.employer_housing_fund SUM
        benefits         — 福利费用（餐补/交通补贴等）；来源: salary_records 其他津贴项 SUM
        total            — 合计 = salary + social_insurance + housing_fund + benefits
    """
    period: str
    salary: float = Field(0, description="工资")
    social_insurance: float = Field(0, description="企业社保")
    housing_fund: float = Field(0, description="企业公积金")
    benefits: float = Field(0, description="福利")
    total: float = Field(0, description="合计")
    # total 应等于上述四项之和，由 Service 层计算填充


class CostComparison(BaseModel):
    """
    HR 成本同环比对比 Schema — HRCostResponse 的子组件

    提供当前统计周期的成本总额及与上期/去年同期的对比。

    字段说明:
        current_total — 当前统计周期的 HR 成本总额（元）
        yoy_rate      — 同比增长率（Year-Over-Year）；正数=同比增长，负数=同比下降；
                        None 表示去年同期无数据（如系统刚上线）
        mom_rate      — 环比增长率（Month-Over-Month）；正数=环比增长；
                        None 表示上月无数据
    """
    current_total: float = 0
    yoy_rate: Optional[float] = Field(None, description="同比增长率")
    # yoy_rate: 同比 = (本月 - 去年同月) / 去年同月；前端展示时乘以 100 加 %
    mom_rate: Optional[float] = Field(None, description="环比增长率")
    # mom_rate: 环比 = (本月 - 上月) / 上月；负值时前端通常显示绿色（成本下降）


class HRCostResponse(BaseModel):
    """
    HR 成本趋势响应 Schema — 用于 GET /api/v1/dashboard/hr-cost 接口返回

    提供指定时间范围内的月度成本趋势明细及同环比对比。
    前端渲染为"堆叠面积图"（按成本构成分层）配合同环比数字卡片。

    字段说明:
        trend      — 月度成本趋势列表，按时间升序排列；列表长度 = 查询月数
        comparison — 最新一期的同环比对比数据
    """
    trend: list[CostComponent] = Field(default_factory=list, description="成本趋势明细")
    comparison: CostComparison = Field(default_factory=CostComparison, description="同比/环比")


# ---------------------------------------------------------------------------
# 3. Attendance rate (出勤率)
# ---------------------------------------------------------------------------

class AttendanceRateItem(BaseModel):
    """
    单分组出勤率数据 Schema — AttendanceRateResponse 的子项

    代表某一地点或部门的出勤率统计，用于对比各组的考勤状况。

    字段说明及计算来源:
        label           — 分组标签（地点名或部门名）
        attendance_rate — 出勤率，范围 0-1；= (应出勤天数 - 缺勤天数) / 应出勤天数
                          来源: attendance_records 按 label 分组统计
        headcount       — 该分组的在职员工人数
        absent_count    — 缺勤记录数（含旷工+未打卡）
        late_count      — 迟到记录数
    """
    label: str
    attendance_rate: float = Field(0, description="出勤率 (0-1)")
    # attendance_rate: 0-1 小数，前端乘以 100 显示为百分比
    headcount: int = 0          # 分组人数，用于计算绝对人数
    absent_count: int = 0       # 缺勤次数（旷工/未打卡等异常）
    late_count: int = 0         # 迟到次数


class AttendanceRateResponse(BaseModel):
    """
    出勤率统计响应 Schema — 用于 GET /api/v1/dashboard/attendance-rate 接口返回

    提供整体出勤率及按地点/部门分组的对比数据。
    前端渲染为仪表盘（整体出勤率）+ 条形图（各组对比）。

    字段说明:
        overall_rate  — 全公司整体出勤率（0-1）
        by_location   — 按工作地点分组的出勤率列表
        by_department — 按部门分组的出勤率列表
    """
    overall_rate: float = Field(0, description="整体出勤率")
    # overall_rate: 0-1 小数，如 0.963 表示 96.3% 出勤率
    by_location: list[AttendanceRateItem] = Field(default_factory=list)
    by_department: list[AttendanceRateItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4. Project manpower (项目人力配置)
# ---------------------------------------------------------------------------

class ProjectManpowerItem(BaseModel):
    """
    单项目人力配置 Schema — ProjectManpowerResponse.projects 的子项

    本公司在全国多个工程现场部署人力，此 Schema 追踪每个项目的
    计划人力 vs 实际配置，帮助发现人力缺口或超编情况。

    字段说明及计算来源:
        project_name   — 项目名称（如"上海汽车工厂 AMR 项目"）
        location       — 项目所在地点
        required_count — 项目计划需求人数；来源: 项目人力需求表或招聘需求
        assigned_count — 当前实际配置（在职员工中分配到此项目的人数）
        gap            — 缺口人数 = required_count - assigned_count；
                         正数=人员不足，负数=超编（需要内部调配）
        roles          — 项目内的角色分布（如机器人工程师/调试员/项目经理等）
    """
    project_name: str
    location: str = ""
    required_count: int = Field(0, description="需求人数")
    assigned_count: int = Field(0, description="已配置人数")
    gap: int = Field(0, description="缺口 (负数=超编)")
    # gap: 正数代表人员缺口（需招聘），负数代表超编（可调配）
    roles: list[DistributionItem] = Field(default_factory=list, description="角色分布")


class ProjectManpowerResponse(BaseModel):
    """
    项目人力配置响应 Schema — 用于 GET /api/v1/dashboard/project-manpower 接口返回

    汇总全部在建项目的人力配置状况，帮助管理层快速识别人力缺口最大的项目。

    字段说明:
        projects        — 各项目人力配置明细列表
        total_required  — 全部项目合计需求人数
        total_assigned  — 全部项目合计已配置人数（total_required - total_assigned = 总缺口）
    """
    projects: list[ProjectManpowerItem] = Field(default_factory=list)
    total_required: int = 0     # 全部项目合计计划人数
    total_assigned: int = 0     # 全部项目合计实际配置人数


# ---------------------------------------------------------------------------
# 5. Turnover (离职率)
# ---------------------------------------------------------------------------

class TurnoverItem(BaseModel):
    """
    单分组离职率数据 Schema — TurnoverResponse 的子项

    代表某一部门的离职率统计，用于识别离职率异常高的部门。

    字段说明及计算来源:
        label         — 部门名称
        turnover_rate — 离职率（0-1）；= 统计期内离职人数 / 期间平均在职人数
                        来源: employees WHERE status='resigned' 按部门分组
        left_count    — 统计期内离职人数
        avg_headcount — 统计期内平均在职人数（通常为期初和期末的平均值）
    """
    label: str
    turnover_rate: float = Field(0, description="离职率 (0-1)")
    # turnover_rate: 0-1 小数，如 0.05 表示 5% 离职率
    left_count: int = 0         # 期间内离职人数
    avg_headcount: int = 0      # 期间平均在职人数（分母）


class TurnoverResponse(BaseModel):
    """
    离职率统计响应 Schema — 用于 GET /api/v1/dashboard/turnover 接口返回

    提供整体离职率、部门对比及月度趋势三个维度的数据。
    前端渲染为: KPI 数字卡片（整体率）+ 横向条形图（部门对比）+ 折线图（趋势）。

    字段说明:
        overall_rate   — 全公司整体离职率（0-1）；统计期内的总离职率
        by_department  — 按部门分组的离职率列表，按离职率降序排列
        trend          — 月度离职率趋势，每个 TrendPoint.value 为当月离职率（0-1）
    """
    overall_rate: float = Field(0, description="整体离职率")
    by_department: list[TurnoverItem] = Field(default_factory=list)
    trend: list[TrendPoint] = Field(default_factory=list, description="离职率趋势")
    # trend: 折线图数据，前端 ECharts 直接消费 period/value 字段


# ---------------------------------------------------------------------------
# 6. Recruitment funnel (招聘漏斗)
# ---------------------------------------------------------------------------

class FunnelStage(BaseModel):
    """
    招聘漏斗单阶段数据 Schema — RecruitmentFunnelResponse.stages 的子项

    代表招聘流程中某一环节的候选人数量和转化率。
    各阶段按流程顺序排列，形成从多到少的漏斗形态。

    字段说明:
        stage           — 招聘阶段名称，典型阶段:
                           "简历收取" → "筛选通过" → "一面" → "二面" → "Offer" → "入职"
        count           — 该阶段的候选人数量
        conversion_rate — 从上一阶段到本阶段的转化率（0-1）；第一阶段为 None
                          转化率 = 本阶段人数 / 上一阶段人数
    """
    stage: str = Field(..., description="阶段名称")
    count: int = 0
    conversion_rate: Optional[float] = Field(None, description="转化率 (0-1)")
    # conversion_rate: None 表示第一阶段（无上一阶段）；低于 0.3 通常需关注


class RecruitmentFunnelResponse(BaseModel):
    """
    招聘漏斗响应 Schema — 用于 GET /api/v1/dashboard/recruitment-funnel 接口返回

    展示当前统计周期内招聘全流程的候选人流转情况，
    帮助 HR 识别筛选效率瓶颈（如哪个阶段流失率最高）。

    字段说明:
        stages              — 各招聘阶段的候选人数量和转化率，按流程顺序排列
        overall_conversion  — 整体转化率 = 入职人数 / 简历收取总数（0-1）
    """
    stages: list[FunnelStage] = Field(default_factory=list)
    overall_conversion: Optional[float] = Field(None, description="整体转化率")
    # overall_conversion: 整体转化率；如 0.05 表示 5% 的简历最终入职


# ---------------------------------------------------------------------------
# 7. Overview (综合概览)
# ---------------------------------------------------------------------------

class OverviewMetric(BaseModel):
    """
    KPI 卡片数据 Schema — OverviewResponse.metrics 的子项

    代表驾驶舱上一个可配置的 KPI 指标卡片。
    支持动态扩展，无需修改 OverviewResponse 主体即可增加新指标。

    字段说明:
        metric_name — 指标名称，如"本月培训完成率"
        value       — 指标数值
        unit        — 单位，如 "人"、"%"、"元"、"天"
        trend       — 趋势方向: "up"（上升）/ "down"（下降）/ "flat"（持平）
                      None 表示无法判断趋势（如第一个统计周期）
        change_rate — 与上期相比的变动率（0-1 小数）；正数为增加，负数为减少
    """
    metric_name: str
    value: float
    unit: str = ""
    trend: Optional[str] = Field(None, description="up / down / flat")
    # trend: 前端根据此值决定箭头方向和颜色（up=绿色上箭头 or 红色上箭头，按业务语义决定）
    change_rate: Optional[float] = Field(None, description="变动率")


class OverviewResponse(BaseModel):
    """
    综合概览响应 Schema — 用于 GET /api/v1/dashboard/overview 接口返回

    驾驶舱首页最核心的 Schema，聚合所有模块的关键指标，供管理层一览全局。

    字段说明及计算来源:
        total_headcount       — 当前人员总数；来源: employees WHERE is_active=True AND status!='离职' COUNT
        new_hires_this_month  — 本月新入职人数；来源: employees WHERE hire_date in 本月 COUNT
        departures_this_month — 本月离职人数；来源: employees WHERE termination_date in 本月 COUNT
        turnover_rate         — 本月离职率（0-1）；= 离职人数 / 月初人数
        attendance_rate       — 本月整体出勤率（0-1）；来源: attendance_records 统计
        open_positions        — 当前开放招聘需求数；来源: recruitment_demands WHERE status='open'
        pending_approvals     — 当前用户待处理审批数；来源: approval_instances WHERE
                                 current_approver=current_user AND status='pending'
        hr_cost_this_month    — 本月 HR 成本合计（元）；来源: salary_records 本月汇总
        department_count      — 部门总数；来源: departments WHERE is_active=True COUNT
        new_hires_trend       — 本月入职人数环比变化百分比字符串，如 "+12.5%"、"-3.2%"
        new_hires_trend_up    — True=本月入职人数多于上月（绿色/向上箭头）
        departures_trend      — 本月离职人数环比变化百分比字符串
        departures_trend_up   — True=本月离职人数多于上月（通常显示红色/警示）
        metrics               — 动态扩展的 KPI 卡片列表（可包含模块特定指标）
    """
    total_headcount: int = 0
    new_hires_this_month: int = 0
    departures_this_month: int = 0
    turnover_rate: float = 0            # 0-1 小数，如 0.05 = 5% 离职率
    attendance_rate: float = 0          # 0-1 小数，如 0.96 = 96% 出勤率
    open_positions: int = 0             # 当前仍在招募中的岗位数
    pending_approvals: int = 0          # 当前用户（HR/管理员）待处理的审批数
    hr_cost_this_month: float = 0       # 本月人力成本总额（元）
    department_count: int = 0           # 在用部门数量
    new_hires_trend: str = Field("0%", description="本月入职环比趋势百分比")
    # new_hires_trend: 字符串格式，如 "+8.3%" 或 "-5.0%"，前端直接展示
    new_hires_trend_up: bool = True     # True=入职人数环比增加（通常为正向指标，显示绿色）
    departures_trend: str = Field("0%", description="本月离职环比趋势百分比")
    departures_trend_up: bool = False   # True=离职人数环比增加（通常为负向指标，显示红色）
    metrics: list[OverviewMetric] = Field(default_factory=list, description="KPI卡片列表")
    # metrics: 可扩展的额外 KPI 列表，如"本月培训完成率"、"合同到期预警数"等


# ---------------------------------------------------------------------------
# 8. Recent activity (近期动态)
# ---------------------------------------------------------------------------

class RecentActivityItem(BaseModel):
    """
    近期动态单条事件 Schema — RecentActivityResponse.items 的子项

    代表系统中发生的一条重要 HR 事件，用于驾驶舱的"动态流"组件展示。

    字段说明:
        id      — 事件 ID（可用于前端跳转详情，具体含义由 type 决定）
        tag     — 事件类型标签: "入职" / "离职" / "审批" / "调动" / "合同" 等
        content — 事件描述文本，如"张三 于 2026-03-01 入职研发部"
        time    — 事件发生时间，格式: "YYYY-MM-DD HH:MM"（字符串）
        type    — Element Plus Tag 组件的颜色类型:
                    "success" — 绿色（入职/审批通过等正向事件）
                    "primary" — 蓝色（调动/一般事件）
                    "danger"  — 红色（离职/审批拒绝等负向事件）
                    "warning" — 橙色（预警/即将到期等）
    """
    id: int
    tag: str = Field(..., description="标签：入职/离职/审批/调动")
    content: str = Field(..., description="事件描述")
    time: str = Field(..., description="时间 YYYY-MM-DD HH:MM")
    type: str = Field("primary", description="类型：success/primary/danger/warning")
    # type 与 Element Plus <el-tag :type=""> 的 type 属性对应


class RecentActivityResponse(BaseModel):
    """
    近期动态响应 Schema — 用于 GET /api/v1/dashboard/recent-activity 接口返回

    返回最近 N 条重要 HR 事件列表（通常取最新 10-20 条），
    前端以时间线或滚动列表形式展示。

    字段说明:
        items — 近期事件列表，按事件时间倒序排列（最新在前）
    """
    items: list[RecentActivityItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 9. Pending items (待办事项)
# ---------------------------------------------------------------------------

class PendingItem(BaseModel):
    """
    待办审批单项 Schema — PendingItemsResponse.items 的子项

    代表当前登录用户（通常为 HR 管理员或直属上级）需要处理的一条审批待办。

    字段说明:
        title       — 审批事项标题，如"年假申请" / "离职申请" / "招聘需求"
        applicant   — 申请人姓名
        date        — 申请提交日期（字符串，格式 "YYYY-MM-DD"）
        status      — 当前状态描述，如"待审批" / "进行中"
        status_type — Element Plus Tag 组件颜色类型:
                        "warning" — 橙色（默认，表示待处理）
                        "success" — 绿色（已完成）
                        "danger"  — 红色（紧急/逾期）
    """
    title: str
    applicant: str
    date: str
    status: str
    status_type: str = Field("warning", description="Element Plus tag type: warning/success/danger")
    # status_type: 与 <el-tag :type="status_type"> 对应，默认 warning（橙色，表示需要处理）


class PendingItemsResponse(BaseModel):
    """
    待办事项响应 Schema — 用于 GET /api/v1/dashboard/pending-items 接口返回

    返回当前登录用户的审批待办列表（通常取最新 10-20 条）。
    数据来源: approval_instances WHERE current_approver_id=current_user.id AND status='pending'

    字段说明:
        items — 待办审批列表，按提交时间倒序（最新/最紧急在前）
    """
    items: list[PendingItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 10. Drill-down schemas (钻取接口)
# ---------------------------------------------------------------------------

class DrillDeptEmployee(BaseModel):
    """
    部门人员钻取 Schema — 点击驾驶舱"部门人数"后展开的员工明细

    用于 GET /api/v1/dashboard/drill/department/{dept_id}/employees 接口返回。
    每行代表该部门的一名在职员工。

    字段说明:
        id       — 员工 ID（可跳转到员工详情页）
        name     — 员工姓名
        position — 岗位名称（可为空，如员工档案未填写职位）
        status   — 在职状态，如 "active"（在职）/ "probation"（试用期）
    """
    id: int
    name: str
    position: Optional[str] = None    # 岗位名称；尚未填写档案的员工可能为 None
    status: str                        # 在职状态: active/probation 等


class DrillLeavePerson(BaseModel):
    """
    今日请假人员钻取 Schema — 点击驾驶舱"今日请假"卡片后展开的员工明细

    用于 GET /api/v1/dashboard/drill/today-leave 接口返回。
    每行代表今天处于请假状态的一名员工。

    字段说明:
        employee_id — 员工 ID
        name        — 员工姓名
        leave_type  — 请假类型，如"年假" / "病假" / "事假"
        days        — 请假天数（支持半天，故使用 float，如 0.5 表示半天）
    """
    employee_id: int
    name: str
    leave_type: str     # 请假类型，对应 leave_types.name
    days: float         # 请假天数，支持 0.5（半天）


class DrillAttendanceAnomaly(BaseModel):
    """
    考勤异常钻取 Schema — 点击驾驶舱"考勤异常"卡片后展开的异常记录明细

    用于 GET /api/v1/dashboard/drill/attendance-anomaly 接口返回。
    每行代表一条今日考勤异常记录。

    字段说明:
        employee_id  — 员工 ID
        name         — 员工姓名
        anomaly_type — 异常类型，如"迟到" / "早退" / "缺勤" / "未打卡"
        time         — 异常发生时间（字符串），格式 "HH:MM" 或 "YYYY-MM-DD HH:MM"
    """
    employee_id: int
    name: str
    anomaly_type: str   # 考勤异常类型: 迟到/早退/缺勤/未打卡
    time: str           # 异常时间字符串


class DrillExpiringContract(BaseModel):
    """
    即将到期合同钻取 Schema — 点击驾驶舱"合同预警"卡片后展开的到期合同明细

    用于 GET /api/v1/dashboard/drill/expiring-contracts 接口返回。
    每行代表一份即将到期（通常 30/60/90 天内）的劳动合同。

    字段说明:
        employee_id  — 员工 ID（可跳转到员工合同页面）
        name         — 员工姓名
        contract_end — 合同到期日期（字符串，格式 "YYYY-MM-DD"）
        days_left    — 距到期还剩天数；<= 30 天通常标红预警
    """
    employee_id: int
    name: str
    contract_end: str   # 合同到期日期，如 "2026-04-15"
    days_left: int      # 剩余天数；值越小越紧急


class DrillPendingApproval(BaseModel):
    """
    待处理审批钻取 Schema — 点击驾驶舱"待审批"卡片后展开的审批实例明细

    用于 GET /api/v1/dashboard/drill/pending-approvals 接口返回。
    每行代表一条当前用户需要审批的审批实例。

    字段说明:
        instance_id — 审批实例 ID（approval_instances.id），可跳转到审批详情页
        type        — 审批类型描述，如"请假审批" / "招聘需求审批" / "薪资调整"
        applicant   — 申请人姓名
        created_at  — 审批发起时间（字符串，格式 "YYYY-MM-DD HH:MM"）
    """
    instance_id: int    # approval_instances.id，前端用于跳转 /approvals/{instance_id}
    type: str           # 审批类型，如"年假申请"、"离职申请"
    applicant: str      # 申请人姓名
    created_at: str     # 申请时间，格式 "YYYY-MM-DD HH:MM"
