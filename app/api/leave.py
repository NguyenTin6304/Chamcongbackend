import threading
from datetime import date, timedelta, timezone, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.services.attendance_time import VN_TZ
from app.models import Employee, LeaveRequest, User
from app.schemas.leave import (
    AdminLeaveRequestCreate,
    LeaveBalanceResponse,
    LeaveRequestApproveRequest,
    LeaveRequestCreate,
    LeaveRequestRejectRequest,
    LeaveRequestResponse,
)

router = APIRouter(prefix="/leave-requests", tags=["leave"])

_PAST_GRACE_DAYS = 3

# ---------------------------------------------------------------------------
# FCM helpers
# ---------------------------------------------------------------------------

_LEAVE_FCM: dict[str, tuple[str, str]] = {
    "approved": ("Đơn nghỉ phép đã được duyệt ✅", "Đơn nghỉ phép của bạn đã được phê duyệt."),
    "rejected": ("Đơn nghỉ phép bị từ chối ❌", "Đơn nghỉ phép của bạn bị từ chối. Vui lòng xem chi tiết."),
    "admin_created": ("Đơn nghỉ phép đã được tạo", "Quản trị viên đã tạo đơn nghỉ phép cho bạn."),
}


def _push_leave_fcm(fcm_token: str, event: str) -> None:
    try:
        from app.services.fcm_service import send_push_notification
        title, body = _LEAVE_FCM.get(event, ("Thông báo nghỉ phép", ""))
        send_push_notification(fcm_token, title, body, data={"route": "/home/leaves"})
    except Exception as fcm_exc:
        # Daemon threads swallow stack traces by default — log explicitly so a
        # misconfigured FCM key doesn't go unnoticed in production.
        import logging
        logging.getLogger(__name__).warning(
            "Leave FCM push failed (event=%s): %s", event, fcm_exc,
        )


def _fire_leave_fcm(employee: Employee, db: Session, event: str) -> None:
    if not employee.user_id:
        return
    user = db.query(User).filter(User.id == employee.user_id).first()
    if user and user.fcm_token and user.fcm_token.strip():
        token = user.fcm_token.strip()
        threading.Thread(target=_push_leave_fcm, args=(token, event), daemon=True).start()  # NV được phép xin nghỉ tối đa 3 ngày trước ngày hôm nay


def _to_response(req: LeaveRequest, employee: Employee) -> LeaveRequestResponse:
    return LeaveRequestResponse(
        id=req.id,
        employee_id=req.employee_id,
        employee_name=employee.full_name,
        employee_code=employee.code,
        leave_type=req.leave_type,
        start_date=req.start_date,
        end_date=req.end_date,
        reason=req.reason,
        status=req.status,
        admin_note=req.admin_note,
        created_at=req.created_at,
    )


def _get_employee_for_user(user: User, db: Session) -> Employee:
    emp = db.query(Employee).filter(
        Employee.user_id == user.id,
        Employee.deleted_at.is_(None),
    ).first()
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee profile not found for this account")
    return emp


def _check_overlap(db: Session, employee_id: int, start_date: date, end_date: date, exclude_id: int | None = None):
    q = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status.in_(["PENDING", "APPROVED"]),
        LeaveRequest.start_date <= end_date,
        LeaveRequest.end_date >= start_date,
    )
    if exclude_id is not None:
        q = q.filter(LeaveRequest.id != exclude_id)
    conflict = q.first()
    if conflict is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Bạn đã có đơn nghỉ phép trùng ngày ({conflict.start_date} → {conflict.end_date}, trạng thái: {conflict.status})",
        )


# ---------------------------------------------------------------------------
# Balance helpers
# ---------------------------------------------------------------------------

def compute_leave_balance(emp: Employee, db: Session, year: int) -> LeaveBalanceResponse:
    """Compute annual leave balance for `emp` in `year`.

    Public so other modules (e.g. /attendance/me/stats) can reuse the same
    semantics — `days_used` and `days_remaining` must agree across endpoints.
    """
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)

    def _day_count_in_year(req: LeaveRequest) -> float:
        effective_start = max(req.start_date, year_start)
        effective_end = min(req.end_date, year_end)
        return float((effective_end - effective_start).days + 1)

    approved = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == emp.id,
        LeaveRequest.leave_type == 'PAID',
        LeaveRequest.status == 'APPROVED',
        LeaveRequest.start_date <= year_end,
        LeaveRequest.end_date >= year_start,
    ).all()
    days_used = sum(_day_count_in_year(r) for r in approved)

    pending = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == emp.id,
        LeaveRequest.leave_type == 'PAID',
        LeaveRequest.status == 'PENDING',
        LeaveRequest.start_date <= year_end,
        LeaveRequest.end_date >= year_start,
    ).all()
    days_pending = sum(_day_count_in_year(r) for r in pending)

    quota = emp.annual_leave_days
    remaining = None if quota is None else max(0.0, quota - days_used)

    return LeaveBalanceResponse(
        annual_quota=quota,
        days_used=days_used,
        days_remaining=remaining,
        days_pending=days_pending,
    )


# ---------------------------------------------------------------------------
# Employee endpoints
# ---------------------------------------------------------------------------

