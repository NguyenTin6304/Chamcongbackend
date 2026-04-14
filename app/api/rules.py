from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_admin
from app.core.policy import WARN_GEOFENCE_RADIUS_M
from app.models import CheckinRule, ExceptionPolicy, User
from app.schemas.rules import RuleResponse, RuleUpdateRequest
from app.schemas.exception_policy import ExceptionPolicyResponse, ExceptionPolicyPatch

router = APIRouter(prefix="/rules", tags=["rules"])

DEFAULT_START_TIME = time(8, 0)
DEFAULT_GRACE_MINUTES = 30
DEFAULT_END_TIME = time(17, 30)
DEFAULT_CHECKOUT_GRACE_MINUTES = 0
DEFAULT_CROSS_DAY_CUTOFF_MINUTES = 240


def _radius_policy_warning(radius_m: int) -> str | None:
    if radius_m > WARN_GEOFENCE_RADIUS_M:
        return "RADIUS_ABOVE_POLICY_THRESHOLD"
    return None


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
        radius_policy_warning=_radius_policy_warning(rule.radius_m),
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


# ---------------------------------------------------------------------------
# Exception Policy (singleton id=1)
# ---------------------------------------------------------------------------

def _get_or_create_policy(db: Session) -> ExceptionPolicy:
    """Return the singleton ExceptionPolicy row, creating it with defaults if absent."""
    policy = db.query(ExceptionPolicy).filter(ExceptionPolicy.id == 1).first()
    if policy is None:
        policy = ExceptionPolicy(
            id=1,
            default_deadline_hours=72,
            grace_period_days=30,
        )
        db.add(policy)
        db.commit()
        db.refresh(policy)
    return policy


def _policy_to_response(policy: ExceptionPolicy, db: Session) -> ExceptionPolicyResponse:
    updated_by_name: str | None = None
    if policy.updated_by_id:
        user = db.query(User).filter(User.id == policy.updated_by_id).first()
        if user:
            updated_by_name = user.full_name or user.email
    return ExceptionPolicyResponse(
        default_deadline_hours=policy.default_deadline_hours,
        auto_closed_deadline_hours=policy.auto_closed_deadline_hours,
        missed_checkout_deadline_hours=policy.missed_checkout_deadline_hours,
        location_risk_deadline_hours=policy.location_risk_deadline_hours,
        large_time_deviation_deadline_hours=policy.large_time_deviation_deadline_hours,
        grace_period_days=policy.grace_period_days,
        updated_at=policy.updated_at,
        updated_by_name=updated_by_name,
    )


@router.get("/exception-policy", response_model=ExceptionPolicyResponse)
def get_exception_policy(
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    policy = _get_or_create_policy(db)
    return _policy_to_response(policy, db)


@router.patch("/exception-policy", response_model=ExceptionPolicyResponse)
def patch_exception_policy(
    payload: ExceptionPolicyPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    policy = _get_or_create_policy(db)

    provided = payload.model_fields_set
    if "default_deadline_hours" in provided and payload.default_deadline_hours is not None:
        policy.default_deadline_hours = payload.default_deadline_hours
    if "auto_closed_deadline_hours" in provided:
        policy.auto_closed_deadline_hours = payload.auto_closed_deadline_hours
    if "missed_checkout_deadline_hours" in provided:
        policy.missed_checkout_deadline_hours = payload.missed_checkout_deadline_hours
    if "location_risk_deadline_hours" in provided:
        policy.location_risk_deadline_hours = payload.location_risk_deadline_hours
    if "large_time_deviation_deadline_hours" in provided:
        policy.large_time_deviation_deadline_hours = payload.large_time_deviation_deadline_hours
    if "grace_period_days" in provided and payload.grace_period_days is not None:
        policy.grace_period_days = payload.grace_period_days

    policy.updated_at = datetime.now(timezone.utc)
    policy.updated_by_id = current_user.id

    db.commit()
    db.refresh(policy)
    return _policy_to_response(policy, db)
