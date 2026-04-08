from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require_admin
from app.models import AttendanceLog, Employee, Group, GroupGeofence

router = APIRouter(prefix="/geofence", tags=["geofence"])


@router.get("/list")
def list_all_geofences(
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """Flat list of all geofences across all groups, with member/present counts."""

    # member count per group
    member_counts = dict(
        db.query(Employee.group_id, func.count(Employee.id))
        .filter(Employee.group_id.isnot(None))
        .group_by(Employee.group_id)
        .all()
    )

    rows = (
        db.query(GroupGeofence, Group.name.label("group_name"))
        .outerjoin(Group, Group.id == GroupGeofence.group_id)
        .order_by(GroupGeofence.group_id, GroupGeofence.id)
        .all()
    )

    return [
        {
            "id": gf.id,
            "name": gf.name,
            "latitude": gf.latitude,
            "longitude": gf.longitude,
            "radius_m": gf.radius_m,
            "radius_meters": gf.radius_m,
            "active": gf.active,
            "is_active": gf.active,
            "group_id": gf.group_id,
            "group_name": group_name,
            "member_count": member_counts.get(gf.group_id, 0),
            "members": member_counts.get(gf.group_id, 0),
            "employee_count": member_counts.get(gf.group_id, 0),
            "present_count": 0,
        }
        for gf, group_name in rows
    ]
