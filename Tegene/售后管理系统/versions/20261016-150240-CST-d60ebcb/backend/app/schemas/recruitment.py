"""
招聘管理模块 - Pydantic Schemas
================================

模块名称: recruitment.py
所属系统: 售后管理系统 - 招聘管理模块

作用:
    定义招聘全流程（招聘漏斗）的请求/响应数据结构。
    招聘流程共 10 个阶段，形成漏斗模型：
      1. 招聘渠道（RecruitmentChannel）— 配置简历来源渠道
      2. 招聘需求（RecruitmentDemand）— 部门提出用人需求并审批
      3. 简历收集（Resume）— 候选人简历录入与管理
      4. 简历筛选（ResumeScreeningRequest）— HR 初步筛选推荐/不合适
      5. 面试安排（Interview）— 支持多轮面试（round 字段）
      6. 面试评价（InterviewEvaluation）— 面试官填写评分与建议
      7. Offer 发放（Offer）— 制作并发送 Offer，含审批流程
      8. 候选人确认（OfferCandidateConfirmRequest）— 接受或拒绝 Offer
      9. 入职管理（OnboardingRecord）— 跟进入职准备事项
      10. 内推管理（InternalReferral）— 员工内推奖励跟踪

数据流向:
    前端表单 → *Create/*Update Schema（请求验证）
         ↓
    service 层（业务逻辑：渠道效果统计、漏斗转化率计算等）
         ↓
    ORM Model 持久化到 PostgreSQL
         ↓
    *Out Schema（响应序列化）→ 前端展示

被调用的 API 端点 (api/v1/endpoints/recruitment.py):
    招聘渠道:
        POST/GET/PUT/DELETE  /recruitment/channels
    招聘需求:
        POST                 /recruitment/demands
        POST                 /recruitment/demands/{id}/approve
    简历:
        POST/GET             /recruitment/resumes
        POST                 /recruitment/resumes/{id}/screen
        POST                 /recruitment/resumes/bulk-screen
        DELETE               /recruitment/resumes/bulk-delete
    面试:
        POST                 /recruitment/interviews
        POST                 /recruitment/interviews/{id}/schedule
        POST                 /recruitment/interviews/{id}/cancel
        POST                 /recruitment/interviews/{id}/reschedule
    面试评价:
        POST                 /recruitment/interviews/{id}/evaluations
    Offer:
        POST/GET             /recruitment/offers
        POST                 /recruitment/offers/{id}/approve
        POST                 /recruitment/offers/{id}/send
        POST                 /recruitment/offers/{id}/candidate-confirm
        POST                 /recruitment/offers/bulk-action
    内推:
        POST/GET             /recruitment/referrals
        POST                 /recruitment/referrals/bulk-mark-paid
    入职:
        POST/GET             /recruitment/onboarding
    统计分析:
        GET                  /recruitment/stats/funnel
        GET                  /recruitment/stats/channels

依赖:
    - pydantic v2: BaseModel, ConfigDict, EmailStr, Field
    - 关联 models/recruitment.py: RecruitmentChannel, RecruitmentDemand, Resume,
      Interview, InterviewEvaluation, Offer, InternalReferral, OnboardingRecord
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ===========================================================================
# RecruitmentChannel 招聘渠道
# ===========================================================================

class RecruitmentChannelBase(BaseModel):
    """
    招聘渠道基础 Schema

    招聘渠道（RecruitmentChannel）是简历来源的分类配置，
    记录从哪些渠道（如 BOSS直聘/猎头/内推/现场招募）收到候选人简历。
    渠道数据用于统计渠道投入产出比（ChannelEffectivenessOut）。

    字段说明:
        name:         渠道名称，例如 "BOSS直聘"、"51job"、"内部推荐"
        channel_type: 渠道大类，枚举: online（线上）/ headhunter（猎头）/
                      internal（内推）/ onsite（现场招募）
        is_active:    是否启用，停用后不影响历史简历记录
        config_json:  渠道特殊配置（如 API 对接参数、渠道费用等），JSON 格式
    """
    name: str = Field(..., max_length=50, description="渠道名称")
    channel_type: str = Field(
        ..., max_length=20,
        description="渠道类型: online/headhunter/internal/onsite",
    )  # online=线上平台, headhunter=猎头, internal=内推, onsite=现场招募
    is_active: bool = Field(default=True, description="是否启用")
    config_json: Optional[dict[str, Any]] = Field(default=None, description="渠道配置")  # 扩展配置，如接口密钥


class RecruitmentChannelCreate(RecruitmentChannelBase):
    """
    创建招聘渠道的请求体。

    对应 API: POST /recruitment/channels
    字段继承自 RecruitmentChannelBase，无额外字段。
    """
    pass


class RecruitmentChannelUpdate(BaseModel):
    """
    更新招聘渠道的请求体（PATCH 语义，所有字段可选）。

    对应 API: PUT /recruitment/channels/{id}
    """
    name: Optional[str] = Field(default=None, max_length=50)
    channel_type: Optional[str] = Field(default=None, max_length=20)
    is_active: Optional[bool] = None
    config_json: Optional[dict[str, Any]] = None


class RecruitmentChannelOut(RecruitmentChannelBase):
    """
    返回给前端的渠道数据（ORM 序列化）。

    from_attributes=True 支持从 SQLAlchemy ORM 对象直接转换。
    """
    model_config = ConfigDict(from_attributes=True)
    id: int  # 渠道数据库主键


# ===========================================================================
# RecruitmentPosition 职位字典
# ===========================================================================

class RecruitmentPositionBase(BaseModel):
    position_name: str = Field(..., max_length=100, description="职位名称")
    job_category: Optional[str] = Field(default=None, max_length=50, description="职位类别")
    job_level: Optional[str] = Field(default=None, max_length=50, description="职位等级")
    description: Optional[str] = Field(default=None, description="岗位描述")
    is_active: bool = Field(default=True, description="是否启用")


class RecruitmentPositionCreate(RecruitmentPositionBase):
    pass


class RecruitmentPositionUpdate(BaseModel):
    position_name: Optional[str] = Field(default=None, max_length=100)
    job_category: Optional[str] = Field(default=None, max_length=50)
    job_level: Optional[str] = Field(default=None, max_length=50)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class RecruitmentPositionOut(RecruitmentPositionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# RecruitmentDemand 招聘需求
# ===========================================================================

class RecruitmentDemandBase(BaseModel):
    """
    招聘需求基础 Schema

    招聘需求（RecruitmentDemand）是由用人部门提出的用人申请，
    经 HR 或管理层审批后，正式开始招募流程。

    字段说明:
        department_id:      用人部门 ID，关联 departments 表
        position_name:      招聘职位名称，例如 "高级机器人工程师"
        headcount:          招聘人数（≥1），表示本次需求计划录用的候选人数量
                            例如 headcount=3 表示需要录用3名候选人
        demand_type:        需求类型，例如 "补人"（原岗位离职补充）或 "增编"（新增岗位）
        urgency:            紧急程度，枚举: 紧急 / 普通 / 储备
                            紧急需求优先排期面试，储备需求可弹性处理
        reason:             招聘原因说明（如 "XX同事离职，需补充人力"）
        requirements_desc:  岗位要求详细描述（学历、经验、技能等）
        salary_range_min/max: 薪资区间（元/月），用于候选人筛选和 Offer 参考
        location_id:        工作地点 ID，关联 locations 表
    """
    department_id: int = Field(..., description="部门ID")
    position_name: str = Field(..., max_length=100, description="职位名称")
    headcount: int = Field(default=1, ge=1, description="招聘人数")  # 本次需要录用的总人数
    demand_type: str = Field(
        default="增编", max_length=20, description="需求类型: 补人/增编"
    )  # 补人=替换离职员工, 增编=新增编制
    urgency: str = Field(default="普通", max_length=20, description="紧急程度: 紧急/普通/储备")
    reason: Optional[str] = Field(default=None, description="招聘原因")
    requirements_desc: Optional[str] = Field(default=None, description="岗位要求描述")
    salary_range_min: Optional[Decimal] = Field(
        default=None, ge=0, description="薪资下限(元)"
    )  # 薪资区间下限（月薪，元）
    salary_range_max: Optional[Decimal] = Field(
        default=None, ge=0, description="薪资上限(元)"
    )  # 薪资区间上限（月薪，元）
    location_id: Optional[int] = Field(default=None, description="工作地点ID")


class RecruitmentDemandCreate(RecruitmentDemandBase):
    """
    创建招聘需求的请求体。

    对应 API: POST /recruitment/demands

    提交后状态默认为 pending（待审批）。
    requester_id 由服务端从 JWT token 中获取（当前登录用户），无需前端传入。
    """
    pass


class RecruitmentDemandUpdate(BaseModel):
    """
    更新招聘需求的请求体（PATCH 语义）。

    对应 API: PUT /recruitment/demands/{id}

    注意：status 字段在此处可修改，但状态变更通常通过专用审批接口完成。
    """
    department_id: Optional[int] = None
    position_name: Optional[str] = Field(default=None, max_length=100)
    headcount: Optional[int] = Field(default=None, ge=1)
    demand_type: Optional[str] = Field(default=None, max_length=20)
    urgency: Optional[str] = Field(default=None, max_length=20)
    reason: Optional[str] = None
    requirements_desc: Optional[str] = None
    salary_range_min: Optional[Decimal] = Field(default=None, ge=0)
    salary_range_max: Optional[Decimal] = Field(default=None, ge=0)
    location_id: Optional[int] = None
    status: Optional[str] = Field(default=None, max_length=20)  # 手动修改状态（谨慎使用）


class RecruitmentDemandOut(RecruitmentDemandBase):
    """
    返回给前端的招聘需求数据（ORM 序列化）。

    字段说明:
        id:              需求记录主键
        requester_id:    提需求的员工 ID（创建时从 JWT 获取）
        approval_status: 审批状态（pending/已通过/已拒绝）
        status:          招聘状态（招聘中/已完成/已取消）
        created_at:      需求创建时间
        updated_at:      最后更新时间
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    requester_id: int       # 提需求的员工 ID
    approval_status: str    # 审批状态：pending | 已通过 | 已拒绝
    status: str             # 招聘状态：招聘中 | 已完成 | 已取消
    created_at: datetime
    updated_at: datetime


