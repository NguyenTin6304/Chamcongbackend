from datetime import date, timedelta, timezone, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.services.attendance_time import VN_TZ
from app.models import Employee, LeaveRequest, User
from app.schemas.leave import (
    LeaveRequestApproveRequest,
    LeaveRequestCreate,
    LeaveRequestRejectRequest,
    LeaveRequestResponse,
)

router = APIRouter(prefix="/leave-requests", tags=["leave"])

_PAST_GRACE_DAYS = 3  # NV được phép xin nghỉ tối đa 3 ngày trước ngày hôm nay


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
# Employee endpoints
# ---------------------------------------------------------------------------

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
    return _to_response(req, emp)
