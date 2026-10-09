"""
薪酬管理服务 - Payroll Service
==============================
**业务职责**：
    售后管理系统的核心薪资计算引擎，负责：
    1. 月度薪资计算（单人 / 批量）
    2. 个人所得税计算（累计预扣法 + 年终奖单独计税）
    3. 五险一金计算（多城市 / 多公司配置）
    4. 薪资批次审批流转（draft → pending_approval → approved → paid）
    5. 工资条生成与员工查询回复
    6. 奖金管理（年终奖 / 项目奖 / 绩效奖）
    7. 银行代发文件导出（工行 / 建行 / 招行）
    8. 薪资成本看板与分析（按部门 / 按月份）
    9. 月度社保明细生成（五险一金分险种）

**核心公式**：
    应发工资(gross) = 基本工资 + 岗位工资 + 绩效工资×绩效系数
                      + 各项补贴 + 加班费（工作日×1.5 / 休息日×2.0 / 节假日×3.0）
    实发工资(net)   = gross - 社保个人 - 公积金个人 - 本月个税
    个税(累计预扣法)：
        累计预扣预缴应纳税所得额 = 累计收入 - 累计减除费用(5000×月数)
                                    - 累计专项扣除(社保+公积金) - 累计专项附加扣除
        本月预扣税额 = MAX(累计应缴税 - 已扣缴税额, 0)

**模块对应 Service 类**：
    - CompanyService               公司主体 CRUD
    - TaxLawVersionService         税法版本管理（多版本 / 历史追溯）
    - EmployeeTaxDeductionService  员工专项附加扣除 CRUD
    - SalaryStructureService       薪酬结构 CRUD
    - SocialInsuranceConfigService 社保/公积金城市配置
    - TaxCalculationService        个税计算引擎
    - PayrollCalculationService    薪资计算引擎（核心）
    - SalaryRecordService          薪资记录管理 + 审批流
    - PaySlipService               工资条发送与员工查询
    - TaxRecordService             个税记录查询
    - PayrollDashboardService      薪资成本看板
    - SocialInsuranceRecordService 月度社保明细生成
    - BonusService                 奖金管理
    - BankPaymentService           银行代发文件生成
    - CostAnalysisService          薪资成本分析

**技术栈**：FastAPI + SQLAlchemy 2.0 Async + PostgreSQL + Pydantic v2
**调用层次**：API 端点 → Service 方法 → SQLAlchemy ORM → PostgreSQL
"""

import csv
import io
import json
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional, Union

from fastapi import HTTPException
from sqlalchemy import and_, select, func, delete, or_, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import (
    BonusRecord,
    Company,
    TaxLawVersion,
    TaxBracket,
    EmployeeTaxDeduction,
    SalaryStructure,
    SocialInsuranceConfig,
    SalaryRecord,
    TaxRecord,
    TaxImportBatch,
    TaxImportRow,
    PaySlip,
    SocialInsuranceRecord,
)
from app.models.attendance import AttendanceMonthSummary
from app.models.leave import ApprovalStatus as LeaveApprovalStatus, HalfDay, LeaveRequest, LeaveType
from app.schemas.payroll import (
    BonusRecordCreate,
    BulkBonusCreate,
    CompanyCreate,
    CompanyUpdate,
    TaxLawVersionCreate,
    TaxLawVersionUpdate,
    TaxBracketCreate,
    EmployeeTaxDeductionCreate,
    EmployeeTaxDeductionUpdate,
    SalaryStructureCreate,
    SalaryStructureUpdate,
    SocialInsuranceConfigCreate,
    SocialInsuranceConfigUpdate,
    SalaryRecordAdjust,
    PayrollCalcRequest,
    PaySlipSendRequest,
    PaySlipQuerySubmit,
    PaySlipReply,
    BonusTaxRequest,
    SalaryCostSummary,
    CostAnalysisResult,
    DepartmentCostItem,
    MonthCostItem,
    GenerateSocialInsuranceRequest,
)

# ---------------------------------------------------------------------------
# 兜底税率表(DB不可用时fallback, 2019个税改革版)
# ---------------------------------------------------------------------------
# 累计预扣法税率表（综合所得适用）
# 每个元组：(累计应纳税所得额上限, 适用税率, 速算扣除数)
# 公式：应缴税 = 累计应纳税所得额 × 税率 - 速算扣除数
_DEFAULT_CUMULATIVE_BRACKETS: list[tuple[Decimal, Decimal, Decimal]] = [
    (Decimal("36000"),    Decimal("0.03"), Decimal("0")),      # 0~3.6万：3%
    (Decimal("144000"),   Decimal("0.10"), Decimal("2520")),   # 3.6~14.4万：10%
    (Decimal("300000"),   Decimal("0.20"), Decimal("16920")),  # 14.4~30万：20%
    (Decimal("420000"),   Decimal("0.25"), Decimal("31920")),  # 30~42万：25%
    (Decimal("660000"),   Decimal("0.30"), Decimal("52920")),  # 42~66万：30%
    (Decimal("960000"),   Decimal("0.35"), Decimal("85920")),  # 66~96万：35%
    (Decimal("999999999"), Decimal("0.45"), Decimal("181920")), # 96万以上：45%
]

# 全年一次性奖金单独计税税率表（年终奖适用）
# 方法：先用 年终奖÷12 确定所在档次，再对全额应用该档次税率
# 每个元组：(月均金额上限, 适用税率, 速算扣除数)
_DEFAULT_BONUS_BRACKETS: list[tuple[Decimal, Decimal, Decimal]] = [
    (Decimal("3000"),    Decimal("0.03"), Decimal("0")),     # 月均0~3000：3%
    (Decimal("12000"),   Decimal("0.10"), Decimal("210")),   # 月均3000~12000：10%
    (Decimal("25000"),   Decimal("0.20"), Decimal("1410")),  # 月均12000~25000：20%
    (Decimal("35000"),   Decimal("0.25"), Decimal("2660")),  # 月均25000~35000：25%
    (Decimal("55000"),   Decimal("0.30"), Decimal("4410")),  # 月均35000~55000：30%
    (Decimal("80000"),   Decimal("0.35"), Decimal("7160")),  # 月均55000~80000：35%
    (Decimal("999999999"), Decimal("0.45"), Decimal("15160")), # 月均80000以上：45%
]

# 月度基本减除费用（5000元/月，2019年改革后标准）
_DEFAULT_MONTHLY_TAX_FREE = Decimal("5000")

# 向后兼容别名（测试文件/旧代码引用）
TAX_BRACKETS = _DEFAULT_CUMULATIVE_BRACKETS
BONUS_TAX_BRACKETS = _DEFAULT_BONUS_BRACKETS
MONTHLY_TAX_FREE = _DEFAULT_MONTHLY_TAX_FREE


def _quantize(value: Decimal) -> Decimal:
    """对 Decimal 值四舍五入到2位小数（金额统一精度处理）。

    Args:
        value: 待处理的 Decimal 金额值

    Returns:
        保留2位小数、四舍五入后的 Decimal
    """
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _parse_tax_version_meta(description: Optional[str]) -> dict:
    """解析税法版本 description 中的扩展元数据。

    当前前端将专项附加扣除开关和规则备注以 JSON 形式存入 description：
        {
          "enabled_deduction_types": ["子女教育", "继续教育"],
          "notes": "2026 版规则"
        }

    若 description 不是 JSON，则按纯文本备注处理，并回退到默认启用项。
    """
    default_enabled = ["子女教育", "继续教育", "住房贷款利息", "住房租金", "赡养老人"]
    if not description:
        return {"enabled_deduction_types": default_enabled, "notes": ""}
    try:
        payload = json.loads(description)
        enabled_types = payload.get("enabled_deduction_types")
        return {
            "enabled_deduction_types": enabled_types if isinstance(enabled_types, list) else default_enabled,
            "notes": payload.get("notes", ""),
        }
    except Exception:
        return {"enabled_deduction_types": default_enabled, "notes": description}


# ============================================================
# Company 公司主体 CRUD
# ============================================================