class DepartmentHeadcountOut(BaseModel):
    """部门编制余额，用于招聘需求发起前校验。"""

    department_id: int = Field(..., description="部门ID")
    department_name: str = Field(..., description="部门名称")
    quota: int = Field(0, ge=0, description="编制上限")
    onboard_count: int = Field(0, ge=0, description="在职/试用人数")
    reserved_count: int = Field(0, ge=0, description="未关闭招聘需求占用人数")
    available_count: int = Field(0, description="可用编制余额")
    is_sufficient: bool = Field(False, description="是否满足本次需求")
    requested_count: int = Field(0, ge=0, description="本次申请人数")


class RecruitmentDemandBusinessSubmitOut(BaseModel):
    """移动端用人部门发起招聘需求后的结果。"""

    demand: RecruitmentDemandOut
    headcount: DepartmentHeadcountOut
    approval_message: str = Field(..., description="审批流转说明")


class DemandPublishRequest(BaseModel):
    """HR 将已审批招聘需求发布到招聘渠道。"""

    channel_id: int = Field(..., description="招聘渠道ID")
    external_job_id: Optional[str] = Field(default=None, max_length=100, description="外部平台职位ID")
    publish_payload: Optional[dict[str, Any]] = Field(default=None, description="覆盖或补充的发布参数")


class DemandPublishOut(BaseModel):
    """岗位发布结果。"""

    demand_id: int
    channel_id: int
    channel_name: str
    status: str
    external_job_id: Optional[str] = None
    message: str
    request_payload: dict[str, Any] = Field(default_factory=dict)
    response_payload: Optional[dict[str, Any]] = None


class DemandApprovalRequest(BaseModel):
    """
    招聘需求审批请求体。

    对应 API: POST /recruitment/demands/{id}/approve

    由 HR 或管理层审批招聘需求，审批通过后才能开始收集简历。

    字段说明:
        approval_status: 审批结果，枚举: "已通过" / "已拒绝"
        comment:         审批备注（如拒绝原因或通过条件说明）
    """
    approval_status: str = Field(
        ..., description="审批结果: 已通过/已拒绝"
    )  # 已通过 = 开放招聘，已拒绝 = 驳回需求
    comment: Optional[str] = Field(default=None, description="审批备注")


# ===========================================================================
# Resume 简历
# ===========================================================================

