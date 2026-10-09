"""Fixed roster columns, in the order of the supplied 售后名册 template."""
from datetime import date, datetime
from pydantic import BaseModel, Field, field_validator, model_validator

ROSTER_COLUMNS = [
    ("name", "姓名", "text"), ("position", "职位", "text"),
    ("department_name", "所属部门", "text"), ("company", "所属公司", "text"),
    ("current_project", "当前所在项目", "text"), ("hire_date", "入职日期", "date"),
    ("leave_date", "离职日期", "date"), ("aftersales_leader", "售后组长", "text"),
    ("phone", "联系电话", "text"), ("clothing_size", "衣服尺码", "text"),
    ("id_card_no", "身份证号", "text"), ("age", "年龄", "number"),
    ("wecom_joined", "是否进入企微", "text"), ("days_employed", "入职天数", "number"),
    ("company_computer", "公司电脑", "text"), ("summer_uniform", "夏季工服", "text"),
    ("reflective_vest", "反光马甲", "text"), ("computer_advice", "电脑建议", "text"),
    ("status", "公司在职状态", "status"),
]


class RosterDetails(BaseModel):
    department_name: str | None = Field(default=None, max_length=100)
    aftersales_leader: str | None = Field(default=None, max_length=100)
    clothing_size: str | None = Field(default=None, max_length=30)
    age: int | None = Field(default=None, ge=0, le=150)
    wecom_joined: str | None = Field(default=None, max_length=30)
    days_employed: int | None = Field(default=None, ge=0, le=60000)
    company_computer: str | None = Field(default=None, max_length=200)
    summer_uniform: str | None = Field(default=None, max_length=100)
    reflective_vest: str | None = Field(default=None, max_length=100)
    computer_advice: str | None = Field(default=None, max_length=1000)


class RosterFields(RosterDetails):
    company: str | None = Field(default=None, max_length=100)
    current_project: str | None = Field(default=None, max_length=200)
    hire_date: date | None = None
    leave_date: date | None = None
    id_card_no: str | None = Field(default=None, pattern=r"^(\d{15}|\d{17}[\dXx])$")

    @field_validator(*RosterDetails.model_fields, "company", "current_project", "hire_date", "leave_date", "id_card_no", mode="before")
    @classmethod
    def normalize(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("="):
                raise ValueError("请填写实际值，不接受公式")
        return None if value == "" else value

    @field_validator("hire_date", "leave_date", mode="before")
    @classmethod
    def normalize_date(cls, value):
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(value.strip(), fmt).date()
                except ValueError:
                    pass
        return value

    @model_validator(mode="after")
    def date_order(self):
        if self.hire_date and self.leave_date and self.leave_date < self.hire_date:
            raise ValueError("离职日期不能早于入职日期")
        return self
