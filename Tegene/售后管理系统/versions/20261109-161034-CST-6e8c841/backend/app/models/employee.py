"""
模块名称: 员工数据模型
文件路径: app/models/employee.py

================================================================================
表结构总览 (Table Overview)
================================================================================
本模块定义以下 7 张数据库表：

  1. employees               — 员工主表，核心实体，存储所有员工的基本信息、
                               组织关系、状态及账号信息
  2. employee_contracts      — 劳动合同表，记录员工与公司签订的历次合同
  3. work_experiences        — 工作经历表，记录员工入职前的历史工作经历
  4. employee_documents      — 档案材料表，跟踪员工各类入职档案的收集状态
  5. family_members          — 家庭成员表，记录员工的家庭成员信息（含紧急联系人）
  6. education_records       — 教育背景表，记录员工的学历及培训经历
  7. employee_personal_skills — 技能特长表，记录员工计算机、外语及其他技能

================================================================================
业务说明 (Business Context)
================================================================================
本模块是售后管理系统的核心数据层，承载「员工生命周期管理」功能。
员工从「待入职」开始，经历「试用期」→「在职」，直至「离职」，全程状态均
在 employees.status 字段中维护。

本公司采用多公司主体架构（总部 + 东莞旋光等子公司），同时在多个物理
地点（北京研发中心 / 东莞凇湖工厂 / 东莞茵茵工厂）运营，因此 employees 表
通过 company_id 和 location_id 分别关联公司主体与工作地点。

员工档案包含两类信息：
  - 基础档案：employees 主表（含组织关系、用工类型、薪酬银行账户等）
  - 附属档案：合同 / 工作经历 / 家庭成员 / 教育背景 / 技能特长 / 材料清单

员工账号体系内嵌于本模块：phone 作为登录账号，hashed_password 存储 bcrypt
哈希，is_active 控制账号可用性，is_superuser 标记超级管理员权限。

================================================================================
数据流 (Data Flow)
================================================================================
写入方：
  - HR 通过「员工生命周期」模块录入新员工，触发 employees / employee_contracts
    / employee_documents 等多张表的写入
  - 员工通过 ESS（Employee Self-Service）模块自助维护个人信息
  - 招聘模块 Offer 转正时，从 Resume 同步基本信息到 employees 表

读取方：
  - 考勤模块（attendance）：读取 employee_id / department_id / location_id
  - 薪资模块（payroll）：读取 bank_account / social_insurance_base / employment_type
  - 绩效模块（performance）：读取 employee_id / department_id / position
  - 审批模块（approval）：读取 direct_manager_id 构建审批链
  - 培训模块（training）：读取 employee_id 关联培训记录
  - 胜任力/人才发展/人才盘点模块：读取 employee_id 关联能力评估结果
  - 管理驾驶舱（dashboard）：统计 status / department / location 分布
  - 认证模块（auth）：通过 phone 查找员工并校验 hashed_password

================================================================================
核心关联关系 (Key Relationships)
================================================================================

  employees (1) ←→ (N) employee_contracts
      一名员工在职期间可签署多份合同（首签 + 多次续签）
      合同按 start_date 倒序排列，最新合同为当前有效合同

  employees (1) ←→ (N) work_experiences
      员工在职前的历史工作经历，支持背调使用
      按 sort_order 排序（HR 可手动调整顺序）

  employees (1) ←→ (N) employee_documents
      入职档案核查清单，每条记录代表一类材料（如身份证复印件）
      is_submitted 标记该材料是否已收集到

  employees (1) ←→ (N) family_members
      家庭成员信息，is_emergency_contact 标记其中的紧急联系人

  employees (1) ←→ (N) education_records
      学历及培训记录，is_highest 标记最高学历，用于统计分析

  employees (1) ←→ (1) employee_personal_skills
      一对一关系（employee_id 有 unique 约束）
      存储员工的技能特长，包含 JSON 格式的技能标签

  employees (N) ←→ (1) departments    [department_id → departments.id]
      员工所属部门，部门删除时置为 NULL（SET NULL）

  employees (N) ←→ (1) locations      [location_id → locations.id]
      员工所在工作地点，对应考勤打卡位置

  employees (N) ←→ (1) companies      [company_id → companies.id]
      员工所属法人主体，影响社保缴纳主体和工资条抬头

  employees (自引用) direct_manager_id → employees.id
      直属上级关系，形成 N 叉树形汇报层级
      direct_manager（多对一）与 direct_reports（一对多）互为反向

================================================================================
被哪些模块使用 (Consumers)
================================================================================
  - app/services/employee.py        — 员工 CRUD 主服务
  - app/services/attendance.py      — 查询员工打卡规则和归属部门
  - app/services/payroll.py         — 读取银行账户、社保基数、用工类型
  - app/services/leave.py           — 读取员工假期余额关联
  - app/services/approval.py        — 读取 direct_manager_id 构建审批节点
  - app/services/recruitment.py     — Offer 转正时写入员工档案
  - app/services/training.py        — 读取员工参加培训的资格信息
  - app/services/competency.py      — 关联员工胜任力评估
  - app/services/talent.py          — 人才发展 IDP 计划关联
  - app/services/talent_profile.py  — 人才画像与技能图谱
  - app/services/dashboard.py       — 人力结构分布统计
  - app/services/analytics.py       — 离职率、招聘漏斗等分析报表
  - app/services/ess.py             — 员工自助查看/修改个人信息
  - app/services/vendor.py          — 外包供应商员工身份核查
  - app/core/deps.py                — JWT 认证时通过 phone 查询员工
  - app/api/v1/auth.py              — 登录接口

"""
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text,
)
from sqlalchemy.orm import relationship
import enum

