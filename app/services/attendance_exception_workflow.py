from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import AttendanceException, ExceptionPolicy


AttendanceExceptionWorkflowStatus = str

PENDING_EMPLOYEE = "PENDING_EMPLOYEE"
PENDING_ADMIN = "PENDING_ADMIN"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
EXPIRED = "EXPIRED"

LEGACY_STATUS_MAP: dict[str, AttendanceExceptionWorkflowStatus] = {
    "OPEN": PENDING_EMPLOYEE,
    "RESOLVED": APPROVED,
}

WORKFLOW_STATUSES: tuple[AttendanceExceptionWorkflowStatus, ...] = (
    PENDING_EMPLOYEE,
    PENDING_ADMIN,
    APPROVED,
    REJECTED,
    EXPIRED,
)

TERMINAL_STATUSES = frozenset({APPROVED, REJECTED, EXPIRED})
PENDING_STATUSES = frozenset({PENDING_EMPLOYEE, PENDING_ADMIN})

ALLOWED_CREATE_STATUSES = frozenset({PENDING_EMPLOYEE, PENDING_ADMIN})
ALLOWED_TRANSITIONS: dict[AttendanceExceptionWorkflowStatus, frozenset[AttendanceExceptionWorkflowStatus]] = {
    PENDING_EMPLOYEE: frozenset({PENDING_ADMIN, EXPIRED}),
    PENDING_ADMIN: frozenset({APPROVED, REJECTED}),
    APPROVED: frozenset(),
    REJECTED: frozenset(),
    EXPIRED: frozenset(),
}

_PENDING_TIMESHEET_TYPES = frozenset({"AUTO_CLOSED", "MISSED_CHECKOUT"})
_DIRECT_ADMIN_TYPES = frozenset({"LARGE_TIME_DEVIATION"})


def normalize_exception_status(status: str | None) -> AttendanceExceptionWorkflowStatus | None:
    if status is None:
        return None
    normalized = status.strip().upper()
    normalized = LEGACY_STATUS_MAP.get(normalized, normalized)
    if normalized in WORKFLOW_STATUSES:
        return normalized
    raise ValueError(f"Unsupported attendance exception status: {status}")


def ensure_known_exception_status(status: str) -> AttendanceExceptionWorkflowStatus:
    normalized = normalize_exception_status(status)
    if normalized is None:
        raise ValueError("Attendance exception status is required")
    return normalized


def ensure_allowed_exception_transition(
    previous_status: str | None,
    next_status: str,
    *,
    is_create: bool = False,
) -> AttendanceExceptionWorkflowStatus:
    normalized_next = ensure_known_exception_status(next_status)
    normalized_previous = normalize_exception_status(previous_status)

    if is_create:
        if normalized_next not in ALLOWED_CREATE_STATUSES:
            raise ValueError(f"Invalid create transition to {normalized_next}")
        return normalized_next

    if normalized_previous is None:
        raise ValueError("Current attendance exception status is required")
    if normalized_previous == normalized_next:
        return normalized_next

    allowed_next = ALLOWED_TRANSITIONS.get(normalized_previous, frozenset())
    if normalized_next not in allowed_next:
        raise ValueError(f"Invalid transition {normalized_previous} -> {normalized_next}")
    return normalized_next


def can_transition_exception_status(
    previous_status: str | None,
    next_status: str,
    *,
    is_create: bool = False,
) -> bool:
    try:
        ensure_allowed_exception_transition(previous_status, next_status, is_create=is_create)
    except ValueError:
        return False
    return True


def is_terminal_exception_status(status: str | None) -> bool:
    normalized = normalize_exception_status(status)
    return normalized in TERMINAL_STATUSES if normalized is not None else False


def is_pending_exception_status(status: str | None) -> bool:
    normalized = normalize_exception_status(status)
    return normalized in PENDING_STATUSES if normalized is not None else False


def is_pending_timesheet_exception(status: str | None, exception_type: str | None) -> bool:
    return is_pending_exception_status(status) and exception_type in _PENDING_TIMESHEET_TYPES


def default_exception_status_for_type(exception_type: str) -> AttendanceExceptionWorkflowStatus:
    normalized_type = exception_type.strip().upper()
    if normalized_type in _DIRECT_ADMIN_TYPES:
        return ensure_allowed_exception_transition(None, PENDING_ADMIN, is_create=True)
    return ensure_allowed_exception_transition(None, PENDING_EMPLOYEE, is_create=True)


def build_exception_status_filter_values(statuses: Iterable[str]) -> list[str]:
    normalized_values: list[str] = []
    for status in statuses:
        normalized = ensure_known_exception_status(status)
        if normalized not in normalized_values:
            normalized_values.append(normalized)
    return normalized_values


# ---------------------------------------------------------------------------
# Deadline helpers
# ---------------------------------------------------------------------------

def get_deadline_hours(policy: "ExceptionPolicy", exception_type: str) -> int:
    """Return the configured deadline in hours for the given exception type.

    Falls back to policy.default_deadline_hours when no per-type override is set.
    """
    normalized = exception_type.strip().upper()
    per_type: int | None = None
    if normalized == "AUTO_CLOSED":
        per_type = policy.auto_closed_deadline_hours
    elif normalized == "MISSED_CHECKOUT":
        per_type = policy.missed_checkout_deadline_hours
    elif normalized == "LOCATION_RISK":
        per_type = policy.location_risk_deadline_hours
    elif normalized == "LARGE_TIME_DEVIATION":
        per_type = policy.large_time_deviation_deadline_hours
    return per_type if per_type is not None else policy.default_deadline_hours


def get_effective_deadline(exception: "AttendanceException") -> datetime | None:
    """Return the effective deadline for an exception.

    Uses extended_deadline_at if set (admin override), otherwise expires_at.
    """
    return exception.extended_deadline_at or exception.expires_at


def auto_expire_overdue(db: "Session", exceptions: list["AttendanceException"]) -> list["AttendanceException"]:
    """Lazily expire any PENDING_EMPLOYEE exceptions whose effective deadline has passed.

    Mutates status in DB and returns the same list (with updated statuses).
    """
    now = datetime.now(timezone.utc)
    dirty = False
    for exc in exceptions:
        if exc.status != PENDING_EMPLOYEE:
            continue
        effective = get_effective_deadline(exc)
        if effective is None:
            continue
        # Normalise to UTC-aware for comparison
        if effective.tzinfo is None:
            from datetime import timezone as _tz
            effective = effective.replace(tzinfo=_tz.utc)
        if now > effective:
            exc.status = EXPIRED
            dirty = True
    if dirty:
        db.flush()
    return exceptions