class ResumeBase(BaseModel):
    """
    简历基础 Schema

    简历（Resume）是候选人的基本信息记录，与招聘需求关联。
    简历进入系统后，HR 对其进行初筛（推荐/不合适），
    通过初筛的候选人进入面试环节。

    字段说明:
        demand_id:        关联的招聘需求 ID（此简历针对哪个职位）
        channel_id:       来源渠道 ID（从哪个渠道获得此简历）
        candidate_name:   候选人姓名
        phone:            候选人手机号（用于联系和去重）
        email:            候选人电子邮箱（EmailStr 格式校验）
        gender:           性别
        birth_date:       出生日期（用于计算年龄）
        education:        最高学历，例如 "本科"、"硕士"、"大专"
        school:           毕业院校
        major:            所学专业
        work_years:       工作年限（≥0）
        current_company:  当前就职公司
        current_position: 当前职位
        expected_salary:  期望薪资（元/月）
        resume_file_url:  简历文件的存储 URL（如 OSS 链接）
        source:           来源备注（如 "2024春季校园招聘"）
        referrer_id:      内推人员工 ID（如果是内推来源）
    """
    demand_id: int = Field(..., description="招聘需求ID")
    channel_id: Optional[int] = Field(default=None, description="渠道ID")
    candidate_name: str = Field(..., max_length=50, description="候选人姓名")
    phone: str = Field(..., max_length=20, description="手机号")
    email: Optional[EmailStr] = Field(default=None, description="电子邮箱")  # Pydantic 自动校验 Email 格式
    gender: Optional[str] = Field(default=None, max_length=10, description="性别")
    birth_date: Optional[date] = Field(default=None, description="出生日期")
    education: Optional[str] = Field(default=None, max_length=20, description="学历")
    school: Optional[str] = Field(default=None, max_length=100, description="毕业院校")
    major: Optional[str] = Field(default=None, max_length=100, description="专业")
    work_years: Optional[int] = Field(default=None, ge=0, description="工作年限")  # ≥0年
    current_company: Optional[str] = Field(
        default=None, max_length=100, description="当前公司"
    )
    current_position: Optional[str] = Field(
        default=None, max_length=100, description="当前职位"
    )
    expected_salary: Optional[Decimal] = Field(
        default=None, ge=0, description="期望薪资(元)"
    )
    resume_file_url: Optional[str] = Field(
        default=None, max_length=500, description="简历文件URL"
    )  # 存储在 OSS/本地的简历文件访问链接
    source: Optional[str] = Field(default=None, max_length=50, description="简历来源备注")
    referrer_id: Optional[int] = Field(default=None, description="内推人ID")  # 内推来源时填写


class ResumeCreate(ResumeBase):
    """
    录入候选人简历的请求体。

    对应 API: POST /recruitment/resumes

    简历创建后，初始筛选状态为 "待筛选"，
    HR 需通过 /resumes/{id}/screen 进行初筛操作。
    """
    pass


class ResumeUpdate(BaseModel):
    """
    更新候选人简历的请求体（PATCH 语义）。

    对应 API: PUT /recruitment/resumes/{id}

    所有字段可选，允许逐字段修改候选人信息。
    筛选状态修改请使用专用的筛选接口。
    """
    demand_id: Optional[int] = None
    channel_id: Optional[int] = None
    candidate_name: Optional[str] = Field(default=None, max_length=50)
    phone: Optional[str] = Field(default=None, max_length=20)
    email: Optional[EmailStr] = None
    gender: Optional[str] = Field(default=None, max_length=10)
    birth_date: Optional[date] = None
    education: Optional[str] = Field(default=None, max_length=20)
    school: Optional[str] = Field(default=None, max_length=100)
    major: Optional[str] = Field(default=None, max_length=100)
    work_years: Optional[int] = Field(default=None, ge=0)
    current_company: Optional[str] = Field(default=None, max_length=100)
    current_position: Optional[str] = Field(default=None, max_length=100)
    expected_salary: Optional[Decimal] = Field(default=None, ge=0)
    resume_file_url: Optional[str] = Field(default=None, max_length=500)
    source: Optional[str] = Field(default=None, max_length=50)
    referrer_id: Optional[int] = None


class ResumeOut(ResumeBase):
    """
    返回给前端的简历数据（ORM 序列化）。

    字段说明:
        id:               简历数据库主键
        screening_status: 简历筛选状态（待筛选 / 推荐 / 不合适）
        screening_note:   筛选备注（HR 备注，如 "学历不符合要求"）
        talent_pool:      是否加入人才库（不合适但有潜力的候选人可加入备用）
        created_at:       简历录入时间
        updated_at:       最后更新时间
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    screening_status: str         # 待筛选 | 推荐 | 不合适
    screening_note: Optional[str] = None  # HR 初筛备注
    talent_pool: bool = False     # 是否存入人才库，用于未来主动触达
    created_at: datetime
    updated_at: datetime


class ResumeScreeningRequest(BaseModel):
    """
    简历筛选操作请求体（HR 初筛）。

    对应 API: POST /recruitment/resumes/{id}/screen

    HR 对候选人简历进行初步评估，决定是否推进至面试阶段。
    "推荐"的简历可以安排面试；"不合适"的简历归档，可选是否加入人才库。

    字段说明:
        screening_status: 筛选结果，枚举: "推荐" / "不合适"
        screening_note:   筛选备注（记录判断依据，供后续参考）
    """
    screening_status: str = Field(
        ..., description="筛选结果: 推荐/不合适"
    )  # 推荐 = 进入面试流程, 不合适 = 归档
    screening_note: Optional[str] = Field(default=None, description="筛选备注")


class ResumeSearchParams(BaseModel):
    """
    简历搜索参数（用于前端高级搜索）。

    对应 API: GET /recruitment/resumes（查询参数）

    字段说明:
        keyword:          关键词模糊匹配（候选人姓名 / 毕业院校 / 当前公司）
        demand_id:        按招聘需求筛选
        channel_id:       按渠道筛选
        screening_status: 按筛选状态筛选（待筛选/推荐/不合适）
        education:        按最高学历筛选
        work_years_min/max: 工作年限范围筛选
    """
    keyword: Optional[str] = Field(default=None, description="关键词(姓名/学校/公司)")  # LIKE %keyword%
    demand_id: Optional[int] = None
    channel_id: Optional[int] = None
    screening_status: Optional[str] = None
    education: Optional[str] = None
    work_years_min: Optional[int] = Field(default=None, ge=0)  # 最低工作年限
    work_years_max: Optional[int] = Field(default=None, ge=0)  # 最高工作年限


# ===========================================================================
# Interview 面试
# ===========================================================================

class InterviewBase(BaseModel):
    """
    面试基础 Schema

    面试（Interview）支持多轮面试机制，通过 round 字段区分轮次。
    典型的面试流程（最多5轮）：
      第1轮: HR初筛（视频/电话）
      第2轮: 技术面（技术负责人主持）
      第3轮: 主管面（用人部门主管）
      第4轮: 总经理面（高级岗位）
      第5轮: 背景调查（特殊岗位）

    同一候选人（同一简历 resume_id）可有多条 Interview 记录，
    每条记录对应一个轮次（round 值不同）。

    字段说明:
        resume_id:      关联简历 ID（哪个候选人参加面试）
        demand_id:      关联招聘需求 ID（针对哪个职位的面试）
        round:          面试轮次（1-5），同一候选人多轮面试时递增
        interview_type: 面试类型描述，如 "HR初筛"、"技术面"、"主管面"、"总经理面"
        interviewer_id: 主面试官的员工 ID
        scheduled_time: 计划面试时间（datetime）
        location:       线下面试地点（如 "深圳总部3楼会议室"）
        meeting_url:    线上面试链接（腾讯会议/Zoom 等）
    """
    resume_id: int = Field(..., description="简历ID")
    demand_id: int = Field(..., description="招聘需求ID")
    round: int = Field(default=1, ge=1, le=5, description="面试轮次")  # 轮次1-5，单个候选人通常不超过4轮
    interview_type: str = Field(
        ..., max_length=20, description="面试类型: HR初筛/技术面/主管面/总经理面"
    )
    interviewer_id: Optional[int] = Field(default=None, description="面试官ID")
    scheduled_time: Optional[datetime] = Field(default=None, description="计划面试时间")
    location: Optional[str] = Field(default=None, max_length=200, description="面试地点")  # 线下地点
    meeting_url: Optional[str] = Field(
        default=None, max_length=500, description="线上面试链接"
    )  # 线上面试 URL


class InterviewCreate(InterviewBase):
    """
    创建面试记录的请求体。

    对应 API: POST /recruitment/interviews

    创建后状态默认为 scheduled，需通过 /schedule 端点绑定具体时间和面试官。
    """
    pass


class InterviewUpdate(BaseModel):
    """
    更新面试记录的请求体（PATCH 语义）。

    对应 API: PUT /recruitment/interviews/{id}

    字段说明:
        actual_time: 实际面试开始时间（可与计划时间不同）
        status:      面试状态（scheduled/conducted/cancelled）
    """
    round: Optional[int] = Field(default=None, ge=1, le=5)
    interview_type: Optional[str] = Field(default=None, max_length=20)
    interviewer_id: Optional[int] = None
    scheduled_time: Optional[datetime] = None
    actual_time: Optional[datetime] = None   # 实际发生时间，面试完成后填写
    location: Optional[str] = Field(default=None, max_length=200)
    meeting_url: Optional[str] = Field(default=None, max_length=500)
    status: Optional[str] = Field(default=None, max_length=20)


class InterviewOut(InterviewBase):
    """
    返回给前端的面试记录数据（ORM 序列化）。

    字段说明:
        id:             面试记录主键
        actual_time:    实际面试时间（与 scheduled_time 可能不同）
        status:         当前状态（scheduled/conducted/cancelled）
        cancel_reason:  取消原因（仅 status=cancelled 时有值）
        created_at:     记录创建时间
        has_evaluation: 是否已提交面试评价（True = 面试官已填写评分）
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    actual_time: Optional[datetime] = None    # 实际面试时间
    status: str                               # scheduled | conducted | cancelled
    cancel_reason: Optional[str] = None       # 取消时的原因说明
    created_at: datetime
    has_evaluation: bool = False              # 标记面试官是否已提交评价