from app.core.database import Base


# ---------- 枚举类型 ----------

class EmploymentType(str, enum.Enum):
    """
    用工类型枚举 — 决定员工适用的管理规则和薪酬策略。

    取值说明：
      REGULAR   (正式)  — 正式员工，签署固定期限或无固定期限劳动合同，
                          享受完整的薪酬福利体系（五险一金、年终奖等）
      PROBATION (试用)  — 处于试用期的员工，薪资通常为 probation_salary，
                          转正后变更为 REGULAR；对应 EmployeeStatus.ON_PROBATION
      INTERN    (实习)  — 实习生，签署实习协议而非劳动合同，不缴纳社保，
                          薪资按实习约定支付
      OUTSOURCE (外包)  — 外包人员，与第三方供应商签合同，不在本公司直接用工，
                          通过 vendors 模块由供应商管理

    注意：employment_type 与 EmployeeStatus 互相配合，
    例如 PROBATION 类型在转正后需同时更新 employment_type 和 status 两个字段。
    """
    REGULAR = "正式"
    PROBATION = "试用"
    INTERN = "实习"
    OUTSOURCE = "外包"


class EmployeeStatus(str, enum.Enum):
    """
    员工在职状态枚举 — 驱动员工生命周期的核心状态机。

    状态流转：
      PENDING → ON_PROBATION（实际入职后进入试用期）
      PENDING → ACTIVE（跳过试用期直接入职，适用于工龄丰富的正式引进人才）
      ON_PROBATION → ACTIVE（试用期通过转正）
      ON_PROBATION → LEFT（试用期不通过，解除劳动关系）
      ACTIVE → LEFT（正常离职/辞退）

    取值说明：
      ACTIVE       (在职)   — 正式在职员工，参与考勤、绩效、薪资所有模块
      ON_PROBATION (试用期) — 试用期员工，薪资按 probation_salary 计算，
                              probation_end_date 到期后由 HR 手动确认转正
      LEFT         (离职)   — 已离职，leave_date 和 leave_reason 记录离职信息；
                              is_active=False 同步禁用账号登录
      PENDING      (待入职) — Offer 已接受但尚未正式报到，系统已预建档案

    业务规则：仅 status=ACTIVE 或 ON_PROBATION 的员工才应计入考勤统计；
    status=LEFT 后应由定时任务将 is_active 置为 False，禁止登录。
    """
    ACTIVE = "在职"
    ON_PROBATION = "试用期"
    LEFT = "离职"
    PENDING = "待入职"


class ContractType(str, enum.Enum):
    """
    合同类型枚举 — 用于区分劳动关系与劳务关系。

    取值说明：
      LABOR   (劳动合同) — 公司与员工建立劳动关系的合同。
      SERVICE (劳务合同) — 公司与个人/劳务人员建立劳务关系的合同。
    """
    LABOR = "劳动合同"
    SERVICE = "劳务合同"


