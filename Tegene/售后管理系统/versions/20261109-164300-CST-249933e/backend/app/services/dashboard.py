"""
管理驾驶舱服务 - Dashboard Service
=====================================

本模块是售后管理系统的"管理驾驶舱"后端服务，为前端大屏/首页提供
跨模块聚合数据，数据直接来自真实数据库查询。

架构设计：
    - 有状态类（DashboardService），构造函数接收 db session，避免每个方法重复传参
    - 所有方法均有 try/except 兜底，数据表不存在或无数据时返回零值/空列表，
      不会导致前端白屏
    - 辅助方法 _month_range / _prev_month_range 复用月份边界计算逻辑

9 个核心数据接口：

1. **headcount（人力分布）**
   按地点、部门、项目三个维度分别统计在职人员分布
   含总人数、各维度比例

2. **hr_cost（人力成本）**
   过去 6 个月的工资成本趋势（薪资 + 社保公司端 + 公积金公司端）
   数据来自 SalaryRecord 表，计算环比变化率

3. **attendance_rate（出勤率）**
   当月整体出勤率（实际出勤天数 / 应出勤天数）
   数据来自 AttendanceMonthSummary 汇总表

4. **project_manpower（项目人力配置）**
   现场人员按所在项目的分配情况
   数据来自 Employee.current_project 字段

5. **turnover（离职分析）**
   当月离职人数、整体离职率、按部门细分
   离职率 = 当月离职 / (在职 + 当月离职)

6. **recruitment_funnel（招聘漏斗）**
   简历→面试→Offer→入职四阶段转化率
   数据来自 Resume/Interview/Offer 表 + 当月新入职员工数

7. **overview（总览KPI）**
   在职人数、本月入职、本月离职、待审批数、部门数
   同时计算与上月对比的趋势（涨/跌百分比和方向箭头）

8. **recent_activity（最近动态）**
   过去 30 天内的入职、离职、请假审批通过事件列表
   多源合并后按时间倒序排列，截取前 N 条

9. **pending_items（待审批事项）**
   待审批的请假申请 + 通用审批流事项
   同时附加最近已处理的 5 条（供管理者了解审批进度）

内部辅助方法：
    _month_range(ref_date)      -- 返回某月的第一天和最后一天
    _prev_month_range(ref_date) -- 返回上个月的第一天和最后一天
    get_overview._trend_str     -- 计算环比变化率字符串和方向

调用关系：
    api/v1/dashboard.py → services/dashboard.py（本文件）
    → models/employee, organization, approval, leave, payroll, attendance, recruitment

依赖框架：FastAPI + SQLAlchemy 2.0 Async + PostgreSQL + Pydantic v2
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, and_, or_, extract, case, literal
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee, EmployeeStatus
from app.models.organization import Department, Location
from app.models.approval import ApprovalInstance
from app.models.leave import LeaveRequest, ApprovalStatus as LeaveApprovalStatus

from app.schemas.dashboard import (
    AttendanceRateItem,
    AttendanceRateResponse,
    CostComponent,
    CostComparison,
    DistributionItem,
    FunnelStage,
    HeadcountResponse,
    HRCostResponse,
    OverviewMetric,
    OverviewResponse,
    ProjectManpowerItem,
    ProjectManpowerResponse,
    RecentActivityItem,
    RecentActivityResponse,
    PendingItem,
    PendingItemsResponse,
    RecruitmentFunnelResponse,
    TrendPoint,
    TurnoverItem,
    TurnoverResponse,
)


class DashboardService:
    """
    管理驾驶舱数据服务（有状态类，通过构造函数注入 db session）。

    使用方式：
        service = DashboardService(db)
        overview = await service.get_overview()

    设计原则：
        - 所有方法在数据不足时优雅降级（返回零值/空列表），不抛出 500
        - 涉及可选依赖模块（如 payroll、attendance、recruitment）的查询
          包裹在 try/except 中，模块未部署时不影响其他指标
    """

    def __init__(self, db: AsyncSession) -> None:
        """
        初始化 DashboardService。

        参数：
            db -- 异步数据库会话（由 FastAPI 依赖注入提供）
        """
        self.db = db

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _month_range(self, ref_date: Optional[date] = None) -> tuple[date, date]:
        """
        计算指定日期所在月份的第一天和最后一天。

        参数：
            ref_date -- 参考日期，默认为今天

        返回：
            (first_day, last_day) 元组，如 (2026-03-01, 2026-03-31)
        """
        d = ref_date or date.today()
        first = d.replace(day=1)
        # 计算最后一天：跳到下个月的第一天再减一天
        if d.month == 12:
            last = d.replace(year=d.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last = d.replace(month=d.month + 1, day=1) - timedelta(days=1)
        return first, last

    @staticmethod
    def _active_employee_filters() -> tuple:
        """驾驶舱统一当前人员口径：与员工管理页默认“在职员工”统计保持一致。"""
        return (
            Employee.is_active == True,
            or_(Employee.status.is_(None), Employee.status != EmployeeStatus.LEFT.value),
        )

    def _prev_month_range(self, ref_date: Optional[date] = None) -> tuple[date, date]:
        """
        计算指定日期的上个月第一天和最后一天。

        参数：
            ref_date -- 参考日期，默认为今天

        返回：
            (first_day, last_day) 元组，如 (2026-02-01, 2026-02-28)
        """
        d = ref_date or date.today()
        first_this = d.replace(day=1)
        last_prev = first_this - timedelta(days=1)   # 本月1日的前一天 = 上月最后一天
        first_prev = last_prev.replace(day=1)         # 上月最后一天的1日 = 上月第一天
        return first_prev, last_prev

    # ------------------------------------------------------------------
    # 1. 人力分布（Headcount）
    # ------------------------------------------------------------------

    async def get_headcount(
        self,
        dimension: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> HeadcountResponse:
        """
        查询在职人员分布数据（按地点、部门、项目三个维度）。

        当前人员定义：is_active=True 且非离职状态，与员工管理页默认看板一致。

        各维度说明：
            by_location   -- 按工作地点（Location 表）统计，含占比
            by_department -- 按所属部门（Department 表）统计，含占比
            by_project    -- 按当前项目（Employee.current_project 字段）统计，
                             仅统计 is_field_staff=True（现场人员）且有项目的员工

        参数：
            db         -- 通过 self.db 访问
            dimension  -- 保留参数（前端可传，当前逻辑不依赖）
            start_date -- 保留参数（当前逻辑不依赖）
            end_date   -- 保留参数（当前逻辑不依赖）

        返回：
            HeadcountResponse，包含 total_headcount 和三个维度的分布列表
        """
        # 在职员工总数（用于计算各分布的占比）
        total_q = select(func.count(Employee.id)).where(
            *self._active_employee_filters()
        )
        total_result = await self.db.execute(total_q)
        total_headcount = total_result.scalar() or 0

        # 按工作地点分布（保留未分配地点员工，保证分布合计与总人数一致）
        location_label = func.coalesce(Location.name, "未分配地点")
        by_location_q = (
            select(
                location_label.label("label"),
                func.count(Employee.id).label("cnt"),
            )
            .select_from(Employee)
            .outerjoin(Location, Employee.location_id == Location.id)
            .where(*self._active_employee_filters())
            .group_by(location_label)
            .order_by(func.count(Employee.id).desc())  # 人数多的地点优先
        )
        by_location_result = await self.db.execute(by_location_q)
        by_location = [
            DistributionItem(
                label=row.label,
                count=row.cnt,
                ratio=round(row.cnt / total_headcount, 2) if total_headcount else 0,
            )
            for row in by_location_result
        ]

        # 按部门分布（保留未分配部门员工，避免部门人员变化图少计）
        department_label = func.coalesce(Department.name, "未分配部门")
        by_dept_q = (
            select(
                department_label.label("label"),
                func.count(Employee.id).label("cnt"),
            )
            .select_from(Employee)
            .outerjoin(Department, Employee.department_id == Department.id)
            .where(*self._active_employee_filters())
            .group_by(department_label)
            .order_by(func.count(Employee.id).desc())
        )
        by_dept_result = await self.db.execute(by_dept_q)
        by_department = [
            DistributionItem(
                label=row.label,
                count=row.cnt,
                ratio=round(row.cnt / total_headcount, 2) if total_headcount else 0,
            )
            for row in by_dept_result
        ]

        # 按项目分布（仅现场人员，按 current_project 字段分组）
        by_proj_q = (
            select(
                Employee.current_project.label("label"),
                func.count(Employee.id).label("cnt"),
            )
            .where(
                *self._active_employee_filters(),
                Employee.is_field_staff == True,           # 仅统计现场人员
                Employee.current_project.isnot(None),     # 排除未分配项目的员工
                Employee.current_project != "",            # 排除空字符串
            )
            .group_by(Employee.current_project)
            .order_by(func.count(Employee.id).desc())
        )
        by_proj_result = await self.db.execute(by_proj_q)
        by_project = [
            DistributionItem(
                label=row.label,
                count=row.cnt,
                ratio=round(row.cnt / total_headcount, 2) if total_headcount else 0,
            )
            for row in by_proj_result
        ]

        return HeadcountResponse(
            by_location=by_location,
            by_department=by_department,
            by_project=by_project,
            total_headcount=total_headcount,
        )

    # ------------------------------------------------------------------
    # 2. 人力成本（HR Cost）
    # ------------------------------------------------------------------

    async def get_hr_cost(
        self,
        dimension: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> HRCostResponse:
        """
        查询过去 6 个月的人力成本趋势及环比对比。

        成本构成（每月）：
            salary          -- 工资总额（SalaryRecord.gross_salary 之和）
            social_insurance -- 公司承担社保（SalaryRecord.social_insurance_company 之和）
            housing_fund    -- 公司承担公积金（SalaryRecord.housing_fund_company 之和）
            benefits        -- 福利费用（当前版本暂无对应字段，固定为 0）
            total           -- 四项合计

        环比计算：
            mom_rate = (当月 total - 上月 total) / 上月 total（保留 4 位小数）

        优雅降级：
            若 SalaryRecord 表不存在或字段不匹配，返回空趋势（trend=[], comparison={}）

        参数：
            dimension  -- 保留参数
            start_date -- 保留参数（当前固定计算过去 6 个月）
            end_date   -- 保留参数

        返回：
            HRCostResponse，包含 6 个月的 trend 列表和当月 vs 上月的 comparison
        """
        try:
            from app.models.payroll import SalaryRecord
            months_back = 6
            today = date.today()
            trend = []
            # 从最早月份开始计算，倒推 6 个月
            for i in range(months_back - 1, -1, -1):
                d = today.replace(day=1) - timedelta(days=30 * i)  # 近似偏移，实际精度足够
                period = d.strftime("%Y-%m")
                year = d.year
                month = d.month

                # 查询该月薪资汇总（三项：工资、社保公司端、公积金公司端）
                salary_q = select(
                    func.coalesce(func.sum(SalaryRecord.gross_salary), 0).label("salary"),
                    func.coalesce(func.sum(SalaryRecord.social_insurance_company), 0).label("si"),
                    func.coalesce(func.sum(SalaryRecord.housing_fund_company), 0).label("hf"),
                ).where(
                    SalaryRecord.year == year,
                    SalaryRecord.month == month,
                )
                result = await self.db.execute(salary_q)
                row = result.one_or_none()
                salary = float(row.salary) if row else 0
                si = float(row.si) if row else 0
                hf = float(row.hf) if row else 0
                benefits = 0.0  # 当前版本无福利费列，预留占位
                trend.append(CostComponent(
                    period=period,
                    salary=salary,
                    social_insurance=si,
                    housing_fund=hf,
                    benefits=benefits,
                    total=salary + si + hf + benefits,
                ))

            # 计算环比变化率（当月 vs 上月）
            current_total = trend[-1].total if trend else 0
            prev_total = trend[-2].total if len(trend) >= 2 else 0
            mom_rate = round((current_total - prev_total) / prev_total, 4) if prev_total else 0
            comparison = CostComparison(
                current_total=current_total,
                yoy_rate=None,   # 同比暂不计算（需要去年同月数据）
                mom_rate=mom_rate,
            )
            return HRCostResponse(trend=trend, comparison=comparison)

        except Exception:
            # SalaryRecord 表不存在或字段不匹配时返回空数据
            return HRCostResponse(trend=[], comparison=CostComparison())

    # ------------------------------------------------------------------
    # 3. 出勤率（Attendance Rate）
    # ------------------------------------------------------------------

    async def get_attendance_rate(
        self,
        dimension: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> AttendanceRateResponse:
        """
        查询当月整体出勤率。

        计算公式：
            出勤率 = AVG(actual_days / work_days) （按员工逐一计算后取均值）
            当 work_days=0 时，该员工出勤率按 1.0（满出勤）处理

        数据来源：AttendanceMonthSummary 汇总表（按年月查询当月数据）

        优雅降级：
            AttendanceMonthSummary 表不存在时返回 overall_rate=0

        参数：
            dimension  -- 保留参数
            start_date -- 保留参数（当前固定查当月）
            end_date   -- 保留参数

        返回：
            AttendanceRateResponse，包含：
            - overall_rate   -- 整体出勤率（0~1 小数，如 0.9567）
            - by_location    -- 按地点细分（当前版本返回空列表）
            - by_department  -- 按部门细分（当前版本返回空列表）
        """
        try:
            from app.models.attendance import AttendanceMonthSummary
            today = date.today()
            year = today.year
            month = today.month

            # 用 CASE 处理 work_days=0 的情况（避免除零错误）
            overall_q = select(
                func.avg(
                    case(
                        (AttendanceMonthSummary.work_days > 0,
                         AttendanceMonthSummary.actual_days * 1.0 / AttendanceMonthSummary.work_days),
                        else_=1.0,  # work_days=0 视为满出勤（如新员工未有工作日）
                    )
                )
            ).where(
                AttendanceMonthSummary.year == year,
                AttendanceMonthSummary.month == month,
            )
            result = await self.db.execute(overall_q)
            overall_rate = float(result.scalar() or 0)

            return AttendanceRateResponse(
                overall_rate=round(overall_rate, 4),
                by_location=[],    # 按地点细分暂未实现
                by_department=[],  # 按部门细分暂未实现
            )
        except Exception:
            return AttendanceRateResponse(overall_rate=0, by_location=[], by_department=[])

    # ------------------------------------------------------------------
    # 4. 项目人力配置（Project Manpower）
    # ------------------------------------------------------------------

    async def get_project_manpower(
        self,
        dimension: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> ProjectManpowerResponse:
        """
        查询现场人员按项目的分配情况。

        数据来源：Employee 表的 current_project 字段（仅 is_field_staff=True 的员工）

        参数：
            dimension  -- 保留参数
            start_date -- 保留参数
            end_date   -- 保留参数

        返回：
            ProjectManpowerResponse，包含：
            - projects       -- 各项目的分配人数列表（按人数倒序）
            - total_assigned -- 已分配项目的现场人员总数
            - total_required -- 计划需求总数（当前版本无需求追踪，固定为 0）

        注意：
            required_count 和 gap 字段当前版本均为 0，
            因为员工模型中没有项目人力需求字段
        """
        q = (
            select(
                Employee.current_project.label("project"),
                func.count(Employee.id).label("cnt"),
            )
            .where(
                *self._active_employee_filters(),
                Employee.is_field_staff == True,           # 仅现场人员
                Employee.current_project.isnot(None),
                Employee.current_project != "",
            )
            .group_by(Employee.current_project)
            .order_by(func.count(Employee.id).desc())
        )
        result = await self.db.execute(q)
        projects = []
        total_assigned = 0
        for row in result:
            projects.append(ProjectManpowerItem(
                project_name=row.project,
                assigned_count=row.cnt,
                required_count=0,  # 暂无需求字段，预留
                gap=0,             # gap = required - assigned（当前为 0）
            ))
            total_assigned += row.cnt

        return ProjectManpowerResponse(
            projects=projects,
            total_required=0,
            total_assigned=total_assigned,
        )

    # ------------------------------------------------------------------
    # 5. 离职分析（Turnover）
    # ------------------------------------------------------------------

    async def get_turnover(
        self,
        dimension: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> TurnoverResponse:
        """
        查询当月离职数据和离职率。

        离职率计算公式：
            overall_rate = 当月离职数 / (当前在职数 + 当月离职数)
            分母含当月离职人员，反映该月员工池大小

        参数：
            dimension  -- 保留参数
            start_date -- 保留参数（当前固定查当月）
            end_date   -- 保留参数

        返回：
            TurnoverResponse，包含：
            - overall_rate   -- 整体离职率（0~1 小数）
            - by_department  -- 按部门细分的离职人数（含 TurnoverItem 列表）
            - trend          -- 月度趋势（当前版本返回空列表）

        注意：
            TurnoverItem 中的 turnover_rate 和 avg_headcount 当前版本均为 0，
            需要历史人员快照数据才能精确计算各部门离职率
        """
        today = date.today()
        month_start, month_end = self._month_range(today)

        # 当月离职人数（leave_date 在本月范围内的 LEFT 状态员工）
        left_q = select(func.count(Employee.id)).where(
            Employee.status == EmployeeStatus.LEFT.value,
            Employee.leave_date >= month_start,
            Employee.leave_date <= month_end,
        )
        left_result = await self.db.execute(left_q)
        left_count = left_result.scalar() or 0

        # 当前在职人数（作为离职率分母的一部分）
        active_q = select(func.count(Employee.id)).where(
            *self._active_employee_filters()
        )
        active_result = await self.db.execute(active_q)
        active_count = active_result.scalar() or 0

        # 离职率 = 当月离职 / (在职 + 当月离职)，防零除
        overall_rate = round(left_count / (active_count + left_count), 4) if (active_count + left_count) else 0

        # 按部门统计当月离职人数
        by_dept_q = (
            select(
                Department.name.label("label"),
                func.count(Employee.id).label("left_cnt"),
            )
            .join(Employee, Employee.department_id == Department.id)
            .where(
                Employee.status == EmployeeStatus.LEFT.value,
                Employee.leave_date >= month_start,
                Employee.leave_date <= month_end,
            )
            .group_by(Department.name)
        )
        by_dept_result = await self.db.execute(by_dept_q)
        by_department = [
            TurnoverItem(
                label=row.label,
                left_count=row.left_cnt,
                turnover_rate=0,       # 部门级离职率需历史快照，暂为 0
                avg_headcount=0,       # 部门平均人数需历史快照，暂为 0
            )
            for row in by_dept_result
        ]

        return TurnoverResponse(
            overall_rate=overall_rate,
            by_department=by_department,
            trend=[],  # 月度趋势暂未实现
        )

    # ------------------------------------------------------------------
    # 6. 招聘漏斗（Recruitment Funnel）
    # ------------------------------------------------------------------

    async def get_recruitment_funnel(
        self,
        dimension: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> RecruitmentFunnelResponse:
        """
        查询招聘漏斗各阶段转化率数据。

        漏斗四阶段（全量统计）：
            简历投递 → 面试 → 发放Offer → 入职

        转化率计算：
            各阶段转化率 = 该阶段数量 / 上一阶段数量
            整体转化率 = 入职数 / 简历数

        入职数据来源：
            当月 hire_date 在本月范围内的新入职员工数
            （而非 Offer 表的入职确认，因为 Offer 表可能有历史积压）

        优雅降级：
            recruitment 模块未部署时返回空漏斗（stages=[], overall_conversion=0）

        参数：
            dimension  -- 保留参数
            start_date -- 保留参数
            end_date   -- 保留参数

        返回：
            RecruitmentFunnelResponse，包含 stages 列表和 overall_conversion 总转化率
        """
        try:
            from app.models.recruitment import Resume, Interview, Offer

            # 各阶段总量（全量统计，不限时间范围）
            resume_q = select(func.count(Resume.id))
            resume_result = await self.db.execute(resume_q)
            resume_count = resume_result.scalar() or 0

            interview_q = select(func.count(Interview.id))
            interview_result = await self.db.execute(interview_q)
            interview_count = interview_result.scalar() or 0

            offer_q = select(func.count(Offer.id))
            offer_result = await self.db.execute(offer_q)
            offer_count = offer_result.scalar() or 0

            # 本月入职人数（来自员工表的 hire_date）
            today = date.today()
            month_start, month_end = self._month_range(today)
            onboard_q = select(func.count(Employee.id)).where(
                Employee.hire_date >= month_start,
                Employee.hire_date <= month_end,
            )
            onboard_result = await self.db.execute(onboard_q)
            onboard_count = onboard_result.scalar() or 0

            # 组装漏斗四阶段数据（含逐级转化率）
            stages = [
                FunnelStage(stage="简历投递", count=resume_count, conversion_rate=1.0),  # 第一阶段，转化率 100%
                FunnelStage(
                    stage="面试",
                    count=interview_count,
                    conversion_rate=round(interview_count / resume_count, 4) if resume_count else 0,
                ),
                FunnelStage(
                    stage="发放Offer",
                    count=offer_count,
                    conversion_rate=round(offer_count / interview_count, 4) if interview_count else 0,
                ),
                FunnelStage(
                    stage="入职",
                    count=onboard_count,
                    conversion_rate=round(onboard_count / offer_count, 4) if offer_count else 0,
                ),
            ]
            # 整体转化率：从简历到入职的端到端转化
            overall = round(onboard_count / resume_count, 4) if resume_count else 0

            return RecruitmentFunnelResponse(stages=stages, overall_conversion=overall)
        except Exception:
            return RecruitmentFunnelResponse(stages=[], overall_conversion=0)

    # ------------------------------------------------------------------
    # 7. 总览 KPI（Overview）
    # ------------------------------------------------------------------

    async def get_overview(
        self,
        dimension: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> OverviewResponse:
        """
        查询管理驾驶舱总览 KPI 数据（前端首屏最重要的接口）。

        指标列表：
            total_headcount        -- 当前人员总数（员工管理页默认“在职员工”口径）
            new_hires_this_month   -- 本月新入职人数
            departures_this_month  -- 本月离职人数
            turnover_rate          -- 本月离职率
            pending_approvals      -- 待审批总数（审批流 + 请假申请）
            department_count       -- 在用部门总数

        环比趋势计算（new_hires_trend, departures_trend）：
            trend_str = |current - previous| / previous，格式化为百分比字符串
            trend_up  = current >= previous（True 表示上升或持平）

        注意：
            待审批数 = ApprovalInstance 中 pending 数 + LeaveRequest 中 pending 数
            （两者可能有重叠，但实际业务中 ApprovalInstance 只记录进入审批流的申请，
            部分直属审批的请假不进审批流，因此两者相加更完整）

        参数：
            dimension  -- 保留参数
            start_date -- 保留参数
            end_date   -- 保留参数

        返回：
            OverviewResponse，包含所有 KPI 指标和 metrics 列表（供前端卡片展示）
        """
        today = date.today()
        month_start, month_end = self._month_range(today)
        prev_month_start, prev_month_end = self._prev_month_range(today)

        # 当前人员总数（统一口径：与员工管理页默认“在职员工”一致）
        total_q = select(func.count(Employee.id)).where(
            *self._active_employee_filters()
        )
        total_result = await self.db.execute(total_q)
        total_headcount = total_result.scalar() or 0

        # 本月新入职人数（含 PENDING 待入职状态，因为可能已入职但未激活账号；排除软删除）
        current_roster_statuses = [
            EmployeeStatus.ACTIVE.value,
            EmployeeStatus.ON_PROBATION.value,
            EmployeeStatus.PENDING.value,
        ]
        new_hires_q = select(func.count(Employee.id)).where(
            Employee.hire_date >= month_start,
            Employee.hire_date <= month_end,
            Employee.is_active == True,
            Employee.status.in_(current_roster_statuses),
        )
        new_hires_result = await self.db.execute(new_hires_q)
        new_hires = new_hires_result.scalar() or 0

        # 上月新入职人数（用于环比计算，排除软删除和离职员工）
        prev_new_hires_q = select(func.count(Employee.id)).where(
            Employee.hire_date >= prev_month_start,
            Employee.hire_date <= prev_month_end,
            Employee.is_active == True,
            Employee.status.in_(current_roster_statuses),
        )
        prev_new_hires_result = await self.db.execute(prev_new_hires_q)
        prev_new_hires = prev_new_hires_result.scalar() or 0

        # 本月离职人数
        departures_q = select(func.count(Employee.id)).where(
            Employee.status == EmployeeStatus.LEFT.value,
            Employee.leave_date >= month_start,
            Employee.leave_date <= month_end,
        )
        departures_result = await self.db.execute(departures_q)
        departures = departures_result.scalar() or 0

        # 上月离职人数（用于环比计算）
        prev_departures_q = select(func.count(Employee.id)).where(
            Employee.status == EmployeeStatus.LEFT.value,
            Employee.leave_date >= prev_month_start,
            Employee.leave_date <= prev_month_end,
        )
        prev_departures_result = await self.db.execute(prev_departures_q)
        prev_departures = prev_departures_result.scalar() or 0

        # 本月离职率 = 本月离职 / (当前在职 + 本月离职)
        denominator = total_headcount + departures
        turnover_rate = round(departures / denominator, 4) if denominator else 0

        # 待审批总数：来自通用审批流 + 来自请假模块（部分请假未进审批流）
        pending_q = select(func.count(ApprovalInstance.id)).where(
            ApprovalInstance.status == "pending"
        )
        pending_result = await self.db.execute(pending_q)
        pending_approvals = pending_result.scalar() or 0

        # 还未进入通用审批流的待审批请假申请
        pending_leave_q = select(func.count(LeaveRequest.id)).where(
            LeaveRequest.approval_status == LeaveApprovalStatus.pending
        )
        pending_leave_result = await self.db.execute(pending_leave_q)
        pending_leave = pending_leave_result.scalar() or 0

        total_pending = pending_approvals + pending_leave  # 合并两个来源的待审批数

        # 在用部门总数
        dept_q = select(func.count(Department.id)).where(Department.is_active == True)
        dept_result = await self.db.execute(dept_q)
        dept_count = dept_result.scalar() or 0

        # 环比趋势计算（内部辅助函数）
        def _trend_str(current: int, previous: int) -> tuple[str, bool]:
            """
            计算环比变化的百分比字符串和方向。

            返回：
                (percentage_string, is_up)
                - percentage_string: 如 "25%"
                - is_up: True 表示当期 >= 上期（上升或持平）
            """
            if previous == 0:
                if current > 0:
                    return ("100%", True)
                return ("0%", True)
            rate = abs(current - previous) / previous
            return (f"{round(rate * 100)}%", current >= previous)

        hire_trend, hire_up = _trend_str(new_hires, prev_new_hires)
        dep_trend, dep_up = _trend_str(departures, prev_departures)

        # 构建 KPI 卡片指标列表（供前端逐卡展示）
        metrics = [
            OverviewMetric(
                metric_name="在职人数",
                value=total_headcount,
                unit="人",
                trend="up" if total_headcount > 0 else "flat",
                change_rate=0,
            ),
            OverviewMetric(
                metric_name="本月入职",
                value=new_hires,
                unit="人",
                trend="up" if hire_up else "down",
                change_rate=0,
            ),
            OverviewMetric(
                metric_name="本月离职",
                value=departures,
                unit="人",
                trend="down" if dep_up else "up",  # 离职上升是负面信号，反向映射
                change_rate=0,
            ),
            OverviewMetric(
                metric_name="待审批",
                value=total_pending,
                unit="条",
                trend="flat",
                change_rate=0,
            ),
        ]

        return OverviewResponse(
            total_headcount=total_headcount,
            new_hires_this_month=new_hires,
            departures_this_month=departures,
            turnover_rate=turnover_rate,
            attendance_rate=0,         # 出勤率由 get_attendance_rate 接口单独提供
            open_positions=0,          # 空缺岗位数暂未实现
            pending_approvals=total_pending,
            hr_cost_this_month=0,      # 当月人力成本由 get_hr_cost 接口单独提供
            department_count=dept_count,
            new_hires_trend=hire_trend,
            new_hires_trend_up=hire_up,
            departures_trend=dep_trend,
            departures_trend_up=dep_up,
            metrics=metrics,
        )

    # ------------------------------------------------------------------
    # 8. 最近动态（Recent Activity）
    # ------------------------------------------------------------------

    async def get_recent_activity(self, limit: int = 10) -> RecentActivityResponse:
        """
        构建过去 30 天的 HR 动态时间轴（入职 + 离职 + 请假审批通过）。

        数据来源（三路）：
            1. 近期入职 -- 过去 30 天 hire_date 在范围内的在职员工
            2. 近期离职 -- 过去 30 天 leave_date 在范围内的 LEFT 状态员工
            3. 近期审批通过的请假 -- 过去 30 天 created_at 的已审批请假申请

        ID 错位处理：
            三路数据合并时，为避免 ID 碰撞，入职用原始 id，
            离职 id+100000，审批 id+200000（前端不依赖此 id，仅用于 key）

        排序：
            三路数据合并后按 time 字符串倒序排列，截取前 limit 条

        参数：
            limit -- 返回的最大事件数，默认 10

        返回：
            RecentActivityResponse，包含 items 列表（RecentActivityItem）
            每项包含：id, tag（入职/离职/审批）, content, time, type（success/danger/primary）
        """
        events: list[RecentActivityItem] = []
        today = date.today()
        thirty_days_ago = today - timedelta(days=30)

        # 1. 近期入职事件
        onboard_q = (
            select(Employee.id, Employee.name, Employee.hire_date, Department.name.label("dept_name"))
            .outerjoin(Department, Employee.department_id == Department.id)
            .where(
                Employee.hire_date >= thirty_days_ago,
                Employee.hire_date <= today,
                *self._active_employee_filters(),
            )
            .order_by(Employee.hire_date.desc())
            .limit(limit)
        )
        onboard_result = await self.db.execute(onboard_q)
        for row in onboard_result:
            dept_str = f"，分配至{row.dept_name}" if row.dept_name else ""
            events.append(RecentActivityItem(
                id=row.id,
                tag="入职",
                content=f"{row.name}已完成入职手续{dept_str}",
                time=row.hire_date.strftime("%Y-%m-%d") if row.hire_date else "",
                type="success",  # 入职为正向事件，绿色
            ))

        # 2. 近期离职事件
        leave_q = (
            select(Employee.id, Employee.name, Employee.leave_date)
            .where(
                Employee.status == EmployeeStatus.LEFT.value,
                Employee.leave_date >= thirty_days_ago,
                Employee.leave_date <= today,
            )
            .order_by(Employee.leave_date.desc())
            .limit(limit)
        )
        leave_result = await self.db.execute(leave_q)
        for row in leave_result:
            events.append(RecentActivityItem(
                id=row.id + 100000,  # ID 错位，避免与入职事件 ID 碰撞
                tag="离职",
                content=f"{row.name}已完成离职交接，正式离职",
                time=row.leave_date.strftime("%Y-%m-%d") if row.leave_date else "",
                type="danger",  # 离职为负向事件，红色
            ))

        # 3. 近期审批通过的请假事件
        approved_leave_q = (
            select(
                LeaveRequest.id,
                Employee.name.label("emp_name"),
                LeaveRequest.approved_at,
                LeaveRequest.created_at,
            )
            .join(Employee, LeaveRequest.employee_id == Employee.id)
            .where(
                LeaveRequest.approval_status == LeaveApprovalStatus.approved,
                LeaveRequest.created_at >= datetime(
                    thirty_days_ago.year, thirty_days_ago.month, thirty_days_ago.day
                ),
            )
            .order_by(LeaveRequest.created_at.desc())
            .limit(limit)
        )
        approved_leave_result = await self.db.execute(approved_leave_q)
        for row in approved_leave_result:
            # 优先使用 approved_at，其次使用 created_at
            ts = row.approved_at or row.created_at
            time_str = ts.strftime("%Y-%m-%d %H:%M") if ts else ""
            events.append(RecentActivityItem(
                id=row.id + 200000,  # ID 错位，避免与离职事件 ID 碰撞
                tag="审批",
                content=f"{row.emp_name}的请假申请已通过审批",
                time=time_str,
                type="primary",  # 审批事件，蓝色
            ))

        # 三路数据合并后按时间倒序排列，取前 limit 条
        events.sort(key=lambda e: e.time, reverse=True)
        events = events[:limit]

        return RecentActivityResponse(items=events)

    # ------------------------------------------------------------------
    # 9. 待审批事项（Pending Items）
    # ------------------------------------------------------------------

    async def get_pending_items(
        self,
        user_id: Optional[int] = None,
        limit: int = 10,
    ) -> PendingItemsResponse:
        """
        查询待审批事项列表（待办 + 最近已处理记录）。

        数据来源（三路）：
            1. 待审批的请假申请（LeaveRequest.approval_status == pending）
            2. 待审批的通用审批流事项（ApprovalInstance.status == "pending"）
            3. 最近 5 条已处理的审批流事项（approved/rejected，供管理者了解进度）

        模块标签映射：
            leave → 请假
            overtime → 加班
            recruitment → 招聘
            payroll → 薪酬
            salary_adjust → 调薪
            offboard → 离职
            其他 → 原始 module 字段值

        参数：
            user_id -- 保留参数（当前版本查全员，未按审批人过滤）
            limit   -- 最终返回的最大条数，默认 10

        返回：
            PendingItemsResponse，包含 items 列表（PendingItem）
            每项包含：title, applicant, date, status（待审批/已通过/已驳回）, status_type
        """
        items: list[PendingItem] = []

        # 1. 待审批请假申请（直接来自 LeaveRequest 表）
        leave_q = (
            select(
                LeaveRequest.id,
                Employee.name.label("applicant"),
                LeaveRequest.start_date,
                LeaveRequest.end_date,
                LeaveRequest.created_at,
                LeaveRequest.approval_status,
            )
            .join(Employee, LeaveRequest.employee_id == Employee.id)
            .where(LeaveRequest.approval_status == LeaveApprovalStatus.pending)
            .order_by(LeaveRequest.created_at.desc())
            .limit(limit)
        )
        leave_result = await self.db.execute(leave_q)
        for row in leave_result:
            items.append(PendingItem(
                title=f"请假申请 - {row.start_date.strftime('%m月%d日')}至{row.end_date.strftime('%m月%d日')}",
                applicant=row.applicant,
                date=row.created_at.strftime("%Y-%m-%d") if row.created_at else "",
                status="待审批",
                status_type="warning",  # 黄色警告状态
            ))

        # 2. 待审批的通用审批流事项（ApprovalInstance 表）
        approval_q = (
            select(
                ApprovalInstance.id,
                ApprovalInstance.business_type,
                ApprovalInstance.module,
                ApprovalInstance.created_at,
                Employee.name.label("applicant"),
            )
            .join(Employee, ApprovalInstance.applicant_id == Employee.id)
            .where(ApprovalInstance.status == "pending")
            .order_by(ApprovalInstance.created_at.desc())
            .limit(limit)
        )
        approval_result = await self.db.execute(approval_q)
        # 模块名称中文映射表
        module_labels = {
            "leave": "请假",
            "overtime": "加班",
            "recruitment": "招聘",
            "payroll": "薪酬",
            "salary_adjust": "调薪",
            "offboard": "离职",
        }
        for row in approval_result:
            module_label = module_labels.get(row.module, row.module)  # 未知模块显示原始名
            items.append(PendingItem(
                title=f"{module_label}审批 - {row.business_type}",
                applicant=row.applicant,
                date=row.created_at.strftime("%Y-%m-%d") if row.created_at else "",
                status="待审批",
                status_type="warning",
            ))

        # 3. 最近已处理的审批流事项（供管理者了解近期审批进度，固定取 5 条）
        recent_done_q = (
            select(
                ApprovalInstance.id,
                ApprovalInstance.business_type,
                ApprovalInstance.module,
                ApprovalInstance.updated_at,
                ApprovalInstance.status,
                Employee.name.label("applicant"),
            )
            .join(Employee, ApprovalInstance.applicant_id == Employee.id)
            .where(ApprovalInstance.status.in_(["approved", "rejected"]))
            .order_by(ApprovalInstance.updated_at.desc())
            .limit(5)  # 固定取最近 5 条已处理记录
        )
        recent_done_result = await self.db.execute(recent_done_q)
        for row in recent_done_result:
            module_label = module_labels.get(row.module, row.module)
            status_label = "已通过" if row.status == "approved" else "已驳回"
            status_type = "success" if row.status == "approved" else "danger"  # 绿色/红色
            items.append(PendingItem(
                title=f"{module_label}审批 - {row.business_type}",
                applicant=row.applicant,
                date=row.updated_at.strftime("%Y-%m-%d") if row.updated_at else "",
                status=status_label,
                status_type=status_type,
            ))

        # 截取最终返回条数
        items = items[:limit]

        return PendingItemsResponse(items=items)
