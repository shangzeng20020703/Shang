"""
模块名称: schemas/payroll.py
模块作用: 薪酬管理域（Payroll Domain）的所有 Pydantic v2 数据传输对象（DTO）定义

功能覆盖:
  - 公司主体管理: CompanyBase / CompanyCreate / CompanyUpdate / CompanyOut
  - 税率条目管理: TaxBracketBase / TaxBracketCreate / TaxBracketOut
  - 税法版本管理: TaxLawVersionBase / TaxLawVersionCreate / TaxLawVersionUpdate / TaxLawVersionOut
  - 员工专项附加扣除: EmployeeTaxDeductionBase / *Create / *Update / *Out
  - 薪酬结构配置: SalaryStructureBase / *Create / *Update / *Out
  - 社保配置: SocialInsuranceConfigBase / *Create / *Update / *Out / ActivateCityYearRequest
  - 月度社保记录: GenerateSocialInsuranceRequest / SocialInsuranceRecordOut
  - 薪酬计算: SalaryRecordBase / SalaryRecordOut / SalaryRecordAdjust / SalaryRecordApprovalAction
  - 批量计算: PayrollCalcRequest / PayrollCalcResult
  - 个税记录: TaxRecordOut / BonusTaxRequest
  - 工资条: PaySlipOut / PaySlipSendRequest / PaySlipQuerySubmit / PaySlipReply
  - 看板数据: SalaryCostSummary / SalaryCostTrend
  - 奖金管理: BonusRecordCreate / BonusRecordOut / BulkBonusItem / BulkBonusCreate
  - 成本分析: DepartmentCostItem / MonthCostItem / CostAnalysisResult

依赖关系:
  - 无其他业务 Schema 依赖（自包含模块）
  - 被 app/api/v1/endpoints/payroll.py 所有路由使用
  - 被 app/services/payroll_service.py 作为输入/输出类型使用
  - SalaryRecordOut 中的数据由考勤月汇总和薪资结构共同计算得出

数据流向:
  HTTP Request Body → *Create/*Update/*Request (Pydantic 验证) → Service Layer (ORM)
  ORM Model → *Out (ConfigDict from_attributes=True) → HTTP Response JSON

关键技术说明:
  DecimalNum 类型:
    Pydantic v2 默认将 Decimal 序列化为字符串（如 "1234.56"），
    而前端期望数字类型（如 1234.56）。
    DecimalNum = Annotated[Decimal, PlainSerializer(float, ...)]
    强制将 Decimal 在 JSON 序列化时转为 float，确保前端兼容性。

薪酬计算核心数学关系（SalaryRecord）:
  应发工资（gross_salary）:
    = base_salary + position_salary
      + performance_salary × performance_coefficient
      + total_allowance
      + overtime_pay_weekday + overtime_pay_weekend + overtime_pay_holiday
      + adjustment_amount（手动调整额，可正可负）

  个人应纳税所得额（taxable_income）:
    = gross_salary - social_insurance_employee - housing_fund_employee
      - special_deduction_total（专项附加扣除）- monthly_tax_free（起征点5000元）

  实发工资（net_salary）:
    = gross_salary - social_insurance_employee - housing_fund_employee
      - tax_amount + adjustment_amount
    （tax_amount 由个税记录（TaxRecord）的累计预扣法计算）

社保结构（SocialInsuranceRecord）:
  个人缴纳 = 养老（personal）+ 医疗（medical）+ 失业（unemployment）+ 公积金（housing_fund）
  公司缴纳 = 养老 + 医疗 + 失业 + 工伤（injury）+ 生育（maternity）+ 公积金
  total_employee = 个人五险一金合计（从 gross_salary 中扣除）
  total_company = 公司五险一金合计（公司额外成本，不从员工工资扣）
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field, ConfigDict, PlainSerializer

# Pydantic v2 serializes Decimal as string in JSON by default.
# DecimalNum forces float serialization so the frontend receives numbers, not strings.
# 注意: 此类型仅影响 JSON 序列化输出，内部计算仍使用 Decimal 保证精度
DecimalNum = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


# ============================================================
# Company 公司主体
# ============================================================

class CompanyBase(BaseModel):
    """
    用途: 公司主体写操作 Schema 的基类
    业务: 本公司可能存在多个法人主体（如北京公司、东莞公司），
          薪资计算时需要关联公司信息（影响社保城市、税务主体等）
    字段说明:
      - name: 公司全称（营业执照上的名称）
      - short_name: 公司简称（前端展示用）
      - tax_id: 统一社会信用代码（18位），用于税务申报
      - city / province: 注册地城市和省份（影响社保参保城市判断）
      - legal_rep: 法定代表人姓名
    """
    name: str = Field(..., max_length=100, description="公司全称")
    short_name: Optional[str] = Field(default=None, max_length=50, description="公司简称")
    company_type: Optional[str] = Field(default=None, max_length=30, description="公司类型")
    legal_entity_name: Optional[str] = Field(default=None, max_length=100, description="法人主体名称")
    tax_id: Optional[str] = Field(default=None, max_length=30, description="统一社会信用代码")
    parent_company_id: Optional[int] = Field(default=None, description="上级公司ID")
    company_owner: Optional[str] = Field(default=None, max_length=50, description="公司负责人")
    city: Optional[str] = Field(default=None, max_length=50, description="注册城市")
    province: Optional[str] = Field(default=None, max_length=50, description="注册省份")
    address: Optional[str] = Field(default=None, max_length=500)
    legal_rep: Optional[str] = Field(default=None, max_length=50, description="法定代表人")
    contact_phone: Optional[str] = Field(default=None, max_length=30)
    contact_email: Optional[str] = Field(default=None, max_length=100)
    is_active: bool = True


class CompanyCreate(CompanyBase):
    """
    用途: POST /api/v1/payroll/companies 新建公司主体的请求体
    """
    pass


class CompanyUpdate(BaseModel):
    """
    用途: PATCH /api/v1/payroll/companies/{id} 更新公司信息的请求体
    业务: 所有字段均为可选，只传需要修改的字段
    """
    name: Optional[str] = Field(default=None, max_length=100)
    short_name: Optional[str] = Field(default=None, max_length=50)
    company_type: Optional[str] = Field(default=None, max_length=30)
    legal_entity_name: Optional[str] = Field(default=None, max_length=100)
    tax_id: Optional[str] = Field(default=None, max_length=30)
    parent_company_id: Optional[int] = None
    company_owner: Optional[str] = Field(default=None, max_length=50)
    city: Optional[str] = Field(default=None, max_length=50)
    province: Optional[str] = Field(default=None, max_length=50)
    address: Optional[str] = None
    legal_rep: Optional[str] = Field(default=None, max_length=50)
    contact_phone: Optional[str] = Field(default=None, max_length=30)
    contact_email: Optional[str] = Field(default=None, max_length=100)
    is_active: Optional[bool] = None


class CompanyOut(CompanyBase):
    """
    用途: 公司主体详情/列表接口的响应体，从 ORM Company 模型序列化
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# ============================================================
# TaxBracket 税率条目
# ============================================================

