from __future__ import annotations

import json
from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.models import AttendanceExceptionAudit
from app.services.attendance_exception_workflow import normalize_exception_status


def record_attendance_exception_audit(
    db: Session,
    *,
    exception_id: int,
    event_type: str,
    previous_status: str | None,
    next_status: str,
    actor_type: str,
    actor_id: int | None = None,
    actor_email: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> AttendanceExceptionAudit:
    audit = AttendanceExceptionAudit(
        exception_id=exception_id,
        event_type=event_type,
        previous_status=normalize_exception_status(previous_status),
        next_status=normalize_exception_status(next_status) or next_status,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_email=actor_email,
        metadata_json=(
            json.dumps(dict(metadata), ensure_ascii=True, separators=(",", ":"), default=str)
            if metadata
            else None
        ),
    )
    db.add(audit)
    return audit
