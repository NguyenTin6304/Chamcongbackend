from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.schemas.attendance import AttendanceExceptionReportResponse

from app.core.db import get_db
from app.core.deps import require_admin
from app.models import AttendanceException, AttendanceLog, Employee, Group

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


def _fetch_daily_report_rows(
    db: Session,
    from_date: date | None,
    to_date: date | None,
    employee_id: int | None,
    group_id: int | None,
):
    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect == "postgresql":
        # Group report by Vietnam calendar date regardless of server/database timezone.
        work_date_expr = func.date(func.timezone("Asia/Ho_Chi_Minh", AttendanceLog.time))
    else:
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
    if group_id:
        q = q.filter(Employee.group_id == group_id)
    if from_date:
        q = q.filter(work_date_expr >= from_date)
    if to_date:
        q = q.filter(work_date_expr <= to_date)

    return (
        q.group_by(work_date_expr, Employee.code, Employee.full_name, Group.code, Group.name)
        .order_by(work_date_expr.asc(), Employee.code.asc())
        .all()
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
        # Defensive: if DB driver returns naive datetime, treat it as UTC.
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
        "fallback_reason",
        "checkin_time",
        "checkout_time",
        "checkin_status",
        "checkout_status",
        "out_of_range",
        "avg_distance_m",
        "max_distance_m",
    ]
    ws.append(headers)

    for cell in ws[1]:
        cell.font = Font(bold=True)

    fill_ok = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")
    fill_warn = PatternFill(start_color="FFEBEE", end_color="FFEBEE", fill_type="solid")

    for row in rows:
        out_of_range_value = bool(row.out_of_range) if row.out_of_range is not None else False
        range_status_text = "OUT_OF_RANGE" if out_of_range_value else "IN_RANGE"
        checkin_status = _rank_to_punctuality(row.punctuality_rank)
        checkout_status = _rank_to_punctuality(row.checkout_rank)
        matched_geofence = row.checkin_matched_geofence or row.checkout_matched_geofence
        geofence_source = _rank_to_geofence_source(row.geofence_source_rank)

        ws.append(
            [
                _to_excel_date(row.work_date),
                row.employee_code,
                row.full_name,
                row.group_code,
                row.group_name,
                matched_geofence,
                geofence_source,
                row.fallback_reason,
                _to_excel_datetime(row.checkin_time),
                _to_excel_datetime(row.checkout_time),
                checkin_status,
                checkout_status,
                range_status_text,
                float(row.avg_distance_m) if row.avg_distance_m is not None else None,
                float(row.max_distance_m) if row.max_distance_m is not None else None,
            ]
        )

        current_row_idx = ws.max_row
        range_cell = ws.cell(row=current_row_idx, column=13)
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
            AttendanceException.created_at.label("created_at"),
            AttendanceException.resolved_at.label("resolved_at"),
        )
        .join(Employee, Employee.id == AttendanceException.employee_id)
        .outerjoin(Group, Group.id == Employee.group_id)
        .outerjoin(AttendanceLog, AttendanceLog.id == AttendanceException.source_checkin_log_id)
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
            created_at=row.created_at,
            resolved_at=row.resolved_at,
        )
        for row in rows
    ]