class TaxBracketBase(BaseModel):
    """
    用途: 个税税率条目写操作 Schema 的基类
    业务: 描述个税税率表中的一个税档（阶梯税率表的一行）
    中国个税采用超额累进税率，通过 7 档税率 + 速算扣除数计算
    字段说明:
      - bracket_type: 税率表类型
          "cumulative": 综合所得累计预扣法（月度薪资计算用）
          "bonus": 全年一次性奖金单独计税法
      - upper_bound: 该税档的应纳税所得额上限（最高档用一个极大值如 9999999）
      - rate: 税率（0~1 之间的小数，如0.03表示3%，0.45表示45%）
      - quick_deduction: 速算扣除数（简化计算用，避免逐档累进计算）
        税额 = 应纳税所得额 × 税率 - 速算扣除数
      - sort_order: 排序序号（从低税档到高税档排序）
    """
    bracket_type: str = Field(
        ..., pattern=r"^(cumulative|bonus)$",
        description="cumulative=综合所得累计预扣, bonus=全年一次性奖金"
    )
    upper_bound: Decimal = Field(..., gt=0, description="应纳税所得额上限")
    rate: Decimal = Field(..., ge=0, le=1, description="税率")                      # 0~1，如0.03=3%
    quick_deduction: Decimal = Field(default=Decimal("0"), ge=0, description="速算扣除数")
    sort_order: int = Field(default=0, ge=0)


class TaxBracketCreate(TaxBracketBase):
    """
    用途: 创建税率条目的请求体（通常作为 TaxLawVersionCreate.brackets 的元素）
    """
    pass


class TaxBracketOut(TaxBracketBase):
    """
    用途: 税率条目响应体，从 ORM TaxBracket 模型序列化
    version_id: 该条目所属的税法版本 ID（外键）
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_id: int   # 关联的税法版本 ID


# ============================================================
# TaxLawVersion 税法版本
# ============================================================

class TaxLawVersionBase(BaseModel):
    """
    用途: 税法版本写操作 Schema 的基类
    业务: 中国个税法规会不定期修订（如2019年个税改革），
          本系统通过版本管理支持历史税率和新税率并存
    字段说明:
      - name: 版本描述名称，如"2019个税改革版"、"2024年版"
      - country_code: 国家代码，默认"CN"（为未来国际化扩展预留）
      - effective_date: 该税法版本的生效日期
      - end_date: 该税法版本的结束日期（None=当前有效版本）
      - monthly_tax_free: 月度基本减除费用（即"起征点"，目前为5000元/月）
        年度累计预扣时 = monthly_tax_free × 已发薪月数
      - is_active: 是否为当前启用版本（薪资计算时优先使用 is_active=True 的版本）
    """
    name: str = Field(..., max_length=100, description="版本名称: 如2019个税改革版")
    country_code: str = Field(default="CN", max_length=10)
    effective_date: date = Field(..., description="生效日期")
    end_date: Optional[date] = Field(default=None, description="结束日期(NULL=当前有效)")
    monthly_tax_free: Decimal = Field(
        default=Decimal("5000"), ge=0, description="月基本减除费用"
    )  # 个税起征点，目前为5000元/月
    description: Optional[str] = None
    is_active: bool = True


class TaxLawVersionCreate(TaxLawVersionBase):
    """
    用途: POST /api/v1/payroll/tax-law-versions 创建税法版本的请求体
    业务: 创建版本时可同步传入税率条目（brackets），也可后续单独维护
    brackets: 税率条目列表，默认为空（可后续通过税率条目接口单独添加）
    """
    brackets: list[TaxBracketCreate] = Field(default=[], description="税率条目(可选,后续单独维护)")


class TaxLawVersionUpdate(BaseModel):
    """
    用途: PATCH /api/v1/payroll/tax-law-versions/{id} 更新税法版本的请求体
    业务: 通常只更新 end_date（使旧版本失效）、monthly_tax_free（调整起征点）
    注意: effective_date 不允许修改（不在此 Schema 中），版本生效日期一旦确定不变
    """
    name: Optional[str] = Field(default=None, max_length=100)
    end_date: Optional[date] = None                              # 设置结束日期使版本失效
    monthly_tax_free: Optional[Decimal] = Field(default=None, ge=0)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class TaxLawVersionOut(TaxLawVersionBase):
    """
    用途: 税法版本详情接口的响应体，从 ORM TaxLawVersion 模型序列化
    brackets: 内嵌该版本的所有税率条目（按 sort_order 排序）
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    brackets: list[TaxBracketOut] = []   # 内嵌税率条目列表
    created_at: datetime


# ============================================================
# EmployeeTaxDeduction 员工专项附加扣除
# ============================================================

# 专项附加扣除类型枚举列表（用于前端下拉选项）
DEDUCTION_TYPES = [
    "子女教育", "继续教育", "大病医疗", "住房贷款利息",
    "住房租金", "赡养老人", "婴幼儿照护", "其他"
]


