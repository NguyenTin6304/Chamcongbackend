from datetime import datetime
import re
from typing import Optional

from pydantic import BaseModel, field_validator


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
    resigned_at: Optional[datetime] = None
    joined_at: Optional[datetime] = None

    class Config:
        from_attributes = True
