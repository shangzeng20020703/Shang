"""
员工模块 Service — 员工生命周期、合同及附属档案的核心业务逻辑层

架构位置：
    API 层 (api/v1/employees.py) → EmployeeService / ContractService / ...
    Service 层（本文件）→ SQLAlchemy ORM Model (models/employee.py)

业务职责：
    1. EmployeeService     - 员工主档案 CRUD、状态机流转、批量操作、Excel 导入导出、预警
    2. ContractService     - 员工劳动合同增删改查、续签、到期提醒
    3. WorkExperienceService   - 工作经历增删改查
    4. EmployeeDocumentService - 员工档案材料（上传文件记录）增删改查
    5. FamilyMemberService     - 家庭成员信息增删改查
    6. EducationRecordService  - 教育背景增删改查
    7. EmployeePersonalSkillService - 员工个人技能（每人一条记录）创建/更新
    8. RegistrationFormService - 解析标准员工信息登记表 XLS 并批量导入

依赖关系：
    - app.core.security: 密码 hash / 验证 / JWT 生成
    - app.models.employee: Employee, EmployeeContract, WorkExperience, ...
    - app.models.organization: Department, Location（用于关联校验）
    - app.schemas.employee: 各 Pydantic 输入 schema

被哪些 API 端点调用：
    - GET/POST/PUT/DELETE /api/v1/employees/*  → EmployeeService
    - GET/POST/PUT /api/v1/employees/{id}/contracts/* → ContractService
    - GET/POST/PUT/DELETE /api/v1/employees/{id}/work-experiences/* → WorkExperienceService
    - GET/POST/PUT/DELETE /api/v1/employees/{id}/documents/* → EmployeeDocumentService
    - GET/POST/PUT/DELETE /api/v1/employees/{id}/family-members/* → FamilyMemberService
    - GET/POST/PUT/DELETE /api/v1/employees/{id}/education-records/* → EducationRecordService
    - GET/POST/PUT /api/v1/employees/{id}/skills → EmployeePersonalSkillService
    - POST /api/v1/employees/import → EmployeeService.import_excel / RegistrationFormService.import_xls

架构约定：
    - 所有方法以 db: AsyncSession 为第一参数，不自行 commit（由 get_db() 统一 commit）
    - 使用 await db.execute(select(...)) 异步查询，await db.flush() 刷写但不提交
    - 业务校验失败抛出 HTTPException（400/404）；全局异常处理器捕获 ValueError → 400
"""
import io
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import and_, extract, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.core.security import (
    DEFAULT_INITIAL_PASSWORD,
    get_password_hash,
    verify_password,
    create_access_token,
)
from app.models.employee import (
    Employee,
    EmployeeContract,
    EmployeeStatus,
    ContractStatus,
    WorkExperience,
    EmployeeDocument,
    FamilyMember,
    EducationRecord,
    EmployeePersonalSkill,
)
from app.models.changelog import DepartmentChangeLog
from app.models.organization import Department, Location
from app.schemas.employee import (
    ContractCreate,
    ContractRenew,
    EmployeeCreate,
    EmployeeUpdate,
    StatusTransition,
    WorkExperienceCreate,
    WorkExperienceUpdate,
    EmployeeDocumentCreate,
    EmployeeDocumentUpdate,
    FamilyMemberCreate,
    FamilyMemberUpdate,
    EducationRecordCreate,
    EducationRecordUpdate,
    EmployeePersonalSkillCreate,
    EmployeePersonalSkillUpdate,
)


# ==================== 状态机：合法的状态流转 ====================
# 员工生命周期只能沿以下路径单向流转，不可逆转、不可跳跃：
#   待入职(PENDING) → 试用期(ON_PROBATION) → 在职(ACTIVE) → 离职(LEFT)
# VALID_TRANSITIONS 以"当前状态"为 key，值为允许到达的目标状态列表。
# 试用期可直接离职（如试用不合格），所以 ON_PROBATION 有两个目标。
# LEFT 为终态，不出现在 key 中，任何到 LEFT 的尝试都会被 transition_status 拒绝。

VALID_TRANSITIONS: dict[str, list[str]] = {
    EmployeeStatus.PENDING.value: [EmployeeStatus.ON_PROBATION.value],
    EmployeeStatus.ON_PROBATION.value: [EmployeeStatus.ACTIVE.value, EmployeeStatus.LEFT.value],
    EmployeeStatus.ACTIVE.value: [EmployeeStatus.LEFT.value],
    # 离职为终态，不可逆
}


