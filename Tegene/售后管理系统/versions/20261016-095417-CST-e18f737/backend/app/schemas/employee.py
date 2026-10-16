"""
模块名称: schemas/employee.py
模块作用: 员工域（Employee Domain）的所有 Pydantic v2 数据传输对象（DTO）定义

功能覆盖:
  - Auth（认证）: LoginRequest / Token / TokenPayload
  - 员工主体: EmployeeBase / EmployeeCreate / EmployeeUpdate / EmployeeOut /
              EmployeeOutSensitive / EmployeeBrief / EmployeeListResponse
  - 状态变更: StatusTransition
  - 合同管理: ContractBase / ContractCreate / ContractRenew / ContractOut / ContractExpiryItem
  - 工作经历: WorkExperienceBase / WorkExperienceCreate / WorkExperienceUpdate / WorkExperienceOut
  - 员工档案: EmployeeDocumentBase / EmployeeDocumentCreate / EmployeeDocumentUpdate / EmployeeDocumentOut
  - 批量操作: BulkOffboardRequest / BulkTransferRequest
  - Excel导入: EmployeeImportErrorItem / EmployeeImportResult
  - 预警: EmployeeAlert
  - 家庭成员: FamilyMemberBase / FamilyMemberCreate / FamilyMemberUpdate / FamilyMemberOut
  - 教育经历: EducationRecordBase / EducationRecordCreate / EducationRecordUpdate / EducationRecordOut
  - 技能特长: EmployeePersonalSkillBase / EmployeePersonalSkillCreate /
              EmployeePersonalSkillUpdate / EmployeePersonalSkillOut
  - 登记表导入: RegistrationFormImportResult

依赖关系:
  - 无依赖其他业务 Schema（Auth 类除外）
  - 被 app/api/v1/endpoints/employee.py 中所有员工相关路由使用
  - 被 app/services/employee_service.py 作为输入/输出类型使用

数据流向:
  HTTP Request Body → *Create/*Update (Pydantic 验证) → Service Layer (ORM)
  ORM Model → *Out (model_config from_attributes=True) → HTTP Response JSON

特别说明:
  - EmployeeOut 会对手机号和身份证号进行脱敏（中间4位/10位替换为*）
  - EmployeeOutSensitive 继承 EmployeeOut 但覆盖 apply_masking 跳过脱敏，需鉴权才能访问
  - EmployeeBase 是所有写操作 Schema 的基类，字段极多（含个人/组织/财务/补充信息四大类）
  - LoginRequest / Token 供 POST /api/v1/auth/login 使用，不属于员工 CRUD 流程
"""
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field, EmailStr, model_validator

from app.core.security import DEFAULT_INITIAL_PASSWORD


# ==================== Auth ====================
# 以下两个 Schema 用于 POST /api/v1/auth/login 接口
# 登录接口接受 JSON body（非 form-urlencoded）

class LoginRequest(BaseModel):
    """
    用途: POST /api/v1/auth/login 的请求体
    业务: 员工使用手机号+密码登录，后端校验后返回 JWT Token
    注意: 接口接受 JSON 格式，不是 OAuth2 form-urlencoded
    """
    phone: str = Field(..., description="登录手机号")
    password: str = Field(..., description="密码")
    remember_me: bool = Field(False, description="是否保持登录；true 时签发较长有效期 Token")


class Token(BaseModel):
    """
    用途: POST /api/v1/auth/login 的响应体
    业务: 登录成功后返回 JWT access_token，前端存储后在后续请求 Authorization 头中携带
    token_type 固定为 "bearer"，符合 OAuth2 Bearer Token 规范
    """
    access_token: str           # JWT 字符串
    token_type: str = "bearer"  # 固定值，供前端拼接 "Bearer <token>"


class TokenPayload(BaseModel):
    """
    用途: JWT Payload 解析时的临时容器（仅内部使用）
    业务: JWT 的 sub 字段存储员工 ID（字符串形式），解码后用于查询当前用户
    注意: sub 为字符串，使用时需 int(sub) 转换（详见 core/deps.py）
    """
    sub: Optional[str] = None   # 员工 ID 字符串，如 "42"


class ChangePasswordRequest(BaseModel):
    """
    用途: POST /api/v1/auth/change-password 的请求体
    业务: 员工在移动端或 Web 个人中心修改自己的登录密码
    """
    old_password: str = Field(..., min_length=1, description="当前密码")
    new_password: str = Field(..., min_length=8, max_length=72, description="新密码，至少8位")


# ==================== Employee ====================