class ContractStatus(str, enum.Enum):
    """
    合同状态枚举 — 跟踪合同的生效与失效状态。

    取值说明：
      ACTIVE     (生效中) — 合同在有效期内且已签署，是员工当前的用工依据
      EXPIRED    (已到期) — 固定期限合同超过 end_date 自然到期；
                            系统可由定时任务自动将过期合同更新为此状态
      TERMINATED (已解除) — 在 end_date 前提前解除（双方协商、辞退、辞职等）；
                            解除时应同步更新 EmployeeStatus 为 LEFT
      PENDING    (待生效) — 合同已创建/签署但 start_date 尚未到达，
                            如提前为新员工建档时录入的合同
    """
    ACTIVE = "生效中"
    EXPIRED = "已到期"
    TERMINATED = "已解除"
    PENDING = "待生效"


# ---------- 员工表 ----------

class Employee(Base):
    """
    员工主表 (employees) — 系统中最核心的实体，贯穿全部 18 个业务模块。

    表作用：
      存储本公司所有员工（含正式、试用、实习、外包）的完整档案信息，
      包括基本个人信息、组织归属、账号凭证以及各类统计辅助字段。

    关键字段说明：
      employee_no     — 工号，全系统唯一标识，格式由 HR 定义（如 TG001）；
                        用于工资条、考勤报表等对外文件的员工标识
      phone           — 手机号，同时作为系统登录账号（username），全局唯一
      hashed_password — 使用 bcrypt（passlib）加密存储，明文密码不落库；
                        密码强度规则在 security.py 中定义
      company_id      — 法人主体 ID，影响社保缴纳主体和工资条抬头；
                        company 字段为冗余文本字段，历史兼容用途
      direct_manager_id — 直属上级的 employees.id，自引用外键；
                          用于构建组织汇报链和审批流的「直属上级」节点
      employment_type — 用工类型，与薪资计算逻辑紧密耦合
      status          — 员工生命周期状态，详见 EmployeeStatus 枚举注释
      is_field_staff  — 驻场标志，驻场员工需记录 current_project；
                        影响考勤规则（驻场员工可能使用项目地点打卡）
      skill_tags (EmployeePersonalSkill) — 存为 JSON 数组格式，含熟练度标注

    业务规则：
      1. 入职流程：HR 创建员工档案时，status 默认为 PENDING；
         员工实际报到后，HR 手动更新为 ON_PROBATION 或 ACTIVE
      2. 离职流程：更新 status=LEFT，填写 leave_date 和 leave_reason，
         同时应将 is_active 置为 False 禁止登录
      3. 同一手机号只能绑定一名员工（phone 有 unique 约束）
      4. 部门/公司/地点删除时，对应外键置 NULL（ondelete="SET NULL"），
         员工记录不会联级删除
      5. 社保相关城市字段（social_insurance_city / housing_fund_city）
         用于多地薪资结算时判断使用哪个城市的社保基数标准

    自引用汇报关系：
      direct_manager   — 多对一，获取该员工的直属上级 Employee 对象
      direct_reports   — 一对多，获取该员工的所有直属下属列表
      两者均使用 foreign_keys=[direct_manager_id] 消除歧义，
      direct_reports 需声明 overlaps="direct_manager" 避免 SAWarning
    """

    __tablename__ = "employees"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    employee_no: str = Column(String(30), unique=True, nullable=False, comment="工号")
    name: str = Column(String(50), nullable=False, comment="姓名")
    gender: str = Column(String(10), nullable=True, comment="性别")
    phone: str = Column(String(20), unique=True, nullable=False, comment="手机号（登录账号）")
    email: str = Column(String(100), nullable=True, comment="邮箱")
    id_card_no: str = Column(String(18), nullable=True, comment="身份证号")
    birth_date: date = Column(Date, nullable=True, comment="出生日期")
    photo_url: str = Column(String(500), nullable=True, comment="照片URL")

    # 个人信息补充
    ethnicity: str = Column(String(20), nullable=True, comment="族别")
    marital_status: str = Column(String(20), nullable=True, comment="婚姻状态：未婚/已婚/离异")
    education: str = Column(String(20), nullable=True, comment="最高学历：本科/硕士/大专/高中等")
    school: str = Column(String(200), nullable=True, comment="毕业院校")
    major: str = Column(String(100), nullable=True, comment="所学专业")
    current_address: str = Column(String(500), nullable=True, comment="现居住地")
    hukou_location: str = Column(String(200), nullable=True, comment="户口所在地")
    hukou_type: str = Column(String(20), nullable=True, comment="户口性质：外地农村/外地城镇/本地城镇/本地农村")
    remark: str = Column(Text, nullable=True, comment="备注")

    # 组织关系
    company: str = Column(String(100), nullable=True, comment="所属公司：总部/东莞旋光等（冗余字段）")
    company_id: int = Column(
        Integer,
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="公司主体ID(关联companies表)",
    )
    location_id: int = Column(
        Integer,
        ForeignKey("locations.id", ondelete="SET NULL"),
        nullable=True,
        comment="工作地点",
    )
    department_id: int = Column(
        Integer,
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        comment="所属部门",
    )
    direct_manager_id: int = Column(
        Integer,
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        comment="直属上级员工ID",
    )
    third_level_dept: str = Column(String(100), nullable=True, comment="三级部门")
    position: str = Column(String(100), nullable=True, comment="职位")
    job_level: str = Column(String(50), nullable=True, comment="职级")
    employment_type: str = Column(
        String(20),
        default=EmploymentType.REGULAR.value,
        nullable=False,
        comment="用工类型：正式/试用/实习/外包",
    )
    status: str = Column(
        String(20),
        default=EmployeeStatus.PENDING.value,
        nullable=False,
        comment="状态：在职/试用期/离职/待入职",
    )

    # 时间节点
    hire_date: date = Column(Date, nullable=True, comment="入职日期")
    probation_end_date: date = Column(Date, nullable=True, comment="试用期结束日期")
    leave_date: date = Column(Date, nullable=True, comment="离职日期")
    leave_reason: str = Column(String(500), nullable=True, comment="离职原因")

    # 紧急联系人
    emergency_contact: str = Column(String(50), nullable=True, comment="紧急联系人")
    emergency_phone: str = Column(String(20), nullable=True, comment="紧急联系人电话")

    # 薪酬相关
    bank_name: str = Column(String(100), nullable=True, comment="开户行")
    bank_account: str = Column(String(50), nullable=True, comment="银行卡号")
    social_insurance_city: str = Column(String(50), nullable=True, comment="社保缴纳城市")
    housing_fund_city: str = Column(String(50), nullable=True, comment="公积金缴纳城市")

    # 驻场信息
    is_field_staff: bool = Column(Boolean, default=False, nullable=False, comment="是否驻场人员")
    current_project: str = Column(String(200), nullable=True, comment="当前项目")

    # 补充个人信息
    fertility_status: str = Column(String(20), nullable=True, comment="生育状况：未育/已育/其他")
    political_status: str = Column(String(30), nullable=True, comment="政治面貌：群众/中共党员/共青团员/民主党派/其他")
    graduation_date: date = Column(Date, nullable=True, comment="毕业时间")
    degree_no: str = Column(String(100), nullable=True, comment="学位证编号")
    has_noncompete: bool = Column(Boolean, default=False, nullable=True, comment="是否有竞业协议")
    current_position_date: date = Column(Date, nullable=True, comment="本岗位任职时间")

    # 登记表补充字段
    native_place: str = Column(String(100), nullable=True, comment="籍贯")
    first_work_date: date = Column(Date, nullable=True, comment="首次参加工作时间")

    # 补充紧急联系
    emergency_contact_relation: str = Column(String(20), nullable=True, comment="紧急联系人关系：父母/配偶/兄弟姐妹/其他")
    emergency_phone2: str = Column(String(20), nullable=True, comment="第二紧急联系人电话")

    # 薪酬补充
    probation_salary: float = Column(Numeric(12, 2), nullable=True, comment="试用期薪资")
    social_insurance_base: float = Column(Numeric(12, 2), nullable=True, comment="社保基数")

    # 账号相关
    hashed_password: str = Column(String(200), nullable=True, comment="密码哈希")
    is_active: bool = Column(Boolean, default=True, nullable=False, comment="账号是否启用")
    is_superuser: bool = Column(Boolean, default=False, nullable=False, comment="是否超级管理员")

    # 时间戳
    created_at: datetime = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, comment="创建时间"
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="更新时间",
    )

    # 关联关系

    # 所属法人主体 — 用于薪资结算和社保缴纳主体判断
    company_entity = relationship(
        "Company", back_populates="employees", foreign_keys=[company_id], lazy="selectin"
    )

    # 所在工作地点 — 用于考勤打卡位置校验和驻场判断
    location = relationship("Location", back_populates="employees", lazy="selectin")

    # 所属部门 — 用于绩效、审批、统计分析等多个模块的部门维度过滤
    department = relationship(
        "Department",
        back_populates="employees",
        foreign_keys=[department_id],
        lazy="selectin",
    )

    # 历次劳动合同列表（按 start_date 倒序，第一条为最新合同）
    # 用于合同到期提醒、续签次数统计和劳动关系证明
    contracts = relationship(
        "EmployeeContract",
        back_populates="employee",
        lazy="selectin",
        order_by="EmployeeContract.start_date.desc()",
    )

    # 薪资结构列表 — 关联 payroll 模块，获取员工薪资组成及调薪历史
    salary_structures = relationship(
        "SalaryStructure",
        back_populates="employee",
        lazy="selectin",
    )

    # 历史工作经历（按 sort_order 排序）— HR 可手动调整顺序，用于背景调查
    work_experiences = relationship(
        "WorkExperience",
        back_populates="employee",
        lazy="selectin",
        order_by="WorkExperience.sort_order",
    )

    # 入职档案材料清单 — 跟踪每类材料的收集进度，如身份证/学历/合同等
    documents = relationship(
        "EmployeeDocument",
        back_populates="employee",
        lazy="selectin",
    )

    # 家庭成员列表（按 id 升序）— 包含紧急联系人标记，用于工伤/突发情况通知
    family_members = relationship(
        "FamilyMember",
        back_populates="employee",
        lazy="selectin",
        order_by="FamilyMember.id",
    )

    # 教育背景记录（按 start_date 倒序）— is_highest=True 的记录用于学历统计
    education_records = relationship(
        "EducationRecord",
        back_populates="employee",
        lazy="selectin",
        order_by="EducationRecord.start_date.desc()",
    )

    # 个人技能特长（一对一，uselist=False）— 存储技能标签 JSON，用于人才画像
    personal_skill = relationship(
        "EmployeePersonalSkill",
        back_populates="employee",
        lazy="selectin",
        uselist=False,
    )

    # 直属上级（多对一自引用）
    # remote_side 指向自身 id，表明 direct_manager_id 是多端（子），id 是一端（父）
    # 用于审批流动态获取「直属上级」审批人，以及组织树的向上遍历
    direct_manager = relationship(
        "Employee",
        foreign_keys=[direct_manager_id],
        primaryjoin="Employee.direct_manager_id == Employee.id",
        remote_side="Employee.id",
        lazy="selectin",
    )

    # 直属下属列表（一对多自引用）
    # 用于组织树的向下展开，以及经理查看自己团队成员列表
    # overlaps="direct_manager" 声明避免 SQLAlchemy 发出 SAWarning（两个关系共享同一外键）
    direct_reports = relationship(
        "Employee",
        foreign_keys="[Employee.direct_manager_id]",
        primaryjoin="Employee.direct_manager_id == Employee.id",
        lazy="select",
        overlaps="direct_manager",
    )

    def __repr__(self) -> str:
        return f"<Employee(id={self.id}, no='{self.employee_no}', name='{self.name}')>"


