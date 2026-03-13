from datetime import date, datetime, time, timezone
from typing import NamedTuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import AttendanceLog, CheckinRule, Employee, Group, GroupGeofence, User
from app.schemas.attendance import (
    AttendanceDailyReportResponse,
    AttendanceLogResponse,
    AttendanceStatusResponse,
    CheckActionResponse,
    LocationRequest,
)
from app.services.attendance_time import classify_checkin_status, classify_checkout_status
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

    last = _last_log(db, emp.id)
    if not last:
        return AttendanceStatusResponse(
            employee_assigned=True,
            employee_id=emp.id,
            current_state="OUT",
            last_action=None,
            last_action_time=None,
            can_checkin=True,
            can_checkout=False,
            message="Bạn chưa check-in trong ca hiện tại",
        )

    if last.type == "IN":
        return AttendanceStatusResponse(
            employee_assigned=True,
            employee_id=emp.id,
            current_state="IN",
            last_action="IN",
            last_action_time=last.time,
            can_checkin=False,
            can_checkout=True,
            message="Bạn đang ở trạng thái check-in",
        )

    return AttendanceStatusResponse(
        employee_assigned=True,
        employee_id=emp.id,
        current_state="OUT",
        last_action="OUT",
        last_action_time=last.time,
        can_checkin=True,
        can_checkout=False,
        message="Bạn đang ở trạng thái checkout",
    )


@router.post("/checkin", response_model=CheckActionResponse)
def checkin(payload: LocationRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = _get_employee_for_user(db, user)
    active_rule = _get_active_rule(db)
    geofences, geofence_source, fallback_reason = _get_effective_geofences(db, emp, active_rule)
    using_fallback_rule = geofence_source == "SYSTEM_FALLBACK"
    time_rule = _get_effective_time_rule(db, emp, active_rule)

    last = _last_log(db, emp.id)
    if last and last.type == "IN":
        raise HTTPException(status_code=400, detail="Bạn đã CHECK-IN rồi. Hãy CHECK-OUT trước.")

    checkin_at = datetime.now(timezone.utc)
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

    last = _last_log(db, emp.id)
    if not last or last.type != "IN":
        raise HTTPException(status_code=400, detail="Bạn chưa CHECK-IN hoặc đã CHECK-OUT rồi.")

    checkout_at = datetime.now(timezone.utc)
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