class EmployeeBase(BaseModel):
    """
    用途: 员工写操作 Schema 的公共基类，包含员工档案的全量字段
    业务: EmployeeCreate / EmployeeUpdate 都从此继承或参考此结构
    字段分组:
      - 基础身份: employee_no, name, gender, phone, email, id_card_no, birth_date, photo_url
      - 组织归属: location_id, department_id, position, job_level, employment_type
      - 在职状态: status, hire_date, probation_end_date
      - 紧急联系: emergency_contact, emergency_phone
      - 财务信息: bank_name, bank_account, social_insurance_city, housing_fund_city
      - 驻场信息: is_field_staff, current_project
      - 个人背景: ethnicity, marital_status, education, school, major, current_address,
                  hukou_location, hukou_type
      - 补充信息: fertility_status, political_status, graduation_date, degree_no,
                  has_noncompete, current_position_date
      - 紧急联系扩展: emergency_contact_relation, emergency_phone2
      - 薪酬补充: probation_salary, social_insurance_base
      - 登记表字段: native_place, first_work_date
    """
    employee_no: str = Field(..., max_length=30, description="工号")
    name: str = Field(..., max_length=50, description="姓名")
    gender: Optional[str] = Field(None, max_length=10, description="性别")
    phone: str = Field(..., max_length=20, description="手机号")
    email: Optional[str] = Field(None, max_length=100, description="邮箱")
    id_card_no: Optional[str] = Field(None, max_length=18, description="身份证号")
    birth_date: Optional[date] = Field(None, description="出生日期")
    photo_url: Optional[str] = Field(None, max_length=500, description="照片URL")
    location_id: Optional[int] = Field(None, description="工作地点ID")
    department_id: Optional[int] = Field(None, description="部门ID")
    direct_manager_id: Optional[int] = Field(None, description="直属上级员工ID")
    position: Optional[str] = Field(None, max_length=100, description="职位")
    job_level: Optional[str] = Field(None, max_length=50, description="职级")
    employment_type: str = Field("正式", description="用工类型")
    status: str = Field("待入职", description="员工状态")
    hire_date: Optional[date] = Field(None, description="入职日期")
    probation_end_date: Optional[date] = Field(None, description="试用期结束日期")
    emergency_contact: Optional[str] = Field(None, max_length=50, description="紧急联系人")
    emergency_phone: Optional[str] = Field(None, max_length=20, description="紧急联系人电话")
    bank_name: Optional[str] = Field(None, max_length=100, description="开户行")
    bank_account: Optional[str] = Field(None, max_length=50, description="银行卡号")
    social_insurance_city: Optional[str] = Field(None, max_length=50, description="社保缴纳城市")
    housing_fund_city: Optional[str] = Field(None, max_length=50, description="公积金缴纳城市")
    is_field_staff: bool = Field(False, description="是否驻场人员")
    current_project: Optional[str] = Field(None, max_length=200, description="当前项目")

    # 个人信息补充
    ethnicity: Optional[str] = Field(None, max_length=20, description="族别")
    marital_status: Optional[str] = Field(None, max_length=20, description="婚姻状态：未婚/已婚/离异")
    education: Optional[str] = Field(None, max_length=20, description="最高学历")
    school: Optional[str] = Field(None, max_length=200, description="毕业院校")
    major: Optional[str] = Field(None, max_length=100, description="所学专业")
    current_address: Optional[str] = Field(None, max_length=500, description="现居住地")
    hukou_location: Optional[str] = Field(None, max_length=200, description="户口所在地")
    hukou_type: Optional[str] = Field(None, max_length=20, description="户口性质")
    remark: Optional[str] = Field(None, description="备注")

    # 组织补充
    company: Optional[str] = Field(None, max_length=100, description="所属公司")
    third_level_dept: Optional[str] = Field(None, max_length=100, description="三级部门")

    # 补充个人信息（新增字段）
    fertility_status: Optional[str] = Field(None, max_length=20, description="生育状况：未育/已育/其他")
    political_status: Optional[str] = Field(None, max_length=30, description="政治面貌")
    graduation_date: Optional[date] = Field(None, description="毕业时间")
    degree_no: Optional[str] = Field(None, max_length=100, description="学位证编号")
    has_noncompete: bool = Field(False, description="是否有竞业协议")
    current_position_date: Optional[date] = Field(None, description="本岗位任职时间")

    # 补充紧急联系（新增字段）
    emergency_contact_relation: Optional[str] = Field(None, max_length=20, description="紧急联系人关系")
    emergency_phone2: Optional[str] = Field(None, max_length=20, description="第二紧急联系人电话")

    # 薪酬补充（新增字段）
    probation_salary: Optional[float] = Field(None, description="试用期薪资")
    social_insurance_base: Optional[float] = Field(None, description="社保基数")

    # 登记表补充字段
    native_place: Optional[str] = Field(None, max_length=100, description="籍贯")
    first_work_date: Optional[date] = Field(None, description="首次参加工作时间")


class EmployeeCreate(EmployeeBase):
    """
    用途: POST /api/v1/employees 的请求体（新建员工）
    业务: 在 EmployeeBase 基础上增加初始密码字段
    注意:
      - password 最短6位，不传时统一使用系统初始密码 123456
      - is_superuser 字段有 exclude=True，即使客户端传入也会被忽略，固定为 False
        （超级管理员权限只能通过数据库直接设置）
    """
    password: str = Field(DEFAULT_INITIAL_PASSWORD, min_length=6, description="初始密码")
    is_superuser: bool = Field(False, exclude=True, description="不允许外部指定，固定为False")


