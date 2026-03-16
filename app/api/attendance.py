from datetime import date, datetime, time, timedelta, timezone
from typing import NamedTuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import AttendanceException, AttendanceLog, CheckinRule, Employee, Group, GroupGeofence, User
from app.schemas.attendance import (
    AttendanceDailyReportResponse,
    AttendanceLogResponse,
    AttendanceStatusResponse,
    CheckActionResponse,
    LocationRequest,
)
from app.services.attendance_time import VN_TZ, classify_checkin_status, classify_checkout_status, to_vn_time
from app.services.geo import haversine_m

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


class _DayLogsState(NamedTuple):
    day_start_utc: datetime
    day_end_utc: datetime
    work_date: date
    has_in: bool
    has_out: bool
    latest_today_log: AttendanceLog | None


def _find_employee_for_user(db: Session, user: User) -> Employee | None:
    return db.query(Employee).filter(Employee.user_id == user.id).first()


def _get_employee_for_user(db: Session, user: User) -> Employee:
    emp = _find_employee_for_user(db, user)
    if not emp:
        raise HTTPException(
            status_code=400,
            detail="User chua duoc gan Employee. Hay tao employee va set employees.user_id = user.id",
        )
    return emp


def _get_active_rule(db: Session) -> CheckinRule:
    rule = db.query(CheckinRule).filter(CheckinRule.active.is_(True)).first()
    if not rule:
        raise HTTPException(status_code=400, detail="Chua co rule active. Admin hay cau hinh /rules/active")
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
    if emp.group_id is not None:
        group = db.query(Group).filter(Group.id == emp.group_id, Group.active.is_(True)).first()
        if group:
            return _EffectiveTimeRule(
                start_time=group.start_time or fallback_rule.start_time,
                grace_minutes=(
                    group.grace_minutes
                    if group.grace_minutes is not None
                    else fallback_rule.grace_minutes
                ),
                end_time=group.end_time or fallback_rule.end_time,
                checkout_grace_minutes=(
                    group.checkout_grace_minutes
                    if group.checkout_grace_minutes is not None
                    else fallback_rule.checkout_grace_minutes
                ),
            )

    return _EffectiveTimeRule(
        start_time=fallback_rule.start_time,
        grace_minutes=fallback_rule.grace_minutes,
        end_time=fallback_rule.end_time,
        checkout_grace_minutes=fallback_rule.checkout_grace_minutes,
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


def _vn_work_date(value: datetime) -> date:
    return to_vn_time(value).date()



def _vn_day_bounds_utc(now_utc: datetime) -> tuple[datetime, datetime, date]:
    vn_now = to_vn_time(now_utc)
    day_start_vn = datetime.combine(vn_now.date(), time.min, tzinfo=VN_TZ)
    day_end_vn = day_start_vn + timedelta(days=1)
    return day_start_vn.astimezone(timezone.utc), day_end_vn.astimezone(timezone.utc), vn_now.date()


def _get_day_logs_state(db: Session, employee_id: int, now_utc: datetime) -> _DayLogsState:
    day_start_utc, day_end_utc, work_date = _vn_day_bounds_utc(now_utc)
    day_logs = (
        db.query(AttendanceLog)
        .filter(
            AttendanceLog.employee_id == employee_id,
            AttendanceLog.time >= day_start_utc,
            AttendanceLog.time < day_end_utc,
        )
        .order_by(AttendanceLog.time.desc())
        .all()
    )
    has_in = any(log.type == "IN" for log in day_logs)
    has_out = any(log.type == "OUT" for log in day_logs)
    latest_today_log = day_logs[0] if day_logs else None
    return _DayLogsState(
        day_start_utc=day_start_utc,
        day_end_utc=day_end_utc,
        work_date=work_date,
        has_in=has_in,
        has_out=has_out,
        latest_today_log=latest_today_log,
    )


def _find_open_in_before_day(db: Session, employee_id: int, day_start_utc: datetime) -> AttendanceLog | None:
    last_before_day = (
        db.query(AttendanceLog)
        .filter(
            AttendanceLog.employee_id == employee_id,
            AttendanceLog.time < day_start_utc,
        )
        .order_by(AttendanceLog.time.desc())
        .first()
    )
    if last_before_day and last_before_day.type == "IN":
        return last_before_day
    return None

def _is_cross_day_open_in(last: AttendanceLog, now_utc: datetime) -> bool:
    if last.type != "IN":
        return False
    return _vn_work_date(last.time) < _vn_work_date(now_utc)


def _format_vn_date(value: date) -> str:
    return value.strftime("%d/%m")


def _ensure_missed_checkout_exception(
    db: Session,
    emp: Employee,
    source_checkin_log: AttendanceLog,
) -> tuple[AttendanceException, bool]:
    existing = (
        db.query(AttendanceException)
        .filter(AttendanceException.source_checkin_log_id == source_checkin_log.id)
        .first()
    )
    if existing:
        return existing, False

    exception = AttendanceException(
        employee_id=emp.id,
        source_checkin_log_id=source_checkin_log.id,
        exception_type="MISSED_CHECKOUT",
        work_date=_vn_work_date(source_checkin_log.time),
        status="OPEN",
        note="Detected cross-day open check-in",
    )
    db.add(exception)
    db.flush()
    return exception, True


def _to_log_response(log: AttendanceLog) -> AttendanceLogResponse:
    return AttendanceLogResponse(
        id=log.id,
        type=log.type,
        time=log.time,
        lat=log.lat,
        lng=log.lng,
        distance_m=log.distance_m,
        nearest_distance_m=log.distance_m,
        matched_geofence=log.matched_geofence_name,
        geofence_source=log.geofence_source,
        fallback_reason=log.fallback_reason,
        is_out_of_range=log.is_out_of_range,
        punctuality_status=log.punctuality_status,
        checkout_status=log.checkout_status,
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
            message="Tài khoản chưa được gán employee",
        )

    now_utc = datetime.now(timezone.utc)
    day_state = _get_day_logs_state(db, emp.id, now_utc)

    if day_state.has_in and day_state.has_out:
        return AttendanceStatusResponse(
            employee_assigned=True,
            employee_id=emp.id,
            current_state="OUT",
            last_action=day_state.latest_today_log.type if day_state.latest_today_log else None,
            last_action_time=day_state.latest_today_log.time if day_state.latest_today_log else None,
            can_checkin=False,
            can_checkout=False,
            message="Bạn đã hoàn thành ca hôm nay.",
        )

    if day_state.has_in and not day_state.has_out:
        return AttendanceStatusResponse(
            employee_assigned=True,
            employee_id=emp.id,
            current_state="IN",
            last_action="IN",
            last_action_time=day_state.latest_today_log.time if day_state.latest_today_log else None,
            can_checkin=False,
            can_checkout=True,
            message="Bạn đang ở trạng thái check-in trong ca hôm nay.",
        )

    if not day_state.has_in and day_state.has_out:
        return AttendanceStatusResponse(
            employee_assigned=True,
            employee_id=emp.id,
            current_state="OUT",
            last_action="OUT",
            last_action_time=day_state.latest_today_log.time if day_state.latest_today_log else None,
            can_checkin=False,
            can_checkout=False,
            message="Hôm nay bạn đã checkout, không thể thao tác thêm.",
        )

    open_cross_day_in = _find_open_in_before_day(db, emp.id, day_state.day_start_utc)
    if open_cross_day_in:
        exception, _ = _ensure_missed_checkout_exception(db, emp, open_cross_day_in)
        db.commit()
        warning_date = exception.work_date
        return AttendanceStatusResponse(
            employee_assigned=True,
            employee_id=emp.id,
            current_state="OUT",
            last_action="IN",
            last_action_time=open_cross_day_in.time,
            can_checkin=True,
            can_checkout=False,
            message=f"Hôm trước quên checkout ({_format_vn_date(warning_date)}). Hôm nay bạn có thể check-in bình thường.",
            warning_code="MISSED_CHECKOUT",
            warning_date=warning_date,
        )

    last = _last_log(db, emp.id)
    return AttendanceStatusResponse(
        employee_assigned=True,
        employee_id=emp.id,
        current_state="OUT",
        last_action=last.type if last else None,
        last_action_time=last.time if last else None,
        can_checkin=True,
        can_checkout=False,
        message="Bạn chưa check-in trong ca hiện tại",
    )


@router.post("/checkin", response_model=CheckActionResponse)
def checkin(payload: LocationRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = _get_employee_for_user(db, user)
    active_rule = _get_active_rule(db)
    geofences, geofence_source, fallback_reason = _get_effective_geofences(db, emp, active_rule)
    using_fallback_rule = geofence_source == "SYSTEM_FALLBACK"
    time_rule = _get_effective_time_rule(db, emp, active_rule)

    checkin_at = datetime.now(timezone.utc)
    day_state = _get_day_logs_state(db, emp.id, checkin_at)

    if day_state.has_in and not day_state.has_out:
        raise HTTPException(status_code=400, detail="Hôm nay bạn đã CHECK-IN. Hãy CHECK-OUT trước.")
    if day_state.has_out:
        raise HTTPException(status_code=400, detail="Bạn đã hoàn thành ca hôm nay. Không thể CHECK-IN lại.")

    missed_checkout_warning_date: date | None = None
    open_cross_day_in = _find_open_in_before_day(db, emp.id, day_state.day_start_utc)
    if open_cross_day_in:
        exception, _ = _ensure_missed_checkout_exception(db, emp, open_cross_day_in)
        missed_checkout_warning_date = exception.work_date

    punctuality_status = classify_checkin_status(
        checkin_at,
        time_rule.start_time,
        time_rule.grace_minutes,
    )

    nearest_distance, out_of_range, nearest_radius, matched_geofence_name = _evaluate_range(
        payload.lat,
        payload.lng,
        geofences,
    )

    log = AttendanceLog(
        employee_id=emp.id,
        type="IN",
        time=checkin_at,
        lat=payload.lat,
        lng=payload.lng,
        distance_m=nearest_distance,
        is_out_of_range=out_of_range,
        punctuality_status=punctuality_status,
        matched_geofence_name=matched_geofence_name,
        geofence_source=geofence_source,
        fallback_reason=fallback_reason,
    )

    db.add(log)
    db.commit()
    db.refresh(log)

    msg = f"Check-in thành công ({punctuality_status})."
    if out_of_range:
        msg = (
            f"Cảnh báo: bạn đã ở ngoài vùng cho phép: ({nearest_distance:.1f}m > {nearest_radius}m). "
            f"Trạng thái giờ vào: {punctuality_status}."
        )
    elif matched_geofence_name:
        msg = f"Check-in thành công ({punctuality_status}) tại geofence: {matched_geofence_name}."
    elif using_fallback_rule:
        msg = f"Check-in thành công ({punctuality_status}) theo rule fallback hệ thống ({fallback_reason})."

    if missed_checkout_warning_date is not None:
        msg = f"{msg} Lưu ý: hôm trước bạn quên checkout ({_format_vn_date(missed_checkout_warning_date)}), hệ thống đã ghi nhận ngoại lệ."

    return CheckActionResponse(
        log=_to_log_response(log),
        message=msg,
        geofence_source=geofence_source,
        fallback_reason=fallback_reason,
    )


@router.post("/checkout", response_model=CheckActionResponse)
def checkout(payload: LocationRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = _get_employee_for_user(db, user)
    active_rule = _get_active_rule(db)
    geofences, geofence_source, fallback_reason = _get_effective_geofences(db, emp, active_rule)
    using_fallback_rule = geofence_source == "SYSTEM_FALLBACK"
    time_rule = _get_effective_time_rule(db, emp, active_rule)

    checkout_at = datetime.now(timezone.utc)
    day_state = _get_day_logs_state(db, emp.id, checkout_at)

    if day_state.has_out:
        raise HTTPException(status_code=400, detail="Hôm nay bạn đã CHECK-OUT rồi.")

    if not day_state.has_in:
        open_cross_day_in = _find_open_in_before_day(db, emp.id, day_state.day_start_utc)
        if open_cross_day_in:
            exception, _ = _ensure_missed_checkout_exception(db, emp, open_cross_day_in)
            db.commit()
            raise HTTPException(
                status_code=400,
                detail=f"Bạn đã quên checkout ngày {_format_vn_date(exception.work_date)}. Hãy check-in ca mới.",
            )
        raise HTTPException(status_code=400, detail="Hôm nay bạn chưa CHECK-IN.")

    checkout_status = classify_checkout_status(
        checkout_at,
        time_rule.end_time,
        time_rule.checkout_grace_minutes,
    )

    nearest_distance, out_of_range, nearest_radius, matched_geofence_name = _evaluate_range(
        payload.lat,
        payload.lng,
        geofences,
    )

    log = AttendanceLog(
        employee_id=emp.id,
        type="OUT",
        time=checkout_at,
        lat=payload.lat,
        lng=payload.lng,
        distance_m=nearest_distance,
        is_out_of_range=out_of_range,
        checkout_status=checkout_status,
        matched_geofence_name=matched_geofence_name,
        geofence_source=geofence_source,
        fallback_reason=fallback_reason,
    )

    db.add(log)
    db.commit()
    db.refresh(log)

    msg = f"Check-out thành công ({checkout_status})."
    if out_of_range:
        msg = (
            f"Cảnh báo: bạn đã ở ngoài vùng cho phép: ({nearest_distance:.1f}m > {nearest_radius}m). "
            f"Trạng thái giờ về: {checkout_status}."
        )
    elif matched_geofence_name:
        msg = f"Check-out thành công ({checkout_status}) tại geofence: {matched_geofence_name}."
    elif using_fallback_rule:
        msg = f"Check-out thành công ({checkout_status}) theo rule fallback hệ thống ({fallback_reason})."

    return CheckActionResponse(
        log=_to_log_response(log),
        message=msg,
        geofence_source=geofence_source,
        fallback_reason=fallback_reason,
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


@router.get("/report/daily", response_model=list[AttendanceDailyReportResponse])
def daily_report_admin(
    from_date: date | None = None,
    to_date: date | None = None,
    employee_id: int | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    work_date_expr = func.date(AttendanceLog.time)

    checkin_time_expr = func.min(
        case((AttendanceLog.type == "IN", AttendanceLog.time), else_=None)
    ).label("checkin_time")
    checkout_time_expr = func.max(
        case((AttendanceLog.type == "OUT", AttendanceLog.time), else_=None)
    ).label("checkout_time")

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

    q = (
        db.query(
            work_date_expr.label("work_date"),
            Employee.code.label("employee_code"),
            Employee.full_name.label("full_name"),
            Group.code.label("group_code"),
            Group.name.label("group_name"),
            checkin_time_expr,
            checkout_time_expr,
            punctuality_rank_expr,
            checkout_rank_expr,
            checkin_matched_geofence_expr,
            checkout_matched_geofence_expr,
            geofence_source_rank_expr,
            fallback_reason_expr,
            out_of_range_expr,
            avg_distance_expr,
            max_distance_expr,
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
        q.group_by(work_date_expr, Employee.code, Employee.full_name, Group.code, Group.name)
        .order_by(work_date_expr.desc(), Employee.code.asc())
        .all()
    )

    return [
        AttendanceDailyReportResponse(
            date=row.work_date,
            employee_code=row.employee_code,
            full_name=row.full_name,
            group_code=row.group_code,
            group_name=row.group_name,
            matched_geofence=row.checkin_matched_geofence or row.checkout_matched_geofence,
            geofence_source=_rank_to_geofence_source(row.geofence_source_rank),
            fallback_reason=row.fallback_reason,
            checkin_time=row.checkin_time,
            checkout_time=row.checkout_time,
            punctuality_status=_rank_to_punctuality(row.punctuality_rank),
            checkout_status=_rank_to_punctuality(row.checkout_rank),
            out_of_range=bool(row.out_of_range) if row.out_of_range is not None else False,
            avg_distance_m=float(row.avg_distance_m) if row.avg_distance_m is not None else None,
            max_distance_m=float(row.max_distance_m) if row.max_distance_m is not None else None,
        )
        for row in rows
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


