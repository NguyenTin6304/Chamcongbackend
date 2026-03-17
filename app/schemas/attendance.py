from datetime import date, datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field

PunctualityStatus = Literal["EARLY", "ON_TIME", "LATE"]
GeofenceSource = Literal["GROUP", "SYSTEM_FALLBACK"]
TimeRuleSource = Literal["GROUP", "SYSTEM_FALLBACK"]
AttendanceExceptionType = Literal["MISSED_CHECKOUT", "AUTO_CLOSED"]
AttendanceExceptionStatus = Literal["OPEN", "RESOLVED"]


class LocationRequest(BaseModel):
    # Accept both {lat,lng} and {latitude,longitude}
    lat: float = Field(validation_alias=AliasChoices("lat", "latitude"), ge=-90, le=90)
    lng: float = Field(validation_alias=AliasChoices("lng", "longitude"), ge=-180, le=180)


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
    checkout_status: PunctualityStatus | None = None


class CheckActionResponse(BaseModel):
    log: AttendanceLogResponse
    message: str
    geofence_source: GeofenceSource
    fallback_reason: str | None = None


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
    punctuality_status: PunctualityStatus | None = None
    checkout_status: PunctualityStatus | None = None
    out_of_range: bool
    avg_distance_m: float | None = None
    max_distance_m: float | None = None
    regular_minutes: int | None = None
    overtime_minutes: int | None = None
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
    source_checkin_log_id: int
    source_checkin_time: datetime | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None