class CompanyService:
    """公司主体管理服务。

    本公司支持多法人主体（如总公司 + 子公司），薪资记录、社保配置均可
    按 company_id 区分归属。本服务提供公司主体的增删改查能力。

    典型调用关系：
        - SocialInsuranceConfigService.list_by_city_year() 按 company_id 优先匹配配置
        - PayrollCalculationService.calculate_single() 自动读取员工的 company_id
    """

    @staticmethod
    async def _validate_parent_company(
        db: AsyncSession,
        parent_company_id: Optional[int],
        company_id: Optional[int] = None,
    ) -> None:
        """校验公司隶属关系是否合法（存在、非自指、无环）。"""
        if parent_company_id is None:
            return

        parent = await CompanyService.get(db, parent_company_id)
        if not parent:
            raise HTTPException(status_code=400, detail="上级公司不存在")

        if company_id is not None and parent_company_id == company_id:
            raise HTTPException(status_code=400, detail="上级公司不能是自己")

        # 禁止形成环：若上级链路中出现当前公司，则会形成循环隶属关系。
        if company_id is None:
            return
        visited: set[int] = set()
        cursor = parent
        while cursor and cursor.parent_company_id:
            if cursor.id in visited:
                break
            visited.add(cursor.id)
            if cursor.parent_company_id == company_id:
                raise HTTPException(status_code=400, detail="不能将公司设置为其子孙公司的下级")
            cursor = await CompanyService.get(db, cursor.parent_company_id)

    @staticmethod
    async def create(db: AsyncSession, data: CompanyCreate) -> Company:
        """创建公司主体记录。

        Args:
            db:   异步数据库会话
            data: 公司创建 Schema（名称、统一信用代码、城市等）

        Returns:
            新建的 Company ORM 对象（含自增 id）
        """
        await CompanyService._validate_parent_company(
            db=db,
            parent_company_id=data.parent_company_id,
            company_id=None,
        )
        obj = Company(**data.model_dump())
        db.add(obj)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def get(db: AsyncSession, company_id: int) -> Optional[Company]:
        """按 ID 查询单个公司主体。

        Args:
            db:         异步数据库会话
            company_id: 公司主键 ID

        Returns:
            Company 对象，不存在则返回 None
        """
        result = await db.execute(select(Company).where(Company.id == company_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(db: AsyncSession, active_only: bool = True) -> list[Company]:
        """查询所有公司主体，按名称排序。

        Args:
            db:          异步数据库会话
            active_only: 是否只返回启用状态的公司，默认 True

        Returns:
            Company 对象列表
        """
        q = select(Company)
        if active_only:
            q = q.where(Company.is_active.is_(True))
        q = q.order_by(Company.name)
        result = await db.execute(q)
        return list(result.scalars().all())

    @staticmethod
    async def update(db: AsyncSession, company_id: int, data: CompanyUpdate) -> Optional[Company]:
        """更新公司主体信息（仅更新传入的字段）。

        Args:
            db:         异步数据库会话
            company_id: 公司主键 ID
            data:       公司更新 Schema（支持部分字段更新）

        Returns:
            更新后的 Company 对象，不存在则返回 None
        """
        obj = await CompanyService.get(db, company_id)
        if not obj:
            return None
        update_data = data.model_dump(exclude_unset=True)
        if "parent_company_id" in update_data:
            await CompanyService._validate_parent_company(
                db=db,
                parent_company_id=update_data.get("parent_company_id"),
                company_id=company_id,
            )
        for field, value in update_data.items():
            setattr(obj, field, value)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def delete(db: AsyncSession, company_id: int) -> bool:
        """删除公司主体记录。

        Args:
            db:         异步数据库会话
            company_id: 公司主键 ID

        Returns:
            True 表示删除成功，False 表示记录不存在
        """
        obj = await CompanyService.get(db, company_id)
        if not obj:
            return False

        child_exists = await db.scalar(
            select(Company.id).where(Company.parent_company_id == company_id).limit(1)
        )
        if child_exists:
            raise HTTPException(status_code=400, detail="请先处理下级公司后再删除")

        await db.delete(obj)
        await db.flush()
        return True


# ============================================================
# TaxLawVersion 税法版本 CRUD + 版本解析
# ============================================================

class TaxLawVersionService:
    """税法版本管理服务。

    支持多版本税法的存储与历史追溯，例如：
        - 2019年个税改革版（月免征额5000元，综合所得7级税率表）
        - 未来政策调整后的新版税率表

    每个 TaxLawVersion 包含：
        - effective_date / end_date：有效期区间
        - monthly_tax_free：月度减除费用（默认5000元）
        - TaxBracket 列表：按 bracket_type 区分（cumulative=累计预扣 / bonus=全年一次性奖金）

    调用关系：
        - PayrollCalculationService.calculate_single() 通过 get_applicable_version()
          自动选取当月适用税法版本，保证历史薪资可追溯
        - TaxCalculationService.calculate_and_save() 使用版本对应的税率表计算个税
    """

    @staticmethod
    async def create(db: AsyncSession, data: TaxLawVersionCreate) -> TaxLawVersion:
        """创建税法版本并批量写入税率档次。

        流程：先插入 TaxLawVersion 头记录（flush 获取 id），
        再逐条插入对应的 TaxBracket 税率档次。

        Args:
            db:   异步数据库会话
            data: TaxLawVersionCreate Schema，含 brackets 列表

        Returns:
            新建的 TaxLawVersion ORM 对象（含 id）
        """
        brackets_data = data.brackets
        version_data = data.model_dump(exclude={"brackets"})
        version = TaxLawVersion(**version_data)
        db.add(version)
        await db.flush()  # 先 flush 以获取 version.id，供 TaxBracket 外键引用
        for i, b in enumerate(brackets_data):
            bracket = TaxBracket(version_id=version.id, sort_order=i, **b.model_dump())
            db.add(bracket)
        await db.flush()
        await db.refresh(version)
        return version

    @staticmethod
    async def get(db: AsyncSession, version_id: int) -> Optional[TaxLawVersion]:
        """按 ID 查询税法版本。

        Args:
            db:         异步数据库会话
            version_id: 税法版本主键 ID

        Returns:
            TaxLawVersion 对象，不存在则返回 None
        """
        result = await db.execute(
            select(TaxLawVersion).where(TaxLawVersion.id == version_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(db: AsyncSession) -> list[TaxLawVersion]:
        """查询所有税法版本，按生效日期倒序排列（最新版本在前）。

        Args:
            db: 异步数据库会话

        Returns:
            TaxLawVersion 对象列表
        """
        result = await db.execute(
            select(TaxLawVersion).order_by(TaxLawVersion.effective_date.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_applicable_version(
        db: AsyncSession, ref_date: date
    ) -> Optional[TaxLawVersion]:
        """根据参考日期查找应适用的税法版本。

        匹配条件：is_active=True AND effective_date <= ref_date AND
                  (end_date IS NULL OR end_date > ref_date)
        若有多条满足条件，取 effective_date 最大（最新生效）的一条。

        Args:
            db:       异步数据库会话
            ref_date: 参考日期（通常为薪资计算月的第一天，如 2024-03-01）

        Returns:
            匹配的 TaxLawVersion 对象，无匹配则返回 None（此时使用兜底税率表）
        """
        result = await db.execute(
            select(TaxLawVersion)
            .where(
                and_(
                    TaxLawVersion.is_active.is_(True),
                    TaxLawVersion.effective_date <= ref_date,
                    or_(
                        TaxLawVersion.end_date.is_(None),
                        TaxLawVersion.end_date > ref_date,
                    ),
                )
            )
            .order_by(TaxLawVersion.effective_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_brackets(
        db: AsyncSession, version_id: int, bracket_type: str
    ) -> list[tuple[Decimal, Decimal, Decimal]]:
        """获取指定税法版本、指定类型的税率档次列表。

        Args:
            db:           异步数据库会话
            version_id:   税法版本 ID
            bracket_type: 档次类型：'cumulative'=累计预扣法，'bonus'=全年一次性奖金

        Returns:
            税率档次列表，每项为 (上限金额, 税率, 速算扣除数) 三元组，按 sort_order 升序排列
            若无记录则返回空列表（调用方应回退到兜底税率表）
        """
        result = await db.execute(
            select(TaxBracket)
            .where(
                and_(
                    TaxBracket.version_id == version_id,
                    TaxBracket.bracket_type == bracket_type,
                )
            )
            .order_by(TaxBracket.sort_order)
        )
        brackets = result.scalars().all()
        return [(b.upper_bound, b.rate, b.quick_deduction) for b in brackets]

    @staticmethod
    async def add_bracket(
        db: AsyncSession, version_id: int, data: TaxBracketCreate
    ) -> TaxBracket:
        """向指定税法版本追加一条税率档次记录。

        Args:
            db:         异步数据库会话
            version_id: 目标税法版本 ID
            data:       TaxBracketCreate Schema（上限/税率/速算扣除数/类型/排序）

        Returns:
            新建的 TaxBracket ORM 对象
        """
        bracket = TaxBracket(version_id=version_id, **data.model_dump())
        db.add(bracket)
        await db.flush()
        await db.refresh(bracket)
        return bracket

    @staticmethod
    async def delete_bracket(db: AsyncSession, bracket_id: int) -> bool:
        """删除单条税率档次记录。

        Args:
            db:         异步数据库会话
            bracket_id: TaxBracket 主键 ID

        Returns:
            True 表示删除成功，False 表示记录不存在
        """
        result = await db.execute(select(TaxBracket).where(TaxBracket.id == bracket_id))
        obj = result.scalar_one_or_none()
        if not obj:
            return False
        await db.delete(obj)
        await db.flush()
        return True

    @staticmethod
    async def update(
        db: AsyncSession, version_id: int, data: TaxLawVersionUpdate
    ) -> Optional[TaxLawVersion]:
        """更新税法版本头信息（仅更新传入的字段，不影响已关联的 TaxBracket）。

        Args:
            db:         异步数据库会话
            version_id: 税法版本主键 ID
            data:       TaxLawVersionUpdate Schema（支持部分字段更新）

        Returns:
            更新后的 TaxLawVersion 对象，不存在则返回 None
        """
        obj = await TaxLawVersionService.get(db, version_id)
        if not obj:
            return None
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(obj, field, value)
        await db.flush()
        await db.refresh(obj)
        return obj


# ============================================================
# EmployeeTaxDeduction 员工专项附加扣除 CRUD
# ============================================================

class EmployeeTaxDeductionService:
    """员工专项附加扣除管理服务。

    专项附加扣除是 2019 年个税改革引入的税前扣除项目，包括：
        子女教育 / 继续教育 / 大病医疗 / 住房贷款利息 /
        住房租金 / 赡养老人 / 3岁以下婴幼儿照护

    每条记录包含：deduction_type（类型）、monthly_amount（每月扣除额）、
    start_year/start_month、end_year/end_month（有效期）。

    关键方法：
        - get_monthly_total()：汇总某员工某月所有有效专项附加扣除金额
          被 PayrollCalculationService.calculate_single() 自动调用
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: EmployeeTaxDeductionCreate
    ) -> EmployeeTaxDeduction:
        """创建员工专项附加扣除记录。

        Args:
            db:   异步数据库会话
            data: EmployeeTaxDeductionCreate Schema

        Returns:
            新建的 EmployeeTaxDeduction ORM 对象
        """
        obj = EmployeeTaxDeduction(**data.model_dump())
        db.add(obj)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def get(db: AsyncSession, deduction_id: int) -> Optional[EmployeeTaxDeduction]:
        """按 ID 查询专项附加扣除记录。

        Args:
            db:           异步数据库会话
            deduction_id: 记录主键 ID

        Returns:
            EmployeeTaxDeduction 对象，不存在则返回 None
        """
        result = await db.execute(
            select(EmployeeTaxDeduction).where(EmployeeTaxDeduction.id == deduction_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_employee(
        db: AsyncSession, employee_id: int, year: Optional[int] = None
    ) -> list[EmployeeTaxDeduction]:
        """查询员工的专项附加扣除列表（可按年份过滤）。

        年份过滤条件：start_year <= year AND (end_year IS NULL OR end_year >= year)
        即只返回在指定年份内有效的记录。

        Args:
            db:          异步数据库会话
            employee_id: 员工 ID
            year:        过滤年份，None 表示返回所有记录

        Returns:
            EmployeeTaxDeduction 列表，按扣除类型排序
        """
        q = select(EmployeeTaxDeduction).where(
            EmployeeTaxDeduction.employee_id == employee_id
        )
        if year:
            # 包含指定年份的记录: start_year <= year AND (end_year IS NULL OR end_year >= year)
            q = q.where(
                and_(
                    EmployeeTaxDeduction.start_year <= year,
                    or_(
                        EmployeeTaxDeduction.end_year.is_(None),
                        EmployeeTaxDeduction.end_year >= year,
                    ),
                )
            )
        q = q.order_by(EmployeeTaxDeduction.deduction_type)
        result = await db.execute(q)
        return list(result.scalars().all())

    @staticmethod
    async def get_monthly_total(
        db: AsyncSession,
        employee_id: int,
        year: int,
        month: int,
        enabled_types: Optional[list[str]] = None,
    ) -> Decimal:
        """计算某员工在指定年月的专项附加扣除合计金额。

        筛选条件（精确到月）：
            is_active = True
            AND (start_year < year OR (start_year = year AND start_month <= month))
            AND (end_year IS NULL OR end_year > year
                 OR (end_year = year AND end_month >= month))

        此方法被 PayrollCalculationService.calculate_single() 自动调用，
        结果进入个税累计预扣的专项附加扣除累计值。

        Args:
            db:          异步数据库会话
            employee_id: 员工 ID
            year:        目标年份
            month:       目标月份（1-12）

        Returns:
            该月专项附加扣除合计（Decimal，四舍五入到2位小数）
        """
        conditions = [
            EmployeeTaxDeduction.employee_id == employee_id,
            EmployeeTaxDeduction.is_active.is_(True),
            or_(
                EmployeeTaxDeduction.start_year < year,
                and_(
                    EmployeeTaxDeduction.start_year == year,
                    EmployeeTaxDeduction.start_month <= month,
                ),
            ),
            or_(
                EmployeeTaxDeduction.end_year.is_(None),
                EmployeeTaxDeduction.end_year > year,
                and_(
                    EmployeeTaxDeduction.end_year == year,
                    EmployeeTaxDeduction.end_month >= month,
                ),
            ),
        ]
        if enabled_types is not None:
            if not enabled_types:
                return Decimal("0")
            conditions.append(EmployeeTaxDeduction.deduction_type.in_(enabled_types))

        result = await db.execute(
            select(EmployeeTaxDeduction).where(and_(*conditions))
        )
        items = result.scalars().all()
        return _quantize(sum((item.monthly_amount for item in items), Decimal("0")))

    @staticmethod
    async def update(
        db: AsyncSession, deduction_id: int, data: EmployeeTaxDeductionUpdate
    ) -> Optional[EmployeeTaxDeduction]:
        """更新专项附加扣除记录（仅更新传入的字段）。

        Args:
            db:           异步数据库会话
            deduction_id: 记录主键 ID
            data:         EmployeeTaxDeductionUpdate Schema

        Returns:
            更新后的对象，不存在则返回 None
        """
        obj = await EmployeeTaxDeductionService.get(db, deduction_id)
        if not obj:
            return None
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(obj, field, value)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def delete(db: AsyncSession, deduction_id: int) -> bool:
        """删除专项附加扣除记录。

        Args:
            db:           异步数据库会话
            deduction_id: 记录主键 ID

        Returns:
            True 表示删除成功，False 表示记录不存在
        """
        obj = await EmployeeTaxDeductionService.get(db, deduction_id)
        if not obj:
            return False
        await db.delete(obj)
        await db.flush()
        return True

    @staticmethod
    async def import_file(
        db: AsyncSession,
        file_bytes: bytes,
        filename: str,
    ) -> dict:
        from app.models.employee import Employee
        from app.schemas.payroll import DEDUCTION_TYPES

        rows = SalaryRecordService._load_import_rows(file_bytes, filename)
        header_row_idx = next(
            (idx for idx, row in enumerate(rows) if any(str(cell or "").strip() for cell in row)),
            None,
        )
        if header_row_idx is None:
            raise HTTPException(status_code=400, detail="导入文件为空")

        field_map = {
            "工号": "employee_no",
            "employee_no": "employee_no",
            "employeeno": "employee_no",
            "扣除类型": "deduction_type",
            "专项附加扣除类型": "deduction_type",
            "月扣除额": "monthly_amount",
            "每月扣除金额": "monthly_amount",
            "monthly_amount": "monthly_amount",
            "开始年份": "start_year",
            "开始月": "start_month",
            "开始月份": "start_month",
            "结束年份": "end_year",
            "结束月": "end_month",
            "结束月份": "end_month",
            "备注": "description",
            "说明": "description",
            "description": "description",
            "是否启用": "is_active",
            "启用": "is_active",
            "is_active": "is_active",
        }
        headers = [
            field_map.get(SalaryRecordService._normalize_import_header(cell))
            for cell in rows[header_row_idx]
        ]
        if "employee_no" not in headers:
            raise HTTPException(status_code=400, detail='导入文件缺少“工号”列')

        employee_nos: set[str] = set()
        data_rows: list[tuple[int, dict[str, object]]] = []
        for row_idx in range(header_row_idx + 1, len(rows)):
            row = rows[row_idx]
            payload: dict[str, object] = {}
            for col_idx, field in enumerate(headers):
                if not field or col_idx >= len(row):
                    continue
                value = row[col_idx]
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                payload[field] = value
            if not payload:
                continue
            employee_no = str(payload.get("employee_no") or "").strip()
            if employee_no:
                employee_nos.add(employee_no)
            data_rows.append((row_idx + 1, payload))

        if not data_rows:
            raise HTTPException(status_code=400, detail="导入文件没有可用数据行")

        employee_result = await db.execute(select(Employee).where(Employee.employee_no.in_(employee_nos)))
        employee_map = {str(item.employee_no): item for item in employee_result.scalars().all()}

        def parse_int(value: object, label: str, row_no: int, required: bool = True) -> Optional[int]:
            if value in (None, ""):
                if required:
                    raise ValueError(f"{label}不能为空")
                return None
            try:
                return int(float(str(value).strip()))
            except Exception:
                raise ValueError(f"{label}格式不正确")

        def parse_bool(value: object) -> bool:
            text = str(value or "").strip().lower()
            if text in ("", "1", "true", "yes", "y", "是", "启用"):
                return True
            if text in ("0", "false", "no", "n", "否", "停用", "禁用"):
                return False
            return True

        success_count = 0
        errors: list[dict[str, object]] = []
        for row_no, payload in data_rows:
            employee_no = str(payload.get("employee_no") or "").strip()
            employee = employee_map.get(employee_no)
            if not employee:
                errors.append({"row": row_no, "reason": f"未找到工号为 {employee_no} 的员工"})
                continue
            deduction_type = str(payload.get("deduction_type") or "").strip()
            if not deduction_type:
                errors.append({"row": row_no, "reason": "扣除类型不能为空"})
                continue
            if deduction_type not in DEDUCTION_TYPES:
                errors.append({"row": row_no, "reason": f"不支持的扣除类型: {deduction_type}"})
                continue
            try:
                monthly_amount = SalaryRecordService._parse_import_decimal(payload.get("monthly_amount"))
                start_year = parse_int(payload.get("start_year"), "开始年份", row_no)
                start_month = parse_int(payload.get("start_month"), "开始月份", row_no)
                end_year = parse_int(payload.get("end_year"), "结束年份", row_no, required=False)
                end_month = parse_int(payload.get("end_month"), "结束月份", row_no, required=False)
                description = str(payload.get("description") or "").strip() or None
                is_active = parse_bool(payload.get("is_active"))
            except ValueError as exc:
                errors.append({"row": row_no, "reason": str(exc)})
                continue

            existing_result = await db.execute(
                select(EmployeeTaxDeduction).where(
                    and_(
                        EmployeeTaxDeduction.employee_id == employee.id,
                        EmployeeTaxDeduction.deduction_type == deduction_type,
                        EmployeeTaxDeduction.start_year == start_year,
                        EmployeeTaxDeduction.start_month == start_month,
                    )
                )
            )
            existing = existing_result.scalar_one_or_none()
            try:
                async with db.begin_nested():
                    if existing:
                        existing.monthly_amount = monthly_amount
                        existing.end_year = end_year
                        existing.end_month = end_month
                        existing.description = description
                        existing.is_active = is_active
                    else:
                        db.add(
                            EmployeeTaxDeduction(
                                employee_id=employee.id,
                                deduction_type=deduction_type,
                                monthly_amount=monthly_amount,
                                start_year=start_year,
                                start_month=start_month,
                                end_year=end_year,
                                end_month=end_month,
                                description=description,
                                is_active=is_active,
                            )
                        )
                    await db.flush()
                success_count += 1
            except Exception as exc:
                errors.append({"row": row_no, "reason": f"导入失败: {exc}"})

        return {"success_count": success_count, "fail_count": len(errors), "errors": errors}


# ============================================================
# SalaryStructure CRUD
# ============================================================

class SalaryStructureService:
    """薪酬结构管理服务。

    薪酬结构（SalaryStructure）记录员工的薪资组成，包括：
        - base_salary：基本工资
        - position_salary：岗位工资
        - performance_salary：绩效工资基数
        - performance_coefficient：绩效系数（绩效工资实发 = 基数 × 系数）
        - 各项补贴：夜班补贴/高温补贴/项目补贴/餐补/交通补贴/住房补贴/其他补贴
        - effective_date：生效日期（支持调薪历史）

    一名员工可有多条薪酬结构记录，代表历次调薪。
    get_effective() 根据日期找到最新生效的一条，是薪资计算的核心数据源。

    调用关系：
        - PayrollCalculationService.calculate_single() 调用 get_effective()
          获取当月薪酬结构，作为计算基础
    """

    @staticmethod
    async def create(db: AsyncSession, data: SalaryStructureCreate) -> SalaryStructure:
        """创建薪酬结构记录（通常在入职定薪或调薪时调用）。

        Args:
            db:   异步数据库会话
            data: SalaryStructureCreate Schema（含员工ID、各项薪资、生效日期）

        Returns:
            新建的 SalaryStructure ORM 对象
        """
        obj = SalaryStructure(**data.model_dump())
        db.add(obj)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def list_all(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 1000,
    ) -> list[SalaryStructure]:
        """分页获取所有员工的薪酬结构，按员工ID和生效日期倒序排列。

        Args:
            db:    异步数据库会话
            skip:  跳过的记录数（分页偏移）
            limit: 返回的最大记录数

        Returns:
            SalaryStructure 列表
        """
        result = await db.execute(
            select(SalaryStructure)
            .order_by(SalaryStructure.employee_id, SalaryStructure.effective_date.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get(db: AsyncSession, structure_id: int) -> Optional[SalaryStructure]:
        """按 ID 查询薪酬结构记录。

        Args:
            db:           异步数据库会话
            structure_id: 薪酬结构主键 ID

        Returns:
            SalaryStructure 对象，不存在则返回 None
        """
        result = await db.execute(
            select(SalaryStructure).where(SalaryStructure.id == structure_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_employee(
        db: AsyncSession, employee_id: int
    ) -> list[SalaryStructure]:
        """获取员工的全部薪酬结构历史，按生效日期倒序排列（最新在前）。

        Args:
            db:          异步数据库会话
            employee_id: 员工 ID

        Returns:
            该员工所有历次薪酬结构记录列表
        """
        result = await db.execute(
            select(SalaryStructure)
            .where(SalaryStructure.employee_id == employee_id)
            .order_by(SalaryStructure.effective_date.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_effective(
        db: AsyncSession, employee_id: int, ref_date: Union[date, datetime]
    ) -> Optional[SalaryStructure]:
        """获取员工在指定日期生效的最新薪酬结构。

        筛选条件：employee_id = ? AND effective_date <= ref_date
        取 effective_date 最大（最近生效）的一条。
        这是薪资计算的核心方法——始终取调薪生效日 <= 计算月的最新薪资档次。

        Args:
            db:          异步数据库会话
            employee_id: 员工 ID
            ref_date:    参考日期（通常为薪资计算月的第一天）

        Returns:
            生效中的 SalaryStructure 对象，若员工尚无薪酬结构则返回 None
        """
        result = await db.execute(
            select(SalaryStructure)
            .where(
                and_(
                    SalaryStructure.employee_id == employee_id,
                    SalaryStructure.effective_date <= ref_date,
                )
            )
            .order_by(SalaryStructure.effective_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update(
        db: AsyncSession, structure_id: int, data: SalaryStructureUpdate
    ) -> Optional[SalaryStructure]:
        """更新薪酬结构记录（仅更新传入的字段）。

        Args:
            db:           异步数据库会话
            structure_id: 薪酬结构主键 ID
            data:         SalaryStructureUpdate Schema（支持部分字段更新）

        Returns:
            更新后的 SalaryStructure 对象，不存在则返回 None
        """
        obj = await SalaryStructureService.get(db, structure_id)
        if not obj:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(obj, field, value)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def delete(db: AsyncSession, structure_id: int) -> bool:
        """删除薪酬结构记录。

        Args:
            db:           异步数据库会话
            structure_id: 薪酬结构主键 ID

        Returns:
            True 表示删除成功，False 表示记录不存在
        """
        obj = await SalaryStructureService.get(db, structure_id)
        if not obj:
            return False
        await db.delete(obj)
        await db.flush()
        return True


# ============================================================
# SocialInsuranceConfig CRUD
# ============================================================

class SocialInsuranceConfigService:
    """社会保险与住房公积金配置管理服务。

    社保配置（SocialInsuranceConfig）存储各城市、各险种的缴费比例与基数上下限，
    支持多公司配置覆盖（company_id 非空的配置优先于通用配置）。

    险种类型（insurance_type）包括：
        养老 / 医疗 / 失业 / 工伤 / 生育 / 公积金

    典型费率（2024年一般水平，以北京为例）：
        养老：个人 8%  单位 16%
        医疗：个人 2%  单位 6%（不含大额互助）
        失业：个人 0.5%  单位 0.5%
        工伤：个人 0%  单位 0.1%~1.5%（行业浮动）
        生育：个人 0%  单位 0.8%
        公积金：个人 12%  单位 12%（双方等额）

    缴费基数：max(min(gross, base_max), base_min)
    即工资高于上限用上限、低于下限用下限。

    关键规则：
        - 同一城市同一险种同一公司只能有一条启用记录（create 时自动停用其他）
        - list_by_city_year() / list_active_by_city() 实现公司专属配置优先于通用配置
        - activate_city_year() 批量切换年度配置（年初更新社保基数时使用）

    调用关系：
        - PayrollCalculationService._calc_social_insurance() 调用 list_active_by_city()
        - SocialInsuranceRecordService.generate() 调用 list_active_by_city()
    """

    @staticmethod
    async def create(
        db: AsyncSession, data: SocialInsuranceConfigCreate
    ) -> SocialInsuranceConfig:
        """创建社保配置，并自动停用同城市同险种的其他配置（互斥逻辑）。

        Args:
            db:   异步数据库会话
            data: SocialInsuranceConfigCreate Schema（城市/险种/年份/费率/基数上下限）

        Returns:
            新建的 SocialInsuranceConfig ORM 对象
        """
        obj = SocialInsuranceConfig(**data.model_dump())
        db.add(obj)
        await db.flush()
        # 若新建配置为启用状态，自动停用同城市同险种的其他配置（同城市同险种只能一条启用）
        if obj.is_active:
            await db.execute(
                __import__("sqlalchemy", fromlist=["update"]).update(SocialInsuranceConfig)
                .where(
                    and_(
                        SocialInsuranceConfig.city == obj.city,
                        SocialInsuranceConfig.insurance_type == obj.insurance_type,
                        SocialInsuranceConfig.id != obj.id,
                    )
                )
                .values(is_active=False)
            )
        await db.refresh(obj)
        return obj

    @staticmethod
    async def get(db: AsyncSession, config_id: int) -> Optional[SocialInsuranceConfig]:
        """按 ID 查询社保配置。

        Args:
            db:        异步数据库会话
            config_id: 配置主键 ID

        Returns:
            SocialInsuranceConfig 对象，不存在则返回 None
        """
        result = await db.execute(
            select(SocialInsuranceConfig).where(SocialInsuranceConfig.id == config_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_city_year(
        db: AsyncSession,
        city: str,
        year: int,
        company_id: Optional[int] = None,
    ) -> list[SocialInsuranceConfig]:
        """查询指定城市+年份的社保配置，实现公司专属优先于通用的配置覆盖。

        查询逻辑：
            1. 拉取该城市/年份下所有 is_active=True 的配置
               （company_id 匹配或 company_id 为 NULL 的）
            2. 按险种去重：对每个险种优先保留 company_id 非空的条目（公司专属），
               无专属配置则保留通用配置（company_id=NULL）

        Args:
            db:         异步数据库会话
            city:       城市名称（如"北京"）
            year:       年份（如 2024）
            company_id: 公司 ID，None 表示只查通用配置

        Returns:
            每个险种取最优先一条的 SocialInsuranceConfig 列表
        """
        result = await db.execute(
            select(SocialInsuranceConfig)
            .where(
                and_(
                    SocialInsuranceConfig.city == city,
                    SocialInsuranceConfig.year == year,
                    SocialInsuranceConfig.is_active.is_(True),
                    or_(
                        SocialInsuranceConfig.company_id == company_id,
                        SocialInsuranceConfig.company_id.is_(None),
                    ),
                )
            )
            .order_by(
                SocialInsuranceConfig.insurance_type,
                # company_id 非空排在前面（公司专属优先）
                SocialInsuranceConfig.company_id.desc().nulls_last(),
            )
        )
        all_configs = result.scalars().all()

        # 对每个险种只保留最优先的条目（公司专属 > 通用）
        seen: set[str] = set()
        deduped: list[SocialInsuranceConfig] = []
        for cfg in all_configs:
            if cfg.insurance_type not in seen:
                seen.add(cfg.insurance_type)
                deduped.append(cfg)
        return deduped

    @staticmethod
    async def update(
        db: AsyncSession, config_id: int, data: SocialInsuranceConfigUpdate
    ) -> Optional[SocialInsuranceConfig]:
        """更新社保配置（仅更新传入的字段）。

        Args:
            db:        异步数据库会话
            config_id: 配置主键 ID
            data:      SocialInsuranceConfigUpdate Schema

        Returns:
            更新后的对象，不存在则返回 None
        """
        obj = await SocialInsuranceConfigService.get(db, config_id)
        if not obj:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(obj, field, value)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def list_active_by_city(
        db: AsyncSession,
        city: str,
        company_id: Optional[int] = None,
    ) -> list[SocialInsuranceConfig]:
        """查询某城市当前启用的社保配置（is_active=True，不按年份过滤）。

        与 list_by_city_year() 的区别：不过滤年份，直接取 is_active=True 的配置。
        适用于月度薪资计算（以 is_active 为准，而非指定年份）。
        同样实现公司专属 > 通用的优先覆盖逻辑。

        Args:
            db:         异步数据库会话
            city:       城市名称
            company_id: 公司 ID，None 表示只查通用配置

        Returns:
            每个险种取最优先一条的 SocialInsuranceConfig 列表
        """
        result = await db.execute(
            select(SocialInsuranceConfig)
            .where(
                and_(
                    SocialInsuranceConfig.city == city,
                    SocialInsuranceConfig.is_active.is_(True),
                    or_(
                        SocialInsuranceConfig.company_id == company_id,
                        SocialInsuranceConfig.company_id.is_(None),
                    ),
                )
            )
            .order_by(
                SocialInsuranceConfig.insurance_type,
                SocialInsuranceConfig.company_id.desc().nulls_last(),
            )
        )
        all_configs = result.scalars().all()
        seen: set[str] = set()
        deduped: list[SocialInsuranceConfig] = []
        for cfg in all_configs:
            if cfg.insurance_type not in seen:
                seen.add(cfg.insurance_type)
                deduped.append(cfg)
        return deduped

    @staticmethod
    async def activate_city_year(
        db: AsyncSession, city: str, year: int
    ) -> int:
        """激活指定城市+年度的全部险种配置，同时停用该城市其余年度配置。

        使用场景：每年初更新社保缴费基数时，调用此方法一键切换到新年度配置。
        操作步骤：
            1. 先将该城市所有配置 is_active = False
            2. 再将该城市目标年份的所有配置 is_active = True
        注意：此操作具有排他性，同一城市在任意时刻只有一个年度的配置处于激活状态。

        Args:
            db:   异步数据库会话
            city: 城市名称
            year: 要激活的年份

        Returns:
            激活的记录数（即该城市该年份共有多少条险种配置）
        """
        from sqlalchemy import update as sa_update
        # 先全部停用该城市的配置
        await db.execute(
            sa_update(SocialInsuranceConfig)
            .where(SocialInsuranceConfig.city == city)
            .values(is_active=False)
        )
        # 再启用目标年度
        await db.execute(
            sa_update(SocialInsuranceConfig)
            .where(
                and_(
                    SocialInsuranceConfig.city == city,
                    SocialInsuranceConfig.year == year,
                )
            )
            .values(is_active=True)
        )
        count_result = await db.execute(
            select(func.count(SocialInsuranceConfig.id)).where(
                and_(
                    SocialInsuranceConfig.city == city,
                    SocialInsuranceConfig.year == year,
                    SocialInsuranceConfig.is_active.is_(True),
                )
            )
        )
        await db.flush()
        return int(count_result.scalar() or 0)

    @staticmethod
    async def list_by_city(
        db: AsyncSession,
        city: str,
    ) -> list[SocialInsuranceConfig]:
        """查询某城市所有年份的社保配置（含历史版本，用于历史对比或审计）。

        Args:
            db:   异步数据库会话
            city: 城市名称

        Returns:
            该城市所有年份的 SocialInsuranceConfig 列表，按年份倒序、险种正序排列
        """
        result = await db.execute(
            select(SocialInsuranceConfig)
            .where(SocialInsuranceConfig.city == city)
            .order_by(SocialInsuranceConfig.year.desc(), SocialInsuranceConfig.insurance_type)
        )
        return list(result.scalars().all())

    @staticmethod
    async def delete(db: AsyncSession, config_id: int) -> bool:
        """删除社保配置记录。

        Args:
            db:        异步数据库会话
            config_id: 配置主键 ID

        Returns:
            True 表示删除成功，False 表示记录不存在
        """
        obj = await SocialInsuranceConfigService.get(db, config_id)
        if not obj:
            return False
        await db.delete(obj)
        await db.flush()
        return True


# ============================================================
# Tax Calculation 个税计算
# ============================================================

class TaxCalculationService:
    """个人所得税计算引擎。

    实现 2019 年个税改革后的「综合所得累计预扣法」，以及
    全年一次性奖金的「单独计税」两种计税方式。

    累计预扣法核心公式（每月执行一次）：
        ① 累计预扣预缴应纳税所得额 =
               累计收入 - 累计减除费用(5000×已发薪月数)
               - 累计专项扣除(社保+公积金) - 累计专项附加扣除
        ② 本年累计应缴税 = 查税率表(累计预扣预缴应纳税所得额)
        ③ 本月预扣税额  = MAX(本年累计应缴税 - 已扣缴税额, 0)

    全年一次性奖金单独计税：
        ① 月均奖金 = 年终奖 ÷ 12
        ② 按月均奖金确定税率档次 → 用该税率对全额计税
        ③ 税额 = 年终奖金额 × 税率 - 速算扣除数

    关键方法：
        - _apply_brackets()         通用税率表查表（内部辅助）
        - calc_cumulative_tax()     按累计应纳税所得额查表返回累计应缴税
        - calc_bonus_tax_separate() 年终奖单独计税
        - get_previous_cumulative() 查询上月累计数据（用于递推）
        - calculate_and_save()      本月个税完整计算并落库（主入口）
        - calculate_bonus_tax()     年终奖个税计算并落库
    """

    @staticmethod
    def _apply_brackets(
        taxable: Decimal,
        brackets: list[tuple[Decimal, Decimal, Decimal]],
    ) -> Decimal:
        """通用税率表查表计算。

        遍历税率档次（已按 upper_bound 升序排列），找到第一个满足
        taxable <= upper_bound 的档次，按公式 taxable × rate - quick_deduction 计算。

        Args:
            taxable:  应纳税所得额（非负）
            brackets: 税率表，格式 [(上限, 税率, 速算扣除数), ...]

        Returns:
            应缴税额（≥ 0），保留2位小数
        """
        if taxable <= 0:
            return Decimal("0")
        for upper, rate, quick_deduction in brackets:
            if taxable <= upper:
                # 公式：应缴税 = 应纳税所得额 × 适用税率 - 速算扣除数
                return _quantize(taxable * rate - quick_deduction)
        # 兜底（理论上不应到达，最后一档 upper=999999999 覆盖所有情况）
        upper, rate, quick_deduction = brackets[-1]
        return _quantize(taxable * rate - quick_deduction)

    @staticmethod
    def calc_cumulative_tax(
        cumulative_taxable: Decimal,
        brackets: Optional[list[tuple[Decimal, Decimal, Decimal]]] = None,
    ) -> Decimal:
        """累计预扣法：根据累计应纳税所得额计算本年累计应缴税额。

        Args:
            cumulative_taxable: 累计预扣预缴应纳税所得额（年初至本月累计）
            brackets:           税率表，None 时使用 _DEFAULT_CUMULATIVE_BRACKETS 兜底

        Returns:
            本年累计应缴税额（非负，保留2位小数）
        """
        active = brackets or _DEFAULT_CUMULATIVE_BRACKETS
        return TaxCalculationService._apply_brackets(cumulative_taxable, active)

    @staticmethod
    def calc_bonus_tax_separate(
        bonus_amount: Decimal,
        brackets: Optional[list[tuple[Decimal, Decimal, Decimal]]] = None,
    ) -> Decimal:
        """全年一次性奖金单独计税。

        计算步骤：
            ① 月均奖金 = bonus_amount ÷ 12
            ② 按月均奖金金额在税率表中找到对应档次（税率 + 速算扣除数）
            ③ 税额 = bonus_amount × 该档次税率 - 速算扣除数
        注意：此方法不考虑当月工资，奖金全额单独适用此税率，
        实际项目中若员工选择并入综合所得，则应走累计预扣法。

        Args:
            bonus_amount: 全年一次性奖金金额
            brackets:     奖金税率表，None 时使用 _DEFAULT_BONUS_BRACKETS 兜底

        Returns:
            年终奖个税金额（非负，保留2位小数）
        """
        active = brackets or _DEFAULT_BONUS_BRACKETS
        # 月均化：用于定档（不是实际按月发放，仅用于找税率档次）
        monthly_avg = _quantize(bonus_amount / Decimal("12"))
        for upper, rate, quick_deduction in active:
            if monthly_avg <= upper:
                return _quantize(bonus_amount * rate - quick_deduction)
        upper, rate, quick_deduction = active[-1]
        return _quantize(bonus_amount * rate - quick_deduction)

    @staticmethod
    async def get_previous_cumulative(
        db: AsyncSession, employee_id: int, year: int, month: int
    ) -> Optional[TaxRecord]:
        """获取员工上一个月的个税累计数据记录。

        用于累计预扣法的递推：本月计算需要上月的累计收入/累计税额等数据。
        若月份为1月，则查询上一年12月（跨年处理）。

        Args:
            db:          异步数据库会话
            employee_id: 员工 ID
            year:        当前年份
            month:       当前月份（1-12）

        Returns:
            上月的 TaxRecord（tax_method='cumulative'），不存在则返回 None
            （返回 None 意味着本月是年度第一次计算，从零开始累计）
        """
        prev_month = month - 1
        prev_year = year
        if prev_month < 1:
            prev_month = 12
            prev_year = year - 1
        result = await db.execute(
            select(TaxRecord)
            .where(
                and_(
                    TaxRecord.employee_id == employee_id,
                    TaxRecord.year == prev_year,
                    TaxRecord.month == prev_month,
                    TaxRecord.tax_method == "cumulative",
                )
            )
            .order_by(TaxRecord.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def calculate_and_save(
        db: AsyncSession,
        employee_id: int,
        year: int,
        month: int,
        gross_salary: Decimal,
        social_insurance_employee: Decimal,
        housing_fund_employee: Decimal,
        special_deduction_total: Decimal = Decimal("0"),
        tax_law_version_id: Optional[int] = None,
        cumulative_brackets: Optional[list[tuple[Decimal, Decimal, Decimal]]] = None,
        monthly_tax_free: Optional[Decimal] = None,
    ) -> TaxRecord:
        """计算本月个税（累计预扣法）并将 TaxRecord 持久化到数据库。

        这是个税计算的主入口，被 PayrollCalculationService.calculate_single() 调用。
        支持传入 tax_law_version_id 以实现历史追溯（不同月份可用不同税法版本）。

        计算流程：
            1. 从数据库查询上月 TaxRecord，获取年初至上月的各项累计值
            2. 将本月数据叠加到累计值：
                   cum_income       += gross_salary
                   cum_tax_free     += monthly_tax_free（默认5000）
                   cum_deductions   += 社保个人 + 公积金个人
                   cum_special      += 专项附加扣除
            3. 计算累计应纳税所得额：
                   cum_taxable = cum_income - cum_tax_free - cum_deductions - cum_special
                   （若为负则取0）
            4. 查税率表得到本年累计应缴税：cum_tax = 查表(cum_taxable)
            5. 本月预扣税额 = MAX(cum_tax - 上月累计已缴税, 0)
               （确保本月税额不为负，避免退税出现在月度发薪中）
            6. 构造并保存 TaxRecord

        跨年处理：若上月记录存在但 year 不等于当前年，说明是新年第一月，
        重置所有累计值从零开始。

        Args:
            db:                         异步数据库会话
            employee_id:                员工 ID
            year:                       计算年份
            month:                      计算月份（1-12）
            gross_salary:               本月应发工资
            social_insurance_employee:  本月社保个人部分
            housing_fund_employee:      本月公积金个人部分
            special_deduction_total:    本月专项附加扣除合计（默认0）
            tax_law_version_id:         税法版本 ID（用于历史追溯，None则不关联）
            cumulative_brackets:        累计预扣税率表（None则使用兜底税率表）
            monthly_tax_free:           月度基本减除费用（None则使用5000元）

        Returns:
            新建并已持久化（flush）的 TaxRecord ORM 对象

        调用的下游：
            - TaxCalculationService.get_previous_cumulative() 获取上月累计数据
            - TaxCalculationService.calc_cumulative_tax()     查税率表计算累计税额
        """
        effective_tax_free = monthly_tax_free or _DEFAULT_MONTHLY_TAX_FREE

        # 幂等处理：删除本月已有的累计法税记录，避免重复计算产生重复行
        await db.execute(
            delete(TaxRecord).where(
                and_(
                    TaxRecord.employee_id == employee_id,
                    TaxRecord.year == year,
                    TaxRecord.month == month,
                    TaxRecord.tax_method == "cumulative",
                )
            )
        )

        prev = await TaxCalculationService.get_previous_cumulative(
            db, employee_id, year, month
        )

        if prev and prev.year == year:
            cum_income = prev.cumulative_income + gross_salary
            cum_tax_free = prev.cumulative_tax_free + effective_tax_free
            cum_deductions = prev.cumulative_deductions + social_insurance_employee + housing_fund_employee
            cum_special = prev.cumulative_special_deductions + special_deduction_total
            prev_cum_tax = prev.cumulative_tax
        else:
            cum_income = gross_salary
            cum_tax_free = effective_tax_free
            cum_deductions = social_insurance_employee + housing_fund_employee
            cum_special = special_deduction_total
            prev_cum_tax = Decimal("0")

        cum_taxable = cum_income - cum_tax_free - cum_deductions - cum_special
        if cum_taxable < 0:
            cum_taxable = Decimal("0")

        cum_tax = TaxCalculationService.calc_cumulative_tax(cum_taxable, cumulative_brackets)
        current_month_tax = max(cum_tax - prev_cum_tax, Decimal("0"))

        tax_record = TaxRecord(
            employee_id=employee_id,
            year=year,
            month=month,
            cumulative_income=_quantize(cum_income),
            cumulative_tax_free=_quantize(cum_tax_free),
            cumulative_deductions=_quantize(cum_deductions),
            cumulative_special_deductions=_quantize(cum_special),
            cumulative_taxable=_quantize(cum_taxable),
            cumulative_tax=_quantize(cum_tax),
            current_month_tax=_quantize(current_month_tax),
            tax_method="cumulative",
            tax_law_version_id=tax_law_version_id,
        )
        db.add(tax_record)
        await db.flush()
        await db.refresh(tax_record)
        return tax_record

    @staticmethod
    async def calculate_bonus_tax(
        db: AsyncSession, data: BonusTaxRequest
    ) -> TaxRecord:
        """年终奖个税计算并落库。

        支持两种计税方式（由 data.tax_method 决定）：
            - 'separate_bonus'：全年一次性奖金单独计税（调用 calc_bonus_tax_separate）
            - 其他（默认）：预留并入综合所得的占位逻辑（当前税额为0，需配合月度计算使用）

        自动从数据库查询适用的税法版本，并读取对应的 bonus 税率表；
        若数据库无匹配版本，则使用兜底 _DEFAULT_BONUS_BRACKETS。

        Args:
            db:   异步数据库会话
            data: BonusTaxRequest Schema（employee_id, year, bonus_amount, tax_method）

        Returns:
            新建并已持久化的 TaxRecord（month=0 表示年终奖记录，区别于月度记录）

        调用的下游：
            - TaxLawVersionService.get_applicable_version() 确定税法版本
            - TaxLawVersionService.get_brackets()           获取 bonus 税率表
            - TaxCalculationService.calc_bonus_tax_separate() 单独计税计算
        """
        # 获取适用税法版本
        ref_date = date(data.year, 12, 1)
        tax_version = await TaxLawVersionService.get_applicable_version(db, ref_date)
        bonus_brackets = None
        tax_law_version_id = None
        if tax_version:
            tax_law_version_id = tax_version.id
            bonus_brackets = await TaxLawVersionService.get_brackets(db, tax_version.id, "bonus")
            if not bonus_brackets:
                bonus_brackets = None

        if data.tax_method == "separate_bonus":
            tax = TaxCalculationService.calc_bonus_tax_separate(data.bonus_amount, bonus_brackets)
            tax_record = TaxRecord(
                employee_id=data.employee_id,
                year=data.year,
                month=0,
                cumulative_income=data.bonus_amount,
                cumulative_tax_free=Decimal("0"),
                cumulative_deductions=Decimal("0"),
                cumulative_special_deductions=Decimal("0"),
                cumulative_taxable=data.bonus_amount,
                cumulative_tax=tax,
                current_month_tax=tax,
                tax_method="separate_bonus",
                tax_law_version_id=tax_law_version_id,
            )
        else:
            tax_record = TaxRecord(
                employee_id=data.employee_id,
                year=data.year,
                month=0,
                cumulative_income=data.bonus_amount,
                cumulative_tax_free=Decimal("0"),
                cumulative_deductions=Decimal("0"),
                cumulative_special_deductions=Decimal("0"),
                cumulative_taxable=Decimal("0"),
                cumulative_tax=Decimal("0"),
                current_month_tax=Decimal("0"),
                tax_method="cumulative",
                tax_law_version_id=tax_law_version_id,
            )
        db.add(tax_record)
        await db.flush()
        await db.refresh(tax_record)
        return tax_record


# ============================================================
# Payroll Calculation 薪资计算引擎
# ============================================================

class PayrollCalculationService:
    """薪资计算引擎——售后管理系统的核心业务服务。

    负责月度薪资的完整计算流程，包括：
        1. 读取员工信息（社保城市、所属公司）
        2. 确定适用的税法版本（历史追溯）
        3. 自动汇总员工专项附加扣除
        4. 获取生效中的薪酬结构
        5. 计算各薪资分项（基本工资、岗位工资、绩效、补贴）
        6. 入离职折算（日历日 / 工作日两种模式）
        7. 计算社保和公积金（个人部分 + 公司部分）
        8. 计算本月个税（累计预扣法）
        9. 计算实发工资，检测异常，持久化 SalaryRecord

    薪资计算公式：
        gross（应发）= base × ratio + position × ratio + perf × ratio
                        + allowance × ratio + 加班费（工作日/休息日/节假日）
        net（实发）  = gross - 社保个人 - 公积金个人 - 当月个税 + 手动调整

    折算比例（ratio）：
        - 全月：ratio = 1
        - 日历日折算：ratio = proration_days / 当月总天数
        - 工作日折算：ratio = proration_days / 21.75（法定月均工作日）

    关键方法：
        - _calc_total_allowance()     计算各项补贴合计（内部辅助）
        - _calc_social_insurance()    社保+公积金计算（内部辅助，支持多城市）
        - _detect_anomalies()         异常检测（入离职折算/薪资变动幅度/手动调整）
        - calculate_single()          单员工月薪计算主入口
        - calculate_batch()           批量计算（全量或指定员工列表）
    """

    @staticmethod
    def _calc_total_allowance(structure: SalaryStructure) -> Decimal:
        """汇总薪酬结构中所有补贴项目合计。

        补贴项包括：夜班补贴 + 高温补贴 + 项目补贴 + 餐补 + 交通补贴 + 住房补贴 + 其他补贴

        Args:
            structure: SalaryStructure ORM 对象

        Returns:
            补贴合计金额（Decimal，保留2位小数）
        """
        return _quantize(
            structure.night_shift_allowance
            + structure.high_temp_allowance
            + structure.project_allowance
            + structure.meal_allowance
            + structure.transport_allowance
            + structure.housing_allowance
            + structure.other_allowance
        )

    @staticmethod
    def _resolve_deduction_policy(raw_config: Optional[dict]) -> dict[str, Decimal]:
        payroll_config = raw_config.get("payroll_deduction", {}) if isinstance(raw_config, dict) else {}

        def to_decimal(key: str, default: str) -> Decimal:
            value = payroll_config.get(key, default)
            try:
                return Decimal(str(value))
            except Exception:
                return Decimal(default)

        return {
            "late_penalty_per_count": to_decimal("late_penalty_per_count", "0"),
            "early_leave_penalty_per_count": to_decimal("early_leave_penalty_per_count", "0"),
            "absent_deduction_multiplier": to_decimal("absent_deduction_multiplier", "1"),
            "unpaid_leave_deduction_multiplier": to_decimal("unpaid_leave_deduction_multiplier", "1"),
            "sick_leave_paid_ratio": to_decimal("sick_leave_paid_ratio", "0.5"),
        }

    @staticmethod
    def _calculate_leave_overlap_days(
        request: LeaveRequest, month_start: date, month_end: date
    ) -> Decimal:
        overlap_start = max(request.start_date, month_start)
        overlap_end = min(request.end_date, month_end)
        if overlap_start > overlap_end:
            return Decimal("0")

        start_half = request.start_half
        end_half = request.end_half
        if overlap_start > request.start_date:
            start_half = HalfDay.am
        if overlap_end < request.end_date:
            end_half = HalfDay.pm

        if overlap_start == overlap_end:
            if start_half == HalfDay.pm and end_half == HalfDay.am:
                return Decimal("1")
            if start_half == end_half:
                return Decimal("0.5")
            return Decimal("1")

        total_days = Decimal(str((overlap_end - overlap_start).days + 1))
        if start_half == HalfDay.pm:
            total_days -= Decimal("0.5")
        if end_half == HalfDay.am:
            total_days -= Decimal("0.5")
        return max(total_days, Decimal("0"))

    @staticmethod
    async def _calc_attendance_deductions(
        db: AsyncSession,
        employee_id: int,
        year: int,
        month: int,
        employee: Optional["Employee"],
        attendance_summary: Optional[AttendanceMonthSummary],
        fixed_salary_base: Decimal,
    ) -> tuple[Decimal, dict[str, Decimal]]:
        from app.services.attendance import AttendanceRecordService

        month_start = date(year, month, 1)
        month_end = date(year, month, monthrange(year, month)[1])
        rule = await AttendanceRecordService._get_rule_for_employee(db, employee_id, month_start)
        policy = PayrollCalculationService._resolve_deduction_policy(
            rule.extra_config if rule else None
        )

        daily_salary = _quantize(fixed_salary_base / Decimal("21.75")) if fixed_salary_base > 0 else Decimal("0")

        late_count = Decimal(str(attendance_summary.late_count or 0)) if attendance_summary else Decimal("0")
        early_leave_count = Decimal(str(attendance_summary.early_leave_count or 0)) if attendance_summary else Decimal("0")
        absent_days = Decimal(str(attendance_summary.absent_count or 0)) if attendance_summary else Decimal("0")

        late_deduction = _quantize(late_count * policy["late_penalty_per_count"])
        early_leave_deduction = _quantize(
            early_leave_count * policy["early_leave_penalty_per_count"]
        )
        absent_deduction = _quantize(
            absent_days * daily_salary * policy["absent_deduction_multiplier"]
        )

        leave_rows_result = await db.execute(
            select(LeaveRequest, LeaveType)
            .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
            .where(
                and_(
                    LeaveRequest.employee_id == employee_id,
                    LeaveRequest.approval_status == LeaveApprovalStatus.approved,
                    LeaveRequest.start_date <= month_end,
                    LeaveRequest.end_date >= month_start,
                )
            )
        )
        unpaid_leave_days = Decimal("0")
        sick_leave_days = Decimal("0")
        for leave_request, leave_type in leave_rows_result.all():
            overlap_days = PayrollCalculationService._calculate_leave_overlap_days(
                leave_request, month_start, month_end
            )
            if overlap_days <= 0:
                continue
            if leave_type.code == "sick":
                sick_leave_days += overlap_days
            elif not leave_type.is_paid:
                unpaid_leave_days += overlap_days

        unpaid_leave_deduction = _quantize(
            unpaid_leave_days * daily_salary * policy["unpaid_leave_deduction_multiplier"]
        )
        sick_leave_deduction = _quantize(
            sick_leave_days * daily_salary * max(Decimal("0"), Decimal("1") - policy["sick_leave_paid_ratio"])
        )

        details = {
            "late_deduction": late_deduction,
            "early_leave_deduction": early_leave_deduction,
            "absent_deduction": absent_deduction,
            "unpaid_leave_deduction": unpaid_leave_deduction,
            "sick_leave_deduction": sick_leave_deduction,
            "unpaid_leave_days": unpaid_leave_days,
            "sick_leave_days": sick_leave_days,
        }
        total = _quantize(
            late_deduction
            + early_leave_deduction
            + absent_deduction
            + unpaid_leave_deduction
            + sick_leave_deduction
        )
        return total, details

    @staticmethod
    async def _calc_social_insurance(
        db: AsyncSession,
        city: str,
        year: int,
        salary_base: Decimal,
        company_id: Optional[int] = None,
    ) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """计算社保（五险）和住房公积金，支持多城市/多公司配置。

        计算逻辑：
            1. 从 SocialInsuranceConfigService 获取该城市/公司的所有险种配置
            2. 对每个险种：缴费基数 = max(min(基本工资, base_max), base_min)
               即基本工资高于上限用上限，低于下限用下限（符合社保征缴规定）
            3. 个人缴费 = 基数 × employee_rate
               公司缴费 = 基数 × company_rate
            4. 公积金（insurance_type='公积金'）单独累计，其余均计入社保

        社保典型费率（以北京2024年为参考）：
            养老：个人8%  + 单位16%
            医疗：个人2%  + 单位6%
            失业：个人0.5% + 单位0.5%
            工伤：个人0%  + 单位0.4%（行业浮动）
            生育：个人0%  + 单位0.8%
            公积金：个人12% + 单位12%

        Args:
            db:          异步数据库会话
            city:        员工社保城市
            year:        计算年份（暂未用于查询，以 is_active 为准）
            salary_base:  基本工资（作为社保缴费基数的参考值）
            company_id:  公司 ID（用于查公司专属社保配置）

        Returns:
            四元组：(社保个人合计, 公积金个人, 社保公司合计, 公积金公司)
            均为 Decimal，保留2位小数

        调用的下游：
            - SocialInsuranceConfigService.list_active_by_city()
        """
        configs = await SocialInsuranceConfigService.list_active_by_city(
            db, city, company_id=company_id
        )
        si_employee = Decimal("0")
        si_company = Decimal("0")
        hf_employee = Decimal("0")
        hf_company = Decimal("0")

        for cfg in configs:
            # 缴纳基数 clamp
            base = max(min(salary_base, cfg.base_max), cfg.base_min)
            emp_amount = _quantize(base * cfg.employee_rate)
            com_amount = _quantize(base * cfg.company_rate)

            if cfg.insurance_type == "公积金":
                hf_employee += emp_amount
                hf_company += com_amount
            else:
                si_employee += emp_amount
                si_company += com_amount

        return (
            _quantize(si_employee),
            _quantize(hf_employee),
            _quantize(si_company),
            _quantize(hf_company),
        )

    @staticmethod
    def _detect_anomalies(
        record: SalaryRecord,
        prev_record: Optional[SalaryRecord],
        threshold_pct: Decimal = Decimal("0.20"),
    ) -> list[str]:
        """检测薪资异常并生成异常标记列表。

        检测项目：
            1. 入离职折算：若 proration_days < 22（大约少于一个月），标记"入离职折算"
            2. 薪资大幅变动：与上月相比变动超过 threshold_pct（默认20%），
               标记"薪资变动XX%"（绝对值变动比例）
            3. 手动调整：adjustment_amount != 0，标记"手动调整X元"

        这些异常标记会被序列化为 JSON 存入 SalaryRecord.anomaly_flags_json，
        供 HR 审核时快速识别问题记录。

        Args:
            record:        当前月的 SalaryRecord（已计算但未持久化）
            prev_record:   上月 SalaryRecord（可为 None，即首月无对比）
            threshold_pct: 薪资变动警戒阈值，默认 0.20（20%）

        Returns:
            异常标记字符串列表（空列表表示无异常）
        """
        flags: list[str] = []

        if record.proration_type and record.proration_days is not None:
            if record.proration_days < 22:  # Rough check for partial month
                flags.append("入离职折算")

        if prev_record:
            if prev_record.gross_salary > 0:
                change_pct = abs(
                    (record.gross_salary - prev_record.gross_salary)
                    / prev_record.gross_salary
                )
                if change_pct > threshold_pct:
                    flags.append(f"薪资变动{_quantize(change_pct * 100)}%")

        if record.adjustment_amount != 0:
            flags.append(f"手动调整{record.adjustment_amount}")

        return flags

    @staticmethod
    async def calculate_single(
        db: AsyncSession,
        employee_id: int,
        year: int,
        month: int,
        city: Optional[str] = None,
        proration_type: str = "calendar_day",
        proration_days: Optional[int] = None,
        special_deduction_total: Optional[Decimal] = None,
        company_id: Optional[int] = None,
    ) -> SalaryRecord:
        """单员工月薪计算主函数——薪资计算引擎的核心入口。

        完整计算流程（9步）：
            1. 读取员工档案，自动推断 city / company_id
            2. 查找适用税法版本（TaxLawVersionService.get_applicable_version）
            3. 自动汇总专项附加扣除（EmployeeTaxDeductionService.get_monthly_total）
            4. 获取生效薪酬结构（SalaryStructureService.get_effective），无则抛 ValueError
            5. 确定本次计算版本号（同月可多次计算，版本号自增）
            6. 计算各薪资分项并应用折算比例（ratio）：
                   ratio = 1 （全月）
                        or proration_days / 当月天数 （日历日折算）
                        or proration_days / 21.75   （工作日折算）
                   gross = (base + position + perf + allowance) × ratio + 加班费
            7. 计算社保/公积金（_calc_social_insurance）
            8. 计算本月个税（TaxCalculationService.calculate_and_save，累计预扣法）
            9. 计算实发、检测异常、持久化 SalaryRecord

        重算处理：同一员工同一年月可多次调用此方法（如修复数据后重算），
        每次生成版本号+1的新 SalaryRecord，不覆盖旧记录。

        加班费说明：当前版本加班费为0（待与考勤模块集成），标注了 TODO。
        加班费公式为：工作日加班 × 1.5 + 休息日加班 × 2.0 + 节假日加班 × 3.0

        Args:
            db:                      异步数据库会话
            employee_id:             员工 ID
            year:                    计算年份
            month:                   计算月份（1-12）
            city:                    社保城市（None则从员工档案读取，默认"北京"）
            proration_type:          折算方式，'calendar_day'=日历日，'workday'=工作日
            proration_days:          实际出勤天数（None 表示全月，不折算）
            special_deduction_total: 专项附加扣除合计（None则自动查询数据库汇总）
            company_id:              公司 ID（None则从员工档案读取）

        Returns:
            计算完成并已 flush 的 SalaryRecord ORM 对象（status='draft'）

        Raises:
            ValueError: 若员工在该月无有效薪酬结构

        调用的下游：
            - SalaryStructureService.get_effective()
            - TaxLawVersionService.get_applicable_version()
            - EmployeeTaxDeductionService.get_monthly_total()
            - PayrollCalculationService._calc_social_insurance()
            - TaxCalculationService.calculate_and_save()
            - PayrollCalculationService._detect_anomalies()
        """
        from datetime import date as date_type
        from app.models.employee import Employee

        ref_date = date_type(year, month, 1)

        # --- 1. 读取员工信息（获取城市/公司） ---
        emp_result = await db.execute(select(Employee).where(Employee.id == employee_id))
        employee = emp_result.scalar_one_or_none()

        effective_city = city
        effective_company_id = company_id
        if employee:
            if not effective_city:
                effective_city = employee.social_insurance_city or "北京"
            if effective_company_id is None:
                effective_company_id = employee.company_id

        if not effective_city:
            effective_city = "北京"

        # --- 2. 解析适用税法版本 ---
        tax_version = await TaxLawVersionService.get_applicable_version(db, ref_date)
        tax_law_version_id: Optional[int] = None
        cumulative_brackets: Optional[list] = None
        monthly_tax_free: Optional[Decimal] = None
        if tax_version:
            tax_law_version_id = tax_version.id
            monthly_tax_free = tax_version.monthly_tax_free
            raw_brackets = await TaxLawVersionService.get_brackets(
                db, tax_version.id, "cumulative"
            )
            if raw_brackets:
                cumulative_brackets = raw_brackets
        tax_version_meta = _parse_tax_version_meta(tax_version.description if tax_version else None)
        enabled_deduction_types = tax_version_meta.get("enabled_deduction_types", [])

        # --- 3. 自动合计员工专项附加扣除 ---
        if special_deduction_total is None:
            special_deduction_total = await EmployeeTaxDeductionService.get_monthly_total(
                db, employee_id, year, month, enabled_types=enabled_deduction_types
            )

        # --- 4. 获取薪酬结构 ---
        structure = await SalaryStructureService.get_effective(db, employee_id, ref_date)
        if not structure:
            raise ValueError(f"员工{employee_id}在{year}-{month}无有效薪酬结构")

        # 获取当前最大版本号
        max_ver_result = await db.execute(
            select(func.coalesce(func.max(SalaryRecord.version), 0)).where(
                and_(
                    SalaryRecord.employee_id == employee_id,
                    SalaryRecord.year == year,
                    SalaryRecord.month == month,
                )
            )
        )
        current_max_version = max_ver_result.scalar() or 0
        new_version = current_max_version + 1

        # 基础计算
        total_allowance = PayrollCalculationService._calc_total_allowance(structure)
        performance_actual = _quantize(
            structure.performance_salary * structure.performance_coefficient
        )

        # 折算
        _, total_days_in_month = monthrange(year, month)
        if proration_days is not None and proration_days < total_days_in_month:
            if proration_type == "calendar_day":
                ratio = Decimal(str(proration_days)) / Decimal(str(total_days_in_month))
            else:  # workday
                # Approximate: assume 21.75 standard workdays
                ratio = Decimal(str(proration_days)) / Decimal("21.75")
            ratio = min(ratio, Decimal("1"))
        else:
            ratio = Decimal("1")
            proration_days = None
            proration_type_val: Optional[str] = None

        if proration_days is not None:
            proration_type_val = proration_type
        else:
            proration_type_val = None

        base = _quantize(structure.base_salary * ratio)
        position = _quantize(structure.position_salary * ratio)
        perf = _quantize(performance_actual * ratio)
        allowance = _quantize(total_allowance * ratio)

        attendance_summary_result = await db.execute(
            select(AttendanceMonthSummary).where(
                and_(
                    AttendanceMonthSummary.employee_id == employee_id,
                    AttendanceMonthSummary.year == year,
                    AttendanceMonthSummary.month == month,
                )
            )
        )
        attendance_summary = attendance_summary_result.scalar_one_or_none()

        overtime_divisor_days = Decimal("21.75")
        if attendance_summary and (attendance_summary.work_days or 0) > 0:
            overtime_divisor_days = Decimal(str(attendance_summary.work_days))

        overtime_hourly_base = Decimal("0")
        if structure.overtime_base and overtime_divisor_days > 0:
            overtime_hourly_base = (
                Decimal(str(structure.overtime_base)) / overtime_divisor_days / Decimal("8")
            )

        overtime_weekday_hours = Decimal("0")
        overtime_weekend_hours = Decimal("0")
        overtime_holiday_hours = Decimal("0")
        if attendance_summary:
            overtime_weekday_hours = Decimal(
                str(attendance_summary.overtime_weekday_hours or Decimal("0"))
            )
            overtime_weekend_hours = Decimal(
                str(attendance_summary.overtime_weekend_hours or Decimal("0"))
            )
            overtime_holiday_hours = Decimal(
                str(attendance_summary.overtime_holiday_hours or Decimal("0"))
            )

        ot_weekday = _quantize(
            overtime_weekday_hours * overtime_hourly_base * Decimal("1.5")
        )
        ot_weekend = _quantize(
            overtime_weekend_hours * overtime_hourly_base * Decimal("2.0")
        )
        ot_holiday = _quantize(
            overtime_holiday_hours * overtime_hourly_base * Decimal("3.0")
        )

        structure_adjustment = _quantize(structure.adjustment_amount or Decimal("0"))
        fixed_salary_base = _quantize(base + position + perf + allowance)
        attendance_deduction_total, attendance_deduction_details = (
            await PayrollCalculationService._calc_attendance_deductions(
                db,
                employee_id,
                year,
                month,
                employee,
                attendance_summary,
                fixed_salary_base,
            )
        )
        total_adjustment = _quantize(structure_adjustment - attendance_deduction_total)
        gross = _quantize(
            fixed_salary_base
            + ot_weekday
            + ot_weekend
            + ot_holiday
            + total_adjustment
        )

        # 社保公积金（按城市+公司查配置，基数口径使用基本工资）
        si_emp, hf_emp, si_com, hf_com = await PayrollCalculationService._calc_social_insurance(
            db, effective_city, year, base, company_id=effective_company_id
        )

        # 个税（传入税法版本ID + 税率表 + 月免征额，实现历史追溯）
        tax_record = await TaxCalculationService.calculate_and_save(
            db,
            employee_id,
            year,
            month,
            gross,
            si_emp,
            hf_emp,
            special_deduction_total,
            tax_law_version_id=tax_law_version_id,
            cumulative_brackets=cumulative_brackets,
            monthly_tax_free=monthly_tax_free,
        )

        net = _quantize(gross - si_emp - hf_emp - tax_record.current_month_tax)
        effective_monthly_tax_free = monthly_tax_free or _DEFAULT_MONTHLY_TAX_FREE
        taxable_income = _quantize(
            gross - si_emp - hf_emp - effective_monthly_tax_free - special_deduction_total
        )
        if taxable_income < 0:
            taxable_income = Decimal("0")

        deduction_reason_parts: list[str] = []
        if structure_adjustment != 0:
            deduction_reason_parts.append(f"结构调整{structure_adjustment}")
        if attendance_deduction_details["late_deduction"] > 0:
            deduction_reason_parts.append(
                f"迟到扣款{attendance_deduction_details['late_deduction']}"
            )
        if attendance_deduction_details["early_leave_deduction"] > 0:
            deduction_reason_parts.append(
                f"早退扣款{attendance_deduction_details['early_leave_deduction']}"
            )
        if attendance_deduction_details["absent_deduction"] > 0:
            deduction_reason_parts.append(
                f"旷工扣款{attendance_deduction_details['absent_deduction']}"
            )
        if attendance_deduction_details["unpaid_leave_deduction"] > 0:
            deduction_reason_parts.append(
                f"事假/无薪假扣款{attendance_deduction_details['unpaid_leave_deduction']}"
            )
        if attendance_deduction_details["sick_leave_deduction"] > 0:
            deduction_reason_parts.append(
                f"病假扣款{attendance_deduction_details['sick_leave_deduction']}"
            )

        # 前月记录(用于异常检测)
        prev_month = month - 1
        prev_year = year
        if prev_month < 1:
            prev_month = 12
            prev_year -= 1
        prev_result = await db.execute(
            select(SalaryRecord)
            .where(
                and_(
                    SalaryRecord.employee_id == employee_id,
                    SalaryRecord.year == prev_year,
                    SalaryRecord.month == prev_month,
                )
            )
            .order_by(SalaryRecord.version.desc())
            .limit(1)
        )
        prev_record = prev_result.scalar_one_or_none()

        record = SalaryRecord(
            employee_id=employee_id,
            year=year,
            month=month,
            version=new_version,
            base_salary=base,
            position_salary=position,
            performance_salary=perf,
            performance_coefficient=structure.performance_coefficient,
            total_allowance=allowance,
            overtime_pay_weekday=ot_weekday,
            overtime_pay_weekend=ot_weekend,
            overtime_pay_holiday=ot_holiday,
            gross_salary=gross,
            social_insurance_employee=si_emp,
            housing_fund_employee=hf_emp,
            social_insurance_company=si_com,
            housing_fund_company=hf_com,
            taxable_income=taxable_income,
            tax_amount=tax_record.current_month_tax,
            special_deduction_total=special_deduction_total,
            net_salary=net,
            adjustment_amount=total_adjustment,
            adjustment_reason="；".join(deduction_reason_parts) if deduction_reason_parts else None,
            proration_type=proration_type_val,
            proration_days=proration_days,
            status="draft",
            snapshot_id=prev_record.id if prev_record else None,
            company_id=effective_company_id,
            tax_law_version_id=tax_law_version_id,
        )

        # Anomaly detection
        anomalies = PayrollCalculationService._detect_anomalies(record, prev_record)
        if attendance_deduction_total > 0:
            anomalies.append("考勤扣薪")
        if anomalies:
            record.anomaly_flags_json = json.dumps(anomalies, ensure_ascii=False)

        db.add(record)
        await db.flush()
        await db.refresh(record)
        return record

    @staticmethod
    async def calculate_batch(
        db: AsyncSession, request: PayrollCalcRequest
    ) -> list[SalaryRecord]:
        """批量计算月度薪资（指定员工列表或全量计算）。

        流程：
            1. 若 request.employee_ids 非空，则只计算指定员工
            2. 否则查询所有存在薪酬结构的员工 ID，逐一计算
            3. 对无薪酬结构的员工（calculate_single 抛 ValueError）静默跳过

        Args:
            db:      异步数据库会话
            request: PayrollCalcRequest Schema（year/month/employee_ids/proration_type）

        Returns:
            成功计算的 SalaryRecord 列表（跳过无薪酬结构的员工）

        调用的下游：
            - PayrollCalculationService.calculate_single()（逐员工调用）
        """
        if request.employee_ids:
            employee_ids = request.employee_ids
        else:
            # 获取所有有薪酬结构的员工
            result = await db.execute(
                select(SalaryStructure.employee_id).distinct()
            )
            employee_ids = [row[0] for row in result.all()]

        records: list[SalaryRecord] = []
        for emp_id in employee_ids:
            try:
                record = await PayrollCalculationService.calculate_single(
                    db,
                    emp_id,
                    request.year,
                    request.month,
                    proration_type=request.proration_type,
                )
                records.append(record)
            except ValueError:
                # Skip employees without salary structure
                continue

        return records


# ============================================================
# SalaryRecord Management
# ============================================================

class SalaryRecordService:
    """薪资记录管理服务——负责薪资批次的审批流转与记录维护。

    薪资记录（SalaryRecord）的生命周期状态流转：
        draft（草稿）
            ↓ submit_for_approval()
        pending_approval（待审批）
            ↓ approve() / batch_approve()
        approved（已审批）
            ↓ mark_paid()
        paid（已发放）

    关键方法：
        - get()                   按 ID 查单条记录
        - list_by_month()         查询某月全量记录（支持按状态过滤）
        - list_by_employee()      查询员工历史薪资
        - adjust()                对草稿/待审批记录做手动调整（修正实发金额）
        - submit_for_approval()   批量提交某月所有草稿记录进入审批
        - approve()               审批单条记录
        - batch_approve()         批量审批某月全部待审批记录
        - mark_paid()             批量标记某月已审批记录为已发放
    """

    @staticmethod
    async def get(db: AsyncSession, record_id: int) -> Optional[SalaryRecord]:
        """按 ID 查询薪资记录。

        Args:
            db:        异步数据库会话
            record_id: 薪资记录主键 ID

        Returns:
            SalaryRecord 对象，不存在则返回 None
        """
        result = await db.execute(
            select(SalaryRecord).where(SalaryRecord.id == record_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_month(
        db: AsyncSession,
        year: int,
        month: int,
        status: Optional[str] = None,
    ) -> list[SalaryRecord]:
        """查询指定年月的所有薪资记录（可按状态过滤），按员工ID和版本号倒序排列。

        Args:
            db:     异步数据库会话
            year:   年份
            month:  月份（1-12）
            status: 状态过滤（'draft'/'pending_approval'/'approved'/'paid'），None则返回全部

        Returns:
            SalaryRecord 列表，同一员工可能有多个版本（取最新版本在前）
        """
        query = select(SalaryRecord).where(
            and_(SalaryRecord.year == year, SalaryRecord.month == month)
        )
        if status:
            query = query.where(SalaryRecord.status == status)
        query = query.order_by(SalaryRecord.employee_id, SalaryRecord.version.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def list_by_employee(
        db: AsyncSession, employee_id: int, year: Optional[int] = None
    ) -> list[SalaryRecord]:
        """查询员工的历史薪资记录（可按年份过滤），按年月倒序排列（最新在前）。

        Args:
            db:          异步数据库会话
            employee_id: 员工 ID
            year:        过滤年份，None 表示返回所有年份

        Returns:
            SalaryRecord 列表
        """
        query = select(SalaryRecord).where(SalaryRecord.employee_id == employee_id)
        if year:
            query = query.where(SalaryRecord.year == year)
        query = query.order_by(SalaryRecord.year.desc(), SalaryRecord.month.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    def _normalize_import_header(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return (
            text.replace("\n", "")
            .replace("\r", "")
            .replace(" ", "")
            .replace("*", "")
            .replace("（", "(")
            .replace("）", ")")
            .replace("：", ":")
            .lower()
        )

    @staticmethod
    def _parse_import_decimal(value: object, default: str = "0") -> Decimal:
        if value is None:
            return Decimal(default)
        if isinstance(value, Decimal):
            return _quantize(value)
        if isinstance(value, (int, float)):
            return _quantize(Decimal(str(value)))
        text = str(value).strip().replace(",", "").replace("，", "")
        if not text:
            return Decimal(default)
        try:
            if text.endswith("%"):
                return _quantize(Decimal(text[:-1]) / Decimal("100"))
            return _quantize(Decimal(text))
        except Exception:
            raise ValueError(f"金额格式不正确: {value}")

    @staticmethod
    def _normalize_import_status(value: object) -> str:
        text = str(value or "").strip().lower()
        mapping = {
            "draft": "draft",
            "草稿": "draft",
            "pending": "pending_approval",
            "pending_approval": "pending_approval",
            "待审批": "pending_approval",
            "approved": "approved",
            "已审批": "approved",
            "paid": "paid",
            "已发放": "paid",
            "已发薪": "paid",
        }
        return mapping.get(text, "draft")

    @staticmethod
    def _load_import_rows(file_bytes: bytes, filename: str) -> list[list[object]]:
        suffix = (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
        is_xls = file_bytes[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        is_xlsx = file_bytes[:2] == b"PK"

        if suffix == "csv" or (not is_xls and not is_xlsx):
            text = None
            for encoding in ("utf-8-sig", "gb18030", "utf-8"):
                try:
                    text = file_bytes.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                raise HTTPException(status_code=400, detail="CSV 文件编码无法识别，请使用 UTF-8 或 GB18030")
            return [list(row) for row in csv.reader(io.StringIO(text))]

        rows: list[list[object]] = []
        if is_xls or suffix == "xls":
            try:
                import xlrd
            except ImportError:
                raise HTTPException(status_code=400, detail="服务器缺少 xlrd 库，无法解析 .xls 文件")
            try:
                book = xlrd.open_workbook(file_contents=file_bytes)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"无法解析 .xls 文件: {exc}")
            sheet = book.sheet_by_index(0)
            for row_idx in range(sheet.nrows):
                row_vals: list[object] = []
                for col_idx in range(sheet.ncols):
                    cell = sheet.cell(row_idx, col_idx)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            value = xlrd.xldate_as_datetime(cell.value, book.datemode)
                        except Exception:
                            value = cell.value
                    elif cell.ctype == xlrd.XL_CELL_FLOAT and cell.value == int(cell.value):
                        value = int(cell.value)
                    else:
                        value = cell.value
                    row_vals.append(value)
                rows.append(row_vals)
            return rows

        from openpyxl import load_workbook

        try:
            workbook = load_workbook(io.BytesIO(file_bytes), data_only=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"无法解析 Excel 文件: {exc}")
        worksheet = workbook.active
        for row in worksheet.iter_rows(values_only=True):
            rows.append(list(row))
        return rows

    @staticmethod
    async def import_salary_records(
        db: AsyncSession,
        file_bytes: bytes,
        filename: str,
        year: int,
        month: int,
    ) -> dict:
        from app.models.employee import Employee

        rows = SalaryRecordService._load_import_rows(file_bytes, filename)
        header_row_idx = next(
            (idx for idx, row in enumerate(rows) if any(str(cell or "").strip() for cell in row)),
            None,
        )
        if header_row_idx is None:
            raise HTTPException(status_code=400, detail="导入文件为空")

        field_map = {
            "工号": "employee_no",
            "员工工号": "employee_no",
            "employee_no": "employee_no",
            "employeeno": "employee_no",
            "基本工资": "base_salary",
            "基础工资": "base_salary",
            "岗位工资": "position_salary",
            "岗位津贴": "position_salary",
            "职位津贴": "position_salary",
            "绩效工资": "performance_salary",
            "绩效系数": "performance_coefficient",
            "补贴": "total_allowance",
            "补贴合计": "total_allowance",
            "津贴合计": "total_allowance",
            "加班费": "overtime_pay",
            "奖金": "bonus_amount",
            "bonus": "bonus_amount",
            "bonus_amount": "bonus_amount",
            "社保": "social_insurance_employee",
            "个人社保": "social_insurance_employee",
            "社保(个人)": "social_insurance_employee",
            "公积金": "housing_fund_employee",
            "个人公积金": "housing_fund_employee",
            "公积金(个人)": "housing_fund_employee",
            "个税": "tax_amount",
            "税额": "tax_amount",
            "应发工资": "gross_salary",
            "应发": "gross_salary",
            "实发工资": "net_salary",
            "实发": "net_salary",
            "状态": "status",
            "调整项": "adjustment_amount",
            "调整金额": "adjustment_amount",
            "调整原因": "adjustment_reason",
            "公司社保": "social_insurance_company",
            "社保(公司)": "social_insurance_company",
            "公司公积金": "housing_fund_company",
            "公积金(公司)": "housing_fund_company",
            "应纳税所得额": "taxable_income",
            "专项附加扣除": "special_deduction_total",
            "专项附加扣除合计": "special_deduction_total",
        }
        header_row = rows[header_row_idx]
        field_names = [
            field_map.get(SalaryRecordService._normalize_import_header(cell))
            for cell in header_row
        ]
        if "employee_no" not in field_names:
            raise HTTPException(status_code=400, detail='导入文件缺少“工号”列')

        parsed_rows: list[tuple[int, dict[str, object]]] = []
        employee_nos: set[str] = set()
        for row_idx in range(header_row_idx + 1, len(rows)):
            row = rows[row_idx]
            payload: dict[str, object] = {}
            for col_idx, field in enumerate(field_names):
                if not field or col_idx >= len(row):
                    continue
                value = row[col_idx]
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                payload[field] = value
            if not payload:
                continue
            employee_no = str(payload.get("employee_no") or "").strip()
            if employee_no:
                employee_nos.add(employee_no)
            parsed_rows.append((row_idx + 1, payload))

        if not parsed_rows:
            raise HTTPException(status_code=400, detail="导入文件没有可用数据行")

        employee_result = await db.execute(
            select(Employee).where(Employee.employee_no.in_(employee_nos))
        )
        employee_map = {
            str(employee.employee_no): employee for employee in employee_result.scalars().all()
        }

        success_count = 0
        errors: list[dict[str, object]] = []

        for display_row, row_data in parsed_rows:
            employee_no = str(row_data.get("employee_no") or "").strip()
            if not employee_no:
                errors.append({"row": display_row, "reason": "工号不能为空"})
                continue

            employee = employee_map.get(employee_no)
            if not employee:
                errors.append({"row": display_row, "reason": f"未找到工号为 {employee_no} 的员工"})
                continue

            try:
                base_salary = SalaryRecordService._parse_import_decimal(row_data.get("base_salary"))
                position_salary = SalaryRecordService._parse_import_decimal(row_data.get("position_salary"))
                performance_salary = SalaryRecordService._parse_import_decimal(row_data.get("performance_salary"))
                performance_coefficient = SalaryRecordService._parse_import_decimal(
                    row_data.get("performance_coefficient"), default="1"
                )
                total_allowance = SalaryRecordService._parse_import_decimal(row_data.get("total_allowance"))
                overtime_pay = SalaryRecordService._parse_import_decimal(row_data.get("overtime_pay"))
                bonus_amount = SalaryRecordService._parse_import_decimal(row_data.get("bonus_amount"))
                social_insurance_employee = SalaryRecordService._parse_import_decimal(
                    row_data.get("social_insurance_employee")
                )
                housing_fund_employee = SalaryRecordService._parse_import_decimal(
                    row_data.get("housing_fund_employee")
                )
                social_insurance_company = SalaryRecordService._parse_import_decimal(
                    row_data.get("social_insurance_company")
                )
                housing_fund_company = SalaryRecordService._parse_import_decimal(
                    row_data.get("housing_fund_company")
                )
                tax_amount = SalaryRecordService._parse_import_decimal(row_data.get("tax_amount"))
                adjustment_amount = SalaryRecordService._parse_import_decimal(row_data.get("adjustment_amount"))
                special_deduction_total = SalaryRecordService._parse_import_decimal(
                    row_data.get("special_deduction_total")
                )
                taxable_income = SalaryRecordService._parse_import_decimal(row_data.get("taxable_income"))
            except ValueError as exc:
                errors.append({"row": display_row, "reason": str(exc)})
                continue

            computed_gross = _quantize(
                base_salary
                + position_salary
                + _quantize(performance_salary * performance_coefficient)
                + total_allowance
                + overtime_pay
                + bonus_amount
                + adjustment_amount
            )
            gross_salary_raw = row_data.get("gross_salary")
            net_salary_raw = row_data.get("net_salary")
            gross_salary = (
                SalaryRecordService._parse_import_decimal(gross_salary_raw)
                if gross_salary_raw not in (None, "")
                else computed_gross
            )
            computed_net = _quantize(
                gross_salary - social_insurance_employee - housing_fund_employee - tax_amount
            )
            net_salary = (
                SalaryRecordService._parse_import_decimal(net_salary_raw)
                if net_salary_raw not in (None, "")
                else computed_net
            )
            if taxable_income == Decimal("0"):
                taxable_income = max(
                    gross_salary
                    - social_insurance_employee
                    - housing_fund_employee
                    - special_deduction_total
                    - Decimal("5000"),
                    Decimal("0"),
                )

            version_result = await db.execute(
                select(func.max(SalaryRecord.version)).where(
                    and_(
                        SalaryRecord.employee_id == employee.id,
                        SalaryRecord.year == year,
                        SalaryRecord.month == month,
                    )
                )
            )
            next_version = (version_result.scalar() or 0) + 1

            import_meta = {
                "import_source": "salary_file",
                "import_file_name": filename,
            }
            if bonus_amount:
                import_meta["bonus_amount"] = float(bonus_amount)

            try:
                async with db.begin_nested():
                    record = SalaryRecord(
                        employee_id=employee.id,
                        year=year,
                        month=month,
                        version=next_version,
                        base_salary=base_salary,
                        position_salary=position_salary,
                        performance_salary=performance_salary,
                        performance_coefficient=performance_coefficient,
                        total_allowance=total_allowance,
                        overtime_pay_weekday=overtime_pay,
                        overtime_pay_weekend=Decimal("0"),
                        overtime_pay_holiday=Decimal("0"),
                        gross_salary=gross_salary,
                        social_insurance_employee=social_insurance_employee,
                        housing_fund_employee=housing_fund_employee,
                        social_insurance_company=social_insurance_company,
                        housing_fund_company=housing_fund_company,
                        taxable_income=_quantize(taxable_income),
                        tax_amount=tax_amount,
                        special_deduction_total=special_deduction_total,
                        net_salary=net_salary,
                        adjustment_amount=adjustment_amount,
                        adjustment_reason=str(row_data.get("adjustment_reason") or "").strip() or None,
                        proration_type=None,
                        proration_days=None,
                        status=SalaryRecordService._normalize_import_status(row_data.get("status")),
                        anomaly_flags_json=json.dumps(import_meta, ensure_ascii=False),
                        snapshot_id=None,
                        company_id=getattr(employee, "company_id", None),
                        tax_law_version_id=None,
                    )
                    db.add(record)
                    await db.flush()
                success_count += 1
            except Exception as exc:
                errors.append({"row": display_row, "reason": f"导入失败: {exc}"})

        return {
            "success_count": success_count,
            "fail_count": len(errors),
            "errors": errors,
        }

    @staticmethod
    async def adjust(
        db: AsyncSession, record_id: int, data: SalaryRecordAdjust
    ) -> Optional[SalaryRecord]:
        """对薪资记录做手动调整（仅允许调整草稿或待审批状态的记录）。

        调整内容：设置 adjustment_amount 和 adjustment_reason，
        并重新计算实发工资：
            net = gross - 社保个人 - 公积金个人 - 个税 + adjustment_amount

        Args:
            db:        异步数据库会话
            record_id: 薪资记录主键 ID
            data:      SalaryRecordAdjust Schema（adjustment_amount, adjustment_reason）

        Returns:
            调整后的 SalaryRecord，不存在则返回 None

        Raises:
            ValueError: 若记录状态不是 'draft' 或 'pending_approval'
        """
        record = await SalaryRecordService.get(db, record_id)
        if not record:
            return None
        if record.status not in ("draft", "pending_approval"):
            raise ValueError("只能调整草稿或待审批状态的薪资记录")

        record.adjustment_amount = data.adjustment_amount
        record.adjustment_reason = data.adjustment_reason
        # 实发 = 应发 - 社保个人 - 公积金个人 - 个税 + 手动调整额
        record.net_salary = _quantize(
            record.gross_salary
            - record.social_insurance_employee
            - record.housing_fund_employee
            - record.tax_amount
            + data.adjustment_amount
        )
        await db.flush()
        await db.refresh(record)
        return record

    @staticmethod
    async def submit_for_approval(
        db: AsyncSession, year: int, month: int
    ) -> list[SalaryRecord]:
        """批量将某月所有草稿状态记录提交审批（状态：draft → pending_approval）。

        Args:
            db:    异步数据库会话
            year:  年份
            month: 月份

        Returns:
            已提交的 SalaryRecord 列表
        """
        records = await SalaryRecordService.list_by_month(db, year, month, status="draft")
        for record in records:
            record.status = "pending_approval"
        await db.flush()
        return records

    @staticmethod
    async def approve(db: AsyncSession, record_id: int) -> Optional[SalaryRecord]:
        """审批单条薪资记录（状态：pending_approval → approved）。

        Args:
            db:        异步数据库会话
            record_id: 薪资记录主键 ID

        Returns:
            审批后的 SalaryRecord，不存在则返回 None

        Raises:
            ValueError: 若记录状态不是 'pending_approval'
        """
        record = await SalaryRecordService.get(db, record_id)
        if not record:
            return None
        if record.status != "pending_approval":
            raise ValueError("只能审批待审批状态的记录")
        record.status = "approved"
        await db.flush()
        await db.refresh(record)
        return record

    @staticmethod
    async def batch_approve(db: AsyncSession, year: int, month: int) -> int:
        """批量审批某月所有待审批记录（状态：pending_approval → approved）。

        Args:
            db:    异步数据库会话
            year:  年份
            month: 月份

        Returns:
            成功审批的记录数量
        """
        records = await SalaryRecordService.list_by_month(
            db, year, month, status="pending_approval"
        )
        count = 0
        for record in records:
            record.status = "approved"
            count += 1
        await db.flush()
        return count

    @staticmethod
    async def mark_paid(db: AsyncSession, year: int, month: int) -> int:
        """批量标记某月所有已审批记录为已发放（状态：approved → paid）。

        通常在银行代发文件上传成功后调用，标志薪资正式发放完成。

        Args:
            db:    异步数据库会话
            year:  年份
            month: 月份

        Returns:
            标记为已发放的记录数量
        """
        records = await SalaryRecordService.list_by_month(
            db, year, month, status="approved"
        )
        count = 0
        for record in records:
            record.status = "paid"
            count += 1
        await db.flush()
        return count

    @staticmethod
    async def export_salary_records_excel(
        db: AsyncSession,
        year: int,
        month: int,
        department_id: Optional[int] = None,
    ) -> bytes:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from sqlalchemy.orm import selectinload

        from app.models.employee import Employee

        query = (
            select(SalaryRecord)
            .options(selectinload(SalaryRecord.employee).selectinload(Employee.department))
            .where(and_(SalaryRecord.year == year, SalaryRecord.month == month))
            .order_by(SalaryRecord.employee_id, SalaryRecord.version.desc())
        )
        if department_id:
            query = query.join(Employee, SalaryRecord.employee_id == Employee.id).where(
                Employee.department_id == department_id
            )

        result = await db.execute(query)
        records = list(result.scalars().all())

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"{year}年{month}月薪资"

        headers = [
            "序号", "姓名", "工号", "部门", "年度", "月份", "基本工资", "岗位工资", "绩效工资",
            "绩效系数", "补贴合计", "加班费", "应发工资", "社保(个人)", "公积金(个人)", "个税",
            "调整金额", "实发工资", "状态", "异常标记",
        ]
        header_fill = PatternFill(start_color="0F766E", end_color="0F766E", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        status_map = {
            "draft": "草稿",
            "pending_approval": "待审批",
            "approved": "已审批",
            "paid": "已发放",
        }

        for idx, record in enumerate(records, 1):
            employee = record.employee
            overtime_total = (
                (record.overtime_pay_weekday or Decimal("0"))
                + (record.overtime_pay_weekend or Decimal("0"))
                + (record.overtime_pay_holiday or Decimal("0"))
            )
            ws.append([
                idx,
                employee.name if employee else "",
                employee.employee_no if employee else "",
                getattr(getattr(employee, "department", None), "name", "") if employee else "",
                record.year,
                record.month,
                float(record.base_salary or 0),
                float(record.position_salary or 0),
                float(record.performance_salary or 0),
                float(record.performance_coefficient or 0),
                float(record.total_allowance or 0),
                float(overtime_total),
                float(record.gross_salary or 0),
                float(record.social_insurance_employee or 0),
                float(record.housing_fund_employee or 0),
                float(record.tax_amount or 0),
                float(record.adjustment_amount or 0),
                float(record.net_salary or 0),
                status_map.get(record.status, record.status),
                record.anomaly_flags_json or "",
            ])

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()


# ============================================================
# PaySlip Management 工资条
# ============================================================

class PaySlipService:
    """工资条管理服务——工资条发送、员工查看与疑问处理。

    工资条（PaySlip）是 SalaryRecord 的员工可见视图，
    每条 approved 薪资记录对应一张工资条（一对一关系）。

    工资条的员工查询状态机：
        （未提问）
            ↓ submit_query()
        queried（已提问）
            ↓ reply_query()
        replied（已回复）
            ↓ confirm_query()    or   escalate_query()
        confirmed（已确认）           escalated（已升级）

    关键方法：
        - send_batch()           批量生成工资条（对 approved 记录创建 PaySlip）
        - get()                  按 ID 查询工资条
        - get_by_employee_month() 查员工某月工资条
        - mark_viewed()          标记员工已查看（记录 viewed_at 时间戳）
        - submit_query()         员工提交工资疑问
        - reply_query()          HR 回复员工疑问
        - confirm_query()        员工确认 HR 回复
        - escalate_query()       员工对 HR 回复不满意，升级处理
        - list_by_employee()     查员工所有工资条历史
    """

    @staticmethod
    async def send_batch(
        db: AsyncSession, request: PaySlipSendRequest
    ) -> list[PaySlip]:
        """批量生成工资条（仅对 approved 状态的薪资记录创建，已存在则跳过）。

        业务规则：
            - 只为 status='approved' 的 SalaryRecord 创建 PaySlip
            - 若某记录已有 PaySlip（salary_record_id 已存在），则跳过（幂等操作）
            - 若 request.employee_ids 非空，则只处理指定员工

        Args:
            db:      异步数据库会话
            request: PaySlipSendRequest Schema（year/month/employee_ids）

        Returns:
            本次新创建的 PaySlip 列表（已存在的不在其中）
        """
        query = select(SalaryRecord).where(
            and_(
                SalaryRecord.year == request.year,
                SalaryRecord.month == request.month,
                SalaryRecord.status.in_(("approved", "paid")),
            )
        )
        if request.employee_ids:
            query = query.where(SalaryRecord.employee_id.in_(request.employee_ids))

        result = await db.execute(query)
        records = list(result.scalars().all())

        slips: list[PaySlip] = []
        now = datetime.now(timezone.utc)
        for record in records:
            # Check if slip already exists
            existing = await db.execute(
                select(PaySlip).where(PaySlip.salary_record_id == record.id)
            )
            slip = existing.scalar_one_or_none()
            if slip:
                slip.sent_at = now
                slips.append(slip)
                continue

            slip = PaySlip(
                salary_record_id=record.id,
                employee_id=record.employee_id,
                year=record.year,
                month=record.month,
                sent_at=now,
            )
            db.add(slip)
            slips.append(slip)

        await db.flush()
        for slip in slips:
            await db.refresh(slip)
        return slips

    @staticmethod
    async def list_admin(
        db: AsyncSession,
        year: int,
        month: int,
        department_id: Optional[int] = None,
        send_status: Optional[str] = None,
    ) -> list[dict]:
        from app.models.employee import Employee
        from sqlalchemy.orm import selectinload

        result = await db.execute(
            select(SalaryRecord)
            .options(
                selectinload(SalaryRecord.employee).selectinload(Employee.department),
            )
            .where(and_(SalaryRecord.year == year, SalaryRecord.month == month))
            .order_by(SalaryRecord.employee_id, SalaryRecord.version.desc())
        )
        records = list(result.scalars().all())

        latest_by_employee: dict[int, SalaryRecord] = {}
        for record in records:
            latest_by_employee.setdefault(record.employee_id, record)

        salary_record_ids = [record.id for record in latest_by_employee.values()]
        slip_map: dict[int, PaySlip] = {}
        if salary_record_ids:
            slip_result = await db.execute(
                select(PaySlip).where(PaySlip.salary_record_id.in_(salary_record_ids))
            )
            slip_map = {
                slip.salary_record_id: slip for slip in slip_result.scalars().all()
            }

        rows: list[dict] = []
        for record in latest_by_employee.values():
            employee = record.employee
            if department_id and getattr(employee, "department_id", None) != department_id:
                continue

            slip = slip_map.get(record.id)
            if slip and slip.sent_at:
                current_send_status = "sent"
                current_view_status = "viewed" if slip.viewed_at else "unviewed"
            elif record.status in ("approved", "paid"):
                current_send_status = "pending"
                current_view_status = "pending"
            else:
                current_send_status = "unsent"
                current_view_status = "pending"

            if send_status and current_send_status != send_status:
                continue

            overtime_total = (
                (record.overtime_pay_weekday or Decimal("0"))
                + (record.overtime_pay_weekend or Decimal("0"))
                + (record.overtime_pay_holiday or Decimal("0"))
            )
            rows.append(
                {
                    "id": slip.id if slip else None,
                    "salary_record_id": record.id,
                    "employee_id": record.employee_id,
                    "employee_no": employee.employee_no if employee else "",
                    "employee_name": employee.name if employee else "",
                    "department_name": getattr(getattr(employee, "department", None), "name", "") if employee else "",
                    "year": record.year,
                    "month": record.month,
                    "gross_salary": float(record.gross_salary or 0),
                    "net_salary": float(record.net_salary or 0),
                    "base_salary": float(record.base_salary or 0),
                    "position_salary": float(record.position_salary or 0),
                    "performance_salary": float(record.performance_salary or 0),
                    "performance_coefficient": float(record.performance_coefficient or 0),
                    "total_allowance": float(record.total_allowance or 0),
                    "overtime_pay": float(overtime_total),
                    "social_insurance_employee": float(record.social_insurance_employee or 0),
                    "housing_fund_employee": float(record.housing_fund_employee or 0),
                    "tax_amount": float(record.tax_amount or 0),
                    "send_status": current_send_status,
                    "view_status": current_view_status,
                    "sent_at": slip.sent_at if slip else None,
                    "viewed_at": slip.viewed_at if slip else None,
                    "query_status": slip.query_status if slip else "none",
                    "query_content": slip.query_content if slip else None,
                    "reply_content": slip.reply_content if slip else None,
                    "reply_deadline": slip.reply_deadline if slip else None,
                    "query_attachment_url": slip.query_attachment_url if slip else None,
                    "reply_attachment_url": slip.reply_attachment_url if slip else None,
                    "record_status": record.status,
                }
            )

        rows.sort(key=lambda item: str(item.get("employee_no") or ""))
        return rows

    @staticmethod
    async def get(db: AsyncSession, slip_id: int) -> Optional[PaySlip]:
        """按 ID 查询工资条。

        Args:
            db:      异步数据库会话
            slip_id: 工资条主键 ID

        Returns:
            PaySlip 对象，不存在则返回 None
        """
        result = await db.execute(select(PaySlip).where(PaySlip.id == slip_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_employee_month(
        db: AsyncSession, employee_id: int, year: int, month: int
    ) -> Optional[PaySlip]:
        """查询员工某年月的工资条（一员工一月最多一张）。

        Args:
            db:          异步数据库会话
            employee_id: 员工 ID
            year:        年份
            month:       月份

        Returns:
            PaySlip 对象，不存在则返回 None
        """
        result = await db.execute(
            select(PaySlip).where(
                and_(
                    PaySlip.employee_id == employee_id,
                    PaySlip.year == year,
                    PaySlip.month == month,
                )
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def mark_viewed(db: AsyncSession, slip_id: int) -> Optional[PaySlip]:
        """标记工资条已被员工查看（仅在首次查看时记录时间戳）。

        幂等操作：若 viewed_at 已有值则不更新（保留首次查看时间）。

        Args:
            db:      异步数据库会话
            slip_id: 工资条主键 ID

        Returns:
            更新后的 PaySlip，不存在则返回 None
        """
        slip = await PaySlipService.get(db, slip_id)
        if not slip:
            return None
        if not slip.viewed_at:
            slip.viewed_at = datetime.now(timezone.utc)
            await db.flush()
            await db.refresh(slip)
        return slip

    @staticmethod
    async def submit_query(
        db: AsyncSession, slip_id: int, data: PaySlipQuerySubmit
    ) -> Optional[PaySlip]:
        """员工提交工资疑问（query_status: None → queried）。

        提交后自动设置回复截止时间（3个自然日后），HR 须在此前回复。

        Args:
            db:      异步数据库会话
            slip_id: 工资条主键 ID
            data:    PaySlipQuerySubmit Schema（query_content：疑问内容）

        Returns:
            更新后的 PaySlip，不存在则返回 None
        """
        slip = await PaySlipService.get(db, slip_id)
        if not slip:
            return None
        slip.query_status = "queried"
        slip.query_content = data.query_content
        slip.query_attachment_url = data.query_attachment_url
        # HR has 3 business days to reply
        from datetime import timedelta
        slip.reply_deadline = datetime.now(timezone.utc) + timedelta(days=3)
        await db.flush()
        await db.refresh(slip)
        return slip

    @staticmethod
    async def reply_query(
        db: AsyncSession, slip_id: int, data: PaySlipReply
    ) -> Optional[PaySlip]:
        """HR 回复员工工资疑问（query_status: queried → replied）。

        Args:
            db:      异步数据库会话
            slip_id: 工资条主键 ID
            data:    PaySlipReply Schema（reply_content：回复内容）

        Returns:
            更新后的 PaySlip，不存在则返回 None

        Raises:
            ValueError: 若当前状态不是 'queried'（没有待回复的疑问）
        """
        slip = await PaySlipService.get(db, slip_id)
        if not slip:
            return None
        if slip.query_status != "queried":
            raise ValueError("该工资条没有待回复的疑问")
        slip.query_status = "replied"
        slip.reply_content = data.reply_content
        slip.reply_attachment_url = data.reply_attachment_url
        await db.flush()
        await db.refresh(slip)
        return slip

    @staticmethod
    async def confirm_query(db: AsyncSession, slip_id: int) -> Optional[PaySlip]:
        """员工确认 HR 回复（query_status: replied → confirmed）。

        表示员工对 HR 的解释满意，疑问流程正常关闭。

        Args:
            db:      异步数据库会话
            slip_id: 工资条主键 ID

        Returns:
            更新后的 PaySlip，不存在则返回 None

        Raises:
            ValueError: 若当前状态不是 'replied'
        """
        slip = await PaySlipService.get(db, slip_id)
        if not slip:
            return None
        if slip.query_status != "replied":
            raise ValueError("只能确认已回复的工资条")
        slip.query_status = "confirmed"
        await db.flush()
        await db.refresh(slip)
        return slip

    @staticmethod
    async def escalate_query(db: AsyncSession, slip_id: int) -> Optional[PaySlip]:
        """员工升级疑问（query_status: queried/replied → escalated）。

        适用场景：员工对 HR 回复不满意，或 HR 超时未回复，需上级介入处理。
        允许从 'queried'（HR 未回复）或 'replied'（员工不接受）两种状态触发。

        Args:
            db:      异步数据库会话
            slip_id: 工资条主键 ID

        Returns:
            更新后的 PaySlip，不存在则返回 None

        Raises:
            ValueError: 若当前状态不是 'queried' 或 'replied'
        """
        slip = await PaySlipService.get(db, slip_id)
        if not slip:
            return None
        if slip.query_status not in ("queried", "replied"):
            raise ValueError("只能升级已提问或已回复的工资条")
        slip.query_status = "escalated"
        await db.flush()
        await db.refresh(slip)
        return slip

    @staticmethod
    async def list_by_employee(
        db: AsyncSession, employee_id: int, year: Optional[int] = None
    ) -> list[PaySlip]:
        """查询员工的所有工资条（可按年份过滤），按年月倒序排列。

        Args:
            db:          异步数据库会话
            employee_id: 员工 ID
            year:        过滤年份，None 表示返回所有年份

        Returns:
            PaySlip 列表
        """
        query = select(PaySlip).where(PaySlip.employee_id == employee_id)
        if year:
            query = query.where(PaySlip.year == year)
        query = query.order_by(PaySlip.year.desc(), PaySlip.month.desc())
        result = await db.execute(query)
        return list(result.scalars().all())


# ============================================================
# TaxRecord Query
# ============================================================

class TaxRecordService:
    """个税记录查询服务（只读）。

    TaxRecord 由 TaxCalculationService.calculate_and_save() 自动创建，
    本服务仅提供查询接口，不执行写操作。

    每条 TaxRecord 存储：
        - 本月应发工资（gross）
        - 年初至本月的累计收入/免税额/专项扣除/专项附加扣除/应纳税所得额
        - 本年累计已缴税（cumulative_tax）
        - 本月实际预扣税额（current_month_tax）
        - 使用的税法版本（tax_law_version_id）
    """

    @staticmethod
    async def list_by_employee_year(
        db: AsyncSession, employee_id: int, year: int
    ) -> list[TaxRecord]:
        """查询员工某年度所有月份的个税记录（按月份升序排列）。

        Args:
            db:          异步数据库会话
            employee_id: 员工 ID
            year:        年份

        Returns:
            TaxRecord 列表（1-12月，可能不足12条）
        """
        result = await db.execute(
            select(TaxRecord)
            .where(
                and_(
                    TaxRecord.employee_id == employee_id,
                    TaxRecord.year == year,
                )
            )
            .order_by(TaxRecord.month)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_by_month(
        db: AsyncSession, year: int, month: int
    ) -> list[TaxRecord]:
        """查询某年月所有员工的个税记录，按员工ID升序排列。

        Args:
            db:    异步数据库会话
            year:  年份
            month: 月份

        Returns:
            TaxRecord 列表
        """
        result = await db.execute(
            select(TaxRecord)
            .where(
                and_(TaxRecord.year == year, TaxRecord.month == month)
            )
            .order_by(TaxRecord.employee_id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def export_tax_records_excel(
        db: AsyncSession,
        year: int,
        month: int,
    ) -> bytes:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from sqlalchemy.orm import selectinload

        from app.models.employee import Employee

        result = await db.execute(
            select(TaxRecord)
            .options(selectinload(TaxRecord.employee).selectinload(Employee.department))
            .where(and_(TaxRecord.year == year, TaxRecord.month == month))
            .order_by(TaxRecord.employee_id)
        )
        records = list(result.scalars().all())

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"{year}年{month}月个税"

        headers = [
            "序号", "姓名", "工号", "部门", "年度", "月份", "累计收入", "累计免税额",
            "累计扣除", "累计专项附加扣除", "累计应纳税所得额", "累计应纳税额", "本月预扣税额", "计税方式",
        ]
        header_fill = PatternFill(start_color="7C3AED", end_color="7C3AED", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        tax_method_map = {
            "cumulative": "累计预扣法",
            "separate_bonus": "全年一次性奖金",
        }

        for idx, record in enumerate(records, 1):
            employee = record.employee
            ws.append([
                idx,
                employee.name if employee else "",
                employee.employee_no if employee else "",
                getattr(getattr(employee, "department", None), "name", "") if employee else "",
                record.year,
                record.month,
                float(record.cumulative_income or 0),
                float(record.cumulative_tax_free or 0),
                float(record.cumulative_deductions or 0),
                float(record.cumulative_special_deductions or 0),
                float(record.cumulative_taxable or 0),
                float(record.cumulative_tax or 0),
                float(record.current_month_tax or 0),
                tax_method_map.get(record.tax_method, record.tax_method),
            ])

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    @staticmethod
    async def export_tax_filing_template_excel(
        db: AsyncSession,
        year: int,
        month: int,
    ) -> bytes:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from sqlalchemy.orm import selectinload

        from app.models.employee import Employee

        result = await db.execute(
            select(SalaryRecord)
            .options(selectinload(SalaryRecord.employee).selectinload(Employee.department))
            .where(and_(SalaryRecord.year == year, SalaryRecord.month == month))
            .order_by(SalaryRecord.employee_id, SalaryRecord.version.desc())
        )
        records = list(result.scalars().all())
        latest_by_employee: dict[int, SalaryRecord] = {}
        for record in records:
            latest_by_employee.setdefault(record.employee_id, record)

        tax_result = await db.execute(
            select(TaxRecord).where(and_(TaxRecord.year == year, TaxRecord.month == month))
        )
        tax_map = {item.employee_id: item for item in tax_result.scalars().all()}

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"{year}年{month}月申报模板"

        headers = ["姓名", "证件号码", "所得期间", "收入额", "免税收入", "基本减除费用", "专项扣除", "专项附加扣除", "应纳税所得额", "税率", "应纳税额", "已预缴税额"]
        header_fill = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        period = f"{year}-{month:02d}"
        for record in latest_by_employee.values():
            employee = record.employee
            tax_record = tax_map.get(record.employee_id)
            special_deduction = (record.social_insurance_employee or Decimal("0")) + (
                record.housing_fund_employee or Decimal("0")
            )
            taxable_income = tax_record.cumulative_taxable if tax_record else record.taxable_income
            paid_tax = (
                (tax_record.cumulative_tax or Decimal("0")) - (tax_record.current_month_tax or Decimal("0"))
                if tax_record
                else Decimal("0")
            )
            rate_text = "-"
            for upper, rate, _quick in _DEFAULT_CUMULATIVE_BRACKETS:
                if taxable_income <= upper:
                    rate_text = f"{(rate * Decimal('100')).quantize(Decimal('1'))}%"
                    break
            ws.append([
                employee.name if employee else "",
                getattr(employee, "id_card_no", "") if employee else "",
                period,
                float(record.gross_salary or 0),
                0,
                5000,
                float(special_deduction),
                float(record.special_deduction_total or 0),
                float(taxable_income or 0),
                rate_text,
                float(record.tax_amount or 0),
                float(paid_tax),
            ])

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    @staticmethod
    async def export_special_deduction_summary_excel(
        db: AsyncSession,
        year: int,
        month: int,
    ) -> bytes:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from sqlalchemy.orm import selectinload

        from app.models.employee import Employee

        period_value = year * 100 + month
        result = await db.execute(
            select(EmployeeTaxDeduction)
            .options(selectinload(EmployeeTaxDeduction.employee).selectinload(Employee.department))
            .where(
                and_(
                    EmployeeTaxDeduction.start_year * 100 + EmployeeTaxDeduction.start_month <= period_value,
                    or_(
                        EmployeeTaxDeduction.end_year.is_(None),
                        EmployeeTaxDeduction.end_month.is_(None),
                        EmployeeTaxDeduction.end_year * 100 + EmployeeTaxDeduction.end_month >= period_value,
                    ),
                )
            )
            .order_by(EmployeeTaxDeduction.employee_id, EmployeeTaxDeduction.deduction_type)
        )
        records = list(result.scalars().all())

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"{year}年{month}月专项附加扣除"

        headers = ["姓名", "工号", "部门", "扣除类型", "月扣除额", "生效起始", "生效结束", "备注"]
        header_fill = PatternFill(start_color="0F766E", end_color="0F766E", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        for record in records:
            employee = record.employee
            ws.append([
                employee.name if employee else "",
                employee.employee_no if employee else "",
                getattr(getattr(employee, "department", None), "name", "") if employee else "",
                record.deduction_type,
                float(record.monthly_amount or 0),
                f"{record.start_year}-{record.start_month:02d}",
                f"{record.end_year}-{record.end_month:02d}" if record.end_year and record.end_month else "持续有效",
                record.description or "",
            ])

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()


class TaxImportService:
    """税款表上传归档服务。"""

    @staticmethod
    def _normalize_cell_value(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, float):
            return int(value) if value.is_integer() else value
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value

    @staticmethod
    def _load_xls_rows(file_bytes: bytes) -> tuple[str | None, list[str], list[dict[str, Any]]]:
        import xlrd

        book = xlrd.open_workbook(file_contents=file_bytes)
        sheet = book.sheet_by_index(0)
        headers = [str(v).strip() for v in sheet.row_values(0)]
        rows: list[dict[str, Any]] = []
        for row_idx in range(1, sheet.nrows):
            values = []
            for col_idx in range(sheet.ncols):
                cell = sheet.cell(row_idx, col_idx)
                cell_value: Any = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    cell_value = xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)
                values.append(TaxImportService._normalize_cell_value(cell_value))
            if not any(value not in (None, "") for value in values):
                continue
            rows.append({headers[col_idx]: values[col_idx] for col_idx in range(len(headers)) if headers[col_idx]})
        return sheet.name, headers, rows

    @staticmethod
    def _load_xlsx_rows(file_bytes: bytes) -> tuple[str | None, list[str], list[dict[str, Any]]]:
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(file_bytes), data_only=True)
        sheet = workbook[workbook.sheetnames[0]]
        header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), [])
        headers = [str(v).strip() if v is not None else "" for v in header_row]
        rows: list[dict[str, Any]] = []
        for row in sheet.iter_rows(min_row=2, values_only=True):
            values = [TaxImportService._normalize_cell_value(value) for value in row[: len(headers)]]
            if not any(value not in (None, "") for value in values):
                continue
            rows.append({headers[idx]: values[idx] for idx in range(len(headers)) if headers[idx]})
        return sheet.title, headers, rows

    @staticmethod
    def parse_tax_import_file(filename: str, file_bytes: bytes) -> tuple[str | None, list[str], list[dict[str, Any]]]:
        lower_name = (filename or "").lower()
        if lower_name.endswith(".xlsx"):
            sheet_name, headers, rows = TaxImportService._load_xlsx_rows(file_bytes)
        elif lower_name.endswith(".xls"):
            sheet_name, headers, rows = TaxImportService._load_xls_rows(file_bytes)
        else:
            raise HTTPException(status_code=400, detail="仅支持上传 .xls 或 .xlsx 文件")
        headers = [header for header in headers if header]
        if not headers:
            raise HTTPException(status_code=400, detail="税款表缺少表头")
        if not rows:
            raise HTTPException(status_code=400, detail="税款表没有可导入的数据行")
        return sheet_name, headers, rows

    @staticmethod
    def serialize_batch(batch: TaxImportBatch) -> dict[str, Any]:
        return {
            "id": batch.id,
            "company_id": batch.company_id,
            "company_name": batch.company.name if batch.company else "",
            "year": batch.year,
            "month": batch.month,
            "sheet_name": batch.sheet_name,
            "source_filename": batch.source_filename,
            "headers": list(batch.headers_json or []),
            "row_count": batch.row_count,
            "uploaded_by": batch.uploaded_by,
            "uploaded_by_name": batch.uploader.name if batch.uploader else None,
            "uploaded_at": batch.uploaded_at,
            "rows": [row.row_data for row in sorted(batch.rows or [], key=lambda item: item.row_no)],
        }

    @staticmethod
    async def get_by_company_month(
        db: AsyncSession,
        company_id: int,
        year: int,
        month: int,
    ) -> Optional[TaxImportBatch]:
        result = await db.execute(
            select(TaxImportBatch).where(
                TaxImportBatch.company_id == company_id,
                TaxImportBatch.year == year,
                TaxImportBatch.month == month,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upload(
        db: AsyncSession,
        *,
        company_id: int,
        year: int,
        month: int,
        filename: str,
        file_bytes: bytes,
        current_user: "Employee",  # noqa: F821
    ) -> TaxImportBatch:
        company = await CompanyService.get(db, company_id)
        if not company:
            raise HTTPException(status_code=404, detail="公司主体不存在")

        sheet_name, headers, rows = TaxImportService.parse_tax_import_file(filename, file_bytes)
        existing = await TaxImportService.get_by_company_month(db, company_id, year, month)
        if existing:
            await db.execute(delete(TaxImportRow).where(TaxImportRow.batch_id == existing.id))
            batch = existing
            batch.sheet_name = sheet_name
            batch.source_filename = filename
            batch.headers_json = headers
            batch.row_count = len(rows)
            batch.uploaded_by = current_user.id
        else:
            batch = TaxImportBatch(
                company_id=company_id,
                year=year,
                month=month,
                sheet_name=sheet_name,
                source_filename=filename,
                headers_json=headers,
                row_count=len(rows),
                uploaded_by=current_user.id,
            )
            db.add(batch)
            await db.flush()

        for index, row in enumerate(rows, start=1):
            db.add(TaxImportRow(batch_id=batch.id, row_no=index, row_data=row))

        await db.flush()
        latest = await TaxImportService.get_by_company_month(db, company_id, year, month)
        if not latest:
            raise HTTPException(status_code=500, detail="税款表上传后读取失败")
        return latest


# ============================================================
# Dashboard 薪资成本看板
# ============================================================

class PayrollDashboardService:
    """薪资成本看板服务——提供管理层所需的汇总数据。

    提供两类统计数据：
        1. 月度汇总（get_monthly_summary）：某月的应发/实发/税额/社保/公积金/人头数/总用人成本
        2. 年度趋势（get_cost_trend）：全年各月汇总数据列表（用于趋势折线图）

    用人成本（total_cost）= 应发工资 + 公司社保 + 公司公积金
    （即公司实际支出，包含雇主社保负担）
    """

    @staticmethod
    async def get_monthly_summary(
        db: AsyncSession, year: int, month: int
    ) -> SalaryCostSummary:
        """获取月度薪资成本汇总（仅统计 approved 和 paid 状态的记录）。

        汇总指标：
            - total_gross:  应发工资合计
            - total_net:    实发工资合计
            - total_tax:    个税合计
            - total_social_insurance_company: 公司社保合计
            - total_housing_fund_company:     公司公积金合计
            - total_cost:   总用人成本 = total_gross + 公司社保 + 公司公积金
            - headcount:    发薪人数

        Args:
            db:    异步数据库会话
            year:  年份
            month: 月份

        Returns:
            SalaryCostSummary Pydantic 模型
        """
        result = await db.execute(
            select(
                func.sum(SalaryRecord.gross_salary),
                func.sum(SalaryRecord.net_salary),
                func.sum(SalaryRecord.tax_amount),
                func.sum(SalaryRecord.social_insurance_company),
                func.sum(SalaryRecord.housing_fund_company),
                func.count(SalaryRecord.id),
            ).where(
                and_(
                    SalaryRecord.year == year,
                    SalaryRecord.month == month,
                    SalaryRecord.status.in_(["approved", "paid"]),
                )
            )
        )
        row = result.one()
        total_gross = row[0] or Decimal("0")
        total_net = row[1] or Decimal("0")
        total_tax = row[2] or Decimal("0")
        total_si = row[3] or Decimal("0")
        total_hf = row[4] or Decimal("0")
        headcount = row[5] or 0

        return SalaryCostSummary(
            year=year,
            month=month,
            total_gross=total_gross,
            total_net=total_net,
            total_tax=total_tax,
            total_social_insurance_company=total_si,
            total_housing_fund_company=total_hf,
            total_cost=_quantize(total_gross + total_si + total_hf),
            headcount=headcount,
        )

    @staticmethod
    async def get_cost_trend(
        db: AsyncSession, year: int, months: int = 12
    ) -> list[SalaryCostSummary]:
        """获取全年薪资成本趋势（逐月调用 get_monthly_summary 汇总）。

        Args:
            db:     异步数据库会话
            year:   年份
            months: 返回的月份数，默认12（全年）

        Returns:
            SalaryCostSummary 列表，从1月到第 months 月
        """
        summaries: list[SalaryCostSummary] = []
        for m in range(1, months + 1):
            summary = await PayrollDashboardService.get_monthly_summary(db, year, m)
            summaries.append(summary)
        return summaries


# ============================================================
# SocialInsuranceRecord 月度社保数据
# ============================================================

class SocialInsuranceRecordService:
    """月度社保明细记录管理服务。

    SocialInsuranceRecord 存储每名员工每个月各险种的缴费基数和金额明细，
    用于：
        - 向社保局申报数据
        - 生成社保缴费清单
        - 企业成本核算（分险种追踪）

    每条记录包含：养老/医疗/失业（个人+单位）、工伤/生育（仅单位）、
    公积金（个人+单位）以及各自的缴费基数。

    关键方法：
        - generate()       批量生成/更新指定年月所有在职员工的社保明细（upsert）
        - list_records()   分页查询社保明细（支持部门/员工筛选）
    """

    @staticmethod
    async def generate(
        db: AsyncSession, req: GenerateSocialInsuranceRequest
    ) -> dict:
        """生成（或更新）指定年月的所有在职员工社保明细记录。

        流程：
            1. 查询所有 status in ('在职', '试用期', '待入职') 的员工
            2. 对每名员工：
                a. 确定社保城市和公司
                b. 获取该城市当前启用的社保配置（list_active_by_city）
                c. 从当月薪资记录（SalaryRecord 版本最高）取应发工资作为基数参考
                d. 按险种计算缴费基数（clamp 到 base_min~base_max）和金额
                e. Upsert：已存在则更新字段，不存在则新建
            3. 若某员工无社保配置则跳过（skipped 计数）

        缴费基数 clamp 规则：base = max(min(gross, base_max), base_min)
        养老险基数（si_base）取自养老险的 base 值，公积金基数（hf_base）取自公积金 base 值。

        Args:
            db:  异步数据库会话
            req: GenerateSocialInsuranceRequest Schema（year, month）

        Returns:
            字典 {'generated': int, 'skipped': int, 'year': int, 'month': int}

        调用的下游：
            - SocialInsuranceConfigService.list_active_by_city()
        """
        from app.models.employee import Employee as EmpModel
        from sqlalchemy.orm import selectinload

        year, month = req.year, req.month

        # 查询所有在职员工（含试用期）
        emp_result = await db.execute(
            select(EmpModel)
            .where(EmpModel.status.in_(["在职", "试用期", "待入职"]))
            .options(selectinload(EmpModel.department))
        )
        employees = emp_result.scalars().all()

        generated = 0
        skipped = 0

        for emp in employees:
            city: str = getattr(emp, "social_insurance_city", None) or "北京"
            company_id: Optional[int] = getattr(emp, "company_id", None)

            # 获取当前城市启用的社保配置
            configs = await SocialInsuranceConfigService.list_active_by_city(
                db, city, company_id=company_id
            )
            if not configs:
                skipped += 1
                continue

            # 从当月工资记录中取社保基数口径：优先基本工资，其次员工档案社保基数，最后回退0
            salary_result = await db.execute(
                select(SalaryRecord)
                .where(
                    and_(
                        SalaryRecord.employee_id == emp.id,
                        SalaryRecord.year == year,
                        SalaryRecord.month == month,
                    )
                )
                .order_by(SalaryRecord.version.desc())
                .limit(1)
            )
            salary = salary_result.scalar_one_or_none()
            salary_base = Decimal(str(getattr(emp, "social_insurance_base", None) or 0))
            if salary and getattr(salary, "base_salary", None) is not None:
                salary_base = salary.base_salary
            elif salary_base <= 0 and salary and getattr(salary, "gross_salary", None) is not None:
                salary_base = salary.gross_salary

            # 按险种计算金额
            pension_emp = pension_com = Decimal("0")
            medical_emp = medical_com = Decimal("0")
            unemp_emp = unemp_com = Decimal("0")
            injury_com = maternity_com = Decimal("0")
            hf_emp = hf_com = Decimal("0")
            si_base = hf_base = salary_base

            for cfg in configs:
                base = max(min(salary_base, cfg.base_max), cfg.base_min)
                emp_amt = _quantize(base * cfg.employee_rate)
                com_amt = _quantize(base * cfg.company_rate)
                t = cfg.insurance_type
                if t == "养老":
                    pension_emp, pension_com = emp_amt, com_amt
                    si_base = base
                elif t == "医疗":
                    medical_emp, medical_com = emp_amt, com_amt
                elif t == "失业":
                    unemp_emp, unemp_com = emp_amt, com_amt
                elif t == "工伤":
                    injury_com = com_amt
                elif t == "生育":
                    maternity_com = com_amt
                elif t == "公积金":
                    hf_emp, hf_com = emp_amt, com_amt
                    hf_base = base

            total_emp = _quantize(pension_emp + medical_emp + unemp_emp + hf_emp)
            total_com = _quantize(pension_com + medical_com + unemp_com + injury_com + maternity_com + hf_com)

            # Upsert
            existing_result = await db.execute(
                select(SocialInsuranceRecord).where(
                    and_(
                        SocialInsuranceRecord.employee_id == emp.id,
                        SocialInsuranceRecord.year == year,
                        SocialInsuranceRecord.month == month,
                    )
                )
            )
            existing = existing_result.scalar_one_or_none()

            record_fields = dict(
                city=city,
                social_insurance_base=si_base,
                housing_fund_base=hf_base,
                pension_employee=pension_emp,
                pension_company=pension_com,
                medical_employee=medical_emp,
                medical_company=medical_com,
                unemployment_employee=unemp_emp,
                unemployment_company=unemp_com,
                injury_company=injury_com,
                maternity_company=maternity_com,
                housing_fund_employee=hf_emp,
                housing_fund_company=hf_com,
                total_employee=total_emp,
                total_company=total_com,
            )

            if existing:
                for k, v in record_fields.items():
                    setattr(existing, k, v)
            else:
                db.add(SocialInsuranceRecord(
                    employee_id=emp.id,
                    year=year,
                    month=month,
                    **record_fields,
                ))
            generated += 1

        await db.flush()
        return {"generated": generated, "skipped": skipped, "year": year, "month": month}

    @staticmethod
    async def list_records(
        db: AsyncSession,
        year: int,
        month: int,
        department_id: Optional[int] = None,
        employee_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> tuple[list[SocialInsuranceRecord], int]:
        """分页查询月度社保明细记录（支持按部门或员工筛选）。

        通过 JOIN Employee 实现部门过滤；使用 selectinload 预加载 employee 关系，
        避免 N+1 查询问题。先查总数（count 子查询），再查分页数据。

        Args:
            db:            异步数据库会话
            year:          年份
            month:         月份
            department_id: 按部门 ID 过滤，None 表示不过滤
            employee_id:   按员工 ID 过滤，None 表示不过滤
            page:          页码（从1开始）
            page_size:     每页记录数（默认100）

        Returns:
            (SocialInsuranceRecord 列表, 总记录数) 元组
        """
        from app.models.employee import Employee as EmpModel
        from sqlalchemy.orm import selectinload

        base_stmt = (
            select(SocialInsuranceRecord)
            .join(EmpModel, SocialInsuranceRecord.employee_id == EmpModel.id)
            .where(
                and_(
                    SocialInsuranceRecord.year == year,
                    SocialInsuranceRecord.month == month,
                )
            )
        )
        if employee_id:
            base_stmt = base_stmt.where(SocialInsuranceRecord.employee_id == employee_id)
        if department_id:
            base_stmt = base_stmt.where(EmpModel.department_id == department_id)

        total_result = await db.execute(
            select(func.count()).select_from(base_stmt.subquery())
        )
        total = total_result.scalar() or 0

        data_stmt = (
            base_stmt
            .options(selectinload(SocialInsuranceRecord.employee))
            .order_by(SocialInsuranceRecord.employee_id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(data_stmt)
        records = list(result.scalars().all())
        return records, total


# ============================================================
# BonusService 奖金管理
# ============================================================

def _calc_bonus_tax(amount: Decimal) -> Decimal:
    """计算奖金个税（全年一次性奖金单独计税，模块级辅助函数）。

    计算方法（全年一次性奖金政策）：
        ① 月均奖金 = amount ÷ 12
        ② 按月均奖金金额在 _DEFAULT_BONUS_BRACKETS 中找到对应税率档次
        ③ 税额 = amount × 该档次税率 - 速算扣除数

    与 TaxCalculationService.calc_bonus_tax_separate() 功能相同，
    此函数是模块级辅助函数，供 BonusService 内部直接调用（无需传入 db）。

    Args:
        amount: 奖金总额（Decimal，非负）

    Returns:
        应缴个税（Decimal，非负，保留2位小数）
    """
    monthly = amount / Decimal("12")
    for upper, rate, quick in _DEFAULT_BONUS_BRACKETS:
        if monthly <= upper:
            tax = _quantize(amount * rate - quick)
            return max(tax, Decimal("0"))
    # 超过最高档按最高档
    _, rate, quick = _DEFAULT_BONUS_BRACKETS[-1]
    tax = _quantize(amount * rate - quick)
    return max(tax, Decimal("0"))


_BONUS_TAX_METHOD_PREFIX = "[tax_method="


def _resolve_bonus_tax_method(
    tax_method: Optional[str],
    bonus_type: str,
    raw_notes: Optional[str] = None,
) -> str:
    if tax_method in {"cumulative", "separate_bonus"}:
        return tax_method
    note_text = str(raw_notes or "")
    if note_text.startswith(_BONUS_TAX_METHOD_PREFIX):
        end_index = note_text.find("]")
        if end_index > len(_BONUS_TAX_METHOD_PREFIX):
            parsed = note_text[len(_BONUS_TAX_METHOD_PREFIX):end_index]
            if parsed in {"cumulative", "separate_bonus"}:
                return parsed
    return "separate_bonus" if bonus_type == "annual" else "cumulative"


def _strip_bonus_notes(raw_notes: Optional[str]) -> str:
    note_text = str(raw_notes or "").strip()
    if note_text.startswith(_BONUS_TAX_METHOD_PREFIX):
        end_index = note_text.find("]")
        if end_index > len(_BONUS_TAX_METHOD_PREFIX):
            return note_text[end_index + 1:].strip()
    return note_text


def _encode_bonus_notes(tax_method: str, raw_notes: Optional[str]) -> Optional[str]:
    note_text = _strip_bonus_notes(raw_notes)
    encoded = f"{_BONUS_TAX_METHOD_PREFIX}{tax_method}]"
    if note_text:
        encoded = f"{encoded}{note_text}"
    return encoded


def _calc_bonus_tax_by_method(amount: Decimal, tax_method: str) -> Decimal:
    if tax_method == "cumulative":
        taxable = max(_quantize(amount), Decimal("0"))
        for upper, rate, quick in _DEFAULT_CUMULATIVE_BRACKETS:
            if taxable <= upper:
                return max(_quantize(taxable * rate - quick), Decimal("0"))
        _, rate, quick = _DEFAULT_CUMULATIVE_BRACKETS[-1]
        return max(_quantize(taxable * rate - quick), Decimal("0"))
    return _calc_bonus_tax(amount)


class BonusService:
    """奖金管理服务——奖金记录的创建、审批与导出。

    支持的奖金类型（bonus_type）：
        - annual：年终奖
        - project：项目奖金
        - performance：绩效奖金
        - other：其他

    奖金状态流转：
        draft（草稿）→ confirm_bonus() → confirmed（已确认）

    关键业务规则：
        - 创建奖金记录时按 tax_method 计算税额
        - net_amount = amount - tax_amount（税后实发金额）
        - 支持单条创建（create_bonus）和批量创建（bulk_create_bonus）
        - export_bonus_excel() 导出 Excel 报表（需要 openpyxl）
    """

    @staticmethod
    def parse_bonus_metadata(raw_notes: Optional[str], bonus_type: str) -> tuple[str, str]:
        tax_method = _resolve_bonus_tax_method(None, bonus_type, raw_notes)
        return tax_method, _strip_bonus_notes(raw_notes)

    @staticmethod
    async def list_bonus(
        db: AsyncSession,
        year: int,
        bonus_type: Optional[str] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[BonusRecord], int]:
        """分页查询奖金记录列表（支持按类型和状态过滤）。

        Args:
            db:         异步数据库会话
            year:       年份（必填，奖金按年管理）
            bonus_type: 奖金类型过滤（annual/project/performance/other），None=不过滤
            status:     状态过滤（draft/confirmed/paid），None=不过滤
            skip:       分页偏移
            limit:      每页最大条数

        Returns:
            (BonusRecord 列表（含 employee 关系）, 总记录数) 元组
        """
        from sqlalchemy.orm import selectinload

        q = select(BonusRecord).where(BonusRecord.year == year)
        if bonus_type:
            q = q.where(BonusRecord.bonus_type == bonus_type)
        if status:
            q = q.where(BonusRecord.status == status)

        count_result = await db.execute(
            select(func.count()).select_from(q.subquery())
        )
        total = count_result.scalar() or 0

        q = (
            q
            .options(selectinload(BonusRecord.employee))
            .order_by(BonusRecord.id.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(q)
        records = list(result.scalars().all())
        return records, total

    @staticmethod
    async def create_bonus(db: AsyncSession, data: BonusRecordCreate) -> BonusRecord:
        """创建单条奖金记录，自动计算个税和税后实发金额。

        税额计算：按 tax_method 选择单独计税或并入综合所得口径
        net_amount = amount - tax_amount

        Args:
            db:   异步数据库会话
            data: BonusRecordCreate Schema（employee_id/bonus_type/year/month/amount/notes）

        Returns:
            新建的 BonusRecord ORM 对象（status='draft'）
        """
        tax_method = _resolve_bonus_tax_method(data.tax_method, data.bonus_type, data.notes)
        tax_amount = _calc_bonus_tax_by_method(data.amount, tax_method)
        net_amount = _quantize(data.amount - tax_amount)

        record = BonusRecord(
            employee_id=data.employee_id,
            bonus_type=data.bonus_type,
            year=data.year,
            month=data.month,
            amount=data.amount,
            tax_amount=tax_amount,
            net_amount=net_amount,
            status="draft",
            notes=_encode_bonus_notes(tax_method, data.notes),
        )
        from sqlalchemy.orm import selectinload

        db.add(record)
        await db.flush()
        result = await db.execute(
            select(BonusRecord)
            .options(selectinload(BonusRecord.employee))
            .where(BonusRecord.id == record.id)
        )
        return result.scalar_one()

    @staticmethod
    async def bulk_create_bonus(db: AsyncSession, data: BulkBonusCreate) -> list[BonusRecord]:
        """批量创建奖金记录（同一批次共享 bonus_type/year/month，各员工金额独立）。

        用于年终奖批量发放场景：HR 一次性录入所有员工的奖金金额，
        系统批量计算税额并生成 BonusRecord。

        Args:
            db:   异步数据库会话
            data: BulkBonusCreate Schema（bonus_type/year/month + bonus_list 员工金额列表）

        Returns:
            新建的 BonusRecord 列表（均为 status='draft'）
        """
        from sqlalchemy.orm import selectinload

        records: list[BonusRecord] = []
        for item in data.bonus_list:
            tax_amount = _calc_bonus_tax(item.amount)
            net_amount = _quantize(item.amount - tax_amount)
            record = BonusRecord(
                employee_id=item.employee_id,
                bonus_type=data.bonus_type,
                year=data.year,
                month=data.month,
                amount=item.amount,
                tax_amount=tax_amount,
                net_amount=net_amount,
                status="draft",
            )
            db.add(record)
            records.append(record)
        await db.flush()
        ids = [record.id for record in records]
        if not ids:
            return []
        result = await db.execute(
            select(BonusRecord)
            .options(selectinload(BonusRecord.employee))
            .where(BonusRecord.id.in_(ids))
        )
        record_map = {record.id: record for record in result.scalars().all()}
        return [record_map[record_id] for record_id in ids if record_id in record_map]

    @staticmethod
    async def confirm_bonus(db: AsyncSession, bonus_id: int) -> Optional[BonusRecord]:
        """确认奖金发放（status: draft → confirmed）。

        Args:
            db:       异步数据库会话
            bonus_id: 奖金记录主键 ID

        Returns:
            更新后的 BonusRecord，不存在则返回 None

        Raises:
            ValueError: 若当前状态不是 'draft'
        """
        result = await db.execute(
            select(BonusRecord).where(BonusRecord.id == bonus_id)
        )
        record = result.scalar_one_or_none()
        if not record:
            return None
        if record.status != "draft":
            raise ValueError("只能确认草稿状态的奖金记录")
        record.status = "confirmed"
        from sqlalchemy.orm import selectinload

        await db.flush()
        refreshed = await db.execute(
            select(BonusRecord)
            .options(selectinload(BonusRecord.employee))
            .where(BonusRecord.id == record.id)
        )
        return refreshed.scalar_one()

    @staticmethod
    async def export_bonus_excel(
        db: AsyncSession, year: int, bonus_type: Optional[str] = None
    ) -> bytes:
        """将奖金记录导出为带格式的 Excel 文件（.xlsx）。

        Excel 格式说明：
            - 表头：紫色背景（#4F46E5）+ 白色粗体字 + 居中对齐
            - 列：序号/员工姓名/工号/奖金类型/年份/发放月份/奖金金额/税额/实发金额/状态/备注
            - 奖金类型和状态均翻译为中文（annual→年终奖，draft→草稿 等）
            - 依赖 openpyxl 库（需在 requirements.txt 中声明）

        Args:
            db:         异步数据库会话
            year:       年份（必填）
            bonus_type: 奖金类型过滤，None 表示导出所有类型

        Returns:
            xlsx 文件的二进制内容（bytes），可直接作为 HTTP 响应体返回
        """
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from sqlalchemy.orm import selectinload

        q = select(BonusRecord).where(BonusRecord.year == year)
        if bonus_type:
            q = q.where(BonusRecord.bonus_type == bonus_type)
        q = q.options(selectinload(BonusRecord.employee)).order_by(BonusRecord.id)
        result = await db.execute(q)
        records = list(result.scalars().all())

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"{year}年奖金明细"

        headers = ["序号", "员工姓名", "工号", "奖金类型", "年份", "发放月份", "奖金金额", "计税方式", "税额", "实发金额", "状态", "备注"]
        header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        bonus_type_map = {
            "annual": "年终奖", "project": "项目奖金",
            "performance": "绩效奖金", "other": "其他"
        }
        status_map = {"draft": "草稿", "confirmed": "已确认", "paid": "已发放"}
        tax_method_map = {
            "separate_bonus": "单独计税",
            "cumulative": "并入综合所得",
        }

        for i, r in enumerate(records, 1):
            emp = r.employee
            tax_method, notes = BonusService.parse_bonus_metadata(r.notes, r.bonus_type)
            ws.append([
                i,
                emp.name if emp else "",
                emp.employee_no if emp else "",
                bonus_type_map.get(r.bonus_type, r.bonus_type),
                r.year,
                r.month or "",
                float(r.amount),
                tax_method_map.get(tax_method, tax_method),
                float(r.tax_amount),
                float(r.net_amount),
                status_map.get(r.status, r.status),
                notes,
            ])

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()


# ============================================================
# BankPaymentService 银行代发文件生成
# ============================================================

class BankPaymentService:
    """银行代发文件生成服务——生成各银行格式的工资代发文件。

    支持三家银行的代发文件格式：
        - 工商银行（icbc）：CSV 格式，UTF-8-BOM 编码
          列：序号,账号,户名,金额,摘要
        - 建设银行（ccb）：TXT 固定宽度格式，GBK 编码
          格式：账号20位 + 户名10位 + 金额12位右对齐 + 摘要20位
        - 招商银行（cmb）：CSV 格式，UTF-8-BOM 编码
          列：收款方账号,收款方户名,金额,用途

    文件名命名规则：代发_{银行名}_{年月}.{格式后缀}

    注意：若员工档案中缺少银行账号（bank_account），金额仍写入，
    摘要/用途字段追加"缺少账号"提示，HR 需人工处理这些记录。

    调用的数据源：SalaryRecord（status in confirmed/approved/paid）+ Employee（含银行账号）
    """

    @staticmethod
    async def _list_latest_records(
        db: AsyncSession,
        year: int,
        month: int,
    ) -> list[SalaryRecord]:
        from sqlalchemy.orm import selectinload

        records_result = await db.execute(
            select(SalaryRecord)
            .where(
                and_(
                    SalaryRecord.year == year,
                    SalaryRecord.month == month,
                    SalaryRecord.status.in_(["approved", "paid"]),
                )
            )
            .options(selectinload(SalaryRecord.employee))
            .order_by(SalaryRecord.employee_id, SalaryRecord.version.desc())
        )
        records = list(records_result.scalars().all())
        latest_by_employee: dict[int, SalaryRecord] = {}
        for record in records:
            latest_by_employee.setdefault(record.employee_id, record)
        return list(latest_by_employee.values())

    @staticmethod
    async def generate_bank_payment_file(
        db: AsyncSession,
        year: int,
        month: int,
        bank_type: str = "icbc",
    ) -> tuple[bytes, str]:
        """生成指定月份的银行代发文件。

        Args:
            db:        异步数据库会话
            year:      年份
            month:     月份
            bank_type: 银行类型（'icbc'=工行 / 'ccb'=建行 / 'cmb'=招行，默认工行）

        Returns:
            (文件内容 bytes, 文件名字符串) 元组

        Raises:
            ValueError: 若 bank_type 不是支持的银行类型

        调用的下游：
            - _gen_icbc() / _gen_ccb() / _gen_cmb()（按银行类型分发）
        """
        records = await BankPaymentService._list_latest_records(db, year, month)

        bank_names = {"icbc": "工商银行", "ccb": "建设银行", "cmb": "招商银行", "abc": "农业银行"}
        period = f"{year}{month:02d}"
        filename = f"代发_{bank_names.get(bank_type, bank_type)}_{period}.{'csv' if bank_type != 'ccb' else 'txt'}"

        if bank_type == "icbc":
            content = BankPaymentService._gen_icbc(records, year, month)
        elif bank_type == "ccb":
            content = BankPaymentService._gen_ccb(records, year, month)
        elif bank_type == "cmb":
            content = BankPaymentService._gen_cmb(records, year, month)
        elif bank_type == "abc":
            content = BankPaymentService._gen_abc(records, year, month)
        else:
            raise ValueError(f"不支持的银行类型: {bank_type}")

        return content, filename

    @staticmethod
    async def preview_bank_payment(
        db: AsyncSession,
        year: int,
        month: int,
        bank_type: str = "icbc",
    ) -> dict:
        bank_names = {"icbc": "工商银行", "ccb": "建设银行", "cmb": "招商银行", "abc": "农业银行"}
        if bank_type not in bank_names:
            raise ValueError(f"不支持的银行类型: {bank_type}")

        records = await BankPaymentService._list_latest_records(db, year, month)

        def mask_account(account: str) -> str:
            text = str(account or "")
            if len(text) <= 8:
                return text or "-"
            return f"{text[:4]}{'*' * (len(text) - 8)}{text[-4:]}"

        preview_rows = []
        total_amount = Decimal("0")
        for record in records:
            employee = record.employee
            total_amount += record.net_salary or Decimal("0")
            preview_rows.append(
                {
                    "employee_name": employee.name if employee else "",
                    "bank_account": mask_account(getattr(employee, "bank_account", "") or ""),
                    "bank_name": getattr(employee, "bank_name", "") or bank_names[bank_type],
                    "amount": float(record.net_salary or 0),
                    "remark": f"{month}月薪资",
                }
            )

        return {
            "batch_name": f"{year}年{month:02d}月薪资",
            "bank_type": bank_type,
            "bank_name": bank_names[bank_type],
            "total_amount": float(total_amount),
            "employee_count": len(records),
            "preview_rows": preview_rows[:10],
        }

    @staticmethod
    def generate_template(bank_type: str = "icbc") -> tuple[bytes, str]:
        bank_names = {"icbc": "工商银行", "ccb": "建设银行", "cmb": "招商银行", "abc": "农业银行"}
        if bank_type not in bank_names:
            raise ValueError(f"不支持的银行类型: {bank_type}")

        if bank_type == "ccb":
            content = "账号".ljust(20) + "户名".ljust(10) + "金额".rjust(12) + "摘要".ljust(20)
            return content.encode("gbk", errors="ignore"), f"{bank_names[bank_type]}_代发模板.txt"

        headers_map = {
            "icbc": ["序号", "账号", "户名", "金额", "摘要"],
            "cmb": ["收款方账号", "收款方户名", "金额", "用途"],
            "abc": ["收款账号", "收款户名", "金额", "摘要"],
        }
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers_map[bank_type])
        return buf.getvalue().encode("utf-8-sig"), f"{bank_names[bank_type]}_代发模板.csv"

    @staticmethod
    def _gen_icbc(records: list, year: int, month: int) -> bytes:
        """生成工商银行代发 CSV 文件（UTF-8-BOM 编码，Excel 可直接打开）。

        列：序号 | 账号 | 户名 | 金额 | 摘要
        摘要格式："{year}年{month}月工资 [缺少账号]"（缺账号时追加提示）

        Args:
            records: SalaryRecord 列表（含 employee 关联）
            year:    年份
            month:   月份

        Returns:
            CSV 文件的 bytes（UTF-8-BOM 编码）
        """
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["序号", "账号", "户名", "金额", "摘要"])
        for i, r in enumerate(records, 1):
            emp = r.employee
            account = (emp.bank_account or "") if emp else ""
            name = (emp.name or "") if emp else ""
            notes = "" if account else "缺少账号"
            remark = f"{year}年{month}月工资"
            writer.writerow([i, account, name, float(r.net_salary), f"{remark} {notes}".strip()])
        return buf.getvalue().encode("utf-8-sig")

    @staticmethod
    def _gen_ccb(records: list, year: int, month: int) -> bytes:
        """生成建设银行代发 TXT 固定宽度文件（GBK 编码）。

        每行固定格式（无分隔符）：
            账号左对齐补空格20位 + 户名左对齐补空格10位 +
            金额右对齐12位（保留2位小数） + 摘要左对齐补空格20位（截断超长）
        编码：GBK（建行系统要求），中文字符转换失败时用 ? 替代。

        Args:
            records: SalaryRecord 列表（含 employee 关联）
            year:    年份
            month:   月份

        Returns:
            TXT 文件的 bytes（GBK 编码）
        """
        lines: list[str] = []
        for r in records:
            emp = r.employee
            account = (emp.bank_account or "") if emp else ""
            name = (emp.name or "") if emp else ""
            notes = "" if account else "缺少账号"
            remark = f"{year}年{month}月工资"
            amount_str = f"{float(r.net_salary):.2f}".rjust(12)
            summary = f"{remark}{notes}"[:20]
            line = f"{account:<20}{name:<10}{amount_str}{summary:<20}"
            lines.append(line)
        return "\n".join(lines).encode("gbk", errors="replace")

    @staticmethod
    def _gen_cmb(records: list, year: int, month: int) -> bytes:
        """生成招商银行代发 CSV 文件（UTF-8-BOM 编码）。

        列：收款方账号 | 收款方户名 | 金额 | 用途
        用途格式："{year}年{month}月工资 [缺少账号]"（缺账号时追加提示）

        Args:
            records: SalaryRecord 列表（含 employee 关联）
            year:    年份
            month:   月份

        Returns:
            CSV 文件的 bytes（UTF-8-BOM 编码）
        """
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["收款方账号", "收款方户名", "金额", "用途"])
        for r in records:
            emp = r.employee
            account = (emp.bank_account or "") if emp else ""
            name = (emp.name or "") if emp else ""
            notes = "" if account else "缺少账号"
            purpose = f"{year}年{month}月工资 {notes}".strip()
            writer.writerow([account, name, float(r.net_salary), purpose])
        return buf.getvalue().encode("utf-8-sig")

    @staticmethod
    def _gen_abc(records: list, year: int, month: int) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["收款账号", "收款户名", "金额", "摘要"])
        for r in records:
            emp = r.employee
            account = (emp.bank_account or "") if emp else ""
            name = (emp.name or "") if emp else ""
            notes = "" if account else "缺少账号"
            writer.writerow([account, name, float(r.net_salary), f"{year}年{month}月工资 {notes}".strip()])
        return buf.getvalue().encode("utf-8-sig")


# ============================================================
# CostAnalysisService 薪资成本分析
# ============================================================

class CostAnalysisService:
    """薪资成本分析服务——为管理驾驶舱提供多维度成本分析数据。

    支持两种分析维度（group_by）：
        1. 'department'：按部门分组汇总（全年）
           指标：人头数 / 应发合计 / 平均应发 / 最高/最低应发 / 占总薪资比例
           结果按应发工资降序排列（最贵部门在前）

        2. 'month'：按月份分组汇总（年度趋势）
           指标：应发合计 / 实发合计 / 已发放人数 / 未发放人数

    关键实现：
        - _by_department()：JOIN Employee + GROUP BY department_id，
          并关联 Department 表获取部门名称
        - _by_month()：GROUP BY month，FILTER 区分已发/未发人数
    """

    @staticmethod
    async def get_cost_analysis(
        db: AsyncSession,
        year: int,
        group_by: str = "department",
    ) -> CostAnalysisResult:
        """薪资成本分析主入口——按维度分发到不同的汇总方法。

        Args:
            db:       异步数据库会话
            year:     分析年份
            group_by: 分组维度（'department'=按部门 / 'month'=按月份）

        Returns:
            CostAnalysisResult Pydantic 模型（含 department_data 或 month_data）

        Raises:
            ValueError: 若 group_by 值不是 'department' 或 'month'

        调用的下游：
            - _by_department()（group_by='department' 时）
            - _by_month()（group_by='month' 时）
        """
        from app.models.employee import Employee as EmpModel
        from app.models.organization import Department

        if group_by == "department":
            return await CostAnalysisService._by_department(db, year, EmpModel, Department)
        elif group_by == "month":
            return await CostAnalysisService._by_month(db, year)
        else:
            raise ValueError(f"不支持的 group_by 值: {group_by}")

    @staticmethod
    async def _by_department(
        db: AsyncSession, year: int, EmpModel: type, Department: type
    ) -> CostAnalysisResult:
        """按部门汇总全年薪资成本数据（内部方法）。

        SQL 逻辑：
            SELECT department_id, SUM(gross), COUNT(DISTINCT employee_id),
                   AVG(gross), MAX(gross), MIN(gross)
            FROM salary_records JOIN employees ON employee_id
            WHERE year = ? GROUP BY department_id

        额外计算：
            - 拉取所有 Department 记录构建 id→name 映射（避免逐条查询）
            - 各部门占比 = 该部门总薪资 / 全体总薪资 × 100%
            - 未分配部门（department_id=NULL）归入"未分配"

        Args:
            db:         异步数据库会话
            year:       分析年份
            EmpModel:   Employee ORM 类（避免循环导入，由调用方传入）
            Department: Department ORM 类（同上）

        Returns:
            CostAnalysisResult（group_by='department'，department_data 已按应发降序排列）
        """
        result = await db.execute(
            select(
                EmpModel.department_id,
                func.sum(SalaryRecord.gross_salary).label("total_gross"),
                func.count(SalaryRecord.employee_id.distinct()).label("headcount"),
                func.avg(SalaryRecord.gross_salary).label("avg_gross"),
                func.max(SalaryRecord.gross_salary).label("max_gross"),
                func.min(SalaryRecord.gross_salary).label("min_gross"),
            )
            .join(EmpModel, SalaryRecord.employee_id == EmpModel.id)
            .where(SalaryRecord.year == year)
            .group_by(EmpModel.department_id)
        )
        rows = result.all()

        # 计算全年总薪资用于占比计算
        total_all = sum((r.total_gross or Decimal("0")) for r in rows)

        # 获取部门名称映射
        dept_result = await db.execute(select(Department))
        depts = {d.id: d.name for d in dept_result.scalars().all()}

        items: list[DepartmentCostItem] = []
        for r in rows:
            dept_id = r.department_id
            dept_name = depts.get(dept_id, "未分配") if dept_id else "未分配"
            total_gross = r.total_gross or Decimal("0")
            ratio = float(total_gross / total_all * 100) if total_all else 0.0
            items.append(DepartmentCostItem(
                department_id=dept_id,
                department_name=dept_name,
                headcount=r.headcount or 0,
                total_gross=total_gross,
                avg_gross=r.avg_gross or Decimal("0"),
                max_gross=r.max_gross or Decimal("0"),
                min_gross=r.min_gross or Decimal("0"),
                ratio=round(ratio, 2),
            ))

        items.sort(key=lambda x: x.total_gross, reverse=True)
        return CostAnalysisResult(year=year, group_by="department", department_data=items)

    @staticmethod
    async def _by_month(db: AsyncSession, year: int) -> CostAnalysisResult:
        """按月份汇总全年薪资成本趋势数据（内部方法）。

        SQL 逻辑：
            SELECT month, SUM(gross), SUM(net),
                   SUM(CASE WHEN status IN approved/paid THEN 1 ELSE 0 END) AS paid_count,
                   SUM(CASE WHEN status IN draft/pending THEN 1 ELSE 0 END) AS unpaid_count
            FROM salary_records WHERE year = ? GROUP BY month ORDER BY month

        兼容性说明：
            SQLAlchemy 的 ``count(...).filter(...)`` 会编译成 PostgreSQL 风格
            ``FILTER (WHERE ...)`` 聚合语法，MySQL 不支持；
            这里改用 ``SUM(CASE WHEN ... THEN 1 ELSE 0 END)``，保证正式环境
            MySQL 与本地 PostgreSQL 都能正常执行。

        Args:
            db:   异步数据库会话
            year: 分析年份

        Returns:
            CostAnalysisResult（group_by='month'，month_data 按月份升序排列）
        """
        result = await db.execute(
            select(
                SalaryRecord.month,
                func.sum(SalaryRecord.gross_salary).label("total_gross"),
                func.sum(SalaryRecord.net_salary).label("total_net"),
                func.sum(
                    case(
                        (SalaryRecord.status.in_(["approved", "paid"]), 1),
                        else_=0,
                    )
                ).label("paid_count"),
                func.sum(
                    case(
                        (SalaryRecord.status.in_(["draft", "pending_approval"]), 1),
                        else_=0,
                    )
                ).label("unpaid_count"),
            )
            .where(SalaryRecord.year == year)
            .group_by(SalaryRecord.month)
            .order_by(SalaryRecord.month)
        )
        rows = result.all()

        items: list[MonthCostItem] = []
        for r in rows:
            items.append(MonthCostItem(
                year=year,
                month=r.month,
                total_gross=r.total_gross or Decimal("0"),
                total_net=r.total_net or Decimal("0"),
                paid_count=r.paid_count or 0,
                unpaid_count=r.unpaid_count or 0,
            ))

        return CostAnalysisResult(year=year, group_by="month", month_data=items)