@router.get("/me/balance", response_model=LeaveBalanceResponse)
def get_my_leave_balance(
    year: int | None = Query(default=None, ge=2000, le=2100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = _get_employee_for_user(current_user, db)
    target_year = year or datetime.now(VN_TZ).year
    return compute_leave_balance(emp, db, target_year)


@router.post("", response_model=LeaveRequestResponse, status_code=201)
def create_leave_request(
    payload: LeaveRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = _get_employee_for_user(current_user, db)

    today = datetime.now(VN_TZ).date()
    cutoff = today - timedelta(days=_PAST_GRACE_DAYS)
    if payload.start_date < cutoff:
        raise HTTPException(
            status_code=422,
            detail=f"Không thể xin nghỉ cho ngày quá {_PAST_GRACE_DAYS} ngày trong quá khứ (từ {cutoff} trở đi)",
        )

    _check_overlap(db, emp.id, payload.start_date, payload.end_date)

    req = LeaveRequest(
        employee_id=emp.id,
        leave_type=payload.leave_type,
        start_date=payload.start_date,
        end_date=payload.end_date,
        reason=payload.reason,
        status="PENDING",
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return _to_response(req, emp)


@router.get("/me", response_model=list[LeaveRequestResponse])
def get_my_leave_requests(
    year: int | None = Query(default=None, ge=2000, le=2100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = _get_employee_for_user(current_user, db)

    q = db.query(LeaveRequest).filter(LeaveRequest.employee_id == emp.id)
    if year is not None:
        q = q.filter(
            LeaveRequest.start_date <= date(year, 12, 31),
            LeaveRequest.end_date >= date(year, 1, 1),
        )
    rows = q.order_by(LeaveRequest.start_date.desc()).all()
    return [_to_response(r, emp) for r in rows]


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@router.get("/balance/{employee_id}", response_model=LeaveBalanceResponse)
def get_employee_leave_balance(
    employee_id: int,
    year: int | None = Query(default=None, ge=2000, le=2100),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    emp = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.deleted_at.is_(None),
    ).first()
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    target_year = year or datetime.now(VN_TZ).year
    return compute_leave_balance(emp, db, target_year)


@router.get("", response_model=list[LeaveRequestResponse])
def list_leave_requests(
    status: str | None = Query(default=None),
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = Query(default=None, ge=2000, le=2100),
    employee_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    q = db.query(LeaveRequest, Employee).join(
        Employee, LeaveRequest.employee_id == Employee.id
    )

    if status:
        q = q.filter(LeaveRequest.status == status.upper())
    if employee_id:
        q = q.filter(LeaveRequest.employee_id == employee_id)
    if year and month:
        month_start = date(year, month, 1)
        if month == 12:
            month_end = date(year, 12, 31)
        else:
            month_end = date(year, month + 1, 1) - timedelta(days=1)
        q = q.filter(
            LeaveRequest.start_date <= month_end,
            LeaveRequest.end_date >= month_start,
        )
    elif year:
        q = q.filter(
            LeaveRequest.start_date <= date(year, 12, 31),
            LeaveRequest.end_date >= date(year, 1, 1),
        )

    rows = q.order_by(LeaveRequest.start_date.desc()).all()
    return [_to_response(req, emp) for req, emp in rows]


@router.post("/admin", response_model=LeaveRequestResponse, status_code=201)
def admin_create_leave_request(
    payload: AdminLeaveRequestCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    emp = db.query(Employee).filter(
        Employee.id == payload.employee_id,
        Employee.deleted_at.is_(None),
    ).first()
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    _check_overlap(db, emp.id, payload.start_date, payload.end_date)

    req = LeaveRequest(
        employee_id=emp.id,
        leave_type=payload.leave_type,
        start_date=payload.start_date,
        end_date=payload.end_date,
        reason=payload.reason,
        status=payload.status,
        admin_note="Tạo bởi quản trị viên" if payload.status == "APPROVED" else None,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    if payload.status == "APPROVED":
        _fire_leave_fcm(emp, db, "admin_created")
    return _to_response(req, emp)


@router.patch("/{leave_id}/approve", response_model=LeaveRequestResponse)
def approve_leave_request(
    leave_id: int,
    payload: LeaveRequestApproveRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    req = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).first()
    if req is None:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if req.status != "PENDING":
        raise HTTPException(status_code=409, detail=f"Cannot approve a request with status {req.status}")

    req.status = "APPROVED"
    req.admin_note = payload.admin_note
    req.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(req)

    emp = db.query(Employee).filter(Employee.id == req.employee_id).first()
    _fire_leave_fcm(emp, db, "approved")
    return _to_response(req, emp)


@router.patch("/{leave_id}/reject", response_model=LeaveRequestResponse)
def reject_leave_request(
    leave_id: int,
    payload: LeaveRequestRejectRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    req = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).first()
    if req is None:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if req.status != "PENDING":
        raise HTTPException(status_code=409, detail=f"Cannot reject a request with status {req.status}")

    req.status = "REJECTED"
    req.admin_note = payload.admin_note
    req.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(req)

    emp = db.query(Employee).filter(Employee.id == req.employee_id).first()
    _fire_leave_fcm(emp, db, "rejected")
    return _to_response(req, emp)
