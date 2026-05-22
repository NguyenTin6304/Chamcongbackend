from datetime import date, datetime, time
import re
from typing import Optional

from pydantic import BaseModel, Field, field_serializer, field_validator


def _normalize_phone(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    phone = re.sub(r"[\s.-]", "", value.strip())
    if not phone:
        return None
    if not re.fullmatch(r"\d{10,11}", phone):
        raise ValueError("Sô điện thoại phải chứa từ 10 đến 11 chữ số")
    return phone


class EmployeeCreateRequest(BaseModel):
    code: str
    full_name: str
    phone: Optional[str] = None
    user_id: Optional[int] = None
    group_id: Optional[int] = None
    annual_leave_days: Optional[float] = None  # None → use company default

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_phone(value)


class EmployeeAssignUserRequest(BaseModel):
    user_id: Optional[int] = None


class EmployeeAssignGroupRequest(BaseModel):
    group_id: Optional[int] = None


class EmployeeUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    group_id: Optional[int] = None
    user_id: Optional[int] = None
    active: Optional[bool] = None
    annual_leave_days: Optional[float] = None  # send -1.0 to set unlimited (NULL)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_phone(value)


class EmployeeResponse(BaseModel):
    id: int
    code: str
    full_name: str
    phone: Optional[str] = None
    user_id: Optional[int] = None
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    active: bool = True
    annual_leave_days: Optional[float] = None
    resigned_at: Optional[datetime] = None
    joined_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ─── Shift override (Phase 3B) ────────────────────────────────────────────────


class EmployeeShiftOverrideUpsertRequest(BaseModel):
    shift_id: int = Field(gt=0)
    effective_date: date
    end_date: Optional[date] = None

    @field_validator("end_date")
    @classmethod
    def validate_end_after_effective(cls, value: Optional[date], info) -> Optional[date]:
        if value is None:
            return value
        effective = info.data.get("effective_date")
        if effective is not None and value < effective:
            raise ValueError("end_date phải >= effective_date")
        return value


class EmployeeShiftOverrideResponse(BaseModel):
    id: int
    employee_id: int
    shift_id: int
    shift_name: str
    shift_start_time: time
    shift_end_time: time
    effective_date: date
    end_date: Optional[date] = None

    @field_serializer("shift_start_time")
    def _ser_start(self, value: time) -> str:
        return value.strftime("%H:%M")

    @field_serializer("shift_end_time")
    def _ser_end(self, value: time) -> str:
        return value.strftime("%H:%M")

    class Config:
        from_attributes = True
