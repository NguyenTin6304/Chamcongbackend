from datetime import date, datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field

PunctualityStatus = Literal["EARLY", "ON_TIME", "LATE"]
CheckinStatus = Literal["EARLY", "ON_TIME", "LATE", "NO_CHECKIN"]
CheckoutStatus = Literal["EARLY", "ON_TIME", "LATE", "NO_CHECKOUT", "SYSTEM_AUTO", "MISSING_PUNCH"]
AttendanceState = Literal["COMPLETE", "MISSED_CHECKOUT", "MISSING_CHECKIN_ANOMALY", "ABSENT", "PENDING_TIMESHEET"]
GeofenceSource = Literal["GROUP", "SYSTEM_FALLBACK"]
TimeRuleSource = Literal["GROUP", "SYSTEM_FALLBACK"]
AttendanceExceptionType = Literal["MISSED_CHECKOUT", "AUTO_CLOSED", "SUSPECTED_LOCATION_SPOOF", "LARGE_TIME_DEVIATION"]
AttendanceExceptionStatus = Literal[
    "PENDING_EMPLOYEE",
    "PENDING_ADMIN",
    "APPROVED",
    "REJECTED",
    "EXPIRED",
]
LocationRiskDecision = Literal["ALLOW", "ALLOW_WITH_EXCEPTION", "BLOCK"]
LocationRiskLevel = Literal["LOW", "MEDIUM", "HIGH"]


class LocationRequest(BaseModel):
    # Accept both {lat,lng} and {latitude,longitude}
    lat: float = Field(validation_alias=AliasChoices("lat", "latitude"), ge=-90, le=90)
    lng: float = Field(validation_alias=AliasChoices("lng", "longitude"), ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, gt=0, le=5000)
    timestamp_client: datetime | None = None


class AttendanceLogResponse(BaseModel):
    id: int
    type: str
    time: datetime
    work_date: date | None = None
    lat: float
    lng: float
    distance_m: float | None = None
    nearest_distance_m: float | None = None
    matched_geofence: str | None = None
    geofence_source: GeofenceSource | None = None
    fallback_reason: str | None = None
    time_rule_source: TimeRuleSource | None = None
    time_rule_fallback_reason: str | None = None
    is_out_of_range: bool
    punctuality_status: PunctualityStatus | None = None
    checkout_status: CheckoutStatus | None = None
    risk_score: int | None = None
    risk_level: LocationRiskLevel | None = None
    risk_flags: list[str] = Field(default_factory=list)
    risk_policy_version: str | None = None
    ip: str | None = None
    ua_hash: str | None = None
    accuracy_m: float | None = None


class CheckActionResponse(BaseModel):
    log: AttendanceLogResponse
    message: str
    geofence_source: GeofenceSource
    fallback_reason: str | None = None
    risk_score: int
    risk_level: LocationRiskLevel
    risk_flags: list[str] = Field(default_factory=list)
    decision: LocationRiskDecision


class AttendanceStatusResponse(BaseModel):
    employee_assigned: bool
    employee_id: int | None = None
    current_state: Literal["IN", "OUT", "UNASSIGNED"]
    last_action: Literal["IN", "OUT"] | None = None
    last_action_time: datetime | None = None
    can_checkin: bool
    can_checkout: bool
    message: str
    warning_code: Literal["MISSED_CHECKOUT", "AUTO_CLOSED"] | None = None
    warning_date: date | None = None


class AttendanceDailyReportResponse(BaseModel):
    date: date
    employee_code: str
    full_name: str
    group_code: str | None = None
    group_name: str | None = None
    matched_geofence: str | None = None
    geofence_source: GeofenceSource | None = None
    fallback_reason: str | None = None
    checkin_time: datetime | None = None
    checkout_time: datetime | None = None
    # Kept for backward compatibility with old clients.
    punctuality_status: PunctualityStatus | None = None
    checkin_status: CheckinStatus | None = None
    checkout_status: CheckoutStatus | None = None
    attendance_state: AttendanceState
    out_of_range: bool
    avg_distance_m: float | None = None
    max_distance_m: float | None = None
    radius_m: int | None = None
    distance_consistency_warning: str | None = None
    regular_minutes: int | None = None
    overtime_minutes: int | None = None
    payable_overtime_minutes: int | None = None
    overtime_cross_day: bool | None = None
    exception_status: AttendanceExceptionStatus | None = None


class AttendanceExceptionReportResponse(BaseModel):
    id: int
    employee_id: int
    employee_code: str
    full_name: str
    group_code: str | None = None
    group_name: str | None = None
    work_date: date
    exception_type: AttendanceExceptionType
    status: AttendanceExceptionStatus
    note: str | None = None
    resolved_note: str | None = None
    risk_score: int | None = None
    risk_level: LocationRiskLevel | None = None
    risk_flags: list[str] = Field(default_factory=list)
    risk_policy_version: str | None = None
    source_checkin_log_id: int
    source_checkin_time: datetime | None = None
    detected_at: datetime | None = None
    expires_at: datetime | None = None
    employee_explanation: str | None = None
    employee_submitted_at: datetime | None = None
    admin_note: str | None = None
    admin_decided_at: datetime | None = None
    decided_by: int | None = None
    decided_by_email: str | None = None
    actual_checkout_time: datetime | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: int | None = None
    resolved_by_email: str | None = None
    can_submit_explanation: bool = False
    can_admin_decide: bool = False
    can_expire: bool = False


class AttendanceExceptionAuditResponse(BaseModel):
    id: int
    event_type: str
    previous_status: AttendanceExceptionStatus | None = None
    next_status: AttendanceExceptionStatus
    actor_type: str
    actor_id: int | None = None
    actor_email: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class AttendanceExceptionDetailResponse(AttendanceExceptionReportResponse):
    timeline: list[AttendanceExceptionAuditResponse] = Field(default_factory=list)


class AttendanceExceptionResolveRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
    actual_checkout_time: datetime | None = None


class AttendanceExceptionReopenRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class AttendanceExceptionCreateRequest(BaseModel):
    employee_id: int
    source_checkin_log_id: int
    exception_type: AttendanceExceptionType
    work_date: date | None = None
    note: str | None = Field(default=None, max_length=2000)
    detected_at: datetime | None = None
    expires_at: datetime | None = None


class AttendanceExceptionSubmitExplanationRequest(BaseModel):
    explanation: str = Field(min_length=1, max_length=2000)


class AttendanceExceptionApproveRequest(BaseModel):
    admin_note: str | None = Field(default=None, max_length=2000)
    actual_checkout_time: datetime | None = None


class AttendanceExceptionRejectRequest(BaseModel):
    admin_note: str = Field(min_length=1, max_length=2000)
