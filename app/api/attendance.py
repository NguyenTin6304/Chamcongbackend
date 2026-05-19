from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from typing import NamedTuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import and_, case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import AttendanceException, AttendanceLog, CheckinRule, Employee, EmployeeShiftOverride, ExceptionPolicy, Group, GroupGeofence, LeaveRequest, OvertimeRecord, PublicHoliday, Shift, User
from app.schemas.attendance import AttendanceDailyReportResponse, AttendanceLogResponse, AttendanceStatusResponse, CheckActionResponse, LocationRequest, MyMonthlyStatsResponse, MyShiftResponse
from app.services.attendance_time import (
    DEFAULT_CROSS_DAY_CUTOFF_MINUTES,
    VN_TZ,
    classify_checkin_status,
    classify_checkout_status,
    compute_work_date,
    split_regular_overtime_minutes,
    work_date_cutoff_utc,
)
from app.services.geo import haversine_m
from app.services.attendance_exception_workflow import (
    PENDING_ADMIN,
    PENDING_EMPLOYEE,
    can_transition_exception_status,
    default_exception_status_for_type,
    ensure_allowed_exception_transition,
    get_deadline_hours,
    is_pending_exception_status,
    is_pending_timesheet_exception,
    is_terminal_exception_status,
    normalize_exception_status,
)
from app.services.attendance_exception_notifications import (
    build_exception_notification_mail,
    create_exception_notification_record,
    send_exception_notification_background,
)
from app.services.location_risk import LocationRiskInput, assess_location_risk
from app.services.overtime_service import auto_create_pending_ot, fetch_payable_minutes_map
from app.services.report_consistency import (
    compute_distance_consistency_warning,
    load_group_geofence_radius_maps,
    resolve_reference_radius_m,
)

router = APIRouter(prefix="/attendance", tags=["attendance"])


class _GeoPoint(NamedTuple):
    name: str
    latitude: float
    longitude: float
    radius_m: int


class _EffectiveTimeRule(NamedTuple):
    start_time: time
    grace_minutes: int
    end_time: time
    checkout_grace_minutes: int
    cutoff_minutes: int
    source: str
    fallback_reason: str | None
    # Phase 3C: name of the resolved Shift if source is GROUP_SHIFT /
    # EMPLOYEE_SHIFT_OVERRIDE; None for legacy GROUP / SYSTEM_FALLBACK sources
    # that don't have a named shift attached.
    shift_name: str | None = None


def _find_employee_for_user(db: Session, user: User) -> Employee | None:
    return db.query(Employee).filter(
        Employee.user_id == user.id,
        Employee.deleted_at.is_(None),
    ).first()


def _get_employee_for_user(db: Session, user: User) -> Employee:
    emp = _find_employee_for_user(db, user)
    if not emp:
        raise HTTPException(
            status_code=400,
            detail="User chưa được gán Employee. Hãy tạo employee và set employees.user_id = user.id",
        )
    if emp.deleted_at is not None:
        raise HTTPException(
            status_code=403,
            detail="Nhân viên đã nghỉ việc, không thể thực hiện chấm công",
        )
    if not emp.active:
        raise HTTPException(
            status_code=403,
            detail="Tài khoản nhân viên đang bị vô hiệu hoá, vui lòng liên hệ quản trị viên",
        )
    return emp


def _get_active_rule(db: Session) -> CheckinRule:
    rule = db.query(CheckinRule).filter(CheckinRule.active.is_(True)).first()
    if not rule:
        raise HTTPException(status_code=400, detail="Chưa có rule active. Admin hãy cấu hình /rules/active")
    return rule


def _get_effective_geofences(
    db: Session,
    emp: Employee,
    fallback_rule: CheckinRule,
) -> tuple[list[_GeoPoint], str, str | None]:
    if emp.group_id is not None:
        group = db.query(Group).filter(Group.id == emp.group_id).first()
        if group and group.active:
            geofences = (
                db.query(GroupGeofence)
                .filter(
                    GroupGeofence.group_id == emp.group_id,
                    GroupGeofence.active.is_(True),
                )
                .all()
            )
            if geofences:
                return [
                    _GeoPoint(name=g.name, latitude=g.latitude, longitude=g.longitude, radius_m=g.radius_m)
                    for g in geofences
                ], "GROUP", None

            return [
                _GeoPoint(
                    name="SYSTEM_RULE",
                    latitude=fallback_rule.latitude,
                    longitude=fallback_rule.longitude,
                    radius_m=fallback_rule.radius_m,
                ),
            ], "SYSTEM_FALLBACK", "NO_ACTIVE_GEOFENCE_IN_GROUP"

        return [
            _GeoPoint(
                name="SYSTEM_RULE",
                latitude=fallback_rule.latitude,
                longitude=fallback_rule.longitude,
                radius_m=fallback_rule.radius_m,
            ),
        ], "SYSTEM_FALLBACK", "GROUP_INACTIVE_OR_NOT_FOUND"

    return [
        _GeoPoint(
            name="SYSTEM_RULE",
            latitude=fallback_rule.latitude,
            longitude=fallback_rule.longitude,
            radius_m=fallback_rule.radius_m,
        ),
    ], "SYSTEM_FALLBACK", "EMPLOYEE_NOT_ASSIGNED_GROUP"


def _get_effective_time_rule(db: Session, emp: Employee, fallback_rule: CheckinRule) -> _EffectiveTimeRule:
    system_cutoff_minutes = (fallback_rule.cross_day_cutoff_minutes if fallback_rule.cross_day_cutoff_minutes is not None else DEFAULT_CROSS_DAY_CUTOFF_MINUTES)

    if emp.group_id is not None:
        group = db.query(Group).filter(Group.id == emp.group_id).first()
        if group and group.active:
            # Phase 3B: per-employee override has highest priority. Active when
            # effective_date <= today_vn AND (end_date IS NULL OR end_date >= today_vn).
            # Outside that window the override row exists but is ignored — we
            # fall through to the group default Shift / Group.end_time chain.
            today_vn = datetime.now(VN_TZ).date()
            override_shift = (
                db.query(Shift)
                .join(
                    EmployeeShiftOverride,
                    EmployeeShiftOverride.shift_id == Shift.id,
                )
                .filter(
                    EmployeeShiftOverride.employee_id == emp.id,
                    EmployeeShiftOverride.effective_date <= today_vn,
                    (EmployeeShiftOverride.end_date.is_(None))
                    | (EmployeeShiftOverride.end_date >= today_vn),
                    Shift.active.is_(True),
                    # The override must point to a shift in the employee's
                    # group — defensive check in case group changed after
                    # override was set.
                    Shift.group_id == group.id,
                )
                .first()
            )
            if override_shift:
                return _EffectiveTimeRule(
                    start_time=override_shift.start_time,
                    grace_minutes=group.grace_minutes if group.grace_minutes is not None else fallback_rule.grace_minutes,
                    end_time=override_shift.end_time,
                    checkout_grace_minutes=(
                        group.checkout_grace_minutes
                        if group.checkout_grace_minutes is not None
                        else fallback_rule.checkout_grace_minutes
                    ),
                    cutoff_minutes=(
                        group.cross_day_cutoff_minutes
                        if group.cross_day_cutoff_minutes is not None
                        else system_cutoff_minutes
                    ),
                    source='EMPLOYEE_SHIFT_OVERRIDE',
                    fallback_reason=None,
                    shift_name=override_shift.name,
                )

            # Phase 3A: if group has a default Shift, use its start/end times.
            # Grace/cutoff settings still come from group (or system fallback) —
            # Shift only owns the shift window.
            default_shift = (
                db.query(Shift)
                .filter(
                    Shift.group_id == group.id,
                    Shift.is_default.is_(True),
                    Shift.active.is_(True),
                )
                .first()
            )
            if default_shift:
                return _EffectiveTimeRule(
                    start_time=default_shift.start_time,
                    grace_minutes=group.grace_minutes if group.grace_minutes is not None else fallback_rule.grace_minutes,
                    end_time=default_shift.end_time,
                    checkout_grace_minutes=(
                        group.checkout_grace_minutes
                        if group.checkout_grace_minutes is not None
                        else fallback_rule.checkout_grace_minutes
                    ),
                    cutoff_minutes=(
                        group.cross_day_cutoff_minutes
                        if group.cross_day_cutoff_minutes is not None
                        else system_cutoff_minutes
                    ),
                    source='GROUP_SHIFT',
                    fallback_reason=None,
                    shift_name=default_shift.name,
                )

            return _EffectiveTimeRule(
                start_time=group.start_time or fallback_rule.start_time,
                grace_minutes=group.grace_minutes if group.grace_minutes is not None else fallback_rule.grace_minutes,
                end_time=group.end_time or fallback_rule.end_time,
                checkout_grace_minutes=(
                    group.checkout_grace_minutes
                    if group.checkout_grace_minutes is not None
                    else fallback_rule.checkout_grace_minutes
                ),
                cutoff_minutes=(
                    group.cross_day_cutoff_minutes
                    if group.cross_day_cutoff_minutes is not None
                    else system_cutoff_minutes
                ),
                source='GROUP',
                fallback_reason=None,
            )
        return _EffectiveTimeRule(
            start_time=fallback_rule.start_time,
            grace_minutes=fallback_rule.grace_minutes,
            end_time=fallback_rule.end_time,
            checkout_grace_minutes=fallback_rule.checkout_grace_minutes,
            cutoff_minutes=system_cutoff_minutes,
            source='SYSTEM_FALLBACK',
            fallback_reason='GROUP_INACTIVE_OR_NOT_FOUND',
        )

    return _EffectiveTimeRule(
        start_time=fallback_rule.start_time,
        grace_minutes=fallback_rule.grace_minutes,
        end_time=fallback_rule.end_time,
        checkout_grace_minutes=fallback_rule.checkout_grace_minutes,
        cutoff_minutes=system_cutoff_minutes,
        source='SYSTEM_FALLBACK',
        fallback_reason='EMPLOYEE_NOT_ASSIGNED_GROUP',
    )


