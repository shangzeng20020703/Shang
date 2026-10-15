"""
外包供应商管理模块 - Pydantic Schemas
=======================================

模块名称: vendor.py
所属系统: 售后管理系统 - 外包供应商管理模块

作用:
    定义供应商相关的请求/响应数据结构，管理公司与外部合作机构的关系，包含四个子模块：

    1. Vendor（供应商）:
       记录劳务派遣公司、猎头机构、培训机构等合作伙伴的基础信息。
       供应商类型（vendor_type）决定了后续可关联的业务（合同类型、佣金管理等）。

    2. VendorContract（供应商合同）:
       记录与供应商签订的合同信息，包含合同期限、费率和合同文件。
       一个供应商可以签署多份合同（不同类型或不同时间段）。

    3. HeadhunterCommission（猎头佣金）:
       专用于猎头服务的佣金跟踪，采用两期付款机制：
         - 第一期（offer_amount）: 候选人接受 Offer 并入职后支付（通常50%）
         - 第二期（final_amount）: 候选人通过试用期转正后支付（剩余50%）
       同时支持保证期退款机制（candidate_left_during_guarantee_period）。

    4. VendorEvaluation（供应商评估）:
       对供应商服务质量进行多维度评分（质量/交付/价格/态度），
       支持季度评估和年度评估，综合分数影响供应商状态和续约决策。

    5. VendorBlacklist（供应商黑名单）:
       记录被列入黑名单的供应商，黑名单影响合同续签和新业务合作。

VendorOut 的特殊设计:
    vendor_type 在数据库中存储中文值（如"猎头服务"），
    但前端某些组件需要英文类型码（如"headhunter"），
    @model_validator(mode="after") _set_type() 方法在序列化时
    自动将中文类型映射为英文类型码，填入 type 字段，实现双格式兼容。

数据流向:
    前端表单 → *Create/*Update Schema（请求验证）
         ↓
    service 层（业务逻辑：状态管理、评分计算、佣金跟踪）
         ↓
    ORM Model 持久化到 PostgreSQL
         ↓
    *Out Schema（响应序列化，含 model_validator 后处理）→ 前端展示

被调用的 API 端点 (api/v1/endpoints/vendor.py):
    供应商:
        POST/GET           /vendors              - 创建/列表
        GET/PUT/DELETE     /vendors/{id}         - 详情/更新/删除
        POST               /vendors/{id}/evaluate - 快速评分（简单版）
    合同:
        POST/GET           /vendors/{id}/contracts - 创建/列表
        GET/PUT/DELETE     /contracts/{id}        - 详情/更新/删除
    猎头佣金:
        POST/GET           /headhunter-commissions - 创建/列表
        GET/PUT            /headhunter-commissions/{id}
        POST               /headhunter-commissions/{id}/pay   - 记录付款
        POST               /headhunter-commissions/{id}/refund - 发起退款
    供应商评估:
        POST/GET           /vendor-evaluations    - 创建/列表
        GET                /vendor-evaluations/summary/{vendor_id} - 得分汇总
    黑名单:
        POST               /vendors/blacklist     - 加入黑名单
        GET                /vendors/blacklist     - 黑名单列表
        DELETE             /vendors/blacklist/{id} - 移出黑名单

依赖:
    - pydantic v2: BaseModel, Field, model_validator
    - 关联 models/vendor.py: Vendor, VendorContract, HeadhunterCommission,
      VendorEvaluation, VendorBlacklist
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ───────── Vendor ─────────

class VendorBase(BaseModel):
    """
    供应商基础 Schema

    记录外部合作机构（劳务派遣/猎头/培训机构等）的基本信息。
    供应商是系统中所有外包业务的核心主体，其他子模块（合同/佣金/评估/黑名单）均关联到供应商。

    字段说明:
        company_name:               供应商公司名称（全称）
        vendor_type:                供应商类型，枚举（中文）:
                                      劳务派遣: 提供劳务派遣工，受《劳务派遣暂行规定》约束
                                      猎头服务/猎头: 帮助招募中高端管理/技术人才，按成功收费
                                      外包服务/外包: 项目外包，按工作量或项目结果收费
                                      培训机构: 提供培训课程和认证服务
                                      其他: 其他类型
        contact_name:               联系人姓名
        contact_phone:              联系电话
        contact_email:              联系邮箱
        business_scope:             业务范围描述（如服务地区、专注领域等）
        qualification_docs_json:    资质文件列表 JSON（营业执照、劳务派遣许可证等文件的 URL 列表）
        associated_project_ids_json: 关联的工程项目 ID 列表 JSON（派遣工服务的项目）
    """
    company_name: str = Field(..., max_length=200, description="公司名称")
    short_name: Optional[str] = Field(None, max_length=100, description="公司简称")
    credit_code: Optional[str] = Field(None, max_length=50, description="统一社会信用代码")
    vendor_type: str = Field(
        ..., max_length=30, description="供应商类型: outsource/headhunter/service"
    )  # 存储中文类型：劳务派遣 | 猎头服务 | 外包服务 | 培训机构 | 其他
    contact_name: Optional[str] = Field(None, max_length=50, description="联系人姓名")
    contact_phone: Optional[str] = Field(None, max_length=20, description="联系人电话")
    contact_email: Optional[str] = Field(None, max_length=100, description="联系人邮箱")
    address: Optional[str] = Field(None, max_length=255, description="公司地址")
    service_type: Optional[str] = Field(None, max_length=50, description="服务类型")
    business_scope: Optional[str] = Field(None, description="业务范围")
    qualification_docs_json: Optional[str] = Field(None, description="资质文件JSON")
    # 格式示例: '["https://oss/license.pdf", "https://oss/permit.pdf"]'
    associated_project_ids_json: Optional[str] = Field(None, description="关联项目ID列表JSON")
    # 格式示例: '[1, 5, 12]'，关联的 projects 表 ID 列表


class VendorCreate(VendorBase):
    """
    创建供应商的请求体。

    对应 API: POST /vendors

    创建后初始状态为 active（合作中），
    dispatch_count（派遣人数）初始为0，annual_eval_score（年度评分）为空。
    """
    pass


class VendorUpdate(BaseModel):
    """
    更新供应商信息的请求体（PATCH 语义）。

    对应 API: PUT /vendors/{id}

    字段说明:
        dispatch_count:   派遣/服务人员数量（HR 手动维护或系统自动统计）
        annual_eval_score: 年度评估综合得分（0-100）
        status:           供应商状态（active=合作中/suspended=暂停/terminated=终止合作）
    """
    company_name: Optional[str] = Field(None, max_length=200)
    vendor_type: Optional[str] = Field(None, max_length=30)
    contact_name: Optional[str] = Field(None, max_length=50)
    contact_phone: Optional[str] = Field(None, max_length=20)
    contact_email: Optional[str] = Field(None, max_length=100)
    short_name: Optional[str] = Field(None, max_length=100)
    credit_code: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = Field(None, max_length=255)
    service_type: Optional[str] = Field(None, max_length=50)
    business_scope: Optional[str] = None
    qualification_docs_json: Optional[str] = None
    associated_project_ids_json: Optional[str] = None
    dispatch_count: Optional[int] = Field(None, ge=0)               # 当前派遣/服务人员数量
    annual_eval_score: Optional[float] = Field(None, ge=0, le=100)  # 年度综合评分 0-100
    status: Optional[str] = None  # active | suspended | terminated


class VendorOut(VendorBase):
    """
    返回给前端的供应商数据（ORM 序列化，含后处理）。

    特殊设计：_set_type() model_validator 将数据库中存储的中文 vendor_type 值
    映射为英文类型码，填入 type 字段，兼容前端通过 v.type 访问英文类型的习惯。

    映射规则:
        "劳务派遣" → "labor_dispatch"
        "猎头服务" / "猎头" → "headhunter"
        "外包服务" / "外包" → "outsource"
        "培训机构" → "training"
        "其他" → "other"
        （未知类型）→ 原中文值（透传）

    字段说明:
        id:               供应商主键
        dispatch_count:   当前派遣/服务人数（统计值）
        annual_eval_score: 最新年度评分（0-100）
        status:           供应商状态（active/suspended/terminated）
        created_at:       供应商创建时间
        updated_at:       最后更新时间
        type:             英文类型码（由 _set_type() 自动映射，前端兼容字段）
    """
    id: int
    dispatch_count: int                     # 当前派遣/服务人员数量
    annual_eval_score: Optional[float]      # 年度综合评分（0-100），新供应商为 None
    status: str                             # active | suspended | terminated
    created_at: datetime
    updated_at: datetime
    type: str = ""  # 英文类型码（如 "headhunter"），由 _set_type() 自动填充，兼容前端

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _set_type(self) -> "VendorOut":
        """
        将中文 vendor_type 映射为英文类型码（兼容前端 v.type 用法）。

        此 validator 在 ORM 对象转换为 Pydantic 模型后自动执行，
        无需在 service 层手动处理，保持序列化逻辑集中在 Schema 层。
        """
        mapping = {
            "劳务派遣": "labor_dispatch",
            "猎头服务": "headhunter",
            "猎头": "headhunter",
            "外包服务": "outsource",
            "外包": "outsource",
            "培训机构": "training",
            "其他": "other",
        }
        self.type = mapping.get(self.vendor_type, self.vendor_type)
        return self


class VendorPage(BaseModel):
    """
    供应商分页响应。

    字段说明:
        total: 符合条件的供应商总数
        items: 当前页的供应商列表
    """
    total: int
    items: list[VendorOut]


class VendorEvaluationRequest(BaseModel):
    """
    供应商快速评分请求体（简化版，单一综合分）。

    对应 API: POST /vendors/{id}/evaluate（快速评分接口）

    与 VendorEvaluationCreate 不同，此接口只需提供一个综合分数，
    适用于日常快速打分，不区分质量/交付/价格等维度。
    详细多维度评分请使用 /vendor-evaluations 接口。

    字段说明:
        score:   综合评估分数（0-100）
        comment: 评估备注（如服务优缺点说明）
    """
    score: float = Field(..., ge=0, le=100, description="评估分数 0-100")  # 综合得分
    comment: Optional[str] = Field(None, description="评估备注")


# ───────── VendorContract ─────────

class VendorContractBase(BaseModel):
    """
    供应商合同基础 Schema

    记录与供应商签订的合同信息，是费用结算和合规管理的依据。
    一个供应商可以有多份合同（不同类型或不同时间段的续签合同）。

    字段说明:
        vendor_id:      关联供应商 ID
        contract_no:    合同编号（唯一，如 "HH-2024-001"）
        contract_type:  合同类型，枚举:
                          outsource_dispatch: 劳务派遣服务合同
                          headhunter_service: 猎头服务合同
                          other: 其他类型合同
        start_date:     合同开始日期
        end_date:       合同到期日期（到期前系统会提醒续签）
        fee_rate_json:  费率配置 JSON（不同合同类型的费率结构不同）
                        劳务派遣示例: '{"management_fee_rate": 0.08, "social_security": true}'
                        猎头服务示例: '{"commission_rate": 0.15, "guarantee_months": 3}'
        total_amount:   合同总金额（元），框架合同可为空
        file_url:       合同文件存储 URL（PDF 格式）
    """
    vendor_id: int = Field(..., description="供应商ID")
    contract_no: str = Field(..., max_length=50, description="合同编号")  # 唯一合同编号
    contract_type: str = Field(
        ...,
        max_length=30,
        description="合同类型: outsource_dispatch/headhunter_service/other",
    )
    start_date: date = Field(..., description="开始日期")
    end_date: date = Field(..., description="结束日期")    # 到期前应续签，否则合同状态变 expired
    fee_rate_json: Optional[str] = Field(None, description="费率JSON")   # 费率结构 JSON
    total_amount: Optional[float] = Field(None, ge=0, description="合同总额")  # 框架合同可为空
    file_url: Optional[str] = Field(None, max_length=500, description="合同文件URL")


class VendorContractCreate(VendorContractBase):
    """
    创建供应商合同的请求体。

    对应 API: POST /vendors/{id}/contracts

    创建后合同状态默认为 active（生效中）。
    系统会定期检查 end_date，到期后自动将状态更新为 expired。
    """
    pass


class VendorContractUpdate(BaseModel):
    """
    更新供应商合同的请求体（PATCH 语义）。

    对应 API: PUT /contracts/{id}

    注意：contract_no 和 vendor_id 创建后不允许修改。
    合同续签应创建新合同记录，而非修改原记录。

    字段说明:
        status: 合同状态（active=生效/expired=到期/terminated=提前终止）
    """
    contract_type: Optional[str] = Field(None, max_length=30)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    fee_rate_json: Optional[str] = None
    total_amount: Optional[float] = Field(None, ge=0)
    status: Optional[str] = None      # active | expired | terminated
    file_url: Optional[str] = Field(None, max_length=500)


class VendorContractOut(VendorContractBase):
    """
    返回给前端的合同数据（ORM 序列化）。

    字段说明:
        id:         合同主键
        status:     当前合同状态（active/expired/terminated）
        created_at: 合同录入时间
    """
    id: int
    status: str       # active | expired | terminated
    created_at: datetime

    model_config = {"from_attributes": True}


class VendorContractPage(BaseModel):
    """合同分页响应。"""
    total: int
    items: list[VendorContractOut]


# ───────── HeadhunterCommission ─────────

class HeadhunterCommissionBase(BaseModel):
    """
    猎头佣金基础 Schema

    HeadhunterCommission 专门追踪猎头服务的佣金管理，
    采用两期付款模式以控制成本和风险：

    两期付款机制:
        第一期（offer_amount / first_payment）:
          - 触发条件：候选人接受 Offer 并正式入职（stage=offer_accepted）
          - 支付时间：入职后通常1-2周内支付
          - 典型比例：总佣金的 50%
        第二期（final_amount / second_payment）:
          - 触发条件：候选人通过试用期考核转为正式员工（stage=probation_passed）
          - 支付时间：转正确认后1-2周内支付
          - 典型比例：总佣金的另外 50%

    保证期机制:
        猎头通常提供3-6个月的保证期（guarantee_period_months）。
        保证期内候选人主动离职，猎头需退还部分佣金（refund_status 跟踪退款状态）。
        guarantee_expiry_date 到期后不再享有退款保障。

    字段说明:
        vendor_id:               猎头供应商 ID
        contract_id:             关联的猎头服务合同 ID（确定费率）
        resume_id:               关联的候选人简历 ID（可选，用于溯源）
        candidate_name:          候选人姓名
        position_name:           推荐成功的职位名称
        commission_rate:         佣金费率（0-1），例如 0.15=年薪的15%
        commission_amount:       佣金总金额（元），优先使用此字段；为空则根据 rate 计算
        guarantee_period_months: 保证期月数（0=无保证期，通常3-6个月）
        guarantee_expiry_date:   保证期截止日期（入职日期 + guarantee_period_months）
    """
    vendor_id: int = Field(..., description="猎头供应商ID")
    contract_id: int = Field(..., description="关联合同ID")
    resume_id: Optional[int] = Field(None, description="简历ID")  # 关联招聘简历，方便追溯
    candidate_name: str = Field(..., max_length=50, description="候选人姓名")
    position_name: str = Field(..., max_length=100, description="职位名称")
    commission_rate: Optional[float] = Field(None, ge=0, le=1, description="佣金比例 0-1")
    # 佣金比例，例如 0.15 = 候选人年薪的15%
    commission_amount: Optional[float] = Field(None, ge=0, description="佣金总额")
    # 优先使用此字段，为 None 时根据 commission_rate × 候选人年薪计算
    guarantee_period_months: Optional[int] = Field(None, ge=0, description="保证期（月）")
    guarantee_expiry_date: Optional[date] = Field(None, description="保证期到期日")
    # 入职日期 + guarantee_period_months，到期后退款保障失效


class HeadhunterCommissionCreate(HeadhunterCommissionBase):
    """
    创建猎头佣金记录的请求体。

    对应 API: POST /headhunter-commissions

    在候选人接受 Offer 后创建，初始 stage 为 offer_accepted。
    可以在创建时预设两期付款金额，便于后续执行付款操作。

    字段说明:
        first_payment_amount:  第一期付款金额（元）；为 None 时，system 按 50% 自动计算
        second_payment_amount: 第二期付款金额（元）；为 None 时，system 按剩余金额计算
    """
    first_payment_amount: Optional[float] = Field(None, ge=0, description="首次付款金额")
    # 入职时支付，通常为总佣金的 50%，为 None 则自动按 commission_amount × 50% 计算
    second_payment_amount: Optional[float] = Field(None, ge=0, description="第二次付款金额")
    # 转正后支付，通常为总佣金的剩余50%，为 None 则自动计算


class HeadhunterCommissionUpdate(BaseModel):
    """
    更新猎头佣金记录的请求体（PATCH 语义）。

    对应 API: PUT /headhunter-commissions/{id}

    字段说明:
        stage: 佣金阶段，枚举:
                 offer_accepted:     候选人已接受 Offer 并入职（第一期付款触发点）
                 probation_passed:   候选人已转正（第二期付款触发点）
                 guarantee_expired:  保证期已届满（退款保障失效）
    """
    stage: Optional[str] = Field(
        None,
        description="阶段: offer_accepted/probation_passed/guarantee_expired",
    )  # 阶段推进触发不同的付款操作
    commission_rate: Optional[float] = Field(None, ge=0, le=1)
    commission_amount: Optional[float] = Field(None, ge=0)
    guarantee_period_months: Optional[int] = Field(None, ge=0)
    guarantee_expiry_date: Optional[date] = None


class HeadhunterPaymentRequest(BaseModel):
    """
    猎头佣金付款请求体（记录实际付款操作）。

    对应 API: POST /headhunter-commissions/{id}/pay

    HR 完成财务转账后，通过此接口记录付款信息。
    系统更新对应期次的支付状态为 paid，并记录实际付款日期。

    字段说明:
        payment_order: 付款批次，枚举: 1=第一期（入职后）, 2=第二期（转正后）
        payment_date:  实际付款日期
        amount:        实际付款金额（元）；不填则使用预设的 first/second_payment_amount
    """
    payment_order: int = Field(
        ..., ge=1, le=2, description="付款批次: 1=首次, 2=第二次"
    )  # 1=第一期（入职后），2=第二期（转正后）
    payment_date: date = Field(..., description="付款日期")  # 实际银行转账日期
    amount: Optional[float] = Field(None, ge=0, description="实际付款金额（不填使用预设金额）")
    # 如果实际付款额与预设不同（如协商减少），可在此填写实际金额


class HeadhunterRefundRequest(BaseModel):
    """
    猎头保证期退款请求体（候选人在保证期内离职时发起退款）。

    对应 API: POST /headhunter-commissions/{id}/refund

    当候选人在 guarantee_expiry_date 之前主动离职时，
    HR 向猎头机构发起退款申请，并通过此接口跟踪退款状态。

    字段说明:
        refund_status: 退款状态，枚举:
                         requested: 已向猎头提出退款申请，等待处理
                         completed: 猎头已退款（款项已到账）
    """
    refund_status: str = Field(
        ..., description="退款状态: requested/completed"
    )  # requested=申请退款中, completed=退款已完成


class HeadhunterCommissionOut(HeadhunterCommissionBase):
    """
    返回给前端的猎头佣金完整数据（ORM 序列化）。

    字段说明:
        id:                     记录主键
        stage:                  当前阶段（offer_accepted/probation_passed/guarantee_expired）
        first_payment_amount:   第一期预设付款金额（元）
        first_payment_status:   第一期支付状态（pending=待付/paid=已付）
        first_payment_date:     第一期实际付款日期
        second_payment_amount:  第二期预设付款金额（元）
        second_payment_status:  第二期支付状态（pending=待付/paid=已付/None=未到触发时间）
        second_payment_date:    第二期实际付款日期
        refund_status:          退款状态（none=无需退款/requested=退款申请中/completed=已退款）
        created_at:             记录创建时间
    """
    id: int
    stage: str                                    # offer_accepted | probation_passed | guarantee_expired
    first_payment_amount: Optional[float]         # 第一期付款金额（入职时）
    first_payment_status: str                     # pending | paid
    first_payment_date: Optional[date]            # 第一期实际付款日期
    second_payment_amount: Optional[float]        # 第二期付款金额（转正后）
    second_payment_status: Optional[str]          # pending | paid（None=未到触发阶段）
    second_payment_date: Optional[date]           # 第二期实际付款日期
    refund_status: str                            # none | requested | completed
    created_at: datetime

    model_config = {"from_attributes": True}


class HeadhunterCommissionPage(BaseModel):
    """猎头佣金分页响应。"""
    total: int
    items: list[HeadhunterCommissionOut]


# ───────── VendorEvaluation ─────────

class VendorEvaluationCreate(BaseModel):
    """
    创建供应商详细评估记录的请求体（多维度评分版本）。

    对应 API: POST /vendor-evaluations

    支持按季度或按年度对供应商进行多维度评分，
    综合分数（overall_score）由 service 层计算（四个维度的平均分）。

    字段说明:
        vendor_id:         被评估的供应商 ID
        eval_year:         评估年份（如 2024）
        eval_quarter:      评估季度（1-4）；不填表示年度评估（非季度性评估）
        quality_score:     服务质量评分（1-10，10=最优）
                           评估维度：服务/人员质量是否达到合同要求
        delivery_score:    交付及时性评分（1-10）
                           评估维度：是否按时交付人员/服务，有无延迟
        price_score:       价格合理性评分（1-10）
                           评估维度：收费是否透明合理，性价比如何
        cooperation_score: 合作态度评分（1-10）
                           评估维度：沟通是否顺畅，问题处理是否积极
        comments:          评估备注（具体说明优缺点，用于决策参考）
    """
    vendor_id: int = Field(..., description="供应商ID")
    eval_year: int = Field(..., ge=2000, le=2100, description="评估年份")
    eval_quarter: Optional[int] = Field(None, ge=1, le=4, description="评估季度（1-4，不填=年度）")
    # None = 全年评估，1-4 = 按季度评估
    quality_score: float = Field(5.0, ge=1.0, le=10.0, description="服务质量评分")   # 1-10分
    delivery_score: float = Field(5.0, ge=1.0, le=10.0, description="交付及时性评分")
    price_score: float = Field(5.0, ge=1.0, le=10.0, description="价格合理性评分")
    cooperation_score: float = Field(5.0, ge=1.0, le=10.0, description="合作态度评分")
    comments: Optional[str] = Field(None, description="评估备注")


class VendorEvaluationOut(BaseModel):
    """
    返回给前端的供应商评估记录（ORM 序列化）。

    字段说明:
        id:               评估记录主键
        evaluator_id:     执行评估的 HR/管理员员工 ID（服务端自动记录）
        overall_score:    综合评分（四维度均值，service 层计算: (quality+delivery+price+cooperation)/4）
        vendor_name:      供应商名称（service 层 JOIN 注入）
        evaluator_name:   评估人姓名（service 层 JOIN 注入）
    """
    id: int
    vendor_id: int
    evaluator_id: int                    # 评估人员工 ID（从 JWT token 自动获取）
    eval_year: int
    eval_quarter: Optional[int]
    quality_score: float
    delivery_score: float
    price_score: float
    cooperation_score: float
    overall_score: float                 # 综合得分 = 四个维度的平均值
    comments: Optional[str]
    created_at: datetime
    vendor_name: str = ""                # 供应商名称（JOIN 注入，默认空字符串）
    evaluator_name: str = ""             # 评估人姓名（JOIN 注入）

    model_config = {"from_attributes": True}


class VendorEvaluationPage(BaseModel):
    """供应商评估记录分页响应。"""
    total: int
    items: list[VendorEvaluationOut]


class VendorScoreSummary(BaseModel):
    """
    供应商历史评分汇总（多次评估的平均分统计）。

    对应 API: GET /vendor-evaluations/summary/{vendor_id}

    汇总该供应商所有历史评估记录的平均分，
    用于长期评价供应商的综合表现，辅助续签/淘汰决策。

    字段说明:
        eval_count:              历史评估次数
        avg_quality_score:       历史平均质量评分
        avg_delivery_score:      历史平均交付评分
        avg_price_score:         历史平均价格评分
        avg_cooperation_score:   历史平均合作态度评分
        avg_overall_score:       历史综合平均分
    """
    vendor_id: int
    vendor_name: str
    eval_count: int                    # 历史评估次数（次数越多，数据越可靠）
    avg_quality_score: float           # 历史平均质量分
    avg_delivery_score: float          # 历史平均交付及时性分
    avg_price_score: float             # 历史平均价格合理性分
    avg_cooperation_score: float       # 历史平均合作态度分
    avg_overall_score: float           # 历史综合平均分（四维度均值的均值）


# ───────── VendorBlacklist ─────────

class BlacklistRequest(BaseModel):
    """
    将供应商加入黑名单的请求体。

    对应 API: POST /vendors/blacklist

    供应商被加入黑名单后，其 status 更新为 terminated，
    系统在合同续签和新业务合作时会发出警告提示。
    黑名单记录永久保存（可移出，但保留历史记录）。

    字段说明:
        vendor_id: 要加入黑名单的供应商 ID
        reason:    加入原因（必填，最多500字，如 "多次派遣不合格人员"、"合同违约"）
    """
    vendor_id: int = Field(..., description="供应商ID")
    reason: str = Field(..., max_length=500, description="加入黑名单原因")  # 必填，记录违规事实


class BlacklistOut(BaseModel):
    """
    返回给前端的黑名单记录（ORM 序列化）。

    字段说明:
        id:            黑名单记录主键
        vendor_id:     供应商 ID
        vendor_name:   供应商名称（service 层 JOIN 注入）
        reason:        加入黑名单的原因
        added_at:      加入黑名单的时间
        removed_at:    移出黑名单的时间（None=仍在黑名单中）
        is_active:     是否当前有效（True=仍在黑名单，False=已移出）
        added_by_name: 操作人姓名（service 层 JOIN 注入）
    """
    id: int
    vendor_id: int
    vendor_name: str = ""               # 供应商名称（JOIN 注入）
    reason: str                         # 加入原因
    added_at: datetime                  # 加入黑名单时间
    removed_at: Optional[datetime]      # 移出黑名单时间（None=仍在黑名单中）
    is_active: bool                     # True=当前有效（在黑名单中），False=已移出
    added_by_name: str = ""             # 操作人姓名（JOIN 注入）

    model_config = {"from_attributes": True}


# ───────── Labor Dispatch ─────────


class LaborDispatchProjectBase(BaseModel):
    vendor_id: int = Field(..., description="劳务公司ID")
    project_name: str = Field(..., max_length=200, description="项目名称")
    client_company: str = Field(..., max_length=200, description="客户公司")
    department: Optional[str] = Field(None, max_length=100, description="用工部门")
    owner: Optional[str] = Field(None, max_length=50, description="项目负责人")
    start_date: Optional[date] = Field(None, description="开始日期")
    end_date: Optional[date] = Field(None, description="结束日期")
    status: str = Field("未开始", description="项目状态")


class LaborDispatchProjectCreate(LaborDispatchProjectBase):
    pass


class LaborDispatchProjectUpdate(BaseModel):
    vendor_id: Optional[int] = Field(None, description="劳务公司ID")
    project_name: Optional[str] = Field(None, max_length=200)
    client_company: Optional[str] = Field(None, max_length=200)
    department: Optional[str] = Field(None, max_length=100)
    owner: Optional[str] = Field(None, max_length=50)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[str] = None


class LaborDispatchProjectOut(LaborDispatchProjectBase):
    id: int
    vendor_name: str = ""
    worker_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LaborDispatchProjectPage(BaseModel):
    total: int
    items: list[LaborDispatchProjectOut]


class LaborDispatchWorkerBase(BaseModel):
    vendor_id: int = Field(..., description="劳务公司ID")
    project_id: Optional[int] = Field(None, description="项目ID")
    name: str = Field(..., max_length=50, description="姓名")
    id_card: str = Field(..., max_length=18, description="身份证号")
    phone: str = Field(..., max_length=20, description="手机号")
    position: Optional[str] = Field(None, max_length=100, description="岗位")
    entry_date: Optional[date] = Field(None, description="入场日期")
    exit_date: Optional[date] = Field(None, description="离场日期")
    status: str = Field("在岗", description="状态")


class LaborDispatchWorkerCreate(LaborDispatchWorkerBase):
    pass


class LaborDispatchWorkerUpdate(BaseModel):
    vendor_id: Optional[int] = None
    project_id: Optional[int] = None
    name: Optional[str] = Field(None, max_length=50)
    id_card: Optional[str] = Field(None, max_length=18)
    phone: Optional[str] = Field(None, max_length=20)
    position: Optional[str] = Field(None, max_length=100)
    entry_date: Optional[date] = None
    exit_date: Optional[date] = None
    status: Optional[str] = None


class LaborDispatchWorkerOut(LaborDispatchWorkerBase):
    id: int
    vendor_name: str = ""
    project_name: str = ""
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LaborDispatchWorkerPage(BaseModel):
    total: int
    items: list[LaborDispatchWorkerOut]


class LaborSettlementBase(BaseModel):
    vendor_id: int = Field(..., description="劳务公司ID")
    project_id: Optional[int] = Field(None, description="项目ID")
    settlement_month: str = Field(..., pattern=r"^\d{4}-\d{2}$", description="结算月份 YYYY-MM")
    method: str = Field(..., description="结算方式")
    dispatch_count: int = Field(0, ge=0, description="派遣人数")
    work_hours: float = Field(0, ge=0, description="工时")
    service_fee: float = Field(0, ge=0, description="服务费")
    payable_amount: float = Field(..., description="应付金额")
    status: str = Field("草稿", description="结算状态")


class LaborSettlementCreate(LaborSettlementBase):
    pass


class LaborSettlementUpdate(BaseModel):
    vendor_id: Optional[int] = None
    project_id: Optional[int] = None
    settlement_month: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}$")
    method: Optional[str] = None
    dispatch_count: Optional[int] = Field(None, ge=0)
    work_hours: Optional[float] = Field(None, ge=0)
    service_fee: Optional[float] = Field(None, ge=0)
    payable_amount: Optional[float] = None
    status: Optional[str] = None


class LaborSettlementOut(LaborSettlementBase):
    id: int
    vendor_name: str = ""
    project_name: str = ""
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LaborSettlementPage(BaseModel):
    total: int
    items: list[LaborSettlementOut]
