from datetime import time

from pydantic import BaseModel, Field, field_serializer, field_validator


class GroupCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    active: bool = True
    start_time: time | None = None
    grace_minutes: int | None = Field(default=None, ge=0, le=240)
    end_time: time | None = None
    checkout_grace_minutes: int | None = Field(default=None, ge=0, le=240)
    cross_day_cutoff_minutes: int | None = Field(default=None, ge=0, le=720)

    @field_validator("start_time", mode="before")
    @classmethod
    def normalize_start_time(cls, value):
        if value is None or isinstance(value, time):
            return value
        if isinstance(value, str):
            raw = value.strip()
            try:
                return time.fromisoformat(raw)
            except ValueError:
                if len(raw) == 5:
                    return time.fromisoformat(f"{raw}:00")
        return value

    @field_validator("end_time", mode="before")
    @classmethod
    def normalize_end_time(cls, value):
        if value is None or isinstance(value, time):
            return value
        if isinstance(value, str):
            raw = value.strip()
            try:
                return time.fromisoformat(raw)
            except ValueError:
                if len(raw) == 5:
                    return time.fromisoformat(f"{raw}:00")
        return value


class GroupUpdateRequest(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    active: bool | None = None
    start_time: time | None = None
    grace_minutes: int | None = Field(default=None, ge=0, le=240)
    end_time: time | None = None
    checkout_grace_minutes: int | None = Field(default=None, ge=0, le=240)
    cross_day_cutoff_minutes: int | None = Field(default=None, ge=0, le=720)

    @field_validator("start_time", mode="before")
    @classmethod
    def normalize_start_time(cls, value):
        if value is None or isinstance(value, time):
            return value
        if isinstance(value, str):
            raw = value.strip()
            try:
                return time.fromisoformat(raw)
            except ValueError:
                if len(raw) == 5:
                    return time.fromisoformat(f"{raw}:00")
        return value

    @field_validator("end_time", mode="before")
    @classmethod
    def normalize_end_time(cls, value):
        if value is None or isinstance(value, time):
            return value
        if isinstance(value, str):
            raw = value.strip()
            try:
                return time.fromisoformat(raw)
            except ValueError:
                if len(raw) == 5:
                    return time.fromisoformat(f"{raw}:00")
        return value


class GroupResponse(BaseModel):
    id: int
    code: str
    name: str
    active: bool
    start_time: time | None = None
    grace_minutes: int | None = None
    end_time: time | None = None
    checkout_grace_minutes: int | None = None
    cross_day_cutoff_minutes: int | None = None

    @field_serializer("start_time")
    def serialize_start_time(self, value: time | None) -> str | None:
        if value is None:
            return None
        return value.strftime("%H:%M")

    @field_serializer("end_time")
    def serialize_end_time(self, value: time | None) -> str | None:
        if value is None:
            return None
        return value.strftime("%H:%M")

    class Config:
        from_attributes = True


class GroupGeofenceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: int = Field(gt=0)
    active: bool = True


class GroupGeofenceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_m: int | None = Field(default=None, gt=0)
    active: bool | None = None


class GroupGeofenceResponse(BaseModel):
    id: int
    group_id: int
    name: str
    latitude: float
    longitude: float
    radius_m: int
    active: bool

    class Config:
        from_attributes = True
