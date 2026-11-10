"""
模块名称: headhunter.py
模块作用: 猎头管理模块的 Pydantic Schemas — 管理猎头公司及其简历推荐记录

业务背景:
    本公司通过外部猎头公司寻访高端人才。本模块维护:
      1. 猎头公司（Headhunter）—— 合作机构档案，含费率和状态
      2. 候选人推荐记录（Recommendation）—— 猎头为特定职位推荐的候选人，
         跟踪从"推荐中"到"已录用"的全流程，并记录猎头费用和付款状态

注意区分:
    - headhunter.py 中的付款状态（HeadhunterRecommendation.payment_status）
      与 vendor.py 中的 HeadhunterCommission（佣金结算）是不同层级的概念:
      前者是单次推荐的付款跟踪，后者是年度/季度的财务对账

数据流向:
    HTTP 请求
        → HeadhunterCreate / HeadhunterUpdate（请求体）
        → models/headhunter.py（Headhunter ORM）
        → PostgreSQL headhunters 表
        → HeadhunterOut（API 响应）

    HTTP 请求
        → RecommendationCreate / RecommendationUpdate（请求体）
        → models/headhunter.py（HeadhunterRecommendation ORM）
        → PostgreSQL headhunter_recommendations 表
        → RecommendationOut（API 响应）

被哪些端点使用:
    - GET    /api/v1/headhunters                    → HeadhunterListResponse
    - POST   /api/v1/headhunters                    → HeadhunterOut
    - GET    /api/v1/headhunters/{id}               → HeadhunterOut
    - PUT    /api/v1/headhunters/{id}               → HeadhunterOut
    - GET    /api/v1/headhunters/{id}/recommendations → RecommendationListResponse
    - POST   /api/v1/headhunters/recommendations    → RecommendationOut
    - PUT    /api/v1/headhunters/recommendations/{id} → RecommendationOut
"""
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ==================== Headhunter（猎头公司）====================

class HeadhunterBase(BaseModel):
    """
    猎头公司基础 Schema — 定义猎头公司档案的核心字段

    被 HeadhunterCreate 和 HeadhunterOut 继承使用。
    包含公司基本信息、联系方式、专业领域和合作状态。

    字段说明:
        name            — 猎头公司全称，必填，最长 200 字符
        contact_person  — 对接联系人姓名（通常是 Account Manager）
        phone           — 联系电话，支持带区号的格式
        email           — 联系邮箱，用于发送职位需求和候选人反馈
        specialty_areas — 专业领域，如"互联网/AI/机器人/高端制造"，自由文本
        fee_rate        — 猎头服务费率，单位为百分比（%），范围 0~100；
                          通常为年薪的 15%~30%，与推荐记录中的 fee_amount 计算关联
        status          — 合作状态，枚举: 合作中 / 暂停合作 / 已终止
        remark          — 备注，如合同有效期、特殊约定等
    """
    name: str = Field(..., max_length=200, description="猎头公司名称")
    contact_person: Optional[str] = Field(None, max_length=50, description="联系人姓名")
    phone: Optional[str] = Field(None, max_length=30, description="联系电话")
    email: Optional[str] = Field(None, max_length=100, description="邮箱")
    specialty_areas: Optional[str] = Field(None, max_length=500, description="专业领域")
    fee_rate: Optional[float] = Field(None, ge=0, le=100, description="猎头费率（%）")
    # fee_rate 校验: ge=0（不能为负）, le=100（不能超过100%）
    status: str = Field("合作中", description="状态：合作中/暂停合作/已终止")
    remark: Optional[str] = Field(None, description="备注")


class HeadhunterCreate(HeadhunterBase):
    """
    猎头公司创建 Schema — 用于 POST /api/v1/headhunters 请求体

    直接继承 HeadhunterBase，无额外字段。
    所有非必填字段默认为 None，status 默认为 "合作中"。
    """
    pass


class HeadhunterUpdate(BaseModel):
    """
    猎头公司更新 Schema — 用于 PUT /api/v1/headhunters/{id} 请求体

    所有字段均为 Optional，支持部分更新（Partial Update / PATCH 语义）。
    前端只需传入需要修改的字段，未传字段保持数据库原值不变。

    注意: headhunter_id 不在此 Schema 中，由路径参数提供。
    """
    name: Optional[str] = Field(None, max_length=200)
    contact_person: Optional[str] = Field(None, max_length=50)
    phone: Optional[str] = Field(None, max_length=30)
    email: Optional[str] = Field(None, max_length=100)
    specialty_areas: Optional[str] = Field(None, max_length=500)
    fee_rate: Optional[float] = Field(None, ge=0, le=100)   # 同 HeadhunterBase 的范围校验
    status: Optional[str] = None                             # 如需停止合作，将此字段设为 "已终止"
    remark: Optional[str] = None


class HeadhunterOut(HeadhunterBase):
    """
    猎头公司响应 Schema — 用于 API 返回，从 ORM 对象序列化

    在 HeadhunterBase 基础上额外附加:
        id                    — 数据库主键
        created_at / updated_at — 记录时间戳（带时区）
        recommendation_count  — 该猎头公司的历史推荐候选人总数（聚合统计值，
                                 由 Service 层计算后注入，ORM 无此列）

    model_config from_attributes=True: 支持从 SQLAlchemy ORM 对象直接序列化
    """
    id: int
    created_at: datetime
    updated_at: datetime
    recommendation_count: int = 0   # 历史推荐总数，Service 层聚合计算后注入；默认为 0

    model_config = {"from_attributes": True}