class EmployeeTaxDeductionBase(BaseModel):
    """
    用途: 员工专项附加扣除写操作 Schema 的基类
    业务: 2019年个税改革引入专项附加扣除，员工可申报多项扣除以减少应纳税额
    个税累计预扣时：cumulative_taxable -= 各月 monthly_amount 之和
    字段说明:
      - deduction_type: 扣除类型（见 DEDUCTION_TYPES 列表）
      - monthly_amount: 每月可扣除金额（如子女教育2000元/月/孩）
      - start_year/start_month: 扣除开始年月
      - end_year/end_month: 扣除结束年月（None=持续有效直至手动停止）
      - is_active: False=暂停该项扣除（如子女毕业后停止子女教育扣除）
    """
    employee_id: int
    deduction_type: str = Field(
        ..., description="扣除类型: 子女教育/继续教育/大病医疗/住房贷款利息/住房租金/赡养老人/婴幼儿照护/其他"
    )
    monthly_amount: Decimal = Field(..., ge=0, description="每月扣除金额")  # 每月可扣除的金额
    start_year: int = Field(..., ge=2019, le=2100)    # 扣除开始年份（不早于2019年）
    start_month: int = Field(..., ge=1, le=12)
    end_year: Optional[int] = Field(default=None, ge=2019, le=2100)    # 扣除结束年份（None=持续）
    end_month: Optional[int] = Field(default=None, ge=1, le=12)
    description: Optional[str] = None
    is_active: bool = True


class EmployeeTaxDeductionCreate(EmployeeTaxDeductionBase):
    """
    用途: POST /api/v1/payroll/tax-deductions 新建员工专项附加扣除的请求体
    """
    pass


class EmployeeTaxDeductionUpdate(BaseModel):
    """
    用途: PATCH /api/v1/payroll/tax-deductions/{id} 更新专项附加扣除的请求体
    业务: 常见操作：调整金额（如孩子增加则子女教育翻倍）、设置结束日期、停用
    """
    monthly_amount: Optional[Decimal] = Field(default=None, ge=0)
    end_year: Optional[int] = Field(default=None, ge=2019, le=2100)
    end_month: Optional[int] = Field(default=None, ge=1, le=12)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class EmployeeTaxDeductionOut(EmployeeTaxDeductionBase):
    """
    用途: 专项附加扣除详情/列表接口的响应体，从 ORM EmployeeTaxDeduction 模型序列化
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# ============================================================
# SalaryStructure 薪酬结构
# ============================================================

class SalaryStructureBase(BaseModel):
    """
    用途: 员工薪酬结构写操作 Schema 的基类
    业务: 定义员工的薪资组成，是薪资计算的核心配置数据
    薪资结构在员工薪资调整时新增一条记录（不修改历史），effective_date 控制生效日期

    薪资组成说明（各字段含义）:
      固定工资部分（每月固定发放）:
        base_salary: 基础工资（底薪），不随绩效变动
        position_salary: 岗位工资，与职级/岗位绑定
        performance_salary: 绩效工资基数（需乘以 performance_coefficient 才是实际发放额）

      绩效部分:
        performance_coefficient: 绩效系数（0~5.0），由绩效评估结果决定
          实际绩效工资 = performance_salary × performance_coefficient
          系数1.0=正常，>1.0=超出预期，<1.0=未达标

      补贴部分（各项独立，累加得 total_allowance）:
        night_shift_allowance: 夜班补贴
        high_temp_allowance: 高温补贴（夏季政策性补贴）
        project_allowance: 项目津贴（驻场或特殊项目）
        meal_allowance: 餐补
        transport_allowance: 交通补贴
        housing_allowance: 住房补贴
        other_allowance: 其他补贴（兜底字段）

      补贴计算方式 (allowance_calc_method):
        "fixed_monthly": 按月固定发放（与出勤无关）
        "actual_attendance": 按实际出勤天数折算（缺勤则扣减补贴）

      加班基数:
        overtime_base: 加班工资计算基数（单独设置，通常低于实际月工资）
          加班时薪 = overtime_base / 21.75 / 8（按月21.75工作日、每日8小时计算）

      生效日期:
        effective_date: 该薪酬结构的生效日期（同一员工可有多条记录，取最新生效的）
    """
    employee_id: int
    base_salary: Decimal = Field(default=Decimal("0"), ge=0, description="基础工资")
    position_salary: Decimal = Field(default=Decimal("0"), ge=0, description="岗位工资")
    performance_salary: Decimal = Field(default=Decimal("0"), ge=0, description="绩效工资基数")
    performance_coefficient: Decimal = Field(
        default=Decimal("1.00"), ge=0, le=Decimal("5.00"), description="绩效系数"
    )  # 范围0~5，1.0为标准，由绩效评估确定
    night_shift_allowance: Decimal = Field(default=Decimal("0"), ge=0)
    high_temp_allowance: Decimal = Field(default=Decimal("0"), ge=0)
    project_allowance: Decimal = Field(default=Decimal("0"), ge=0)
    meal_allowance: Decimal = Field(default=Decimal("0"), ge=0)
    transport_allowance: Decimal = Field(default=Decimal("0"), ge=0)
    housing_allowance: Decimal = Field(default=Decimal("0"), ge=0)
    other_allowance: Decimal = Field(default=Decimal("0"), ge=0)
    allowance_calc_method: str = Field(
        default="fixed_monthly",
        pattern=r"^(actual_attendance|fixed_monthly)$",
        description="补贴计算方式",
    )  # 按实际出勤折算 or 按月固定发放
    overtime_base: Decimal = Field(default=Decimal("0"), ge=0, description="加班工资计算基数")
    adjustment_amount: Decimal = Field(default=Decimal("0"), description="结构调整项(正=补发, 负=扣款)")
    effective_date: date   # 该薪资结构的生效日期


class SalaryStructureCreate(SalaryStructureBase):
    """
    用途: POST /api/v1/payroll/salary-structures 新建薪酬结构的请求体
    业务: 直接继承 SalaryStructureBase，无额外字段
    注意: 每次薪资调整都应新建一条记录而非修改现有记录，保留历史
    """
    pass


class SalaryStructureUpdate(BaseModel):
    """
    用途: PATCH /api/v1/payroll/salary-structures/{id} 更新薪酬结构的请求体
    业务: 通常建议新建记录（薪资调整语义），此接口适用于纠正录入错误
    """
    base_salary: Optional[Decimal] = Field(default=None, ge=0)
    position_salary: Optional[Decimal] = Field(default=None, ge=0)
    performance_salary: Optional[Decimal] = Field(default=None, ge=0)
    performance_coefficient: Optional[Decimal] = Field(default=None, ge=0, le=Decimal("5.00"))
    night_shift_allowance: Optional[Decimal] = Field(default=None, ge=0)
    high_temp_allowance: Optional[Decimal] = Field(default=None, ge=0)
    project_allowance: Optional[Decimal] = Field(default=None, ge=0)
    meal_allowance: Optional[Decimal] = Field(default=None, ge=0)
    transport_allowance: Optional[Decimal] = Field(default=None, ge=0)
    housing_allowance: Optional[Decimal] = Field(default=None, ge=0)
    other_allowance: Optional[Decimal] = Field(default=None, ge=0)
    allowance_calc_method: Optional[str] = Field(
        default=None, pattern=r"^(actual_attendance|fixed_monthly)$"
    )
    overtime_base: Optional[Decimal] = Field(default=None, ge=0)
    adjustment_amount: Optional[Decimal] = Field(default=None, description="结构调整项(正=补发, 负=扣款)")
    effective_date: Optional[date] = None


class SalaryStructureOut(SalaryStructureBase):
    """
    用途: 薪酬结构详情/历史列表接口的响应体，从 ORM SalaryStructure 模型序列化
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# ============================================================
# SocialInsuranceConfig 社保配置
# ============================================================