def _evaluate_range(lat: float, lng: float, geofences: list[_GeoPoint]) -> tuple[float, bool, int, str | None]:
    nearest_distance: float | None = None
    nearest_radius: int | None = None
    best_in_range_distance: float | None = None
    matched_geofence_name: str | None = None

    for geofence in geofences:
        distance = haversine_m(lat, lng, geofence.latitude, geofence.longitude)

        if nearest_distance is None or distance < nearest_distance:
            nearest_distance = distance
            nearest_radius = geofence.radius_m

        if distance <= geofence.radius_m:
            if best_in_range_distance is None or distance < best_in_range_distance:
                best_in_range_distance = distance
                matched_geofence_name = geofence.name

    if nearest_distance is None or nearest_radius is None:
        return 0.0, True, 0, None

    return nearest_distance, matched_geofence_name is None, nearest_radius, matched_geofence_name


def _last_log(db: Session, employee_id: int) -> AttendanceLog | None:
    return (
        db.query(AttendanceLog)
        .filter(AttendanceLog.employee_id == employee_id)
        .order_by(AttendanceLog.time.desc())
        .first()
    )


def _count_recent_exact_coordinate_reuse(
    db: Session,
    employee_id: int,
    lat: float,
    lng: float,
    *,
    lookback_days: int = 14,
    limit: int = 30,
) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, lookback_days))
    rows = (
        db.query(AttendanceLog.lat, AttendanceLog.lng)
        .filter(
            AttendanceLog.employee_id == employee_id,
            AttendanceLog.time >= since,
        )
        .order_by(AttendanceLog.time.desc())
        .limit(max(1, limit))
        .all()
    )
    epsilon = 1e-6
    return sum(1 for row in rows if abs(float(row.lat) - lat) <= epsilon and abs(float(row.lng) - lng) <= epsilon)


def _extract_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip

    x_real_ip = request.headers.get("x-real-ip", "").strip()
    if x_real_ip:
        return x_real_ip

    if request.client and request.client.host:
        return request.client.host
    return None


def _header_float(request: Request, names: tuple[str, ...]) -> float | None:
    for name in names:
        raw = request.headers.get(name)
        if raw is None:
            continue
        try:
            return float(raw.strip())
        except ValueError:
            continue
    return None


def _header_bool(request: Request, names: tuple[str, ...]) -> bool | None:
    for name in names:
        raw = request.headers.get(name)
        if raw is None:
            continue
        value = raw.strip().lower()
        if value in {"1", "true", "yes", "y", "on"}:
            return True
        if value in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _extract_client_ip_geo_lat(request: Request) -> float | None:
    lat = _header_float(request, ("x-vercel-ip-latitude", "x-ip-latitude", "cf-iplatitude"))
    if lat is None or lat < -90 or lat > 90:
        return None
    return lat


def _extract_client_ip_geo_lng(request: Request) -> float | None:
    lng = _header_float(request, ("x-vercel-ip-longitude", "x-ip-longitude", "cf-iplongitude"))
    if lng is None or lng < -180 or lng > 180:
        return None
    return lng


def _extract_client_asn_hint(request: Request) -> str | None:
    candidates = (
        "x-vercel-ip-asn",
        "x-vercel-ip-as-number",
        "x-vercel-ip-as-organization",
        "x-ip-asn",
        "x-ip-as-org",
    )
    values = [request.headers.get(name, "").strip() for name in candidates]
    merged = " ".join(value for value in values if value)
    return merged or None


def _extract_client_proxy_hint(request: Request) -> bool | None:
    return _header_bool(request, ("x-ip-proxy", "x-ip-vpn", "x-forwarded-proto-vpn"))


def _hash_user_agent(user_agent: str | None) -> str | None:
    value = (user_agent or "").strip()
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _serialize_risk_flags(flags: list[str]) -> str:
    return json.dumps(flags, ensure_ascii=True, separators=(",", ":"))


def _deserialize_risk_flags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if isinstance(parsed, list):
        return [str(x) for x in parsed if str(x).strip()]
    return []


def _get_open_session_checkin(db: Session, employee_id: int) -> AttendanceLog | None:
    checkin_log = aliased(AttendanceLog)
    checkout_log = aliased(AttendanceLog)
    work_date_matches = or_(
        checkout_log.work_date == checkin_log.work_date,
        and_(checkout_log.work_date.is_(None), checkin_log.work_date.is_(None)),
    )
    return (
        db.query(checkin_log)
        .outerjoin(
            checkout_log,
            and_(
                checkout_log.employee_id == checkin_log.employee_id,
                checkout_log.type == "OUT",
                work_date_matches,
            ),
        )
        .filter(
            checkin_log.employee_id == employee_id,
            checkin_log.type == "IN",
            checkout_log.id.is_(None),
        )
        .order_by(checkin_log.time.desc())
        .first()
    )


def _format_vn_date(value: date) -> str:
    return value.strftime("%d/%m")


def _get_log_work_date(log: AttendanceLog, cutoff_minutes: int) -> date:
    if log.work_date is not None:
        return log.work_date
    return compute_work_date(log.time, cutoff_minutes)

def _lock_employee_row(db: Session, employee_id: int) -> None:
    q = db.query(Employee).filter(Employee.id == employee_id)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        q = q.with_for_update()
    q.first()


def _ensure_auto_closed_exception(db: Session, emp: Employee, source_checkin_log: AttendanceLog, work_date: date) -> None:
    existing = (
        db.query(AttendanceException)
        .filter(AttendanceException.source_checkin_log_id == source_checkin_log.id)
        .first()
    )

    if existing is None:
        db.add(
            AttendanceException(
                employee_id=emp.id,
                source_checkin_log_id=source_checkin_log.id,
                exception_type="AUTO_CLOSED",
                work_date=work_date,
                status=default_exception_status_for_type("AUTO_CLOSED"),
                note="System auto closed session at cross-day cutoff",
            )
        )
        return

    if is_terminal_exception_status(existing.status):
        return

    existing.exception_type = "AUTO_CLOSED"
    existing.work_date = work_date
    current_status = normalize_exception_status(existing.status)
    target_status = default_exception_status_for_type("AUTO_CLOSED")
    existing.status = current_status or target_status
    if current_status != target_status and can_transition_exception_status(current_status, target_status):
        existing.status = ensure_allowed_exception_transition(current_status, target_status)
    existing.note = "System auto closed session at cross-day cutoff"
    existing.resolved_note = None


def _ensure_missed_checkout_exception(
    db: Session,
    emp: Employee,
    source_checkin_log: AttendanceLog,
    work_date: date,
) -> bool:
    existing = (
        db.query(AttendanceException)
        .filter(AttendanceException.source_checkin_log_id == source_checkin_log.id)
        .first()
    )
    default_note = "Missing checkout detected: no real OUT log after checkout threshold"

    if existing is None:
        db.add(
            AttendanceException(
                employee_id=emp.id,
                source_checkin_log_id=source_checkin_log.id,
                exception_type="MISSED_CHECKOUT",
                work_date=work_date,
                status=default_exception_status_for_type("MISSED_CHECKOUT"),
                note=default_note,
            )
        )
        return True

    # AUTO_CLOSED has higher precedence once system generated OUT at cutoff.
    if existing.exception_type == "AUTO_CLOSED":
        return False

    if is_terminal_exception_status(existing.status):
        return False

    changed = False
    if existing.exception_type != "MISSED_CHECKOUT":
        existing.exception_type = "MISSED_CHECKOUT"
        changed = True
    if existing.work_date != work_date:
        existing.work_date = work_date
        changed = True
    target_status = default_exception_status_for_type("MISSED_CHECKOUT")
    current_status = normalize_exception_status(existing.status)
    if current_status != target_status and can_transition_exception_status(current_status, target_status):
        existing.status = ensure_allowed_exception_transition(current_status, target_status)
        existing.resolved_by = None
        existing.resolved_at = None
        existing.resolved_note = None
        existing.actual_checkout_time = None
        changed = True
    if changed and existing.note != default_note:
        existing.note = default_note
    return changed


def _default_exception_expires_at(detected_at: datetime, exception_type: str, db: Session) -> datetime | None:
    """Compute deadline for a new PENDING_EMPLOYEE exception using the configured policy."""
    hours = 72  # fallback
    policy = db.query(ExceptionPolicy).filter(ExceptionPolicy.id == 1).first()
    if policy is not None:
        hours = get_deadline_hours(policy, exception_type)
    return detected_at + timedelta(hours=hours)


