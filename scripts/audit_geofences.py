from __future__ import annotations

from dataclasses import dataclass

from app.core.db import SessionLocal
from app.core.policy import (
    AUDIT_MAX_DISTANCE_FROM_ACTIVE_RULE_M,
    MAX_GEOFENCE_RADIUS_M,
    MIN_GEOFENCE_RADIUS_M,
    VN_LAT_MAX,
    VN_LAT_MIN,
    VN_LNG_MAX,
    VN_LNG_MIN,
    WARN_GEOFENCE_RADIUS_M,
)
from app.models import CheckinRule, Group, GroupGeofence
from app.services.geo import haversine_m


@dataclass
class AuditIssue:
    geofence_id: int
    group_code: str
    geofence_name: str
    code: str
    detail: str


def _collect_issues() -> list[AuditIssue]:
    db = SessionLocal()
    try:
        active_rule = db.query(CheckinRule).filter(CheckinRule.active.is_(True)).first()
        geofences = (
            db.query(GroupGeofence, Group)
            .join(Group, Group.id == GroupGeofence.group_id)
            .order_by(GroupGeofence.id.asc())
            .all()
        )

        issues: list[AuditIssue] = []
        for geofence, group in geofences:
            if geofence.radius_m < MIN_GEOFENCE_RADIUS_M or geofence.radius_m > MAX_GEOFENCE_RADIUS_M:
                issues.append(
                    AuditIssue(
                        geofence_id=geofence.id,
                        group_code=group.code,
                        geofence_name=geofence.name,
                        code="RADIUS_OUT_OF_BOUNDS",
                        detail=(
                            f"radius_m={geofence.radius_m} outside [{MIN_GEOFENCE_RADIUS_M}, {MAX_GEOFENCE_RADIUS_M}]"
                        ),
                    )
                )
            elif geofence.radius_m > WARN_GEOFENCE_RADIUS_M:
                issues.append(
                    AuditIssue(
                        geofence_id=geofence.id,
                        group_code=group.code,
                        geofence_name=geofence.name,
                        code="RADIUS_ABOVE_POLICY_THRESHOLD",
                        detail=f"radius_m={geofence.radius_m} > {WARN_GEOFENCE_RADIUS_M}",
                    )
                )

            if not (VN_LAT_MIN <= geofence.latitude <= VN_LAT_MAX) or not (
                VN_LNG_MIN <= geofence.longitude <= VN_LNG_MAX
            ):
                issues.append(
                    AuditIssue(
                        geofence_id=geofence.id,
                        group_code=group.code,
                        geofence_name=geofence.name,
                        code="COORD_OUTSIDE_VN_BOUNDS",
                        detail=(
                            f"lat={geofence.latitude}, lng={geofence.longitude} outside VN bounds "
                            f"lat[{VN_LAT_MIN}, {VN_LAT_MAX}], lng[{VN_LNG_MIN}, {VN_LNG_MAX}]"
                        ),
                    )
                )

            if active_rule is not None:
                distance_from_rule = haversine_m(
                    geofence.latitude,
                    geofence.longitude,
                    active_rule.latitude,
                    active_rule.longitude,
                )
                if distance_from_rule > AUDIT_MAX_DISTANCE_FROM_ACTIVE_RULE_M:
                    issues.append(
                        AuditIssue(
                            geofence_id=geofence.id,
                            group_code=group.code,
                            geofence_name=geofence.name,
                            code="FAR_FROM_ACTIVE_RULE",
                            detail=(
                                f"distance={distance_from_rule:.1f}m > {AUDIT_MAX_DISTANCE_FROM_ACTIVE_RULE_M}m"
                            ),
                        )
                    )

        return issues
    finally:
        db.close()


def main() -> None:
    issues = _collect_issues()
    if not issues:
        print("No geofence policy issues found.")
        return

    print(f"Found {len(issues)} geofence issues:")
    for issue in issues:
        print(
            f"- geofence_id={issue.geofence_id} group={issue.group_code} "
            f"name='{issue.geofence_name}' code={issue.code} detail={issue.detail}"
        )


if __name__ == "__main__":
    main()