class SocialInsuranceConfigBase(BaseModel):
    """
    用途: 社保配置写操作 Schema 的基类
    业务: 按城市+年度+险种维度配置社保缴纳比例和基数上下限
          各城市、各年度的社保政策不同，需独立配置
    险种类型（insurance_type 字段）:
      - 养老: 养老保险
      - 医疗: 医疗保险（含大病医疗附加）
      - 失业: 失业保险
      - 工伤: 工伤保险（仅公司缴纳，个人不缴）
      - 生育: 生育保险（仅公司缴纳，个人不缴）
      - 公积金: 住房公积金
    字段说明:
      - company_id: None=适用所有公司，指定 ID=仅适用该公司
      - employee_rate / company_rate: 个人/公司缴纳比例（0~1，如0.08=8%）
      - base_min / base_max: 缴费基数下限和上限（员工实际缴费基数在此范围内取值）
        实际缴费基数 = max(base_min, min(employee.social_insurance_base, base_max))
    """
    company_id: Optional[int] = Field(default=None, description="公司ID(NULL=适用所有公司)")
    city: str = Field(..., max_length=50, description="城市: 北京/东莞")
    insurance_type: str = Field(
        ..., max_length=20, description="险种: 养老/医疗/失业/工伤/生育/公积金"
    )
    employee_rate: Decimal = Field(..., ge=0, le=1, description="个人缴纳比例")  # 如0.08=8%
    company_rate: Decimal = Field(..., ge=0, le=1, description="公司缴纳比例")
    base_min: Decimal = Field(..., ge=0, description="缴纳基数下限")   # 低于此值按下限计算
    base_max: Decimal = Field(..., ge=0, description="缴纳基数上限")   # 超过此值按上限计算
    year: int = Field(..., ge=2020, le=2100)
    effective_date: Optional[date] = Field(default=None, description="生效日期")
    is_active: bool = True


class SocialInsuranceConfigCreate(SocialInsuranceConfigBase):
    """
    用途: POST /api/v1/payroll/social-insurance-configs 新建社保配置的请求体
    """
    pass


class SocialInsuranceConfigUpdate(BaseModel):
    """
    用途: PATCH /api/v1/payroll/social-insurance-configs/{id} 更新社保配置的请求体
    业务: 政策调整时更新比例或基数上下限，effective_date 控制新政策生效时间
    """
    employee_rate: Optional[Decimal] = Field(default=None, ge=0, le=1)
    company_rate: Optional[Decimal] = Field(default=None, ge=0, le=1)
    base_min: Optional[Decimal] = Field(default=None, ge=0)
    base_max: Optional[Decimal] = Field(default=None, ge=0)
    effective_date: Optional[date] = None
    is_active: Optional[bool] = None


class SocialInsuranceConfigOut(SocialInsuranceConfigBase):
    """
    用途: 社保配置详情/列表接口的响应体，从 ORM SocialInsuranceConfig 模型序列化
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class ActivateCityYearRequest(BaseModel):
    """
    用途: POST /api/v1/payroll/social-insurance-configs/activate 激活指定城市年度配置的请求体
    业务: 一次性激活某城市某年度的所有险种配置，并自动停用该城市其他年度的配置
    使用场景: 每年社保政策更新后，激活新年度配置并停用旧年度
    """
    city: str = Field(..., max_length=50)
    year: int = Field(..., ge=2020, le=2100)


# ============================================================
# SocialInsuranceRecord 月度社保数据
# ============================================================

class GenerateSocialInsuranceRequest(BaseModel):
    """
    用途: POST /api/v1/payroll/social-insurance/generate 生成月度社保数据的请求体
    业务: 为指定年月的所有在职员工计算五险一金数据，生成 SocialInsuranceRecord
    通常在薪资计算（PayrollCalcRequest）之前执行，作为前置步骤
    """
    year: int = Field(..., ge=2020, le=2100, description="年份")
    month: int = Field(..., ge=1, le=12, description="月份")


class SocialInsuranceRecordOut(BaseModel):
    """
    用途: 月度社保数据查询接口的响应体，从 ORM SocialInsuranceRecord 模型序列化
    业务: 记录员工某月各险种的具体缴纳金额（个人和公司两部分）

    字段分组（个人缴纳部分 — 从员工 gross_salary 中扣除）:
      pension_employee: 养老保险个人缴纳
      medical_employee: 医疗保险个人缴纳
      unemployment_employee: 失业保险个人缴纳
      housing_fund_employee: 公积金个人缴纳
      total_employee: 个人五险一金合计 = 上述四项之和

    字段分组（公司缴纳部分 — 不从员工工资扣除，是公司额外人工成本）:
      pension_company: 养老保险公司缴纳
      medical_company: 医疗保险公司缴纳
      unemployment_company: 失业保险公司缴纳
      injury_company: 工伤保险公司缴纳（个人不缴）
      maternity_company: 生育保险公司缴纳（个人不缴）
      housing_fund_company: 公积金公司缴纳
      total_company: 公司五险一金合计 = 上述六项之和

    缴费基数字段:
      social_insurance_base: 五险缴费基数（在 base_min 和 base_max 之间取值）
      housing_fund_base: 公积金缴费基数（可与社保基数不同）

    由 API 层注入的员工信息（非 ORM 直接映射）:
      employee_name / employee_no / department_name
    所有金额字段使用 DecimalNum（序列化为 JSON 时输出 float）
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    year: int
    month: int
    city: str = ""                                     # 社保缴纳城市

    # 缴费基数
    social_insurance_base: DecimalNum = Decimal("0")   # 五险缴费基数
    housing_fund_base: DecimalNum = Decimal("0")        # 公积金缴费基数

    # 个人缴纳部分（从 gross_salary 中扣除）
    pension_employee: DecimalNum = Decimal("0")         # 养老保险个人
    pension_company: DecimalNum = Decimal("0")          # 养老保险公司
    medical_employee: DecimalNum = Decimal("0")         # 医疗保险个人
    medical_company: DecimalNum = Decimal("0")          # 医疗保险公司
    unemployment_employee: DecimalNum = Decimal("0")    # 失业保险个人
    unemployment_company: DecimalNum = Decimal("0")     # 失业保险公司
    injury_company: DecimalNum = Decimal("0")           # 工伤保险公司（个人不缴）
    maternity_company: DecimalNum = Decimal("0")        # 生育保险公司（个人不缴）
    housing_fund_employee: DecimalNum = Decimal("0")    # 公积金个人
    housing_fund_company: DecimalNum = Decimal("0")     # 公积金公司
    total_employee: DecimalNum = Decimal("0")           # 个人五险一金合计
    total_company: DecimalNum = Decimal("0")            # 公司五险一金合计

    # Employee info（由 API 层注入）
    employee_name: Optional[str] = None
    employee_no: Optional[str] = None
    department_name: Optional[str] = None


