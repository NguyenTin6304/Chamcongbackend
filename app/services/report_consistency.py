from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import GroupGeofence

DISTANCE_CONSISTENCY_WARNING = "IN_RANGE_DISTANCE_EXCEEDS_RADIUS"


def load_group_geofence_radius_maps(
    db: Session,
    group_ids: set[int],
) -> tuple[dict[tuple[int, str], int], dict[int, int]]:
    if not group_ids:
        return {}, {}

    rows = (
        db.query(
            GroupGeofence.group_id,
            GroupGeofence.name,
            GroupGeofence.radius_m,
        )
        .filter(
            GroupGeofence.group_id.in_(group_ids),
            GroupGeofence.active.is_(True),
        )
        .all()
    )

    named_radius_map: dict[tuple[int, str], int] = {}
    max_radius_map: dict[int, int] = {}

    for row in rows:
        key = (int(row.group_id), str(row.name).strip().lower())
        existing_named = named_radius_map.get(key)
        if existing_named is None or int(row.radius_m) > existing_named:
            named_radius_map[key] = int(row.radius_m)

        existing_max = max_radius_map.get(int(row.group_id))
        if existing_max is None or int(row.radius_m) > existing_max:
            max_radius_map[int(row.group_id)] = int(row.radius_m)

    return named_radius_map, max_radius_map


def resolve_reference_radius_m(
    *,
    geofence_source: str | None,
    matched_geofence: str | None,
    group_id: int | None,
    fallback_radius_m: int | None,
    named_radius_map: dict[tuple[int, str], int],
    max_radius_map: dict[int, int],
) -> int | None:
    if geofence_source == "GROUP":
        if group_id is not None and matched_geofence:
            key = (int(group_id), matched_geofence.strip().lower())
            radius = named_radius_map.get(key)
            if radius is not None:
                return radius
        if group_id is not None:
            return max_radius_map.get(int(group_id))

    if geofence_source == "SYSTEM_FALLBACK":
        return fallback_radius_m

    return None


def compute_distance_consistency_warning(
    *,
    out_of_range: bool,
    avg_distance_m: float | None,
    max_distance_m: float | None,
    radius_m: int | None,
) -> str | None:
    if out_of_range or radius_m is None:
        return None

    if avg_distance_m is not None and avg_distance_m > radius_m:
        return DISTANCE_CONSISTENCY_WARNING
    if max_distance_m is not None and max_distance_m > radius_m:
        return DISTANCE_CONSISTENCY_WARNING

    return None
