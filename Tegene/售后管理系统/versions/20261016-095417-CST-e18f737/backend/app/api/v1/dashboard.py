"""
管理驾驶舱模块 API 路由 — Dashboard
======================================

本模块负责售后管理系统中「管理驾驶舱」相关的所有 HTTP 接口，挂载前缀为
``/api/v1/dashboard``（由 router.py 直接 import 注册，不使用 _try_include，
以保证此模块始终加载）。

架构说明
--------
驾驶舱是**跨模块数据聚合层**，不属于某一业务模块，而是从多个模块取数后汇总
展示关键指标。主要数据来源：
- Employee / EmployeeContract  — 人数、合同到期
- AttendanceRecord             — 出勤率、考勤异常
- LeaveRequest                 — 今日请假人员
- ApprovalInstance             — 待处理审批
- SalaryRecord / PayrollRecord — HR 成本
- RecruitmentDemand / Resume   — 招聘漏斗

功能分区
--------
1. **概览指标（Overview）**
   - GET /dashboard/overview — 汇总关键 KPI：在职人数、入职/离职、出勤率、HR成本等

2. **专项指标**
   - GET /dashboard/headcount          — 人员分布（按地点/部门/项目）
   - GET /dashboard/hr-cost            — HR 成本趋势（含同比/环比）
   - GET /dashboard/attendance-rate    — 出勤率统计
   - GET /dashboard/project-manpower   — 项目人力配置情况
   - GET /dashboard/turnover           — 离职率及趋势
   - GET /dashboard/recruitment-funnel — 招聘漏斗（各阶段转化率）

3. **动态与待办**
   - GET /dashboard/recent-activity — 近期 HR 动态时间线
   - GET /dashboard/pending-items   — 当前用户待审批 / 待处理事项

4. **钻取接口（Drill-down）**
   - 点击驾驶舱卡片上的数字，进入明细列表
   - GET /dashboard/drill/department-headcount — 部门员工明细
   - GET /dashboard/drill/leave-today          — 今日请假人员明细
   - GET /dashboard/drill/attendance-anomaly   — 今日考勤异常明细
   - GET /dashboard/drill/expiring-contracts   — 即将到期合同明细
   - GET /dashboard/drill/pending-approvals    — 待处理审批明细

公共查询参数（通过 _dimension_params 依赖注入）
-----------------------------------------------
大部分指标接口均接受以下三个可选查询参数：
- dimension  : str  — 统计维度：region（大区）/ project（项目）/ department（部门）
- start_date : date — 起始日期（YYYY-MM-DD），不传则使用服务层默认范围
- end_date   : date — 截止日期（YYYY-MM-DD），不传则使用当前日期

权限说明
--------
- 所有接口均需登录（``Depends(get_current_user)``），无 JWT 返回 401。
- /dashboard/pending-items 会使用 current_user.id 过滤当前用户相关事项。
- 其余接口不做用户级数据隔离（HR / 管理员全量查看）。

服务层对应
----------
- ``app.services.dashboard.DashboardService``（实例化方式：svc = DashboardService(db)）
  - get_headcount() / get_hr_cost() / get_attendance_rate()
  - get_project_manpower() / get_turnover() / get_recruitment_funnel()
  - get_overview() / get_recent_activity() / get_pending_items()

注意：钻取接口（drill/*）直接在路由层执行 SQLAlchemy 查询，不经过 DashboardService，
以减少服务层的复杂度（属于轻量展示型查询）。
"""

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee, EmployeeStatus, EmployeeContract, ContractStatus
from app.models.organization import Department
from app.schemas.dashboard import (
    AttendanceRateResponse,
    DrillAttendanceAnomaly,
    DrillDeptEmployee,
    DrillExpiringContract,
    DrillLeavePerson,
    DrillPendingApproval,
    HeadcountResponse,
    HRCostResponse,
    OverviewResponse,
    PendingItemsResponse,
    ProjectManpowerResponse,
    RecentActivityResponse,
    RecruitmentFunnelResponse,
    TurnoverResponse,
)
from app.services.dashboard import DashboardService

router = APIRouter(
    dependencies=[Depends(require_roles("admin", "hr"))]
)


# ---------------------------------------------------------------------------
# 公共依赖：维度参数注入
# 多个指标接口共用相同的三个查询参数，通过 Depends 统一注入，避免重复声明。
# ---------------------------------------------------------------------------