class EmployeeUpdate(BaseModel):
    """
    用途: PATCH /api/v1/employees/{employee_id} 的请求体（更新员工信息）
    业务: 所有字段均为可选，只传需要修改的字段（部分更新语义）
    注意:
      - 与 EmployeeBase 不同，此类不继承 EmployeeBase，所有字段独立声明为 Optional
      - is_active 字段仅在此类中出现（EmployeeBase 无此字段），用于启用/禁用账号
      - 不含 password 字段，密码修改走单独接口
      - 不含 employee_no 字段，工号一旦创建不允许修改
    """
    name: Optional[str] = Field(None, max_length=50)
    gender: Optional[str] = Field(None, max_length=10)
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=100)
    id_card_no: Optional[str] = Field(None, max_length=18)
    birth_date: Optional[date] = None
    photo_url: Optional[str] = Field(None, max_length=500)
    location_id: Optional[int] = None
    department_id: Optional[int] = None
    direct_manager_id: Optional[int] = None
    position: Optional[str] = Field(None, max_length=100)
    job_level: Optional[str] = Field(None, max_length=50)
    employment_type: Optional[str] = None
    hire_date: Optional[date] = None
    probation_end_date: Optional[date] = None
    emergency_contact: Optional[str] = Field(None, max_length=50)
    emergency_phone: Optional[str] = Field(None, max_length=20)
    bank_name: Optional[str] = Field(None, max_length=100)
    bank_account: Optional[str] = Field(None, max_length=50)
    social_insurance_city: Optional[str] = Field(None, max_length=50)
    housing_fund_city: Optional[str] = Field(None, max_length=50)
    is_field_staff: Optional[bool] = None
    current_project: Optional[str] = Field(None, max_length=200)
    is_active: Optional[bool] = None  # 启用/禁用账号（EmployeeBase 中无此字段）

    # 个人信息补充
    ethnicity: Optional[str] = Field(None, max_length=20)
    marital_status: Optional[str] = Field(None, max_length=20)
    education: Optional[str] = Field(None, max_length=20)
    school: Optional[str] = Field(None, max_length=200)
    major: Optional[str] = Field(None, max_length=100)
    current_address: Optional[str] = Field(None, max_length=500)
    hukou_location: Optional[str] = Field(None, max_length=200)
    hukou_type: Optional[str] = Field(None, max_length=20)
    remark: Optional[str] = None

    # 组织补充
    company: Optional[str] = Field(None, max_length=100)
    third_level_dept: Optional[str] = Field(None, max_length=100)

    # 补充个人信息（新增字段）
    fertility_status: Optional[str] = Field(None, max_length=20)
    political_status: Optional[str] = Field(None, max_length=30)
    graduation_date: Optional[date] = None
    degree_no: Optional[str] = Field(None, max_length=100)
    has_noncompete: Optional[bool] = None
    current_position_date: Optional[date] = None

    # 补充紧急联系（新增字段）
    emergency_contact_relation: Optional[str] = Field(None, max_length=20)
    emergency_phone2: Optional[str] = Field(None, max_length=20)

    # 薪酬补充（新增字段）
    probation_salary: Optional[float] = None
    social_insurance_base: Optional[float] = None

    # 登记表补充字段
    native_place: Optional[str] = Field(None, max_length=100)
    first_work_date: Optional[date] = None


class EmployeeOut(BaseModel):
    """
    用途: GET /api/v1/employees/{id} 及列表接口的响应体（默认脱敏版本）
    业务: 将 ORM Employee 模型序列化为 JSON，适合一般查看场景
    脱敏规则（由 apply_masking validator 执行）:
      - 手机号: 保留前3位和后4位，中间4位替换为 ****，如 138****1234
      - 身份证: 保留前4位和后4位，中间10位（5对**）替换，如 6101**********1234
    特殊字段说明:
      - leave_date / leave_reason: 仅离职员工有值，EmployeeBase 中无此字段
      - is_active / is_superuser: 系统字段，非业务字段，EmployeeBase 中无
      - location_name / department_name: 由 Service 层关联查询后注入，非 ORM 直接映射
    model_config from_attributes=True: 允许从 SQLAlchemy ORM 实例直接构造
    """
    id: int
    employee_no: str
    name: str
    gender: Optional[str] = None
    phone: str          # 输出时自动脱敏为 138****1234 格式
    email: Optional[str] = None
    id_card_no: Optional[str] = None  # 输出时自动脱敏为 6101**********1234 格式
    birth_date: Optional[date] = None
    photo_url: Optional[str] = None
    location_id: Optional[int] = None
    department_id: Optional[int] = None
    direct_manager_id: Optional[int] = None
    position: Optional[str] = None
    job_level: Optional[str] = None
    employment_type: str
    status: str
    hire_date: Optional[date] = None
    probation_end_date: Optional[date] = None
    leave_date: Optional[date] = None        # 离职日期（仅离职员工有值）
    leave_reason: Optional[str] = None      # 离职原因（仅离职员工有值）
    emergency_contact: Optional[str] = None
    emergency_phone: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account: Optional[str] = None
    social_insurance_city: Optional[str] = None
    housing_fund_city: Optional[str] = None
    is_field_staff: bool
    current_project: Optional[str] = None
    is_active: bool                          # 账号是否启用
    is_superuser: bool                       # 是否超级管理员（通常为 False）

    # 个人信息补充
    ethnicity: Optional[str] = None
    marital_status: Optional[str] = None
    education: Optional[str] = None
    school: Optional[str] = None
    major: Optional[str] = None
    current_address: Optional[str] = None
    hukou_location: Optional[str] = None
    hukou_type: Optional[str] = None
    remark: Optional[str] = None

    # 组织补充
    company: Optional[str] = None
    third_level_dept: Optional[str] = None

    # 补充个人信息（新增字段）
    fertility_status: Optional[str] = None
    political_status: Optional[str] = None
    graduation_date: Optional[date] = None
    degree_no: Optional[str] = None
    has_noncompete: Optional[bool] = None
    current_position_date: Optional[date] = None

    # 补充紧急联系（新增字段）
    emergency_contact_relation: Optional[str] = None
    emergency_phone2: Optional[str] = None

    # 薪酬补充（新增字段）
    probation_salary: Optional[float] = None
    social_insurance_base: Optional[float] = None

    # 登记表补充字段
    native_place: Optional[str] = None
    first_work_date: Optional[date] = None

    created_at: datetime
    updated_at: datetime

    # 嵌套展示组织信息名称（由 API/Service 层关联查询后注入，非 ORM 字段）
    location_name: Optional[str] = None
    department_name: Optional[str] = None
    direct_manager_name: Optional[str] = None

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def apply_masking(self) -> "EmployeeOut":
        """
        脱敏 validator：在对象构造完成后（mode="after"）执行字段脱敏
        - 手机号脱敏: 要求至少7位，保留首3尾4，中间替换为 ****
        - 身份证脱敏: 要求至少10位，保留首4尾4，中间5对 ** 替换
        EmployeeOutSensitive 子类会覆盖此方法以跳过脱敏
        """
        if self.phone and len(self.phone) >= 7:
            self.phone = self.phone[:3] + "****" + self.phone[-4:]
        if self.id_card_no and len(self.id_card_no) >= 10:
            self.id_card_no = self.id_card_no[:4] + "**" * 5 + self.id_card_no[-4:]
        return self