# ==================== HeadhunterRecommendation（推荐记录）====================

class RecommendationBase(BaseModel):
    """
    猎头推荐记录基础 Schema — 记录猎头为某职位推荐的一位候选人的全生命周期

    一条推荐记录对应"一家猎头公司 + 一位候选人 + 一个目标职位"的组合。
    推荐状态（status）随面试流程推进，付款状态（payment_status）随 Offer 确认推进。

    字段说明:
        headhunter_id   — 关联猎头公司 ID（外键）
        candidate_name  — 候选人姓名（此时候选人可能尚未建档，故用文本存储）
        position        — 推荐的目标职位名称
        resume_id       — 关联到 recruitment.Resume 表的简历 ID（可选）；
                          当候选人进入面试流程后，HR 会在系统中创建简历档案并关联
        recommend_date  — 猎头提交推荐的日期
        fee_amount      — 本次推荐的猎头服务费（元）；通常 = 候选人年薪 × fee_rate
        payment_status  — 付款状态: 未付款 / 已付款 / 部分付款；
                          与 Offer 流程关联：Offer 接受+入职后才触发付款
        status          — 推荐进展状态: 推荐中 → 面试中 → 已录用 / 未录用 / 已放弃
        remark          — 备注，如候选人意向说明、面试反馈摘要等
    """
    headhunter_id: int = Field(..., description="猎头公司ID")
    candidate_name: str = Field(..., max_length=50, description="候选人姓名")
    position: Optional[str] = Field(None, max_length=100, description="推荐职位")
    resume_id: Optional[int] = Field(None, description="关联简历ID（可选）")
    # resume_id: 候选人进入系统面试流程后关联，初始推荐时通常为 None
    recommend_date: Optional[date] = Field(None, description="推荐日期")
    fee_amount: Optional[float] = Field(None, ge=0, description="猎头费用（元）")
    # fee_amount: ge=0 校验，猎头费用不能为负数
    payment_status: str = Field("未付款", description="付款状态：未付款/已付款/部分付款")
    # payment_status 业务规则:
    #   - "未付款": 默认初始状态
    #   - "部分付款": 首付（如候选人入职当月）
    #   - "已付款": 全额结清（如候选人转正后）
    status: str = Field("推荐中", description="推荐状态：推荐中/面试中/已录用/未录用/已放弃")
    # status 状态流转: 推荐中 → 面试中 → 已录用（触发付款流程）
    #                                  → 未录用（关闭，无需付款）
    #                                  → 已放弃（候选人主动放弃）
    remark: Optional[str] = Field(None, description="备注")


class RecommendationCreate(RecommendationBase):
    """
    推荐记录创建 Schema — 用于 POST /api/v1/headhunters/recommendations 请求体

    直接继承 RecommendationBase，无额外字段。
    headhunter_id 为必填，确保推荐记录必须关联到已存在的猎头公司。
    """
    pass


class RecommendationUpdate(BaseModel):
    """
    推荐记录更新 Schema — 用于 PUT /api/v1/headhunters/recommendations/{id} 请求体

    所有字段均为 Optional，支持部分更新。常见更新场景:
      - 推进状态: 将 status 从 "推荐中" 更新为 "面试中" 或 "已录用"
      - 关联简历: 候选人进入系统后设置 resume_id
      - 更新付款: 付款完成后将 payment_status 设为 "已付款"
      - 确认费用: 确认实际猎头费用 fee_amount

    注意: headhunter_id 不允许修改（推荐记录不能转移给其他猎头公司）。
    """
    candidate_name: Optional[str] = Field(None, max_length=50)
    position: Optional[str] = Field(None, max_length=100)
    resume_id: Optional[int] = None           # 候选人进入系统面试后关联简历
    recommend_date: Optional[date] = None
    fee_amount: Optional[float] = None        # 确认实际费用时更新
    payment_status: Optional[str] = None     # 付款完成后更新
    status: Optional[str] = None             # 推进面试流程时更新
    remark: Optional[str] = None


class RecommendationOut(RecommendationBase):
    """
    推荐记录响应 Schema — 用于 API 返回，从 ORM 对象序列化

    在 RecommendationBase 基础上额外附加:
        id              — 数据库主键
        headhunter_name — 关联猎头公司名称（冗余字段，避免前端二次请求；
                          由 Service 层 JOIN 查询后注入）
        created_at      — 记录创建时间（带时区）

    model_config from_attributes=True: 支持从 SQLAlchemy ORM 对象直接序列化
    """
    id: int
    headhunter_name: Optional[str] = None   # JOIN 查询获得的猎头公司名称，方便列表展示
    created_at: datetime

    model_config = {"from_attributes": True}


# ==================== 列表响应（分页封装）====================

class HeadhunterListResponse(BaseModel):
    """
    猎头公司列表响应 Schema — 用于 GET /api/v1/headhunters 分页列表接口

    字段说明:
        total — 符合筛选条件的猎头公司总数（用于前端分页组件）
        items — 当前页的猎头公司列表
    """
    total: int      # 数据库中符合条件的记录总数
    items: List[HeadhunterOut]


class RecommendationListResponse(BaseModel):
    """
    推荐记录列表响应 Schema — 用于 GET /api/v1/headhunters/{id}/recommendations 分页列表接口

    字段说明:
        total — 符合筛选条件的推荐记录总数（用于前端分页组件）
        items — 当前页的推荐记录列表，每条记录含猎头公司名称
    """
    total: int      # 数据库中符合条件的记录总数
    items: List[RecommendationOut]
