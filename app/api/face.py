"""Phase 4.1 — Face capture upload and retrieval.

Phase 4.2 extends this module with embedding extraction, verification against
a per-employee reference, and admin reference-management endpoints.
"""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import (
    AttendanceException,
    AttendanceLog,
    Employee,
    EmployeeFaceReference,
    ExceptionPolicy,
    User,
)
from app.services.attendance_exception_audit import record_attendance_exception_audit
from app.services.attendance_exception_workflow import (
    default_exception_status_for_type,
    get_deadline_hours,
)
from app.services.face_embedding import cosine_similarity, extract_embedding
from app.services.face_quality import validate_face_image

router = APIRouter()

_MAX_FILE_SIZE = 8 * 1024 * 1024  # 8 MB
_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

# Phase 4.2 cosine-similarity thresholds. Tune after collecting real data.
_FACE_MATCH_THRESHOLD = 0.80
_FACE_LOW_CONFIDENCE_THRESHOLD = 0.60


def _resolve_upload_path(work_date_str: str, employee_id: int, log_id: int, log_type: str) -> Path:
    """Return absolute path for the face image. Parent dirs are created on demand."""
    base = Path(settings.FACE_UPLOAD_DIR)
    date_dir = base / work_date_str / str(employee_id)
    date_dir.mkdir(parents=True, exist_ok=True)
    suffix = "in" if log_type == "IN" else "out"
    return date_dir / f"{log_id}_{suffix}.jpg"


def _create_face_verify_exception(
    db: Session,
    log: AttendanceLog,
    exception_type: str,
    score: float,
) -> None:
    """Create a FACE_MISMATCH / FACE_LOW_CONFIDENCE exception for the given log.

    Idempotent: skips creation only when an exception of the SAME face-verify
    type already exists for this log. We do NOT skip when an unrelated type
    (e.g. FACE_NOT_CAPTURED) is present — those represent different conditions.
    Note: the AttendanceException table has UNIQUE(source_checkin_log_id), so
    only one exception per log is permitted in total; this means a verify-time
    exception cannot coexist with FACE_NOT_CAPTURED. That is acceptable because
    flag-no-camera and upload are mutually exclusive flows.
    """
    existing = (
        db.query(AttendanceException)
        .filter(
            AttendanceException.source_checkin_log_id == log.id,
            AttendanceException.exception_type == exception_type,
        )
        .first()
    )
    if existing is not None:
        return

    now_utc = datetime.now(timezone.utc)
    initial_status = default_exception_status_for_type(exception_type)
    policy = db.query(ExceptionPolicy).filter(ExceptionPolicy.id == 1).first()
    expires_at = None
    if policy is not None:
        deadline_hours = get_deadline_hours(policy, exception_type)
        expires_at = now_utc + timedelta(hours=deadline_hours)

    note_map = {
        "FACE_MISMATCH": "Khuôn mặt khi chấm công không khớp với ảnh tham chiếu.",
        "FACE_LOW_CONFIDENCE": "Độ tương đồng khuôn mặt thấp, cần admin xác nhận.",
    }
    exc = AttendanceException(
        employee_id=log.employee_id,
        source_checkin_log_id=log.id,
        exception_type=exception_type,
        work_date=log.work_date or log.time.date(),
        status=initial_status,
        note=note_map.get(exception_type, "Cần xác nhận khuôn mặt."),
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
            "exception_type": exception_type,
            "source_checkin_log_id": log.id,
            "employee_id": log.employee_id,
            "face_match_score": round(score, 4),
        },
    )