class EmployeeOutSensitive(EmployeeOut):
    """
    用途: 含明文敏感信息的员工详情响应体（需额外鉴权才能访问）
    业务: 与 EmployeeOut 完全相同，唯一区别是覆盖了父类的 apply_masking，
          跳过手机号和身份证号的脱敏，返回原始明文数据
    使用场景: HR 或管理员查看完整档案时使用，普通员工不应调用此接口
    """

    @model_validator(mode="after")
    def apply_masking(self) -> "EmployeeOutSensitive":  # type: ignore[override]
        """覆盖父类：不做脱敏，直接返回原始数据"""
        return self


class EmployeeListItemOut(EmployeeOut):
    """
    用途: 员工管理列表响应项。
    业务: 管理端列表需要展示完整手机号用于测试和办公核对，但其他敏感字段仍按默认规则脱敏。
    """

    @model_validator(mode="after")
    def apply_masking(self) -> "EmployeeListItemOut":  # type: ignore[override]
        """列表页展示完整手机号，但身份证号仍保持脱敏。"""
        if self.id_card_no and len(self.id_card_no) >= 10:
            self.id_card_no = self.id_card_no[:4] + "**" * 5 + self.id_card_no[-4:]
        return self


class EmployeeBrief(BaseModel):
    """
    用途: 员工简要信息，用于列表展示、下拉选择器、关联引用等轻量场景
    业务: 只包含最基础的标识字段，避免传输大量不需要的字段
    使用场景: 部门成员列表、审批人选择、考勤打卡人员列表等
    """
    id: int
    employee_no: str
    name: str
    phone: str
    position: Optional[str] = None
    department_id: Optional[int] = None
    department_name: Optional[str] = None  # 由 Service 层注入
    status: str
    role_names: List[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class EmployeeChangeLogOut(BaseModel):
    """
    用途: 员工详情页展示任职变动历史。
    业务: 记录部门/职位/职级变动前后的快照，以及调动生效时间。
    """
    id: int
    employee_id: int
    change_type: str
    change_type_label: str
    from_department_id: Optional[int] = None
    from_department_name: Optional[str] = None
    to_department_id: Optional[int] = None
    to_department_name: Optional[str] = None
    from_position: Optional[str] = None
    to_position: Optional[str] = None
    from_job_level: Optional[str] = None
    to_job_level: Optional[str] = None
    effective_date: datetime
    operator_id: Optional[int] = None
    operator_name: Optional[str] = None
    remark: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class EmployeeListResponse(BaseModel):
    """
    用途: GET /api/v1/employees 分页列表接口的响应体
    业务: 包含总数和当前页数据，前端据此计算总页数和渲染分页控件
    注意: items 中手机号用于管理端完整展示，身份证号等其他敏感字段仍脱敏
    """
    total: int               # 符合查询条件的总记录数（非当前页数量）
    items: List[EmployeeListItemOut] # 当前页员工列表


# ==================== 状态变更 ====================

class StatusTransition(BaseModel):
    """
    用途: POST /api/v1/employees/{id}/status 员工状态变更接口的请求体
    业务: 员工状态机流转，支持以下目标状态：
      - "试用期": 入职后进入试用
      - "在职": 试用期转正
      - "离职": 员工离职（需提供 leave_date，建议提供 leave_reason）
    注意:
      - 离职时 leave_date 必须填写（由 Service 层校验，Schema 层仅设 Optional）
      - leave_reason 建议填写，最大500字
    """
    new_status: str = Field(..., description="目标状态：试用期/在职/离职")
    leave_date: Optional[date] = Field(None, description="离职日期（离职时必填）")
    leave_reason: Optional[str] = Field(None, max_length=500, description="离职原因")


# ==================== Contract ====================

class ContractBase(BaseModel):
    """
    用途: 员工合同写操作 Schema 的基类
    业务: 描述一份劳动合同的完整信息，包括类型、期限、签署日期、文件及续签信息
    合同类型枚举（contract_type 字段业务值）:
      - "劳动合同": 公司与员工建立劳动关系的合同
      - "劳务合同": 公司与个人/劳务人员建立劳务关系的合同
    续签相关字段说明:
      - contract_renewal_date: 续签生效日期（新一份合同的开始日期）
      - contract_renewal_end_date: 续签合同的到期日期
      - contract_renewal_reminder: 系统发送续签提醒的触发日期（早于 end_date N天）
    """
    employee_id: int = Field(..., description="员工ID")
    contract_type: str = Field(..., description="合同类型：劳动合同/劳务合同")
    contract_no: Optional[str] = Field(None, max_length=50, description="合同编号")
    start_date: date = Field(..., description="开始日期")
    end_date: Optional[date] = Field(None, description="结束日期")
    sign_date: Optional[date] = Field(None, description="签署日期")
    file_url: Optional[str] = Field(None, max_length=500, description="合同文件URL")
    contract_renewal_date: Optional[date] = Field(None, description="续签开始日期")
    contract_renewal_end_date: Optional[date] = Field(None, description="续签结束日期")
    contract_renewal_reminder: Optional[date] = Field(None, description="续签提醒日期")
    status: Optional[str] = Field("生效中", description="合同状态：生效中/已到期/已解除")


class ContractCreate(ContractBase):
    """
    用途: POST /api/v1/employees/{id}/contracts 新建合同的请求体
    业务: 直接继承 ContractBase，无额外字段
    """
    pass


class ContractRenew(BaseModel):
    """
    用途: POST /api/v1/contracts/{id}/renew 续签合同的请求体
    业务: 只需提供新的到期日和续签签署日期，后端自动处理合同状态和续签计数
    注意: 续签会将原合同状态置为 renewed，并创建或更新续签日期字段
    """
    new_end_date: date = Field(..., description="新的结束日期")
    sign_date: Optional[date] = Field(None, description="续签签署日期")


class ContractOut(BaseModel):
    """
    用途: 合同详情接口的响应体，从 ORM Contract 模型序列化
    业务: 包含合同全量信息，renewal_count 表示已续签次数（初始为0）
    status 字段可能的值: 生效中 / 已到期 / 已解除
    """
    id: int
    employee_id: int
    contract_type: str
    contract_no: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None
    renewal_count: int                           # 已续签次数，0表示首签
    sign_date: Optional[date] = None
    file_url: Optional[str] = None
    contract_renewal_date: Optional[date] = None
    contract_renewal_end_date: Optional[date] = None
    contract_renewal_reminder: Optional[date] = None
    status: str                                  # 合同当前状态
    created_at: datetime

    model_config = {"from_attributes": True}


class ContractExpiryItem(BaseModel):
    """
    用途: 合同即将到期预警列表中的单条记录
    业务: 由定时任务或 GET /api/v1/contracts/expiring 接口返回，
          列出 N 天内到期的合同，HR 可提前安排续签
    days_remaining: 距到期日的剩余天数（已到期则为负数）
    """
    contract_id: int
    contract_no: Optional[str] = None
    employee_id: int
    employee_no: str
    employee_name: str
    end_date: date
    days_remaining: int       # 剩余天数，负数表示已过期

    model_config = {"from_attributes": True}


# ==================== WorkExperience ====================

class WorkExperienceBase(BaseModel):
    """
    用途: 员工工作经历写操作 Schema 的基类
    业务: 描述员工在加入本公司之前的一段工作经历
    字段说明:
      - is_current: True 表示该工作经历对应当前工作（即本公司），end_date 可为空
      - referee_info: 旧版证明人字段（单字段存名称+电话），新版用 reference_name + reference_phone
      - sort_order: 前端排序序号，通常按时间倒序（最近的排在前面，sort_order 较小）
    """
    employee_id: int = Field(..., description="员工ID")
    start_date: Optional[date] = Field(None, description="开始日期")
    end_date: Optional[date] = Field(None, description="结束日期（在职可为空）")
    company_name: str = Field(..., max_length=200, description="公司名称")
    industry: Optional[str] = Field(None, max_length=100, description="公司行业")
    position: Optional[str] = Field(None, max_length=100, description="职位")
    responsibilities: Optional[str] = Field(None, description="主要职责")
    is_current: bool = Field(False, description="是否在职")
    leave_reason: Optional[str] = Field(None, max_length=500, description="离职原因")
    previous_salary: Optional[float] = Field(None, description="上家薪资")
    referee_info: Optional[str] = Field(None, max_length=200, description="证明人及联系方式（旧字段）")
    reference_name: Optional[str] = Field(None, max_length=50, description="证明人姓名")
    reference_phone: Optional[str] = Field(None, max_length=30, description="证明人电话")
    sort_order: int = Field(0, description="排序序号")


class WorkExperienceCreate(WorkExperienceBase):
    """
    用途: POST /api/v1/employees/{id}/work-experiences 新建工作经历的请求体
    """
    pass


class WorkExperienceUpdate(BaseModel):
    """
    用途: PATCH /api/v1/work-experiences/{id} 更新工作经历的请求体
    业务: 所有字段均可选，只传需要修改的字段（不含 employee_id，绑定关系不可变）
    """
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    company_name: Optional[str] = Field(None, max_length=200)
    industry: Optional[str] = Field(None, max_length=100)
    position: Optional[str] = Field(None, max_length=100)
    responsibilities: Optional[str] = None
    is_current: Optional[bool] = None
    leave_reason: Optional[str] = Field(None, max_length=500)
    previous_salary: Optional[float] = None
    referee_info: Optional[str] = Field(None, max_length=200)
    reference_name: Optional[str] = Field(None, max_length=50)
    reference_phone: Optional[str] = Field(None, max_length=30)
    sort_order: Optional[int] = None


class WorkExperienceOut(BaseModel):
    """
    用途: 工作经历详情/列表接口的响应体，从 ORM WorkExperience 模型序列化
    """
    id: int
    employee_id: int
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    company_name: str
    industry: Optional[str] = None
    position: Optional[str] = None
    responsibilities: Optional[str] = None
    is_current: bool = False
    leave_reason: Optional[str] = None
    previous_salary: Optional[float] = None
    referee_info: Optional[str] = None
    reference_name: Optional[str] = None
    reference_phone: Optional[str] = None
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ==================== EmployeeDocument ====================

class EmployeeDocumentBase(BaseModel):
    """
    用途: 员工档案材料（入职必交材料）写操作 Schema 的基类
    业务: 记录员工应提交的各类证件材料及其提交状态
    doc_type 枚举值（材料类型）:
      - 员工信息表 / 身份证复印件 / 银行卡复印件 / 离职证明
      - 学历复印件 / 学位复印件 / 体检报告
      - 劳动合同书 / 保密及竞业协议 / 实习协议 / 其他
    is_submitted: False=未提交（待收集），True=已提交（材料到位）
    file_url: 电子版材料的存储 URL（可为空，纸质材料不需要）
    """
    employee_id: int = Field(..., description="员工ID")
    doc_type: str = Field(
        ..., max_length=50,
        description="材料类型：员工信息表/身份证复印件/银行卡复印件/离职证明/学历复印件/学位复印件/体检报告/劳动合同书/保密及竞业协议/实习协议/其他",
    )
    is_submitted: bool = Field(False, description="是否已提交")
    file_url: Optional[str] = Field(None, max_length=500, description="文件URL")
    remark: Optional[str] = Field(None, description="备注")


class EmployeeDocumentCreate(EmployeeDocumentBase):
    """
    用途: POST /api/v1/employees/{id}/documents 新建档案材料记录的请求体
    """
    pass


class EmployeeDocumentUpdate(BaseModel):
    """
    用途: PATCH /api/v1/documents/{id} 更新档案材料状态的请求体
    业务: 常见场景为 HR 确认材料已收到时将 is_submitted 置为 True，并上传 file_url
    """
    doc_type: Optional[str] = Field(None, max_length=50)
    is_submitted: Optional[bool] = None
    file_url: Optional[str] = Field(None, max_length=500)
    remark: Optional[str] = None


class EmployeeDocumentOut(BaseModel):
    """
    用途: 档案材料详情/列表接口的响应体，从 ORM EmployeeDocument 模型序列化
    """
    id: int
    employee_id: int
    doc_type: str
    is_submitted: bool
    file_url: Optional[str] = None
    remark: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ==================== 批量操作 ====================

class BulkOffboardRequest(BaseModel):
    """
    用途: POST /api/v1/employees/bulk-offboard 批量离职接口的请求体
    业务: HR 可一次性对多名员工执行离职操作，适用于裁员或集中离职场景
    注意: 所有员工使用相同的 leave_date 和 leave_reason，若需要不同原因应逐个操作
    """
    employee_ids: List[int] = Field(..., description="员工ID列表")
    leave_date: date = Field(..., description="离职日期")
    leave_reason: Optional[str] = Field(None, max_length=500, description="离职原因")


class BulkTransferRequest(BaseModel):
    """
    用途: POST /api/v1/employees/bulk-transfer 批量调岗接口的请求体
    业务: HR 可一次性将多名员工调整到同一部门或职位
    注意: department_id 和 position 至少应提供其中一个（由 Service 层校验）
    """
    employee_ids: List[int] = Field(..., description="员工ID列表")
    department_id: Optional[int] = Field(None, description="新部门ID")
    position: Optional[str] = Field(None, max_length=100, description="新职位")
    effective_date: Optional[date] = Field(None, description="调岗生效日期（默认当天）")
    remark: Optional[str] = Field(None, max_length=500, description="调岗备注")


class TransferRequest(BaseModel):
    """
    用途: POST /api/v1/employees/{id}/transfer 单人调岗接口请求体
    业务: 支持同部门换岗、跨部门调动；至少需要传 department_id 或 position 任意一项
    """
    department_id: Optional[int] = Field(None, description="新部门ID")
    position: Optional[str] = Field(None, max_length=100, description="新职位")
    effective_date: Optional[date] = Field(None, description="调岗生效日期（默认当天）")
    remark: Optional[str] = Field(None, max_length=500, description="调岗备注")

    @model_validator(mode="after")
    def validate_transfer_fields(self):
        if self.department_id is None and not (self.position and self.position.strip()):
            raise ValueError("调岗至少需要填写新部门或新职位")
        return self


class OffboardRequest(BaseModel):
    """
    用途: POST /api/v1/employees/{id}/offboard 单人离职接口请求体
    业务: 执行离职状态流转，并记录离职日期与原因
    """
    leave_date: date = Field(..., description="离职日期")
    leave_reason: Optional[str] = Field(None, max_length=500, description="离职原因")


# ==================== Excel 导入结果 ====================

class EmployeeImportErrorItem(BaseModel):
    """
    用途: Excel 批量导入员工时，单条错误信息的描述
    业务: 记录哪一行（row 从2开始，1为表头）出现了什么问题（reason）
    """
    row: int      # Excel 行号（从2开始，1=表头）
    reason: str   # 错误描述，如"工号已存在"、"手机号格式错误"


class EmployeeImportResult(BaseModel):
    """
    用途: POST /api/v1/employees/import 批量导入接口的响应体
    业务: 汇总本次导入的成功/失败数量，并列出所有失败行的详情
    success + failed 应等于 Excel 中的有效数据行总数
    """
    success: int                           # 成功导入的员工数
    failed: int                            # 导入失败的行数
    errors: List[EmployeeImportErrorItem]  # 失败详情列表


# ==================== 预警 ====================

class EmployeeAlert(BaseModel):
    """
    用途: GET /api/v1/employees/alerts 员工预警列表中的单条预警记录
    业务: 系统自动生成的预警，HR 需要关注并采取相应行动
    alert_type 可能的值:
      - "contract_expiry": 合同即将到期（days_remaining 为剩余天数）
      - "probation_end": 试用期即将结束（需要 HR 发起转正审批）
      - "birthday": 员工生日提醒（alert_date 为生日日期）
    """
    employee_id: int
    employee_no: str
    employee_name: str
    alert_type: str  # contract_expiry | probation_end | birthday
    alert_message: str                   # 预警说明文字，如"合同将于30天后到期"
    alert_date: Optional[date] = None    # 预警触发的关键日期
    days_remaining: Optional[int] = None # 距关键日期的剩余天数（负数=已过期）


# ==================== FamilyMember ====================

class FamilyMemberBase(BaseModel):
    """
    用途: 员工家庭成员写操作 Schema 的基类
    业务: 记录员工的家庭成员信息，用于紧急联系、员工福利、背景调查等
    relation 枚举值: 父/母/配偶/子女/兄弟姐妹/其他
    age 与 birth_date: 两者均可使用，age 为兼容旧数据保留，建议优先使用 birth_date
    is_emergency_contact: 标记该家庭成员是否作为紧急联系人，应与 employee.emergency_contact 保持一致
    """
    employee_id: int = Field(..., description="员工ID")
    name: str = Field(..., max_length=50, description="姓名")
    relation: Optional[str] = Field(None, max_length=30, description="关系（父/母/配偶/子女/兄弟姐妹/其他）")
    age: Optional[int] = Field(None, ge=0, le=150, description="年龄（兼容旧数据）")
    birth_date: Optional[date] = Field(None, description="出生日期")
    is_emergency_contact: bool = Field(False, description="是否紧急联系人")
    work_unit: Optional[str] = Field(None, max_length=200, description="工作单位")
    position: Optional[str] = Field(None, max_length=100, description="职务")
    phone: Optional[str] = Field(None, max_length=30, description="联系方式")


class FamilyMemberCreate(FamilyMemberBase):
    """
    用途: POST /api/v1/employees/{id}/family-members 新建家庭成员的请求体
    """
    pass


class FamilyMemberUpdate(BaseModel):
    """
    用途: PATCH /api/v1/family-members/{id} 更新家庭成员信息的请求体
    业务: 不含 employee_id（归属关系不可变）
    """
    name: Optional[str] = Field(None, max_length=50)
    relation: Optional[str] = Field(None, max_length=30)
    age: Optional[int] = Field(None, ge=0, le=150)
    birth_date: Optional[date] = None
    is_emergency_contact: Optional[bool] = None
    work_unit: Optional[str] = Field(None, max_length=200)
    position: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=30)