# ---------- 合同表 ----------

class EmployeeContract(Base):
    """
    员工劳动合同表 (employee_contracts)

    表作用：
      记录员工与本公司（各法人主体）签订的每一份劳动合同或实习协议。
      一名员工在职期间可有多条合同记录（初签 + 多次续签），合同按
      start_date 倒序排列，最新的即为当前有效合同。

    关键字段说明：
      contract_no   — 合同编号，全局唯一，对应纸质合同上的编号；
                      nullable=True 是因为部分历史数据无编号
      end_date      — 合同结束日期，无固定期限合同此字段为 NULL
      renewal_count — 本合同的续签次数，用于判断是否触发《劳动合同法》
                      第 14 条「第二次续签可要求无固定期限」规则
      contract_renewal_reminder — HR 设置的到期提醒日期，系统定时任务
                                   可据此推送合同到期提醒消息
      file_url      — 合同扫描件或电子合同的存储 URL（OSS/本地）

    业务规则：
      1. 合同状态由 HR 手动维护，也可由定时任务自动将超过 end_date 的
         合同状态从 ACTIVE 更新为 EXPIRED
      2. 员工离职时，应将当前 ACTIVE 合同状态更新为 TERMINATED
      3. employee_id 使用 ondelete="CASCADE"，员工档案删除时合同联级删除；
         但实际业务中不应物理删除员工，应通过 status=LEFT 标记离职

    关联关系：
      employee — 多对一，指向 employees 表，反向为 Employee.contracts
    """

    __tablename__ = "employee_contracts"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    employee_id: int = Column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        comment="员工ID",
    )
    contract_type: str = Column(
        String(20),
        nullable=False,
        comment="合同类型：劳动合同/劳务合同",
    )
    contract_no: str = Column(String(50), unique=True, nullable=True, comment="合同编号")
    start_date: date = Column(Date, nullable=False, comment="合同开始日期")
    end_date: date = Column(Date, nullable=True, comment="合同结束日期（无固定期限可为空）")
    renewal_count: int = Column(Integer, default=0, nullable=False, comment="续签次数")
    contract_renewal_date: date = Column(Date, nullable=True, comment="续签开始日期")
    contract_renewal_end_date: date = Column(Date, nullable=True, comment="续签结束日期")
    contract_renewal_reminder: date = Column(Date, nullable=True, comment="续签提醒日期")
    sign_date: date = Column(Date, nullable=True, comment="签署日期")
    file_url: str = Column(String(500), nullable=True, comment="合同文件URL")
    status: str = Column(
        String(20),
        default=ContractStatus.PENDING.value,
        nullable=False,
        comment="合同状态",
    )
    created_at: datetime = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, comment="创建时间"
    )

    # 关联：反向引用 Employee.contracts，可通过 contract.employee 获取员工完整信息
    employee = relationship("Employee", back_populates="contracts", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<EmployeeContract(id={self.id}, employee_id={self.employee_id}, "
            f"type='{self.contract_type}')>"
        )


# ---------- 工作经历表 ----------

class WorkExperience(Base):
    """
    员工历史工作经历表 (work_experiences)

    表作用：
      记录员工入职本公司之前的历次工作经历，每条记录对应一段工作经历。
      主要用于：
        1. HR 背景调查（reference_name / reference_phone 供背调联系）
        2. 面试评估参考（responsibilities 描述工作职责）
        3. 薪资谈判依据（previous_salary 上家薪资）
        4. 学历/经验年限统计分析

    关键字段说明：
      referee_info  — 旧版证明人字段（将姓名和电话合并存储），已废弃；
                      新数据请使用 reference_name + reference_phone 分开存储
      previous_salary — 上家薪资，敏感信息，应控制查看权限
      is_current    — 标记当前在职经历（正常情况应为 False，
                      若员工仍在职则此条代表当前工作，end_date 为空）
      sort_order    — 允许 HR 手动调整显示顺序，一般按时间倒序排列

    关联关系：
      employee — 多对一，指向 employees 表，反向为 Employee.work_experiences
    """

    __tablename__ = "work_experiences"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    employee_id: int = Column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        comment="员工ID",
    )
    start_date: date = Column(Date, nullable=True, comment="开始日期")
    end_date: date = Column(Date, nullable=True, comment="结束日期")
    company_name: str = Column(String(200), nullable=False, comment="公司名称")
    position: str = Column(String(100), nullable=True, comment="职位")
    responsibilities: str = Column(Text, nullable=True, comment="主要职责")
    industry: str = Column(String(100), nullable=True, comment="公司行业")
    leave_reason: str = Column(String(500), nullable=True, comment="离职原因")
    previous_salary: float = Column(Numeric(12, 2), nullable=True, comment="上家薪资")
    referee_info: str = Column(String(200), nullable=True, comment="证明人及联系方式（旧字段）")
    reference_name: str = Column(String(50), nullable=True, comment="证明人姓名")
    reference_phone: str = Column(String(30), nullable=True, comment="证明人电话")
    is_current: bool = Column(Boolean, default=False, nullable=False, comment="是否在职")
    sort_order: int = Column(Integer, default=0, nullable=False, comment="排序序号")
    created_at: datetime = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, comment="创建时间"
    )

    # 关联：通过 work_experience.employee 获取员工信息；反向 Employee.work_experiences
    employee = relationship("Employee", back_populates="work_experiences", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<WorkExperience(id={self.id}, employee_id={self.employee_id}, "
            f"company='{self.company_name}')>"
        )


# ---------- 档案材料表 ----------

class EmployeeDocument(Base):
    """
    员工入职档案材料表 (employee_documents)

    表作用：
      作为入职档案的「收集清单」，跟踪每名员工每类材料的提交状态。
      每条记录代表「该员工的某一类档案材料」，is_submitted 标记是否已收齐，
      file_url 存储已上传的扫描件链接。

      HR 在员工入职时创建待收集的材料清单，随着员工逐步提交材料，
      逐一将 is_submitted 更新为 True 并填写 file_url。

    关键字段说明：
      doc_type    — 材料类型，取值范围（来源于 column comment）：
                    员工信息表 / 身份证复印件 / 银行卡复印件 / 离职证明 /
                    学历复印件 / 学位复印件 / 体检报告 / 劳动合同书 /
                    保密及竞业协议 / 实习协议 / 其他
      is_submitted — False=待提交，True=已收到实体或电子件
      file_url    — 上传到 OSS/本地存储的文件访问路径；
                    is_submitted=True 但 file_url 为空，说明只收到实体尚未扫描

    业务规则：
      档案收集完整度是 HR 入职 checklist 的重要指标，
      管理驾驶舱中「档案完整率」统计基于此表数据

    关联关系：
      employee — 多对一，指向 employees 表，反向为 Employee.documents
    """

    __tablename__ = "employee_documents"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    employee_id: int = Column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        comment="员工ID",
    )
    doc_type: str = Column(
        String(50),
        nullable=False,
        comment="材料类型：员工信息表/身份证复印件/银行卡复印件/离职证明/学历复印件/学位复印件/体检报告/劳动合同书/保密及竞业协议/实习协议/其他",
    )
    is_submitted: bool = Column(Boolean, default=False, nullable=False, comment="是否已提交")
    file_url: str = Column(String(500), nullable=True, comment="文件URL")
    remark: str = Column(Text, nullable=True, comment="备注")
    created_at: datetime = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, comment="创建时间"
    )

    # 关联：反向 Employee.documents；通过 doc.employee 可获取员工姓名等信息
    employee = relationship("Employee", back_populates="documents", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<EmployeeDocument(id={self.id}, employee_id={self.employee_id}, "
            f"type='{self.doc_type}')>"
        )


# ---------- 家庭成员表 ----------

class FamilyMember(Base):
    """
    员工家庭成员表 (family_members)

    表作用：
      记录员工的家庭成员信息，主要用途：
        1. 工伤 / 突发事件时联系家属（is_emergency_contact=True 的成员）
        2. 员工福利核验（如子女教育补贴、婚育假证明）
        3. 五险一金中的受益人信息
        4. 部分公司要求员工如实申报家庭情况（合规需求）

    关键字段说明：
      relation            — 与员工的关系，如「父亲」「配偶」「子女」等
      age                 — 兼容旧数据的年龄字段，新数据优先使用 birth_date；
                            两者可能并存，优先以 birth_date 计算实际年龄
      is_emergency_contact — 标记此成员为紧急联系人；
                             employees 表也有冗余的 emergency_contact 字段，
                             两者数据应保持同步
      work_unit / position — 成员的工作单位和职务，部分合规场景需要申报

    业务规则：
      一名员工可有多个家庭成员记录，但 is_emergency_contact=True 通常仅1-2人。
      家庭成员的 phone 字段应与 employees.emergency_phone 保持一致（如是紧急联系人）

    关联关系：
      employee — 多对一，指向 employees 表，反向为 Employee.family_members
    """

    __tablename__ = "family_members"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    employee_id: int = Column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        comment="员工ID",
    )
    name: str = Column(String(50), nullable=False, comment="姓名")
    relation: str = Column(String(30), nullable=True, comment="关系")
    age: int = Column(Integer, nullable=True, comment="年龄（兼容旧数据）")
    birth_date: date = Column(Date, nullable=True, comment="出生日期")
    is_emergency_contact: bool = Column(Boolean, default=False, nullable=False, comment="是否紧急联系人")
    work_unit: str = Column(String(200), nullable=True, comment="工作单位")
    position: str = Column(String(100), nullable=True, comment="职务")
    phone: str = Column(String(30), nullable=True, comment="联系方式")
    created_at: datetime = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, comment="创建时间"
    )

    # 关联：反向 Employee.family_members
    employee = relationship("Employee", back_populates="family_members")

    def __repr__(self) -> str:
        return f"<FamilyMember(id={self.id}, employee_id={self.employee_id}, name='{self.name}')>"


