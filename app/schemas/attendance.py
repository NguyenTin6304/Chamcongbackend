from datetime import date, datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field

PunctualityStatus = Literal["EARLY", "ON_TIME", "LATE"]
GeofenceSource = Literal["GROUP", "SYSTEM_FALLBACK"]


class LocationRequest(BaseModel):
    # Accept both {lat,lng} and {latitude,longitude}
    lat: float = Field(validation_alias=AliasChoices("lat", "latitude"), ge=-90, le=90)
    lng: float = Field(validation_alias=AliasChoices("lng", "longitude"), ge=-180, le=180)


class AttendanceLogResponse(BaseModel):
    id: int
    type: str
    time: datetime
    lat: float
    lng: float
    distance_m: float | None = None
    nearest_distance_m: float | None = None
    matched_geofence: str | None = None
    geofence_source: GeofenceSource | None = None
    fallback_reason: str | None = None
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
