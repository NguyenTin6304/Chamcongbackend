"""Phase 1 — Leave balance unit tests.

Covers the two PLAN.md test cases:
  1. Employee with quota=12 and 5 used → remaining=7
  2. Intern with quota=NULL → balance returns null remaining (unlimited)

Verifies that the `compute_leave_balance` helper agrees with what
/leave-requests/me/balance and /attendance/me/stats both consume.
"""
import os
import sqlite3
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///./test_leave_balance.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-16")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
os.environ.setdefault("EXCEPTION_WORKFLOW_SYSTEM_KEY", "test-leave-balance-key")

from sqlalchemy import event

from app.api.leave import compute_leave_balance
from app.core.db import Base, SessionLocal, engine
from app.core.security import hash_password
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


class LeaveBalanceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db_path = Path("test_leave_balance.db")
        if cls.db_path.exists():
            cls.db_path.unlink()
        engine.dispose()
        Base.metadata.create_all(bind=engine)

    @classmethod
    def tearDownClass(cls) -> None:
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

    def _make_employee(self, *, quota: float | None) -> int:
        with SessionLocal() as db:
            user = User(email="emp@test.com", password_hash=hash_password("pw"), role="employee")
            db.add(user)
            db.commit()
            db.refresh(user)
            emp = Employee(
                code="E001", full_name="Test Employee",
                user_id=user.id, annual_leave_days=quota,
            )
            db.add(emp)
            db.commit()
            db.refresh(emp)
            return emp.id

    def _add_paid_leave(
        self,
        emp_id: int,
        start: date,
        end: date,
        status: str = "APPROVED",
    ) -> None:
        with SessionLocal() as db:
            db.add(LeaveRequest(
                employee_id=emp_id, leave_type="PAID",
                start_date=start, end_date=end, status=status,
                reason="test",
            ))
            db.commit()

    def test_quota_12_used_5_remaining_7(self) -> None:
        """PLAN test case 1: quota=12, used=5 → remaining=7."""
        emp_id = self._make_employee(quota=12.0)
        # 5 approved leave days in 2026 (Mon-Fri)
        self._add_paid_leave(emp_id, date(2026, 6, 1), date(2026, 6, 5), "APPROVED")

        with SessionLocal() as db:
            emp = db.get(Employee, emp_id)
            balance = compute_leave_balance(emp, db, year=2026)

        self.assertEqual(balance.annual_quota, 12.0)
        self.assertEqual(balance.days_used, 5.0)
        self.assertEqual(balance.days_remaining, 7.0)
        self.assertEqual(balance.days_pending, 0.0)

    def test_intern_unlimited_quota_returns_null_remaining(self) -> None:
        """PLAN test case 2: intern (quota=NULL) → days_remaining=None, no block."""
        emp_id = self._make_employee(quota=None)
        # Even with leave logged, remaining stays null because quota is null
        self._add_paid_leave(emp_id, date(2026, 6, 1), date(2026, 6, 10), "APPROVED")

        with SessionLocal() as db:
            emp = db.get(Employee, emp_id)
            balance = compute_leave_balance(emp, db, year=2026)

        self.assertIsNone(balance.annual_quota)
        self.assertIsNone(balance.days_remaining)
        self.assertEqual(balance.days_used, 10.0)

    def test_pending_counted_separately_from_used(self) -> None:
        """PENDING leave days surface as `days_pending`, not `days_used`."""
        emp_id = self._make_employee(quota=12.0)
        self._add_paid_leave(emp_id, date(2026, 6, 1), date(2026, 6, 3), "APPROVED")
        self._add_paid_leave(emp_id, date(2026, 7, 1), date(2026, 7, 2), "PENDING")

        with SessionLocal() as db:
            emp = db.get(Employee, emp_id)
            balance = compute_leave_balance(emp, db, year=2026)

        self.assertEqual(balance.days_used, 3.0)
        self.assertEqual(balance.days_pending, 2.0)
        self.assertEqual(balance.days_remaining, 9.0)  # quota - used, pending not subtracted

    def test_remaining_clamped_at_zero(self) -> None:
        """If used > quota, remaining is 0 (not negative)."""
        emp_id = self._make_employee(quota=5.0)
        # 7 days used, quota only 5 → remaining must be 0, not -2
        self._add_paid_leave(emp_id, date(2026, 6, 1), date(2026, 6, 7), "APPROVED")

        with SessionLocal() as db:
            emp = db.get(Employee, emp_id)
            balance = compute_leave_balance(emp, db, year=2026)

        self.assertEqual(balance.days_used, 7.0)
        self.assertEqual(balance.days_remaining, 0.0)

    def test_year_boundary_clip(self) -> None:
        """Leave spanning Dec 30 → Jan 3 is split per year."""
        emp_id = self._make_employee(quota=12.0)
        self._add_paid_leave(emp_id, date(2025, 12, 30), date(2026, 1, 3), "APPROVED")

        with SessionLocal() as db:
            emp = db.get(Employee, emp_id)
            balance_2025 = compute_leave_balance(emp, db, year=2025)
            balance_2026 = compute_leave_balance(emp, db, year=2026)

        self.assertEqual(balance_2025.days_used, 2.0)  # Dec 30, 31
        self.assertEqual(balance_2026.days_used, 3.0)  # Jan 1, 2, 3


if __name__ == "__main__":
    unittest.main()