class FamilyMemberOut(BaseModel):
    """
    用途: 家庭成员详情/列表接口的响应体，从 ORM FamilyMember 模型序列化
    """
    id: int
    employee_id: int
    name: str
    relation: Optional[str] = None
    age: Optional[int] = None
    birth_date: Optional[date] = None
    is_emergency_contact: bool = False
    work_unit: Optional[str] = None
    position: Optional[str] = None
    phone: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ==================== EducationRecord ====================

class EducationRecordBase(BaseModel):
    """
    用途: 员工教育经历写操作 Schema 的基类
    业务: 记录员工的学历、培训、证书等教育背景
    study_type 枚举值: 全日制/非全日制/自考/其他
    is_highest: 标记为最高学历，前端展示时可优先展示（每个员工建议只有一条最高学历）
    certificate_name: 所获证书名称（如：机器人工程师证、PMP证书等）
    sort_order: 排序，通常按时间倒序
    """
    employee_id: int = Field(..., description="员工ID")
    start_date: Optional[date] = Field(None, description="开始日期")
    end_date: Optional[date] = Field(None, description="结束日期")
    school: str = Field(..., max_length=200, description="学校/培训机构")
    major: Optional[str] = Field(None, max_length=200, description="专业/培训内容")
    study_type: Optional[str] = Field(None, max_length=20, description="学历类型：全日制/非全日制/自考/其他")
    degree: Optional[str] = Field(None, max_length=100, description="所获学历/资质/学位")
    is_highest: bool = Field(False, description="是否最高学历")
    certificate_name: Optional[str] = Field(None, max_length=200, description="所获证书名称")
    sort_order: int = Field(0, description="排序序号")