# ---------- 教育背景表 ----------

class EducationRecord(Base):
    """
    员工教育/培训背景表 (education_records)

    表作用：
      记录员工的完整受教育经历，包括全日制学历教育和在职培训。
      一名员工可有多条记录（如本科 + 研究生 + 职业资格培训）。
      is_highest=True 的记录对应 employees.education 字段的最高学历。

    关键字段说明：
      study_type    — 学历类型：全日制 / 非全日制 / 自考 / 其他；
                      影响学历核验方式（全日制可查教育部学信网）
      degree        — 所获学历/资质/学位，如「本科」「工学学士」「PMP证书」
      is_highest    — 标记最高学历，用于 HR 统计学历分布图表；
                      employee_service 在更新时应确保只有一条记录为 True
      certificate_name — 所获证书名称，适用于职业技能证书类记录，
                          如「注册会计师」「一级建造师」等
      sort_order    — 显示排序，通常按时间倒序（最新学历排首位）

    关联关系：
      employee — 多对一，指向 employees 表，反向为 Employee.education_records
    """

    __tablename__ = "education_records"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    employee_id: int = Column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        comment="员工ID",
    )
    start_date: date = Column(Date, nullable=True, comment="开始日期")
    end_date: date = Column(Date, nullable=True, comment="结束日期")
    school: str = Column(String(200), nullable=False, comment="学校/培训机构")
    major: str = Column(String(200), nullable=True, comment="专业/培训内容")
    study_type: str = Column(String(20), nullable=True, comment="学历类型：全日制/非全日制/自考/其他")
    degree: str = Column(String(100), nullable=True, comment="所获学历/资质/学位")
    is_highest: bool = Column(Boolean, default=False, nullable=False, comment="是否最高学历")
    certificate_name: str = Column(String(200), nullable=True, comment="所获证书名称")
    sort_order: int = Column(Integer, default=0, nullable=False, comment="排序序号")
    created_at: datetime = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, comment="创建时间"
    )

    # 关联：反向 Employee.education_records
    employee = relationship("Employee", back_populates="education_records")

    def __repr__(self) -> str:
        return f"<EducationRecord(id={self.id}, employee_id={self.employee_id}, school='{self.school}')>"