class EmployeeService:
    """
    员工主档案 Service。

    负责员工的全生命周期管理，包括：
    - 查询（分页/关键字/多维筛选）
    - 增删改
    - 状态机流转（入职→试用→在职→离职）
    - Excel 花名册导出（在职/离职双 sheet）
    - Excel 批量导入（支持标准导入模板和花名册两种格式）
    - 批量离职、批量调岗
    - 预警（合同到期/试用期结束/本月生日）
    - 手机号+密码登录认证
    """

    @staticmethod
    async def _sync_leave_balances_for_employees(
        db: AsyncSession,
        employee_ids: List[int],
        *,
        year: Optional[int] = None,
    ) -> None:
        """
        员工主档案变更后同步规则型假期余额。

        假期余额依赖员工入职日期、最初工作时间、部门、员工类型等档案字段。
        这些字段发生变化时，需要立即按当前假期规则重算当年余额，避免员工管理
        与假期管理页面看到两套不同数据。
        """
        normalized_ids = sorted({int(emp_id) for emp_id in employee_ids if emp_id})
        if not normalized_ids:
            return
        from app.services.leave import LeaveBalanceService, LeaveTypeService

        target_year = int(year or date.today().year)
        await LeaveTypeService.ensure_default_wecom_types(db)
        await LeaveBalanceService.sync_rule_based_balances(
            db,
            year=target_year,
            employee_ids=normalized_ids,
        )

    @staticmethod
    def _normalize_identity_card(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip().upper()
        return text or None

    @staticmethod
    def _is_masked_identity_card(value: Optional[str]) -> bool:
        return "*" in str(value or "")

    @staticmethod
    async def _ensure_identity_card_unique(
        db: AsyncSession,
        id_card_no: Optional[str],
        *,
        employee_id: Optional[int] = None,
    ) -> None:
        normalized = EmployeeService._normalize_identity_card(id_card_no)
        if not normalized or EmployeeService._is_masked_identity_card(normalized):
            return

        stmt = (
            select(func.count())
            .select_from(Employee)
            .where(Employee.id_card_no == normalized)
        )
        if employee_id is not None:
            stmt = stmt.where(Employee.id != employee_id)
        exists = (await db.execute(stmt)).scalar() or 0
        if exists > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="身份证号已存在，请核对在职和离职员工档案",
            )

    @staticmethod
    def _validate_leave_date_not_before_hire(employee: Employee, leave_date: date) -> None:
        if employee.hire_date and leave_date < employee.hire_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"离职日期不能早于入职日期（{employee.hire_date.isoformat()}）",
            )

    @staticmethod
    async def _apply_due_transfer_logs(
        db: AsyncSession,
        *,
        as_of: Optional[date] = None,
    ) -> List[int]:
        """
        将已到生效日的任职变动日志同步到员工当前主档。

        department_change_logs 没有单独的“已应用”字段，所以这里按日志中的
        变动前快照做幂等判断：只有员工当前部门/职位仍等于 from 快照时，才
        应用对应维度，避免历史日志或人工修正把当前组织回滚。
        """
        effective_day = as_of or date.today()
        cutoff = datetime.combine(effective_day, datetime.max.time(), tzinfo=timezone.utc)
        stmt = (
            select(DepartmentChangeLog, Employee)
            .join(Employee, DepartmentChangeLog.employee_id == Employee.id)
            .where(
                DepartmentChangeLog.effective_date <= cutoff,
                DepartmentChangeLog.change_type.in_(["department_transfer", "position_change"]),
                Employee.status != EmployeeStatus.LEFT.value,
            )
            .order_by(
                DepartmentChangeLog.employee_id.asc(),
                DepartmentChangeLog.effective_date.asc(),
                DepartmentChangeLog.id.asc(),
            )
        )
        rows = (await db.execute(stmt)).all()
        changed_employee_ids: set[int] = set()

        for log, emp in rows:
            changed = False
            log_changes_department = log.from_department_id != log.to_department_id
            log_changes_position = (log.from_position or None) != (log.to_position or None)
            snapshot_still_matches = (
                emp.department_id == log.from_department_id
                and (emp.position or None) == (log.from_position or None)
            )

            if not snapshot_still_matches:
                continue

            if log_changes_department:
                await EmployeeService._sync_manager_after_transfer(
                    db,
                    employee=emp,
                    new_department_id=log.to_department_id,
                )
                emp.department_id = log.to_department_id
                changed = True

            if log_changes_position:
                emp.position = log.to_position
                changed = True

            if changed:
                changed_employee_ids.add(int(emp.id))

        if changed_employee_ids:
            await db.flush()
            await EmployeeService._sync_leave_balances_for_employees(
                db,
                sorted(changed_employee_ids),
            )
        return sorted(changed_employee_ids)

    # ---------- 查询 ----------

    @staticmethod
    async def _write_department_change_log(
        db: AsyncSession,
        *,
        employee: Employee,
        to_department_id: Optional[int],
        to_position: Optional[str],
        effective_date: date,
        operator_id: Optional[int] = None,
        remark: Optional[str] = None,
    ) -> None:
        """
        写入员工部门/职位变更历史（如无实际变化则不写）。
        """
        from_department_id = employee.department_id
        from_position = employee.position
        new_position = to_position if to_position is not None else employee.position
        new_department_id = to_department_id if to_department_id is not None else employee.department_id

        department_changed = from_department_id != new_department_id
        position_changed = from_position != new_position
        if not department_changed and not position_changed:
            return

        if department_changed:
            change_type = "department_transfer"
        else:
            change_type = "position_change"

        log = DepartmentChangeLog(
            employee_id=employee.id,
            change_type=change_type,
            from_department_id=from_department_id,
            to_department_id=new_department_id,
            from_position=from_position,
            to_position=new_position,
            from_job_level=employee.job_level,
            to_job_level=employee.job_level,
            effective_date=datetime.combine(effective_date, datetime.min.time(), tzinfo=timezone.utc),
            operator_id=operator_id,
            remark=remark,
        )
        db.add(log)

    @staticmethod
    async def _sync_manager_after_transfer(
        db: AsyncSession,
        *,
        employee: Employee,
        new_department_id: Optional[int],
    ) -> None:
        """
        调岗后同步直属上级，避免审批链继续指向旧部门负责人。
        规则：
        - 未变更部门则不处理
        - 变更后优先绑定目标部门 manager_id（且不能是自己）
        - 若目标部门无负责人，则清空 direct_manager_id
        """
        if new_department_id is None or new_department_id == employee.department_id:
            if new_department_id is None and employee.department_id is not None:
                employee.direct_manager_id = None
            return
        dept = (
            await db.execute(
                select(Department).where(Department.id == new_department_id, Department.is_active == True)
            )
        ).scalar_one_or_none()
        if not dept:
            return
        target_manager_id = dept.manager_id if dept.manager_id != employee.id else None
        employee.direct_manager_id = target_manager_id

    @staticmethod
    async def _department_default_manager_id(
        db: AsyncSession,
        department_id: Optional[int],
        *,
        employee_id: Optional[int] = None,
    ) -> Optional[int]:
        if not department_id:
            return None
        dept = (
            await db.execute(
                select(Department.manager_id).where(Department.id == department_id, Department.is_active == True)
            )
        ).scalar_one_or_none()
        if not dept or dept == employee_id:
            return None
        manager_exists = (
            await db.execute(
                select(func.count())
                .select_from(Employee)
                .where(Employee.id == dept, Employee.is_active == True)
            )
        ).scalar()
        return int(dept) if manager_exists else None

    @staticmethod
    async def _validate_direct_manager_id(
        db: AsyncSession,
        manager_id: Optional[int],
        *,
        employee_id: Optional[int] = None,
    ) -> None:
        if manager_id is None:
            return
        if employee_id and manager_id == employee_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="直属上级不能是员工本人",
            )
        manager = (
            await db.execute(
                select(Employee.id, Employee.direct_manager_id)
                .where(Employee.id == manager_id, Employee.is_active == True)
            )
        ).first()
        if not manager:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="直属上级不存在或已停用",
            )
        if not employee_id:
            return

        visited: set[int] = set()
        current_manager_id = int(manager_id)
        for _ in range(30):
            if current_manager_id in visited:
                break
            visited.add(current_manager_id)
            if current_manager_id == employee_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="直属上级不能形成循环汇报关系",
                )
            result = await db.execute(
                select(Employee.direct_manager_id).where(Employee.id == current_manager_id)
            )
            next_manager_id = result.scalar_one_or_none()
            if not next_manager_id:
                break
            current_manager_id = int(next_manager_id)

    @staticmethod
    async def get_list(
        db: AsyncSession,
        *,
        keyword: Optional[str] = None,
        company: Optional[str] = None,
        department_id: Optional[int] = None,
        department_ids: Optional[List[int]] = None,
        location_id: Optional[int] = None,
        status: Optional[str] = None,
        employment_type: Optional[str] = None,
        is_field_staff: Optional[bool] = None,
        position: Optional[str] = None,
        education: Optional[str] = None,
        hire_date_from: Optional[date] = None,
        hire_date_to: Optional[date] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[int, List[Employee]]:
        """
        分页查询员工列表，支持关键字搜索（姓名/工号/手机号）和多维度筛选。

        注意：
        - 默认过滤掉 is_active=False 的员工（在职视图）
        - 当 status=离职（或已离职）时，返回离职员工历史档案
        分页采用 OFFSET/LIMIT 模式，先 count 再 select，减少大结果集开销。
        关联加载 location、department 以避免 N+1 查询。

        参数：
            db             - 异步数据库会话
            keyword        - 模糊搜索，匹配姓名 / 工号 / 手机号（ILIKE，大小写不敏感）
            company        - 所属公司关键词模糊搜索
            department_id  - 按部门 ID 精确筛选
            department_ids - 按多个部门 ID 筛选
            location_id    - 按工作地点 ID 精确筛选
            status         - 按员工状态精确筛选（待入职/试用期/在职/离职）
            employment_type - 按用工类型精确筛选（正式/外包/实习等）
            is_field_staff - 是否外勤人员
            position       - 职位关键字模糊搜索
            education      - 学历精确筛选
            hire_date_from - 入职日期范围起始（含）
            hire_date_to   - 入职日期范围截止（含）
            page           - 页码（从 1 开始）
            page_size      - 每页条数

        返回：
            (total_count, items) 元组，items 已加载 location 和 department 关联
        """
        await EmployeeService._apply_due_transfer_logs(db)

        # 基础查询：
        # - 默认仅查活跃员工（在职/待入职/试用期）
        # - 当筛选状态为“离职/已离职”时，切换到离职员工集（保留历史档案）
        left_status_values = {EmployeeStatus.LEFT.value, "已离职"}
        query_left_only = status in left_status_values
        base_stmt = select(Employee)
        if query_left_only:
            base_stmt = base_stmt.where(Employee.status == EmployeeStatus.LEFT.value)
        else:
            base_stmt = base_stmt.where(Employee.is_active == True)

        # 关键字模糊搜索（姓名 OR 工号 OR 手机号，ILIKE 大小写不敏感）
        if keyword:
            like_pattern = f"%{keyword}%"
            base_stmt = base_stmt.where(
                or_(
                    Employee.name.ilike(like_pattern),
                    Employee.employee_no.ilike(like_pattern),
                    Employee.phone.ilike(like_pattern),
                )
            )
        if company:
            base_stmt = base_stmt.where(Employee.company.ilike(f"%{company}%"))

        # 筛选条件（各条件之间为 AND 关系）
        normalized_department_ids = {
            int(item) for item in (department_ids or []) if item is not None
        }
        if department_id is not None:
            normalized_department_ids.add(int(department_id))
        if normalized_department_ids:
            if len(normalized_department_ids) == 1:
                base_stmt = base_stmt.where(Employee.department_id == next(iter(normalized_department_ids)))
            else:
                base_stmt = base_stmt.where(Employee.department_id.in_(sorted(normalized_department_ids)))
        if location_id is not None:
            base_stmt = base_stmt.where(Employee.location_id == location_id)
        if status is not None and not query_left_only:
            base_stmt = base_stmt.where(Employee.status == status)
        if employment_type is not None:
            base_stmt = base_stmt.where(Employee.employment_type == employment_type)
        if is_field_staff is not None:
            base_stmt = base_stmt.where(Employee.is_field_staff == is_field_staff)
        if position:
            base_stmt = base_stmt.where(Employee.position.ilike(f"%{position}%"))
        if education:
            base_stmt = base_stmt.where(Employee.education == education)
        if hire_date_from is not None:
            base_stmt = base_stmt.where(Employee.hire_date >= hire_date_from)
        if hire_date_to is not None:
            base_stmt = base_stmt.where(Employee.hire_date <= hire_date_to)

        # 先查总数（将过滤后的查询作为子查询，再 count）
        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        # 分页查询并预加载关联（避免 N+1）
        offset = (page - 1) * page_size
        items_stmt = (
            base_stmt
            .options(selectinload(Employee.location), selectinload(Employee.department))
            .options(selectinload(Employee.direct_manager))
            .order_by(Employee.created_at.desc(), Employee.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(items_stmt)
        items = list(result.scalars().all())
        return total, items

    @staticmethod
    async def get_by_id(db: AsyncSession, employee_id: int) -> Employee:
        """
        按主键 ID 获取员工详情，同时预加载 location、department、contracts 关联。

        参数：
            db          - 异步数据库会话
            employee_id - 员工主键 ID

        返回：
            Employee ORM 对象（含关联数据）

        异常：
            HTTPException(404) - 员工不存在时抛出
        """
        await EmployeeService._apply_due_transfer_logs(db)

        result = await db.execute(
            select(Employee)
            .options(
                selectinload(Employee.location),
                selectinload(Employee.department),
                selectinload(Employee.direct_manager),
                selectinload(Employee.contracts),
            )
            .where(Employee.id == employee_id)
        )
        emp = result.scalar_one_or_none()
        if not emp:
            raise HTTPException(
                status_code=404,
                detail=f"员工 ID={employee_id} 不存在",
            )
        return emp

    @staticmethod
    async def list_change_logs(db: AsyncSession, employee_id: int) -> List[dict]:
        """
        查询员工任职变动历史，按生效时间倒序返回。

        这里在日志快照之外补齐部门名和操作人姓名，便于员工详情页直接展示
        “原部门 -> 新部门”和时间节点。
        """
        from_department = aliased(Department)
        to_department = aliased(Department)
        operator = aliased(Employee)
        change_type_labels = {
            "department_transfer": "部门调动",
            "position_change": "职位变更",
            "promotion": "晋升",
            "demotion": "降职",
        }

        stmt = (
            select(
                DepartmentChangeLog,
                from_department.name.label("from_department_name"),
                to_department.name.label("to_department_name"),
                operator.name.label("operator_name"),
            )
            .outerjoin(from_department, DepartmentChangeLog.from_department_id == from_department.id)
            .outerjoin(to_department, DepartmentChangeLog.to_department_id == to_department.id)
            .outerjoin(operator, DepartmentChangeLog.operator_id == operator.id)
            .where(DepartmentChangeLog.employee_id == employee_id)
            .order_by(DepartmentChangeLog.effective_date.desc(), DepartmentChangeLog.id.desc())
        )
        rows = (await db.execute(stmt)).all()

        return [
            {
                "id": log.id,
                "employee_id": log.employee_id,
                "change_type": log.change_type,
                "change_type_label": change_type_labels.get(log.change_type, "任职变动"),
                "from_department_id": log.from_department_id,
                "from_department_name": from_department_name,
                "to_department_id": log.to_department_id,
                "to_department_name": to_department_name,
                "from_position": log.from_position,
                "to_position": log.to_position,
                "from_job_level": log.from_job_level,
                "to_job_level": log.to_job_level,
                "effective_date": log.effective_date,
                "operator_id": log.operator_id,
                "operator_name": operator_name,
                "remark": log.remark,
                "created_at": log.created_at,
            }
            for log, from_department_name, to_department_name, operator_name in rows
        ]

    @staticmethod
    async def get_by_phone(db: AsyncSession, phone: str) -> Optional[Employee]:
        """
        按手机号查找员工，主要用于登录时查找账号。

        参数：
            db    - 异步数据库会话
            phone - 手机号字符串

        返回：
            Employee 对象，若不存在返回 None
        """
        result = await db.execute(
            select(Employee).where(Employee.phone == phone)
        )
        return result.scalar_one_or_none()

    # ---------- 创建 ----------

    @staticmethod
    async def create(db: AsyncSession, data: EmployeeCreate) -> Employee:
        """
        创建新员工。

        业务规则：
        1. 工号（employee_no）全局唯一，重复则 400
        2. 手机号（phone）全局唯一，重复则 400
        3. 初始密码默认统一为 123456，并以 bcrypt hash 存储，原文不入库
        4. 不自行 commit，由调用方的 get_db() 统一提交

        参数：
            db   - 异步数据库会话
            data - EmployeeCreate schema（含 password 明文；缺省为统一初始密码）

        返回：
            新创建的 Employee ORM 对象（已刷写，含自增 id）

        异常：
            HTTPException(400) - 工号或手机号重复
        """
        # 检查工号唯一
        exists = await db.execute(
            select(func.count())
            .select_from(Employee)
            .where(Employee.employee_no == data.employee_no)
        )
        if exists.scalar() > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"工号 '{data.employee_no}' 已存在",
            )

        # 检查手机号唯一
        exists_phone = await db.execute(
            select(func.count())
            .select_from(Employee)
            .where(Employee.phone == data.phone)
        )
        if exists_phone.scalar() > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"手机号 '{data.phone}' 已被注册",
            )

        normalized_id_card = EmployeeService._normalize_identity_card(data.id_card_no)
        await EmployeeService._ensure_identity_card_unique(db, normalized_id_card)

        # 排除明文密码字段，单独 hash 后存入 hashed_password
        emp_data = data.model_dump(exclude={"password"})
        emp_data["id_card_no"] = normalized_id_card
        if emp_data.get("direct_manager_id") is not None:
            await EmployeeService._validate_direct_manager_id(db, emp_data.get("direct_manager_id"))
        else:
            emp_data["direct_manager_id"] = await EmployeeService._department_default_manager_id(
                db,
                emp_data.get("department_id"),
            )
        emp_data["hashed_password"] = get_password_hash(data.password or DEFAULT_INITIAL_PASSWORD)
        emp = Employee(**emp_data)
        db.add(emp)
        await db.flush()       # 写入数据库但不提交，使自增 id 可用
        await EmployeeService._sync_leave_balances_for_employees(db, [emp.id])
        await db.refresh(emp)  # 刷新 ORM 对象，获取数据库默认值（如 created_at）
        return emp

    # ---------- 更新 ----------

    @staticmethod
    async def update(
        db: AsyncSession, employee_id: int, data: EmployeeUpdate
    ) -> Employee:
        """
        更新员工基本信息（部分更新，exclude_unset=True）。

        若请求中包含 phone 字段，额外检查新手机号不与其他员工冲突。
        注意：状态变更不走此方法，须通过 transition_status 以强制走状态机校验。

        参数：
            db          - 异步数据库会话
            employee_id - 目标员工主键 ID
            data        - EmployeeUpdate schema（仅含需更新的字段）

        返回：
            更新后的 Employee ORM 对象

        异常：
            HTTPException(404) - 员工不存在
            HTTPException(400) - 新手机号已被其他员工使用
        """
        emp = await EmployeeService.get_by_id(db, employee_id)
        update_data = data.model_dump(exclude_unset=True)

        if "id_card_no" in update_data:
            normalized_id_card = EmployeeService._normalize_identity_card(update_data.get("id_card_no"))
            if EmployeeService._is_masked_identity_card(normalized_id_card):
                update_data.pop("id_card_no", None)
            else:
                update_data["id_card_no"] = normalized_id_card
                if normalized_id_card != emp.id_card_no:
                    await EmployeeService._ensure_identity_card_unique(
                        db,
                        normalized_id_card,
                        employee_id=employee_id,
                    )

        # 如果更新手机号，需检查唯一性（排除自身）
        if "phone" in update_data and update_data["phone"] != emp.phone:
            exists = await db.execute(
                select(func.count())
                .select_from(Employee)
                .where(Employee.phone == update_data["phone"])
            )
            if exists.scalar() > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"手机号 '{update_data['phone']}' 已被使用",
                )

        if "direct_manager_id" in update_data:
            await EmployeeService._validate_direct_manager_id(
                db,
                update_data.get("direct_manager_id"),
                employee_id=employee_id,
            )
        elif "department_id" in update_data:
            update_data["direct_manager_id"] = await EmployeeService._department_default_manager_id(
                db,
                update_data.get("department_id"),
                employee_id=employee_id,
            )

        if "department_id" in update_data or "position" in update_data:
            await EmployeeService._write_department_change_log(
                db,
                employee=emp,
                to_department_id=update_data.get("department_id"),
                to_position=update_data.get("position"),
                effective_date=date.today(),
                operator_id=None,
                remark="员工信息维护触发调岗记录",
            )
        for field, value in update_data.items():
            setattr(emp, field, value)
        await db.flush()
        await EmployeeService._sync_leave_balances_for_employees(db, [emp.id])
        await db.refresh(emp)
        return emp

    # ---------- 状态流转 ----------

    @staticmethod
    async def transition_status(
        db: AsyncSession, employee_id: int, transition: StatusTransition
    ) -> Employee:
        """
        员工生命周期状态机变更。

        合法路径（见模块级常量 VALID_TRANSITIONS）：
            待入职(PENDING) → 试用期(ON_PROBATION)
            试用期(ON_PROBATION) → 在职(ACTIVE) | 离职(LEFT)
            在职(ACTIVE) → 离职(LEFT)
            离职(LEFT) 为终态，不可流转

        特殊规则：
        - 目标状态为 LEFT（离职）时，必须提供 leave_date，否则 400
        - 离职时将 is_active 置为 False（等效软删除，后续查询默认过滤）
        - leave_reason 可选

        参数：
            db          - 异步数据库会话
            employee_id - 目标员工主键 ID
            transition  - StatusTransition schema，含 new_status、leave_date、leave_reason

        返回：
            更新状态后的 Employee ORM 对象

        异常：
            HTTPException(404) - 员工不存在
            HTTPException(400) - 非法状态转换 / 离职缺少 leave_date
        """
        emp = await EmployeeService.get_by_id(db, employee_id)
        current = emp.status
        target = transition.new_status

        # 查找当前状态的合法目标列表，不在列表中则拒绝
        allowed = VALID_TRANSITIONS.get(current, [])
        if target not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"不允许从 '{current}' 变更到 '{target}'，合法目标：{allowed}",
            )

        emp.status = target

        # 离职时记录日期和原因，并将员工标记为不活跃（软删除语义）
        if target == EmployeeStatus.LEFT.value:
            if not transition.leave_date:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="离职操作必须提供离职日期",
                )
            EmployeeService._validate_leave_date_not_before_hire(emp, transition.leave_date)
            emp.leave_date = transition.leave_date
            emp.leave_reason = transition.leave_reason
            emp.is_active = False  # 软删除：后续 get_list 默认 is_active=True 过滤掉此员工

        await db.flush()
        await db.refresh(emp)
        return emp

    # ---------- Excel 导出 ----------

    @staticmethod
    async def export_excel(
        db: AsyncSession,
        *,
        keyword: Optional[str] = None,
        company: Optional[str] = None,
        department_id: Optional[int] = None,
        department_ids: Optional[List[int]] = None,
        location_id: Optional[int] = None,
        status: Optional[str] = None,
        employment_type: Optional[str] = None,
        is_field_staff: Optional[bool] = None,
        position: Optional[str] = None,
        education: Optional[str] = None,
        hire_date_from: Optional[date] = None,
        hire_date_to: Optional[date] = None,
    ) -> io.BytesIO:
        """
        按花名册格式导出员工数据为 Excel（.xlsx），在职和离职员工分别写入两个 sheet。

        数据流：
            1. 复用 get_list() 全量拉取（page_size=10000）过滤后的员工
            2. 按 status 分割为在职列表和离职列表
            3. 使用 openpyxl 分别写入"在职员工花名册"和"离职员工花名册"两个 sheet
            4. 返回 BytesIO 对象，由 API 层以 StreamingResponse 返回给前端

        在职 sheet 列结构（双行表头）：
            主标题行（深蓝底白字）+ 子表头行（浅蓝底加粗）+ 数据行
            包含：序号/姓名/身份证/部门/职位/手机/入职日期/合同信息/工作经历/入职资料收集 等约50列

        离职 sheet 列结构（单行表头）：
            包含：序号/姓名/入职日期/离职日期/离职原因/合同信息 等约30列

        内部辅助函数：
            _s(v)                     - 将任意值转为字符串（None→""，bool→"是/否"）
            _date(v)                  - 将日期转为 YYYY-MM-DD 字符串
            _get_dept_levels(emp)     - 提取一/二/三级部门名称
            _get_active_contract(emp) - 取最新合同（合同列表已按 start_date desc 排序）
            _get_work_exp_segment(emp, idx) - 取第 idx 段工作经历的(起止日期/单位/职务/离职原因)
            _get_emergency(emp)       - 优先从 family_members.is_emergency_contact 取紧急联系人

        参数：
            db          - 异步数据库会话
            （其余参数与 get_list 一致，用于过滤导出范围）

        返回：
            io.BytesIO，调用方需 seek(0) 后方可读取（本方法内已执行 seek）
        """
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        # 取全量数据（不分页）
        _, all_items = await EmployeeService.get_list(
            db,
            keyword=keyword,
            company=company,
            department_id=department_id,
            department_ids=department_ids,
            location_id=location_id,
            status=status,
            employment_type=employment_type,
            is_field_staff=is_field_staff,
            position=position,
            education=education,
            hire_date_from=hire_date_from,
            hire_date_to=hire_date_to,
            page=1,
            page_size=10000,
        )

        # 按状态分组（在职包含试用期/待入职，离职单独一组）
        active_emps = [e for e in all_items if e.status != "离职"]
        left_emps = [e for e in all_items if e.status == "离职"]

        wb = Workbook()

        # ---- 工具函数 ----
        def _s(v) -> str:
            """将任意值转为安全字符串：None→""，bool→"是/否"，其余 str()"""
            if v is None:
                return ""
            if isinstance(v, bool):
                return "是" if v else "否"
            return str(v)

        def _date(v) -> str:
            """将日期对象或字符串截取为 YYYY-MM-DD 格式，None→"""""
            if v is None:
                return ""
            return str(v)[:10]  # YYYY-MM-DD

        def _get_dept_levels(emp):
            """返回 (一级部门, 二级部门, 三级部门)，当前实现仅取直属部门名和 third_level_dept 字段"""
            dept = emp.department
            if not dept:
                return "", "", _s(emp.third_level_dept)
            # 仅有 department 对象，没有递归关系；用 third_level_dept 字段
            return dept.name, "", _s(emp.third_level_dept)

        def _get_active_contract(emp):
            """返回最新合同（合同列表已按 start_date desc 排序，取第一条）"""
            if not emp.contracts:
                return None
            # 已按 start_date desc 排序
            return emp.contracts[0]

        def _get_work_exp_segment(emp, idx: int):
            """返回第 idx 段工作经历 (起止日期, 工作单位, 职务, 离职原因)，不足则返回空字符串"""
            exps = emp.work_experiences or []
            if idx < len(exps):
                e = exps[idx]
                period = f"{_date(e.start_date)}~{_date(e.end_date) or '至今'}"
                return period, _s(e.company_name), _s(e.position), _s(e.leave_reason)
            return "", "", "", ""

        def _get_emergency(emp):
            """返回 (紧急联系人姓名, 联系方式) —— 优先 family_members 中 is_emergency_contact 字段"""
            for fm in (emp.family_members or []):
                if getattr(fm, "is_emergency_contact", False):
                    return _s(fm.name), _s(fm.phone)
            # fallback 到员工表冗余字段
            return _s(emp.emergency_contact), _s(emp.emergency_phone)

        # 样式定义
        thin = Side(style="thin")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        sub_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True, size=10)
        sub_font = Font(bold=True, size=9)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)

        def _write_sheet_active(ws, emps):
            """
            写入在职员工花名册 sheet。
            第1行：主标题（深蓝底白字）
            第2行：子标题（浅蓝底加粗，对工作经历、资料收集列做细分说明）
            第3行起：数据行
            列数约 50 列，含工作经历（最多2段）和 14 列入职资料收集（留白供勾选）
            """
            ws.row_dimensions[1].height = 20
            ws.row_dimensions[2].height = 20
            # 第一行主标题
            MAIN_HEADERS = [
                "序号", "姓名", "身份证号", "所属公司", "一级部门", "二级部门", "三级部门",
                "职位", "手机号码", "入职日期", "银行帐号", "工号", "性别",
                "户口所在地/国家", "族别", "婚姻状态", "出生日期", "最高学历",
                "毕业院校", "所学专业", "现居住地", "邮箱", "户口性质", "在职状态",
                "试用期截止", "合同签订时间", "合同到期时间", "合同到期提醒",
                "合同续签时间", "合同续签到期时间", "合同续签到期提醒",
                "紧急联系人", "联系方式",
                "第一段工作经历", "", "", "",
                "第二段工作经历", "", "", "",
                "入职资料收集", "", "", "", "", "", "", "", "", "", "", "", "", "",
                "备注",
            ]
            SUB_HEADERS = [
                "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
                "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
                "起止日期", "工作单位", "职务", "离职原因",
                "起止日期", "工作单位", "职务", "离职原因",
                "员工信息表", "身份证复印件", "银行卡复印件", "离职证明", "学历复印件",
                "学位复印件", "体检报告", "劳动合同书", "保密及竞业协议", "实习协议",
                "其他2", "其他3", "其他4", "录用审批", "转正审批",
                "",
            ]
            for col, h in enumerate(MAIN_HEADERS, 1):
                cell = ws.cell(row=1, column=col, value=h)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = center
                cell.border = border
                ws.column_dimensions[cell.column_letter].width = 14
            for col, h in enumerate(SUB_HEADERS, 1):
                cell = ws.cell(row=2, column=col, value=h)
                cell.font = sub_font
                cell.fill = sub_fill
                cell.alignment = center
                cell.border = border

            for r, emp in enumerate(emps, 3):
                contract = _get_active_contract(emp)
                e1 = _get_work_exp_segment(emp, 0)
                e2 = _get_work_exp_segment(emp, 1)
                ec_name, ec_phone = _get_emergency(emp)
                dept1, dept2, dept3 = _get_dept_levels(emp)
                row_data = [
                    r - 2, emp.name, _s(emp.id_card_no), _s(emp.company),
                    dept1, dept2, dept3,
                    _s(emp.position), emp.phone, _date(emp.hire_date),
                    _s(emp.bank_account), emp.employee_no, _s(emp.gender),
                    _s(emp.hukou_location), _s(emp.ethnicity), _s(emp.marital_status),
                    _date(emp.birth_date), _s(emp.education), _s(emp.school), _s(emp.major),
                    _s(emp.current_address), _s(emp.email), _s(emp.hukou_type),
                    emp.status,
                    _date(emp.probation_end_date),
                    _date(contract.start_date) if contract else "",
                    _date(contract.end_date) if contract else "",
                    _date(getattr(contract, "renewal_reminder", None)) if contract else "",
                    "", "", "",  # 续签时间/到期/提醒 (若需二次合同可扩展)
                    ec_name, ec_phone,
                    *e1, *e2,
                    "", "", "", "", "", "", "", "", "", "", "", "", "", "",  # 14个文件收集列
                    _s(emp.remark),
                ]
                for col, value in enumerate(row_data, 1):
                    cell = ws.cell(row=r, column=col, value=value)
                    cell.alignment = Alignment(vertical="center", wrap_text=False)
                    cell.border = border
                ws.row_dimensions[r].height = 16

        def _write_sheet_left(ws, emps):
            """
            写入离职员工花名册 sheet（单行表头，约 30 列）。
            相比在职 sheet 增加"离职日期"和"离职原因"列，删除入职资料收集列。
            """
            ws.row_dimensions[1].height = 20
            HEADERS = [
                "序号", "姓名", "身份证号", "所属公司", "部门", "二级部门", "职位",
                "手机号码", "入职日期", "离职日期", "离职原因",
                "银行帐号", "工号", "性别", "户口所在地", "族别", "婚姻状态",
                "出生日期", "最高学历", "毕业院校", "所学专业", "现居住地", "邮箱",
                "户口性质", "在职状态", "试用期截止", "合同签订时间", "合同到期时间",
                "紧急联系人", "联系方式",
            ]
            for col, h in enumerate(HEADERS, 1):
                cell = ws.cell(row=1, column=col, value=h)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = center
                cell.border = border
                ws.column_dimensions[cell.column_letter].width = 14

            for r, emp in enumerate(emps, 2):
                contract = _get_active_contract(emp)
                ec_name, ec_phone = _get_emergency(emp)
                dept1, _, _ = _get_dept_levels(emp)
                row_data = [
                    r - 1, emp.name, _s(emp.id_card_no), _s(emp.company),
                    dept1, "",
                    _s(emp.position), emp.phone,
                    _date(emp.hire_date), _date(emp.leave_date), _s(emp.leave_reason),
                    _s(emp.bank_account), emp.employee_no, _s(emp.gender),
                    _s(emp.hukou_location), _s(emp.ethnicity), _s(emp.marital_status),
                    _date(emp.birth_date), _s(emp.education), _s(emp.school), _s(emp.major),
                    _s(emp.current_address), _s(emp.email), _s(emp.hukou_type),
                    emp.status,
                    _date(emp.probation_end_date),
                    _date(contract.start_date) if contract else "",
                    _date(contract.end_date) if contract else "",
                    ec_name, ec_phone,
                ]
                for col, value in enumerate(row_data, 1):
                    cell = ws.cell(row=r, column=col, value=value)
                    cell.alignment = Alignment(vertical="center")
                    cell.border = border
                ws.row_dimensions[r].height = 16

        # 在职 sheet（默认第一个 sheet）
        ws_active = wb.active
        ws_active.title = "在职员工花名册"
        ws_active.freeze_panes = "C3"  # 冻结前两列（序号/姓名）和表头两行
        _write_sheet_active(ws_active, active_emps)

        # 离职 sheet
        ws_left = wb.create_sheet("离职员工花名册")
        ws_left.freeze_panes = "C2"  # 冻结前两列和表头一行
        _write_sheet_left(ws_left, left_emps)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)  # 重置指针，调用方可直接 read()
        return output

    # ---------- Excel 导入 ----------

    @staticmethod
    def generate_import_template() -> io.BytesIO:
        """
        生成标准员工导入模板 Excel（.xlsx）。

        模板格式：
        - 第1行：蓝紫色标题行（加粗白字），带星号的列为必填项
        - 第2行：灰色斜体示例数据行（仅供参考，导入时跳过）
        - 列含：工号/姓名/性别/手机号/密码/邮箱/身份证号/出生日期/部门ID/
                职位/职级/入职日期/学历/用工类型/状态

        返回：
            io.BytesIO，调用方 seek(0) 后可读取
        """
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment

        wb = Workbook()
        ws = wb.active
        ws.title = "员工导入模板"

        headers = [
            "工号*", "姓名*", "性别", "手机号*", "密码(可选，默认123456)",
            "邮箱", "身份证号", "出生日期(YYYY-MM-DD)",
            "部门ID", "职位", "职级", "入职日期(YYYY-MM-DD)",
            "最初工作时间(YYYY-MM-DD)", "人员所属公司", "学历", "用工类型", "状态",
        ]
        header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
            ws.column_dimensions[cell.column_letter].width = 22

        # 示例行（灰色斜体，导入时 data_start_idx=1 跳过）
        sample = [
            "EMP001", "张三", "男", "13800138001", "123456",
            "zhangsan@example.com", "110101199001011234", "1990-01-01",
            "", "工程师", "P4", "2024-01-01",
            "2018-07-01", "示例公司(上海分公司)", "本科", "正式", "待入职",
        ]
        sample_font = Font(color="808080", italic=True)
        for col, value in enumerate(sample, 1):
            cell = ws.cell(row=2, column=col, value=value)
            cell.font = sample_font

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output

    @staticmethod
    async def import_excel(db: AsyncSession, file_bytes: bytes) -> dict:
        """
        从 Excel 文件批量导入员工，支持两种格式：
            1. 标准导入模板（generate_import_template 生成）：单行表头，数据从第2行开始
            2. 花名册格式（export_excel 导出的花名册）：第一列标题为"序号"且列数>20，
               双行表头，数据从第3行开始

        支持文件格式：
            - .xlsx（ZIP magic bytes）：使用 openpyxl 解析
            - .xls（OLE2 magic bytes）：使用 xlrd 解析（需安装 xlrd 库）

        数据流：
            Step 1: 检测文件格式，读取所有行到 rows (list[list])
            Step 2: 判断是花名册还是导入模板，确定 data_start_idx（数据起始行）
            Step 3: 建立"列标题 → 模型字段名"映射（FIELD_MAP），生成 col_fields 列表
            Step 4: 逐行处理：日期解析 → 必填校验 → 唯一性校验 → savepoint 插入

        花名册格式特殊处理：
            - 自动生成工号（EMP + max(id)+success 序号，避免空工号）
            - 未提供密码时统一使用默认初始密码 "123456"

        每行失败不影响其他行（async with db.begin_nested() 创建 savepoint）

        参数：
            db         - 异步数据库会话
            file_bytes - 上传文件的原始字节

        返回：
            {"success": int, "failed": int, "errors": [{"row": int, "reason": str}]}

        异常：
            HTTPException(400) - 文件格式不支持 / 文件损坏
        """
        import datetime as _dt

        # ── Step 1: 检测文件格式并读取所有行 ──────────────────────────────────
        # OLE2 magic bytes → .xls；ZIP magic bytes → .xlsx
        is_xls = file_bytes[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'

        rows: list[list] = []  # list of rows, each row is a list of raw cell values

        if is_xls:
            try:
                import xlrd
            except ImportError:
                raise HTTPException(status_code=400, detail="服务器缺少 xlrd 库，无法解析 .xls 文件")
            book = xlrd.open_workbook(file_contents=file_bytes)
            sh = book.sheet_by_index(0)
            for r in range(sh.nrows):
                row_vals = []
                for c in range(sh.ncols):
                    cell = sh.cell(r, c)
                    # xlrd 将日期存为浮点数，需转为 datetime
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            val = xlrd.xldate_as_datetime(cell.value, book.datemode)
                        except Exception:
                            val = cell.value
                    # 整数浮点（如工号"1.0"）转为 int 避免变成"1.0"字符串
                    elif cell.ctype == xlrd.XL_CELL_FLOAT and cell.value == int(cell.value):
                        val = int(cell.value)
                    else:
                        val = cell.value
                    row_vals.append(val)
                rows.append(row_vals)
        else:
            from openpyxl import load_workbook
            try:
                wb = load_workbook(io.BytesIO(file_bytes))
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"无法解析文件，请确认为有效的 Excel 文件（.xlsx 或 .xls）：{e}")
            ws = wb.active
            for r in ws.iter_rows(values_only=True):
                rows.append(list(r))

        if not rows:
            return {"success": 0, "failed": 0, "errors": []}

        # ── Step 2: 判断是花名册格式还是导入模板格式 ──────────────────────────
        # 花名册特征：第一列标题为"序号"，且列数 > 20
        first_cell = str(rows[0][0] or '').strip()
        is_huamingce = first_cell == '序号' and len(rows[0]) > 20

        header_row_idx = 0
        data_start_idx = 2 if is_huamingce else 1  # 花名册数据从第3行开始（0-indexed=2）

        # ── Step 3: 建立列→字段映射 ───────────────────────────────────────────
        # FIELD_MAP 将 Excel 表头（中文/带星号）映射到 Employee 模型字段名
        FIELD_MAP = {
            "工号": "employee_no",
            "姓名": "name",
            "性别": "gender",
            "手机号": "phone",
            "手机号码": "phone",
            "密码": "password",
            "邮箱": "email",
            "身份证号": "id_card_no",
            "出生日期": "birth_date",
            "部门ID": "department_id",
            "职位": "position",
            "职级": "job_level",
            "入职日期": "hire_date",
            "学历": "education",
            "最高学历": "education",
            "用工类型": "employment_type",
            "状态": "status",
            "在职状态": "status",
            "所属公司": "company",
            "人员所属公司": "company",
            "最初工作时间": "first_work_date",
            "首次参加工作时间": "first_work_date",
            "银行帐号": "bank_account",
            "户口所在地": "hukou_location",
            "户口所在地/国家": "hukou_location",
            "族别": "ethnicity",
            "婚姻状态": "marital_status",
            "毕业院校": "school",
            "所学专业": "major",
            "现居住地": "current_address",
            "户口性质": "hukou_type",
            "试用期截止": "probation_end_date",
            "备注": "remark",
        }

        header_raw = rows[header_row_idx] if rows else []
        col_fields: list = []
        for h in header_raw:
            if h is None:
                col_fields.append(None)
            else:
                # 清理标题：去空格、去星号（必填标记）、取括号前内容
                cleaned = str(h).strip().rstrip("*").split("(")[0].split("（")[0].strip()
                col_fields.append(FIELD_MAP.get(cleaned))

        DATE_FIELDS = {"birth_date", "hire_date", "probation_end_date", "first_work_date"}
        # 获取 Employee 表所有合法列名，过滤花名册多余列
        valid_cols = {c.key for c in Employee.__table__.columns}

        # ── Step 4: 逐行处理 ──────────────────────────────────────────────────
        errors: list = []
        success = 0
        created_employee_ids: list[int] = []

        for row_idx in range(data_start_idx, len(rows)):
            row = rows[row_idx]
            row_data: dict = {}
            # 按列映射提取字段值，None 或空字符串跳过
            for col_idx, field in enumerate(col_fields):
                if field is None or col_idx >= len(row):
                    continue
                val = row[col_idx]
                if val is None or str(val).strip() == '':
                    continue
                row_data[field] = val

            if not row_data:
                continue  # 空行跳过

            display_row = row_idx + 1  # 转换为 Excel 1-indexed 行号，便于错误提示

            # 日期字段解析（支持 datetime 对象、date 对象、字符串"YYYY/MM/DD"等格式）
            for df in DATE_FIELDS:
                if df not in row_data:
                    continue
                v = row_data[df]
                if isinstance(v, _dt.datetime):
                    row_data[df] = v.date()
                elif isinstance(v, _dt.date):
                    pass  # 已是 date 类型，无需转换
                else:
                    s = str(v).strip()
                    try:
                        parts = s.replace("/", "-").split("-")
                        row_data[df] = _dt.date(int(parts[0]), int(parts[1]), int(parts[2]))
                    except Exception:
                        row_data.pop(df)  # 日期格式无法解析时忽略该字段

            # 非日期字段统一转字符串并去除首尾空格
            for k in list(row_data.keys()):
                if k not in DATE_FIELDS:
                    row_data[k] = str(row_data[k]).strip()

            # 部门ID 从字符串/浮点数转为整数
            if v := row_data.get("department_id"):
                try:
                    row_data["department_id"] = int(float(v))
                except Exception:
                    row_data.pop("department_id")

            # 必填字段校验（姓名和手机号为最低要求）
            if not row_data.get("name") or not row_data.get("phone"):
                errors.append({"row": display_row, "reason": "缺少必填字段: 姓名 或 手机号"})
                continue

            if is_huamingce:
                # 花名册格式：自动生成工号
                # 使用 max(employee_no) 提取序号而非 max(id)，防止并发导入时 id 预测冲突
                if not row_data.get("employee_no"):
                    max_no_result = await db.execute(
                        select(func.max(Employee.employee_no))
                        .where(Employee.employee_no.like("EMP%"))
                    )
                    max_no = max_no_result.scalar()
                    if max_no and max_no.startswith("EMP"):
                        try:
                            seq = int(max_no[3:]) + success + 1
                        except ValueError:
                            seq = success + 1
                    else:
                        seq = success + 1
                    row_data["employee_no"] = f"EMP{seq:04d}"
                # 花名册格式：未提供密码时统一使用系统初始密码。
                row_data.setdefault("password", DEFAULT_INITIAL_PASSWORD)
            else:
                # 导入模板格式：工号、姓名、手机号必填；密码不填时使用统一初始密码。
                required = ["employee_no", "name", "phone"]
                missing = [f for f in required if not row_data.get(f)]
                if missing:
                    errors.append({"row": display_row, "reason": f"缺少必填字段: {', '.join(missing)}"})
                    continue

            # 工号唯一性检查（查库）
            if row_data.get("employee_no"):
                exists_no = (await db.execute(
                    select(func.count()).select_from(Employee)
                    .where(Employee.employee_no == row_data["employee_no"])
                )).scalar()
                if exists_no > 0:
                    errors.append({"row": display_row, "reason": f"工号 '{row_data['employee_no']}' 已存在"})
                    continue

            # 手机号唯一性检查（查库）
            exists_phone = (await db.execute(
                select(func.count()).select_from(Employee)
                .where(Employee.phone == row_data["phone"])
            )).scalar()
            if exists_phone > 0:
                errors.append({"row": display_row, "reason": f"手机号 '{row_data['phone']}' 已存在"})
                continue

            # 创建员工（savepoint 保证单行失败不影响其他行）
            try:
                password = row_data.pop("password", DEFAULT_INITIAL_PASSWORD)
                emp_data = {k: v for k, v in row_data.items() if v != "" and v is not None}
                emp_data.setdefault("status", "待入职")
                emp_data.setdefault("employment_type", "正式")
                emp_data.setdefault("is_field_staff", False)
                emp_data["hashed_password"] = get_password_hash(password)
                # 只保留 Employee 模型中存在的列，忽略花名册多余列（如"入职资料收集"等）
                emp_data = {k: v for k, v in emp_data.items() if k in valid_cols}

                async with db.begin_nested():  # SAVEPOINT，单行失败可回滚到此处
                    emp = Employee(**emp_data)
                    db.add(emp)
                    await db.flush()
                    created_employee_ids.append(int(emp.id))
                success += 1
            except Exception as exc:
                errors.append({"row": display_row, "reason": str(exc)})

        await EmployeeService._sync_leave_balances_for_employees(db, created_employee_ids)
        return {"success": success, "failed": len(errors), "errors": errors}

    # ---------- 批量操作 ----------

    @staticmethod
    async def bulk_offboard(
        db: AsyncSession,
        employee_ids: List[int],
        leave_date: date,
        leave_reason: Optional[str] = None,
    ) -> int:
        """
        批量离职处理。

        使用单条 UPDATE 语句高效处理多员工离职，避免逐条查询。
        只对状态为 ACTIVE / ON_PROBATION / PENDING 的员工生效（已离职的跳过）。
        同时将 is_active 置为 False（软删除，后续默认查询不再返回）。

        参数：
            db           - 异步数据库会话
            employee_ids - 待离职员工 ID 列表
            leave_date   - 统一离职日期
            leave_reason - 离职原因（可选）

        返回：
            实际更新的员工数量（已离职员工不计入）
        """
        active_stmt = (
            select(Employee)
            .where(Employee.id.in_(employee_ids))
            .where(Employee.status.in_([
                EmployeeStatus.ACTIVE.value,
                EmployeeStatus.ON_PROBATION.value,
                EmployeeStatus.PENDING.value,
            ]))
        )
        employees = (await db.execute(active_stmt)).scalars().all()
        for emp in employees:
            EmployeeService._validate_leave_date_not_before_hire(emp, leave_date)
        if not employees:
            return 0

        stmt = (
            update(Employee)
            .where(Employee.id.in_([emp.id for emp in employees]))
            .where(Employee.status.in_([
                EmployeeStatus.ACTIVE.value,
                EmployeeStatus.ON_PROBATION.value,
                EmployeeStatus.PENDING.value,
            ]))
            .values(
                status=EmployeeStatus.LEFT.value,
                leave_date=leave_date,
                leave_reason=leave_reason,
                is_active=False,
            )
        )
        result = await db.execute(stmt)
        await db.flush()
        await EmployeeService._sync_leave_balances_for_employees(db, [int(emp.id) for emp in employees])
        return result.rowcount

    @staticmethod
    async def bulk_transfer(
        db: AsyncSession,
        employee_ids: List[int],
        department_id: Optional[int] = None,
        position: Optional[str] = None,
        effective_date: Optional[date] = None,
        operator_id: Optional[int] = None,
        remark: Optional[str] = None,
    ) -> int:
        """
        批量调岗：将多名员工转移到新部门和/或新职位。

        业务规则：
        - department_id 和 position 至少提供一个，否则 400
        - 若提供 department_id，需校验目标部门存在且处于启用状态
        - 只对非离职员工生效（status != LEFT）

        参数：
            db            - 异步数据库会话
            employee_ids  - 待调岗员工 ID 列表
            department_id - 目标部门 ID（可选）
            position      - 新职位名称（可选）

        返回：
            实际更新的员工数量

        异常：
            HTTPException(400) - 未提供任何更新字段 / 目标部门不存在或已停用
        """
        if not department_id and not position:
            raise HTTPException(status_code=400, detail="必须提供新部门或新职位")
        if department_id is not None:
            # 校验目标部门存在且活跃
            dept = await db.execute(
                select(Department).where(Department.id == department_id, Department.is_active == True)
            )
            if dept.scalar_one_or_none() is None:
                raise HTTPException(status_code=400, detail=f"部门 ID={department_id} 不存在或已停用")
        effective_on = effective_date or date.today()
        apply_now = effective_on <= date.today()
        position_clean = position.strip() if isinstance(position, str) else position
        stmt = (
            select(Employee)
            .where(Employee.id.in_(employee_ids))
            .where(Employee.status != EmployeeStatus.LEFT.value)
        )
        employees = (await db.execute(stmt)).scalars().all()
        if not employees:
            return 0

        updated_count = 0
        for emp in employees:
            will_change = (
                (department_id is not None and emp.department_id != department_id)
                or (bool(position_clean) and emp.position != position_clean)
            )
            if not will_change:
                continue
            await EmployeeService._write_department_change_log(
                db,
                employee=emp,
                to_department_id=department_id,
                to_position=position_clean,
                effective_date=effective_on,
                operator_id=operator_id,
                remark=remark,
            )
            if not apply_now:
                updated_count += 1
                continue
            changed = False
            if department_id is not None and emp.department_id != department_id:
                await EmployeeService._sync_manager_after_transfer(
                    db,
                    employee=emp,
                    new_department_id=department_id,
                )
                emp.department_id = department_id
                changed = True
            if position_clean and emp.position != position_clean:
                emp.position = position_clean
                changed = True
            if changed:
                updated_count += 1

        await db.flush()
        if apply_now:
            await EmployeeService._sync_leave_balances_for_employees(db, [int(emp.id) for emp in employees])
        return updated_count

    @staticmethod
    async def transfer_employee(
        db: AsyncSession,
        employee_id: int,
        *,
        department_id: Optional[int] = None,
        position: Optional[str] = None,
        effective_date: Optional[date] = None,
        operator_id: Optional[int] = None,
        remark: Optional[str] = None,
    ) -> Employee:
        """
        单员工调岗，写入部门/职位变更日志。
        """
        if department_id is None and not (position and position.strip()):
            raise HTTPException(status_code=400, detail="必须提供新部门或新职位")

        emp = await EmployeeService.get_by_id(db, employee_id)
        if emp.status == EmployeeStatus.LEFT.value:
            raise HTTPException(status_code=400, detail="离职员工不可调岗")

        if department_id is not None:
            dept = await db.execute(
                select(Department).where(Department.id == department_id, Department.is_active == True)
            )
            if dept.scalar_one_or_none() is None:
                raise HTTPException(status_code=400, detail=f"部门 ID={department_id} 不存在或已停用")

        effective_on = effective_date or date.today()
        apply_now = effective_on <= date.today()
        position_clean = position.strip() if isinstance(position, str) else position
        will_change = (
            (department_id is not None and emp.department_id != department_id)
            or (bool(position_clean) and emp.position != position_clean)
        )
        if not will_change:
            return emp
        await EmployeeService._write_department_change_log(
            db,
            employee=emp,
            to_department_id=department_id,
            to_position=position_clean,
            effective_date=effective_on,
            operator_id=operator_id,
            remark=remark,
        )

        if not apply_now:
            await db.flush()
            await db.refresh(emp)
            return emp

        if department_id is not None:
            await EmployeeService._sync_manager_after_transfer(
                db,
                employee=emp,
                new_department_id=department_id,
            )
            emp.department_id = department_id
        if position_clean:
            emp.position = position_clean

        await db.flush()
        await EmployeeService._sync_leave_balances_for_employees(db, [emp.id])
        await db.refresh(emp)
        return emp

    # ---------- 预警 ----------

    @staticmethod
    async def get_alerts(db: AsyncSession) -> List[dict]:
        """
        获取管理驾驶舱预警列表，包含三类预警事件：

        1. 合同即将到期（≤45天）
           - 查询状态为 ACTIVE 的合同（非已终止），到期日期在今日到45天后之间
           - 关联员工信息，只取 is_active=True 的员工
           - 返回 alert_type="contract_expiry"

        2. 试用期即将结束（≤14天）
           - 查询状态为 ON_PROBATION 且 probation_end_date 在今日到14天后的员工
           - 返回 alert_type="probation_end"，提示 HR 及时办理转正

        3. 本月生日
           - 通过 PostgreSQL extract("month", ...) 函数按月份匹配
           - 只查在职员工（is_active=True 且 status != LEFT）
           - 返回 alert_type="birthday"，无 days_remaining

        参数：
            db - 异步数据库会话

        返回：
            List[dict]，每项含：
                employee_id, employee_no, employee_name,
                alert_type, alert_message, alert_date, days_remaining (生日预警为 None)
        """
        today = date.today()
        alerts: List[dict] = []

        # 1. 合同即将到期（45天内）
        # 查询活跃合同（status=ACTIVE）且到期日期在 [today, today+45] 的记录
        contract_deadline = today + timedelta(days=CONTRACT_EXPIRY_WARNING_DAYS)
        stmt = (
            select(EmployeeContract, Employee)
            .join(Employee, EmployeeContract.employee_id == Employee.id)
            .where(
                and_(
                    EmployeeContract.status == ContractStatus.ACTIVE.value,
                    EmployeeContract.end_date.isnot(None),
                    EmployeeContract.end_date >= today,
                    EmployeeContract.end_date <= contract_deadline,
                    Employee.is_active == True,
                )
            )
            .order_by(EmployeeContract.end_date)  # 按到期日期升序，最紧急的在前
        )
        for contract, emp in (await db.execute(stmt)).all():
            days_left = (contract.end_date - today).days
            alerts.append({
                "employee_id": emp.id,
                "employee_no": emp.employee_no,
                "employee_name": emp.name,
                "alert_type": "contract_expiry",
                "alert_message": f"合同将于 {contract.end_date} 到期（还剩 {days_left} 天）",
                "alert_date": contract.end_date,
                "days_remaining": days_left,
            })

        # 2. 试用期即将结束（14天内）
        # 查询状态为试用期且 probation_end_date 在 [today, today+14] 的员工
        prob_deadline = today + timedelta(days=14)
        prob_stmt = (
            select(Employee)
            .where(
                and_(
                    Employee.status == EmployeeStatus.ON_PROBATION.value,
                    Employee.probation_end_date.isnot(None),
                    Employee.probation_end_date >= today,
                    Employee.probation_end_date <= prob_deadline,
                    Employee.is_active == True,
                )
            )
        )
        for emp in (await db.execute(prob_stmt)).scalars().all():
            days_left = (emp.probation_end_date - today).days
            alerts.append({
                "employee_id": emp.id,
                "employee_no": emp.employee_no,
                "employee_name": emp.name,
                "alert_type": "probation_end",
                "alert_message": f"试用期将于 {emp.probation_end_date} 结束（还剩 {days_left} 天），请及时转正",
                "alert_date": emp.probation_end_date,
                "days_remaining": days_left,
            })

        # 3. 本月生日（使用 PostgreSQL extract 函数按月份过滤）
        bday_stmt = (
            select(Employee)
            .where(
                and_(
                    Employee.birth_date.isnot(None),
                    extract("month", Employee.birth_date) == today.month,  # 月份匹配
                    Employee.is_active == True,
                    Employee.status != EmployeeStatus.LEFT.value,
                )
            )
        )
        for emp in (await db.execute(bday_stmt)).scalars().all():
            alerts.append({
                "employee_id": emp.id,
                "employee_no": emp.employee_no,
                "employee_name": emp.name,
                "alert_type": "birthday",
                "alert_message": f"本月生日（{emp.birth_date.strftime('%m-%d')}），祝生日快乐！",
                "alert_date": emp.birth_date,
                "days_remaining": None,  # 生日预警无剩余天数概念
            })

        return alerts

    # ---------- 认证 ----------

    @staticmethod
    async def authenticate(db: AsyncSession, phone: str, password: str) -> Optional[Employee]:
        """
        手机号 + 密码登录验证（用于 POST /api/v1/auth/login）。

        验证流程：
            1. 按手机号查找员工（get_by_phone）
            2. 检查 is_active（已离职员工不允许登录）
            3. 检查 hashed_password 是否存在（防止未设密码的账号）
            4. 使用 bcrypt 比对明文密码与 hash（verify_password）

        安全说明：
            - 无论是用户不存在、已离职还是密码错误，均返回 None（不区分具体原因，防枚举）
            - 调用方（auth.py）根据 None 返回 401

        参数：
            db       - 异步数据库会话
            phone    - 登录手机号
            password - 登录密码（明文）

        返回：
            验证成功返回 Employee 对象，失败返回 None

        下游调用：
            get_by_phone() → verify_password()（来自 app.core.security）
        """
        emp = await EmployeeService.get_by_phone(db, phone)
        if not emp:
            return None
        if not emp.is_active:
            return None
        if not emp.hashed_password:
            return None
        if not verify_password(password, emp.hashed_password):
            return None
        return emp


