"""Overtime workflow API — Phase 2.5.

Admin-facing endpoints to list, approve, reject, edit, or bulk-approve OT records.
Employee-facing endpoint to inspect own OT for a given month.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import Employee, Group, OvertimeAudit, OvertimeRecord, User
from app.schemas.overtime import (
    MyOvertimeListItem,
    MyOvertimeMonthSummary,
    OvertimeApproveRequest,
    OvertimeAuditItem,
    OvertimeBulkApproveRequest,
    OvertimeBulkApproveResponse,
    OvertimeDetailResponse,
    OvertimeEditRequest,
    OvertimeListItem,
    OvertimeRejectRequest,
)
from app.services import overtime_service

router = APIRouter(prefix="/overtime", tags=["overtime"])


# ── Helpers ──────────────────────────────────────────────────────────────────
def _parse_month(value: str | None) -> tuple[date, date] | None:
    if not value:
        return None
    try:
        year_str, month_str = value.split("-", 1)
        year, month = int(year_str), int(month_str)
        if not (2000 <= year <= 2100) or not (1 <= month <= 12):
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail="month must be in 'YYYY-MM' format (year 2000-2100)")
    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _to_list_item(record: OvertimeRecord, employee: Employee | None, group_name: str | None) -> OvertimeListItem:
    return OvertimeListItem(
        id=record.id,
        employee_id=record.employee_id,
        employee_code=employee.code if employee else "",
        employee_name=employee.full_name if employee else "",
        group_name=group_name,
        work_date=record.work_date,
        raw_minutes=record.raw_minutes,
        approved_minutes=record.approved_minutes,
        status=record.status,
        source=record.source,
        employee_note=record.employee_note,
        admin_note=record.admin_note,
        admin_id=record.admin_id,
        decided_at=record.decided_at,
        shift_start_snapshot=record.shift_start_snapshot,
        shift_end_snapshot=record.shift_end_snapshot,
        is_weekend=record.is_weekend,
        is_holiday=record.is_holiday,
        created_at=record.created_at,
    )


# ── Admin endpoints ──────────────────────────────────────────────────────────
@router.get("", response_model=list[OvertimeListItem])
def list_overtime_records(
    status: str | None = Query(default=None, pattern="^(PENDING|APPROVED|REJECTED|all)$"),
    month: str | None = Query(default=None, description="YYYY-MM"),
    group_id: int | None = Query(default=None),
    employee_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    q = db.query(OvertimeRecord)
    if status and status != "all":
        q = q.filter(OvertimeRecord.status == status)
    period = _parse_month(month)
    if period:
        q = q.filter(OvertimeRecord.work_date >= period[0], OvertimeRecord.work_date <= period[1])
    if employee_id:
        q = q.filter(OvertimeRecord.employee_id == employee_id)

    records = q.order_by(OvertimeRecord.work_date.desc(), OvertimeRecord.id.desc()).all()
    if not records:
        return []

    emp_ids = {r.employee_id for r in records}
    employees = (
        db.query(Employee).filter(Employee.id.in_(emp_ids)).all()
    )
    emp_by_id = {emp.id: emp for emp in employees}

    if group_id is not None:
        records = [r for r in records if (emp_by_id.get(r.employee_id) and emp_by_id[r.employee_id].group_id == group_id)]

    group_ids = {emp.group_id for emp in employees if emp.group_id}
    groups = db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    group_name_by_id = {g.id: g.name for g in groups}

    return [
        _to_list_item(r, emp_by_id.get(r.employee_id), group_name_by_id.get(emp_by_id[r.employee_id].group_id) if emp_by_id.get(r.employee_id) and emp_by_id[r.employee_id].group_id else None)
        for r in records
    ]


@router.get("/me", response_model=MyOvertimeMonthSummary)
def my_overtime(
    month: str | None = Query(default=None, description="YYYY-MM; defaults to current month"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    employee = (
        db.query(Employee)
        .filter(Employee.user_id == current_user.id, Employee.deleted_at.is_(None))
        .first()
    )
    if employee is None:
        raise HTTPException(status_code=404, detail="employee profile not found for this account")

    if not month:
        today = date.today()
        month = f"{today.year:04d}-{today.month:02d}"
    period = _parse_month(month)
    if period is None:
        # _parse_month only returns None for empty input; we just defaulted month above.
        raise HTTPException(status_code=400, detail="month must be in 'YYYY-MM' format")

    records = (
        db.query(OvertimeRecord)
        .filter(
            OvertimeRecord.employee_id == employee.id,
            OvertimeRecord.work_date >= period[0],
            OvertimeRecord.work_date <= period[1],
        )
        .order_by(OvertimeRecord.work_date.desc())
        .all()
    )

    pending_total = sum(r.raw_minutes for r in records if r.status == "PENDING")
    approved_total = sum((r.approved_minutes or 0) for r in records if r.status == "APPROVED")
    rejected_total = sum(r.raw_minutes for r in records if r.status == "REJECTED")

    items = [
        MyOvertimeListItem(
            id=r.id,
            work_date=r.work_date,
            raw_minutes=r.raw_minutes,
            approved_minutes=r.approved_minutes,
            status=r.status,
            admin_note=r.admin_note,
            decided_at=r.decided_at,
            is_weekend=r.is_weekend,
            is_holiday=r.is_holiday,
        )
        for r in records
    ]
    return MyOvertimeMonthSummary(
        month=month,
        items=items,
        total_pending_minutes=pending_total,
        total_approved_minutes=approved_total,
        total_rejected_minutes=rejected_total,
    )


# NOTE: /bulk-approve is registered BEFORE /{ot_id} routes so FastAPI does not
# attempt to parse "bulk-approve" as an integer ot_id (which would 422).
@router.post("/bulk-approve", response_model=OvertimeBulkApproveResponse)
def bulk_approve_endpoint(
    payload: OvertimeBulkApproveRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    approved, skipped = overtime_service.bulk_approve(
        db,
        ids=payload.ids,
        strategy=payload.strategy,
        admin_id=admin.id,
        admin_note=payload.admin_note,
    )
    db.commit()
    return OvertimeBulkApproveResponse(approved_count=approved, skipped_ids=skipped)


@router.get("/{ot_id}", response_model=OvertimeDetailResponse)
def get_overtime_detail(
    ot_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    record = db.get(OvertimeRecord, ot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="overtime record not found")
    employee = db.get(Employee, record.employee_id)
    group_name = None
    if employee and employee.group_id:
        grp = db.get(Group, employee.group_id)
        group_name = grp.name if grp else None

    audits = (
        db.query(OvertimeAudit)
        .filter(OvertimeAudit.overtime_id == ot_id)
        .order_by(OvertimeAudit.id.asc())
        .all()
    )
    base = _to_list_item(record, employee, group_name)
    return OvertimeDetailResponse(
        **base.model_dump(),
        audits=[
            OvertimeAuditItem(
                id=a.id,
                action=a.action,
                actor_id=a.actor_id,
                from_status=a.from_status,
                to_status=a.to_status,
                from_minutes=a.from_minutes,
                to_minutes=a.to_minutes,
                note=a.note,
                created_at=a.created_at,
            )
            for a in audits
        ],
    )


@router.post("/{ot_id}/approve", response_model=OvertimeListItem)
def approve_overtime_endpoint(
    ot_id: int,
    payload: OvertimeApproveRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    record = overtime_service.approve_overtime(
        db, ot_id,
        approved_minutes=payload.approved_minutes,
        admin_id=admin.id,
        admin_note=payload.admin_note,
    )
    db.commit()
    db.refresh(record)
    employee = db.get(Employee, record.employee_id)
    group_name = None
    if employee and employee.group_id:
        grp = db.get(Group, employee.group_id)
        group_name = grp.name if grp else None
    # Fire FCM in background — done by the caller after commit
    try:
        from app.services.overtime_notifications import fire_decision_fcm  # type: ignore
        fire_decision_fcm(db, record, "approved")
    except Exception as fcm_exc:
        import logging
        logging.getLogger(__name__).warning("OT approve FCM dispatch failed: %s", fcm_exc)
    return _to_list_item(record, employee, group_name)


@router.post("/{ot_id}/reject", response_model=OvertimeListItem)
def reject_overtime_endpoint(
    ot_id: int,
    payload: OvertimeRejectRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    record = overtime_service.reject_overtime(
        db, ot_id,
        admin_id=admin.id,
        admin_note=payload.admin_note,
    )
    db.commit()
    db.refresh(record)
    employee = db.get(Employee, record.employee_id)
    group_name = None
    if employee and employee.group_id:
        grp = db.get(Group, employee.group_id)
        group_name = grp.name if grp else None
    try:
        from app.services.overtime_notifications import fire_decision_fcm  # type: ignore
        fire_decision_fcm(db, record, "rejected")
    except Exception as fcm_exc:
        import logging
        logging.getLogger(__name__).warning("OT reject FCM dispatch failed: %s", fcm_exc)
    return _to_list_item(record, employee, group_name)


@router.patch("/{ot_id}", response_model=OvertimeListItem)
def edit_overtime_endpoint(
    ot_id: int,
    payload: OvertimeEditRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    record = overtime_service.edit_approved_overtime(
        db, ot_id,
        approved_minutes=payload.approved_minutes,
        admin_id=admin.id,
        admin_note=payload.admin_note,
    )
    db.commit()
    db.refresh(record)
    employee = db.get(Employee, record.employee_id)
    group_name = None
    if employee and employee.group_id:
        grp = db.get(Group, employee.group_id)
        group_name = grp.name if grp else None
    try:
        from app.services.overtime_notifications import fire_decision_fcm  # type: ignore
        fire_decision_fcm(db, record, "edited")
    except Exception as fcm_exc:
        import logging
        logging.getLogger(__name__).warning("OT edit FCM dispatch failed: %s", fcm_exc)
    return _to_list_item(record, employee, group_name)
