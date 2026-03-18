from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import case, func
from sqlalchemy.orm import Session, aliased

from app.core.db import get_db
from app.core.deps import require_admin
from app.models import AttendanceException, AttendanceLog, Employee, Group, User
from app.schemas.attendance import AttendanceExceptionReopenRequest, AttendanceExceptionReportResponse, AttendanceExceptionResolveRequest
from app.services.attendance_time import (
    DEFAULT_CROSS_DAY_CUTOFF_MINUTES,
    classify_checkout_status,
    normalize_utc,
    split_regular_overtime_minutes,
    work_date_cutoff_utc,
)

router = APIRouter(prefix="/reports", tags=["reports"])
VN_TZ = timezone(timedelta(hours=7))
UTC_TZ = timezone.utc


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


def _work_date_expr(db: Session):
    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect == "postgresql":
        legacy_expr = func.date(func.timezone("Asia/Ho_Chi_Minh", AttendanceLog.time))
    else:
        legacy_expr = func.date(AttendanceLog.time)
    return func.coalesce(AttendanceLog.work_date, legacy_expr)


def _fetch_daily_report_rows(
    db: Session,
    from_date: date | None,
    to_date: date | None,
    employee_id: int | None,
    group_id: int | None,
):
    work_date_expr = _work_date_expr(db)

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
    if group_id:
        q = q.filter(Employee.group_id == group_id)
    if from_date:
        q = q.filter(work_date_expr >= from_date)
    if to_date:
        q = q.filter(work_date_expr <= to_date)

    return (
        q.group_by(work_date_expr, Employee.id, Employee.code, Employee.full_name, Group.code, Group.name)
        .order_by(work_date_expr.asc(), Employee.code.asc())
        .all()
    )


def _build_exception_map(
    db: Session,
    from_date: date | None,
    to_date: date | None,
    employee_id: int | None,
    group_id: int | None,
) -> dict[tuple[int, date], tuple[str, str]]:
    q = db.query(
        AttendanceException.employee_id,
        AttendanceException.work_date,
        AttendanceException.status,
        AttendanceException.exception_type,
    ).join(Employee, Employee.id == AttendanceException.employee_id)

    if employee_id:
        q = q.filter(AttendanceException.employee_id == employee_id)
    if group_id:
        q = q.filter(Employee.group_id == group_id)
    if from_date:
        q = q.filter(AttendanceException.work_date >= from_date)
    if to_date:
        q = q.filter(AttendanceException.work_date <= to_date)

    status_map: dict[tuple[int, date], tuple[str, str]] = {}
    for row in q.all():
        key = (row.employee_id, row.work_date)
        current = status_map.get(key)
        if current is None or current[0] != "OPEN":
            status_map[key] = (row.status, row.exception_type)
    return status_map


def _apply_exception_to_attendance_state(
    attendance_state: str,
    exception_status: str | None,
    exception_type: str | None,
) -> str:
    if exception_status == "OPEN" and exception_type in {"AUTO_CLOSED", "MISSED_CHECKOUT"}:
        return "PENDING_TIMESHEET"
    return attendance_state



def _compute_payable_overtime_minutes(
    overtime_minutes: int | None,
    exception_status: str | None,
    exception_type: str | None,
) -> int | None:
    if overtime_minutes is None:
        return None
    if exception_status == "OPEN" and exception_type in {"AUTO_CLOSED", "MISSED_CHECKOUT"}:
        return 0
    return overtime_minutes


def _find_checkout_log_for_exception(db: Session, exception: AttendanceException, source_checkin: AttendanceLog) -> AttendanceLog | None:
    return (
        db.query(AttendanceLog)
        .filter(
            AttendanceLog.employee_id == exception.employee_id,
            AttendanceLog.work_date == exception.work_date,
            AttendanceLog.type == "OUT",
            AttendanceLog.time >= source_checkin.time,
        )
        .order_by(AttendanceLog.time.asc())
        .first()
    )


