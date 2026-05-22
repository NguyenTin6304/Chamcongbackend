"""Phase 4 — `GET /attendance/me/stats?month=YYYY-MM` integration tests.

Verifies attendance counts, leave-day overlap, working-day calculation,
and Plan-B worked minutes (regular + approved OT) for one employee.
"""
import os
import sqlite3
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///./test_me_stats.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-16")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
os.environ.setdefault("EXCEPTION_WORKFLOW_SYSTEM_KEY", "test-stats-system-key")

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.db import Base, SessionLocal, engine
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import (
    AttendanceException,
    AttendanceExceptionAudit,
    AttendanceExceptionNotification,
    AttendanceLog,
    CheckinRule,
    Employee,
    Group,
    GroupGeofence,
    LeaveRequest,
    OvertimeAudit,
    OvertimeRecord,
    PasswordResetToken,
    PublicHoliday,
    RefreshToken,
    User,
)

UTC = timezone.utc
VN = timezone(timedelta(hours=7))


def _utc(d: date, h: int, m: int = 0) -> datetime:
    """VN wall-clock time → UTC-aware datetime (matches storage round-trip)."""
    return datetime.combine(d, time(h, m), tzinfo=VN).astimezone(UTC)


class _BoolOr:
    def __init__(self) -> None:
        self.value = False

    def step(self, item) -> None:
        if item:
            self.value = True

    def finalize(self) -> int:
        return 1 if self.value else 0


@event.listens_for(engine, "connect")
def _register_sqlite_bool_or(dbapi_connection, _connection_record) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.create_aggregate("bool_or", 1, _BoolOr)


class MeStatsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db_path = Path("test_me_stats.db")
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
            db.query(OvertimeAudit).delete()
            db.query(OvertimeRecord).delete()
            db.query(AttendanceExceptionNotification).delete()
            db.query(AttendanceExceptionAudit).delete()
            db.query(AttendanceException).delete()
            db.query(AttendanceLog).delete()
            db.query(LeaveRequest).delete()
            db.query(PublicHoliday).delete()
            db.query(RefreshToken).delete()
            db.query(PasswordResetToken).delete()
            db.query(Employee).delete()
            db.query(GroupGeofence).delete()
            db.query(Group).delete()
            db.query(CheckinRule).delete()
            db.query(User).delete()
            db.commit()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _seed(
        self,
        *,
        annual_leave_days: float | None = 12.0,
    ) -> tuple[User, Employee, str]:
        with SessionLocal() as db:
            db.add(CheckinRule(
                latitude=10.7769, longitude=106.7009, radius_m=200,
                start_time=time(8, 0), end_time=time(17, 0),
                grace_minutes=10, checkout_grace_minutes=0,
                cross_day_cutoff_minutes=240,
                overtime_enabled=True, overtime_minimum_minutes=30,
                active=True,
            ))
            user = User(email="emp@test.com", password_hash=hash_password("pw"), role="employee")
            db.add(user)
            db.commit()
            db.refresh(user)
            emp = Employee(
                code="E001", full_name="Test Employee",
                user_id=user.id, annual_leave_days=annual_leave_days,
            )
            db.add(emp)
            db.commit()
            db.refresh(emp)
            user_id, emp_id = user.id, emp.id

        token = create_access_token({"sub": str(user_id), "role": "employee"})
        with SessionLocal() as db:
            user = db.get(User, user_id)
            emp = db.get(Employee, emp_id)
        return user, emp, token

    def _add_log(
        self,
        *,
        employee_id: int,
        work_date: date,
        happened_at: datetime,
        log_type: str,
        punctuality: str | None = None,
    ) -> int:
        with SessionLocal() as db:
            log = AttendanceLog(
                employee_id=employee_id, type=log_type,
                time=happened_at, work_date=work_date,
                lat=10.7769, lng=106.7009, is_out_of_range=False,
                punctuality_status=punctuality if log_type == "IN" else None,
                checkout_status="ON_TIME" if log_type == "OUT" else None,
            )
            db.add(log)
            db.commit()
            db.refresh(log)
            return log.id

    def _add_ot(
        self,
        *,
        employee_id: int,
        work_date: date,
        raw_minutes: int,
        approved_minutes: int | None,
        status: str,
    ) -> None:
        with SessionLocal() as db:
            db.add(OvertimeRecord(
                employee_id=employee_id, work_date=work_date,
                raw_minutes=raw_minutes, approved_minutes=approved_minutes,
                status=status, source="AUTO_CHECKOUT",
                shift_start_snapshot=time(8, 0), shift_end_snapshot=time(17, 0),
                is_weekend=False, is_holiday=False,
            ))
            db.commit()

    # ── tests ────────────────────────────────────────────────────────────────

    def test_invalid_month_format_returns_422(self) -> None:
        _, _, token = self._seed()
        res = self.client.get(
            "/attendance/me/stats?month=2026/05",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(res.status_code, 422, res.text)

    def test_empty_month_returns_zeroes(self) -> None:
        """No logs/leaves/OT in the queried month → all counts zero."""
        _, _, token = self._seed()
        res = self.client.get(
            "/attendance/me/stats?month=2026-05",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["month"], "2026-05")
        self.assertEqual(body["checkins_total"], 0)
        self.assertEqual(body["checkins_on_time"], 0)
        self.assertEqual(body["checkins_late"], 0)
        self.assertEqual(body["leave_days_used"], 0.0)
        self.assertEqual(body["total_worked_minutes"], 0)

    def test_counts_distinct_workdates_with_punctuality(self) -> None:
        """Each work_date contributes one to checkins_total and one to its bucket."""
        _, emp, token = self._seed()
        # 2 ON_TIME days, 1 LATE day
        self._add_log(employee_id=emp.id, work_date=date(2026, 5, 4),
                      happened_at=_utc(date(2026, 5, 4), 8), log_type="IN", punctuality="ON_TIME")
        self._add_log(employee_id=emp.id, work_date=date(2026, 5, 5),
                      happened_at=_utc(date(2026, 5, 5), 8), log_type="IN", punctuality="ON_TIME")
        self._add_log(employee_id=emp.id, work_date=date(2026, 5, 6),
                      happened_at=_utc(date(2026, 5, 6), 9), log_type="IN", punctuality="LATE")

        # Past month — period_end = last day, no future cap
        res = self.client.get(
            "/attendance/me/stats?month=2026-05",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["checkins_total"], 3)
        self.assertEqual(body["checkins_on_time"], 2)
        self.assertEqual(body["checkins_late"], 1)

    def test_plan_b_worked_minutes_includes_only_approved_ot(self) -> None:
        """worked = regular + approved_ot. PENDING OT is reported separately, not in worked."""
        _, emp, token = self._seed()
        wd = date(2026, 5, 4)
        # Checkin 08:00, Checkout 18:00 → regular=540, raw_ot=60
        self._add_log(employee_id=emp.id, work_date=wd,
                      happened_at=_utc(wd, 8), log_type="IN", punctuality="ON_TIME")
        self._add_log(employee_id=emp.id, work_date=wd,
                      happened_at=_utc(wd, 18), log_type="OUT")
        # Admin only approves 30 min of the 60 raw
        self._add_ot(employee_id=emp.id, work_date=wd,
                     raw_minutes=60, approved_minutes=30, status="APPROVED")

        # A second day with PENDING OT — must not contribute to worked
        wd2 = date(2026, 5, 5)
        self._add_log(employee_id=emp.id, work_date=wd2,
                      happened_at=_utc(wd2, 8), log_type="IN", punctuality="ON_TIME")
        self._add_log(employee_id=emp.id, work_date=wd2,
                      happened_at=_utc(wd2, 19), log_type="OUT")
        self._add_ot(employee_id=emp.id, work_date=wd2,
                     raw_minutes=120, approved_minutes=None, status="PENDING")

        res = self.client.get(
            "/attendance/me/stats?month=2026-05",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        # regular: 540 (day1) + 540 (day2) = 1080
        self.assertEqual(body["total_regular_minutes"], 1080)
        # approved OT only: 30 (PENDING 120 not counted)
        self.assertEqual(body["total_approved_overtime_minutes"], 30)
        # worked = regular + approved
        self.assertEqual(body["total_worked_minutes"], 1110)
        # pending reported separately
        self.assertEqual(body["total_pending_overtime_minutes"], 120)

    def test_leave_days_overlap_clipped_to_month_boundary(self) -> None:
        """Leave that spans month boundary: only days inside the month count."""
        _, emp, token = self._seed()
        # Approved leave 2026-04-29 → 2026-05-04 (6 days; 4 of them in May)
        with SessionLocal() as db:
            db.add(LeaveRequest(
                employee_id=emp.id, leave_type="PAID",
                start_date=date(2026, 4, 29), end_date=date(2026, 5, 4),
                status="APPROVED", reason="test",
            ))
            db.commit()

        res = self.client.get(
            "/attendance/me/stats?month=2026-05",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        # May 1, 2, 3, 4 = 4 days inside the month
        self.assertEqual(body["leave_days_used"], 4.0)

    def test_unlimited_quota_returns_null_remaining(self) -> None:
        """Employee with annual_leave_days=NULL → leave_balance_remaining=null."""
        _, _, token = self._seed(annual_leave_days=None)
        res = self.client.get(
            "/attendance/me/stats?month=2026-05",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIsNone(body["annual_quota"])
        self.assertIsNone(body["leave_balance_remaining"])

    def test_working_days_excludes_weekends_holidays_and_leave(self) -> None:
        """April 2026: 22 weekdays. Holiday Apr 1 + leave Apr 6-7 → 19 working days.

        Use a fully-past month so period_end is the last day of month and the
        expected value isn't affected by the system clock.
        """
        _, emp, token = self._seed()
        with SessionLocal() as db:
            db.add(PublicHoliday(date=date(2026, 4, 1), name="Test Holiday"))
            db.add(LeaveRequest(
                employee_id=emp.id, leave_type="PAID",
                start_date=date(2026, 4, 6), end_date=date(2026, 4, 7),  # Mon-Tue
                status="APPROVED", reason="test",
            ))
            db.commit()

        res = self.client.get(
            "/attendance/me/stats?month=2026-04",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        # April 2026: 22 weekdays - 1 holiday - 2 leave = 19
        self.assertEqual(body["working_days"], 19)
        # No checkins logged → absent_days = working_days
        self.assertEqual(body["absent_days"], 19)
        # Leave used in April = 2
        self.assertEqual(body["leave_days_used"], 2.0)


if __name__ == "__main__":
    unittest.main()
