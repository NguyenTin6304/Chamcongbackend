from datetime import time

from pydantic import AliasChoices, BaseModel, Field, field_serializer, field_validator


class RuleResponse(BaseModel):
    latitude: float
    longitude: float
    radius_m: int
    start_time: time
    grace_minutes: int
    end_time: time
    checkout_grace_minutes: int

    @field_serializer("start_time")
    def serialize_start_time(self, value: time) -> str:
        return value.strftime("%H:%M")

    @field_serializer("end_time")
    def serialize_end_time(self, value: time) -> str:
        return value.strftime("%H:%M")


class RuleUpdateRequest(BaseModel):
    # Accept both {latitude, longitude, radius_m} and {lat, lng, radius}
    latitude: float = Field(validation_alias=AliasChoices("latitude", "lat"), ge=-90, le=90)
    longitude: float = Field(validation_alias=AliasChoices("longitude", "lng"), ge=-180, le=180)
    radius_m: int = Field(validation_alias=AliasChoices("radius_m", "radius"), gt=0)

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