class InterviewScheduleRequest(BaseModel):
    """
    面试排期请求体（确定面试时间和面试官）。

    对应 API: POST /recruitment/interviews/{id}/schedule

    创建面试记录后，通过此接口绑定具体时间、面试官和地点，
    确认后可触发通知（发短信/邮件给候选人和面试官）。

    字段说明:
        scheduled_time: 面试时间（datetime，含时区）
        interviewer_id: 面试官员工 ID（必填）
        location:       线下面试地点
        meeting_url:    线上面试链接
        notify:         是否发送通知消息给候选人和面试官，默认 True
    """
    scheduled_time: datetime = Field(..., description="面试时间")
    interviewer_id: int = Field(..., description="面试官ID")
    location: Optional[str] = Field(default=None, max_length=200, description="面试地点")
    meeting_url: Optional[str] = Field(
        default=None, max_length=500, description="线上面试链接"
    )
    notify: bool = Field(default=True, description="是否发送通知")  # True 时触发消息通知


class InterviewCancelRequest(BaseModel):
    """
    面试取消请求体。

    对应 API: POST /recruitment/interviews/{id}/cancel

    字段说明:
        cancel_reason: 取消原因（必填，如 "候选人临时有事"、"职位需求变化"）
        notify:        是否发送取消通知给候选人
    """
    cancel_reason: str = Field(..., description="取消原因")  # 必填，记录取消原因
    notify: bool = Field(default=True, description="是否发送通知")


class InterviewRescheduleRequest(BaseModel):
    """
    面试改约请求体（修改已排期的面试时间）。

    对应 API: POST /recruitment/interviews/{id}/reschedule

    改约时需记录原因，系统会先取消原定时间，再设置新时间。
    如果 notify=True，会向候选人发送改约通知。

    字段说明:
        scheduled_time: 新的面试时间
        location:       新的面试地点（可选）
        meeting_url:    新的线上链接（可选）
        cancel_reason:  改约原因（必填）
        notify:         是否发送改约通知
    """
    scheduled_time: datetime = Field(..., description="新面试时间")
    location: Optional[str] = Field(default=None, max_length=200)
    meeting_url: Optional[str] = Field(default=None, max_length=500)
    cancel_reason: str = Field(..., description="改约原因")  # 必填，说明为何改约
    notify: bool = Field(default=True, description="是否发送通知")


# ===========================================================================
# InterviewEvaluation 面试评价
# ===========================================================================

class InterviewEvaluationBase(BaseModel):
    """
    面试评价基础 Schema

    面试评价（InterviewEvaluation）由面试官在面试结束后填写，
    对候选人的多个维度进行评分，并给出最终录用建议。

    评分维度（均为 1-10 分）:
        professional_score:  专业能力（技术知识、岗位技能等）
        communication_score: 沟通能力（表达清晰度、理解能力等）
        culture_fit_score:   文化契合度（与公司价值观/团队文化匹配度）
        overall_score:       综合评分（面试官对候选人的整体印象分）

    字段说明:
        interview_id:     关联的面试记录 ID
        overall_result:   最终建议，枚举: "推荐录用" / "待定" / "不推荐"
        strengths:        候选人突出优势描述
        weaknesses:       候选人不足之处描述
        comments:         其他备注或综合评语
        has_skill_test:   是否进行了技能测试（如编程测试、操作实操）
        skill_test_score: 技能测试得分（0-100，has_skill_test=True 时填写）
    """
    interview_id: int = Field(..., description="面试ID")
    professional_score: Optional[int] = Field(
        default=None, ge=1, le=10, description="专业能力评分(1-10)"
    )  # 1=非常差, 10=非常优秀
    communication_score: Optional[int] = Field(
        default=None, ge=1, le=10, description="沟通能力评分(1-10)"
    )
    culture_fit_score: Optional[int] = Field(
        default=None, ge=1, le=10, description="文化契合度评分(1-10)"
    )
    overall_score: Optional[int] = Field(
        default=None, ge=1, le=10, description="综合评分(1-10)"
    )
    overall_result: str = Field(
        default="待定", description="综合评价: 推荐录用/待定/不推荐"
    )  # 面试官最终建议：推荐录用 | 待定 | 不推荐
    strengths: Optional[str] = Field(default=None, description="优势")    # 候选人亮点
    weaknesses: Optional[str] = Field(default=None, description="不足")   # 候选人短板
    comments: Optional[str] = Field(default=None, description="备注/评语")  # 补充说明
    has_skill_test: bool = Field(default=False, description="是否进行技能测试")  # 是否有技术测试环节
    skill_test_score: Optional[Decimal] = Field(
        default=None, ge=0, le=100, description="技能测试分数"
    )  # 技术/技能笔试/实操得分，0-100