def _upsert_checkout_log_from_resolution(
    db: Session,
    exception: AttendanceException,
    source_checkin: AttendanceLog,
    actual_checkout_utc: datetime,
) -> AttendanceLog:
    checkout_log = _find_checkout_log_for_exception(db, exception, source_checkin)

    shift_end = source_checkin.snapshot_end_time or time(17, 0)
    checkout_grace = (
        source_checkin.snapshot_checkout_grace_minutes
        if source_checkin.snapshot_checkout_grace_minutes is not None
        else 0
    )
    checkout_status = classify_checkout_status(
        actual_checkout_utc,
        shift_end,
        checkout_grace,
        work_date=exception.work_date,
    )

    if checkout_log is None:
        checkout_log = AttendanceLog(
            employee_id=exception.employee_id,
            type="OUT",
            time=actual_checkout_utc,
            work_date=exception.work_date,
            lat=source_checkin.lat,
            lng=source_checkin.lng,
            distance_m=source_checkin.distance_m,
            is_out_of_range=source_checkin.is_out_of_range,
            checkout_status=checkout_status,
            matched_geofence_name=source_checkin.matched_geofence_name,
            geofence_source=source_checkin.geofence_source,
            fallback_reason=source_checkin.fallback_reason,
            snapshot_start_time=source_checkin.snapshot_start_time,
            snapshot_end_time=source_checkin.snapshot_end_time,
            snapshot_grace_minutes=source_checkin.snapshot_grace_minutes,
            snapshot_checkout_grace_minutes=source_checkin.snapshot_checkout_grace_minutes,
            snapshot_cutoff_minutes=source_checkin.snapshot_cutoff_minutes,
            time_rule_source=source_checkin.time_rule_source,
            time_rule_fallback_reason=source_checkin.time_rule_fallback_reason,
            address_text="RESOLVED_BY_ADMIN",
        )
        db.add(checkout_log)
    else:
        checkout_log.time = actual_checkout_utc
        checkout_log.checkout_status = checkout_status
        checkout_log.address_text = "RESOLVED_BY_ADMIN"

    return checkout_log


def _revert_checkout_log_for_reopen(
    db: Session,
    exception: AttendanceException,
    source_checkin: AttendanceLog,
) -> None:
    checkout_log = _find_checkout_log_for_exception(db, exception, source_checkin)
    if checkout_log is None:
        return

    if exception.exception_type == "AUTO_CLOSED":
        cutoff_minutes = source_checkin.snapshot_cutoff_minutes or DEFAULT_CROSS_DAY_CUTOFF_MINUTES
        checkout_log.time = work_date_cutoff_utc(exception.work_date, cutoff_minutes)
        checkout_log.checkout_status = "SYSTEM_AUTO"
        checkout_log.address_text = "AUTO_CLOSED_AT_CUTOFF"
        return

    if exception.exception_type == "MISSED_CHECKOUT" and checkout_log.address_text == "RESOLVED_BY_ADMIN":
        db.delete(checkout_log)