def _ensure_location_risk_exception(
    db: Session,
    emp: Employee,
    source_checkin_log: AttendanceLog,
    risk_score: int,
    risk_level: str,
    risk_flags: list[str],
    risk_policy_version: str,
    action_type: str,
) -> "AttendanceException | None":
    """Create a SUSPECTED_LOCATION_SPOOF exception for GPS-risk checkin/checkout.

    Returns the newly created AttendanceException, or None if one already existed.
    """
    existing = (
        db.query(AttendanceException)
        .filter(AttendanceException.source_checkin_log_id == source_checkin_log.id)
        .first()
    )
    note = (
        f"GPS risk detected on {action_type}: score={risk_score}, level={risk_level}, "
        f"flags={','.join(risk_flags[:8])}, policy={risk_policy_version}"
    )

    if existing is None:
        detected_at = datetime.now(timezone.utc)
        initial_status = default_exception_status_for_type("SUSPECTED_LOCATION_SPOOF")
        exc = AttendanceException(
            employee_id=emp.id,
            source_checkin_log_id=source_checkin_log.id,
            exception_type="SUSPECTED_LOCATION_SPOOF",
            work_date=source_checkin_log.work_date
            or compute_work_date(
                source_checkin_log.time,
                source_checkin_log.snapshot_cutoff_minutes or DEFAULT_CROSS_DAY_CUTOFF_MINUTES,
            ),
            status=initial_status,
            detected_at=detected_at,
            expires_at=_default_exception_expires_at(detected_at, "SUSPECTED_LOCATION_SPOOF", db) if initial_status == PENDING_EMPLOYEE else None,
            note=note,
            resolved_note=None,
        )
        db.add(exc)
        db.flush()
        return exc

    if existing.exception_type in {"AUTO_CLOSED", "MISSED_CHECKOUT"}:
        if existing.note:
            if "GPS risk detected on" not in existing.note:
                existing.note = f"{existing.note} | {note}"
        else:
            existing.note = note
        return None

    if is_terminal_exception_status(existing.status):
        return None

    existing.exception_type = "SUSPECTED_LOCATION_SPOOF"
    current_status = normalize_exception_status(existing.status)
    target_status = default_exception_status_for_type("SUSPECTED_LOCATION_SPOOF")
    existing.status = current_status or target_status
    if current_status != target_status and can_transition_exception_status(current_status, target_status):
        existing.status = ensure_allowed_exception_transition(current_status, target_status)
    existing.note = note
    existing.resolved_note = None
    existing.resolved_by = None
    existing.resolved_at = None
    return None


def _ensure_large_time_deviation_exception(
    db: Session,
    emp: Employee,
    source_checkin_log: AttendanceLog,
    deviation_seconds: float,
    action_type: str,
) -> "AttendanceException | None":
    """Create a LARGE_TIME_DEVIATION exception.

    Returns the newly created AttendanceException, or None if one already existed.
    """
    existing = (
        db.query(AttendanceException)
        .filter(AttendanceException.source_checkin_log_id == source_checkin_log.id)
        .first()
    )
    note = (
        f"Large time deviation detected on {action_type}: "
        f"client timestamp differs from server by {abs(deviation_seconds):.0f}s "
        f"({abs(deviation_seconds) / 60:.1f} min)"
    )

    if existing is None:
        detected_at = datetime.now(timezone.utc)
        exc = AttendanceException(
            employee_id=emp.id,
            source_checkin_log_id=source_checkin_log.id,
            exception_type="LARGE_TIME_DEVIATION",
            work_date=source_checkin_log.work_date
            or compute_work_date(
                source_checkin_log.time,
                source_checkin_log.snapshot_cutoff_minutes or DEFAULT_CROSS_DAY_CUTOFF_MINUTES,
            ),
            status=default_exception_status_for_type("LARGE_TIME_DEVIATION"),
            detected_at=detected_at,
            note=note,
        )
        db.add(exc)
        db.flush()
        return exc

    if is_terminal_exception_status(existing.status):
        return None

    # Higher-priority types keep their exception_type; append note only.
    if existing.exception_type in {"AUTO_CLOSED", "MISSED_CHECKOUT", "SUSPECTED_LOCATION_SPOOF"}:
        if existing.note and "Large time deviation" not in existing.note:
            existing.note = f"{existing.note} | {note}"
        elif not existing.note:
            existing.note = note
        return None

    # Existing LARGE_TIME_DEVIATION — refresh note.
    existing.note = note
    return None


def _ensure_out_of_range_exception(
    db: Session,
    emp: Employee,
    source_checkin_log: AttendanceLog,
    distance_m: float,
    radius_m: int,
    action_type: str,
) -> "AttendanceException | None":
    """Create a SUSPECTED_LOCATION_SPOOF exception when user checks in/out outside the geofence.

    Only creates a new exception when none already exists for this checkin log.
    Returns the newly created exception, or None if one already existed.
    """
    existing = (
        db.query(AttendanceException)
        .filter(AttendanceException.source_checkin_log_id == source_checkin_log.id)
        .first()
    )
    note = (
        f"Chấm công ngoài phạm vi ({action_type}): "
        f"khoảng cách {distance_m:.0f}m > bán kính {radius_m}m"
    )

    if existing is not None:
        # Append note if not already noted; don't re-notify
        if not is_terminal_exception_status(existing.status):
            if existing.note and "ngoài phạm vi" not in existing.note:
                existing.note = f"{existing.note} | {note}"
            elif not existing.note:
                existing.note = note
        return None

    detected_at = datetime.now(timezone.utc)
    initial_status = default_exception_status_for_type("SUSPECTED_LOCATION_SPOOF")
    exc = AttendanceException(
        employee_id=emp.id,
        source_checkin_log_id=source_checkin_log.id,
        exception_type="SUSPECTED_LOCATION_SPOOF",
        work_date=source_checkin_log.work_date
        or compute_work_date(
            source_checkin_log.time,
            source_checkin_log.snapshot_cutoff_minutes or DEFAULT_CROSS_DAY_CUTOFF_MINUTES,
        ),
        status=initial_status,
        detected_at=detected_at,
        expires_at=_default_exception_expires_at(detected_at, "SUSPECTED_LOCATION_SPOOF", db) if initial_status == PENDING_EMPLOYEE else None,
        note=note,
    )
    db.add(exc)
    db.flush()
    return exc


def _notify_new_exception(
    background_tasks: BackgroundTasks,
    db: Session,
    *,
    exception: AttendanceException,
    employee: Employee,
) -> None:
    """Queue FCM + email notifications for a newly detected exception.

    - PENDING_EMPLOYEE: notifies the employee to explain, and also all admins so they are aware.
    - PENDING_ADMIN: notifies all admins to decide (employee already submitted or skipped).
    """
    # Notify employee when they need to explain
    if exception.status == PENDING_EMPLOYEE and employee.user_id is not None:
        emp_user = db.query(User).filter(User.id == employee.user_id).first()
        if emp_user and emp_user.email:
            payload = build_exception_notification_mail(
                event_type="exception_detected_employee",
                to_email=emp_user.email,
                exception=exception,
                employee=employee,
                recipient_role="EMPLOYEE",
            )
            if payload is not None:
                notif = create_exception_notification_record(
                    db,
                    payload=payload,
                    exception_id=exception.id,
                    recipient_user_id=emp_user.id,
                    recipient_role="EMPLOYEE",
                    dedupe_key=f"exception:{exception.id}:exception_detected_employee:employee:{emp_user.id}",
                )
                if notif is not None:
                    background_tasks.add_task(
                        send_exception_notification_background,
                        payload,
                        notif.id,
                        emp_user.fcm_token,
                    )

    # Always notify all admins
    event_type_admin = "exception_detected_admin"
    admins = db.query(User).filter(User.role == "ADMIN").all()
    for admin in admins:
        if not admin.email:
            continue
        payload = build_exception_notification_mail(
            event_type=event_type_admin,
            to_email=admin.email,
            exception=exception,
            employee=employee,
            recipient_role="ADMIN",
            admin_user=admin,
        )
        if payload is None:
            continue
        notif = create_exception_notification_record(
            db,
            payload=payload,
            exception_id=exception.id,
            recipient_user_id=admin.id,
            recipient_role="ADMIN",
            dedupe_key=f"exception:{exception.id}:{event_type_admin}:admin:{admin.id}",
        )
        if notif is not None:
            background_tasks.add_task(
                send_exception_notification_background,
                payload,
                notif.id,
                admin.fcm_token,
            )


def _missed_checkout_threshold_utc(open_checkin: AttendanceLog) -> datetime:
    cutoff_minutes = open_checkin.snapshot_cutoff_minutes or DEFAULT_CROSS_DAY_CUTOFF_MINUTES
    work_date = _get_log_work_date(open_checkin, cutoff_minutes)
    end_time_value = open_checkin.snapshot_end_time or time(17, 0)
    checkout_grace = (
        open_checkin.snapshot_checkout_grace_minutes
        if open_checkin.snapshot_checkout_grace_minutes is not None
        else 0
    )

    threshold_vn = datetime.combine(work_date, end_time_value, tzinfo=VN_TZ) + timedelta(minutes=max(checkout_grace, 0))
    threshold_utc = threshold_vn.astimezone(timezone.utc)
    cutoff_utc = work_date_cutoff_utc(work_date, cutoff_minutes)
    return min(threshold_utc, cutoff_utc)