def _run_face_verification(
    db: Session,
    log: AttendanceLog,
    image_bytes: bytes,
) -> None:
    """Extract embedding and compare against reference if any.

    Mutates `log` in place (face_embedding, face_match_score, face_verify_status)
    and creates a FACE_MISMATCH / FACE_LOW_CONFIDENCE exception if needed.
    Caller is responsible for committing the transaction.
    """
    embedding = extract_embedding(image_bytes)
    if embedding is None:
        # No face detected (or insightface missing) — leave verify_status NULL
        # so admin can still review the image; quality status already set.
        return
    log.face_embedding = embedding

    # Don't compare low-quality images — risk of false MISMATCH.
    if log.face_check_status == "QUALITY_LOW":
        log.face_verify_status = "SKIPPED"
        return

    ref = (
        db.query(EmployeeFaceReference)
        .filter(EmployeeFaceReference.employee_id == log.employee_id)
        .first()
    )
    if ref is None or not ref.face_embedding:
        log.face_verify_status = "SKIPPED"
        return

    score = cosine_similarity(embedding, list(ref.face_embedding))
    log.face_match_score = round(score, 4)
    if score >= _FACE_MATCH_THRESHOLD:
        log.face_verify_status = "MATCH"
    elif score >= _FACE_LOW_CONFIDENCE_THRESHOLD:
        log.face_verify_status = "LOW_CONFIDENCE"
        _create_face_verify_exception(db, log, "FACE_LOW_CONFIDENCE", score)
    else:
        log.face_verify_status = "MISMATCH"
        _create_face_verify_exception(db, log, "FACE_MISMATCH", score)


def _verify_face_in_background(log_id: int, image_bytes: bytes) -> None:
    """Run after the upload response is sent so the employee is not blocked
    by the ~30-60s insightface cold-start on first use.
    Opens its own DB session since the request session is already closed.
    """
    from app.core.db import SessionLocal

    try:
        with SessionLocal() as bg_db:
            log = bg_db.query(AttendanceLog).filter(AttendanceLog.id == log_id).first()
            if log is None:
                return
            _run_face_verification(bg_db, log, image_bytes)
            bg_db.commit()
    except Exception:
        _logger.exception("background face verification failed for log_id=%s", log_id)


