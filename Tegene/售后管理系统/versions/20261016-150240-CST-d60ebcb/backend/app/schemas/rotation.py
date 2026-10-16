from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, model_validator, field_validator


class ReasonInput(BaseModel):
    @field_validator("reason", "note", check_fields=False, mode="before")
    @classmethod
    def clean_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class DayEdit(BaseModel):
    date: date
    kind: Literal["work", "rest"]
    shift_label: Literal["day", "night"] | None = None
    expected_revision: int | None = None


class PersonDayEdit(DayEdit):
    employee_id: int = Field(gt=0)


class RotationPublish(ReasonInput):
    employee_ids: list[int] = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date
    cycle_start: date
    edits: list[DayEdit] = Field(default_factory=list, max_length=366)
    reason: str = Field(default="周期排班", min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_range(self):
        if not 0 <= (self.end_date - self.start_date).days <= 365 or self.cycle_start > self.start_date:
            raise ValueError("排班范围须为1至366天，周期起点不能晚于开始日期")
        if len({d.date for d in self.edits}) != len(self.edits) or any(not self.start_date <= d.date <= self.end_date for d in self.edits):
            raise ValueError("调整日期重复或不在排班范围内")
        return self


class RotationAdjust(ReasonInput):
    employee_ids: list[int] = Field(default_factory=list, max_length=100)
    days: list[DayEdit] = Field(default_factory=list, max_length=366)
    changes: list[PersonDayEdit] = Field(default_factory=list, max_length=1000)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_changes(self):
        if self.changes:
            if self.employee_ids or self.days or len({(d.employee_id, d.date) for d in self.changes}) != len(self.changes):
                raise ValueError("请使用一份不重复的人员日期调整清单")
        elif not self.employee_ids or not self.days:
            raise ValueError("请选择人员和日期")
        return self


class ClaimDay(BaseModel):
    date: date
    kind: Literal["work", "rest", "leave", "half_leave"]
    project_id: int | None = Field(default=None, gt=0)
    shift_label: Literal["day", "night"] | None = None


class ClaimInput(ReasonInput):
    project_id: int | None = Field(default=None, gt=0)
    employee_id: int | None = None
    kind: Literal["schedule", "credit", "leave"]
    days: list[ClaimDay] = Field(min_length=1, max_length=62)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_days(self):
        if self.project_id is None and any(d.project_id is None for d in self.days):
            raise ValueError("跨项目申请须为每个日期指定项目")
        allowed = {"schedule": {"work", "rest"}, "credit": {"work"}, "leave": {"leave", "half_leave"}}[self.kind]
        if len({d.date for d in self.days}) != len(self.days) or any(d.kind not in allowed for d in self.days):
            raise ValueError("申请日期重复或日期类型不符合申请类型")
        if self.kind == "credit" and len(self.days) != 1:
            raise ValueError("休息日出勤须逐日确认")
        return self


class ClaimReview(ReasonInput):
    action: Literal["approve", "reject", "revoke"]
    days: Literal[0, 0.5, 1] | None = None
    expected_step: int | None = Field(default=None, ge=0)
    note: str = Field(min_length=1, max_length=500)