def _ensure_missed_checkout_if_due(
    db: Session,
    emp: Employee,
    open_checkin: AttendanceLog | None,
    now_utc: datetime,
) -> date | None:
    if open_checkin is None:
        return None

    cutoff_minutes = open_checkin.snapshot_cutoff_minutes or DEFAULT_CROSS_DAY_CUTOFF_MINUTES
    work_date = _get_log_work_date(open_checkin, cutoff_minutes)
    cutoff_utc = work_date_cutoff_utc(work_date, cutoff_minutes)
    if now_utc >= cutoff_utc:
        return None

    missed_threshold_utc = _missed_checkout_threshold_utc(open_checkin)
    if now_utc < missed_threshold_utc:
        return None

    if _ensure_missed_checkout_exception(db, emp, open_checkin, work_date):
        db.commit()
    return work_date

def _auto_close_open_session_if_past_cutoff(
    db: Session,
    emp: Employee,
    open_checkin: AttendanceLog | None,
    now_utc: datetime,
) -> date | None:
    if open_checkin is None:
        return None

    cutoff_minutes = open_checkin.snapshot_cutoff_minutes or DEFAULT_CROSS_DAY_CUTOFF_MINUTES
    work_date = _get_log_work_date(open_checkin, cutoff_minutes)
    cutoff_utc = work_date_cutoff_utc(work_date, cutoff_minutes)

    if now_utc < cutoff_utc:
        return None

    out_log = AttendanceLog(
        employee_id=emp.id,
        type="OUT",
        time=cutoff_utc,
        work_date=work_date,
        lat=open_checkin.lat,
        lng=open_checkin.lng,
        distance_m=open_checkin.distance_m,
        is_out_of_range=open_checkin.is_out_of_range,
        checkout_status="SYSTEM_AUTO",
        matched_geofence_name=open_checkin.matched_geofence_name,
        geofence_source=open_checkin.geofence_source,
        fallback_reason=open_checkin.fallback_reason,
        risk_score=open_checkin.risk_score,
        risk_level=open_checkin.risk_level,
        risk_flags=open_checkin.risk_flags,
        risk_policy_version=open_checkin.risk_policy_version,
        ip=open_checkin.ip,
        ua_hash=open_checkin.ua_hash,
        accuracy_m=open_checkin.accuracy_m,
        snapshot_start_time=open_checkin.snapshot_start_time,
        snapshot_end_time=open_checkin.snapshot_end_time,
        snapshot_grace_minutes=open_checkin.snapshot_grace_minutes,
        snapshot_checkout_grace_minutes=open_checkin.snapshot_checkout_grace_minutes,
        snapshot_cutoff_minutes=cutoff_minutes,
        time_rule_source=open_checkin.time_rule_source,
        time_rule_fallback_reason=open_checkin.time_rule_fallback_reason,
        address_text="AUTO_CLOSED_AT_CUTOFF",
    )
    db.add(out_log)
    _ensure_auto_closed_exception(db, emp, open_checkin, work_date)
    db.commit()
    return work_date


def _has_checkin_for_work_date(db: Session, employee_id: int, work_date: date) -> bool:
    return (
        db.query(AttendanceLog.id)
        .filter(
            AttendanceLog.employee_id == employee_id,
            AttendanceLog.work_date == work_date,
            AttendanceLog.type == "IN",
        )
        .first()
        is not None
    )


def _has_checkout_for_work_date(db: Session, employee_id: int, work_date: date) -> bool:
    return (
        db.query(AttendanceLog.id)
        .filter(
            AttendanceLog.employee_id == employee_id,
            AttendanceLog.work_date == work_date,
            AttendanceLog.type == "OUT",
        )
        .first()
        is not None
    )


def _to_log_response(log: AttendanceLog) -> AttendanceLogResponse:
    return AttendanceLogResponse(
        id=log.id,
        type=log.type,
        time=log.time,
        work_date=log.work_date,
        lat=log.lat,
        lng=log.lng,
        distance_m=log.distance_m,
        nearest_distance_m=log.distance_m,
        matched_geofence=log.matched_geofence_name,
        geofence_source=log.geofence_source,
        fallback_reason=log.fallback_reason,
        time_rule_source=log.time_rule_source,
        time_rule_fallback_reason=log.time_rule_fallback_reason,
        is_out_of_range=log.is_out_of_range,
        punctuality_status=log.punctuality_status,
        checkout_status=log.checkout_status,
        risk_score=log.risk_score,
        risk_level=log.risk_level,
        risk_flags=_deserialize_risk_flags(log.risk_flags),
        risk_policy_version=log.risk_policy_version,
        ip=log.ip,
        ua_hash=log.ua_hash,
        accuracy_m=log.accuracy_m,
    )


def _rank_to_punctuality(rank_value) -> str | None:
    if rank_value is None:
        return None
    mapping = {1: "EARLY", 2: "ON_TIME", 3: "LATE"}
    return mapping.get(int(rank_value))


def _rank_to_geofence_source(rank_value) -> str | None:
    if rank_value is None:
        return None
    mapping = {1: "GROUP", 2: "SYSTEM_FALLBACK"}
    return mapping.get(int(rank_value))


def _derive_daily_status(
    checkin_time: datetime | None,
    checkout_time: datetime | None,
    checkin_rank,
    checkout_rank,
    checkout_raw_status: str | None = None,
) -> tuple[str | None, str | None, str]:
    checkin_status = _rank_to_punctuality(checkin_rank)
    checkout_status = _rank_to_punctuality(checkout_rank)

    if checkout_raw_status in {"SYSTEM_AUTO", "MISSING_PUNCH"}:
        checkout_status = checkout_raw_status

    if checkin_time is None and checkout_time is None:
        return "NO_CHECKIN", "NO_CHECKOUT", "ABSENT"

    if checkin_time is None:
        return "NO_CHECKIN", checkout_status or "NO_CHECKOUT", "MISSING_CHECKIN_ANOMALY"

    if checkout_time is None:
        return checkin_status, "NO_CHECKOUT", "MISSED_CHECKOUT"

    return checkin_status, checkout_status, "COMPLETE"


def _attendance_work_date_expr(db: Session):
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        legacy_expr = func.date(func.timezone("Asia/Ho_Chi_Minh", AttendanceLog.time))
    else:
        legacy_expr = func.date(AttendanceLog.time)
    return func.coalesce(AttendanceLog.work_date, legacy_expr)


def _build_exception_map(
    db: Session,
    from_date: date | None,
    to_date: date | None,
    employee_id: int | None,
) -> dict[tuple[int, date], tuple[str, str]]:
    q = db.query(
        AttendanceException.employee_id,
        AttendanceException.work_date,
        AttendanceException.status,
        AttendanceException.exception_type,
    )

    if employee_id:
        q = q.filter(AttendanceException.employee_id == employee_id)
    if from_date:
        q = q.filter(AttendanceException.work_date >= from_date)
    if to_date:
        q = q.filter(AttendanceException.work_date <= to_date)

    status_map: dict[tuple[int, date], tuple[str, str]] = {}
    for row in q.all():
        key = (row.employee_id, row.work_date)
        current = status_map.get(key)
        normalized_status = normalize_exception_status(row.status)
        if current is None or not is_pending_exception_status(current[0]):
            status_map[key] = (normalized_status, row.exception_type)
    return status_map


def _apply_exception_to_attendance_state(
    attendance_state: str,
    exception_status: str | None,
    exception_type: str | None,
) -> str:
    if is_pending_timesheet_exception(exception_status, exception_type):
        return "PENDING_TIMESHEET"
    return attendance_state


def _get_pending_face_log_id(db: Session, employee_id: int, work_date) -> int | None:
    """Return the most-recent log for work_date whose face has not been captured yet.

    face_check_status IS NULL means the upload dialog was never completed
    (e.g. employee refreshed the page right after checkin/checkout).
    QUALITY_LOW / NOT_CAPTURED are terminal states — no retry needed.
    """
    log = (
        db.query(AttendanceLog)
        .filter(
            AttendanceLog.employee_id == employee_id,
            AttendanceLog.work_date == work_date,
            AttendanceLog.face_check_status.is_(None),
        )
        .order_by(AttendanceLog.time.desc())
        .first()
    )
    return log.id if log else None