# ============================================================
# SalaryRecord 薪酬记录
# ============================================================

class SalaryRecordBase(BaseModel):
    """
    用途: 薪酬记录写操作 Schema 的基类（仅含标识字段）
    业务: 一条 SalaryRecord 代表某员工某月的一次薪资计算结果
    实际的薪资明细字段在 SalaryRecordOut 中（只读，由计算引擎填充）
    """
    employee_id: int
    year: int
    month: int = Field(..., ge=1, le=12)


class SalaryInsuranceItemOut(BaseModel):
    """ESS 工资条中的个人社保分项。"""

    label: str
    amount: DecimalNum


class SalaryRecordOut(BaseModel):
    """
    用途: 薪酬记录查询接口的响应体，从 ORM SalaryRecord 模型序列化
    业务: 描述一个员工某月的完整薪资明细，由薪资计算引擎（PayrollCalcRequest）生成

    薪资计算公式（关键字段数学关系）:
      gross_salary（应发工资）= base_salary + position_salary
                                + performance_salary × performance_coefficient
                                + total_allowance（所有补贴之和）
                                + overtime_pay_weekday（工作日加班工资）
                                + overtime_pay_weekend（周末加班工资）
                                + overtime_pay_holiday（节假日加班工资）
                               [不含 adjustment_amount，该项单独核算]

      taxable_income（应纳税所得额）= gross_salary
                                       - social_insurance_employee（个人社保）
                                       - housing_fund_employee（个人公积金）
                                       - special_deduction_total（专项附加扣除合计）
                                       - monthly_tax_free（起征点5000）
                                      [年累计预扣法，本月税 = 年累计应纳税 - 已扣税额]

      net_salary（实发工资）= gross_salary
                               - social_insurance_employee
                               - housing_fund_employee
                               - tax_amount（本月个税）
                               + adjustment_amount（手动调整，正=补发/负=扣款）

    字段说明:
      - version: 同一员工同月可能重算多次（如数据修正），version 递增标记
      - proration_type: 入/离职月份折算方式（calendar_day=自然日/workday=工作日）
      - proration_days: 折算天数（入职不满月时有效）
      - status: 计算状态（draft=草稿/pending=待审批/approved=已审批/paid=已发放/rejected=已驳回）
      - anomaly_flags_json: JSON 字符串，记录该月薪资计算中的异常标记（如"基数超上限"）
      - snapshot_id: 关联的薪酬结构快照 ID（计算时锁定当时的薪资配置）
      - company_id / tax_law_version_id: 计算时使用的公司主体和税法版本
    所有金额字段使用 DecimalNum（序列化为 JSON 时输出 float）
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    year: int
    month: int
    version: int                              # 同月重算时版本号递增（从1开始）

    # 薪资组成（应发部分）
    base_salary: DecimalNum                   # 基础工资
    position_salary: DecimalNum               # 岗位工资
    performance_salary: DecimalNum            # 绩效工资基数
    performance_coefficient: DecimalNum       # 绩效系数（实发 = 基数 × 系数）
    total_allowance: DecimalNum               # 各项补贴合计

    # 加班工资（三种类型分开记录，便于核查）
    overtime_pay_weekday: DecimalNum          # 工作日加班工资（1.5倍率计算）
    overtime_pay_weekend: DecimalNum          # 周末加班工资（2.0倍率）
    overtime_pay_holiday: DecimalNum          # 法定节假日加班工资（3.0倍率）

    gross_salary: DecimalNum                  # 应发工资合计（见上方公式）

    # 扣除项（个人承担部分）
    social_insurance_employee: DecimalNum     # 个人社保（养老+医疗+失业，不含工伤生育）
    housing_fund_employee: DecimalNum         # 个人公积金
    # 以下为公司承担部分（不从员工工资扣，但影响企业成本统计）
    social_insurance_company: DecimalNum      # 公司社保合计
    housing_fund_company: DecimalNum          # 公司公积金

    # 个税相关
    taxable_income: DecimalNum                # 应纳税所得额（累计预扣法计算后的当月应税额）
    tax_amount: DecimalNum                    # 本月应缴个税
    special_deduction_total: DecimalNum       # 专项附加扣除合计（子女教育+住房等）

    net_salary: DecimalNum                    # 实发工资（税后到手金额，见上方公式）

    # 调整项
    adjustment_amount: DecimalNum             # 手动调整金额（正=补发，负=扣款）
    adjustment_reason: Optional[str]          # 调整原因说明

    # 入/离职月份折算
    proration_type: Optional[str]             # 折算方式（calendar_day/workday）
    proration_days: Optional[int]             # 实际工作天数（折算用）

    # 状态和元数据
    status: str                               # draft/pending/approved/paid/rejected
    anomaly_flags_json: Optional[str]         # JSON 字符串，记录计算异常标记
    snapshot_id: Optional[int]                # 薪酬结构快照 ID（计算时锁定配置）
    company_id: Optional[int] = None          # 关联公司主体 ID
    tax_law_version_id: Optional[int] = None  # 使用的税法版本 ID
    social_insurance_detail: list[SalaryInsuranceItemOut] = []  # ESS 展示用社保明细
    created_at: datetime


class SalaryRecordAdjust(BaseModel):
    """
    用途: POST /api/v1/payroll/salary-records/{id}/adjust 手动调整薪资记录的请求体
    业务: HR 对已计算的薪资进行补发或扣款调整（如多发了餐补需扣回）
    字段说明:
      - adjustment_amount: 正数=补发（增加实发），负数=扣款（减少实发）
      - adjustment_reason: 调整原因（必填，审计留存）
    注意: 调整后需重新计算税额（tax_amount）和实发金额（net_salary）
    """
    adjustment_amount: Decimal = Field(..., description="调整金额(正=补发, 负=扣款)")
    adjustment_reason: str = Field(..., min_length=1, max_length=500)


class SalaryRecordApprovalAction(BaseModel):
    """
    用途: POST /api/v1/payroll/salary-records/{id}/approve 薪资审批操作的请求体
    业务: 薪资计算完成后需 HR/财务审批，审批通过后状态变为 approved，可发放
    action 枚举值: "approve"=审批通过 / "reject"=驳回（需重新计算）
    """
    action: str = Field(..., pattern=r"^(approve|reject)$")
    comment: Optional[str] = None   # 审批意见（驳回时建议填写原因）


# ============================================================
# PayrollCalc 薪酬计算请求
# ============================================================

class PayrollCalcRequest(BaseModel):
    """
    用途: POST /api/v1/payroll/calculate 批量计算薪资的请求体
    业务: 触发薪资计算引擎，为指定月份的员工批量计算薪资
    计算引擎会自动读取:
      - 员工薪资结构（SalaryStructure，取 effective_date <= 月末 的最新一条）
      - 考勤月汇总（AttendanceMonthSummary，需已锁定）
      - 月度社保数据（SocialInsuranceRecord，需已生成）
      - 个税累计数据（TaxRecord，用于累计预扣法）
    字段说明:
      - employee_ids: 指定员工列表（None=全量计算所有在职员工）
      - proration_type: 入/离职月份的薪资折算方式
          "calendar_day": 按自然日折算（适用大多数场景）
          "workday": 按工作日折算（某些合同约定）
    """
    year: int
    month: int = Field(..., ge=1, le=12)
    employee_ids: Optional[list[int]] = Field(
        default=None, description="指定员工ID列表, 为空则全量计算"
    )
    proration_type: str = Field(
        default="calendar_day",
        pattern=r"^(calendar_day|workday)$",
        description="入离职折算方式",
    )


class PayrollCalcResult(BaseModel):
    """
    用途: 薪资批量计算接口的响应体（计算结果摘要）
    业务: 汇总本次计算的整体统计数据，以及每个员工的薪资明细
    字段说明:
      - total_employees: 本次计算的员工总数
      - total_gross: 所有员工应发工资合计
      - total_net: 所有员工实发工资合计
      - total_tax: 所有员工个税合计
      - total_social_insurance_company: 公司社保合计（人工成本参考）
      - total_housing_fund_company: 公司公积金合计
      - anomaly_count: 计算中出现异常的员工数（需人工核查）
      - records: 每个员工的详细薪资记录列表（SalaryRecordOut）
    """
    total_employees: int
    total_gross: Decimal              # 应发工资总额
    total_net: Decimal                # 实发工资总额
    total_tax: Decimal                # 个税总额
    total_social_insurance_company: Decimal   # 公司社保合计（成本）
    total_housing_fund_company: Decimal       # 公司公积金合计（成本）
    anomaly_count: int                # 计算异常员工数（需核查）
    records: list[SalaryRecordOut]    # 各员工薪资明细


# ============================================================
# TaxRecord 个税记录
# ============================================================

class TaxRecordOut(BaseModel):
    """
    用途: GET /api/v1/payroll/tax-records 个税记录查询接口的响应体
    业务: 记录员工某月个税的详细计算过程数据（累计预扣法）
    中国个税采用累计预扣法（每月预扣，年终汇算清缴）:
      1. 累加 1月 到 当月 的收入、扣除等
      2. 计算年度累计应纳税额
      3. 减去 1月 到 上月 已预扣税额
      4. 得出当月应预扣税额（current_month_tax）

    字段说明（均为截至当月的年度累计值）:
      - cumulative_income: 年度累计收入（gross_salary 累加）
      - cumulative_tax_free: 年度累计基本减除费用（5000元/月 × 已发薪月数）
      - cumulative_deductions: 年度累计专项扣除（社保公积金个人部分累加）
      - cumulative_special_deductions: 年度累计专项附加扣除（子女教育等累加）
      - cumulative_taxable: 年度累计应纳税所得额
        = cumulative_income - cumulative_tax_free
          - cumulative_deductions - cumulative_special_deductions
      - cumulative_tax: 年度累计应纳个税（查税率表计算）
      - current_month_tax: 本月实际预扣税额
        = cumulative_tax - 前月 cumulative_tax
      - tax_method: 计税方法（"cumulative"=综合所得累计预扣）
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    year: int
    month: int
    cumulative_income: DecimalNum             # 年度累计收入
    cumulative_tax_free: DecimalNum           # 年度累计基本减除费用（5000×月数）
    cumulative_deductions: DecimalNum         # 年度累计专项扣除（社保公积金个人部分）
    cumulative_special_deductions: DecimalNum # 年度累计专项附加扣除（子女教育等）
    cumulative_taxable: DecimalNum            # 年度累计应纳税所得额
    cumulative_tax: DecimalNum                # 年度累计应纳个税
    current_month_tax: DecimalNum             # 本月预扣个税（= 本月累计税 - 上月累计税）
    tax_method: str                           # 计税方法：cumulative（综合所得累计预扣）
    tax_law_version_id: Optional[int] = None  # 使用的税法版本 ID
    created_at: datetime


