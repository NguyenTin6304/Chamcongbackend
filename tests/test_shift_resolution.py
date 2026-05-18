"""Unit tests for Phase 3A + 3B — Shift resolution in _get_effective_time_rule.

Verifies the 4-tier resolution order:
  1. EmployeeShiftOverride (NEW in 3B, only when active by date)
  2. Group default Shift (NEW in 3A)
  3. Group.start_time / Group.end_time (legacy, unchanged)
  4. CheckinRule system fallback (legacy, unchanged)
"""
import os
import sqlite3
import unittest
from datetime import date, time, timedelta
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./test_shift_resolution.db"
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-16")

from sqlalchemy import event

from app.api.attendance import _get_effective_time_rule
from app.core.db import Base, SessionLocal, engine
from app.models import CheckinRule, Employee, EmployeeShiftOverride, Group, Shift


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


class ShiftResolutionTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db_path = Path("test_shift_resolution.db")
        if cls.db_path.exists():
            cls.db_path.unlink()
        Base.metadata.create_all(bind=engine)

    @classmethod
    def tearDownClass(cls) -> None:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if cls.db_path.exists():
            try:
                cls.db_path.unlink()
            except PermissionError:
                pass  # Windows file lock; harmless for next run.

    def setUp(self) -> None:
        self.db = SessionLocal()
        # Clean state between tests. Order matters because of FKs.
        for model in (EmployeeShiftOverride, Shift, Employee, Group, CheckinRule):
            self.db.query(model).delete()
        self.db.commit()

        self.system_rule = CheckinRule(
            latitude=10.0,
            longitude=106.0,
            radius_m=200,
            start_time=time(9, 0),
            grace_minutes=10,
            end_time=time(18, 0),
            checkout_grace_minutes=5,
            cross_day_cutoff_minutes=240,
            active=True,
        )
        self.db.add(self.system_rule)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _make_group(self, *, with_times: bool = False) -> Group:
        group = Group(
            code="G1",
            name="Group 1",
            active=True,
            start_time=time(8, 0) if with_times else None,
            grace_minutes=15 if with_times else None,
            end_time=time(17, 30) if with_times else None,
            checkout_grace_minutes=20 if with_times else None,
        )
        self.db.add(group)
        self.db.commit()
        self.db.refresh(group)
        return group

    def _make_employee(self, group_id: int | None) -> Employee:
        emp = Employee(
            code="E1",
            full_name="Test Employee",
            group_id=group_id,
            active=True,
        )
        self.db.add(emp)
        self.db.commit()
        self.db.refresh(emp)
        return emp

    # ─── Case 1: group has default Shift → use Shift times ─────────────────────
    def test_resolve_uses_group_default_shift_when_present(self) -> None:
        group = self._make_group(with_times=True)
        shift = Shift(
            group_id=group.id,
            name="Ca chiều",
            start_time=time(13, 0),
            end_time=time(22, 0),
            is_default=True,
            active=True,
        )
        self.db.add(shift)
        self.db.commit()
        emp = self._make_employee(group.id)

        result = _get_effective_time_rule(self.db, emp, self.system_rule)

        self.assertEqual(result.source, "GROUP_SHIFT")
        self.assertEqual(result.start_time, time(13, 0))
        self.assertEqual(result.end_time, time(22, 0))
        # Grace/cutoff still come from group config.
        self.assertEqual(result.grace_minutes, 15)
        self.assertEqual(result.checkout_grace_minutes, 20)

    # ─── Case 2: group has no Shift → fallback to Group.end_time ──────────────
    def test_resolve_falls_back_to_group_times_without_shift(self) -> None:
        group = self._make_group(with_times=True)
        emp = self._make_employee(group.id)

        result = _get_effective_time_rule(self.db, emp, self.system_rule)

        self.assertEqual(result.source, "GROUP")
        self.assertEqual(result.start_time, time(8, 0))
        self.assertEqual(result.end_time, time(17, 30))
        self.assertEqual(result.grace_minutes, 15)

    # ─── Case 3: inactive Shift is ignored → fallback to group ────────────────
    def test_resolve_ignores_inactive_default_shift(self) -> None:
        group = self._make_group(with_times=True)
        shift = Shift(
            group_id=group.id,
            name="Ca cũ",
            start_time=time(7, 0),
            end_time=time(15, 0),
            is_default=True,
            active=False,  # inactive
        )
        self.db.add(shift)
        self.db.commit()
        emp = self._make_employee(group.id)

        result = _get_effective_time_rule(self.db, emp, self.system_rule)

        self.assertEqual(result.source, "GROUP")
        self.assertEqual(result.end_time, time(17, 30))

    # ─── Case 4: non-default Shift is ignored → fallback to group ─────────────
    def test_resolve_ignores_non_default_shift(self) -> None:
        group = self._make_group(with_times=True)
        shift = Shift(
            group_id=group.id,
            name="Ca phụ",
            start_time=time(14, 0),
            end_time=time(23, 0),
            is_default=False,
            active=True,
        )
        self.db.add(shift)
        self.db.commit()
        emp = self._make_employee(group.id)

        result = _get_effective_time_rule(self.db, emp, self.system_rule)

        self.assertEqual(result.source, "GROUP")
        self.assertEqual(result.end_time, time(17, 30))

    # ─── Case 5: no group, no shift → system fallback ─────────────────────────
    def test_resolve_falls_back_to_system_when_no_group(self) -> None:
        emp = self._make_employee(group_id=None)

        result = _get_effective_time_rule(self.db, emp, self.system_rule)

        self.assertEqual(result.source, "SYSTEM_FALLBACK")
        self.assertEqual(result.fallback_reason, "EMPLOYEE_NOT_ASSIGNED_GROUP")
        self.assertEqual(result.start_time, time(9, 0))
        self.assertEqual(result.end_time, time(18, 0))

    # ─── Phase 3B cases ───────────────────────────────────────────────────────

    def _make_override(
        self,
        *,
        employee_id: int,
        shift_id: int,
        effective_date: date,
        end_date: date | None,
    ) -> EmployeeShiftOverride:
        override = EmployeeShiftOverride(
            employee_id=employee_id,
            shift_id=shift_id,
            effective_date=effective_date,
            end_date=end_date,
        )
        self.db.add(override)
        self.db.commit()
        self.db.refresh(override)
        return override

    def test_resolve_uses_active_override_over_default_shift(self) -> None:
        """Active override (effective_date in past, end_date in future) wins
        over the group's default Shift."""
        group = self._make_group(with_times=True)
        default_shift = Shift(
            group_id=group.id,
            name="Ca mặc định",
            start_time=time(8, 0),
            end_time=time(17, 0),
            is_default=True,
            active=True,
        )
        override_shift = Shift(
            group_id=group.id,
            name="Ca ưu tiên",
            start_time=time(13, 0),
            end_time=time(22, 0),
            is_default=False,
            active=True,
        )
        self.db.add_all([default_shift, override_shift])
        self.db.commit()
        self.db.refresh(override_shift)

        emp = self._make_employee(group.id)
        today = date.today()
        self._make_override(
            employee_id=emp.id,
            shift_id=override_shift.id,
            effective_date=today - timedelta(days=1),
            end_date=today + timedelta(days=30),
        )

        result = _get_effective_time_rule(self.db, emp, self.system_rule)

        self.assertEqual(result.source, "EMPLOYEE_SHIFT_OVERRIDE")
        self.assertEqual(result.start_time, time(13, 0))
        self.assertEqual(result.end_time, time(22, 0))

    def test_resolve_ignores_future_dated_override(self) -> None:
        """Override with effective_date > today is not yet active — falls
        through to the group default Shift."""
        group = self._make_group(with_times=True)
        default_shift = Shift(
            group_id=group.id,
            name="Ca mặc định",
            start_time=time(8, 0),
            end_time=time(17, 0),
            is_default=True,
            active=True,
        )
        future_shift = Shift(
            group_id=group.id,
            name="Ca tương lai",
            start_time=time(13, 0),
            end_time=time(22, 0),
            is_default=False,
            active=True,
        )
        self.db.add_all([default_shift, future_shift])
        self.db.commit()
        self.db.refresh(future_shift)

        emp = self._make_employee(group.id)
        today = date.today()
        self._make_override(
            employee_id=emp.id,
            shift_id=future_shift.id,
            effective_date=today + timedelta(days=7),
            end_date=None,
        )

        result = _get_effective_time_rule(self.db, emp, self.system_rule)

        self.assertEqual(result.source, "GROUP_SHIFT")
        self.assertEqual(result.end_time, time(17, 0))

    def test_resolve_ignores_expired_override(self) -> None:
        """Override with end_date < today is expired — falls through."""
        group = self._make_group(with_times=True)
        default_shift = Shift(
            group_id=group.id,
            name="Ca mặc định",
            start_time=time(8, 0),
            end_time=time(17, 0),
            is_default=True,
            active=True,
        )
        expired_shift = Shift(
            group_id=group.id,
            name="Ca đã hết",
            start_time=time(13, 0),
            end_time=time(22, 0),
            is_default=False,
            active=True,
        )
        self.db.add_all([default_shift, expired_shift])
        self.db.commit()
        self.db.refresh(expired_shift)

        emp = self._make_employee(group.id)
        today = date.today()
        self._make_override(
            employee_id=emp.id,
            shift_id=expired_shift.id,
            effective_date=today - timedelta(days=30),
            end_date=today - timedelta(days=1),
        )

        result = _get_effective_time_rule(self.db, emp, self.system_rule)

        self.assertEqual(result.source, "GROUP_SHIFT")
        self.assertEqual(result.end_time, time(17, 0))

    def test_resolve_open_ended_override_no_end_date(self) -> None:
        """Override with end_date=NULL stays active indefinitely."""
        group = self._make_group(with_times=True)
        override_shift = Shift(
            group_id=group.id,
            name="Ca vô hạn",
            start_time=time(7, 0),
            end_time=time(16, 0),
            is_default=False,
            active=True,
        )
        self.db.add(override_shift)
        self.db.commit()
        self.db.refresh(override_shift)

        emp = self._make_employee(group.id)
        today = date.today()
        self._make_override(
            employee_id=emp.id,
            shift_id=override_shift.id,
            effective_date=today - timedelta(days=1),
            end_date=None,
        )

        result = _get_effective_time_rule(self.db, emp, self.system_rule)

        self.assertEqual(result.source, "EMPLOYEE_SHIFT_OVERRIDE")
        self.assertEqual(result.start_time, time(7, 0))
        self.assertEqual(result.end_time, time(16, 0))

    def test_resolve_ignores_override_pointing_to_inactive_shift(self) -> None:
        """If the shift referenced by override is deactivated, fall through."""
        group = self._make_group(with_times=True)
        inactive_shift = Shift(
            group_id=group.id,
            name="Ca đã tắt",
            start_time=time(13, 0),
            end_time=time(22, 0),
            is_default=False,
            active=False,
        )
        self.db.add(inactive_shift)
        self.db.commit()
        self.db.refresh(inactive_shift)

        emp = self._make_employee(group.id)
        today = date.today()
        self._make_override(
            employee_id=emp.id,
            shift_id=inactive_shift.id,
            effective_date=today - timedelta(days=1),
            end_date=None,
        )

        result = _get_effective_time_rule(self.db, emp, self.system_rule)

        self.assertEqual(result.source, "GROUP")
        self.assertEqual(result.end_time, time(17, 30))


if __name__ == "__main__":
    unittest.main()