@router.get("/status", response_model=AttendanceStatusResponse)
def my_attendance_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = _find_employee_for_user(db, user)
    if not emp:
        return AttendanceStatusResponse(
            employee_assigned=False,
            employee_id=None,
            current_state="UNASSIGNED",
            last_action=None,
            last_action_time=None,
            can_checkin=False,
            can_checkout=False,
            message="Tai khoan chua duoc gan employee",
        )


    if emp.deleted_at is not None:
        return AttendanceStatusResponse(
            employee_assigned=True,
            employee_id=emp.id,
            current_state="INACTIVE",
            last_action=None,
            last_action_time=None,
            can_checkin=False,
            can_checkout=False,
            message="Nhan vien da nghi viec",
        )
    if not emp.active:
        return AttendanceStatusResponse(
            employee_assigned=True,
            employee_id=emp.id,
            current_state="INACTIVE",
            last_action=None,
            last_action_time=None,
            can_checkin=False,
            can_checkout=False,
            message="Tai khoan nhan vien dang bi vo hieu hoa",
        )

    now_utc = datetime.now(timezone.utc)
    open_checkin = _get_open_session_checkin(db, emp.id)
    auto_closed_work_date = _auto_close_open_session_if_past_cutoff(db, emp, open_checkin, now_utc)

    if auto_closed_work_date is None:
        open_checkin = _get_open_session_checkin(db, emp.id)
        if open_checkin:
            open_work_date = _get_log_work_date(
                open_checkin,
                open_checkin.snapshot_cutoff_minutes or DEFAULT_CROSS_DAY_CUTOFF_MINUTES,
            )
            missed_work_date = _ensure_missed_checkout_if_due(db, emp, open_checkin, now_utc)
            if missed_work_date is not None:
                message = (
                    f"Phien IN ngay cong {_format_vn_date(missed_work_date)} da qua nguong checkout. "
                    "Hay checkout hoac lien he admin de xu ly."
                )
            else:
                message = f"Ban dang trong phien lam viec ngay cong {_format_vn_date(open_work_date)}."

            return AttendanceStatusResponse(
                employee_assigned=True,
                employee_id=emp.id,
                current_state="IN",
                last_action="IN",
                last_action_time=open_checkin.time,
                can_checkin=False,
                can_checkout=True,
                message=message,
                warning_code="MISSED_CHECKOUT" if missed_work_date is not None else None,
                warning_date=missed_work_date,
                pending_face_log_id=_get_pending_face_log_id(db, emp.id, open_work_date),
            )

    active_rule = _get_active_rule(db)
    time_rule = _get_effective_time_rule(db, emp, active_rule)
    current_work_date = compute_work_date(now_utc, time_rule.cutoff_minutes)

    completed_today = _has_checkout_for_work_date(db, emp.id, current_work_date)
    last = _last_log(db, emp.id)

    if completed_today:
        return AttendanceStatusResponse(
            employee_assigned=True,
            employee_id=emp.id,
            current_state="OUT",
            last_action=last.type if last else None,
            last_action_time=last.time if last else None,
            can_checkin=False,
            can_checkout=False,
            message="Ban da hoan thanh phien lam viec cho ngay cong hien tai.",
            warning_code="AUTO_CLOSED" if auto_closed_work_date else None,
            warning_date=auto_closed_work_date,
            pending_face_log_id=_get_pending_face_log_id(db, emp.id, current_work_date),
        )

    return AttendanceStatusResponse(
        employee_assigned=True,
        employee_id=emp.id,
        current_state="OUT",
        last_action=last.type if last else None,
        last_action_time=last.time if last else None,
        can_checkin=True,
        can_checkout=False,
        message=(
            f"Ban co the check-in ngay cong {_format_vn_date(current_work_date)}."
            if auto_closed_work_date is None
            else f"He thong da tu dong dong phien ngay {_format_vn_date(auto_closed_work_date)} do qua cutoff. Ban co the check-in phien moi."
        ),
        warning_code="AUTO_CLOSED" if auto_closed_work_date else None,
        warning_date=auto_closed_work_date,
    )


@router.post("/checkin", response_model=CheckActionResponse)
def checkin(
    payload: LocationRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _get_employee_for_user(db, user)
    _lock_employee_row(db, emp.id)
    active_rule = _get_active_rule(db)
    geofences, geofence_source, geofence_fallback_reason = _get_effective_geofences(db, emp, active_rule)
    using_fallback_rule = geofence_source == "SYSTEM_FALLBACK"
    time_rule = _get_effective_time_rule(db, emp, active_rule)

    checkin_at = datetime.now(timezone.utc)

    open_checkin = _get_open_session_checkin(db, emp.id)
    auto_closed_work_date = _auto_close_open_session_if_past_cutoff(db, emp, open_checkin, checkin_at)

    open_checkin = _get_open_session_checkin(db, emp.id)
    if open_checkin:
        missed_work_date = _ensure_missed_checkout_if_due(db, emp, open_checkin, checkin_at)
        if missed_work_date is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Phien IN ngay cong {_format_vn_date(missed_work_date)} da qua nguong checkout (MISSED_CHECKOUT). "
                    "Hay checkout hoac nho admin xu ly."
                ),
            )
        raise HTTPException(status_code=400, detail="Ban dang co phien IN chua checkout. Hay checkout truoc.")

    work_date = compute_work_date(checkin_at, time_rule.cutoff_minutes)
    if _has_checkin_for_work_date(db, emp.id, work_date):
        raise HTTPException(status_code=400, detail="Bạn đã có phiên chấm công cho ngày công này.")

    punctuality_status = classify_checkin_status(
        checkin_at,
        time_rule.start_time,
        time_rule.grace_minutes,
        work_date=work_date,
    )

    nearest_distance, out_of_range, nearest_radius, matched_geofence_name = _evaluate_range(
        payload.lat,
        payload.lng,
        geofences,
    )
    client_ip = _extract_client_ip(request)
    user_agent = request.headers.get("user-agent")
    previous_action = _last_log(db, emp.id)
    recent_exact_coord_reuse_count = _count_recent_exact_coordinate_reuse(
        db,
        emp.id,
        payload.lat,
        payload.lng,
    )
    risk_assessment = assess_location_risk(
        LocationRiskInput(
            lat=payload.lat,
            lng=payload.lng,
            accuracy_m=payload.accuracy_m,
            timestamp_client=payload.timestamp_client,
            server_time=checkin_at,
            ip=client_ip,
            user_agent=user_agent,
            accept_language=request.headers.get("accept-language"),
            ip_geo_lat=_extract_client_ip_geo_lat(request),
            ip_geo_lng=_extract_client_ip_geo_lng(request),
            ip_asn=_extract_client_asn_hint(request),
            ip_proxy_or_vpn=_extract_client_proxy_hint(request),
            risk_policy_version=settings.RISK_POLICY_VERSION,
            distance_to_geofence_m=nearest_distance,
            radius_m=nearest_radius,
            is_out_of_range=out_of_range,
            previous_action_time=previous_action.time if previous_action else None,
            previous_action_lat=previous_action.lat if previous_action else None,
            previous_action_lng=previous_action.lng if previous_action else None,
            recent_exact_coord_reuse_count=recent_exact_coord_reuse_count,
        )
    )
    if risk_assessment.decision == "BLOCK":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "LOCATION_RISK_BLOCKED",
                "message": "Vị trí check-in bị từ chối do rủi ro giả mạo GPS cao.",
                "details": {
                    "risk_score": risk_assessment.score,
                    "risk_level": risk_assessment.level,
                    "risk_flags": risk_assessment.flags,
                    "risk_policy_version": risk_assessment.policy_version,
                    "decision": risk_assessment.decision,
                    "message": risk_assessment.user_message,
                },
            },
        )

    risk_note = None
    if risk_assessment.decision != "ALLOW":
        risk_note = (
            f"RISK:{risk_assessment.decision};"
            f"SCORE={risk_assessment.score};"
            f"FLAGS={','.join(risk_assessment.flags[:6])}"
        )

    log = AttendanceLog(
        employee_id=emp.id,
        type="IN",
        time=checkin_at,
        work_date=work_date,
        lat=payload.lat,
        lng=payload.lng,
        distance_m=nearest_distance,
        is_out_of_range=out_of_range,
        punctuality_status=punctuality_status,
        matched_geofence_name=matched_geofence_name,
        geofence_source=geofence_source,
        fallback_reason=geofence_fallback_reason,
        snapshot_start_time=time_rule.start_time,
        snapshot_end_time=time_rule.end_time,
        snapshot_grace_minutes=time_rule.grace_minutes,
        snapshot_checkout_grace_minutes=time_rule.checkout_grace_minutes,
        snapshot_cutoff_minutes=time_rule.cutoff_minutes,
        time_rule_source=time_rule.source,
        time_rule_fallback_reason=time_rule.fallback_reason,
        risk_score=risk_assessment.score,
        risk_level=risk_assessment.level,
        risk_flags=_serialize_risk_flags(risk_assessment.flags),
        risk_policy_version=risk_assessment.policy_version,
        ip=client_ip,
        ua_hash=_hash_user_agent(user_agent),
        accuracy_m=payload.accuracy_m,
        address_text=risk_note,
    )

    db.add(log)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Bạn đã có phiên chấm công cho ngày công này.")
    db.refresh(log)

    new_exception: AttendanceException | None = None

    if risk_assessment.decision == "ALLOW_WITH_EXCEPTION":
        new_exception = _ensure_location_risk_exception(
            db=db,
            emp=emp,
            source_checkin_log=log,
            risk_score=risk_assessment.score,
            risk_level=risk_assessment.level,
            risk_flags=risk_assessment.flags,
            risk_policy_version=risk_assessment.policy_version,
            action_type="IN",
        )
        db.commit()
    elif out_of_range:
        # Plain out-of-range (no GPS spoofing risk detected) — still needs an exception
        new_exception = _ensure_out_of_range_exception(
            db=db,
            emp=emp,
            source_checkin_log=log,
            distance_m=nearest_distance,
            radius_m=nearest_radius,
            action_type="IN",
        )
        db.commit()

    if payload.timestamp_client is not None:
        deviation_sec = (checkin_at - payload.timestamp_client).total_seconds()
        if abs(deviation_sec) > settings.LARGE_TIME_DEVIATION_THRESHOLD_MINUTES * 60:
            ltd_exc = _ensure_large_time_deviation_exception(
                db=db,
                emp=emp,
                source_checkin_log=log,
                deviation_seconds=deviation_sec,
                action_type="IN",
            )
            db.commit()
            if new_exception is None:
                new_exception = ltd_exc

    if new_exception is not None:
        _notify_new_exception(background_tasks, db, exception=new_exception, employee=emp)
        db.commit()

    msg = f"Check-in thành công ({punctuality_status}) cho ngày công {_format_vn_date(work_date)}."
    if out_of_range:
        msg = (
            f"Cảnh báo: bạn đã ở ngoài vùng cho phép ({nearest_distance:.1f}m > {nearest_radius}m). "
            f"Trạng thái giờ vào: {punctuality_status}."
        )
    elif matched_geofence_name:
        msg = f"Check-in thành công ({punctuality_status}) tại geofence: {matched_geofence_name}."
    elif using_fallback_rule:
        msg = f"Check-in thành công ({punctuality_status}) theo rule fallback hệ thống ({geofence_fallback_reason})."

    if auto_closed_work_date:
        msg = f"{msg} Hệ thống đã tự đóng phiên cũ ngày {_format_vn_date(auto_closed_work_date)} do qua cutoff."
    if risk_assessment.decision == "ALLOW_WITH_EXCEPTION":
        msg = f"{msg} {risk_assessment.user_message}"

    return CheckActionResponse(
        log=_to_log_response(log),
        message=msg,
        geofence_source=geofence_source,
        fallback_reason=geofence_fallback_reason,
        risk_score=risk_assessment.score,
        risk_level=risk_assessment.level,
        risk_flags=risk_assessment.flags,
        decision=risk_assessment.decision,
    )