class TaxImportBatchOut(BaseModel):
    """
    用途: 个税数据 Tab 中按公司+月份查看已上传税款表内容。
    业务: 保留线下税务表的原始表头与行数据，仅支持上传覆盖，不支持编辑删除。
    """

    id: int
    company_id: int
    company_name: str
    year: int
    month: int
    sheet_name: Optional[str] = None
    source_filename: str
    headers: list[str]
    row_count: int
    uploaded_by: Optional[int] = None
    uploaded_by_name: Optional[str] = None
    uploaded_at: datetime
    rows: list[dict[str, Any]]


class BonusTaxRequest(BaseModel):
    """
    用途: POST /api/v1/payroll/bonus-tax 年终奖计税请求体
    业务: 计算年终奖的个税（支持两种计税方式，员工可选择对自己更有利的方式）
    字段说明:
      - employee_id: 计税员工 ID
      - bonus_amount: 年终奖金额（必须大于0）
      - tax_method: 计税方式
          "cumulative": 并入综合所得（与当月薪资合并计算当年个税，适合奖金少的情况）
          "separate_bonus": 全年一次性奖金单独计税（单独查税率表，适合奖金多的情况）
      - year: 年终奖对应的年份（影响累计收入计算）
    计税结果通过 BonusRecordOut 返回（含税额和实发净额）
    """
    employee_id: int
    bonus_amount: Decimal = Field(..., gt=0)
    tax_method: str = Field(
        ...,
        pattern=r"^(cumulative|separate_bonus)$",
        description="计税方式: cumulative=并入综合所得, separate_bonus=单独计税",
    )
    year: int


