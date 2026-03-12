import os
import sqlite3
import unittest
from unittest.mock import patch
from datetime import date, datetime, time, timezone
from io import BytesIO
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./test_minimum_flows.db"
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-16")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "60")

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import event

from app.core.db import Base, SessionLocal, engine
from app.core.security import hash_password
from app.main import app
from app.models import AttendanceLog, CheckinRule, Employee, Group, GroupGeofence, User


class _BoolOr:
    def __init__(self) -> None:
        self.value = False

    def step(self, item) -> None:
        if item:
            self.value = True

    def finalize(self) -> int:
        return 1 if self.value else 0


class _FixedDateTime(datetime):
    fixed_now: datetime | None = None

    @classmethod
    def now(cls, tz=None):
        if cls.fixed_now is None:
            return super().now(tz=tz)
        if tz is None:
            return cls.fixed_now.replace(tzinfo=None)
        return cls.fixed_now.astimezone(tz)


@event.listens_for(engine, "connect")
def _register_sqlite_bool_or(dbapi_connection, _connection_record) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.create_aggregate("bool_or", 1, _BoolOr)


class MinimumFlowsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db_path = Path("test_minimum_flows.db")
        if cls.db_path.exists():
            cls.db_path.unlink()

        engine.dispose()
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        engine.dispose()
        if cls.db_path.exists():
            cls.db_path.unlink()

    def setUp(self) -> None:
        with SessionLocal() as db:
            db.query(AttendanceLog).delete()
            db.query(Employee).delete()
            db.query(GroupGeofence).delete()
            db.query(Group).delete()
            db.query(CheckinRule).delete()
            db.query(User).delete()
            db.commit()

    def _create_user(self, email: str, password: str, role: str) -> User:
        with SessionLocal() as db:
            user = User(email=email, password_hash=hash_password(password), role=role)
            db.add(user)
            db.commit()
            db.refresh(user)
            return user

    def _create_group(
        self,
        code: str,
        name: str,
        active: bool = True,
        start_time: time | None = None,
        grace_minutes: int | None = None,
        end_time: time | None = None,
        checkout_grace_minutes: int | None = None,
    ) -> Group:
        with SessionLocal() as db:
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
            db.commit()
            db.refresh(group)
            return group

    def _create_geofence(
        self,
        group_id: int,
        name: str,
        latitude: float,
        longitude: float,
        radius_m: int,
        active: bool = True,
    ) -> GroupGeofence:
        with SessionLocal() as db:
            geofence = GroupGeofence(
                group_id=group_id,
                name=name,
                latitude=latitude,
                longitude=longitude,
                radius_m=radius_m,
                active=active,
            )
            db.add(geofence)
            db.commit()
            db.refresh(geofence)
            return geofence

    def _create_employee(
        self,
        code: str,
        full_name: str,
        user_id: int | None,
        group_id: int | None = None,
    ) -> Employee:
        with SessionLocal() as db:
            emp = Employee(code=code, full_name=full_name, user_id=user_id, group_id=group_id)
            db.add(emp)
            db.commit()
            db.refresh(emp)
            return emp

    def _create_rule(self, latitude: float = 10.7769, longitude: float = 106.7009, radius_m: int = 200) -> CheckinRule:
        with SessionLocal() as db:
            rule = CheckinRule(latitude=latitude, longitude=longitude, radius_m=radius_m, active=True)
            db.add(rule)
            db.commit()
            db.refresh(rule)
            return rule

    def _login(self, email: str, password: str) -> str:
        response = self.client.post("/auth/login", json={"email": email, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("access_token", body)
        return body["access_token"]

    def test_register_login_flow(self) -> None:
        email = "flow_user@example.com"
        password = "user123"

        register_res = self.client.post("/auth/register", json={"email": email, "password": password})
        self.assertEqual(register_res.status_code, 201, register_res.text)
        self.assertEqual(register_res.json()["role"], "USER")

        token = self._login(email, password)

        me_res = self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_res.status_code, 200, me_res.text)
        self.assertEqual(me_res.json()["email"], email)

    def test_set_rule_flow(self) -> None:
        self._create_user(email="admin@example.com", password="admin123", role="ADMIN")
        admin_token = self._login("admin@example.com", "admin123")

        put_res = self.client.put(
            "/rules/active",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "lat": 10.7769,
                "lng": 106.7009,
                "radius": 250,
                "start_time": "08:00",
                "grace_minutes": 30,
                "end_time": "17:30",
                "checkout_grace_minutes": 10,
            },
        )
        self.assertEqual(put_res.status_code, 200, put_res.text)
        self.assertEqual(put_res.json()["radius_m"], 250)
        self.assertEqual(put_res.json()["end_time"], "17:30")
        self.assertEqual(put_res.json()["checkout_grace_minutes"], 10)

        get_res = self.client.get(
            "/rules/active",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(get_res.status_code, 200, get_res.text)
        self.assertEqual(get_res.json()["radius_m"], 250)

    def test_group_time_rule_crud_flow(self) -> None:
        self._create_user(email="admin_group_time@example.com", password="admin123", role="ADMIN")
        admin_token = self._login("admin_group_time@example.com", "admin123")
        headers = {"Authorization": f"Bearer {admin_token}"}

        create_res = self.client.post(
            "/groups",
            headers=headers,
            json={
                "code": "GTM_API",
                "name": "Group Time API",
                "start_time": "09:00",
                "grace_minutes": 10,
                "end_time": "18:00",
                "checkout_grace_minutes": 5,
                "active": True,
            },
        )
        self.assertEqual(create_res.status_code, 200, create_res.text)
        body = create_res.json()
        self.assertEqual(body["start_time"], "09:00")
        self.assertEqual(body["grace_minutes"], 10)
        self.assertEqual(body["end_time"], "18:00")
        self.assertEqual(body["checkout_grace_minutes"], 5)

        group_id = body["id"]
        update_res = self.client.put(
            f"/groups/{group_id}",
            headers=headers,
            json={
                "grace_minutes": 25,
                "checkout_grace_minutes": 20,
                "start_time": None,
            },
        )
        self.assertEqual(update_res.status_code, 200, update_res.text)
        updated = update_res.json()
        self.assertIsNone(updated["start_time"])
        self.assertEqual(updated["grace_minutes"], 25)
        self.assertEqual(updated["checkout_grace_minutes"], 20)

    def test_checkin_checkout_flow(self) -> None:
        user = self._create_user(email="staff@example.com", password="staff123", role="USER")
        self._create_employee(code="EM001", full_name="Staff One", user_id=user.id)
        self._create_rule()

        token = self._login("staff@example.com", "staff123")
        headers = {"Authorization": f"Bearer {token}"}

        checkin_res = self.client.post(
            "/attendance/checkin",
            headers=headers,
            json={"lat": 10.7769, "lng": 106.7009},
        )
        self.assertEqual(checkin_res.status_code, 200, checkin_res.text)
        self.assertEqual(checkin_res.json()["log"]["type"], "IN")
        self.assertIn(checkin_res.json()["log"].get("punctuality_status"), {"EARLY", "ON_TIME", "LATE"})

        checkout_res = self.client.post(
            "/attendance/checkout",
            headers=headers,
            json={"lat": 10.7769, "lng": 106.7009},
        )
        self.assertEqual(checkout_res.status_code, 200, checkout_res.text)
        self.assertEqual(checkout_res.json()["log"]["type"], "OUT")
        self.assertIn(checkout_res.json()["log"].get("checkout_status"), {"EARLY", "ON_TIME", "LATE"})

    def test_group_geofence_flow(self) -> None:
        user = self._create_user(email="group_user@example.com", password="user123", role="USER")

        group_a = self._create_group("GA", "Group A")
        group_b = self._create_group("GB", "Group B")

        self._create_geofence(group_a.id, "Gate", 10.7769, 106.7009, 200)
        self._create_geofence(group_a.id, "Annex", 10.7774, 106.7014, 200)
        self._create_geofence(group_b.id, "Warehouse", 10.7905, 106.5950, 300)

        self._create_employee(code="EM003", full_name="Group User", user_id=user.id, group_id=group_a.id)
        self._create_rule()  # fallback + timing source

        token = self._login("group_user@example.com", "user123")
        headers = {"Authorization": f"Bearer {token}"}

        checkin_res = self.client.post(
            "/attendance/checkin",
            headers=headers,
            json={"lat": 10.7774, "lng": 106.7014},
        )
        self.assertEqual(checkin_res.status_code, 200, checkin_res.text)
        self.assertFalse(checkin_res.json()["log"]["is_out_of_range"])
        self.assertEqual(checkin_res.json()["geofence_source"], "GROUP")
        self.assertIsNone(checkin_res.json()["fallback_reason"])

        checkout_res = self.client.post(
            "/attendance/checkout",
            headers=headers,
            json={"lat": 10.7905, "lng": 106.5950},
        )
        self.assertEqual(checkout_res.status_code, 200, checkout_res.text)
        self.assertTrue(checkout_res.json()["log"]["is_out_of_range"])
        self.assertEqual(checkout_res.json()["geofence_source"], "GROUP")
        self.assertIsNone(checkout_res.json()["fallback_reason"])

    def test_group_time_rule_overrides_system_rule(self) -> None:
        user = self._create_user(email="group_time_user@example.com", password="user123", role="USER")
        group = self._create_group(
            "GTIME",
            "Group Time",
            start_time=time(9, 0),
            grace_minutes=5,
            end_time=time(17, 0),
            checkout_grace_minutes=0,
        )
        self._create_geofence(group.id, "Time Gate", 10.7769, 106.7009, 250)
        self._create_employee(code="EM006", full_name="Group Time User", user_id=user.id, group_id=group.id)

        # System fallback rule remains different, so we can assert override behavior.
        self._create_rule(latitude=10.7769, longitude=106.7009, radius_m=300)

        token = self._login("group_time_user@example.com", "user123")
        headers = {"Authorization": f"Bearer {token}"}

        _FixedDateTime.fixed_now = datetime(2026, 3, 11, 1, 5, tzinfo=timezone.utc)  # 08:05 VN
        with patch("app.api.attendance.datetime", _FixedDateTime):
            checkin_res = self.client.post(
                "/attendance/checkin",
                headers=headers,
                json={"lat": 10.7769, "lng": 106.7009},
            )
        self.assertEqual(checkin_res.status_code, 200, checkin_res.text)
        self.assertEqual(checkin_res.json()["log"]["punctuality_status"], "EARLY")

        _FixedDateTime.fixed_now = datetime(2026, 3, 11, 10, 10, tzinfo=timezone.utc)  # 17:10 VN
        with patch("app.api.attendance.datetime", _FixedDateTime):
            checkout_res = self.client.post(
                "/attendance/checkout",
                headers=headers,
                json={"lat": 10.7769, "lng": 106.7009},
            )
        self.assertEqual(checkout_res.status_code, 200, checkout_res.text)
        self.assertEqual(checkout_res.json()["log"]["checkout_status"], "LATE")

        _FixedDateTime.fixed_now = None

    def test_employee_without_group_uses_active_rule_fallback(self) -> None:
        user = self._create_user(email="nogroup_user@example.com", password="user123", role="USER")
        self._create_employee(code="EM004", full_name="No Group User", user_id=user.id, group_id=None)
        self._create_rule(latitude=10.7769, longitude=106.7009, radius_m=300)

        token = self._login("nogroup_user@example.com", "user123")
        headers = {"Authorization": f"Bearer {token}"}

        checkin_res = self.client.post(
            "/attendance/checkin",
            headers=headers,
            json={"lat": 10.7769, "lng": 106.7009},
        )
        self.assertEqual(checkin_res.status_code, 200, checkin_res.text)
        self.assertFalse(checkin_res.json()["log"]["is_out_of_range"])
        self.assertEqual(checkin_res.json()["log"]["matched_geofence"], "SYSTEM_RULE")
        self.assertEqual(checkin_res.json()["geofence_source"], "SYSTEM_FALLBACK")
        self.assertEqual(checkin_res.json()["fallback_reason"], "EMPLOYEE_NOT_ASSIGNED_GROUP")

        checkout_res = self.client.post(
            "/attendance/checkout",
            headers=headers,
            json={"lat": 10.7905, "lng": 106.5950},
        )
        self.assertEqual(checkout_res.status_code, 200, checkout_res.text)
        self.assertTrue(checkout_res.json()["log"]["is_out_of_range"])
        self.assertIsNone(checkout_res.json()["log"]["matched_geofence"])
        self.assertEqual(checkout_res.json()["geofence_source"], "SYSTEM_FALLBACK")
        self.assertEqual(checkout_res.json()["fallback_reason"], "EMPLOYEE_NOT_ASSIGNED_GROUP")

    def test_daily_report_contains_fallback_source(self) -> None:
        admin = self._create_user(email="admin_daily_report@example.com", password="admin123", role="ADMIN")
        user = self._create_user(email="daily_report_user@example.com", password="user123", role="USER")

        self._create_employee(code="EM009", full_name="Daily Report User", user_id=user.id, group_id=None)
        self._create_rule(latitude=10.7769, longitude=106.7009, radius_m=300)

        user_token = self._login("daily_report_user@example.com", "user123")
        user_headers = {"Authorization": f"Bearer {user_token}"}

        in_res = self.client.post(
            "/attendance/checkin",
            headers=user_headers,
            json={"lat": 10.7769, "lng": 106.7009},
        )
        self.assertEqual(in_res.status_code, 200, in_res.text)

        out_res = self.client.post(
            "/attendance/checkout",
            headers=user_headers,
            json={"lat": 10.7905, "lng": 106.5950},
        )
        self.assertEqual(out_res.status_code, 200, out_res.text)

        admin_token = self._login("admin_daily_report@example.com", "admin123")
        report_res = self.client.get(
            "/attendance/report/daily",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(report_res.status_code, 200, report_res.text)

        rows = report_res.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["employee_code"], "EM009")
        self.assertEqual(rows[0]["geofence_source"], "SYSTEM_FALLBACK")
        self.assertEqual(rows[0]["fallback_reason"], "EMPLOYEE_NOT_ASSIGNED_GROUP")
    def test_group_inactive_uses_system_fallback(self) -> None:
        user = self._create_user(email="inactive_group_user@example.com", password="user123", role="USER")
        group = self._create_group("GINACT", "Inactive Group", active=False)
        self._create_geofence(group.id, "Inactive Gate", 10.7769, 106.7009, 500, active=True)
        self._create_employee(code="EM007", full_name="Inactive Group User", user_id=user.id, group_id=group.id)
        self._create_rule(latitude=10.7769, longitude=106.7009, radius_m=300)

        token = self._login("inactive_group_user@example.com", "user123")
        headers = {"Authorization": f"Bearer {token}"}

        checkin_res = self.client.post(
            "/attendance/checkin",
            headers=headers,
            json={"lat": 10.7769, "lng": 106.7009},
        )
        self.assertEqual(checkin_res.status_code, 200, checkin_res.text)
        self.assertEqual(checkin_res.json()["geofence_source"], "SYSTEM_FALLBACK")
        self.assertEqual(checkin_res.json()["fallback_reason"], "GROUP_INACTIVE_OR_NOT_FOUND")

    def test_group_without_active_geofence_uses_system_fallback(self) -> None:
        user = self._create_user(email="no_active_geofence_user@example.com", password="user123", role="USER")
        group = self._create_group("GNOFG", "No Active Geofence Group", active=True)
        self._create_geofence(group.id, "Inactive Geofence", 10.7769, 106.7009, 500, active=False)
        self._create_employee(code="EM008", full_name="No Active Geofence User", user_id=user.id, group_id=group.id)
        self._create_rule(latitude=10.7769, longitude=106.7009, radius_m=300)

        token = self._login("no_active_geofence_user@example.com", "user123")
        headers = {"Authorization": f"Bearer {token}"}

        checkin_res = self.client.post(
            "/attendance/checkin",
            headers=headers,
            json={"lat": 10.7769, "lng": 106.7009},
        )
        self.assertEqual(checkin_res.status_code, 200, checkin_res.text)
        self.assertEqual(checkin_res.json()["geofence_source"], "SYSTEM_FALLBACK")
        self.assertEqual(checkin_res.json()["fallback_reason"], "NO_ACTIVE_GEOFENCE_IN_GROUP")

    def test_delete_group_flow(self) -> None:
        admin = self._create_user(email="admin_group@example.com", password="admin123", role="ADMIN")
        user = self._create_user(email="user_group@example.com", password="user123", role="USER")

        group = self._create_group("DEL", "Delete Group")
        self._create_geofence(group.id, "Delete Gate", 10.7769, 106.7009, 300)
        employee = self._create_employee(code="EM005", full_name="Delete User", user_id=user.id, group_id=group.id)

        admin_token = self._login("admin_group@example.com", "admin123")
        res = self.client.delete(
            f"/groups/{group.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(res.status_code, 200, res.text)

        with SessionLocal() as db:
            refreshed_emp = db.query(Employee).filter(Employee.id == employee.id).first()
            self.assertIsNotNone(refreshed_emp)
            self.assertIsNone(refreshed_emp.group_id)

            deleted_group = db.query(Group).filter(Group.id == group.id).first()
            self.assertIsNone(deleted_group)

            deleted_geofence = db.query(GroupGeofence).filter(GroupGeofence.group_id == group.id).first()
            self.assertIsNone(deleted_geofence)

    def test_export_report_flow(self) -> None:
        admin = self._create_user(email="admin_report@example.com", password="admin123", role="ADMIN")
        user = self._create_user(email="user_report@example.com", password="user123", role="USER")

        report_group = self._create_group("REP", "Report Group")
        self._create_geofence(report_group.id, "Report Gate", 10.7769, 106.7009, 300)

        self._create_employee(code="AD001", full_name="Admin", user_id=admin.id)
        self._create_employee(code="EM002", full_name="User Report", user_id=user.id, group_id=report_group.id)
        self._create_rule()

        user_token = self._login("user_report@example.com", "user123")
        user_headers = {"Authorization": f"Bearer {user_token}"}

        in_res = self.client.post(
            "/attendance/checkin",
            headers=user_headers,
            json={"lat": 10.7769, "lng": 106.7009},
        )
        self.assertEqual(in_res.status_code, 200, in_res.text)

        out_res = self.client.post(
            "/attendance/checkout",
            headers=user_headers,
            json={"lat": 10.7769, "lng": 106.7009},
        )
        self.assertEqual(out_res.status_code, 200, out_res.text)

        admin_token = self._login("admin_report@example.com", "admin123")
        today = date.today().isoformat()

        report_res = self.client.get(
            f"/reports/attendance.xlsx?from={today}&to={today}&group_id={report_group.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(report_res.status_code, 200, report_res.text)
        self.assertIn(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            report_res.headers.get("content-type", ""),
        )
        self.assertTrue(report_res.content.startswith(b"PK"))

        workbook = load_workbook(BytesIO(report_res.content))
        worksheet = workbook.active

        headers = [cell.value for cell in worksheet[1]]
        for required_header in ("group_code", "group_name", "matched_geofence", "geofence_source", "fallback_reason"):
            self.assertIn(required_header, headers)

        header_index = {name: idx + 1 for idx, name in enumerate(headers)}
        self.assertGreaterEqual(worksheet.max_row, 2)

        self.assertEqual(worksheet.cell(row=2, column=header_index["group_code"]).value, "REP")
        self.assertEqual(worksheet.cell(row=2, column=header_index["group_name"]).value, "Report Group")
        self.assertEqual(worksheet.cell(row=2, column=header_index["matched_geofence"]).value, "Report Gate")
        self.assertEqual(worksheet.cell(row=2, column=header_index["geofence_source"]).value, "GROUP")
        self.assertIsNone(worksheet.cell(row=2, column=header_index["fallback_reason"]).value)
        self.assertEqual(worksheet.cell(row=2, column=header_index["out_of_range"]).value, "IN_RANGE")


if __name__ == "__main__":
    unittest.main()

