@router.post("/checkout", response_model=CheckActionResponse)
def checkout(
    payload: LocationRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _get_employee_for_user(db, user)
    _lock_employee_row(db, emp.id)
    active_rule = _get_active_rule(db)
    geofences, geofence_source, geofence_fallback_reason = _get_effective_geofences(db, emp, active_rule)
    using_fallback_rule = geofence_source == "SYSTEM_FALLBACK"

    checkout_at = datetime.now(timezone.utc)

    open_checkin = _get_open_session_checkin(db, emp.id)
    auto_closed_work_date = _auto_close_open_session_if_past_cutoff(db, emp, open_checkin, checkout_at)
    if auto_closed_work_date is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Phiên ngày {_format_vn_date(auto_closed_work_date)} đã bị auto-close do quá cutoff. Hãy check-in phiên mới.",
        )

    open_checkin = _get_open_session_checkin(db, emp.id)
    if not open_checkin:
        raise HTTPException(status_code=400, detail="Không có phiên IN đang mở để checkout.")

    shift_end = open_checkin.snapshot_end_time or active_rule.end_time
    checkout_grace = open_checkin.snapshot_checkout_grace_minutes
    if checkout_grace is None:
        checkout_grace = active_rule.checkout_grace_minutes

    work_date = _get_log_work_date(open_checkin, open_checkin.snapshot_cutoff_minutes or DEFAULT_CROSS_DAY_CUTOFF_MINUTES)

    checkout_status = classify_checkout_status(
        checkout_at,
        shift_end,
        checkout_grace,
        work_date=work_date,
    )

    nearest_distance, out_of_range, nearest_radius, matched_geofence_name = _evaluate_range(
        payload.lat,
        payload.lng,
        geofences,
    )
    client_ip = _extract_client_ip(request)
    user_agent = request.headers.get("user-agent")
    previous_action = _last_log(db, emp.id)
    recent_exact_coord_reuse_count = _count_recent_exact_coordinate_reuse(
        db,
        emp.id,
        payload.lat,
        payload.lng,
    )
    risk_assessment = assess_location_risk(
        LocationRiskInput(
            lat=payload.lat,
            lng=payload.lng,
            accuracy_m=payload.accuracy_m,
            timestamp_client=payload.timestamp_client,
            server_time=checkout_at,
            ip=client_ip,
            user_agent=user_agent,
            accept_language=request.headers.get("accept-language"),
            ip_geo_lat=_extract_client_ip_geo_lat(request),
            ip_geo_lng=_extract_client_ip_geo_lng(request),
            ip_asn=_extract_client_asn_hint(request),
            ip_proxy_or_vpn=_extract_client_proxy_hint(request),
            risk_policy_version=settings.RISK_POLICY_VERSION,
            distance_to_geofence_m=nearest_distance,
            radius_m=nearest_radius,
            is_out_of_range=out_of_range,
            previous_action_time=previous_action.time if previous_action else None,
            previous_action_lat=previous_action.lat if previous_action else None,
            previous_action_lng=previous_action.lng if previous_action else None,
            recent_exact_coord_reuse_count=recent_exact_coord_reuse_count,
        )
    )
    if risk_assessment.decision == "BLOCK":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "LOCATION_RISK_BLOCKED",
                "message": "Vị trí check-out bị từ chối do rủi ro giả mạo GPS cao.",
                "details": {
                    "risk_score": risk_assessment.score,
                    "risk_level": risk_assessment.level,
                    "risk_flags": risk_assessment.flags,
                    "risk_policy_version": risk_assessment.policy_version,
                    "decision": risk_assessment.decision,
                    "message": risk_assessment.user_message,
                },
            },
        )

    risk_note = None
    if risk_assessment.decision != "ALLOW":
        risk_note = (
            f"RISK:{risk_assessment.decision};"
            f"SCORE={risk_assessment.score};"
            f"FLAGS={','.join(risk_assessment.flags[:6])}"
        )

    log = AttendanceLog(
        employee_id=emp.id,
        type="OUT",
        time=checkout_at,
        work_date=work_date,
        lat=payload.lat,
        lng=payload.lng,
        distance_m=nearest_distance,
        is_out_of_range=out_of_range,
        checkout_status=checkout_status,
        matched_geofence_name=matched_geofence_name,
        geofence_source=geofence_source,
        fallback_reason=geofence_fallback_reason,
        snapshot_start_time=open_checkin.snapshot_start_time,
        snapshot_end_time=open_checkin.snapshot_end_time,
        snapshot_grace_minutes=open_checkin.snapshot_grace_minutes,
        snapshot_checkout_grace_minutes=open_checkin.snapshot_checkout_grace_minutes,
        snapshot_cutoff_minutes=open_checkin.snapshot_cutoff_minutes,
        time_rule_source=open_checkin.time_rule_source,
        time_rule_fallback_reason=open_checkin.time_rule_fallback_reason,
        risk_score=risk_assessment.score,
        risk_level=risk_assessment.level,
        risk_flags=_serialize_risk_flags(risk_assessment.flags),
        risk_policy_version=risk_assessment.policy_version,
        ip=client_ip,
        ua_hash=_hash_user_agent(user_agent),
        accuracy_m=payload.accuracy_m,
        address_text=risk_note,
    )

    db.add(log)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Bạn đã checkout cho ngày công này.")
    db.refresh(log)

    new_exception: AttendanceException | None = None

    if risk_assessment.decision == "ALLOW_WITH_EXCEPTION":
        new_exception = _ensure_location_risk_exception(
            db=db,
            emp=emp,
            source_checkin_log=open_checkin,
            risk_score=risk_assessment.score,
            risk_level=risk_assessment.level,
            risk_flags=risk_assessment.flags,
            risk_policy_version=risk_assessment.policy_version,
            action_type="OUT",
        )
        db.commit()
    elif out_of_range:
        new_exception = _ensure_out_of_range_exception(
            db=db,
            emp=emp,
            source_checkin_log=open_checkin,
            distance_m=nearest_distance,
            radius_m=nearest_radius,
            action_type="OUT",
        )
        db.commit()

    if payload.timestamp_client is not None:
        deviation_sec = (checkout_at - payload.timestamp_client).total_seconds()
        if abs(deviation_sec) > settings.LARGE_TIME_DEVIATION_THRESHOLD_MINUTES * 60:
            ltd_exc = _ensure_large_time_deviation_exception(
                db=db,
                emp=emp,
                source_checkin_log=open_checkin,
                deviation_seconds=deviation_sec,
                action_type="OUT",
            )
            db.commit()
            if new_exception is None:
                new_exception = ltd_exc

    if new_exception is not None:
        _notify_new_exception(background_tasks, db, exception=new_exception, employee=emp)
        db.commit()

    # Phase 2.5 — auto-create OT record if checkout exceeds shift end by threshold.
    # Best-effort: failures here must not block a successful checkout. The
    # checkout log itself is already committed above; this is a separate
    # transaction so a rollback only discards the unflushed OT row.
    try:
        auto_create_pending_ot(db, log, checkin_log=open_checkin)
        db.commit()
    except Exception as ot_exc:
        db.rollback()
        import logging
        logging.getLogger(__name__).warning(
            "auto_create_pending_ot failed for employee=%s work_date=%s: %s",
            emp.id, log.work_date, ot_exc,
        )

    msg = f"Check-out thành công ({checkout_status}) cho ngày công {_format_vn_date(work_date)}."
    if out_of_range:
        msg = (
            f"Cảnh báo: bạn đã ở ngoài vùng cho phép ({nearest_distance:.1f}m > {nearest_radius}m). "
            f"Trạng thái giờ về: {checkout_status}."
        )
    elif matched_geofence_name:
        msg = f"Check-out thành công ({checkout_status}) tại geofence: {matched_geofence_name}."
    elif using_fallback_rule:
        msg = f"Check-out thành công ({checkout_status}) theo rule fallback hệ thống ({geofence_fallback_reason})."
    if risk_assessment.decision == "ALLOW_WITH_EXCEPTION":
        msg = f"{msg} {risk_assessment.user_message}"

    return CheckActionResponse(
        log=_to_log_response(log),
        message=msg,
        geofence_source=geofence_source,
        fallback_reason=geofence_fallback_reason,
        risk_score=risk_assessment.score,
        risk_level=risk_assessment.level,
        risk_flags=risk_assessment.flags,
        decision=risk_assessment.decision,
    )


