from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator


class LeaveRequestCreate(BaseModel):
    leave_type: str = Field(pattern="^(PAID|UNPAID)$")
    start_date: date
    end_date: date
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def check_date_order(self) -> "LeaveRequestCreate":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be >= start_date")
        return self


class LeaveRequestApproveRequest(BaseModel):
    admin_note: str | None = Field(default=None, max_length=255)


class LeaveRequestRejectRequest(BaseModel):
    admin_note: str = Field(min_length=1, max_length=255)

    @field_validator("admin_note")
    @classmethod
    def admin_note_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("admin_note must not be blank or whitespace-only")
        return stripped


class LeaveRequestResponse(BaseModel):
    id: int
    employee_id: int
    employee_name: str
    employee_code: str
    leave_type: str
    start_date: date
    end_date: date
    reason: str | None
    status: str
    admin_note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
