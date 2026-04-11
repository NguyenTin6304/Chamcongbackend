from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class EmployeeCreateRequest(BaseModel):
    code: str
    full_name: str
    user_id: Optional[int] = None
    group_id: Optional[int] = None


class EmployeeAssignUserRequest(BaseModel):
    user_id: Optional[int] = None


class EmployeeAssignGroupRequest(BaseModel):
    group_id: Optional[int] = None


class EmployeeUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    group_id: Optional[int] = None
    user_id: Optional[int] = None


class EmployeeResponse(BaseModel):
    id: int
    code: str
    full_name: str
    user_id: Optional[int] = None
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    joined_at: Optional[datetime] = None

    class Config:
        from_attributes = True
