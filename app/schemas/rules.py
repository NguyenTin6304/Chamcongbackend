from datetime import datetime, time

from pydantic import AliasChoices, BaseModel, Field, field_serializer, field_validator

from app.core.policy import MAX_GEOFENCE_RADIUS_M, MIN_GEOFENCE_RADIUS_M, WARN_GEOFENCE_RADIUS_M


class RuleResponse(BaseModel):
    latitude: float
    longitude: float
    radius_m: int
    start_time: time
    grace_minutes: int
    end_time: time
    checkout_grace_minutes: int
    cross_day_cutoff_minutes: int
    radius_policy_warning: str | None = None

    @field_serializer("start_time")
    def serialize_start_time(self, value: time) -> str:
        return value.strftime("%H:%M")

    @field_serializer("end_time")
    def serialize_end_time(self, value: time) -> str:
        return value.strftime("%H:%M")


def _normalize_time_value(value):
    if value is None:
        return None

    if isinstance(value, time):
        # DB Time columns are timezone-naive. Drop tz if provided.
        return value.replace(tzinfo=None)

    if isinstance(value, datetime):
        return value.timetz().replace(tzinfo=None)

    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"

        # Accept HH:MM
        if len(raw) == 5 and raw[2] == ":":
            raw = f"{raw}:00"

        try:
            parsed = time.fromisoformat(raw)
            return parsed.replace(tzinfo=None)
        except ValueError:
            pass

        # Accept full datetime string and extract time part.
        try:
            parsed_dt = datetime.fromisoformat(raw)
            return parsed_dt.timetz().replace(tzinfo=None)
        except ValueError:
            return value

    return value


class RuleUpdateRequest(BaseModel):
    # Accept both {latitude, longitude, radius_m} and {lat, lng, radius}
    latitude: float = Field(validation_alias=AliasChoices("latitude", "lat"), ge=-90, le=90)
    longitude: float = Field(validation_alias=AliasChoices("longitude", "lng"), ge=-180, le=180)
    radius_m: int = Field(
        validation_alias=AliasChoices("radius_m", "radius"),
        ge=MIN_GEOFENCE_RADIUS_M,
        le=MAX_GEOFENCE_RADIUS_M,
    )

    # Optional in update to keep backward compatibility for existing clients.
    start_time: time | None = Field(
        default=None,
        validation_alias=AliasChoices("start_time", "start", "shift_start"),
    )
    grace_minutes: int | None = Field(
        default=None,
        validation_alias=AliasChoices("grace_minutes", "grace", "grace_period"),
        ge=0,
        le=240,
    )
    end_time: time | None = Field(
        default=None,
        validation_alias=AliasChoices("end_time", "end", "shift_end"),
    )
    checkout_grace_minutes: int | None = Field(
        default=None,
        validation_alias=AliasChoices("checkout_grace_minutes", "checkout_grace", "checkout_grace_period"),
        ge=0,
        le=240,
    )
    cross_day_cutoff_minutes: int | None = Field(
        default=None,
        validation_alias=AliasChoices("cross_day_cutoff_minutes", "cutoff_minutes", "cross_day_cutoff"),
        ge=0,
        le=720,
    )

    @field_validator("start_time", mode="before")
    @classmethod
    def normalize_start_time(cls, value):
        return _normalize_time_value(value)

    @field_validator("end_time", mode="before")
    @classmethod
    def normalize_end_time(cls, value):
        return _normalize_time_value(value)


__all__ = [
    "RuleResponse",
    "RuleUpdateRequest",
    "WARN_GEOFENCE_RADIUS_M",
]