# ============================================================
# PaySlip 工资条
# ============================================================

class PaySlipOut(BaseModel):
    """
    用途: GET /api/v1/payroll/payslips 工资条查询接口的响应体
    业务: 每条 SalaryRecord 审批通过后，系统为员工生成对应的工资条
          员工可查看工资条，并可提交疑问，HR 在线回复
    字段说明:
      - salary_record_id: 关联的薪资记录 ID（一对一）
      - sent_at: 工资条发送时间（None=未发送）
      - viewed_at: 员工查看时间（None=未查看）
      - query_status: 疑问状态（no_query=无疑问/pending=待回复/replied=已回复/closed=已关闭）
      - query_content: 员工提交的疑问内容
      - reply_content: HR 回复内容
      - reply_deadline: 回复截止日期
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    salary_record_id: int
    employee_id: int
    year: int
    month: int
    sent_at: Optional[datetime]               # 工资条发送时间
    viewed_at: Optional[datetime]             # 员工查看时间
    query_status: str                         # no_query/pending/replied/closed
    query_content: Optional[str]              # 员工疑问内容
    query_attachment_url: Optional[str] = None
    reply_content: Optional[str]              # HR 回复内容
    reply_attachment_url: Optional[str] = None
    reply_deadline: Optional[datetime]        # 回复截止日期
    created_at: datetime


class PaySlipSendRequest(BaseModel):
    """
    用途: POST /api/v1/payroll/payslips/send 批量发送工资条的请求体
    业务: HR 审批薪资后，通过此接口将工资条推送给员工（系统内消息或邮件通知）
    字段说明:
      - year / month: 要发送的薪资月份
      - employee_ids: 指定员工（None=发送所有该月已审批状态的薪资记录对应的工资条）
    """
    year: int
    month: int = Field(..., ge=1, le=12)
    employee_ids: Optional[list[int]] = Field(
        default=None, description="指定员工, 为空则发送所有已审批的"
    )


class PaySlipQuerySubmit(BaseModel):
    """
    用途: POST /api/v1/payroll/payslips/{id}/query 员工提交工资疑问的请求体
    业务: 员工查看工资条后发现疑问，通过此接口提交给 HR 处理
    query_content: 疑问内容（至少1字，最大2000字）
    """
    query_content: str = Field(..., min_length=1, max_length=2000)
    query_attachment_url: Optional[str] = Field(default=None, max_length=500)


class PaySlipReply(BaseModel):
    """
    用途: POST /api/v1/payroll/payslips/{id}/reply HR回复工资疑问的请求体
    业务: HR 对员工提交的工资疑问进行回复，状态变为 replied
    reply_content: 回复内容（至少1字，最大2000字）
    """
    reply_content: str = Field(..., min_length=1, max_length=2000)
    reply_attachment_url: Optional[str] = Field(default=None, max_length=500)


# ============================================================
# Dashboard 看板
# ============================================================

class SalaryCostSummary(BaseModel):
    """
    用途: GET /api/v1/payroll/dashboard/cost-summary 薪资成本汇总看板数据
    业务: 汇总指定月份的薪资成本数据，支持按部门或地点维度分组
    字段说明:
      - total_gross: 应发工资合计
      - total_net: 实发工资合计
      - total_tax: 个税合计
      - total_social_insurance_company: 公司社保合计（人工成本组成部分）
      - total_housing_fund_company: 公司公积金合计（人工成本组成部分）
      - total_cost: 企业人工总成本 = gross_salary + company_social + company_housing_fund
        （注意不是 net_salary，因为税由员工承担，但公司需预扣代缴）
      - headcount: 该分组的员工人数
    """
    year: int
    month: int
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    location: Optional[str] = None
    total_gross: DecimalNum                         # 应发工资合计
    total_net: DecimalNum                           # 实发工资合计
    total_tax: DecimalNum                           # 个税合计
    total_social_insurance_company: DecimalNum      # 公司社保合计
    total_housing_fund_company: DecimalNum          # 公司公积金合计
    total_cost: DecimalNum  # gross + company social + company housing fund（企业人工总成本）
    headcount: int          # 员工人数


class SalaryCostTrend(BaseModel):
    """
    用途: GET /api/v1/payroll/dashboard/cost-trend 薪资成本趋势数据
    业务: 展示多个月份的薪资成本变化趋势，用于管理驾驶舱折线图
    data: 按月排列的成本汇总数据列表
    """
    data: list[SalaryCostSummary]


# ============================================================
# BonusRecord 奖金记录 Schemas
# ============================================================

# 奖金类型枚举（用于前端下拉选项和后端校验）
BONUS_TYPES = ["annual", "project", "performance", "other"]
BONUS_TYPE_LABELS = {
    "annual": "年终奖",
    "project": "项目奖金",
    "performance": "绩效奖金",
    "other": "其他",
}


class BonusRecordCreate(BaseModel):
    """
    用途: POST /api/v1/payroll/bonus-records 新建奖金记录的请求体
    业务: 为员工录入奖金发放记录，系统会自动计算奖金个税和实发净额
    字段说明:
      - bonus_type: 奖金类型（annual=年终奖/project=项目奖/performance=绩效奖/other=其他）
      - year: 奖金归属年份（税务申报用）
      - month: 发放月份（None=仅指定年份，不指定月份）
      - amount: 奖金总额（税前，大于0）
    奖金个税计算由 Service 层调用 BonusTaxRequest 逻辑完成
    """
    employee_id: int
    bonus_type: str = Field(default="annual", description="annual/project/performance/other")
    year: int = Field(..., ge=2019, le=2100)
    month: Optional[int] = Field(default=None, ge=1, le=12, description="发放月份")
    amount: Decimal = Field(..., gt=0, description="奖金金额")   # 税前奖金金额
    tax_method: Optional[str] = Field(
        default=None,
        pattern=r"^(cumulative|separate_bonus)$",
        description="计税方式: cumulative=并入综合所得, separate_bonus=单独计税",
    )
    notes: Optional[str] = Field(default=None, max_length=500)


class BonusRecordOut(BaseModel):
    """
    用途: 奖金记录查询接口的响应体，从 ORM BonusRecord 模型序列化
    字段说明:
      - amount: 奖金总额（税前）
      - tax_amount: 奖金个税（由计算引擎填充）
      - net_amount: 实发净额 = amount - tax_amount
      - status: 奖金状态（draft=草稿/approved=已审批/paid=已发放）
    由 API 层注入的字段: employee_name / employee_no / department_name
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    company_id: Optional[int] = None
    bonus_type: str
    year: int
    month: Optional[int] = None
    amount: DecimalNum          # 奖金总额（税前）
    tax_amount: DecimalNum      # 奖金个税
    net_amount: DecimalNum      # 奖金实发净额 = amount - tax_amount
    status: str                 # draft/approved/paid
    tax_method: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # Injected by API layer（非 ORM 字段，由 Service 层关联查询注入）
    employee_name: Optional[str] = None
    employee_no: Optional[str] = None
    department_name: Optional[str] = None


