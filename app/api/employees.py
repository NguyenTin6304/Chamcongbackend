from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import AttendanceLog, CheckinRule, Employee, EmployeeShiftOverride, Group, Shift, User
from app.schemas.employees import (
    EmployeeAssignGroupRequest,
    EmployeeAssignUserRequest,
    EmployeeCreateRequest,
    EmployeeResponse,
    EmployeeShiftOverrideResponse,
    EmployeeShiftOverrideUpsertRequest,
    EmployeeUpdateRequest,
)

router = APIRouter(prefix="/employees", tags=["employees"])


def _validate_user_mapping(db: Session, employee_id: int | None, user_id: int | None) -> None:
    if user_id is None:
        return

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="user_id không tồn tại")

    query = db.query(Employee).filter(
        Employee.user_id == user_id,
        Employee.deleted_at.is_(None),
    )
    if employee_id is not None:
        query = query.filter(Employee.id != employee_id)
    existed_emp = query.first()
    if existed_emp:
        raise HTTPException(status_code=400, detail="User này đã được gán cho employee khác")


def _validate_group_exists(db: Session, group_id: int | None) -> None:
    if group_id is None:
        return
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=400, detail="group_id không tồn tại")


@router.post("", response_model=EmployeeResponse)
def create_employee(
    payload: EmployeeCreateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    _validate_user_mapping(db, employee_id=None, user_id=payload.user_id)
    _validate_group_exists(db, payload.group_id)

    # Resolve annual_leave_days: use explicit value if provided, else company default
    annual_leave_days = payload.annual_leave_days
    if annual_leave_days is None:
        active_rule = db.query(CheckinRule).filter(CheckinRule.active.is_(True)).first()
        if active_rule is not None:
            annual_leave_days = active_rule.default_annual_leave_days

    emp = Employee(
        code=payload.code,
        full_name=payload.full_name,
        phone=payload.phone,
        user_id=payload.user_id,
        group_id=payload.group_id,
        annual_leave_days=annual_leave_days,
    )

    try:
        db.add(emp)
        db.commit()
        db.refresh(emp)
        return emp
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Mã nhân viên bị trùng hoặc dữ liệu không hợp lệ")


@router.get("", response_model=list[EmployeeResponse])
def list_employees(
    q: str | None = None,
    unassigned_only: bool = False,
    status: str | None = None,  # "active" | "inactive" | "resigned" | None (all non-resigned)
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    if status == "resigned":
        query = db.query(Employee).filter(Employee.deleted_at.isnot(None))
    else:
        query = db.query(Employee).filter(Employee.deleted_at.is_(None))
        if status == "active":
            query = query.filter(Employee.active.is_(True))
        elif status == "inactive":
            query = query.filter(Employee.active.is_(False))

    if q:
        like = f"%{q}%"
        query = query.filter((Employee.code.ilike(like)) | (Employee.full_name.ilike(like)))

    if unassigned_only:
        query = query.filter(Employee.user_id.is_(None))

    return query.order_by(Employee.id.desc()).all()


@router.get("/me", response_model=EmployeeResponse)
def my_employee_profile(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = db.query(Employee).filter(
        Employee.user_id == user.id,
        Employee.deleted_at.is_(None),
    ).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Bạn chưa được gán nhân viên, vui lòng liên hệ quản trị viên")

    group_name: str | None = None
    if emp.group_id is not None:
        group = db.query(Group).filter(Group.id == emp.group_id).first()
        if group:
            group_name = group.name

    return EmployeeResponse(
        id=emp.id,
        code=emp.code,
        full_name=emp.full_name,
        phone=emp.phone,
        user_id=emp.user_id,
        group_id=emp.group_id,
        group_name=group_name,
        active=emp.active,
        resigned_at=emp.deleted_at,
        joined_at=emp.created_at,
    )


@router.put("/{employee_id}", response_model=EmployeeResponse)
def update_employee(
    employee_id: int,
    payload: EmployeeUpdateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    emp = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.deleted_at.is_(None),
    ).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên")

    if payload.full_name is not None:
        name = payload.full_name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Họ và tên không được để trống")
        emp.full_name = name

    if "phone" in payload.model_fields_set:
        emp.phone = payload.phone

    if "user_id" in payload.model_fields_set:
        _validate_user_mapping(db, employee_id=employee_id, user_id=payload.user_id)
        emp.user_id = payload.user_id

    if "group_id" in payload.model_fields_set:
        _validate_group_exists(db, payload.group_id)
        emp.group_id = payload.group_id

    if "active" in payload.model_fields_set and payload.active is not None:
        emp.active = payload.active

    if "annual_leave_days" in payload.model_fields_set:
        # -1.0 is the sentinel for "set to unlimited (NULL)"
        if payload.annual_leave_days is not None and payload.annual_leave_days < 0:
            emp.annual_leave_days = None
        else:
            emp.annual_leave_days = payload.annual_leave_days

    db.commit()
    db.refresh(emp)
    return emp


@router.put("/{employee_id}/assign-user", response_model=EmployeeResponse)
def assign_user_to_employee(
    employee_id: int,
    payload: EmployeeAssignUserRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    emp = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.deleted_at.is_(None),
    ).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên")

    _validate_user_mapping(db, employee_id=employee_id, user_id=payload.user_id)
    emp.user_id = payload.user_id

    db.commit()
    db.refresh(emp)
    return emp


@router.put("/{employee_id}/assign-group", response_model=EmployeeResponse)
def assign_group_to_employee(
    employee_id: int,
    payload: EmployeeAssignGroupRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    emp = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.deleted_at.is_(None),
    ).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên")

    _validate_group_exists(db, payload.group_id)
    emp.group_id = payload.group_id

    db.commit()
    db.refresh(emp)
    return emp


@router.get("/{employee_id}", response_model=EmployeeResponse)
def get_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    emp = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.deleted_at.is_(None),
    ).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên")
    return emp


@router.delete("/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên")

    if emp.deleted_at is None:
        # Stage 1: nhân viên đang làm → chuyển sang "Đã nghỉ việc"
        emp.deleted_at = datetime.now(timezone.utc)
        emp.active = False
        emp.user_id = None  # Huỷ liên kết tài khoản
        db.commit()
    else:
        # Stage 2: nhân viên đã nghỉ → kiểm tra lịch sử chấm công
        has_logs = db.query(AttendanceLog).filter(
            AttendanceLog.employee_id == employee_id
        ).first() is not None
        if has_logs:
            raise HTTPException(
                status_code=409,
                detail="Nhân viên có lịch sử chấm công, hồ sơ được giữ lại để tra cứu",
            )
        db.delete(emp)
        db.commit()


@router.put("/{employee_id}/restore", response_model=EmployeeResponse)
def restore_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    emp = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.deleted_at.isnot(None),
    ).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên đã nghỉ việc")

    emp.deleted_at = None
    emp.active = True
    db.commit()
    db.refresh(emp)
    return emp


# ─── Shift override (Phase 3B) ────────────────────────────────────────────────


def _override_to_response(
    override: EmployeeShiftOverride, shift: Shift
) -> EmployeeShiftOverrideResponse:
    return EmployeeShiftOverrideResponse(
        id=override.id,
        employee_id=override.employee_id,
        shift_id=override.shift_id,
        shift_name=shift.name,
        shift_start_time=shift.start_time,
        shift_end_time=shift.end_time,
        effective_date=override.effective_date,
        end_date=override.end_date,
    )


def _get_active_employee_or_404(db: Session, employee_id: int) -> Employee:
    emp = (
        db.query(Employee)
        .filter(Employee.id == employee_id, Employee.deleted_at.is_(None))
        .first()
    )
    if not emp:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên")
    return emp


@router.get(
    "/{employee_id}/shift-override",
    response_model=EmployeeShiftOverrideResponse | None,
)
def get_employee_shift_override(
    employee_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    _get_active_employee_or_404(db, employee_id)

    override = (
        db.query(EmployeeShiftOverride)
        .filter(EmployeeShiftOverride.employee_id == employee_id)
        .first()
    )
    if override is None:
        return None

    shift = db.query(Shift).filter(Shift.id == override.shift_id).first()
    if shift is None:
        # Orphan override (shift was deleted) — clean up silently.
        db.delete(override)
        db.commit()
        return None

    return _override_to_response(override, shift)


@router.put(
    "/{employee_id}/shift-override",
    response_model=EmployeeShiftOverrideResponse,
)
def upsert_employee_shift_override(
    employee_id: int,
    payload: EmployeeShiftOverrideUpsertRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    emp = _get_active_employee_or_404(db, employee_id)

    shift = db.query(Shift).filter(Shift.id == payload.shift_id).first()
    if shift is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy ca làm việc")

    # Shift must belong to the same group as the employee. Without this guard
    # an admin could assign "Ca chiều của nhóm B" to an employee in nhóm A,
    # which would silently break geofence assumptions downstream.
    if emp.group_id is None or shift.group_id != emp.group_id:
        raise HTTPException(
            status_code=400,
            detail="Ca làm việc phải thuộc cùng nhóm với nhân viên",
        )

    if not shift.active:
        raise HTTPException(status_code=400, detail="Ca làm việc đã ngừng hoạt động")

    override = (
        db.query(EmployeeShiftOverride)
        .filter(EmployeeShiftOverride.employee_id == employee_id)
        .first()
    )
    if override is None:
        override = EmployeeShiftOverride(
            employee_id=employee_id,
            shift_id=payload.shift_id,
            effective_date=payload.effective_date,
            end_date=payload.end_date,
        )
        db.add(override)
    else:
        override.shift_id = payload.shift_id
        override.effective_date = payload.effective_date
        override.end_date = payload.end_date

    db.commit()
    db.refresh(override)
    return _override_to_response(override, shift)


@router.delete(
    "/{employee_id}/shift-override",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_employee_shift_override(
    employee_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    _get_active_employee_or_404(db, employee_id)

    override = (
        db.query(EmployeeShiftOverride)
        .filter(EmployeeShiftOverride.employee_id == employee_id)
        .first()
    )
    if override is None:
        return
    db.delete(override)
    db.commit()