class InterviewEvaluationCreate(InterviewEvaluationBase):
    """
    创建面试评价的请求体（面试官提交）。

    对应 API: POST /recruitment/interviews/{id}/evaluations

    interviewer_id 由服务端从 JWT token 获取当前登录用户，无需传入。
    """
    pass


class InterviewEvaluationOut(InterviewEvaluationBase):
    """
    返回给前端的面试评价数据（ORM 序列化）。

    字段说明:
        id:             评价记录主键
        interviewer_id: 提交评价的面试官员工 ID（服务端自动记录）
        created_at:     评价提交时间
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    interviewer_id: int   # 评价提交人（面试官）的员工 ID
    created_at: datetime


# ===========================================================================
# Offer
# ===========================================================================

class OfferBase(BaseModel):
    """
    Offer 基础 Schema

    Offer（录用通知书）是给候选人的正式录用邀请，包含薪资待遇、试用期等条款。
    Offer 创建后需经过内部审批，审批通过后才能发送给候选人。

    字段说明:
        resume_id:               关联简历 ID（向哪位候选人发 Offer）
        demand_id:               关联招聘需求 ID（针对哪个职位）
        position_name:           录用职位名称
        department_id:           录用部门 ID
        location_id:             工作地点 ID
        salary_base:             基本月薪（元）
        salary_total:            综合年薪（元，含年终奖等）
        probation_months:        试用期月数（0-6，0表示无试用期）
        probation_salary_ratio:  试用期薪资比例（0-1），试用期月薪 = salary_base × ratio
                                 默认 0.80，即试用期薪资为正式薪资的 80%
        start_date:              预计入职日期
        background_check_required: 是否需要背景调查（通过后才能正式入职）
    """
    resume_id: int = Field(..., description="简历ID")
    demand_id: int = Field(..., description="招聘需求ID")
    position_name: str = Field(..., max_length=100, description="录用职位名称")
    department_id: int = Field(..., description="部门ID")
    location_id: Optional[int] = Field(default=None, description="工作地点ID")
    salary_base: Decimal = Field(..., ge=0, description="基本薪资(元/月)")      # 基本月薪
    salary_total: Decimal = Field(..., ge=0, description="综合年薪(元)")         # 年薪总包
    probation_months: int = Field(default=3, ge=0, le=6, description="试用期月数")  # 默认3个月试用期
    probation_salary_ratio: Decimal = Field(
        default=Decimal("0.80"), ge=0, le=1, description="试用期薪资比例"
    )  # 试用期薪资系数，默认0.80（试用期薪资=月薪×80%）
    start_date: Optional[date] = Field(default=None, description="预计入职日期")
    background_check_required: bool = Field(
        default=False, description="是否需要背景调查"
    )  # True 时候选人入职前需完成背景调查


class OfferCreate(OfferBase):
    """
    创建 Offer 的请求体。

    对应 API: POST /recruitment/offers

    创建后 Offer 状态为 draft，approval_status 为 pending（待审批）。
    需通过 /offers/{id}/approve 完成审批后，才能发送给候选人。
    """
    pass


class OfferUpdate(BaseModel):
    """
    更新 Offer 的请求体（PATCH 语义）。

    对应 API: PUT /recruitment/offers/{id}

    注意：审批通过后的 Offer 不允许修改核心条款（薪资、职位）。
    background_check_status 和 health_check_status 在入职流程中更新。

    字段说明:
        background_check_status: 背景调查状态（pending/passed/failed）
        health_check_status:     体检状态（pending/passed/failed）
        pdf_url:                 生成的 Offer PDF 存储链接
        sign_url:                电子签名链接（候选人在线签署）
    """
    position_name: Optional[str] = Field(default=None, max_length=100)
    department_id: Optional[int] = None
    location_id: Optional[int] = None
    salary_base: Optional[Decimal] = Field(default=None, ge=0)
    salary_total: Optional[Decimal] = Field(default=None, ge=0)
    probation_months: Optional[int] = Field(default=None, ge=0, le=6)
    probation_salary_ratio: Optional[Decimal] = Field(default=None, ge=0, le=1)
    start_date: Optional[date] = None
    background_check_required: Optional[bool] = None
    background_check_status: Optional[str] = Field(default=None, max_length=20)  # pending|passed|failed
    health_check_status: Optional[str] = Field(default=None, max_length=20)      # 体检状态
    pdf_url: Optional[str] = Field(default=None, max_length=500)    # Offer PDF 文件链接
    sign_url: Optional[str] = Field(default=None, max_length=500)   # 电子签名链接


class OfferOut(OfferBase):
    """
    返回给前端的 Offer 数据（ORM 序列化）。

    字段说明:
        id:                      Offer 主键
        approval_status:         审批状态（pending/已通过/已拒绝）
        offer_status:            Offer 状态（draft/sent/accepted/rejected/expired）
        pdf_url:                 Offer PDF 链接（发送后有值）
        sign_url:                电子签名链接
        background_check_status: 背景调查状态
        health_check_status:     体检状态
        created_at:              创建时间
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    approval_status: str             # pending | 已通过 | 已拒绝
    offer_status: str                # draft | sent | accepted | rejected | expired
    pdf_url: Optional[str] = None    # 生成的 Offer PDF 链接
    sign_url: Optional[str] = None   # 候选人在线签署链接
    background_check_status: Optional[str] = None  # 背景调查结果
    health_check_status: Optional[str] = None      # 体检结果
    created_at: datetime


class OfferApprovalRequest(BaseModel):
    """
    Offer 审批请求体（管理层审批）。

    对应 API: POST /recruitment/offers/{id}/approve

    字段说明:
        approval_status: 审批结果，枚举: "已通过" / "已拒绝"
        comment:         审批意见（如条件修改建议）
    """
    approval_status: str = Field(..., description="审批结果: 已通过/已拒绝")
    comment: Optional[str] = Field(default=None, description="审批备注")


class OfferSendRequest(BaseModel):
    """
    发送 Offer 给候选人的请求体。

    对应 API: POST /recruitment/offers/{id}/send
    前提条件: approval_status = 已通过

    字段说明:
        pdf_url:  Offer PDF 的存储 URL（可提前上传后填写链接）
        sign_url: 电子签名平台的签署链接（如法大大、e签宝）
        notify:   是否同时发送消息通知（短信/邮件）给候选人
    """
    pdf_url: Optional[str] = Field(default=None, max_length=500, description="Offer PDF")
    sign_url: Optional[str] = Field(
        default=None, max_length=500, description="电子签名链接"
    )
    notify: bool = Field(default=True, description="是否发送通知")  # 发送后状态变为 sent


