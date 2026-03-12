from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import AttendanceLog, Employee, Group, User
from app.schemas.employees import (
    EmployeeAssignGroupRequest,
    EmployeeAssignUserRequest,
    EmployeeCreateRequest,
    EmployeeResponse,
)

router = APIRouter(prefix="/employees", tags=["employees"])


def _validate_user_mapping(db: Session, employee_id: int | None, user_id: int | None) -> None:
    if user_id is None:
        return

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="user_id khong ton tai")

    query = db.query(Employee).filter(Employee.user_id == user_id)
    if employee_id is not None:
        query = query.filter(Employee.id != employee_id)
    existed_emp = query.first()
    if existed_emp:
        raise HTTPException(status_code=400, detail="User nay da duoc gan cho employee khac")


def _validate_group_exists(db: Session, group_id: int | None) -> None:
    if group_id is None:
        return
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=400, detail="group_id khong ton tai")


@router.post("", response_model=EmployeeResponse)
def create_employee(
    payload: EmployeeCreateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    _validate_user_mapping(db, employee_id=None, user_id=payload.user_id)
    _validate_group_exists(db, payload.group_id)

    emp = Employee(
        code=payload.code,
        full_name=payload.full_name,
        user_id=payload.user_id,
        group_id=payload.group_id,
    )

    try:
        db.add(emp)
        db.commit()
        db.refresh(emp)
        return emp
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Ma employee (code) bi trung hoac du lieu khong hop le")


@router.get("", response_model=list[EmployeeResponse])
def list_employees(
    q: str | None = None,
    unassigned_only: bool = False,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    query = db.query(Employee)

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
    emp = db.query(Employee).filter(Employee.user_id == user.id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Ban chua duoc gan Employee")
    return emp


@router.put("/{employee_id}/assign-user", response_model=EmployeeResponse)
def assign_user_to_employee(
    employee_id: int,
    payload: EmployeeAssignUserRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

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
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

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
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return emp


@router.delete("/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    has_logs = db.query(AttendanceLog.id).filter(AttendanceLog.employee_id == employee_id).first()
    if has_logs:
        raise HTTPException(
            status_code=409,
            detail="Employee da co du lieu cham cong, khong the xoa de tranh mat lich su",
        )

    try:
        db.delete(emp)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Khong the xoa employee do rang buoc du lieu")