class EducationRecordCreate(EducationRecordBase):
    """
    用途: POST /api/v1/employees/{id}/education-records 新建教育经历的请求体
    """
    pass


class EducationRecordUpdate(BaseModel):
    """
    用途: PATCH /api/v1/education-records/{id} 更新教育经历的请求体
    业务: 不含 employee_id（归属关系不可变）
    """
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    school: Optional[str] = Field(None, max_length=200)
    major: Optional[str] = Field(None, max_length=200)
    study_type: Optional[str] = Field(None, max_length=20)
    degree: Optional[str] = Field(None, max_length=100)
    is_highest: Optional[bool] = None
    certificate_name: Optional[str] = Field(None, max_length=200)
    sort_order: Optional[int] = None


class EducationRecordOut(BaseModel):
    """
    用途: 教育经历详情/列表接口的响应体，从 ORM EducationRecord 模型序列化
    """
    id: int
    employee_id: int
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    school: str
    major: Optional[str] = None
    study_type: Optional[str] = None
    degree: Optional[str] = None
    is_highest: bool = False
    certificate_name: Optional[str] = None
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ==================== EmployeeSkill ====================

class EmployeePersonalSkillBase(BaseModel):
    """
    用途: 员工个人技能特长写操作 Schema 的基类
    业务: 记录员工的技能特长，用于人才盘点、内部项目匹配等场景
    skill_tags: JSON 数组字符串，格式为 [{name: "Python", proficiency: "熟练"}, ...]
                前端应负责将 skill_tags 序列化为 JSON 字符串再传入，接收时反序列化
    computer_level: 计算机水平描述，如"熟练使用Office"、"精通Python/Java"
    foreign_language + foreign_language_level: 外语种类和级别，如"英语"+"CET-6"
    """
    employee_id: int = Field(..., description="员工ID")
    computer_level: Optional[str] = Field(None, max_length=100, description="计算机水平")
    foreign_language: Optional[str] = Field(None, max_length=50, description="外语语种")
    foreign_language_level: Optional[str] = Field(None, max_length=50, description="外语级别")
    other_skills: Optional[str] = Field(None, description="其它技能描述")
    skill_tags: Optional[str] = Field(None, description="技能标签（JSON数组，格式：[{name,proficiency}]）")
    hobbies: Optional[str] = Field(None, description="个人爱好特长")


