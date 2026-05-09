from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


OvertimeStatus = Literal["PENDING", "APPROVED", "REJECTED"]


class OvertimeListItem(BaseModel):
    id: int
    employee_id: int
    employee_code: str
    employee_name: str
    group_name: str | None = None
    work_date: date

    raw_minutes: int
    approved_minutes: int | None = None
    status: OvertimeStatus
    source: str

    employee_note: str | None = None
    admin_note: str | None = None
    admin_id: int | None = None
    decided_at: datetime | None = None

    shift_start_snapshot: time | None = None
    shift_end_snapshot: time | None = None
    is_weekend: bool = False
    is_holiday: bool = False

    created_at: datetime


class OvertimeAuditItem(BaseModel):
    id: int
    action: str
    actor_id: int | None = None
    from_status: str | None = None
    to_status: str | None = None
    from_minutes: int | None = None
    to_minutes: int | None = None
    note: str | None = None
    created_at: datetime


class OvertimeDetailResponse(OvertimeListItem):
    audits: list[OvertimeAuditItem] = []


class OvertimeApproveRequest(BaseModel):
    """Approve a PENDING overtime record.

    If `|approved_minutes - raw_minutes| > 30` → admin_note is required.
    """
    approved_minutes: int = Field(ge=0, le=24 * 60)
    admin_note: str | None = Field(default=None, max_length=500)


class OvertimeRejectRequest(BaseModel):
    admin_note: str = Field(min_length=1, max_length=500)

    @field_validator("admin_note")
    @classmethod
    def not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("admin_note must not be blank")
        return stripped


class OvertimeEditRequest(BaseModel):
    """Edit an already-APPROVED overtime record (admin correction)."""
    approved_minutes: int = Field(ge=0, le=24 * 60)
    admin_note: str = Field(min_length=1, max_length=500)

    @field_validator("admin_note")
    @classmethod
    def not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("admin_note must not be blank")
        return stripped


BulkStrategy = Literal["as_is", "round_up_30"]


class OvertimeBulkApproveRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    strategy: BulkStrategy = "as_is"
    admin_note: str | None = Field(default=None, max_length=500)


class OvertimeBulkApproveResponse(BaseModel):
    approved_count: int
    skipped_ids: list[int] = []  # already non-PENDING


class MyOvertimeListItem(BaseModel):
    """Lightweight item for the employee-facing endpoint."""
    id: int
    work_date: date
    raw_minutes: int
    approved_minutes: int | None = None
    status: OvertimeStatus
    admin_note: str | None = None
    decided_at: datetime | None = None
    is_weekend: bool = False
    is_holiday: bool = False


class MyOvertimeMonthSummary(BaseModel):
    month: str  # "YYYY-MM"
    items: list[MyOvertimeListItem]
    total_pending_minutes: int
    total_approved_minutes: int
    total_rejected_minutes: int


class OvertimeFromExceptionRequest(BaseModel):
    """Used by exception decide endpoint to combine OT decision with MISSED_CHECKOUT approval."""
    approved_minutes: int = Field(ge=0, le=24 * 60)
    admin_note: str | None = Field(default=None, max_length=500)