# ==================== Contract Service ====================

CONTRACT_EXPIRY_WARNING_DAYS = 45
CONTRACT_RENEWAL_REMINDER_DAYS = 30


class ContractService:
    """
    员工劳动合同 Service。

    负责合同的增删改查、续签及到期提醒。
    每名员工可有多份合同（历史合同），通过 start_date desc 排序取最新合同。

    合同状态：ACTIVE（生效）→ 续签（更新 end_date，renewal_count+1）
    被调用端点：/api/v1/employees/{id}/contracts/*
    """

    @staticmethod
    def _default_renewal_reminder(end_date: Optional[date]) -> Optional[date]:
        if not end_date:
            return None
        return end_date - timedelta(days=CONTRACT_RENEWAL_REMINDER_DAYS)

    @staticmethod
    def _validate_contract_dates(start_date: date, end_date: Optional[date]) -> None:
        if end_date and end_date < start_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="合同到期日不能早于合同开始日期",
            )

    @staticmethod
    async def get_by_employee(db: AsyncSession, employee_id: int) -> List[EmployeeContract]:
        """
        获取某员工的所有合同，按开始日期降序排列（最新合同在前）。

        参数：
            db          - 异步数据库会话
            employee_id - 员工主键 ID

        返回：
            EmployeeContract 列表（可能为空）
        """
        result = await db.execute(
            select(EmployeeContract)
            .where(EmployeeContract.employee_id == employee_id)
            .order_by(EmployeeContract.start_date.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, contract_id: int) -> EmployeeContract:
        """
        按主键 ID 获取合同。

        参数：
            db          - 异步数据库会话
            contract_id - 合同主键 ID

        返回：
            EmployeeContract ORM 对象

        异常：
            HTTPException(404) - 合同不存在
        """
        result = await db.execute(
            select(EmployeeContract).where(EmployeeContract.id == contract_id)
        )
        contract = result.scalar_one_or_none()
        if not contract:
            raise HTTPException(
                status_code=404,
                detail=f"合同 ID={contract_id} 不存在",
            )
        return contract

    @staticmethod
    async def create(db: AsyncSession, data: ContractCreate) -> EmployeeContract:
        """
        为员工创建新合同。

        业务规则：
        - 员工必须存在（校验 employee_id）
        - 如果提供了 contract_no，需全局唯一
        - 新合同初始状态为 ACTIVE

        参数：
            db   - 异步数据库会话
            data - ContractCreate schema（含 employee_id、start_date、end_date 等）

        返回：
            新创建的 EmployeeContract ORM 对象

        异常：
            HTTPException(400) - 员工不存在 / 合同编号重复
        """
        # 验证员工存在
        emp_exists = await db.execute(
            select(func.count()).select_from(Employee).where(Employee.id == data.employee_id)
        )
        if emp_exists.scalar() == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"员工 ID={data.employee_id} 不存在",
            )

        # 检查合同编号唯一性（可选字段，若提供则需唯一）
        if data.contract_no:
            no_exists = await db.execute(
                select(func.count())
                .select_from(EmployeeContract)
                .where(EmployeeContract.contract_no == data.contract_no)
            )
            if no_exists.scalar() > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"合同编号 '{data.contract_no}' 已存在",
                )

        ContractService._validate_contract_dates(data.start_date, data.end_date)

        payload = data.model_dump()
        if payload.get("contract_renewal_reminder") is None:
            payload["contract_renewal_reminder"] = ContractService._default_renewal_reminder(data.end_date)
        contract_status = payload.pop("status", None) or ContractStatus.ACTIVE.value
        allowed_statuses = {
            ContractStatus.ACTIVE.value,
            ContractStatus.EXPIRED.value,
            ContractStatus.TERMINATED.value,
        }
        if contract_status not in allowed_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="合同状态必须是：生效中、已到期或已解除",
            )

        contract = EmployeeContract(
            **payload,
            status=contract_status,
        )
        db.add(contract)
        await db.flush()
        await db.refresh(contract)
        return contract

    @staticmethod
    async def renew(
        db: AsyncSession, contract_id: int, data: ContractRenew
    ) -> EmployeeContract:
        """
        续签合同：更新到期日期，递增续签次数，可选更新签署日期。

        业务规则：
        - 只有状态为 ACTIVE 的合同才能续签
        - 续签不创建新合同，而是在原合同上修改 end_date 并累加 renewal_count

        参数：
            db          - 异步数据库会话
            contract_id - 待续签合同 ID
            data        - ContractRenew schema（含 new_end_date、可选 sign_date）

        返回：
            续签后的 EmployeeContract ORM 对象

        异常：
            HTTPException(404) - 合同不存在
            HTTPException(400) - 合同不处于生效状态（如已过期或已终止）
        """
        contract = await ContractService.get_by_id(db, contract_id)

        if contract.status != ContractStatus.ACTIVE.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只能续签处于生效中的合同",
            )

        ContractService._validate_contract_dates(contract.start_date, data.new_end_date)

        contract.end_date = data.new_end_date
        contract.contract_renewal_reminder = ContractService._default_renewal_reminder(data.new_end_date)
        contract.renewal_count += 1  # 续签计数加一
        if data.sign_date:
            contract.sign_date = data.sign_date

        await db.flush()
        await db.refresh(contract)
        return contract

    @staticmethod
    async def get_expiring(
        db: AsyncSession,
        days: int = CONTRACT_EXPIRY_WARNING_DAYS,
    ) -> List[dict]:
        """
        查询即将到期的合同（默认45天内到期）。

        与 EmployeeService.get_alerts 中合同预警逻辑类似，但本方法：
        - 可自定义 days 参数（不限于45天）
        - 不过滤 Employee.is_active（包含所有员工的到期合同）
        - 返回包含合同和员工详情的字典列表（用于专项合同到期管理页面）

        参数：
            db   - 异步数据库会话
            days - 提前预警天数，默认30

        返回：
            List[dict]，每项含：
                contract_id, contract_no, employee_id, employee_no,
                employee_name, end_date, days_remaining
        """
        today = date.today()
        deadline = today + timedelta(days=days)

        # 联表查询：合同 JOIN 员工，筛选活跃合同且到期日在 [today, deadline]
        stmt = (
            select(EmployeeContract, Employee)
            .join(Employee, EmployeeContract.employee_id == Employee.id)
            .where(
                and_(
                    EmployeeContract.status == ContractStatus.ACTIVE.value,
                    EmployeeContract.end_date.isnot(None),
                    EmployeeContract.end_date >= today,
                    EmployeeContract.end_date <= deadline,
                )
            )
            .order_by(EmployeeContract.end_date)  # 最先到期的在前
        )
        result = await db.execute(stmt)
        rows = result.all()

        expiring_list = []
        for contract, employee in rows:
            days_remaining = (contract.end_date - today).days
            expiring_list.append({
                "contract_id": contract.id,
                "contract_no": contract.contract_no,
                "employee_id": employee.id,
                "employee_no": employee.employee_no,
                "employee_name": employee.name,
                "end_date": contract.end_date,
                "days_remaining": days_remaining,
            })

        return expiring_list