def _build_exception_response(db: Session, exception_id: int) -> AttendanceExceptionReportResponse:
    resolver = aliased(User)
    row = (
        db.query(
            AttendanceException.id.label("id"),
            AttendanceException.employee_id.label("employee_id"),
            Employee.code.label("employee_code"),
            Employee.full_name.label("full_name"),
            Group.code.label("group_code"),
            Group.name.label("group_name"),
            AttendanceException.work_date.label("work_date"),
            AttendanceException.exception_type.label("exception_type"),
            AttendanceException.status.label("status"),
            AttendanceException.note.label("note"),
            AttendanceException.source_checkin_log_id.label("source_checkin_log_id"),
            AttendanceLog.time.label("source_checkin_time"),
            AttendanceException.actual_checkout_time.label("actual_checkout_time"),
            AttendanceException.created_at.label("created_at"),
            AttendanceException.resolved_at.label("resolved_at"),
            AttendanceException.resolved_by.label("resolved_by"),
            resolver.email.label("resolved_by_email"),
        )
        .join(Employee, Employee.id == AttendanceException.employee_id)
        .outerjoin(Group, Group.id == Employee.group_id)
        .outerjoin(AttendanceLog, AttendanceLog.id == AttendanceException.source_checkin_log_id)
        .outerjoin(resolver, resolver.id == AttendanceException.resolved_by)
        .filter(AttendanceException.id == exception_id)
        .first()
    )

    if row is None:
        raise HTTPException(status_code=404, detail="attendance_exception not found")

    return AttendanceExceptionReportResponse(
        id=row.id,
        employee_id=row.employee_id,
        employee_code=row.employee_code,
        full_name=row.full_name,
        group_code=row.group_code,
        group_name=row.group_name,
        work_date=row.work_date,
        exception_type=row.exception_type,
        status=row.status,
        note=row.note,
        source_checkin_log_id=row.source_checkin_log_id,
        source_checkin_time=row.source_checkin_time,
        actual_checkout_time=row.actual_checkout_time,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        resolved_by=row.resolved_by,
        resolved_by_email=row.resolved_by_email,
    )

def _to_excel_date(value: date | datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _to_excel_datetime(value: datetime | str | None) -> str | None:
    if value is None:
        return None

    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        value = parsed

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC_TZ)

    return value.astimezone(VN_TZ).isoformat(sep=" ", timespec="seconds")


@router.get("/attendance.xlsx")
def export_attendance_report_excel(
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    employee_id: int | None = None,
    group_id: int | None = None,
    include_empty: bool = False,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    if from_date and to_date and from_date > to_date:
        raise HTTPException(status_code=400, detail="'from' must be <= 'to'")

    if employee_id is not None:
        emp = db.query(Employee).filter(Employee.id == employee_id).first()
        if not emp:
            raise HTTPException(status_code=404, detail="employee_id not found")

    if group_id is not None:
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="group_id not found")

    rows = _fetch_daily_report_rows(db, from_date, to_date, employee_id, group_id)
    if not rows and not include_empty:
        raise HTTPException(status_code=404, detail="No attendance data for selected filters")

    exception_status_map = _build_exception_map(db, from_date, to_date, employee_id, group_id)

    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"

    headers = [
        "date",
        "employee_code",
        "full_name",
        "group_code",
        "group_name",
        "matched_geofence",
        "geofence_source",
        "checkin_time",
        "checkout_time",
        "checkin_status",
        "checkout_status",
        "attendance_state",
        "out_of_range",
        "avg_distance_m",
        "max_distance_m",
        "regular_minutes",
        "overtime_minutes",
        "payable_overtime_minutes",
        "overtime_cross_day",
        "exception_status",
    ]
    ws.append(headers)

    for cell in ws[1]:
        cell.font = Font(bold=True)

    fill_ok = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")
    fill_warn = PatternFill(start_color="FFEBEE", end_color="FFEBEE", fill_type="solid")

    for row in rows:
        out_of_range_value = bool(row.out_of_range) if row.out_of_range is not None else False
        range_status_text = "OUT_OF_RANGE" if out_of_range_value else "IN_RANGE"
        checkin_status, checkout_status, attendance_state = _derive_daily_status(
            row.checkin_time,
            row.checkout_time,
            row.punctuality_rank,
            row.checkout_rank,
            row.checkout_raw_status,
        )
        matched_geofence = row.checkin_matched_geofence or row.checkout_matched_geofence
        geofence_source = _rank_to_geofence_source(row.geofence_source_rank)

        shift_start = row.shift_start or time(8, 0)
        shift_end = row.shift_end or time(17, 0)
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
        payable_overtime_minutes = _compute_payable_overtime_minutes(
            overtime_minutes=overtime_minutes,
            exception_status=exception_status,
            exception_type=exception_type,
        )

        ws.append(
            [
                _to_excel_date(row.work_date),
                row.employee_code,
                row.full_name,
                row.group_code,
                row.group_name,
                matched_geofence,
                geofence_source,
                _to_excel_datetime(row.checkin_time),
                _to_excel_datetime(row.checkout_time),
                checkin_status,
                checkout_status,
                attendance_state,
                range_status_text,
                float(row.avg_distance_m) if row.avg_distance_m is not None else None,
                float(row.max_distance_m) if row.max_distance_m is not None else None,
                regular_minutes,
                overtime_minutes,
                payable_overtime_minutes,
                "YES" if overtime_cross_day else "NO",
                exception_status,
            ]
        )

        current_row_idx = ws.max_row
        out_of_range_col = headers.index("out_of_range") + 1
        range_cell = ws.cell(row=current_row_idx, column=out_of_range_col)
        range_cell.fill = fill_warn if out_of_range_value else fill_ok

    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"

    for col_idx, _ in enumerate(headers, start=1):
        col_letter = get_column_letter(col_idx)
        max_len = 0
        for cell in ws[col_letter]:
            value = "" if cell.value is None else str(cell.value)
            if len(value) > max_len:
                max_len = len(value)
        ws.column_dimensions[col_letter].width = min(max_len + 2, 50)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = "attendance_report.xlsx"
    if group_id is not None:
        filename = f"attendance_report_group_{group_id}.xlsx"
    if from_date or to_date:
        filename = (
            f"attendance_report_{from_date or 'all'}_{to_date or 'all'}"
            + (f"_group_{group_id}" if group_id is not None else "")
            + ".xlsx"
        )

    headers_response = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers_response,
    )