class OfferCandidateConfirmRequest(BaseModel):
    """
    候选人确认 Offer 的请求体。

    对应 API: POST /recruitment/offers/{id}/candidate-confirm

    候选人收到 Offer 后，通过系统（或 HR 代为操作）确认接受或拒绝。

    字段说明:
        accepted:      True=接受Offer（状态变为 accepted），False=拒绝（状态变为 rejected）
        reject_reason: 拒绝原因（accepted=False 时建议填写，如 "已接受其他公司 Offer"）
    """
    accepted: bool = Field(..., description="是否接受Offer")
    reject_reason: Optional[str] = Field(default=None, description="拒绝原因")  # 拒绝时的原因说明


# ===========================================================================
# InternalReferral 内推
# ===========================================================================

class InternalReferralBase(BaseModel):
    """
    内推基础 Schema

    内推（InternalReferral）记录员工推荐候选人的信息及奖励跟踪。
    内推奖励分两期发放：
      - 第一期（首付）: 候选人成功入职时发放，比例由 first_payment_ratio 决定
      - 第二期（尾款）: 候选人通过试用期（转正）后发放，比例 = 1 - first_payment_ratio

    例如: reward_amount=3000元, first_payment_ratio=0.50
      → 入职时发放 1500元，转正后发放 1500元

    字段说明:
        referrer_id:        推荐人（员工）的 ID
        resume_id:          被推荐候选人的简历 ID
        reward_rule_type:   奖励规则类型（如 "普通岗位"、"技术岗位"、"管理岗位"）
        reward_amount:      总奖励金额（元）
        first_payment_ratio: 首次发放比例（0-1），默认 0.50（50%于入职时发放）
    """
    referrer_id: int = Field(..., description="推荐人ID")
    resume_id: int = Field(..., description="简历ID")
    reward_rule_type: Optional[str] = Field(
        default=None, max_length=50, description="奖励规则类型"
    )
    reward_amount: Optional[Decimal] = Field(
        default=None, ge=0, description="奖励总额(元)"
    )  # 内推总奖励金额
    first_payment_ratio: Decimal = Field(
        default=Decimal("0.50"), ge=0, le=1, description="首次发放比例"
    )  # 入职时发放的比例，默认50%；剩余50%在转正时发放


class InternalReferralCreate(InternalReferralBase):
    """
    创建内推记录的请求体。

    对应 API: POST /recruitment/referrals

    在录入候选人简历时或候选人确认接受 Offer 后创建。

    字段说明:
        candidate_name:     候选人姓名（冗余字段，方便查看）
        candidate_phone:    候选人联系电话
        candidate_email:    候选人邮箱
        notes:              内推备注
        expected_join_date: 预计入职日期
    """
    candidate_name: Optional[str] = None
    candidate_phone: Optional[str] = None
    candidate_email: Optional[str] = None
    notes: Optional[str] = None
    expected_join_date: Optional[date] = None


class InternalReferralUpdate(BaseModel):
    """
    更新内推记录的请求体（PATCH 语义）。

    对应 API: PUT /recruitment/referrals/{id}

    字段说明:
        first_payment_status:  第一期支付状态（pending/paid）
        second_payment_status: 第二期支付状态（pending/paid）
        candidate_status:      候选人当前阶段（简历筛选/面试中/已录用/已入职/已转正）
        paid_at:               实际付款时间
        paid_by:               操作付款的 HR 员工 ID
    """
    reward_rule_type: Optional[str] = Field(default=None, max_length=50)
    reward_amount: Optional[Decimal] = Field(default=None, ge=0)
    first_payment_ratio: Optional[Decimal] = Field(default=None, ge=0, le=1)
    first_payment_status: Optional[str] = Field(default=None, max_length=20)   # pending | paid
    second_payment_status: Optional[str] = Field(default=None, max_length=20)  # pending | paid
    candidate_name: Optional[str] = None
    candidate_phone: Optional[str] = None
    candidate_email: Optional[str] = None
    candidate_status: Optional[str] = None   # 候选人当前阶段
    notes: Optional[str] = None
    expected_join_date: Optional[date] = None
    paid_at: Optional[datetime] = None   # 最近一次付款时间
    paid_by: Optional[int] = None        # 操作付款的 HR ID


class InternalReferralOut(InternalReferralBase):
    """
    返回给前端的内推记录数据（ORM 序列化）。

    字段说明:
        id:                    内推记录主键
        first_payment_status:  第一期支付状态（pending=待发放，paid=已发放）
        second_payment_status: 第二期支付状态（pending=待发放，paid=已发放）
        candidate_status:      候选人当前所处招聘阶段
        referrer_name:         推荐人姓名（service 层 JOIN 注入）
        referrer_department:   推荐人所属部门名称（service 层 JOIN 注入）
        position_name:         推荐的职位名称（service 层 JOIN 注入）
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    first_payment_status: str          # 第一期发放状态：pending | paid
    second_payment_status: str         # 第二期发放状态：pending | paid
    created_at: datetime
    # 候选人补充信息（冗余字段，方便列表展示）
    candidate_name: Optional[str] = None
    candidate_phone: Optional[str] = None
    candidate_email: Optional[str] = None
    candidate_status: str = "简历筛选"  # 候选人招聘进度
    notes: Optional[str] = None
    expected_join_date: Optional[date] = None
    paid_at: Optional[datetime] = None
    # 关联字段（service 层查询后注入，避免前端多次请求）
    referrer_name: Optional[str] = None       # 推荐人姓名
    referrer_department: Optional[str] = None  # 推荐人部门
    position_name: Optional[str] = None       # 推荐的岗位名称


class ReferralRuleCreate(BaseModel):
    """
    创建内推奖励规则的请求体。

    对应 API: POST /recruitment/referral-rules

    定义不同岗位类型的内推奖励标准，
    例如技术岗奖励 5000元，管理岗奖励 8000元。

    字段说明:
        rule_name:     规则名称，例如 "技术类岗位内推"
        job_category:  适用职位类别（如 "技术"、"管理"、"生产"）
        job_level:     适用职位级别（如 "P4-P6"、"M2以上"）
        reward_amount: 奖励总金额（元），默认 3000 元
    """
    rule_name: str
    job_category: Optional[str] = None   # 适用岗位类别
    job_level: Optional[str] = None      # 适用岗位级别
    reward_amount: Decimal = Decimal("3000")  # 默认奖励额度 3000 元
    description: Optional[str] = None


class ReferralRuleOut(ReferralRuleCreate):
    """
    返回给前端的内推规则数据（ORM 序列化）。
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_active: bool    # 是否启用，停用后不影响历史记录
    created_at: datetime


class ReferralStatsOut(BaseModel):
    """
    内推统计数据（用于内推管理看板）。

    对应 API: GET /recruitment/referrals/stats

    字段说明:
        month_total:    本月内推简历总数
        pending_amount: 待发放奖励总金额（元）
        success_count:  成功入职的内推候选人数（本月）
        monthly_trend:  月度内推趋势数据（列表，每项含月份和数量）
    """
    month_total: int = 0            # 本月内推简历总数
    pending_amount: Decimal = Decimal("0")  # 待发放奖励总额
    success_count: int = 0          # 成功录用人数
    monthly_trend: List[dict] = []  # 月度趋势，格式: [{"month": "2024-01", "count": 5}, ...]