@router.get("/me", response_model=list[AttendanceLogResponse])
def my_logs(
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _get_employee_for_user(db, user)

    q = db.query(AttendanceLog).filter(AttendanceLog.employee_id == emp.id)

    if from_date:
        q = q.filter(AttendanceLog.time >= from_date)
    if to_date:
        q = q.filter(AttendanceLog.time <= to_date)

    logs = q.order_by(AttendanceLog.time.desc()).all()
    return [_to_log_response(x) for x in logs]


@router.get("/me/shift", response_model=MyShiftResponse)
def my_resolved_shift(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Phase 3C — return the shift currently in effect for the caller.

    Mirrors the same resolve chain used at checkin time so the home screen
    always shows the right window before the user actually checks in.
    """
    emp = _get_employee_for_user(db, user)
    active_rule = _get_active_rule(db)
    rule = _get_effective_time_rule(db, emp, active_rule)
    return MyShiftResponse(
        shift_name=rule.shift_name,
        start_time=rule.start_time.strftime("%H:%M"),
        end_time=rule.end_time.strftime("%H:%M"),
        source=rule.source,
    )


@router.get("/me/stats", response_model=MyMonthlyStatsResponse)
def my_monthly_stats(
    month: str | None = None,  # YYYY-MM; defaults to current month in VN time
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Phase 4 — Employee self stats for one month.

    Returns attendance counts, leave usage, leave balance, and Plan-B
    worked-minute totals (regular + approved OT) for the given month.
    """
    emp = _get_employee_for_user(db, user)

    # ── Resolve target month ──────────────────────────────────────────────
    if month is None:
        today_vn = datetime.now(VN_TZ).date()
        target_year, target_month = today_vn.year, today_vn.month
    else:
        try:
            target_year, target_month = map(int, month.split("-", 1))
            if not (2000 <= target_year <= 2100 and 1 <= target_month <= 12):
                raise ValueError
        except (ValueError, AttributeError):
            raise HTTPException(status_code=422, detail="month must be YYYY-MM")

    period_start = date(target_year, target_month, 1)
    if target_month == 12:
        next_first = date(target_year + 1, 1, 1)
    else:
        next_first = date(target_year, target_month + 1, 1)
    period_last_day_of_month = next_first - timedelta(days=1)

    today_vn = datetime.now(VN_TZ).date()
    period_end = min(period_last_day_of_month, today_vn)
    if period_end < period_start:
        # Future month: nothing to count yet, but return empty stats.
        period_end = period_start - timedelta(days=1)

    # ── Attendance counts (per work_date IN-log) ──────────────────────────
    in_logs = (
        db.query(
            AttendanceLog.work_date,
            AttendanceLog.punctuality_status,
        )
        .filter(
            AttendanceLog.employee_id == emp.id,
            AttendanceLog.type == "IN",
            AttendanceLog.work_date >= period_start,
            AttendanceLog.work_date <= period_last_day_of_month,
        )
        .all()
    )

    checkin_dates: set[date] = set()
    on_time_dates: set[date] = set()
    late_dates: set[date] = set()
    early_dates: set[date] = set()
    for wd, status in in_logs:
        if wd is None:
            continue
        checkin_dates.add(wd)
        if status == "ON_TIME":
            on_time_dates.add(wd)
        elif status == "LATE":
            late_dates.add(wd)
        elif status == "EARLY":
            early_dates.add(wd)

    # ── Holidays in the period ────────────────────────────────────────────
    holidays_in_period = {
        h.date
        for h in db.query(PublicHoliday)
        .filter(PublicHoliday.date >= period_start, PublicHoliday.date <= period_end)
        .all()
    }

    # ── Approved leave days that overlap the period ───────────────────────
    overlapping_leaves = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.employee_id == emp.id,
            LeaveRequest.status == "APPROVED",
            LeaveRequest.start_date <= period_last_day_of_month,
            LeaveRequest.end_date >= period_start,
        )
        .all()
    )
    leave_days_in_period: set[date] = set()
    leave_days_used = 0.0
    for req in overlapping_leaves:
        eff_start = max(req.start_date, period_start)
        eff_end = min(req.end_date, period_last_day_of_month)
        cur = eff_start
        while cur <= eff_end:
            leave_days_in_period.add(cur)
            cur += timedelta(days=1)
        # leave days used count uses paid leave only for balance accuracy;
        # for monthly summary, count all approved leave days within the month.
        leave_days_used += float((eff_end - eff_start).days + 1)

    pending_leaves = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.employee_id == emp.id,
            LeaveRequest.status == "PENDING",
            LeaveRequest.start_date <= period_last_day_of_month,
            LeaveRequest.end_date >= period_start,
        )
        .all()
    )
    leave_days_pending = 0.0
    for req in pending_leaves:
        eff_start = max(req.start_date, period_start)
        eff_end = min(req.end_date, period_last_day_of_month)
        leave_days_pending += float((eff_end - eff_start).days + 1)

    # ── Working days (weekdays in [start, end] minus holidays/leaves) ─────
    working_days = 0
    cur = period_start
    while cur <= period_end:
        is_weekend = cur.weekday() >= 5
        if not is_weekend and cur not in holidays_in_period and cur not in leave_days_in_period:
            working_days += 1
        cur += timedelta(days=1)

    checkins_total = len(checkin_dates)
    absent_days = max(0, working_days - checkins_total)

    # ── Worked minutes (regular + approved OT) ────────────────────────────
    # Pair up IN/OUT logs by work_date and compute regular minutes per pair.
    pair_logs = (
        db.query(AttendanceLog)
        .filter(
            AttendanceLog.employee_id == emp.id,
            AttendanceLog.work_date >= period_start,
            AttendanceLog.work_date <= period_last_day_of_month,
            AttendanceLog.type.in_(["IN", "OUT"]),
        )
        .order_by(AttendanceLog.work_date.asc(), AttendanceLog.time.asc())
        .all()
    )

    by_date: dict[date, dict[str, AttendanceLog]] = {}
    for log in pair_logs:
        if log.work_date is None:
            continue
        bucket = by_date.setdefault(log.work_date, {})
        if log.type == "IN" and "in" not in bucket:
            bucket["in"] = log
        elif log.type == "OUT":
            # keep the latest OUT for the day
            bucket["out"] = log

    rule = _get_active_rule(db)
    default_start = rule.start_time if rule else time(8, 0)
    default_end = rule.end_time if rule else time(17, 0)

    total_regular_minutes = 0
    for wd, bucket in by_date.items():
        in_log = bucket.get("in")
        out_log = bucket.get("out")
        if in_log is None or out_log is None:
            continue
        shift_start = (in_log.snapshot_start_time or default_start)
        shift_end = (in_log.snapshot_end_time or default_end)
        regular, _, _ = split_regular_overtime_minutes(
            wd, in_log.time, out_log.time, shift_start, shift_end,
        )
        total_regular_minutes += regular

    payable_map = fetch_payable_minutes_map(
        db,
        employee_ids=[emp.id],
        from_date=period_start,
        to_date=period_last_day_of_month,
    )
    total_approved_overtime_minutes = sum(payable_map.values())

    # Pending OT: sum raw_minutes for status='PENDING' in the month
    pending_rows = (
        db.query(OvertimeRecord.raw_minutes)
        .filter(
            OvertimeRecord.employee_id == emp.id,
            OvertimeRecord.status == "PENDING",
            OvertimeRecord.work_date >= period_start,
            OvertimeRecord.work_date <= period_last_day_of_month,
        )
        .all()
    )
    total_pending_overtime_minutes = sum(int(r[0] or 0) for r in pending_rows)

    total_worked_minutes = total_regular_minutes + total_approved_overtime_minutes

    # ── Leave balance for the year of this month ──────────────────────────
    # Delegate to the canonical helper in leave.py so /attendance/me/stats and
    # /leave-requests/me/balance can never disagree on remaining quota.
    from app.api.leave import compute_leave_balance
    balance = compute_leave_balance(emp, db, target_year)
    quota = balance.annual_quota
    balance_remaining = balance.days_remaining

    return MyMonthlyStatsResponse(
        month=f"{target_year:04d}-{target_month:02d}",
        period_start=period_start,
        period_end=period_end,
        checkins_total=checkins_total,
        checkins_on_time=len(on_time_dates),
        checkins_late=len(late_dates),
        checkins_early=len(early_dates),
        absent_days=absent_days,
        working_days=working_days,
        leave_days_used=leave_days_used,
        leave_days_pending=leave_days_pending,
        annual_quota=quota,
        leave_balance_remaining=balance_remaining,
        total_worked_minutes=total_worked_minutes,
        total_regular_minutes=total_regular_minutes,
        total_approved_overtime_minutes=total_approved_overtime_minutes,
        total_pending_overtime_minutes=total_pending_overtime_minutes,
    )


