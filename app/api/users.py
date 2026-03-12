from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require_admin
from app.models import User
from app.schemas.users import UserLiteResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserLiteResponse])
def list_users(
    q: str | None = None,
    role: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    query = db.query(User)

    if q:
        like = f"%{q}%"
        query = query.filter(User.email.ilike(like))

    if role:
        query = query.filter(User.role == role.upper())

    safe_limit = min(max(limit, 1), 1000)

    users = query.order_by(User.id.desc()).limit(safe_limit).all()
    return [
        UserLiteResponse(id=u.id, email=u.email, role=u.role)
        for u in users
    ]
