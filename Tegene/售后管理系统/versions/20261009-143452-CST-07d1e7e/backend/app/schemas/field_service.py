from datetime import date
from pydantic import BaseModel, Field, model_validator, field_validator
from app.schemas.field_roster import RosterFields


class CustomerInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    contact: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=40)
    is_active: bool = True


class ProjectInput(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=150)
    customer_id: int = Field(gt=0)
    manager_id: int = Field(gt=0)
    description: str = Field(default="", max_length=2000)
    is_active: bool = True


class TegeneProjectInput(BaseModel):
    manager_id: int = Field(gt=0)


class ProjectConfigurationInput(BaseModel):
    manager_id: int | None = Field(default=None, gt=0)
    rule_id: int | None = Field(default=None, gt=0)


class SiteInput(BaseModel):
    project_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=150)
    address: str = Field(default="", max_length=500)
    rule_id: int = Field(gt=0)
    is_active: bool = True


class AssignmentInput(BaseModel):
    site_id: int = Field(gt=0)
    employee_id: int = Field(gt=0)
    start_date: date
    end_date: date
    description: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def check_dates(self):
        if (
            self.end_date < self.start_date
            or (self.end_date - self.start_date).days > 365
        ):
            raise ValueError("参与起止日期无效，单次最多366天")
        return self


class ProjectMembersInput(BaseModel):
    employee_ids: list[int] = Field(min_length=1, max_length=100)
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def check_members(self):
        if len(set(self.employee_ids)) != len(self.employee_ids) or any(i <= 0 for i in self.employee_ids):
            raise ValueError("请选择不重复的有效人员")
        if self.start_date or self.end_date:
            if not self.start_date or not self.end_date:
                raise ValueError("起止日期须同时填写")
            AssignmentInput(site_id=1, employee_id=1, start_date=self.start_date, end_date=self.end_date)
        return self


class TransferInput(BaseModel):
    project_id: int = Field(gt=0)


class PersonInput(RosterFields):
    employee_no: str = Field(default="", max_length=30)
    name: str = Field(min_length=1, max_length=50)
    phone: str = Field(min_length=1, max_length=20)
    position: str = Field(default="", max_length=100)
    department_id: int | None = None
    direct_manager_id: int | None = None
    is_active: bool = True
    status: str = Field(default="在职", pattern="^(在职|试用|离职)$")
    password: str | None = Field(default=None, min_length=8, max_length=72)
    wecom_userid: str | None = Field(default=None, max_length=128)
    role: str = Field(default="employee", pattern="^(employee|manager|hr|admin)$")

    @field_validator("status", mode="before")
    @classmethod
    def normalize_probation(cls, value):
        return "试用" if value == "试用期" else value


class PositionInput(BaseModel):
    department_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def trim_name(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("请填写岗位名称")
        return self