class BulkMarkPaidRequest(BaseModel):
    """
    批量标记内推奖励已发放的请求体。

    对应 API: POST /recruitment/referrals/bulk-mark-paid

    HR 完成财务转账后，批量更新内推记录的支付状态。

    字段说明:
        referral_ids:  要标记的内推记录 ID 列表
        payment_type:  发放类型，"first" = 第一期（入职），"second" = 第二期（转正）
    """
    referral_ids: List[int]
    payment_type: str = "first"  # first=首次发放（入职时）, second=第二次发放（转正后）


# ===========================================================================
# Statistics 数据分析
# ===========================================================================

class FunnelStageStats(BaseModel):
    """
    招聘漏斗单阶段统计数据。

    作为 RecruitmentFunnelOut.stages 列表的元素，
    展示招聘各阶段的人数和转化率。

    字段说明:
        stage:           阶段名称（如 "简历筛选"、"初轮面试"、"Offer发放"）
        count:           该阶段的候选人数量
        conversion_rate: 相对上一阶段的转化率（%），第一阶段为 None
    """
    stage: str = Field(..., description="阶段名称")  # 如 "简历收集" | "初筛" | "面试" | "Offer" | "入职"
    count: int = Field(..., description="数量")
    conversion_rate: Optional[float] = Field(
        default=None, description="转化率(%)"
    )  # 该阶段相对于上一阶段的转化率，百分比


class RecruitmentFunnelOut(BaseModel):
    """
    招聘漏斗整体统计（漏斗模型各层数量）。

    对应 API: GET /recruitment/stats/funnel

    反映整体招聘效率，各阶段候选人数量递减形成漏斗形状。

    字段说明:
        demand_id:          如果只统计某个职位的漏斗，传入需求ID；全局漏斗则为 None
        total_resumes:      收到的简历总数（漏斗顶部）
        screened:           通过初筛的简历数
        interview_scheduled: 安排了面试的候选人数
        interview_passed:   面试通过的候选人数
        offer_sent:         发出 Offer 的候选人数
        offer_accepted:     接受 Offer 的候选人数（漏斗底部，最终录用）
        stages:             各阶段详细统计（含转化率）
    """
    demand_id: Optional[int] = None      # 指定职位漏斗，None 表示全公司漏斗
    total_resumes: int = 0               # 简历总数（漏斗入口）
    screened: int = 0                    # 通过初筛数
    interview_scheduled: int = 0         # 安排面试数
    interview_passed: int = 0            # 面试通过数
    offer_sent: int = 0                  # 发出 Offer 数
    offer_accepted: int = 0              # 接受 Offer 数（漏斗出口）
    stages: list[FunnelStageStats] = []  # 各阶段详细数据


class ChannelEffectivenessOut(BaseModel):
    """
    单个招聘渠道的效果统计（ROI 分析）。

    对应 API: GET /recruitment/stats/channels（列表响应的元素）

    用于评估各渠道的投入产出比，帮助 HR 优化渠道预算分配。

    字段说明:
        channel_id/name:     渠道 ID 和名称
        total_resumes:       该渠道收到的简历总数
        screened_pass:       通过初筛的数量
        interview_pass:      面试通过的数量
        offer_accepted:      接受 Offer 的数量（最终录用）
        screening_pass_rate: 初筛通过率（%）= screened_pass / total_resumes × 100
        offer_accept_rate:   录用率（%）= offer_accepted / total_resumes × 100
    """
    channel_id: int
    channel_name: str
    total_resumes: int = 0             # 简历总数
    screened_pass: int = 0             # 初筛通过数
    interview_pass: int = 0            # 面试通过数
    offer_accepted: int = 0            # 录用数
    screening_pass_rate: Optional[float] = None  # 初筛通过率 %
    offer_accept_rate: Optional[float] = None    # 最终录用率 %


class RecruitmentStatsOut(BaseModel):
    """
    招聘整体统计汇总（漏斗 + 渠道效果）。

    对应 API: GET /recruitment/stats

    组合了漏斗统计和渠道效果分析，提供管理层概览。
    """
    funnel: RecruitmentFunnelOut                         # 整体招聘漏斗
    channel_effectiveness: list[ChannelEffectivenessOut] = []  # 各渠道效果排名


# ===========================================================================
# Pagination 分页
# ===========================================================================

class PaginatedResponse(BaseModel):
    """
    通用分页响应 Schema（招聘模块复用）。

    用于所有支持分页的列表查询响应，统一格式。

    字段说明:
        total:     符合条件的记录总数（用于前端分页器计算总页数）
        page:      当前页码（从1开始）
        page_size: 每页返回的数据条数
        items:     当前页的数据列表（类型由具体接口决定）
    """
    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    items: list = Field(default_factory=list, description="数据列表")


# ===========================================================================
# OfferTemplate Offer模板
# ===========================================================================

class OfferTemplateCreate(BaseModel):
    """
    创建 Offer 模板的请求体。

    对应 API: POST /recruitment/offer-templates

    Offer 模板用于快速生成标准化 Offer，避免重复填写相同信息。
    例如：为特定职位预设薪资范围和试用期条款，HR 只需选择模板即可。

    字段说明:
        name:                  模板名称，例如 "研发工程师标准 Offer"
        position_name:         适用职位名称
        department_id:         适用部门（可选，为空则通用）
        salary_base/total:     预设薪资（可选，仅供参考，实际 Offer 可调整）
        probation_months:      预设试用期月数（默认3个月）
        probation_salary_ratio: 预设试用期薪资比例（默认80%）
        notes:                 模板备注说明
        is_active:             是否启用该模板
    """
    name: str
    position_name: str
    department_id: Optional[int] = None
    salary_base: Optional[Decimal] = None        # 参考基本月薪
    salary_total: Optional[Decimal] = None       # 参考综合年薪
    probation_months: int = 3                    # 试用期月数，默认3个月
    probation_salary_ratio: Decimal = Decimal("0.80")  # 试用期薪资比例，默认80%
    notes: Optional[str] = None
    is_active: bool = True