# ==================== WorkExperience Service ====================

class WorkExperienceService:
    """
    员工工作经历 Service。

    管理员工的历史工作经历记录（可多条），支持排序（sort_order）。
    被调用端点：/api/v1/employees/{id}/work-experiences/*
    """

    @staticmethod
    async def get_by_employee(db: AsyncSession, employee_id: int) -> List[WorkExperience]:
        """
        获取员工工作经历列表，按 sort_order 升序、start_date 降序排列。

        参数：
            db          - 异步数据库会话
            employee_id - 员工主键 ID

        返回：
            WorkExperience 列表
        """
        result = await db.execute(
            select(WorkExperience)
            .where(WorkExperience.employee_id == employee_id)
            .order_by(WorkExperience.sort_order, WorkExperience.start_date.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, exp_id: int) -> WorkExperience:
        """
        按主键 ID 获取工作经历记录。

        异常：
            HTTPException(404) - 记录不存在
        """
        result = await db.execute(
            select(WorkExperience).where(WorkExperience.id == exp_id)
        )
        exp = result.scalar_one_or_none()
        if not exp:
            raise HTTPException(status_code=404, detail=f"工作经历 ID={exp_id} 不存在")
        return exp

    @staticmethod
    async def create(db: AsyncSession, data: WorkExperienceCreate) -> WorkExperience:
        """
        创建工作经历记录。

        参数：
            db   - 异步数据库会话
            data - WorkExperienceCreate schema

        返回：
            新建的 WorkExperience ORM 对象
        """
        exp = WorkExperience(**data.model_dump())
        db.add(exp)
        await db.flush()
        await db.refresh(exp)
        return exp

    @staticmethod
    async def update(
        db: AsyncSession, exp_id: int, data: WorkExperienceUpdate
    ) -> WorkExperience:
        """
        更新工作经历记录（部分更新）。

        参数：
            db     - 异步数据库会话
            exp_id - 工作经历主键 ID
            data   - WorkExperienceUpdate schema（仅含需更新的字段）

        返回：
            更新后的 WorkExperience ORM 对象

        异常：
            HTTPException(404) - 记录不存在
        """
        exp = await WorkExperienceService.get_by_id(db, exp_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(exp, field, value)
        await db.flush()
        await db.refresh(exp)
        return exp

    @staticmethod
    async def delete(db: AsyncSession, exp_id: int) -> None:
        """
        删除工作经历记录（硬删除）。

        参数：
            db     - 异步数据库会话
            exp_id - 工作经历主键 ID

        异常：
            HTTPException(404) - 记录不存在
        """
        exp = await WorkExperienceService.get_by_id(db, exp_id)
        await db.delete(exp)
        await db.flush()


# ==================== EmployeeDocument Service ====================

class EmployeeDocumentService:
    """
    员工档案材料 Service。

    管理员工上传的各类档案文件记录（非文件本体，仅文件元数据：名称、路径、类型等）。
    被调用端点：/api/v1/employees/{id}/documents/*
    """

    @staticmethod
    async def get_by_employee(db: AsyncSession, employee_id: int) -> List[EmployeeDocument]:
        """
        获取员工档案材料列表，按创建时间升序排列。

        参数：
            db          - 异步数据库会话
            employee_id - 员工主键 ID

        返回：
            EmployeeDocument 列表
        """
        result = await db.execute(
            select(EmployeeDocument)
            .where(EmployeeDocument.employee_id == employee_id)
            .order_by(EmployeeDocument.created_at)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, doc_id: int) -> EmployeeDocument:
        """
        按主键 ID 获取档案材料记录。

        异常：
            HTTPException(404) - 记录不存在
        """
        result = await db.execute(
            select(EmployeeDocument).where(EmployeeDocument.id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            raise HTTPException(status_code=404, detail=f"档案材料 ID={doc_id} 不存在")
        return doc

    @staticmethod
    async def create(db: AsyncSession, data: EmployeeDocumentCreate) -> EmployeeDocument:
        """
        创建档案材料记录。

        参数：
            db   - 异步数据库会话
            data - EmployeeDocumentCreate schema

        返回：
            新建的 EmployeeDocument ORM 对象
        """
        doc = EmployeeDocument(**data.model_dump())
        db.add(doc)
        await db.flush()
        await db.refresh(doc)
        return doc

    @staticmethod
    async def update(
        db: AsyncSession, doc_id: int, data: EmployeeDocumentUpdate
    ) -> EmployeeDocument:
        """
        更新档案材料记录（部分更新）。

        参数：
            db     - 异步数据库会话
            doc_id - 档案材料主键 ID
            data   - EmployeeDocumentUpdate schema

        返回：
            更新后的 EmployeeDocument ORM 对象

        异常：
            HTTPException(404) - 记录不存在
        """
        doc = await EmployeeDocumentService.get_by_id(db, doc_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(doc, field, value)
        await db.flush()
        await db.refresh(doc)
        return doc

    @staticmethod
    async def delete(db: AsyncSession, doc_id: int) -> None:
        """
        删除档案材料记录（硬删除，不删除实际文件）。

        参数：
            db     - 异步数据库会话
            doc_id - 档案材料主键 ID

        异常：
            HTTPException(404) - 记录不存在
        """
        doc = await EmployeeDocumentService.get_by_id(db, doc_id)
        await db.delete(doc)
        await db.flush()


# ==================== FamilyMember Service ====================

class FamilyMemberService:
    """
    员工家庭成员 Service。

    管理员工的家庭成员信息（配偶/父母/子女等），其中 is_emergency_contact 标记
    紧急联系人，导出花名册时优先使用该标记的成员作为紧急联系人。
    被调用端点：/api/v1/employees/{id}/family-members/*
    """

    @staticmethod
    async def get_by_employee(db: AsyncSession, employee_id: int) -> List[FamilyMember]:
        """
        获取员工家庭成员列表，按 ID 升序。

        参数：
            db          - 异步数据库会话
            employee_id - 员工主键 ID

        返回：
            FamilyMember 列表
        """
        result = await db.execute(
            select(FamilyMember)
            .where(FamilyMember.employee_id == employee_id)
            .order_by(FamilyMember.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, member_id: int) -> FamilyMember:
        """
        按主键 ID 获取家庭成员记录。

        异常：
            HTTPException(404) - 记录不存在
        """
        result = await db.execute(
            select(FamilyMember).where(FamilyMember.id == member_id)
        )
        member = result.scalar_one_or_none()
        if not member:
            raise HTTPException(status_code=404, detail=f"家庭成员 ID={member_id} 不存在")
        return member

    @staticmethod
    async def create(db: AsyncSession, data: FamilyMemberCreate) -> FamilyMember:
        """
        添加家庭成员记录。

        参数：
            db   - 异步数据库会话
            data - FamilyMemberCreate schema

        返回：
            新建的 FamilyMember ORM 对象
        """
        member = FamilyMember(**data.model_dump())
        db.add(member)
        await db.flush()
        await db.refresh(member)
        return member

    @staticmethod
    async def update(db: AsyncSession, member_id: int, data: FamilyMemberUpdate) -> FamilyMember:
        """
        更新家庭成员记录（部分更新）。

        参数：
            db        - 异步数据库会话
            member_id - 家庭成员主键 ID
            data      - FamilyMemberUpdate schema

        返回：
            更新后的 FamilyMember ORM 对象

        异常：
            HTTPException(404) - 记录不存在
        """
        member = await FamilyMemberService.get_by_id(db, member_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(member, field, value)
        await db.flush()
        await db.refresh(member)
        return member

    @staticmethod
    async def delete(db: AsyncSession, member_id: int) -> None:
        """
        删除家庭成员记录（硬删除）。

        参数：
            db        - 异步数据库会话
            member_id - 家庭成员主键 ID

        异常：
            HTTPException(404) - 记录不存在
        """
        member = await FamilyMemberService.get_by_id(db, member_id)
        await db.delete(member)
        await db.flush()


# ==================== EducationRecord Service ====================

class EducationRecordService:
    """
    员工教育背景 Service。

    管理员工的教育经历（可多条：高中/大专/本科/研究生等），支持排序。
    被调用端点：/api/v1/employees/{id}/education-records/*
    """

    @staticmethod
    async def get_by_employee(db: AsyncSession, employee_id: int) -> List[EducationRecord]:
        """
        获取员工教育背景列表，按 sort_order 升序、start_date 降序排列。

        参数：
            db          - 异步数据库会话
            employee_id - 员工主键 ID

        返回：
            EducationRecord 列表
        """
        result = await db.execute(
            select(EducationRecord)
            .where(EducationRecord.employee_id == employee_id)
            .order_by(EducationRecord.sort_order, EducationRecord.start_date.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, record_id: int) -> EducationRecord:
        """
        按主键 ID 获取教育背景记录。

        异常：
            HTTPException(404) - 记录不存在
        """
        result = await db.execute(
            select(EducationRecord).where(EducationRecord.id == record_id)
        )
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail=f"教育记录 ID={record_id} 不存在")
        return record

    @staticmethod
    async def create(db: AsyncSession, data: EducationRecordCreate) -> EducationRecord:
        """
        创建教育背景记录。

        参数：
            db   - 异步数据库会话
            data - EducationRecordCreate schema

        返回：
            新建的 EducationRecord ORM 对象
        """
        record = EducationRecord(**data.model_dump())
        db.add(record)
        await db.flush()
        await db.refresh(record)
        return record

    @staticmethod
    async def update(db: AsyncSession, record_id: int, data: EducationRecordUpdate) -> EducationRecord:
        """
        更新教育背景记录（部分更新）。

        参数：
            db        - 异步数据库会话
            record_id - 教育背景主键 ID
            data      - EducationRecordUpdate schema

        返回：
            更新后的 EducationRecord ORM 对象

        异常：
            HTTPException(404) - 记录不存在
        """
        record = await EducationRecordService.get_by_id(db, record_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(record, field, value)
        await db.flush()
        await db.refresh(record)
        return record

    @staticmethod
    async def delete(db: AsyncSession, record_id: int) -> None:
        """
        删除教育背景记录（硬删除）。

        参数：
            db        - 异步数据库会话
            record_id - 教育背景主键 ID

        异常：
            HTTPException(404) - 记录不存在
        """
        record = await EducationRecordService.get_by_id(db, record_id)
        await db.delete(record)
        await db.flush()


# ==================== EmployeeSkill Service ====================

class EmployeePersonalSkillService:
    """
    员工个人技能 Service。

    每名员工最多一条技能记录（含计算机水平、外语、其他技能、兴趣爱好等）。
    使用 upsert 模式：不存在则创建，存在则更新（非幂等 POST，而是语义上的 PUT）。
    被调用端点：/api/v1/employees/{id}/skills
    """

    @staticmethod
    async def get_by_employee(db: AsyncSession, employee_id: int) -> Optional[EmployeePersonalSkill]:
        """
        获取员工技能记录（每人至多一条）。

        参数：
            db          - 异步数据库会话
            employee_id - 员工主键 ID

        返回：
            EmployeePersonalSkill 对象，若未录入则返回 None
        """
        result = await db.execute(
            select(EmployeePersonalSkill).where(EmployeePersonalSkill.employee_id == employee_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert(db: AsyncSession, data: EmployeePersonalSkillCreate) -> EmployeePersonalSkill:
        """
        创建或更新员工技能（upsert 语义，每人仅一条记录）。

        逻辑：先查询该员工是否已有技能记录：
        - 若存在：对 data 中非 None 的字段逐一更新（不清空已有数据）
        - 若不存在：直接创建新记录

        参数：
            db   - 异步数据库会话
            data - EmployeePersonalSkillCreate schema（含 employee_id）

        返回：
            创建或更新后的 EmployeePersonalSkill ORM 对象

        下游调用：
            get_by_employee()
        """
        existing = await EmployeePersonalSkillService.get_by_employee(db, data.employee_id)
        if existing:
            # 已有记录：只更新 data 中非 None 的字段
            for field, value in data.model_dump(exclude={"employee_id"}, exclude_unset=False).items():
                if value is not None:
                    setattr(existing, field, value)
            await db.flush()
            await db.refresh(existing)
            return existing
        # 无记录：创建新记录
        skill = EmployeePersonalSkill(**data.model_dump())
        db.add(skill)
        await db.flush()
        await db.refresh(skill)
        return skill

    @staticmethod
    async def update(db: AsyncSession, employee_id: int, data: EmployeePersonalSkillUpdate) -> EmployeePersonalSkill:
        """
        按员工 ID 更新技能记录（部分更新，需记录已存在）。

        与 upsert 不同：本方法要求记录必须存在，否则 404。

        参数：
            db          - 异步数据库会话
            employee_id - 员工主键 ID
            data        - EmployeePersonalSkillUpdate schema

        返回：
            更新后的 EmployeePersonalSkill ORM 对象

        异常：
            HTTPException(404) - 员工尚未录入技能信息
        """
        skill = await EmployeePersonalSkillService.get_by_employee(db, employee_id)
        if not skill:
            raise HTTPException(status_code=404, detail="该员工尚未录入技能信息")
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(skill, field, value)
        await db.flush()
        await db.refresh(skill)
        return skill


# ==================== 员工信息登记表 XLS 导入 ====================

class RegistrationFormService:
    """
    员工信息登记表（标准 XLS 格式）导入 Service。

    与 EmployeeService.import_excel 不同：本 Service 专门处理"员工信息登记表"这类
    有固定行列坐标的结构化 XLS 表格（非通用花名册），直接按坐标读取单元格数据，
    一次导入一名员工并同时创建家庭成员、工作经历、教育背景、技能特长等附属数据。

    XLS 表格结构（行号为 xlrd 0-indexed）：
        rows 4-9  : 基本信息（姓名/性别/出生日期/学历/手机/身份证/地址等）
        rows 10-13: 家庭成员（最多3行数据）
        rows 14-17: 工作经历（最多3段）
        rows 18-21: 教育背景（最多3段）
        rows 22-23: 技能特长

    被调用端点：POST /api/v1/employees/import-registration-form
    """

    @staticmethod
    def _cv(ws, row: int, col: int) -> Optional[str]:
        """
        读取 xlrd 工作表指定单元格的值，返回清理后的字符串或 None。

        对异常（越界/类型错误）静默返回 None，避免解析失败导致整体中断。

        参数：
            ws  - xlrd worksheet 对象
            row - 行号（0-indexed）
            col - 列号（0-indexed）

        返回：
            字符串（已 strip）或 None
        """
        try:
            val = ws.cell(row, col).value
            if val is None or str(val).strip() == "":
                return None
            return str(val).strip()
        except Exception:
            return None

    @staticmethod
    def _parse_date(s: Optional[str]) -> Optional[date]:
        """
        尝试从字符串解析日期，支持多种格式：
            - YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
            - YYYY-MM / YYYY.MM（取当月1日）
            - YYYY年MM月 / YYYY年（取当年1月1日）

        参数：
            s - 日期字符串（或 None）

        返回：
            date 对象，解析失败返回 None
        """
        if not s:
            return None
        import re
        # YYYY-MM-DD
        m = re.match(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except Exception:
                pass
        # YYYY-MM or YYYY.MM
        m = re.match(r"(\d{4})[-./](\d{1,2})", s)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), 1)
            except Exception:
                pass
        # YYYY年MM月 or YYYY年
        m = re.match(r"(\d{4})年(\d{1,2})月", s)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), 1)
            except Exception:
                pass
        m = re.match(r"(\d{4})年", s)
        if m:
            try:
                return date(int(m.group(1)), 1, 1)
            except Exception:
                pass
        return None

    @staticmethod
    def _parse_date_range(s: Optional[str]) -> tuple:
        """
        解析起止日期字符串（如 "2020.01-2022.06"），返回 (start_date, end_date)。

        支持多种分隔符：- / – / — / ~ / ～ / 至

        参数：
            s - 形如"YYYY.MM-YYYY.MM"的字符串

        返回：
            (date | None, date | None) 元组；解析失败则对应位置为 None

        下游调用：
            _parse_date()
        """
        if not s:
            return None, None
        import re
        # 按常见范围分隔符拆分为最多2段
        parts = re.split(r"[-–—~～至]", s, maxsplit=1)
        if len(parts) == 2:
            return RegistrationFormService._parse_date(parts[0].strip()), \
                   RegistrationFormService._parse_date(parts[1].strip())
        return RegistrationFormService._parse_date(s), None

    @staticmethod
    def _parse_skills_text(text: Optional[str]) -> dict:
        """
        从技能行文本中用正则解析出结构化字段：
            - computer_level        : 计算机水平
            - foreign_language      : 外语语种
            - foreign_language_level: 外语级别
            - other_skills          : 其他技能

        参数：
            text - 技能行原始文本（如 "计算机水平：熟练 外语：英语 级别：六级 其它技能：Python"）

        返回：
            包含上述可识别字段的 dict（仅含成功解析的字段）
        """
        if not text:
            return {}
        import re
        result = {}
        # 提取计算机水平（截至"外语"或行尾）
        m = re.search(r"计算机水平[：:]\s*([^外\n]{1,50}?)(?:外语|$)", text)
        if m:
            v = m.group(1).strip().rstrip("，,")
            if v:
                result["computer_level"] = v
        # 提取外语语种（截至"级别"或行尾）
        m = re.search(r"语种\s*([^\n级]{1,30}?)(?:级别|$)", text)
        if m:
            v = m.group(1).strip()
            if v:
                result["foreign_language"] = v
        # 提取外语级别（截至"其它技能"或行尾）
        m = re.search(r"级别\s*([^\n其它技能]{1,30}?)(?:其[它他]技能|$)", text, re.DOTALL)
        if m:
            v = m.group(1).strip()
            if v:
                result["foreign_language_level"] = v
        # 提取其他技能（截至行尾）
        m = re.search(r"其[它他]技能[：:]\s*(.+?)$", text, re.DOTALL)
        if m:
            v = m.group(1).strip()
            if v:
                result["other_skills"] = v
        return result

    @staticmethod
    async def import_xls(
        db: AsyncSession,
        file_bytes: bytes,
        employee_no: Optional[str] = None,
        default_password: Optional[str] = None,
    ) -> dict:
        """
        解析员工信息登记表 XLS，创建员工及其附属数据（家庭成员/工作经历/教育背景/技能）。

        与 import_excel 的区别：
        - import_excel 处理批量花名册（多行数据，每行一名员工）
        - import_xls 处理单份标准登记表（固定坐标读取，一次导入一名员工及其全部子表数据）

        数据流：
            1. 用 xlrd 打开 XLS，按坐标读取基本信息
            2. 解析婚姻状态、户籍类型、紧急联系人（文本规范化）
            3. 自动生成工号（若未提供，用时间戳生成 EMP+YYYYMMDDHHMMSS）
            4. 校验手机号和工号唯一性
            5. 创建 Employee 记录
            6. 按行读取家庭成员（rows 11-13）
            7. 按行读取工作经历（rows 15-17），含薪资解析
            8. 按行读取教育背景（rows 19-21）
            9. 解析技能特长（rows 22-23），调用 _parse_skills_text
            10. 统一 flush，返回导入结果字典

        参数：
            db               - 异步数据库会话
            file_bytes       - 上传的 XLS 文件字节
            employee_no      - 可选工号，未提供时自动生成
            default_password - 可选默认密码，未提供时使用统一初始密码 123456

        返回：
            成功：{"success": True, "employee_id": int, "employee_no": str, "name": str,
                   "message": str, "family_members_imported": int,
                   "work_experiences_imported": int, "education_records_imported": int,
                   "skills_imported": bool}
            失败：{"success": False, "message": str}

        下游调用：
            _cv(), _parse_date(), _parse_date_range(), _parse_skills_text()
        """
        import xlrd

        try:
            wb = xlrd.open_workbook(file_contents=file_bytes)
            ws = wb.sheet_by_index(0)
        except Exception as e:
            return {
                "success": False,
                "message": f"无法读取 XLS 文件: {e}",
            }

        # 辅助函数别名，简化后续调用
        cv = RegistrationFormService._cv
        pd = RegistrationFormService._parse_date
        pdr = RegistrationFormService._parse_date_range

        # --- 基本信息 (rows 4-9, 0-indexed) ---
        # 坐标对应登记表固定位置（行号 0-indexed，列号 0-indexed）
        name = cv(ws, 4, 3)
        gender = cv(ws, 4, 5)
        birth_date_raw = cv(ws, 4, 7)
        native_place = cv(ws, 4, 9)
        ethnicity = cv(ws, 5, 3)
        political_status = cv(ws, 5, 7)
        marital_raw = cv(ws, 5, 9)
        education = cv(ws, 6, 3)
        school = cv(ws, 6, 5)
        major = cv(ws, 6, 7)
        first_work_date_raw = cv(ws, 6, 9)
        phone = cv(ws, 7, 3)
        emergency_raw = cv(ws, 7, 5)
        email = cv(ws, 7, 8)
        id_card_no = cv(ws, 8, 3)
        current_address = cv(ws, 8, 7)
        hukou_raw = cv(ws, 9, 3)
        hukou_location = cv(ws, 9, 7)

        if not name:
            return {"success": False, "message": "姓名不能为空"}
        if not phone:
            return {"success": False, "message": "手机号不能为空"}

        # 处理婚姻状态（将登记表中的勾选文本规范化为标准值）
        marital_status = None
        if marital_raw:
            if "已" in marital_raw or "是" in marital_raw:
                marital_status = "已婚"
            elif "未" in marital_raw or "否" in marital_raw:
                marital_status = "未婚"
            elif "离" in marital_raw:
                marital_status = "离异"
            else:
                marital_status = marital_raw  # 无法识别则原样保留

        # 处理户籍类型（匹配已知枚举值，含特殊符号过滤）
        hukou_type = None
        if hukou_raw:
            known = ["本地城镇", "本地农村", "外地城镇", "外地农村"]
            for k in known:
                if k in hukou_raw:
                    hukou_type = k
                    break
            # 过滤表格中的勾选框符号（¨ □）
            if not hukou_type and "¨" not in hukou_raw and "□" not in hukou_raw:
                hukou_type = hukou_raw

        # 处理紧急联系人（"张三/13800138001" 格式拆分为姓名和电话）
        emergency_contact = None
        emergency_phone = None
        if emergency_raw:
            import re
            parts = re.split(r"[/／\s]+", emergency_raw.strip(), maxsplit=1)
            if len(parts) == 2:
                emergency_contact = parts[0].strip()
                emergency_phone = parts[1].strip()
            else:
                emergency_contact = emergency_raw.strip()

        # 自动生成工号（时间戳格式，确保唯一性）
        if not employee_no:
            from datetime import datetime as dt
            ts = dt.now().strftime("%Y%m%d%H%M%S")
            employee_no = f"EMP{ts}"

        # 密码默认使用系统统一初始密码。
        password = default_password or DEFAULT_INITIAL_PASSWORD

        emp_data = {
            "employee_no": employee_no,
            "name": name,
            "gender": gender,
            "phone": phone,
            "email": email,
            "id_card_no": id_card_no,
            "birth_date": pd(birth_date_raw),
            "native_place": native_place,
            "ethnicity": ethnicity,
            "political_status": political_status,
            "marital_status": marital_status,
            "education": education,
            "school": school,
            "major": major,
            "first_work_date": pd(first_work_date_raw),
            "current_address": current_address,
            "hukou_type": hukou_type,
            "hukou_location": hukou_location,
            "emergency_contact": emergency_contact,
            "emergency_phone": emergency_phone,
            "hashed_password": get_password_hash(password),
            "status": "待入职",
            "employment_type": "正式",
            "is_field_staff": False,
        }
        # 去掉 None 值，避免覆盖数据库默认值
        emp_data = {k: v for k, v in emp_data.items() if v is not None}

        # 检查手机号唯一
        exists_phone = (await db.execute(
            select(func.count()).select_from(Employee).where(Employee.phone == phone)
        )).scalar()
        if exists_phone > 0:
            return {"success": False, "message": f"手机号 '{phone}' 已存在"}

        # 检查工号唯一
        exists_no = (await db.execute(
            select(func.count()).select_from(Employee).where(Employee.employee_no == employee_no)
        )).scalar()
        if exists_no > 0:
            return {"success": False, "message": f"工号 '{employee_no}' 已存在"}

        try:
            emp = Employee(**emp_data)
            db.add(emp)
            await db.flush()
            await db.refresh(emp)
        except Exception as e:
            return {"success": False, "message": f"创建员工失败: {e}"}

        family_count = 0
        work_count = 0
        edu_count = 0
        skills_imported = False

        # --- 家庭成员 (rows 11-13, header at row 10) ---
        # 每行对应一个家庭成员：姓名/关系/年龄/工作单位/职务/电话
        for r in range(11, 14):
            m_name = cv(ws, r, 2)
            if not m_name:
                continue
            member = FamilyMember(
                employee_id=emp.id,
                name=m_name,
                relation=cv(ws, r, 3),
                age=int(cv(ws, r, 4)) if cv(ws, r, 4) and cv(ws, r, 4).isdigit() else None,
                work_unit=cv(ws, r, 5),
                position=cv(ws, r, 7),
                phone=cv(ws, r, 8),
            )
            db.add(member)
            family_count += 1

        # --- 工作经历 (rows 15-17, header at row 14) ---
        # 每行对应一段工作经历：起止日期/公司名/职务/离职原因/上份薪资/证明人
        for idx, r in enumerate(range(15, 18)):
            company = cv(ws, r, 3)
            if not company:
                continue
            date_raw = cv(ws, r, 2)
            start_dt, end_dt = pdr(date_raw)
            # 从薪资列用正则提取数字（可能含货币符号或汉字"元"）
            salary_raw = cv(ws, r, 8)
            prev_salary = None
            if salary_raw:
                import re
                m_sal = re.search(r"[\d,，.]+", salary_raw)
                if m_sal:
                    try:
                        prev_salary = float(m_sal.group().replace(",", "").replace("，", ""))
                    except Exception:
                        pass
            exp = WorkExperience(
                employee_id=emp.id,
                start_date=start_dt,
                end_date=end_dt,
                company_name=company,
                position=cv(ws, r, 5),
                responsibilities=cv(ws, r, 5),  # col 5 has 职务/主要职责 merged
                leave_reason=cv(ws, r, 7),
                previous_salary=prev_salary,
                referee_info=cv(ws, r, 9),
                sort_order=idx,
            )
            db.add(exp)
            work_count += 1

        # --- 教育背景 (rows 19-21, header at row 18) ---
        # 每行对应一段教育经历：起止日期/学校/专业/学习形式/学位/证书
        for idx, r in enumerate(range(19, 22)):
            school_val = cv(ws, r, 3)
            if not school_val:
                continue
            date_raw = cv(ws, r, 2)
            start_dt, end_dt = pdr(date_raw)
            edu = EducationRecord(
                employee_id=emp.id,
                start_date=start_dt,
                end_date=end_dt,
                school=school_val,
                major=cv(ws, r, 5),
                study_type=cv(ws, r, 7),
                degree=cv(ws, r, 8),
                certificate_name=cv(ws, r, 9),
                sort_order=idx,
            )
            db.add(edu)
            edu_count += 1

        # --- 技能特长 (rows 22-23) ---
        # row 22: 计算机水平 + 外语信息（用 _parse_skills_text 解析）
        # row 23: 个人爱好及特长
        skills_line1 = cv(ws, 22, 2) or ""
        skills_line2 = cv(ws, 23, 2) or ""
        skills_data = RegistrationFormService._parse_skills_text(skills_line1)
        if skills_line2:
            import re
            m_hobby = re.search(r"个人爱好[及和]?特长[：:]\s*(.+?)$", skills_line2, re.DOTALL)
            if m_hobby:
                v = m_hobby.group(1).strip()
                if v:
                    skills_data["hobbies"] = v
            elif skills_line2 and not skills_data.get("hobbies"):
                skills_data["hobbies"] = skills_line2  # 整行作为兴趣爱好

        if skills_data:
            skill = EmployeePersonalSkill(employee_id=emp.id, **skills_data)
            db.add(skill)
            skills_imported = True

        # 统一 flush（不 commit，由调用方的 get_db() 提交）
        await db.flush()

        return {
            "employee_id": emp.id,
            "employee_no": emp.employee_no,
            "name": emp.name,
            "success": True,
            "message": f"成功导入员工 '{name}'（工号 {employee_no}）",
            "family_members_imported": family_count,
            "work_experiences_imported": work_count,
            "education_records_imported": edu_count,
            "skills_imported": skills_imported,
        }