class EmployeePersonalSkillCreate(EmployeePersonalSkillBase):
    """
    用途: POST /api/v1/employees/{id}/skills 新建技能特长记录的请求体
    """
    pass


class EmployeePersonalSkillUpdate(BaseModel):
    """
    用途: PATCH /api/v1/employees/{id}/skills 更新技能特长的请求体
    业务: 技能记录为一员工一条（upsert 语义），所有字段可选
    """
    computer_level: Optional[str] = Field(None, max_length=100)
    foreign_language: Optional[str] = Field(None, max_length=50)
    foreign_language_level: Optional[str] = Field(None, max_length=50)
    other_skills: Optional[str] = None
    skill_tags: Optional[str] = None
    hobbies: Optional[str] = None


class EmployeePersonalSkillOut(BaseModel):
    """
    用途: 技能特长详情接口的响应体，从 ORM EmployeePersonalSkill 模型序列化
    """
    id: int
    employee_id: int
    computer_level: Optional[str] = None
    foreign_language: Optional[str] = None
    foreign_language_level: Optional[str] = None
    other_skills: Optional[str] = None
    skill_tags: Optional[str] = None     # JSON 字符串，前端需反序列化
    hobbies: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ==================== 登记表导入结果 ====================

class RegistrationFormImportResult(BaseModel):
    """
    用途: POST /api/v1/employees/{id}/import-registration-form
          员工登记表（XLS 格式）导入接口的响应体
    业务: 导入一份员工登记表后，后端解析并写入基础信息、家庭成员、
          工作经历、教育经历、技能信息，此 Schema 汇总各部分的导入结果
    字段说明:
      - employee_id / employee_no / name: 匹配到的员工标识信息（若匹配失败则为 None）
      - success: 是否整体导入成功
      - message: 导入结果说明（成功或失败原因）
      - family_members_imported: 成功写入的家庭成员条数
      - work_experiences_imported: 成功写入的工作经历条数
      - education_records_imported: 成功写入的教育经历条数
      - skills_imported: 技能信息是否成功写入（True/False）
    """
    employee_id: Optional[int] = None
    employee_no: Optional[str] = None
    name: Optional[str] = None
    success: bool
    message: str
    family_members_imported: int = 0        # 写入的家庭成员条数
    work_experiences_imported: int = 0      # 写入的工作经历条数
    education_records_imported: int = 0     # 写入的教育经历条数
    skills_imported: bool = False           # 技能信息是否写入