class OfferTemplateUpdate(BaseModel):
    """
    更新 Offer 模板的请求体（PATCH 语义）。

    对应 API: PUT /recruitment/offer-templates/{id}
    """
    name: Optional[str] = None
    position_name: Optional[str] = None
    department_id: Optional[int] = None
    salary_base: Optional[Decimal] = None
    salary_total: Optional[Decimal] = None
    probation_months: Optional[int] = None
    probation_salary_ratio: Optional[Decimal] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class OfferTemplateOut(OfferTemplateCreate):
    """
    返回给前端的 Offer 模板数据（ORM 序列化）。
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


# ===========================================================================
# OnboardingRecord 入职管理
# ===========================================================================

class OnboardingRecordCreate(BaseModel):
    """
    创建入职管理记录的请求体。

    对应 API: POST /recruitment/onboarding

    候选人接受 Offer 后，HR 创建入职跟踪记录，
    管理入职前的准备事项（证件收集、工位准备、系统开通等）。

    字段说明:
        offer_id:      关联的 Offer ID
        candidate_name: 候选人姓名（冗余，方便列表展示）
        position_name:  入职职位
        department_id:  入职部门 ID
        planned_date:   计划入职日期
        hr_owner_id:    负责跟进的 HR 员工 ID
        notes:          入职备注
        checklist:      入职准备清单（JSON 格式，如 {"工牌": false, "工位": true}）
    """
    offer_id: int
    candidate_name: str
    position_name: str
    department_id: Optional[int] = None
    planned_date: Optional[date] = None     # 计划入职日期（来自 Offer.start_date）
    hr_owner_id: Optional[int] = None       # 跟进 HR
    notes: Optional[str] = None
    checklist: Optional[dict] = None        # 入职准备事项清单，key=事项名，value=是否完成


class OnboardingRecordUpdate(BaseModel):
    """
    更新入职管理记录的请求体（PATCH 语义）。

    对应 API: PUT /recruitment/onboarding/{id}

    字段说明:
        actual_date: 实际入职日期（完成入职手续后填写）
        status:      入职状态（pending/in_progress/completed/cancelled）
        employee_id: 入职成功后，关联创建的员工 ID（转化为员工档案）
        checklist:   更新准备清单的完成状态
    """
    planned_date: Optional[date] = None
    actual_date: Optional[date] = None      # 实际报到日期
    status: Optional[str] = None           # pending | in_progress | completed | cancelled
    employee_id: Optional[int] = None      # 入职完成后关联的员工档案 ID
    checklist: Optional[dict] = None
    notes: Optional[str] = None
    hr_owner_id: Optional[int] = None


class OnboardingRecordOut(BaseModel):
    """
    返回给前端的入职记录数据（ORM 序列化）。

    字段说明:
        id:           入职记录主键
        actual_date:  实际入职日期（报到后填写）
        status:       当前状态（pending=待入职 / completed=已入职 / cancelled=取消）
        employee_id:  入职成功后创建的员工 ID（入职完成前为 None）
    """
    model_config = ConfigDict(from_attributes=True)
    id: int
    offer_id: int
    candidate_name: str
    position_name: str
    department_id: Optional[int] = None
    planned_date: Optional[date] = None
    actual_date: Optional[date] = None     # 实际报到日期
    status: str                            # pending | in_progress | completed | cancelled
    employee_id: Optional[int] = None     # 关联的正式员工档案 ID（入职完成后有值）
    checklist: Optional[dict] = None
    notes: Optional[str] = None
    hr_owner_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# BulkScreen 简历批量筛选
# ===========================================================================

class BulkScreenRequest(BaseModel):
    """
    批量筛选简历的请求体。

    对应 API: POST /recruitment/resumes/bulk-screen

    HR 可以同时对多份简历进行相同的筛选操作，提高工作效率。

    字段说明:
        resume_ids:       要批量筛选的简历 ID 列表（至少1条）
        screening_status: 统一设置的筛选结果："推荐" 或 "不合适"
        screening_note:   统一的筛选备注
    """
    resume_ids: list[int] = Field(..., min_length=1)  # 批量操作的简历 ID 列表
    screening_status: str = Field(..., description="筛选结果: 推荐 或 不合适")
    screening_note: Optional[str] = None


class BulkScreenResult(BaseModel):
    """
    批量筛选操作结果。

    字段说明:
        success: 成功处理的简历数
        failed:  失败的简历数（如状态不允许修改）
        errors:  失败的详细原因列表
    """
    success: int        # 成功筛选数
    failed: int         # 失败数（如简历不存在或状态异常）
    errors: list[str] = []  # 错误详情列表


# ===========================================================================
# BulkDelete 简历批量删除
# ===========================================================================

class BulkDeleteRequest(BaseModel):
    """
    批量删除简历的请求体。

    对应 API: POST /recruitment/resumes/bulk-delete

    注意：只有状态为"不合适"且未进入面试流程的简历才允许删除。
    已进入面试的简历受保护，不能批量删除。
    """
    resume_ids: list[int] = Field(..., min_length=1)  # 要删除的简历 ID 列表


class BulkDeleteResult(BaseModel):
    """
    批量删除操作结果。

    字段说明:
        deleted: 成功删除的简历数量
    """
    deleted: int  # 实际删除数量（受保护的简历不计入）


# ===========================================================================
# BulkOfferAction Offer批量操作
# ===========================================================================

class BulkOfferActionRequest(BaseModel):
    """
    Offer 批量操作请求体（批量审批或批量撤回）。

    对应 API: POST /recruitment/offers/bulk-action

    字段说明:
        offer_ids: 要操作的 Offer ID 列表（至少1条）
        action:    操作类型，枚举: "approve"（批准）/ "revoke"（撤回）
        comment:   操作备注（批量审批原因）
    """
    offer_ids: list[int] = Field(..., min_length=1)
    action: str = Field(..., description="操作: approve / revoke")  # approve=批准, revoke=撤回
    comment: Optional[str] = None


class BulkOfferActionResult(BaseModel):
    """
    Offer 批量操作结果。

    字段说明:
        success: 成功处理数
        failed:  失败数（如状态不允许操作）
        errors:  失败详情列表
    """
    success: int
    failed: int
    errors: list[str] = []


# ===========================================================================
# LeaderboardOut 内推排行榜
# ===========================================================================

class LeaderboardItem(BaseModel):
    """
    内推排行榜单条记录（单个推荐人的数据）。

    作为 LeaderboardOut.leaderboard 列表的元素，
    展示各员工的内推贡献情况。

    字段说明:
        referrer_id:    推荐人员工 ID
        referrer_name:  推荐人姓名
        department:     推荐人所属部门
        total_count:    推荐的简历总数
        success_count:  成功录用数（接受 Offer 或已入职）
        pending_amount: 待发放的奖励总金额（元）
    """
    referrer_id: int
    referrer_name: str
    department: str = ""        # 部门名称，未找到时为空字符串
    total_count: int            # 推荐简历总数
    success_count: int          # 成功录用数
    pending_amount: float       # 待发放奖励总额（元）


class LeaderboardOut(BaseModel):
    """
    内推排行榜响应数据。

    对应 API: GET /recruitment/referrals/leaderboard

    支持按年份和月份筛选，展示指定时间段内的内推排行榜。

    字段说明:
        leaderboard: 按成功推荐数降序排列的推荐人列表
        year:        统计年份
        month:       统计月份（None 表示全年统计）
    """
    leaderboard: List[LeaderboardItem]  # 排行榜列表，按 success_count 降序
    year: int
    month: Optional[int] = None   # None = 全年统计，1-12 = 指定月份统计
