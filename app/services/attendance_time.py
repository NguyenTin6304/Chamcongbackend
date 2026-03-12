from datetime import datetime, time, timedelta, timezone
from typing import Literal

PunctualityStatus = Literal["EARLY", "ON_TIME", "LATE"]

VN_TZ = timezone(timedelta(hours=7))


def to_vn_time(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(VN_TZ)


def classify_checkin_status(
    checkin_at: datetime,
    shift_start_time: time,
    grace_minutes: int,
) -> PunctualityStatus:
    vn_now = to_vn_time(checkin_at)
    start_at = datetime.combine(vn_now.date(), shift_start_time, tzinfo=VN_TZ)
    on_time_until = start_at + timedelta(minutes=max(grace_minutes, 0))

    if vn_now < start_at:
        return "EARLY"
    if vn_now <= on_time_until:
        return "ON_TIME"
    return "LATE"


def classify_checkout_status(
    checkout_at: datetime,
    shift_end_time: time,
    grace_minutes: int,
) -> PunctualityStatus:
    vn_now = to_vn_time(checkout_at)
    end_at = datetime.combine(vn_now.date(), shift_end_time, tzinfo=VN_TZ)
    on_time_until = end_at + timedelta(minutes=max(grace_minutes, 0))

    if vn_now < end_at:
        return "EARLY"
    if vn_now <= on_time_until:
        return "ON_TIME"
    return "LATE"
