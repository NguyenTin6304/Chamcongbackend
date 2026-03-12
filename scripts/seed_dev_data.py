import argparse
from datetime import time
import sys
from pathlib import Path

from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.db import Base, SessionLocal, engine
from app.core.security import hash_password
from app.models import CheckinRule, Employee, Group, GroupGeofence, User


def upsert_user(db: Session, email: str, password: str, role: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, password_hash=hash_password(password), role=role)
        db.add(user)
        db.flush()
        return user

    user.password_hash = hash_password(password)
    user.role = role
    db.flush()
    return user


def upsert_group(
    db: Session,
    code: str,
    name: str,
    active: bool = True,
    start_time=None,
    grace_minutes=None,
    end_time=None,
    checkout_grace_minutes=None,
) -> Group:
    group = db.query(Group).filter(Group.code == code).first()
    if not group:
        group = Group(
            code=code,
            name=name,
            active=active,
            start_time=start_time,
            grace_minutes=grace_minutes,
            end_time=end_time,
            checkout_grace_minutes=checkout_grace_minutes,
        )
        db.add(group)
        db.flush()
        return group

    group.name = name
    group.active = active
    group.start_time = start_time
    group.grace_minutes = grace_minutes
    group.end_time = end_time
    group.checkout_grace_minutes = checkout_grace_minutes
    db.flush()
    return group


def upsert_geofence(
    db: Session,
    group_id: int,
    name: str,
    latitude: float,
    longitude: float,
    radius_m: int,
    active: bool = True,
) -> GroupGeofence:
    geofence = db.query(GroupGeofence).filter(GroupGeofence.group_id == group_id, GroupGeofence.name == name).first()

    if not geofence:
        geofence = GroupGeofence(
            group_id=group_id,
            name=name,
            latitude=latitude,
            longitude=longitude,
            radius_m=radius_m,
            active=active,
        )
        db.add(geofence)
        db.flush()
        return geofence

    geofence.latitude = latitude
    geofence.longitude = longitude
    geofence.radius_m = radius_m
    geofence.active = active
    db.flush()
    return geofence


def upsert_employee(
    db: Session,
    code: str,
    full_name: str,
    user_id: int | None,
    group_id: int | None,
) -> Employee:
    emp = db.query(Employee).filter(Employee.code == code).first()

    if user_id is not None:
        conflict = db.query(Employee).filter(Employee.user_id == user_id, Employee.code != code).first()
        if conflict:
            conflict.user_id = None

    if not emp:
        emp = Employee(code=code, full_name=full_name, user_id=user_id, group_id=group_id)
        db.add(emp)
        db.flush()
        return emp

    emp.full_name = full_name
    emp.user_id = user_id
    emp.group_id = group_id
    db.flush()
    return emp


def upsert_active_rule(db: Session, latitude: float, longitude: float, radius_m: int) -> CheckinRule:
    rule = db.query(CheckinRule).filter(CheckinRule.active.is_(True)).order_by(CheckinRule.id.asc()).first()

    if not rule:
        rule = db.query(CheckinRule).order_by(CheckinRule.id.asc()).first()

    if not rule:
        rule = CheckinRule(
            latitude=latitude,
            longitude=longitude,
            radius_m=radius_m,
            active=True,
        )
        db.add(rule)
        db.flush()
    else:
        rule.latitude = latitude
        rule.longitude = longitude
        rule.radius_m = radius_m
        rule.active = True
        db.flush()

    db.query(CheckinRule).filter(CheckinRule.id != rule.id).update(
        {CheckinRule.active: False}, synchronize_session=False
    )

    return rule


def seed(args: argparse.Namespace) -> None:
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        admin = upsert_user(db, args.admin_email, args.admin_password, "ADMIN")
        user_q1 = upsert_user(db, args.user_email, args.user_password, "USER")
        user_wh = upsert_user(db, args.warehouse_user_email, args.warehouse_user_password, "USER")

        group_q1 = upsert_group(
            db,
            "Q1_OFFICE",
            "Van phong quan 1",
            active=True,
            start_time=time(8, 0),
            grace_minutes=15,
            end_time=time(17, 30),
            checkout_grace_minutes=10,
        )
        group_bt = upsert_group(
            db,
            "BT_WAREHOUSE",
            "Kho Binh Tan",
            active=True,
            start_time=time(9, 0),
            grace_minutes=20,
            end_time=time(18, 0),
            checkout_grace_minutes=15,
        )

        upsert_geofence(db, group_q1.id, "Cong chinh", 10.7769, 106.7009, 250, active=True)
        upsert_geofence(db, group_q1.id, "Toa nha phu", 10.7774, 106.7014, 200, active=True)
        upsert_geofence(db, group_bt.id, "Kho trung tam", 10.7905, 106.5950, 300, active=True)

        admin_emp = upsert_employee(db, args.admin_employee_code, args.admin_full_name, admin.id, group_q1.id)
        user_emp = upsert_employee(db, args.user_employee_code, args.user_full_name, user_q1.id, group_q1.id)
        wh_emp = upsert_employee(db, args.warehouse_employee_code, args.warehouse_full_name, user_wh.id, group_bt.id)

        # Keep legacy system rule as fallback for old clients and ungrouped employees.
        rule = upsert_active_rule(db, args.rule_lat, args.rule_lng, args.rule_radius)

        db.commit()

        print("Seed completed")
        print(f"ADMIN user: {admin.email} (id={admin.id})")
        print(f"USER Q1:    {user_q1.email} (id={user_q1.id})")
        print(f"USER WH:    {user_wh.email} (id={user_wh.id})")
        print(f"Group A: {group_q1.code} ({group_q1.name}) time={group_q1.start_time}-{group_q1.end_time}")
        print("  - Cong chinh")
        print("  - Toa nha phu")
        print(f"Group B: {group_bt.code} ({group_bt.name}) time={group_bt.start_time}-{group_bt.end_time}")
        print("  - Kho trung tam")
        print(f"Employee: {admin_emp.code} -> user_id={admin_emp.user_id}, group_id={admin_emp.group_id}")
        print(f"Employee: {user_emp.code} -> user_id={user_emp.user_id}, group_id={user_emp.group_id}")
        print(f"Employee: {wh_emp.code} -> user_id={wh_emp.user_id}, group_id={wh_emp.group_id}")
        print(
            "Active fallback rule: "
            f"lat={rule.latitude}, lng={rule.longitude}, radius_m={rule.radius_m}, active={rule.active}"
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed admin/user/employee/group/geofence for local dev")

    parser.add_argument("--admin-email", default="admin@example.com")
    parser.add_argument("--admin-password", default="admin123")
    parser.add_argument("--admin-full-name", default="System Admin")
    parser.add_argument("--admin-employee-code", default="AD001")

    parser.add_argument("--user-email", default="user@example.com")
    parser.add_argument("--user-password", default="user123")
    parser.add_argument("--user-full-name", default="Demo User Q1")
    parser.add_argument("--user-employee-code", default="EM001")

    parser.add_argument("--warehouse-user-email", default="warehouse@example.com")
    parser.add_argument("--warehouse-user-password", default="warehouse123")
    parser.add_argument("--warehouse-full-name", default="Demo User Warehouse")
    parser.add_argument("--warehouse-employee-code", default="EM002")

    parser.add_argument("--rule-lat", type=float, default=10.7769)
    parser.add_argument("--rule-lng", type=float, default=106.7009)
    parser.add_argument("--rule-radius", type=int, default=200)

    return parser


if __name__ == "__main__":
    seed(build_parser().parse_args())





