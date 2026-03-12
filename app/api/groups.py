from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require_admin
from app.models import Employee, Group, GroupGeofence
from app.schemas.groups import (
    GroupCreateRequest,
    GroupGeofenceCreateRequest,
    GroupGeofenceResponse,
    GroupGeofenceUpdateRequest,
    GroupResponse,
    GroupUpdateRequest,
)

router = APIRouter(prefix="/groups", tags=["groups"])


def _get_group_or_404(db: Session, group_id: int) -> Group:
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


def _get_geofence_or_404(db: Session, group_id: int, geofence_id: int) -> GroupGeofence:
    geofence = (
        db.query(GroupGeofence)
        .filter(GroupGeofence.id == geofence_id, GroupGeofence.group_id == group_id)
        .first()
    )
    if not geofence:
        raise HTTPException(status_code=404, detail="Geofence not found")
    return geofence


@router.post("", response_model=GroupResponse)
def create_group(
    payload: GroupCreateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    group = Group(
        code=payload.code,
        name=payload.name,
        active=payload.active,
        start_time=payload.start_time,
        grace_minutes=payload.grace_minutes,
        end_time=payload.end_time,
        checkout_grace_minutes=payload.checkout_grace_minutes,
    )
    try:
        db.add(group)
        db.commit()
        db.refresh(group)
        return group
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Group code already exists")


@router.get("", response_model=list[GroupResponse])
def list_groups(
    active_only: bool = False,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    query = db.query(Group)
    if active_only:
        query = query.filter(Group.active.is_(True))
    return query.order_by(Group.id.asc()).all()


@router.put("/{group_id}", response_model=GroupResponse)
def update_group(
    group_id: int,
    payload: GroupUpdateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    group = _get_group_or_404(db, group_id)

    if "code" in payload.model_fields_set:
        group.code = payload.code
    if "name" in payload.model_fields_set:
        group.name = payload.name
    if "active" in payload.model_fields_set:
        group.active = payload.active
    if "start_time" in payload.model_fields_set:
        group.start_time = payload.start_time
    if "grace_minutes" in payload.model_fields_set:
        group.grace_minutes = payload.grace_minutes
    if "end_time" in payload.model_fields_set:
        group.end_time = payload.end_time
    if "checkout_grace_minutes" in payload.model_fields_set:
        group.checkout_grace_minutes = payload.checkout_grace_minutes

    try:
        db.commit()
        db.refresh(group)
        return group
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Group code already exists")


@router.delete("/{group_id}")
def delete_group(
    group_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    group = _get_group_or_404(db, group_id)

    try:
        cleared_employee_count = (
            db.query(Employee)
            .filter(Employee.group_id == group_id)
            .update({Employee.group_id: None}, synchronize_session=False)
        )
        deleted_geofence_count = (
            db.query(GroupGeofence)
            .filter(GroupGeofence.group_id == group_id)
            .delete(synchronize_session=False)
        )
        db.delete(group)
        db.commit()

        return {
            "ok": True,
            "deleted_group_id": group_id,
            "cleared_employee_count": cleared_employee_count,
            "deleted_geofence_count": deleted_geofence_count,
        }
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Cannot delete group due to related data")


@router.post("/{group_id}/geofences", response_model=GroupGeofenceResponse)
def create_group_geofence(
    group_id: int,
    payload: GroupGeofenceCreateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    _get_group_or_404(db, group_id)

    geofence = GroupGeofence(
        group_id=group_id,
        name=payload.name,
        latitude=payload.latitude,
        longitude=payload.longitude,
        radius_m=payload.radius_m,
        active=payload.active,
    )

    db.add(geofence)
    db.commit()
    db.refresh(geofence)
    return geofence


@router.get("/{group_id}/geofences", response_model=list[GroupGeofenceResponse])
def list_group_geofences(
    group_id: int,
    active_only: bool = False,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    _get_group_or_404(db, group_id)

    query = db.query(GroupGeofence).filter(GroupGeofence.group_id == group_id)
    if active_only:
        query = query.filter(GroupGeofence.active.is_(True))

    return query.order_by(GroupGeofence.id.asc()).all()


@router.put("/{group_id}/geofences/{geofence_id}", response_model=GroupGeofenceResponse)
def update_group_geofence(
    group_id: int,
    geofence_id: int,
    payload: GroupGeofenceUpdateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    geofence = _get_geofence_or_404(db, group_id, geofence_id)

    if payload.name is not None:
        geofence.name = payload.name
    if payload.latitude is not None:
        geofence.latitude = payload.latitude
    if payload.longitude is not None:
        geofence.longitude = payload.longitude
    if payload.radius_m is not None:
        geofence.radius_m = payload.radius_m
    if payload.active is not None:
        geofence.active = payload.active

    db.commit()
    db.refresh(geofence)
    return geofence


@router.delete("/{group_id}/geofences/{geofence_id}")
def delete_group_geofence(
    group_id: int,
    geofence_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    geofence = _get_geofence_or_404(db, group_id, geofence_id)
    db.delete(geofence)
    db.commit()
    return {"ok": True, "deleted_id": geofence_id}


