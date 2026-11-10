"""
薪酬管理数据模型 — 售后管理系统
============================================================

模块职责
--------
定义薪酬域的全部 ORM 模型，覆盖以下业务实体（共 10 张表）：

    1. Company               — 公司主体（多公司架构）
    2. TaxLawVersion         — 税法版本（历史版本管理）
    3. TaxBracket            — 税率区间（从属于税法版本）
    4. EmployeeTaxDeduction  — 员工专项附加扣除
    5. SalaryStructure       — 薪酬结构配置（员工薪资组成模板）
    6. SocialInsuranceConfig — 社保/公积金比率配置（按城市/年度）
    7. SalaryRecord          — 月度薪资记录（核心计算结果）
    8. TaxRecord             — 月度个税记录（累计预扣法）
    9. PaySlip               — 工资条（发放与员工反馈）
   10. SocialInsuranceRecord — 月度社保明细记录
   11. BonusRecord           — 奖金记录（年终奖/项目奖/绩效奖）

────────────────────────────────────────────────────────────
完整薪资计算流程
────────────────────────────────────────────────────────────

步骤 1：读取员工当月有效的 SalaryStructure（薪酬结构快照）
步骤 2：从考勤数据获取加班时长（工作日/周末/法定节假日）
步骤 3：计算应发工资（gross_salary）

    gross = 基础工资
           + 岗位工资
           + 绩效工资基数 × 绩效系数
           + 补贴合计（夜班/高温/项目/餐补/交通/住房/其他）
           + 加班费（工作日×1.5 + 周末×2 + 法定节假日×3）
           + 调整金额（adjustment_amount，正=补发，负=扣款）
           × 入离职折算比（proration_days / 当月总天数，仅当月入/离职时）

步骤 4：按 SocialInsuranceConfig 计算社保/公积金
        实际缴费基数 = clamp(应发工资, base_min, base_max)
        个人社保 = 养老(8%) + 医疗(2%) + 失业(0.5%) + ...（各险种基数×个人比例）
        公司社保 = 养老(16%) + 医疗(8%) + 失业(0.5%) + 工伤 + 生育 + ...
        个人公积金 = 公积金基数 × 个人比例（一般 5%~12%，各城市略有差异）

步骤 5：计算个税（累计预扣法，见 TaxLawVersion/TaxBracket 说明）
        月应纳税所得额 = gross - 个人社保 - 个人公积金 - 专项附加扣除 - 5000免征额
        累计应纳税所得额 = 当年1月至本月累计值
        本月预扣税额 = 累计应纳税所得额 × 适用税率 - 速算扣除数 - 前月已缴税额合计

步骤 6：计算实发工资（net_salary）

    net = gross - 个人社保 - 个人公积金 - 个税

步骤 7：生成 SalaryRecord（月度快照）、TaxRecord（个税台账）、PaySlip（工资条）

────────────────────────────────────────────────────────────
多公司架构（Multi-Company）
────────────────────────────────────────────────────────────
- Company 表存储集团下各法人主体（如本公司北京/本公司东莞）
- Employee.company_id 外键挂载员工所属法人
- SalaryRecord.company_id 冗余快照当月所属法人，支持跨公司汇总报表
- SocialInsuranceConfig 按 (company_id, city, year) 区分不同主体、不同城市的缴纳政策
  company_id = NULL 表示适用所有公司的通用配置（回退匹配）

────────────────────────────────────────────────────────────
税法版本管理（Tax Law Versioning）
────────────────────────────────────────────────────────────
- TaxLawVersion 记录历次税法改革节点（如 2019 年新个税法）
- 每个版本绑定一组 TaxBracket（税率区间表）
- SalaryRecord/TaxRecord 均快照 tax_law_version_id，
  保证历史月份重算时使用同一版本的税率，满足审计追溯要求
- end_date = NULL 表示该版本为当前有效版本

────────────────────────────────────────────────────────────
专项附加扣除（Special Additional Deductions）
────────────────────────────────────────────────────────────
《个人所得税专项附加扣除暂行办法》（2019年起）规定的七类扣除项：
  1. 子女教育       — 每子每月 1000 元（共同扣除时各 500 元）
  2. 继续教育       — 学历教育每月 400 元 / 职业资格证书年度 3600 元
  3. 大病医疗       — 年度自负超 1.5 万元部分，上限 8 万元
  4. 住房贷款利息   — 首套贷款每月 1000 元
  5. 住房租金       — 按城市等级每月 800/1100/1500 元
  6. 赡养老人       — 独生子女每月 3000 元；非独生子女共担每月最高 1500 元
  7. 婴幼儿照护     — 3岁以下每子每月 2000 元（2022年起）
存储于 EmployeeTaxDeduction，按员工、生效年月区间维护，
服务层在计算每月个税时累加当月有效的各项扣除额。

────────────────────────────────────────────────────────────
SQLAlchemy 使用约定
────────────────────────────────────────────────────────────
- 所有 DateTime 列使用 DateTime(timezone=True)（asyncpg 要求 TIMESTAMPTZ）
- 金额列统一使用 Numeric(精度, 2)，业务层用 Python Decimal 操作
- lazy="noload" 用于非必要关联，避免 N+1 查询；
  lazy="selectin" 用于高频一起加载的关联（如 Employee、TaxLawVersion）
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# ============================================================
# Company 公司主体
# ============================================================

class Company(Base):
    """
    公司主体 — 支持集团多公司（Multi-Company）架构

    业务说明
    --------
    本公司集团可能在多个城市注册独立法人主体（如北京公司、东莞公司）。
    本表存储每个法人主体的基本信息，作为员工归属、社保缴纳地、
    纳税主体的基准依据。

    关键规则
    --------
    - tax_id（统一社会信用代码）全局唯一，用于对外开具劳动合同/社保证明
    - city/province 影响社保政策匹配（SocialInsuranceConfig 按城市区分）
    - is_active = False 时，该主体停止新增员工关联，历史记录仍保留
    - 与 Employee 的关联通过 Employee.company_id 外键建立（Employee 模型定义）

    关联关系
    --------
    - employees            : 该公司下的所有员工（lazy=noload，避免大量加载）
    - social_insurance_configs : 该公司专属的社保配置（按年度/城市）
    """

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, comment="公司全称")
    short_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="公司简称")
    company_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, comment="公司类型")
    legal_entity_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="法人主体名称")
    tax_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, unique=True, comment="统一社会信用代码")
    parent_company_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True, comment="上级公司ID"
    )
    company_owner: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="公司负责人")
    city: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="注册城市")
    province: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="注册省份")
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="注册地址")
    legal_rep: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="法定代表人")
    contact_phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, comment="联系电话")
    contact_email: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="联系邮箱")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    employees: Mapped[list["Employee"]] = relationship(  # noqa: F821
        "Employee", back_populates="company_entity", lazy="noload"
    )
    social_insurance_configs: Mapped[list["SocialInsuranceConfig"]] = relationship(
        "SocialInsuranceConfig", back_populates="company_entity", lazy="noload"
    )
    parent_company: Mapped[Optional["Company"]] = relationship(
        "Company", remote_side=[id], back_populates="child_companies", lazy="selectin"
    )
    child_companies: Mapped[list["Company"]] = relationship(
        "Company", back_populates="parent_company", lazy="noload"
    )


# ============================================================
# TaxLawVersion 税法版本
# ============================================================

class TaxLawVersion(Base):
    """
    税法版本 — 历史税率版本管理，支持审计追溯

    业务说明
    --------
    中国个人所得税法经历多次重大改革（如 2019 年综合征收制改革），
    每次改革带来免征额、税率表、扣除项的变化。
    本表记录每个历史版本的元数据，关联的 TaxBracket 存储该版本对应的
    分级税率表（超额累进税率）。

    累计预扣法简介（2019 年新个税法核心算法）
    ------------------------------------------
    传统按月代扣制（每月独立计算）会造成"年底多退少补"问题。
    2019 年改革引入「累计预扣法」：

        1. 每月累加当年1月至本月的累计收入、累计扣除额
        2. 对累计应纳税所得额查超额累进税率表，得出「累计应缴税额」
        3. 本月代扣 = 累计应缴税额 - 前月已代扣合计
        4. 年末汇算清缴：综合年收入重新计算，与全年已代扣对齐（多退少补）

    这样每月税负更平滑，避免前几个月税少、年底税多的问题。

    版本切换策略
    ------------
    - end_date = NULL 表示当前生效版本，系统每月计算时自动匹配
    - 旧版本 end_date 填写到废止日期，新版本 effective_date 接续
    - SalaryRecord/TaxRecord 均快照 tax_law_version_id，
      历史月份重算时可精确还原当时的税率

    关联关系
    --------
    - brackets : 按 sort_order 排序的税率区间列表（selectin 加载，常与版本一起使用）
    """

    __tablename__ = "tax_law_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="版本名称: 如2019个税改革版")
    country_code: Mapped[str] = mapped_column(String(10), nullable=False, default="CN", comment="国家代码")
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, comment="生效日期")
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="结束日期(NULL=当前有效)")
    monthly_tax_free: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("5000"), comment="月基本减除费用"
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="版本说明")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    brackets: Mapped[list["TaxBracket"]] = relationship(
        "TaxBracket", back_populates="version", lazy="selectin", order_by="TaxBracket.sort_order"
    )


class TaxBracket(Base):
    """
    税率区间 — 超额累进税率表的单条区间记录

    业务说明
    --------
    本表存储某一税法版本下的分级税率表，每条记录代表一个税率档位。
    2019 年综合所得税率表（累计预扣用）共 7 档：

        累计应纳税所得额(元)      税率   速算扣除数
        ≤ 36,000                  3%        0
        ≤ 144,000                10%     2,520
        ≤ 300,000                20%    16,920
        ≤ 420,000                25%    31,920
        ≤ 660,000                30%    52,920
        ≤ 960,000                35%    85,920
        > 960,000                45%   181,920

    速算公式（任意档位）：
        应缴税 = 累计应纳税所得额 × 税率 - 速算扣除数

    bracket_type 区分两种税率表：
    - cumulative    : 累计预扣综合所得税率表（正常工资薪金，主流）
    - separate_bonus: 全年一次性奖金单独计税（年终奖可选，2027 年后取消优惠）

    upper_bound 字段
    ----------------
    上限金额（含），最后一档用 999_999_999.99 表示正无穷。
    服务层按 sort_order 从小到大遍历，找到第一个 upper_bound ≥ 累计应纳税所得额的档位。

    关联关系
    --------
    - version : 所属的税法版本（lazy=noload，仅从 TaxLawVersion.brackets 侧加载）
    """

    __tablename__ = "tax_brackets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tax_law_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bracket_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="类型: cumulative=累计预扣综合所得, bonus=全年一次性奖金"
    )
    upper_bound: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False, comment="应纳税所得额上限(含)"
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, comment="税率")
    quick_deduction: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0"), comment="速算扣除数"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="排序")

    # Relationships
    version: Mapped["TaxLawVersion"] = relationship(
        "TaxLawVersion", back_populates="brackets", lazy="noload"
    )


# ============================================================
# EmployeeTaxDeduction 员工专项附加扣除
# ============================================================

class EmployeeTaxDeduction(Base):
    """
    员工专项附加扣除 — 个税前置扣减项（2019 年新个税法引入）

    业务说明
    --------
    专项附加扣除在计算「应纳税所得额」时从税前收入中减除，直接降低税基。
    员工需向雇主（HR）申报扣除信息，雇主代扣代缴时予以扣减。

    deduction_type 取值
    -------------------
    - 子女教育     : 每子女每月 1000 元（多孩累加，夫妻共同申报时各 500）
    - 继续教育     : 学历/学位教育期间每月 400 元；职业技能证书当年 3600 元（一次性）
    - 大病医疗     : 年度内自负医疗费超 1.5 万元部分，据实扣除，上限 8 万元
    - 住房贷款利息 : 首套住房商业贷款/公积金贷款每月 1000 元（限一套）
    - 住房租金     : 按主要工作城市划档，每月 800/1100/1500 元（未享受贷款利息扣除）
    - 赡养老人     : 被赡养人须年满 60 周岁；独生子女每月 3000，非独生子女每月最高 1500
    - 婴幼儿照护   : 3 岁以下婴幼儿，每子每月 2000 元（2022 年 1 月起）
    - 其他         : 预留扩展

    生效区间管理
    ------------
    start_year/start_month 到 end_year/end_month（NULL 表示持续有效）定义扣除生效窗口。
    服务层每月计算个税时，查询满足区间条件且 is_active=True 的记录，
    累加 monthly_amount 作为当月专项附加扣除合计（special_deduction_total）。

    关联关系
    --------
    - employee : 所属员工（lazy=noload，通过 employee_id 查询即可）
    """

    __tablename__ = "employee_tax_deductions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    deduction_type: Mapped[str] = mapped_column(
        String(30), nullable=False,
        comment="扣除类型: 子女教育/继续教育/大病医疗/住房贷款利息/住房租金/赡养老人/婴幼儿照护/其他"
    )
    monthly_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, comment="每月扣除金额"
    )
    start_year: Mapped[int] = mapped_column(Integer, nullable=False, comment="开始年份")
    start_month: Mapped[int] = mapped_column(Integer, nullable=False, comment="开始月份")
    end_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="结束年份(NULL=持续有效)")
    end_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="结束月份")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="备注")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    employee: Mapped["Employee"] = relationship("Employee", lazy="noload")  # noqa: F821


class SalaryStructure(Base):
    """
    薪酬结构 — 员工薪资组成的配置模板

    业务说明
    --------
    每个员工在任意时点最多有一个「生效中」的薪酬结构。
    当薪资调整时，不修改原记录，而是新建一条新 effective_date 的记录（历史留存）。
    服务层在计算某月薪资时，取 effective_date ≤ 该月最后一天且最新的那条记录。

    字段分组说明
    ------------
    基本薪资（三项相加，再乘绩效系数得基础薪酬部分）：
      - base_salary         : 基础工资，固定发放，与岗位等级挂钩
      - position_salary     : 岗位工资，体现岗位价值差异
      - performance_salary  : 绩效工资基数，需乘以绩效系数（performance_coefficient）
      - performance_coefficient : 绩效评分对应的系数，如 A=1.2、B=1.0、C=0.8

    补贴项（七类，按月或按实际出勤发放）：
      - night_shift_allowance : 夜班津贴（连续夜班岗位固定发放）
      - high_temp_allowance   : 高温津贴（6~9 月生产车间强制发放，依国家标准）
      - project_allowance     : 项目补贴（驻场/出差项目人员）
      - meal_allowance        : 餐补（不计入社保基数）
      - transport_allowance   : 交通补贴（不计入社保基数）
      - housing_allowance     : 住房补贴（外地/异地员工）
      - other_allowance       : 其他补贴
      - allowance_calc_method : 补贴计算方式
            actual_attendance = 按实际出勤天数折算（适用于非固定岗）
            fixed_monthly     = 固定月发（无论出勤情况，按月全额发放）

    加班基数：
      - overtime_base : 计算加班费的基准工资
            加班日薪 = overtime_base / 当月应出勤天数
            工作日加班费 = 日薪 × 1.5 × 加班小时 / 8
            周末加班费   = 日薪 × 2.0 × 加班小时 / 8
            法定节假日   = 日薪 × 3.0 × 加班小时 / 8

    生效日期：
      - effective_date : 该薪酬结构的生效日，用于历史版本查找

    关联关系
    --------
    - employee : 所属员工（selectin，薪酬结构通常与员工一起查询）
    """

    __tablename__ = "salary_structures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # --- 基本薪资 ---
    base_salary: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, comment="基础工资"
    )
    position_salary: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, comment="岗位工资"
    )
    performance_salary: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, comment="绩效工资基数"
    )
    performance_coefficient: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=Decimal("1.00"), comment="绩效系数"
    )

    # --- 补贴项 ---
    night_shift_allowance: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="夜班津贴"
    )
    high_temp_allowance: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="高温津贴"
    )
    project_allowance: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="项目补贴"
    )
    meal_allowance: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="餐补"
    )
    transport_allowance: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="交通补贴"
    )
    housing_allowance: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="住房补贴"
    )
    other_allowance: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="其他补贴"
    )
    allowance_calc_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="fixed_monthly",
        comment="补贴计算方式: actual_attendance=按实际出勤, fixed_monthly=固定月发",
    )

    # --- 加班基数 ---
    overtime_base: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, comment="加班工资计算基数"
    )

    # --- 结构调整项 ---
    adjustment_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="结构调整项(正=补发,负=扣款)"
    )

    # --- 生效日期 ---
    effective_date: Mapped[date] = mapped_column(
        Date, nullable=False, comment="生效日期"
    )

    # --- 时间戳 ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", back_populates="salary_structures", lazy="selectin"
    )


class SocialInsuranceConfig(Base):
    """
    社保/公积金配置 — 按城市、年度、公司维护缴纳比率与基数上下限

    业务说明
    --------
    中国各城市社保政策每年调整一次（通常 7 月生效），不同城市险种比率、
    缴费基数上下限均有差异（如北京、东莞差别较大）。
    本表为服务层提供查询基准：给定 (company_id, city, year)，
    拉取当年各险种的个人/公司比率和基数上下限。

    匹配优先级
    ----------
    服务层查询时：
      1. 优先匹配 company_id = 该员工所属公司 AND city = 员工工作城市 AND year = 当前年
      2. 如未找到，回退到 company_id IS NULL（通用配置）AND city AND year

    insurance_type 取值
    -------------------
    - 养老   : 个人 8%，公司 16%（北京，2023年）
    - 医疗   : 个人 2%，公司 8%（各地有差异）
    - 失业   : 个人 0.5%，公司 0.5%
    - 工伤   : 个人 0%，公司 0.2%~1.9%（行业风险等级决定）
    - 生育   : 个人 0%，公司 0.8%（部分城市已并入医疗）
    - 公积金 : 个人与公司各 5%~12%（企业自主选择比率，需在区间内）

    基数处理
    --------
    实际缴费基数 = clamp(员工应发工资, base_min, base_max)
    当月工资低于下限 → 按下限缴纳；高于上限 → 按上限缴纳

    关联关系
    --------
    - company_entity : 关联的公司主体（selectin，查询时通常一并加载）
    """

    __tablename__ = "social_insurance_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True, index=True,
        comment="公司ID(NULL=适用所有公司)"
    )
    city: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True, comment="城市: 北京/东莞"
    )
    insurance_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="险种: 养老/医疗/失业/工伤/生育/公积金",
    )
    employee_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, comment="个人缴纳比例"
    )
    company_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, comment="公司缴纳比例"
    )
    base_min: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, comment="缴纳基数下限"
    )
    base_max: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, comment="缴纳基数上限"
    )
    year: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="适用年份"
    )
    effective_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="生效日期"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否启用"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    company_entity: Mapped[Optional["Company"]] = relationship(
        "Company", back_populates="social_insurance_configs", lazy="selectin"
    )


class SalaryRecord(Base):
    """
    月度薪资记录 — 每月每人一条，记录完整的薪资计算结果快照

    业务说明
    --------
    SalaryRecord 是薪酬域的核心表，每次月度薪资计算（或重算）后写入一条记录。
    同一员工同一月份允许存在多个版本（version 字段递增），最新版本为当前有效值。
    所有金额字段均为当月最终计算结果的「快照」，即使薪酬结构事后调整，
    历史记录也保持不变，满足审计要求。

    字段分组详解
    ============

    【基础薪资 — 来自 SalaryStructure 快照】
    ─────────────────────────────────────────
    base_salary            : 当月基础工资（从 SalaryStructure 快照，非实时读取）
    position_salary        : 当月岗位工资
    performance_salary     : 绩效工资基数（需乘以 performance_coefficient 得实际绩效工资）
    performance_coefficient: 当月绩效系数（来自绩效评估结果，如 1.0/1.2/0.8）

    【补贴 — 汇总值，明细在 SocialInsuranceRecord】
    ─────────────────────────────────────────────────
    total_allowance        : 七类补贴合计（夜班+高温+项目+餐补+交通+住房+其他）
                             按 allowance_calc_method 计算后取值

    【加班费 — 分类记录三种加班倍率】
    ──────────────────────────────────
    overtime_pay_weekday   : 工作日延时加班费（1.5 倍，劳动法第 44 条第1款）
    overtime_pay_weekend   : 周末加班费（2 倍，劳动法第 44 条第2款）
    overtime_pay_holiday   : 法定节假日加班费（3 倍，劳动法第 44 条第3款）
    -- 加班费计算公式: 日薪 × 倍率 × 加班小时 / 8
    -- 日薪 = overtime_base / 当月应出勤天数

    【汇总 — 应发工资】
    ────────────────────
    gross_salary           : 应发工资合计
        公式: base_salary
             + position_salary
             + performance_salary × performance_coefficient
             + total_allowance
             + overtime_pay_weekday + overtime_pay_weekend + overtime_pay_holiday
             + adjustment_amount
             × 入离职折算比（如当月入职/离职则按 proration_days 折算）

    【社保公积金 — 个人与公司双向快照】
    ──────────────────────────────────
    social_insurance_employee : 社保个人缴纳合计（养老+医疗+失业，按各险种基数×个人比率）
    housing_fund_employee     : 公积金个人缴纳（公积金基数 × 个人比率）
    social_insurance_company  : 社保公司缴纳合计（养老+医疗+失业+工伤+生育）
    housing_fund_company      : 公积金公司缴纳
    -- 以上均来自 SocialInsuranceRecord，冗余到此便于汇总查询

    【个税 — 累计预扣法结果】
    ─────────────────────────
    taxable_income         : 本月应纳税所得额（月维度，非累计值）
                             = gross - social_insurance_employee
                               - housing_fund_employee - special_deduction_total - 5000
    tax_amount             : 本月实际代扣个税（= TaxRecord.current_month_tax）
    special_deduction_total: 当月专项附加扣除合计（从 EmployeeTaxDeduction 汇总）

    【实发工资】
    ────────────
    net_salary             : 实发工资（到账金额）
        公式: gross_salary
             - social_insurance_employee
             - housing_fund_employee
             - tax_amount

    【调整项 — HR 手动补扣款】
    ───────────────────────────
    adjustment_amount      : 调整金额（正数=补发，负数=扣款）
                             常见场景：上月少发补偿、罚款、报销并入工资
    adjustment_reason      : 调整原因（HR 填写，保留审计说明）

    【入离职折算 — 当月入职或离职时按天折算】
    ──────────────────────────────────────────
    proration_type         : 折算基准
                             calendar_day = 按自然日（日历天），适用于固定月薪
                             workday      = 按应出勤工作日，适用于计件/日薪制
    proration_days         : 实际工作天数（入职当月 = 入职到月底；离职当月 = 月初到离职）
                             NULL 表示当月无需折算（完整工作月）
    -- 折算系数 = proration_days / 当月总天数（calendar_day）
              或 proration_days / 当月应出勤天数（workday）
    -- 应发工资中的固定薪资部分均乘以该系数；加班费等按实际小时不折算

    【状态与审批】
    ───────────────
    status                 : 薪资记录生命周期
                             draft            = 草稿（计算完成，待HR核查）
                             pending_approval = 已提交审批（等待财务/领导确认）
                             approved         = 已审批（可生成工资条）
                             paid             = 已发放（银行划账完成）

    【异常标记】
    ────────────
    anomaly_flags_json     : JSON 字符串，记录本月薪资计算的异常标记，供HR核查
                             示例: {"salary_change_pct": 25.3, "is_new_hire": true}
                             常见标记：薪资环比变动超阈值（如±20%）、新入职、离职、补发等

    【版本与追溯】
    ───────────────
    version                : 同一员工同月份的版本号，每次重算递增，最大值为当前有效版本
    snapshot_id            : 预留字段，未来可关联到独立的历史快照表（当前业务暂未使用）
    company_id             : 快照当月所属公司（员工转公司后历史记录仍保持原公司 ID）
    tax_law_version_id     : 快照计算时使用的税法版本，支持历史月份按原税率重算

    关联关系
    --------
    - employee       : 所属员工（selectin，薪资记录查询几乎总需要员工信息）
    - pay_slip       : 一对一关联的工资条（uselist=False，审批通过后生成）
    - tax_law_version: 使用的税法版本（selectin，用于重算校验）
    """

    __tablename__ = "salary_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="版本号,每次修订+1"
    )

    # --- 薪资明细(快照) ---
    base_salary: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, comment="基础工资"
    )
    position_salary: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, comment="岗位工资"
    )
    performance_salary: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, comment="绩效工资基数"
    )
    performance_coefficient: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=Decimal("1.00"), comment="绩效系数"
    )

    # --- 补贴 ---
    total_allowance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, comment="补贴合计"
    )

    # --- 加班费 ---
    overtime_pay_weekday: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="工作日加班费(1.5倍)"
    )
    overtime_pay_weekend: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="周末加班费(2倍)"
    )
    overtime_pay_holiday: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="法定假日加班费(3倍)"
    )

    # --- 汇总 ---
    gross_salary: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, comment="应发工资"
    )

    # --- 社保公积金 ---
    social_insurance_employee: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="社保个人部分"
    )
    housing_fund_employee: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="公积金个人部分"
    )
    social_insurance_company: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="社保公司部分"
    )
    housing_fund_company: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="公积金公司部分"
    )

    # --- 个税 ---
    taxable_income: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, comment="应纳税所得额"
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="个税"
    )
    special_deduction_total: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="专项附加扣除合计"
    )

    # --- 实发 ---
    net_salary: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, comment="实发工资"
    )

    # --- 调整 ---
    adjustment_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="调整金额(正=补发, 负=扣款)"
    )
    adjustment_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="调整原因"
    )

    # --- 入离职折算 ---
    proration_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="折算方式: calendar_day/workday"
    )
    proration_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="折算天数"
    )

    # --- 状态与审批 ---
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
        index=True,
        comment="状态: draft/pending_approval/approved/paid",
    )

    # --- 异常标记 ---
    anomaly_flags_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="异常标记JSON(薪资变动>X%, 新入职, 离职等)"
    )

    # --- 快照引用 ---
    snapshot_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="历史快照ID(版本管理)"
    )

    # --- 多公司/税法版本追溯 ---
    company_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True, index=True, comment="所属公司ID(快照)"
    )
    tax_law_version_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tax_law_versions.id", ondelete="SET NULL"),
        nullable=True, comment="计算时使用的税法版本ID(用于历史追溯)"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    employee: Mapped["Employee"] = relationship("Employee", lazy="selectin")  # noqa: F821
    pay_slip: Mapped["PaySlip | None"] = relationship(
        "PaySlip", back_populates="salary_record", uselist=False, lazy="selectin"
    )
    tax_law_version: Mapped[Optional["TaxLawVersion"]] = relationship(
        "TaxLawVersion", lazy="selectin", foreign_keys=[tax_law_version_id]
    )


class TaxRecord(Base):
    """
    月度个税记录 — 累计预扣法的逐月台账

    业务说明
    --------
    与 SalaryRecord 一一对应（同一员工同月），专门存储个税相关的累计数据。
    独立成表的原因：累计预扣法需要「年初至今」的累计值，
    将其从薪资主表分离，便于年末汇算清缴、税务申报和审计核查。

    累计预扣法字段说明
    ------------------
    以 N 月为例（N = 1..12）：

    cumulative_income        : 1 月至 N 月的税前收入累计值（含本月 gross）
    cumulative_tax_free      : 1 月至 N 月的基本减除费用累计值（= 5000 × N）
    cumulative_deductions    : 1 月至 N 月的社保+公积金个人部分累计值
    cumulative_special_deductions : 1 月至 N 月的专项附加扣除累计值
    cumulative_taxable       : 累计应纳税所得额
                               = cumulative_income
                                 - cumulative_tax_free
                                 - cumulative_deductions
                                 - cumulative_special_deductions
    cumulative_tax           : 对 cumulative_taxable 查税率表得出的累计应缴税额
                               = cumulative_taxable × 适用税率 - 速算扣除数
    current_month_tax        : 本月实际代扣税额
                               = cumulative_tax - 前(N-1)月 cumulative_tax 之和
                               （即差额预扣，当 current_month_tax < 0 时应退税或下月抵扣）

    tax_method 计税方式
    -------------------
    - cumulative      : 累计预扣法（综合所得，工资薪金标准路径）
    - separate_bonus  : 全年一次性奖金单独计税（年终奖按月均值查税率，优惠截至2027年底）
                        注意：年终奖单独计税时，此记录对应 BonusRecord，不与 SalaryRecord 关联

    tax_law_version_id
    ------------------
    快照当月使用的税法版本，与 SalaryRecord.tax_law_version_id 保持一致，
    确保月度薪税数据完全闭合，可独立进行历史重算。

    关联关系
    --------
    - employee        : 所属员工（selectin）
    - tax_law_version : 使用的税法版本（selectin，重算时验证一致性）
    """

    __tablename__ = "tax_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- 累计数据 ---
    cumulative_income: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, comment="累计收入"
    )
    cumulative_tax_free: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, comment="累计免税额(5000×月数)"
    )
    cumulative_deductions: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, comment="累计扣除(社保+公积金)"
    )
    cumulative_special_deductions: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, comment="累计专项附加扣除"
    )
    cumulative_taxable: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, comment="累计应纳税所得额"
    )
    cumulative_tax: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, comment="累计应缴税额"
    )
    current_month_tax: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, comment="本月应缴税额"
    )
    tax_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="cumulative",
        comment="计税方式: cumulative=累计预扣法, separate_bonus=全年一次性奖金单独计税",
    )
    tax_law_version_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tax_law_versions.id", ondelete="SET NULL"),
        nullable=True, comment="计算时使用的税法版本ID(历史追溯)"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    employee: Mapped["Employee"] = relationship("Employee", lazy="selectin")  # noqa: F821
    tax_law_version: Mapped[Optional["TaxLawVersion"]] = relationship(
        "TaxLawVersion", lazy="selectin", foreign_keys=[tax_law_version_id]
    )


class TaxImportBatch(Base):
    """
    税款表上传批次。

    用于按公司主体 + 年月归档线下税款表上传结果。
    当前规则：
    - 同一公司同一月份只保留一份有效批次
    - 再次上传视为覆盖当期内容，不提供编辑/删除接口
    """

    __tablename__ = "tax_import_batches"
    __table_args__ = (
        UniqueConstraint("company_id", "year", "month", name="uq_tax_import_batches_company_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True, comment="公司主体ID"
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="所属年度")
    month: Mapped[int] = mapped_column(Integer, nullable=False, comment="所属月份")
    sheet_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="工作表名称")
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False, comment="原始文件名")
    headers_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, comment="表头列表")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="数据行数")
    uploaded_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, comment="上传人"
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="上传时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    company: Mapped["Company"] = relationship("Company", lazy="selectin")
    uploader: Mapped[Optional["Employee"]] = relationship("Employee", lazy="selectin", foreign_keys=[uploaded_by])  # noqa: F821
    rows: Mapped[list["TaxImportRow"]] = relationship(
        "TaxImportRow",
        back_populates="batch",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="TaxImportRow.row_no",
    )


class TaxImportRow(Base):
    """税款表上传后的逐行明细，按原始表头存 JSON。"""

    __tablename__ = "tax_import_rows"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_no", name="uq_tax_import_rows_batch_row"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tax_import_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    row_no: Mapped[int] = mapped_column(Integer, nullable=False, comment="Excel 原始数据行序号（从 1 开始）")
    row_data: Mapped[dict] = mapped_column(JSON, nullable=False, comment="按原始表头映射的行数据")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    batch: Mapped["TaxImportBatch"] = relationship("TaxImportBatch", back_populates="rows", lazy="selectin")


class PaySlip(Base):
    """
    工资条 — 薪资结果的员工可见凭证及反馈闭环

    业务说明
    --------
    当 SalaryRecord 状态流转到 approved 后，系统自动生成工资条（PaySlip）。
    工资条与 SalaryRecord 为一对一关系（unique 约束保证），
    记录发送时间、员工查阅时间，以及员工对工资条提出疑问的完整交互记录。

    发放流程
    --------
    1. HR 批量生成工资条，系统记录 sent_at 时间戳
    2. 员工通过 ESS（员工自助服务）查看工资条，系统记录 viewed_at
    3. 员工可对工资条提出疑问（query_content），query_status 变为 queried
    4. HR 在 reply_deadline 前回复（reply_content），query_status 变为 replied
    5. 员工确认后 query_status = confirmed；或升级为 escalated 提交上级处理

    query_status 状态流转
    ----------------------
    none      : 初始状态（已发送，员工未提问）
    queried   : 员工已提问（待HR回复）
    replied   : HR 已回复（待员工确认）
    confirmed : 员工已确认（疑问解决，闭环）
    escalated : 升级处理（分歧未解决，提交给更高层级）

    reply_deadline
    --------------
    发送疑问后系统自动设置回复截止时间（如 3 个工作日），
    到期未回复触发提醒或自动升级，确保薪资疑问的 SLA 管理。

    关联关系
    --------
    - salary_record : 关联的薪资记录（selectin，工资条展示数据来源）
    - employee      : 所属员工（selectin，用于权限校验和邮件/消息推送）
    """

    __tablename__ = "pay_slips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    salary_record_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("salary_records.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="发送时间"
    )
    viewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="查看时间"
    )

    # --- 反馈流程 ---
    query_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="none",
        comment="反馈状态: none/queried/replied/confirmed/escalated",
    )
    query_content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="员工疑问内容"
    )
    query_attachment_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="员工疑问附件URL"
    )
    reply_content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="HR回复内容"
    )
    reply_attachment_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="HR回复附件URL"
    )
    reply_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="回复截止时间"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    salary_record: Mapped["SalaryRecord"] = relationship(
        "SalaryRecord", back_populates="pay_slip", lazy="selectin"
    )
    employee: Mapped["Employee"] = relationship("Employee", lazy="selectin")  # noqa: F821


class SocialInsuranceRecord(Base):
    """
    月度社保明细记录 — 按险种分别记录个人与公司缴纳金额

    业务说明
    --------
    本表为 SalaryRecord 中社保汇总字段（social_insurance_employee/company）
    提供支撑明细，每月每人一条（唯一约束保证）。
    同时作为独立的社保台账，支持：
      - 月度社保申报明细导出（按险种分列，符合社保局申报格式）
      - 社保基数调整后的历史对比
      - 工伤/生育等仅公司缴纳险种的独立核查

    缴费基数说明
    ------------
    social_insurance_base : 社保缴费基数（基于员工上年度月均工资，年中不变）
    housing_fund_base     : 公积金缴费基数（可与社保基数不同，按公积金管理中心规定）
    两者均已经过 clamp(actual_wage, base_min, base_max) 处理

    险种字段说明
    ------------
    养老保险：pension_employee（个人缴纳）/ pension_company（单位缴纳）
    医疗保险：medical_employee / medical_company
    失业保险：unemployment_employee / unemployment_company
    工伤保险：injury_company（仅单位缴纳，个人不缴）
    生育保险：maternity_company（仅单位缴纳，部分城市已并入医疗）
    住房公积金：housing_fund_employee / housing_fund_company

    合计字段
    --------
    total_employee : 个人缴纳合计 = 养老+医疗+失业+公积金（个人）
    total_company  : 单位缴纳合计 = 养老+医疗+失业+工伤+生育+公积金（单位）

    唯一约束
    --------
    uq_si_record_emp_year_month 确保同一员工同一月份只有一条社保记录（不允许多版本）

    关联关系
    --------
    - employee : 所属员工（selectin）
    """

    __tablename__ = "social_insurance_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, comment="年份")
    month: Mapped[int] = mapped_column(Integer, nullable=False, comment="月份")
    city: Mapped[str] = mapped_column(String(50), nullable=False, default="", comment="缴纳城市")

    # 社保基数
    social_insurance_base: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, comment="社保缴纳基数"
    )
    housing_fund_base: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, comment="公积金缴纳基数"
    )

    # 养老保险
    pension_employee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, comment="养老-个人")
    pension_company: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, comment="养老-公司")

    # 医疗保险
    medical_employee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, comment="医疗-个人")
    medical_company: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, comment="医疗-公司")

    # 失业保险
    unemployment_employee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, comment="失业-个人")
    unemployment_company: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, comment="失业-公司")

    # 工伤保险（仅公司）
    injury_company: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, comment="工伤-公司")

    # 生育保险（仅公司）
    maternity_company: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, comment="生育-公司")

    # 公积金
    housing_fund_employee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, comment="公积金-个人")
    housing_fund_company: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, comment="公积金-公司")

    # 合计
    total_employee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, comment="个人合计")
    total_company: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, comment="公司合计")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )

    # Relationships
    employee: Mapped["Employee"] = relationship("Employee", lazy="selectin")  # noqa: F821

    __table_args__ = (
        UniqueConstraint("employee_id", "year", "month", name="uq_si_record_emp_year_month"),
    )


# ============================================================
# BonusRecord 奖金记录（年终奖/项目奖金/绩效奖金/其他）
# ============================================================

class BonusRecord(Base):
    """
    奖金记录 — 年终奖、项目奖金等非月度薪资的一次性发放记录

    业务说明
    --------
    奖金不计入月度 SalaryRecord（因其发放时间与金额不固定），
    独立成表方便统计年度奖金总额、按奖金类型分析和个税处理。

    bonus_type 奖金类型
    -------------------
    - annual      : 年终奖（最常见，可选择全年一次性奖金单独计税优惠）
    - project     : 项目奖金（项目里程碑完成后一次性发放）
    - performance : 绩效奖金（季度/年度绩效评估结果对应的额外奖励）
    - other       : 其他（如司龄奖、推荐奖、节日红包等）

    个税处理
    --------
    年终奖（annual）可选两种计税方案（员工自行申报时选择最优方案）：
    方案A - 全年一次性奖金单独计税（优惠，2027年底前有效）：
        月均奖金 = amount / 12
        查税率表得到对应税率和速算扣除数
        tax_amount = amount × 税率 - 速算扣除数
    方案B - 并入当月综合所得计税（无优惠）：
        并入当月 SalaryRecord.gross_salary 一起计算累计预扣税

    status 状态流转
    ---------------
    draft     = 草稿（已录入，待HR确认）
    confirmed = 已确认（财务审批通过）
    paid      = 已发放（已划账到员工账户）

    company_id
    ----------
    记录发放奖金的法人主体（多公司架构下，奖金可能从不同主体发放）。

    关联关系
    --------
    - employee : 所属员工（selectin）
    """

    __tablename__ = "bonus_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    bonus_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="annual",
        comment="奖金类型: annual=年终奖, project=项目奖金, performance=绩效奖金, other=其他"
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, comment="所属年份")
    month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="发放月份")
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, comment="奖金金额"
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0"), comment="税额"
    )
    net_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0"), comment="税后实发"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft",
        comment="状态: draft=草稿, confirmed=已确认, paid=已发放"
    )
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    employee: Mapped["Employee"] = relationship("Employee", lazy="selectin")  # noqa: F821
