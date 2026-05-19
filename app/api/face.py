"""Phase 4.1 — Face capture upload and retrieval."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import AttendanceException, AttendanceLog, Employee, ExceptionPolicy, User
from app.services.attendance_exception_audit import record_attendance_exception_audit
from app.services.attendance_exception_workflow import (
    default_exception_status_for_type,
    get_deadline_hours,
)
from app.services.face_quality import validate_face_image

router = APIRouter()

_MAX_FILE_SIZE = 8 * 1024 * 1024  # 8 MB
_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _resolve_upload_path(work_date_str: str, employee_id: int, log_id: int, log_type: str) -> Path:
    """Return absolute path for the face image. Parent dirs are created on demand."""
    base = Path(settings.FACE_UPLOAD_DIR)
    date_dir = base / work_date_str / str(employee_id)
    date_dir.mkdir(parents=True, exist_ok=True)
    suffix = "in" if log_type == "IN" else "out"
    return date_dir / f"{log_id}_{suffix}.jpg"


@router.post("/upload")
def upload_face_image(
    log_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Employee uploads a selfie after checkin/checkout.

    Validates:
    - file type and size
    - log_id belongs to the calling employee
    - image quality (size, brightness, not blank)
    Updates attendance_logs.face_* columns.
    """
    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Định dạng ảnh không hợp lệ. Chỉ chấp nhận JPEG, PNG, WebP.")

    image_bytes = file.file.read()
    if len(image_bytes) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="Ảnh quá lớn (tối đa 8 MB).")

    # Verify ownership
    emp = db.query(Employee).filter(Employee.user_id == current_user.id).first()
    if not emp:
        raise HTTPException(status_code=403, detail="Không tìm thấy thông tin nhân viên.")

    log = db.query(AttendanceLog).filter(
        AttendanceLog.id == log_id,
        AttendanceLog.employee_id == emp.id,
    ).first()
    if not log:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi chấm công.")

    # Quality validation
    is_valid, reason = validate_face_image(image_bytes)

    work_date_str = log.work_date.isoformat() if log.work_date else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    save_path = _resolve_upload_path(work_date_str, emp.id, log_id, log.type)

    save_path.write_bytes(image_bytes)

    log.face_image_path = str(save_path.relative_to(Path(settings.FACE_UPLOAD_DIR).parent))
    log.face_check_status = "CAPTURED" if is_valid else "QUALITY_LOW"
    log.face_captured_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "status": "ok" if is_valid else "quality_low",
        "face_check_status": log.face_check_status,
        "reason": None if is_valid else reason,
    }


@router.post("/flag-no-camera")
def flag_no_camera(
    log_id: int = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a log as NOT_CAPTURED + auto-create FACE_NOT_CAPTURED exception
    so admin can review the manual checkin.

    Idempotent: re-calling with the same log_id will not create a duplicate
    exception (per UNIQUE(source_checkin_log_id) constraint).
    """
    emp = db.query(Employee).filter(Employee.user_id == current_user.id).first()
    if not emp:
        raise HTTPException(status_code=403, detail="Không tìm thấy thông tin nhân viên.")

    log = db.query(AttendanceLog).filter(
        AttendanceLog.id == log_id,
        AttendanceLog.employee_id == emp.id,
    ).first()
    if not log:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi chấm công.")

    now_utc = datetime.now(timezone.utc)
    log.face_check_status = "NOT_CAPTURED"
    log.face_captured_at = now_utc

    # Auto-create exception if not yet present for this log.
    existing = (
        db.query(AttendanceException)
        .filter(AttendanceException.source_checkin_log_id == log.id)
        .first()
    )
    if existing is None:
        initial_status = default_exception_status_for_type("FACE_NOT_CAPTURED")
        # Compute deadline from policy (falls back to default_deadline_hours if not configured).
        policy = db.query(ExceptionPolicy).filter(ExceptionPolicy.id == 1).first()
        expires_at = None
        if policy is not None:
            deadline_hours = get_deadline_hours(policy, "FACE_NOT_CAPTURED")
            expires_at = now_utc + timedelta(hours=deadline_hours)
        exc = AttendanceException(
            employee_id=emp.id,
            source_checkin_log_id=log.id,
            exception_type="FACE_NOT_CAPTURED",
            work_date=log.work_date or log.time.date(),
            status=initial_status,
            note="Thiết bị không có camera khi chấm công.",
            detected_at=now_utc,
            expires_at=expires_at,
        )
        db.add(exc)
        db.flush()
        record_attendance_exception_audit(
            db,
            exception_id=exc.id,
            event_type="exception_detected",
            previous_status=None,
            next_status=exc.status,
            actor_type="SYSTEM",
            actor_email="SYSTEM",
            metadata={
                "exception_type": "FACE_NOT_CAPTURED",
                "source_checkin_log_id": log.id,
                "employee_id": emp.id,
            },
        )

    db.commit()
    return {"status": "ok", "face_check_status": "NOT_CAPTURED"}


@router.get("/image/{log_id}")
def get_face_image(
    log_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Admin retrieves the face image for a given attendance log."""
    log = db.query(AttendanceLog).filter(AttendanceLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi.")

    if not log.face_image_path:
        raise HTTPException(status_code=404, detail="Không có ảnh cho bản ghi này.")

    # face_image_path is relative to parent of FACE_UPLOAD_DIR
    image_path = Path(settings.FACE_UPLOAD_DIR).parent / log.face_image_path
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="File ảnh không còn trên server.")

    return FileResponse(path=str(image_path), media_type="image/jpeg")