class BulkBonusItem(BaseModel):
    """
    用途: 批量奖金创建中单个员工的奖金条目
    业务: 作为 BulkBonusCreate.bonus_list 的元素使用
    """
    employee_id: int
    amount: Decimal = Field(..., gt=0)   # 该员工的奖金金额（税前，大于0）


class BulkBonusCreate(BaseModel):
    """
    用途: POST /api/v1/payroll/bonus-records/bulk 批量创建奖金记录的请求体
    业务: 年终奖季节 HR 可一次性为多名员工录入不同金额的奖金
    字段说明:
      - year / month: 奖金归属年月
      - bonus_type: 本批次所有奖金的统一类型（如"annual"）
      - bonus_list: 各员工奖金条目列表（至少1条）
    """
    year: int = Field(..., ge=2019, le=2100)
    month: Optional[int] = Field(default=None, ge=1, le=12)
    bonus_type: str = Field(default="annual", description="annual/project/performance/other")
    bonus_list: list[BulkBonusItem] = Field(..., min_length=1)  # 至少1条员工奖金


# ============================================================
# Cost Analysis Schemas
# ============================================================

class DepartmentCostItem(BaseModel):
    """
    用途: GET /api/v1/payroll/analysis/cost 按部门维度的薪资成本分析数据
    业务: 汇总各部门的薪资成本，供财务和管理层进行人力成本分析
    字段说明:
      - total_gross: 部门薪资总额（应发）
      - avg_gross: 部门人均应发工资
      - max_gross / min_gross: 部门内最高/最低应发工资
      - ratio: 该部门薪资占公司总薪资的比例（0~1，如0.15=15%）
    """
    department_id: Optional[int] = None
    department_name: str
    headcount: int              # 部门员工人数
    total_gross: DecimalNum     # 部门薪资总额（应发）
    avg_gross: DecimalNum       # 部门人均应发
    max_gross: DecimalNum       # 部门最高应发（单人）
    min_gross: DecimalNum       # 部门最低应发（单人）
    ratio: float = 0.0          # 占公司总薪资比例（0~1）


class MonthCostItem(BaseModel):
    """
    用途: GET /api/v1/payroll/analysis/trend 按月份维度的薪资成本汇总
    业务: 展示月度薪资趋势，用于同比/环比分析
    字段说明:
      - total_gross: 该月应发工资合计
      - total_net: 该月实发工资合计
      - paid_count: 已发放工资的员工数
      - unpaid_count: 未发放工资的员工数（状态为 draft/pending/rejected）
    """
    year: int
    month: int
    total_gross: DecimalNum    # 应发工资合计
    total_net: DecimalNum      # 实发工资合计
    paid_count: int            # 已发放员工数
    unpaid_count: int          # 未发放员工数


class CostAnalysisResult(BaseModel):
    """
    用途: GET /api/v1/payroll/analysis 薪资成本分析接口的综合响应体
    业务: 根据 group_by 参数返回不同维度的分析数据
    字段说明:
      - year: 分析年份
      - group_by: 分组维度（"department"=按部门 / "month"=按月份 / 其他扩展）
      - department_data: 按部门分组时的数据（group_by="department" 时有值）
      - month_data: 按月份分组时的数据（group_by="month" 时有值）
    两个数据字段互斥，只有与 group_by 对应的字段有值，另一个为 None
    """
    year: int
    group_by: str                                           # department/month
    department_data: Optional[list[DepartmentCostItem]] = None  # 按部门分组数据
    month_data: Optional[list[MonthCostItem]] = None        # 按月份分组数据
