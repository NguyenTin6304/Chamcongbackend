from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require_admin
from app.models import Employee, User
from app.schemas.users import UserLiteResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserLiteResponse])
def list_users(
    q: str | None = None,
    role: str | None = None,
    limit: int = 200,
    unlinked_only: bool = False,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    query = db.query(User)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                User.email.ilike(like),
                User.full_name.ilike(like),
                User.phone.ilike(like),
            )
        )

    if role:
        query = query.filter(User.role == role.upper())

    if unlinked_only:
        # Exclude users already linked to an active (non-deleted) employee.
        linked_user_ids = (
            db.query(Employee.user_id)
            .filter(Employee.user_id.isnot(None), Employee.deleted_at.is_(None))
            .scalar_subquery()
        )
        query = query.filter(User.id.notin_(linked_user_ids))

    safe_limit = min(max(limit, 1), 1000)

    users = query.order_by(User.id.desc()).limit(safe_limit).all()
    return [
        UserLiteResponse(
            id=u.id,
            email=u.email,
            role=u.role,
            full_name=u.full_name,
            phone=u.phone,
        )
        for u in users
    ]
