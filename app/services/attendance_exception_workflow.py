from __future__ import annotations

from collections.abc import Iterable


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