# ---------- 技能特长表 ----------

class EmployeePersonalSkill(Base):
    """
    员工个人技能特长表 (employee_personal_skills)

    表作用：
      存储员工的技能画像信息，与 employees 表呈严格的一对一关系
      （employee_id 有 unique 约束，每位员工只有一条记录）。

      此表记录的是「个人通用技能」，有别于胜任力模块（competency）中
      针对岗位的专业能力评估。主要来源于员工入职时填写的 HR 登记表。

    关键字段说明：
      computer_level        — 计算机水平描述，如「熟练使用 Office」「熟悉 Linux」
      foreign_language      — 外语语种，如「英语」「日语」
      foreign_language_level — 外语级别，如「CET-6」「JLPT N2」「商务英语」
      skill_tags            — JSON 数组格式，结构示例：
                              [{"name": "Python", "level": "熟练"},
                               {"name": "SolidWorks", "level": "精通"}]
                              前端「技能画像」雷达图基于此数据渲染
      hobbies               — 个人爱好特长，用于团队文化建设和活动组织参考

    业务规则：
      1. 创建员工时可不填此表，首次更新技能信息时 upsert 创建记录
      2. skill_tags 字段变更时，应同步更新 updated_at 时间戳
      3. 人才盘点（talent_review）和人才画像（talent_profile）模块
         会读取此表数据丰富员工能力标签

    关联关系：
      employee — 一对一（uselist=False），指向 employees 表，
                 反向为 Employee.personal_skill
    """

    __tablename__ = "employee_personal_skills"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    employee_id: int = Column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,   # unique 约束确保一对一关系
        comment="员工ID",
    )
    computer_level: str = Column(String(100), nullable=True, comment="计算机水平")
    foreign_language: str = Column(String(50), nullable=True, comment="外语语种")
    foreign_language_level: str = Column(String(50), nullable=True, comment="外语级别")
    other_skills: str = Column(Text, nullable=True, comment="其它技能")
    skill_tags: str = Column(Text, nullable=True, comment="技能标签（JSON数组，含熟练度）")
    hobbies: str = Column(Text, nullable=True, comment="个人爱好特长")
    created_at: datetime = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, comment="创建时间"
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="更新时间",
    )

    # 关联：一对一反向引用 Employee.personal_skill（uselist=False）
    employee = relationship("Employee", back_populates="personal_skill")

    def __repr__(self) -> str:
        return f"<EmployeePersonalSkill(id={self.id}, employee_id={self.employee_id})>"
