"""Administrator-managed annual legal holidays for field attendance."""

import json
from datetime import date

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.employee import Employee
from app.models.system_setting import SystemSetting


class LegalHolidayEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    holiday_name: str = Field(min_length=1, max_length=100)

    @field_validator("holiday_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        name = value.strip()
        if not name or any(ord(character) < 32 for character in name):
            raise ValueError("节日名称不能为空或包含控制字符")
        return name


class LegalHolidayYearUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[LegalHolidayEntry] = Field(max_length=366)
    expected_revision: int = Field(ge=0)


class LegalHolidayYearOut(BaseModel):
    year: int
    entries: list[LegalHolidayEntry]
    revision: int
    source: str


INITIAL_2026_HOLIDAYS = [
    LegalHolidayEntry(date=date(2026, 1, 1), holiday_name="元旦"),
    LegalHolidayEntry(date=date(2026, 2, 17), holiday_name="正月初一"),
    LegalHolidayEntry(date=date(2026, 2, 18), holiday_name="正月初二"),
    LegalHolidayEntry(date=date(2026, 2, 19), holiday_name="正月初三"),
    LegalHolidayEntry(date=date(2026, 5, 1), holiday_name="劳动节"),
    LegalHolidayEntry(date=date(2026, 5, 2), holiday_name="劳动节"),
    LegalHolidayEntry(date=date(2026, 6, 19), holiday_name="端午节"),
    LegalHolidayEntry(date=date(2026, 9, 25), holiday_name="中秋节"),
    LegalHolidayEntry(date=date(2026, 10, 1), holiday_name="国庆节"),
    LegalHolidayEntry(date=date(2026, 10, 2), holiday_name="国庆假期"),
    LegalHolidayEntry(date=date(2026, 10, 3), holiday_name="国庆假期"),
]


class LegalHolidayService:
    @staticmethod
    def validate_year(year: int) -> None:
        if not 1900 <= year <= 2100:
            raise HTTPException(422, "年份需在 1900 至 2100 之间")

    @staticmethod
    def setting_name(year: int) -> str:
        return f"legal_holidays_{year}"

    @staticmethod
    def _decode(year: int, saved: SystemSetting | None) -> LegalHolidayYearOut:
        if saved is None:
            return LegalHolidayYearOut(
                year=year,
                entries=list(INITIAL_2026_HOLIDAYS) if year == 2026 else [],
                revision=0,
                source="default" if year == 2026 else "empty",
            )
        payload = json.loads(saved.value)
        return LegalHolidayYearOut(
            year=year,
            entries=[LegalHolidayEntry.model_validate(item) for item in payload["entries"]],
            revision=payload["revision"],
            source="configured",
        )

    @staticmethod
    async def get_year(db: AsyncSession, year: int) -> LegalHolidayYearOut:
        LegalHolidayService.validate_year(year)
        cache = getattr(db, "info", None)
        cached_years = cache.setdefault("legal_holiday_years", {}) if isinstance(cache, dict) else {}
        if year not in cached_years:
            saved = (await db.execute(select(SystemSetting).where(SystemSetting.name == LegalHolidayService.setting_name(year)))).scalar_one_or_none()
            cached_years[year] = LegalHolidayService._decode(year, saved)
        return cached_years[year].model_copy(deep=True)

    @staticmethod
    async def put_year(
        db: AsyncSession,
        year: int,
        change: LegalHolidayYearUpdate,
        operator: Employee,
    ) -> LegalHolidayYearOut:
        LegalHolidayService.validate_year(year)
        dates: set[date] = set()
        for entry in change.entries:
            if entry.date.year != year:
                raise HTTPException(422, "日期必须属于所选年份")
            if entry.date in dates:
                raise HTTPException(422, "同一天不能重复录入")
            dates.add(entry.date)

        setting_name = LegalHolidayService.setting_name(year)
        saved = await db.get(SystemSetting, setting_name)
        before = LegalHolidayService._decode(year, saved)
        if change.expected_revision != before.revision:
            raise HTTPException(409, "节假日表已被其他管理员修改，请刷新后重试")

        after = LegalHolidayYearOut(
            year=year,
            entries=sorted(change.entries, key=lambda entry: entry.date),
            revision=before.revision + 1,
            source="configured",
        )
        value = json.dumps(
            {
                "revision": after.revision,
                "entries": [entry.model_dump(mode="json") for entry in after.entries],
            },
            ensure_ascii=False,
        )
        if saved is None:
            try:
                async with db.begin_nested():
                    db.add(SystemSetting(name=setting_name, value=value))
                    await db.flush()
            except IntegrityError as exc:
                raise HTTPException(409, "节假日表已被其他管理员修改，请刷新后重试") from exc
        else:
            result = await db.execute(
                update(SystemSetting)
                .where(SystemSetting.name == setting_name, SystemSetting.value == saved.value)
                .values(value=value)
            )
            if result.rowcount != 1:
                raise HTTPException(409, "节假日表已被其他管理员修改，请刷新后重试")

        cache = getattr(db, "info", None)
        if isinstance(cache, dict):
            cache.get("legal_holiday_years", {}).pop(year, None)

        db.add(AuditLog(
            operator_id=operator.id,
            operator_name=operator.name,
            action="UPDATE",
            module="attendance",
            resource_type="LegalHolidayYear",
            resource_id=year,
            before_data=before.model_dump_json(),
            after_data=after.model_dump_json(),
        ))
        return after