@router.get("/report/daily", response_model=list[AttendanceDailyReportResponse])
def daily_report_admin(
    from_date: date | None = None,
    to_date: date | None = None,
    employee_id: int | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    work_date_expr = _attendance_work_date_expr(db)

    checkin_time_expr = func.min(case((AttendanceLog.type == "IN", AttendanceLog.time), else_=None)).label("checkin_time")
    checkout_time_expr = func.max(case((AttendanceLog.type == "OUT", AttendanceLog.time), else_=None)).label("checkout_time")

    punctuality_rank_expr = func.min(
        case(
            (
                AttendanceLog.type == "IN",
                case(
                    (AttendanceLog.punctuality_status == "EARLY", 1),
                    (AttendanceLog.punctuality_status == "ON_TIME", 2),
                    (AttendanceLog.punctuality_status == "LATE", 3),
                    else_=None,
                ),
            ),
            else_=None,
        )
    ).label("punctuality_rank")

    checkout_rank_expr = func.max(
        case(
            (
                AttendanceLog.type == "OUT",
                case(
                    (AttendanceLog.checkout_status == "EARLY", 1),
                    (AttendanceLog.checkout_status == "ON_TIME", 2),
                    (AttendanceLog.checkout_status == "LATE", 3),
                    else_=None,
                ),
            ),
            else_=None,
        )
    ).label("checkout_rank")

    checkout_raw_status_expr = func.max(
        case((AttendanceLog.type == "OUT", AttendanceLog.checkout_status), else_=None)
    ).label("checkout_raw_status")


    checkin_matched_geofence_expr = func.max(
        case((AttendanceLog.type == "IN", AttendanceLog.matched_geofence_name), else_=None)
    ).label("checkin_matched_geofence")
    checkout_matched_geofence_expr = func.max(
        case((AttendanceLog.type == "OUT", AttendanceLog.matched_geofence_name), else_=None)
    ).label("checkout_matched_geofence")

    geofence_source_rank_expr = func.min(
        case(
            (AttendanceLog.geofence_source == "GROUP", 1),
            (AttendanceLog.geofence_source == "SYSTEM_FALLBACK", 2),
            else_=None,
        )
    ).label("geofence_source_rank")
    fallback_reason_expr = func.max(
        case((AttendanceLog.geofence_source == "SYSTEM_FALLBACK", AttendanceLog.fallback_reason), else_=None)
    ).label("fallback_reason")

    out_of_range_expr = func.bool_or(AttendanceLog.is_out_of_range).label("out_of_range")
    avg_distance_expr = func.avg(AttendanceLog.distance_m).label("avg_distance_m")
    max_distance_expr = func.max(AttendanceLog.distance_m).label("max_distance_m")
    shift_start_expr = func.max(case((AttendanceLog.type == "IN", AttendanceLog.snapshot_start_time), else_=None)).label("shift_start")
    shift_end_expr = func.max(case((AttendanceLog.type == "IN", AttendanceLog.snapshot_end_time), else_=None)).label("shift_end")

    q = (
        db.query(
            work_date_expr.label("work_date"),
            Employee.id.label("employee_id"),
            Employee.code.label("employee_code"),
            Employee.full_name.label("full_name"),
            Group.id.label("group_id"),
            Group.code.label("group_code"),
            Group.name.label("group_name"),
            checkin_time_expr,
            checkout_time_expr,
            punctuality_rank_expr,
            checkout_rank_expr,
            checkout_raw_status_expr,
            checkin_matched_geofence_expr,
            checkout_matched_geofence_expr,
            geofence_source_rank_expr,
            fallback_reason_expr,
            out_of_range_expr,
            avg_distance_expr,
            max_distance_expr,
            shift_start_expr,
            shift_end_expr,
        )
        .join(Employee, Employee.id == AttendanceLog.employee_id)
        .outerjoin(Group, Group.id == Employee.group_id)
    )

    if employee_id:
        q = q.filter(AttendanceLog.employee_id == employee_id)
    if from_date:
        q = q.filter(work_date_expr >= from_date)
    if to_date:
        q = q.filter(work_date_expr <= to_date)

    rows = (
        q.group_by(work_date_expr, Employee.id, Employee.code, Employee.full_name, Group.id, Group.code, Group.name)
        .order_by(work_date_expr.desc(), Employee.code.asc())
        .all()
    )

    default_rule = _get_active_rule(db)
    exception_status_map = _build_exception_map(db, from_date, to_date, employee_id)
    group_ids = {int(row.group_id) for row in rows if row.group_id is not None}
    geofence_radius_map, group_max_radius_map = load_group_geofence_radius_maps(db, group_ids)

    # Plan B: payable OT = approved_minutes from OvertimeRecord (not raw OT).
    employee_ids_in_rows = list({int(row.employee_id) for row in rows if row.employee_id is not None})
    payable_ot_map = fetch_payable_minutes_map(
        db,
        employee_ids=employee_ids_in_rows or None,
        from_date=from_date,
        to_date=to_date,
    )

    response: list[AttendanceDailyReportResponse] = []
    for row in rows:
        checkin_status, checkout_status, attendance_state = _derive_daily_status(
            row.checkin_time,
            row.checkout_time,
            row.punctuality_rank,
            row.checkout_rank,
            row.checkout_raw_status,
        )
        shift_start = row.shift_start or default_rule.start_time
        shift_end = row.shift_end or default_rule.end_time
        regular_minutes, overtime_minutes, overtime_cross_day = split_regular_overtime_minutes(
            row.work_date,
            row.checkin_time,
            row.checkout_time,
            shift_start,
            shift_end,
        )

        exception_status, exception_type = exception_status_map.get((row.employee_id, row.work_date), (None, None))
        attendance_state = _apply_exception_to_attendance_state(
            attendance_state=attendance_state,
            exception_status=exception_status,
            exception_type=exception_type,
        )
        payable_overtime_minutes = payable_ot_map.get((int(row.employee_id), row.work_date), 0)

        matched_geofence = row.checkin_matched_geofence or row.checkout_matched_geofence
        geofence_source = _rank_to_geofence_source(row.geofence_source_rank)
        reference_radius_m = resolve_reference_radius_m(
            geofence_source=geofence_source,
            matched_geofence=matched_geofence,
            group_id=row.group_id,
            fallback_radius_m=default_rule.radius_m,
            named_radius_map=geofence_radius_map,
            max_radius_map=group_max_radius_map,
        )
        out_of_range = bool(row.out_of_range) if row.out_of_range is not None else False
        avg_distance_m = float(row.avg_distance_m) if row.avg_distance_m is not None else None
        max_distance_m = float(row.max_distance_m) if row.max_distance_m is not None else None
        distance_consistency_warning = compute_distance_consistency_warning(
            out_of_range=out_of_range,
            avg_distance_m=avg_distance_m,
            max_distance_m=max_distance_m,
            radius_m=reference_radius_m,
        )

        response.append(
            AttendanceDailyReportResponse(
                date=row.work_date,
                employee_code=row.employee_code,
                full_name=row.full_name,
                group_code=row.group_code,
                group_name=row.group_name,
                matched_geofence=matched_geofence,
                geofence_source=geofence_source,
                fallback_reason=row.fallback_reason,
                checkin_time=row.checkin_time,
                checkout_time=row.checkout_time,
                punctuality_status=_rank_to_punctuality(row.punctuality_rank),
                checkin_status=checkin_status,
                checkout_status=checkout_status,
                attendance_state=attendance_state,
                out_of_range=out_of_range,
                avg_distance_m=avg_distance_m,
                max_distance_m=max_distance_m,
                radius_m=reference_radius_m,
                distance_consistency_warning=distance_consistency_warning,
                regular_minutes=regular_minutes,
                overtime_minutes=overtime_minutes,
                payable_overtime_minutes=payable_overtime_minutes,
                overtime_cross_day=overtime_cross_day,
                exception_status=exception_status,
            )
        )

    return response


@router.get("/geofences")
def get_my_geofences(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return the effective geofence(s) for the current user — used by the home map."""
    emp = _find_employee_for_user(db, user)
    if emp is None:
        return []

    active_rule = db.query(CheckinRule).filter(CheckinRule.active.is_(True)).first()
    if active_rule is None:
        return []

    geofences, geofence_source, fallback_reason = _get_effective_geofences(db, emp, active_rule)
    return [
        {
            "name": g.name,
            "latitude": g.latitude,
            "longitude": g.longitude,
            "radius_m": g.radius_m,
            "source": geofence_source,
        }
        for g in geofences
    ]


@router.get("", response_model=list[AttendanceLogResponse])
def list_logs_admin(
    employee_id: int | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    q = db.query(AttendanceLog)

    if employee_id:
        q = q.filter(AttendanceLog.employee_id == employee_id)
    if from_date:
        q = q.filter(AttendanceLog.time >= from_date)
    if to_date:
        q = q.filter(AttendanceLog.time <= to_date)

    logs = q.order_by(AttendanceLog.time.desc()).all()
    return [_to_log_response(x) for x in logs]

