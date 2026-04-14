from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ExceptionPolicyResponse(BaseModel):
    default_deadline_hours: int
    auto_closed_deadline_hours: Optional[int] = None
    missed_checkout_deadline_hours: Optional[int] = None
    location_risk_deadline_hours: Optional[int] = None
    large_time_deviation_deadline_hours: Optional[int] = None
    grace_period_days: int
    updated_at: Optional[datetime] = None
    updated_by_name: Optional[str] = None

    model_config = {"from_attributes": True}


class ExceptionPolicyPatch(BaseModel):
    default_deadline_hours: Optional[int] = Field(default=None, ge=1, le=8760)  # max 1 year
    auto_closed_deadline_hours: Optional[int] = Field(default=None, ge=1, le=8760)
    missed_checkout_deadline_hours: Optional[int] = Field(default=None, ge=1, le=8760)
    location_risk_deadline_hours: Optional[int] = Field(default=None, ge=1, le=8760)
    large_time_deviation_deadline_hours: Optional[int] = Field(default=None, ge=1, le=8760)
    grace_period_days: Optional[int] = Field(default=None, ge=1, le=3650)  # max 10 years
    # Sentinel: passing null explicitly clears the override for that type
    # Use Field with special sentinel to distinguish "not provided" from "set to null"


__all__ = ["ExceptionPolicyResponse", "ExceptionPolicyPatch"]