@router.get("/attendance-exceptions", response_model=list[AttendanceExceptionReportResponse])
def list_attendance_exceptions(
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    employee_id: int | None = None,
    group_id: int | None = None,
    exception_type: str = "MISSED_CHECKOUT",
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    if from_date and to_date and from_date > to_date:
        raise HTTPException(status_code=400, detail="'from' must be <= 'to'")

    normalized_exception_type = exception_type.strip().upper()
    if normalized_exception_type not in {"MISSED_CHECKOUT", "AUTO_CLOSED"}:
        raise HTTPException(status_code=400, detail="exception_type must be MISSED_CHECKOUT or AUTO_CLOSED")

    normalized_status = status_filter.strip().upper() if status_filter else None
    if normalized_status and normalized_status not in {"OPEN", "RESOLVED"}:
        raise HTTPException(status_code=400, detail="status must be OPEN or RESOLVED")

    resolver = aliased(User)

    q = (
        db.query(
            AttendanceException.id.label("id"),
            AttendanceException.employee_id.label("employee_id"),
            Employee.code.label("employee_code"),
            Employee.full_name.label("full_name"),
            Group.code.label("group_code"),
            Group.name.label("group_name"),
            AttendanceException.work_date.label("work_date"),
            AttendanceException.exception_type.label("exception_type"),
            AttendanceException.status.label("status"),
            AttendanceException.note.label("note"),
            AttendanceException.source_checkin_log_id.label("source_checkin_log_id"),
            AttendanceLog.time.label("source_checkin_time"),
            AttendanceException.actual_checkout_time.label("actual_checkout_time"),
            AttendanceException.created_at.label("created_at"),
            AttendanceException.resolved_at.label("resolved_at"),
            AttendanceException.resolved_by.label("resolved_by"),
            resolver.email.label("resolved_by_email"),
        )
        .join(Employee, Employee.id == AttendanceException.employee_id)
        .outerjoin(Group, Group.id == Employee.group_id)
        .outerjoin(AttendanceLog, AttendanceLog.id == AttendanceException.source_checkin_log_id)
        .outerjoin(resolver, resolver.id == AttendanceException.resolved_by)
        .filter(AttendanceException.exception_type == normalized_exception_type)
    )

    if employee_id:
        q = q.filter(AttendanceException.employee_id == employee_id)
    if group_id:
        q = q.filter(Employee.group_id == group_id)
    if from_date:
        q = q.filter(AttendanceException.work_date >= from_date)
    if to_date:
        q = q.filter(AttendanceException.work_date <= to_date)
    if normalized_status:
        q = q.filter(AttendanceException.status == normalized_status)

    rows = q.order_by(AttendanceException.work_date.desc(), AttendanceException.created_at.desc()).all()

    return [
        AttendanceExceptionReportResponse(
            id=row.id,
            employee_id=row.employee_id,
            employee_code=row.employee_code,
            full_name=row.full_name,
            group_code=row.group_code,
            group_name=row.group_name,
            work_date=row.work_date,
            exception_type=row.exception_type,
            status=row.status,
            note=row.note,
            source_checkin_log_id=row.source_checkin_log_id,
            source_checkin_time=row.source_checkin_time,
            actual_checkout_time=row.actual_checkout_time,
            created_at=row.created_at,
            resolved_at=row.resolved_at,
            resolved_by=row.resolved_by,
            resolved_by_email=row.resolved_by_email,
        )
        for row in rows
    ]


@router.patch("/attendance-exceptions/{exception_id}/resolve", response_model=AttendanceExceptionReportResponse)
def resolve_attendance_exception(
    exception_id: int,
    payload: AttendanceExceptionResolveRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    exception = db.query(AttendanceException).filter(AttendanceException.id == exception_id).first()
    if exception is None:
        raise HTTPException(status_code=404, detail="attendance_exception not found")

    source_checkin = db.query(AttendanceLog).filter(AttendanceLog.id == exception.source_checkin_log_id).first()
    if source_checkin is None:
        raise HTTPException(status_code=400, detail="source check-in log not found")

    existing_checkout_log = _find_checkout_log_for_exception(db, exception, source_checkin)
    actual_checkout_utc: datetime | None = None
    if payload.actual_checkout_time is not None:
        actual_checkout_utc = normalize_utc(payload.actual_checkout_time)
    elif exception.actual_checkout_time is not None:
        actual_checkout_utc = normalize_utc(exception.actual_checkout_time)

    if actual_checkout_utc is not None and actual_checkout_utc <= normalize_utc(source_checkin.time):
        raise HTTPException(status_code=400, detail="actual_checkout_time must be later than source check-in time")

    if actual_checkout_utc is None and existing_checkout_log is None:
        raise HTTPException(status_code=400, detail="actual_checkout_time is required when no checkout log exists")

    if actual_checkout_utc is not None:
        _upsert_checkout_log_from_resolution(db, exception, source_checkin, actual_checkout_utc)

    exception.status = "RESOLVED"
    exception.resolved_by = admin_user.id
    exception.resolved_at = datetime.now(timezone.utc)
    if payload.note is not None:
        exception.note = payload.note
    if actual_checkout_utc is not None:
        exception.actual_checkout_time = actual_checkout_utc

    db.commit()
    return _build_exception_response(db, exception_id)


@router.patch("/attendance-exceptions/{exception_id}/reopen", response_model=AttendanceExceptionReportResponse)
def reopen_attendance_exception(
    exception_id: int,
    payload: AttendanceExceptionReopenRequest,
    db: Session = Depends(get_db),
    _admin_user: User = Depends(require_admin),
):
    exception = db.query(AttendanceException).filter(AttendanceException.id == exception_id).first()
    if exception is None:
        raise HTTPException(status_code=404, detail="attendance_exception not found")

    source_checkin = db.query(AttendanceLog).filter(AttendanceLog.id == exception.source_checkin_log_id).first()
    if source_checkin is None:
        raise HTTPException(status_code=400, detail="source check-in log not found")

    _revert_checkout_log_for_reopen(db, exception, source_checkin)

    exception.status = "OPEN"
    exception.resolved_by = None
    exception.resolved_at = None
    exception.actual_checkout_time = None
    if payload.note is not None:
        exception.note = payload.note

    db.commit()
    return _build_exception_response(db, exception_id)
