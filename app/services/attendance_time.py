from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

PunctualityStatus = Literal["EARLY", "ON_TIME", "LATE"]

VN_TZ = timezone(timedelta(hours=7))
DEFAULT_CROSS_DAY_CUTOFF_MINUTES = 4 * 60


def to_vn_time(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(VN_TZ)


def normalize_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_work_date(dt: datetime, cutoff_minutes: int = DEFAULT_CROSS_DAY_CUTOFF_MINUTES) -> date:
    vn_time = to_vn_time(dt)
    adjusted = vn_time - timedelta(minutes=max(cutoff_minutes, 0))
    return adjusted.date()


def work_date_cutoff_utc(work_date: date, cutoff_minutes: int = DEFAULT_CROSS_DAY_CUTOFF_MINUTES) -> datetime:
    safe_cutoff = max(0, min(cutoff_minutes, 23 * 60 + 59))
    cutoff_hour = safe_cutoff // 60
    cutoff_minute = safe_cutoff % 60
    cutoff_vn = datetime.combine(
        work_date + timedelta(days=1),
        time(hour=cutoff_hour, minute=cutoff_minute),
        tzinfo=VN_TZ,
    )
    return cutoff_vn.astimezone(timezone.utc)


def shift_window_utc(work_date: date, start_time: time, end_time: time) -> tuple[datetime, datetime]:
    start_vn = datetime.combine(work_date, start_time, tzinfo=VN_TZ)
    end_vn = datetime.combine(work_date, end_time, tzinfo=VN_TZ)
    if end_vn <= start_vn:
        end_vn += timedelta(days=1)
    return start_vn.astimezone(timezone.utc), end_vn.astimezone(timezone.utc)


def _overlap_minutes(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> int:
    start = max(start_a, start_b)
    end = min(end_a, end_b)
    if end <= start:
        return 0
    return int((end - start).total_seconds() // 60)


def split_regular_overtime_minutes(
    work_date: date,
    checkin_time: datetime | None,
    checkout_time: datetime | None,
    shift_start_time: time,
    shift_end_time: time,
) -> tuple[int, int, bool]:
    if checkin_time is None or checkout_time is None:
        return 0, 0, False

    checkin_utc = normalize_utc(checkin_time)
    checkout_utc = normalize_utc(checkout_time)
    if checkout_utc <= checkin_utc:
        return 0, 0, False

    total_minutes = int((checkout_utc - checkin_utc).total_seconds() // 60)
    shift_start_utc, shift_end_utc = shift_window_utc(work_date, shift_start_time, shift_end_time)
    regular_minutes = _overlap_minutes(checkin_utc, checkout_utc, shift_start_utc, shift_end_utc)
    overtime_minutes = max(0, total_minutes - regular_minutes)
    overtime_cross_day = to_vn_time(checkout_utc).date() > work_date
    return regular_minutes, overtime_minutes, overtime_cross_day


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
