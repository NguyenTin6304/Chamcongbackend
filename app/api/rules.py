from datetime import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.models import CheckinRule
from app.schemas.rules import RuleResponse, RuleUpdateRequest

router = APIRouter(prefix="/rules", tags=["rules"])

DEFAULT_START_TIME = time(8, 0)
DEFAULT_GRACE_MINUTES = 30
DEFAULT_END_TIME = time(17, 30)
DEFAULT_CHECKOUT_GRACE_MINUTES = 0
DEFAULT_CROSS_DAY_CUTOFF_MINUTES = 240


def _to_rule_response(rule: CheckinRule) -> RuleResponse:
    return RuleResponse(
        latitude=rule.latitude,
        longitude=rule.longitude,
        radius_m=rule.radius_m,
        start_time=rule.start_time,
        grace_minutes=rule.grace_minutes,
        end_time=rule.end_time,
        checkout_grace_minutes=rule.checkout_grace_minutes,
        cross_day_cutoff_minutes=rule.cross_day_cutoff_minutes,
    )


@router.get("/active", response_model=RuleResponse)
def get_active_rule(db: Session = Depends(get_db), _=Depends(get_current_user)):
    rule = db.query(CheckinRule).filter(CheckinRule.active.is_(True)).first()
    if not rule:
        raise HTTPException(status_code=404, detail="No active rule")
    return _to_rule_response(rule)


@router.put("/active", response_model=RuleResponse)
def update_active_rule(
    payload: RuleUpdateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    rule = db.query(CheckinRule).filter(CheckinRule.active.is_(True)).first()

    if not rule:
        rule = CheckinRule(
            active=True,
            latitude=payload.latitude,
            longitude=payload.longitude,
            radius_m=payload.radius_m,
            start_time=payload.start_time or DEFAULT_START_TIME,
            grace_minutes=(
                payload.grace_minutes
                if payload.grace_minutes is not None
                else DEFAULT_GRACE_MINUTES
            ),
            end_time=payload.end_time or DEFAULT_END_TIME,
            checkout_grace_minutes=(
                payload.checkout_grace_minutes
                if payload.checkout_grace_minutes is not None
                else DEFAULT_CHECKOUT_GRACE_MINUTES
            ),
            cross_day_cutoff_minutes=(
                payload.cross_day_cutoff_minutes
                if payload.cross_day_cutoff_minutes is not None
                else DEFAULT_CROSS_DAY_CUTOFF_MINUTES
            ),
        )
        db.add(rule)
    else:
        rule.latitude = payload.latitude
        rule.longitude = payload.longitude
        rule.radius_m = payload.radius_m
        if payload.start_time is not None:
            rule.start_time = payload.start_time
        elif rule.start_time is None:
            rule.start_time = DEFAULT_START_TIME

        if payload.grace_minutes is not None:
            rule.grace_minutes = payload.grace_minutes
        elif rule.grace_minutes is None:
            rule.grace_minutes = DEFAULT_GRACE_MINUTES

        if payload.end_time is not None:
            rule.end_time = payload.end_time
        elif rule.end_time is None:
            rule.end_time = DEFAULT_END_TIME

        if payload.checkout_grace_minutes is not None:
            rule.checkout_grace_minutes = payload.checkout_grace_minutes
        elif rule.checkout_grace_minutes is None:
            rule.checkout_grace_minutes = DEFAULT_CHECKOUT_GRACE_MINUTES

        if payload.cross_day_cutoff_minutes is not None:
            rule.cross_day_cutoff_minutes = payload.cross_day_cutoff_minutes
        elif rule.cross_day_cutoff_minutes is None:
            rule.cross_day_cutoff_minutes = DEFAULT_CROSS_DAY_CUTOFF_MINUTES

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        err = str(exc).lower()
        if "cross_day_cutoff_minutes" in err and (
            "does not exist" in err or "no such column" in err
        ):
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "DB_MIGRATION_REQUIRED",
                    "message": "Database schema chua cap nhat. Hay chay: alembic upgrade head",
                },
            )
        raise HTTPException(status_code=500, detail="Failed to update active rule")

    db.refresh(rule)
    return _to_rule_response(rule)
