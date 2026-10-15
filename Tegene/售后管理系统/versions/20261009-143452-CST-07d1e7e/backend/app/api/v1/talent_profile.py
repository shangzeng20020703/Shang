"""
人才画像模块 API 路由 — Talent Profile
========================================

本模块负责售后管理系统中「人才画像」相关的所有 HTTP 接口，挂载前缀为
``/api/v1/talent-profile``（由 router.py 注册时指定）。

功能分区
--------
1. **人才搜索（TalentSearch）**
   - 多维度搜索员工：姓名/岗位关键词、技能标签、胜任力最低等级
   - 路径：GET /talent-profile/search
   - 注意：此固定路径必须注册在 /{employee_id} 参数化路径之前，
     否则「search」会被误解析为 employee_id（整数），导致 422 错误

2. **完整人才画像（TalentProfile）**
   - 聚合单名员工的全部人才数据：基本信息、绩效历史、胜任力评估、
     培训记录、IDP 计划、证书、技能、盘点历史等
   - 路径：GET /talent-profile/{employee_id}
   - 属于重型查询，服务层跨多个模块聚合数据

3. **胜任力雷达数据（CompetencyRadar）**
   - 返回员工各维度胜任力评估等级 vs 岗位要求的对比数据
   - 用于前端 ECharts 雷达图渲染
   - 路径：GET /talent-profile/{employee_id}/radar

4. **成长时间线（Timeline）**
   - 返回员工职业生涯中的关键事件（入职、晋升、转岗、培训、获证等）
   - 按时间顺序排列，用于前端时间轴组件
   - 路径：GET /talent-profile/{employee_id}/timeline

权限说明
--------
- 所有接口均需登录（``Depends(get_current_user)``），无 JWT 返回 401。
- 建议 HR / 管理者可查看任意员工画像，员工本人仅可查看自己的。
  当前版本未做细粒度权限控制。

服务层对应
----------
- ``app.services.talent_profile.TalentProfileService``
  - search_talents()   — 多维度人才搜索
  - build_profile()    — 构建完整人才画像（跨模块聚合）
  - get_radar_data()   — 胜任力雷达数据
  - get_timeline()     — 成长时间线
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.schemas.talent_profile import (
    TalentProfile,
    CompetencyRadarItem,
    TimelineEvent,
    TalentSearchResult,
)
from app.services.talent_profile import TalentProfileService

# 路由器标签用于 Swagger UI 分组展示
router = APIRouter(
    tags=["人才画像"],
    dependencies=[Depends(require_roles("admin", "hr"))],
)


# ============================================================
# GET /talent-profile/search 人才搜索
# 注意：此固定路径端点必须在 /{employee_id} 参数化路径之前注册，
# 否则 FastAPI 会将 "search" 字符串尝试解析为整数 employee_id 并返回 422。
# ============================================================

@router.get("/search", response_model=list[TalentSearchResult])
async def search_talents(
    keyword: Optional[str] = Query(default=None, description="搜索关键词(姓名/岗位)"),
    skill: Optional[str] = Query(default=None, description="技能搜索"),
    min_competency: Optional[int] = Query(
        default=None, ge=1, le=5, description="最低胜任力均值"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TalentSearchResult]:
    """
    GET /talent-profile/search — 多维度人才搜索

    用途
    ----
    根据多个条件组合搜索人才库，用于「人才寻访」和「岗位匹配」场景。
    例如：搜索拥有「Python」技能且胜任力均值不低于3的工程师。
    至少需要提供一个搜索条件，否则返回 400 错误（防止全量扫描）。

    查询参数
    --------
    - keyword       : str (可选) — 姓名或岗位关键词模糊匹配，如"张"、"工程师"
    - skill         : str (可选) — 技能标签精确或模糊匹配，如"Python"、"项目管理"
    - min_competency: int (可选) — 胜任力各维度平均等级下限（1-5），
                                   如填3则只返回胜任力均值≥3的员工

    参数约束
    --------
    keyword、skill、min_competency 三者至少提供一个，全部为空返回 400。

    响应 (200 OK)
    -------------
    list[TalentSearchResult] — 匹配的员工列表，每条包含：
      - employee_id     : int   — 员工 ID
      - name            : str   — 姓名
      - position        : str   — 岗位
      - department_name : str   — 部门名称
      - avg_competency  : float — 平均胜任力等级
      - skills          : list  — 技能标签列表

    错误
    ----
    400 — 未提供任何搜索条件

    权限
    ----
    已登录用户（get_current_user），建议 HR / 管理者使用

    对应服务
    --------
    TalentProfileService.search_talents(db, keyword, skill, min_competency)
    """
    if not keyword and not skill and min_competency is None:
        raise HTTPException(
            status_code=400,
            detail="请至少提供一个搜索条件: keyword, skill, min_competency",
        )
    return await TalentProfileService.search_talents(
        db, keyword=keyword, skill=skill, min_competency=min_competency
    )


# ============================================================
# GET /talent-profile/{employee_id} 完整人才画像
# ============================================================

@router.get("/{employee_id}", response_model=TalentProfile)
async def get_talent_profile(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> TalentProfile:
    """
    GET /talent-profile/{employee_id} — 获取员工完整人才画像

    用途
    ----
    聚合指定员工的全部人才数据，构建立体的员工画像，供 HR 和管理者
    做人才决策（晋升、调岗、发展计划制定等）时参考。

    聚合数据来源（跨模块查询）
    --------------------------
    - Employee         : 基本信息（姓名、岗位、部门、司龄）
    - PerformanceEvaluation : 近3年绩效历史及评级趋势
    - EmployeeCompetency    : 胜任力评估结果（各维度等级）
    - TrainingEnrollment    : 参加的培训课程及完成情况
    - IDPPlan               : 当前及历史 IDP 计划
    - EmployeeCertification : 持有证书列表
    - EmployeeSkill         : 技能标签及熟练度
    - TalentReviewEntry     : 历次盘点结果（九宫格位置、关键人才标记）

    路径参数
    --------
    - employee_id : int — 员工主键 ID

    响应 (200 OK)
    -------------
    TalentProfile — 完整人才画像，包含上述所有聚合数据

    错误
    ----
    404 — 员工不存在（服务层抛出 ValueError，路由层转换为 404）

    权限
    ----
    已登录用户（get_current_user）

    性能说明
    --------
    此接口为重型查询，服务层会并行查询多个表。建议前端按需加载，
    非必要时使用 /search 接口代替。

    对应服务
    --------
    TalentProfileService.build_profile(db, employee_id)
    """
    try:
        return await TalentProfileService.build_profile(db, employee_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================
# GET /talent-profile/{employee_id}/radar 胜任力雷达数据
# ============================================================

@router.get("/{employee_id}/radar", response_model=list[CompetencyRadarItem])
async def get_talent_radar(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[CompetencyRadarItem]:
    """
    GET /talent-profile/{employee_id}/radar — 获取员工胜任力雷达数据

    用途
    ----
    返回员工各胜任力维度的「实际评估等级」与「岗位要求等级」对比数据，
    专门用于前端 ECharts 雷达图（RadarChart）渲染。

    路径参数
    --------
    - employee_id : int — 员工主键 ID

    响应 (200 OK)
    -------------
    list[CompetencyRadarItem] — 雷达图数据点列表，每条包含：
      - competency_name : str   — 胜任力维度名称（如"领导力"、"技术能力"）
      - actual_score    : float — 实际评估等级（1-5）
      - required_score  : float — 岗位要求等级（1-5）
      - gap             : float — 差距（required - actual，负值表示超出要求）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    TalentProfileService.get_radar_data(db, employee_id)
    """
    return await TalentProfileService.get_radar_data(db, employee_id)


# ============================================================
# GET /talent-profile/{employee_id}/timeline 成长时间线
# ============================================================

@router.get("/{employee_id}/timeline", response_model=list[TimelineEvent])
async def get_talent_timeline(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[TimelineEvent]:
    """
    GET /talent-profile/{employee_id}/timeline — 获取员工成长时间线

    用途
    ----
    返回员工在公司的职业发展轨迹，包含所有关键里程碑事件，
    用于前端「成长时间线」组件（el-timeline）渲染。

    时间线事件类型（event_type）
    ----------------------------
    - onboard       : 入职
    - promotion     : 晋升（职级提升）
    - transfer      : 调岗（岗位/部门变动）
    - training      : 培训完成
    - certification : 获得证书
    - idp_milestone : IDP 重要节点（计划生效/完成）
    - review        : 参与人才盘点
    - performance   : 绩效评定结果

    路径参数
    --------
    - employee_id : int — 员工主键 ID

    响应 (200 OK)
    -------------
    list[TimelineEvent] — 时间线事件列表（按时间倒序排列），每条包含：
      - event_type  : str  — 事件类型（见上方枚举）
      - event_date  : date — 事件发生日期
      - title       : str  — 事件标题
      - description : str  — 事件描述（可选）

    权限
    ----
    已登录用户（get_current_user）

    对应服务
    --------
    TalentProfileService.get_timeline(db, employee_id)
    """
    return await TalentProfileService.get_timeline(db, employee_id)