def _dimension_params(
    dimension: Optional[str] = Query(
        None,
        description="统计维度: region / project / department",
        examples=["region", "project", "department"],
    ),
    start_date: Optional[date] = Query(None, description="起始日期 (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="截止日期 (YYYY-MM-DD)"),
) -> dict:
    """
    公共维度参数依赖函数。

    用途：将 dimension / start_date / end_date 三个查询参数打包为 dict，
    通过 Depends(_dimension_params) 注入到各指标端点，再以 **params 解包
    传给 DashboardService 方法。

    返回
    ----
    dict — {"dimension": ..., "start_date": ..., "end_date": ...}
    """
    return {
        "dimension": dimension,
        "start_date": start_date,
        "end_date": end_date,
    }


# ---------------------------------------------------------------------------
# 专项指标端点
# ---------------------------------------------------------------------------

@router.get(
    "/headcount",
    response_model=HeadcountResponse,
    summary="人员分布",
    description="按地点、部门、项目查看人员分布情况",
)
async def get_headcount(
    params: dict = Depends(_dimension_params),
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> HeadcountResponse:
    """
    GET /dashboard/headcount — 人员分布统计

    用途
    ----
    按指定维度（地点 / 部门 / 项目）统计在职员工分布情况，用于驾驶舱
    「人员分布」饼图或柱状图渲染。仅统计状态为 ACTIVE / ON_PROBATION 的员工。

    查询参数（通过 _dimension_params 注入）
    ----------------------------------------
    - dimension  : str  — 统计维度（region / project / department），默认 department
    - start_date : date — 不适用于此接口（为保持参数一致性保留）
    - end_date   : date — 不适用于此接口（为保持参数一致性保留）

    响应 (200 OK)
    -------------
    HeadcountResponse — 包含：
      - total      : int          — 在职总人数
      - breakdown  : list[dict]   — 各维度分组人数列表
        - label  : str  — 维度标签（部门名 / 地点名 / 项目名）
        - count  : int  — 该组人数
        - ratio  : float — 占比百分比

    权限
    ----
    已登录用户（get_current_user，使用 _current_user 占位，不传入服务）

    对应服务
    --------
    DashboardService(db).get_headcount(dimension, start_date, end_date)
    """
    svc = DashboardService(db)
    return await svc.get_headcount(**params)


@router.get(
    "/hr-cost",
    response_model=HRCostResponse,
    summary="HR成本趋势",
    description="工资+企业社保+公积金+福利成本趋势，含同比/环比",
)
async def get_hr_cost(
    params: dict = Depends(_dimension_params),
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> HRCostResponse:
    """
    GET /dashboard/hr-cost — HR 成本趋势

    用途
    ----
    按月聚合 HR 人力成本（工资 + 企业缴纳社保 + 公积金 + 福利），
    并计算与上月（环比）、去年同月（同比）的变化率。
    用于驾驶舱「HR成本趋势」折线图渲染。

    查询参数（通过 _dimension_params 注入）
    ----------------------------------------
    - dimension  : str  — 统计维度，按部门/地点/项目分组展示
    - start_date : date — 统计起始月份（默认近12个月）
    - end_date   : date — 统计截止月份（默认当月）

    响应 (200 OK)
    -------------
    HRCostResponse — 包含：
      - months       : list[str]   — 月份标签列表（YYYY-MM 格式）
      - total_cost   : list[float] — 各月总人力成本（元）
      - breakdown    : dict        — 按成本类型分组（工资/社保/公积金/福利）
      - mom_change   : float       — 环比变化率（%）
      - yoy_change   : float       — 同比变化率（%）

    权限
    ----
    已登录用户（get_current_user），建议仅 HR / 财务角色查看

    对应服务
    --------
    DashboardService(db).get_hr_cost(dimension, start_date, end_date)
    """
    svc = DashboardService(db)
    return await svc.get_hr_cost(**params)


@router.get(
    "/attendance-rate",
    response_model=AttendanceRateResponse,
    summary="出勤率",
    description="按地点/部门查看出勤率统计",
)
async def get_attendance_rate(
    params: dict = Depends(_dimension_params),
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> AttendanceRateResponse:
    """
    GET /dashboard/attendance-rate — 出勤率统计

    用途
    ----
    按维度统计出勤率（实际出勤天数 / 应出勤天数），用于驾驶舱
    「出勤率」仪表盘或分组柱状图渲染。

    查询参数（通过 _dimension_params 注入）
    ----------------------------------------
    - dimension  : str  — 统计维度（region / department），默认全公司
    - start_date : date — 统计起始日期（默认本月初）
    - end_date   : date — 统计截止日期（默认今日）

    响应 (200 OK)
    -------------
    AttendanceRateResponse — 包含：
      - overall_rate    : float      — 全公司出勤率（%）
      - breakdown       : list[dict] — 各维度出勤率分组
        - label : str   — 维度标签
        - rate  : float — 出勤率（%）
      - anomaly_count   : int        — 考勤异常记录数（迟到/早退/缺勤）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    DashboardService(db).get_attendance_rate(dimension, start_date, end_date)
    """
    svc = DashboardService(db)
    return await svc.get_attendance_rate(**params)


@router.get(
    "/project-manpower",
    response_model=ProjectManpowerResponse,
    summary="项目人力配置",
    description="查看各项目人力需求与配置情况",
)
async def get_project_manpower(
    params: dict = Depends(_dimension_params),
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> ProjectManpowerResponse:
    """
    GET /dashboard/project-manpower — 项目人力配置情况

    用途
    ----
    展示各项目的人力需求数、当前配置数、缺口数，帮助 HR 掌握人力资源
    分配状态。本公司以项目制为核心，此指标尤为重要。

    查询参数（通过 _dimension_params 注入）
    ----------------------------------------
    - dimension  : str  — 不常用，此接口以项目为主维度
    - start_date : date — 查询有效期内的项目（按项目开始日过滤）
    - end_date   : date — 查询有效期内的项目（按项目结束日过滤）

    响应 (200 OK)
    -------------
    ProjectManpowerResponse — 包含：
      - projects : list[dict] — 各项目人力数据
        - project_name  : str  — 项目名称
        - required      : int  — 需求人数
        - assigned      : int  — 已配置人数
        - gap           : int  — 缺口（required - assigned，负值表示超配）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    DashboardService(db).get_project_manpower(dimension, start_date, end_date)
    """
    svc = DashboardService(db)
    return await svc.get_project_manpower(**params)


@router.get(
    "/turnover",
    response_model=TurnoverResponse,
    summary="离职率",
    description="按部门查看离职率及趋势",
)
async def get_turnover(
    params: dict = Depends(_dimension_params),
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> TurnoverResponse:
    """
    GET /dashboard/turnover — 离职率统计

    用途
    ----
    按月统计员工离职率（离职人数 / 期初在职人数），并按维度分组展示，
    用于驾驶舱「离职率趋势」折线图渲染，帮助 HR 识别高流失风险部门。

    查询参数（通过 _dimension_params 注入）
    ----------------------------------------
    - dimension  : str  — 统计维度（department / region）
    - start_date : date — 统计起始月份（默认近12个月）
    - end_date   : date — 统计截止月份（默认当月）

    响应 (200 OK)
    -------------
    TurnoverResponse — 包含：
      - overall_rate  : float      — 全公司综合离职率（%）
      - trend         : list[dict] — 按月离职率趋势
        - month  : str   — 月份（YYYY-MM）
        - rate   : float — 离职率（%）
        - count  : int   — 离职人数
      - breakdown     : list[dict] — 按维度分组离职率

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    DashboardService(db).get_turnover(dimension, start_date, end_date)
    """
    svc = DashboardService(db)
    return await svc.get_turnover(**params)


@router.get(
    "/recruitment-funnel",
    response_model=RecruitmentFunnelResponse,
    summary="招聘漏斗",
    description="简历→筛选→面试→Offer→入职各阶段转化率",
)
async def get_recruitment_funnel(
    params: dict = Depends(_dimension_params),
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> RecruitmentFunnelResponse:
    """
    GET /dashboard/recruitment-funnel — 招聘漏斗转化率

    用途
    ----
    统计招聘各阶段人数及转化率，形成漏斗图数据，帮助 HR 识别招聘瓶颈。
    阶段：收到简历 → 简历通过 → 面试邀约 → 面试通过 → Offer 发出 → 最终入职。

    查询参数（通过 _dimension_params 注入）
    ----------------------------------------
    - dimension  : str  — 统计维度（department / project）
    - start_date : date — 统计起始日期（默认近3个月）
    - end_date   : date — 统计截止日期（默认今日）

    响应 (200 OK)
    -------------
    RecruitmentFunnelResponse — 包含：
      - stages : list[dict] — 漏斗各阶段数据
        - stage_name       : str   — 阶段名称
        - count            : int   — 该阶段人数
        - conversion_rate  : float — 相对上一阶段转化率（%）
      - total_positions    : int   — 统计期内开放职位数
      - avg_time_to_hire   : float — 平均招聘周期（天）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    DashboardService(db).get_recruitment_funnel(dimension, start_date, end_date)
    """
    svc = DashboardService(db)
    return await svc.get_recruitment_funnel(**params)


@router.get(
    "/overview",
    response_model=OverviewResponse,
    summary="综合概览",
    description="汇总关键指标：在职人数、入职/离职、出勤率、招聘、待审批、HR成本等",
)
async def get_overview(
    params: dict = Depends(_dimension_params),
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> OverviewResponse:
    """
    GET /dashboard/overview — 管理驾驶舱综合概览

    用途
    ----
    驾驶舱首页最核心的接口，一次性返回所有关键 KPI 卡片数据。
    前端加载驾驶舱时优先调用此接口，快速呈现全局态势。

    聚合指标包括
    -----------
    - 在职总人数（含本月新入职人数）
    - 本月离职人数及离职率
    - 本月出勤率及考勤异常数
    - 开放招聘需求数及漏斗概况
    - 当前待审批事项总数
    - 当月 HR 成本总额及环比变化
    - 合同到期预警人数（30天内）

    查询参数（通过 _dimension_params 注入）
    ----------------------------------------
    - dimension  : str  — 统计维度（通常首页用全公司视图，不填）
    - start_date : date — 通常不填，服务层默认使用当月
    - end_date   : date — 通常不填，服务层默认使用当日

    响应 (200 OK)
    -------------
    OverviewResponse — 包含所有关键 KPI 字段（见 schemas/dashboard.py）

    权限
    ----
    已登录用户（get_current_user），建议 HR 管理员 / 公司高层查看

    对应服务
    --------
    DashboardService(db).get_overview(dimension, start_date, end_date)
    """
    svc = DashboardService(db)
    return await svc.get_overview(**params)


@router.get(
    "/recent-activity",
    response_model=RecentActivityResponse,
    summary="近期动态",
    description="获取近期入职、离职、审批等HR活动时间线",
)
async def get_recent_activity(
    limit: int = Query(10, ge=1, le=50, description="返回条数"),
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> RecentActivityResponse:
    """
    GET /dashboard/recent-activity — 近期 HR 动态时间线

    用途
    ----
    返回近期发生的 HR 活动事件，用于驾驶舱右侧「动态流」组件。
    事件类型包括：新员工入职、员工离职、审批通过/拒绝等。

    查询参数
    --------
    - limit : int — 返回条数，最少1条，最多50条，默认10条

    响应 (200 OK)
    -------------
    RecentActivityResponse — 包含：
      - activities : list[dict] — 活动列表（按时间倒序）
        - event_type  : str      — 事件类型
        - description : str      — 事件描述，如"张三 已入职，部门：技术部"
        - occurred_at : datetime — 事件时间

    权限
    ----
    已登录用户（get_current_user，使用 _current_user 占位）

    对应服务
    --------
    DashboardService(db).get_recent_activity(limit=limit)
    """
    svc = DashboardService(db)
    return await svc.get_recent_activity(limit=limit)


@router.get(
    "/pending-items",
    response_model=PendingItemsResponse,
    summary="待办事项",
    description="获取当前用户相关的待审批/已审批事项列表",
)
async def get_pending_items(
    limit: int = Query(10, ge=1, le=50, description="返回条数"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> PendingItemsResponse:
    """
    GET /dashboard/pending-items — 当前用户待办事项

    用途
    ----
    获取当前登录用户需要处理的待审批事项（需要本人审批的）及本人发起的
    审批申请（待他人审批）。用于驾驶舱「待办」卡片和侧边栏待办提示。

    与 /drill/pending-approvals 的区别
    ------------------------------------
    - /pending-items    : 个人视角，只看与 current_user 相关的事项，有用户过滤
    - /drill/pending-approvals : 全局视角，所有 pending 状态的审批，无用户过滤

    查询参数
    --------
    - limit : int — 返回条数，最少1条，最多50条，默认10条

    响应 (200 OK)
    -------------
    PendingItemsResponse — 包含：
      - to_approve   : list[dict] — 需要本人审批的事项
      - my_pending   : list[dict] — 本人发起、等待他人审批的事项
      - total_count  : int        — 总待处理数

    权限
    ----
    已登录用户（get_current_user，current_user.id 用于用户过滤）

    对应服务
    --------
    DashboardService(db).get_pending_items(user_id=current_user.id, limit=limit)
    """
    svc = DashboardService(db)
    return await svc.get_pending_items(user_id=current_user.id, limit=limit)


# ---------------------------------------------------------------------------
# 钻取接口（Drill-down）
# 注意：这些接口直接在路由层执行 SQLAlchemy 查询，不经过 DashboardService。
# 原因：属于简单的明细展示查询，逻辑轻量，无需封装进服务层。
# ---------------------------------------------------------------------------


@router.get(
    "/drill/department-headcount",
    response_model=List[DrillDeptEmployee],
    summary="部门人员钻取",
    description="点击某部门人数 → 返回该部门员工列表",
)
async def drill_department_headcount(
    department_id: int = Query(..., description="部门ID"),
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> List[DrillDeptEmployee]:
    """
    GET /dashboard/drill/department-headcount — 部门员工明细钻取

    用途
    ----
    点击驾驶舱「人员分布」图表中某部门的数值后，弹窗展示该部门当前
    在职员工的详细列表（姓名、岗位、状态）。

    查询参数
    --------
    - department_id : int (必填) — 目标部门主键 ID

    查询范围
    --------
    只返回员工管理页默认“在职员工”口径内的员工，
    按姓名升序排列。

    响应 (200 OK)
    -------------
    list[DrillDeptEmployee] — 员工列表，每条包含：
      - id       : int — 员工 ID
      - name     : str — 员工姓名
      - position : str — 岗位名称
      - status   : str — 在职状态枚举值

    权限
    ----
    已登录用户（get_current_user，使用 _current_user 占位）
    """
    q = (
        select(Employee.id, Employee.name, Employee.position, Employee.status)
        .where(
            Employee.department_id == department_id,
            *DashboardService._active_employee_filters(),
        )
        .order_by(Employee.name)
    )
    result = await db.execute(q)
    return [
        DrillDeptEmployee(
            id=row.id,
            name=row.name,
            position=row.position,
            status=row.status,
        )
        for row in result
    ]


@router.get(
    "/drill/leave-today",
    response_model=List[DrillLeavePerson],
    summary="今日请假钻取",
    description="今日请假人员列表",
)
async def drill_leave_today(
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> List[DrillLeavePerson]:
    """
    GET /dashboard/drill/leave-today — 今日请假人员明细

    用途
    ----
    点击驾驶舱「今日请假」数值后，弹窗展示当日所有已批准请假的员工列表，
    包括请假类型和天数。

    查询逻辑
    --------
    - 过滤条件：LeaveRequest.start_date <= 今日 AND end_date >= 今日
    - 只取 approval_status = approved（已审批通过）的请假申请
    - 关联 Employee（取姓名）和 LeaveType（取假期类型名称）
    - 按员工姓名升序排列

    响应 (200 OK)
    -------------
    list[DrillLeavePerson] — 请假人员列表，每条包含：
      - employee_id : int   — 员工 ID
      - name        : str   — 员工姓名
      - leave_type  : str   — 请假类型（如"年假"、"病假"）
      - days        : float — 请假天数

    权限
    ----
    已登录用户（get_current_user，使用 _current_user 占位）
    """
    from app.models.leave import LeaveRequest, LeaveType, ApprovalStatus as LeaveStatus
    today = date.today()
    q = (
        select(
            LeaveRequest.employee_id,
            Employee.name,
            LeaveType.name.label("leave_type_name"),
            LeaveRequest.days,
        )
        .join(Employee, LeaveRequest.employee_id == Employee.id)
        .join(LeaveType, LeaveRequest.leave_type_id == LeaveType.id)
        .where(
            LeaveRequest.start_date <= today,
            LeaveRequest.end_date >= today,
            LeaveRequest.approval_status == LeaveStatus.approved,
        )
        .order_by(Employee.name)
    )
    result = await db.execute(q)
    return [
        DrillLeavePerson(
            employee_id=row.employee_id,
            name=row.name,
            leave_type=row.leave_type_name,
            days=float(row.days),
        )
        for row in result
    ]


@router.get(
    "/drill/attendance-anomaly",
    response_model=List[DrillAttendanceAnomaly],
    summary="考勤异常钻取",
    description="今日考勤异常列表",
)
async def drill_attendance_anomaly(
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> List[DrillAttendanceAnomaly]:
    """
    GET /dashboard/drill/attendance-anomaly — 今日考勤异常明细

    用途
    ----
    点击驾驶舱「考勤异常」数值后，弹窗展示今日所有异常考勤记录。
    异常类型包括：迟到、早退、漏打卡、缺勤。

    查询逻辑
    --------
    - 过滤条件：AttendanceRecord.date = 今日 AND status IN (late, early_leave, missed_clock, absent)
    - 关联 Employee 取姓名
    - 按员工姓名升序排列
    - 发生查询异常时（模型不存在等）返回空列表，不抛出 500 错误

    响应 (200 OK)
    -------------
    list[DrillAttendanceAnomaly] — 异常列表，每条包含：
      - employee_id  : int — 员工 ID
      - name         : str — 员工姓名
      - anomaly_type : str — 异常类型（优先取 anomaly_type 字段，缺则取 status）
      - time         : str — 记录创建时间（HH:MM 格式）

    权限
    ----
    已登录用户（get_current_user，使用 _current_user 占位）

    容错说明
    --------
    查询体用 try/except Exception 包裹，若 AttendanceRecord 模型未导入或
    表不存在，返回空列表而非 500，保证驾驶舱其他卡片正常加载。
    """
    try:
        from app.models.attendance import AttendanceRecord, AttendanceStatus
        today = date.today()
        anomaly_statuses = [
            AttendanceStatus.late.value,
            AttendanceStatus.early_leave.value,
            AttendanceStatus.missed_clock.value,
            AttendanceStatus.absent.value,
        ]
        q = (
            select(
                AttendanceRecord.employee_id,
                Employee.name,
                AttendanceRecord.anomaly_type,
                AttendanceRecord.status,
                AttendanceRecord.created_at,
            )
            .join(Employee, AttendanceRecord.employee_id == Employee.id)
            .where(
                AttendanceRecord.date == today,
                AttendanceRecord.status.in_(anomaly_statuses),
            )
            .order_by(Employee.name)
        )
        result = await db.execute(q)
        items = []
        for row in result:
            # anomaly_type 字段优先（更具描述性），不存在则退回 status 枚举值
            anomaly = row.anomaly_type or row.status
            time_str = row.created_at.strftime("%H:%M") if row.created_at else ""
            items.append(DrillAttendanceAnomaly(
                employee_id=row.employee_id,
                name=row.name,
                anomaly_type=anomaly,
                time=time_str,
            ))
        return items
    except Exception:
        # 考勤模块数据库异常时，返回空列表而不影响驾驶舱整体可用性
        return []


@router.get(
    "/drill/expiring-contracts",
    response_model=List[DrillExpiringContract],
    summary="即将到期合同钻取",
    description="即将到期合同列表",
)
async def drill_expiring_contracts(
    days: int = Query(30, ge=1, le=365, description="提前预警天数"),
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> List[DrillExpiringContract]:
    """
    GET /dashboard/drill/expiring-contracts — 即将到期合同明细

    用途
    ----
    点击驾驶舱「合同到期预警」数值后，弹窗展示指定天数内到期合同的员工列表，
    帮助 HR 及时处理续签事宜。

    查询参数
    --------
    - days : int — 预警天数范围，默认30天，最少1天，最多365天

    查询逻辑
    --------
    - 过滤条件：EmployeeContract.end_date IN [今日, 今日+days天]
    - 只取 ContractStatus.ACTIVE（合同有效中）的合同
    - 只取 ACTIVE / ON_PROBATION 状态的员工（过滤已离职员工的历史合同）
    - 按合同到期日升序排列（最紧迫的排前面）

    响应 (200 OK)
    -------------
    list[DrillExpiringContract] — 到期合同列表，每条包含：
      - employee_id  : int — 员工 ID
      - name         : str — 员工姓名
      - contract_end : str — 合同到期日（YYYY-MM-DD 格式）
      - days_left    : int — 距到期剩余天数

    权限
    ----
    已登录用户（get_current_user，使用 _current_user 占位），建议 HR 角色使用
    """
    today = date.today()
    deadline = today + timedelta(days=days)
    q = (
        select(
            EmployeeContract.employee_id,
            Employee.name,
            EmployeeContract.end_date,
        )
        .join(Employee, EmployeeContract.employee_id == Employee.id)
        .where(
            EmployeeContract.end_date >= today,
            EmployeeContract.end_date <= deadline,
            EmployeeContract.status == ContractStatus.ACTIVE.value,
            Employee.status.in_([EmployeeStatus.ACTIVE.value, EmployeeStatus.ON_PROBATION.value]),
        )
        .order_by(EmployeeContract.end_date)
    )
    result = await db.execute(q)
    return [
        DrillExpiringContract(
            employee_id=row.employee_id,
            name=row.name,
            contract_end=row.end_date.strftime("%Y-%m-%d") if row.end_date else "",
            days_left=(row.end_date - today).days if row.end_date else 0,
        )
        for row in result
    ]


@router.get(
    "/drill/pending-approvals",
    response_model=List[DrillPendingApproval],
    summary="待处理审批钻取",
    description="待处理审批列表",
)
async def drill_pending_approvals(
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
) -> List[DrillPendingApproval]:
    """
    GET /dashboard/drill/pending-approvals — 全局待处理审批明细

    用途
    ----
    点击驾驶舱「待审批」数值后，弹窗展示系统中所有处于 pending 状态
    的审批申请明细，供 HR 管理员查阅全局审批情况。
    注意：此接口为**全局视角**，不按当前用户过滤（与 /pending-items 不同）。

    查询逻辑
    --------
    - 过滤条件：ApprovalInstance.status = 'pending'
    - 关联 Employee（申请人）取姓名
    - 按创建时间降序排列（最新的在前）
    - 最多返回 100 条（避免全量扫描）
    - module_labels 字典将模块英文 key 映射为中文标签

    支持的审批模块
    ---------------
    - leave         → 请假
    - overtime      → 加班
    - recruitment   → 招聘
    - payroll       → 薪酬
    - salary_adjust → 调薪
    - offboard      → 离职

    响应 (200 OK)
    -------------
    list[DrillPendingApproval] — 待审批列表，每条包含：
      - instance_id : int — 审批实例 ID
      - type        : str — 审批类型标签（如"请假 - annual_leave"）
      - applicant   : str — 申请人姓名
      - created_at  : str — 提交时间（YYYY-MM-DD HH:MM 格式）

    权限
    ----
    已登录用户（get_current_user，使用 _current_user 占位），建议 HR 管理员查看
    """
    from app.models.approval import ApprovalInstance
    # 模块英文 key → 中文标签映射（用于前端展示）
    module_labels = {
        "leave": "请假",
        "overtime": "加班",
        "recruitment": "招聘",
        "payroll": "薪酬",
        "salary_adjust": "调薪",
        "offboard": "离职",
    }
    q = (
        select(
            ApprovalInstance.id,
            ApprovalInstance.module,
            ApprovalInstance.business_type,
            ApprovalInstance.created_at,
            Employee.name.label("applicant_name"),
        )
        .join(Employee, ApprovalInstance.applicant_id == Employee.id)
        .where(ApprovalInstance.status == "pending")
        .order_by(ApprovalInstance.created_at.desc())
        .limit(100)
    )
    result = await db.execute(q)
    items = []
    for row in result:
        label = module_labels.get(row.module, row.module)
        # 组合「模块中文名 - 业务类型」作为 type 展示
        type_str = f"{label} - {row.business_type}" if row.business_type else label
        created_str = row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else ""
        items.append(DrillPendingApproval(
            instance_id=row.id,
            type=type_str,
            applicant=row.applicant_name,
            created_at=created_str,
        ))
    return items