@router.post("/upload")
def upload_face_image(
    background_tasks: BackgroundTasks,
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

    # Commit image save immediately — respond to employee without waiting for
    # insightface (which can take 30-60s on first cold-start on Windows).
    db.commit()

    # Phase 4.2 — face verification runs after the response is returned so the
    # employee is never blocked by ONNX model load time.
    background_tasks.add_task(_verify_face_in_background, log_id, image_bytes)

    return {
        "status": "ok" if is_valid else "quality_low",
        "face_check_status": log.face_check_status,
        "face_verify_status": None,  # set asynchronously by background task
        "face_match_score": None,
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


# ---------------------------------------------------------------------------
# Phase 4.2 — Reference management (admin only)
# ---------------------------------------------------------------------------


def _serialize_candidate(log: AttendanceLog) -> dict:
    return {
        "log_id": log.id,
        "work_date": log.work_date.isoformat() if log.work_date else None,
        "type": log.type,
        "captured_at": log.face_captured_at.isoformat() if log.face_captured_at else None,
        "face_image_path": log.face_image_path,
    }


def _serialize_reference(ref: EmployeeFaceReference, admin_email: Optional[str]) -> dict:
    return {
        "employee_id": ref.employee_id,
        "log_id_source": ref.log_id_source,
        "set_by_admin_email": admin_email,
        "created_at": ref.created_at.isoformat() if ref.created_at else None,
        "updated_at": ref.updated_at.isoformat() if ref.updated_at else None,
    }


@router.get("/candidates/{employee_id}")
def list_face_candidates(
    employee_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Return up to 3 most-recent CAPTURED face logs (one per day) + current reference.

    Used by the admin UI to let HR pick which capture becomes the verification
    reference for the employee.
    """
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên.")

    logs = (
        db.query(AttendanceLog)
        .filter(
            AttendanceLog.employee_id == employee_id,
            AttendanceLog.face_check_status == "CAPTURED",
            AttendanceLog.face_image_path.isnot(None),
        )
        .order_by(AttendanceLog.time.desc())
        .limit(60)  # scan a small window, then de-dupe by day below
        .all()
    )

    candidates: list[dict] = []
    seen_dates: set = set()
    for log in logs:
        key = log.work_date.isoformat() if log.work_date else log.time.date().isoformat()
        if key in seen_dates:
            continue
        seen_dates.add(key)
        candidates.append(_serialize_candidate(log))
        if len(candidates) >= 3:
            break

    ref = (
        db.query(EmployeeFaceReference)
        .filter(EmployeeFaceReference.employee_id == employee_id)
        .first()
    )
    current = None
    if ref is not None:
        admin_email = None
        if ref.set_by_admin_id is not None:
            admin = db.query(User).filter(User.id == ref.set_by_admin_id).first()
            admin_email = admin.email if admin else None
        current = _serialize_reference(ref, admin_email)

    return {"candidates": candidates, "current": current}


@router.post("/reference/{employee_id}")
def set_face_reference(
    employee_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Set / replace the verification reference for an employee from a log_id.

    The embedding is re-extracted from the saved image file so we don't trust
    whatever was stored in attendance_logs.face_embedding (which can be NULL
    on pre-Phase-4.2 captures).
    """
    log_id = payload.get("log_id")
    if not isinstance(log_id, int):
        raise HTTPException(status_code=400, detail="log_id (int) là bắt buộc.")

    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên.")

    log = (
        db.query(AttendanceLog)
        .filter(
            AttendanceLog.id == log_id,
            AttendanceLog.employee_id == employee_id,
        )
        .first()
    )
    if not log:
        raise HTTPException(
            status_code=404,
            detail="Bản ghi chấm công không thuộc nhân viên này.",
        )
    if not log.face_image_path:
        raise HTTPException(status_code=400, detail="Bản ghi này chưa có ảnh khuôn mặt.")
    if log.face_check_status != "CAPTURED":
        raise HTTPException(
            status_code=400,
            detail="Chỉ được chọn ảnh có chất lượng đạt chuẩn (CAPTURED) làm tham chiếu.",
        )

    image_path = Path(settings.FACE_UPLOAD_DIR).parent / log.face_image_path
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="File ảnh không còn trên server.")

    embedding = extract_embedding(image_path.read_bytes())
    if embedding is None:
        raise HTTPException(
            status_code=400,
            detail="Không phát hiện khuôn mặt trong ảnh đã chọn. Vui lòng chọn ảnh khác.",
        )

    ref = (
        db.query(EmployeeFaceReference)
        .filter(EmployeeFaceReference.employee_id == employee_id)
        .first()
    )
    if ref is None:
        ref = EmployeeFaceReference(
            employee_id=employee_id,
            log_id_source=log.id,
            face_embedding=embedding,
            set_by_admin_id=admin.id,
        )
        db.add(ref)
    else:
        ref.log_id_source = log.id
        ref.face_embedding = embedding
        ref.set_by_admin_id = admin.id

    db.commit()
    db.refresh(ref)
    return _serialize_reference(ref, admin.email)


@router.get("/reference/{employee_id}")
def get_face_reference(
    employee_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Return the current reference (or 404)."""
    ref = (
        db.query(EmployeeFaceReference)
        .filter(EmployeeFaceReference.employee_id == employee_id)
        .first()
    )
    if ref is None:
        raise HTTPException(status_code=404, detail="Chưa có ảnh tham chiếu.")
    admin_email = None
    if ref.set_by_admin_id is not None:
        admin = db.query(User).filter(User.id == ref.set_by_admin_id).first()
        admin_email = admin.email if admin else None
    return _serialize_reference(ref, admin_email)


@router.delete("/reference/{employee_id}")
def delete_face_reference(
    employee_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Remove the reference so future captures fall back to SKIPPED verify."""
    ref = (
        db.query(EmployeeFaceReference)
        .filter(EmployeeFaceReference.employee_id == employee_id)
        .first()
    )
    if ref is None:
        raise HTTPException(status_code=404, detail="Chưa có ảnh tham chiếu.")
    db.delete(ref)
    db.commit()
    return {"message": "Đã xóa ảnh tham chiếu."}
