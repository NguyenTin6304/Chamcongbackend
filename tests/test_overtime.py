"""Phase 2.5 overtime service unit tests.

Covers: auto-creation, approval/rejection/edit workflow, exception integration,
bulk approve, payable-minutes map, and rule-snapshot isolation.

Run individually:
    python -m pytest tests/test_overtime.py -v

Design notes:
  - Attendance logs are stored with UTC-aware datetimes (SQLite strips TZ on store,
    then normalize_utc treats naive as UTC — matching the live server behavior).
  - Assertions on service return values happen BEFORE db.commit() to avoid
    DetachedInstanceError (SQLAlchemy expires all attrs on commit).
  - Tests that need to verify persisted state use a second SessionLocal block.
"""
import os
import sqlite3
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

# Must be set before any app imports so the engine is created with this URL.
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///./test_overtime.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-16")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
os.environ.setdefault("EXCEPTION_WORKFLOW_SYSTEM_KEY", "test-exception-system-key")

from fastapi import HTTPException
from sqlalchemy import event

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
from app.services.attendance_time import split_regular_overtime_minutes
from app.services.overtime_service import (
    approve_overtime,
    auto_create_pending_ot,
    bulk_approve,
    create_or_approve_from_exception,
    edit_approved_overtime,
    fetch_payable_minutes_map,
    reject_overtime,
    round_up_to_30,
)

UTC = timezone.utc
VN = timezone(timedelta(hours=7))

TUESDAY = date(2026, 5, 5)   # weekday() = 1 (normal workday)
SATURDAY = date(2026, 5, 9)  # weekday() = 5 (weekend)


def _utc(d: date, h: int, m: int = 0) -> datetime:
    """VN wall-clock time → UTC-aware datetime (as the server stores it).

    SQLite stores timezone-aware datetimes without the tz suffix.  When read
    back the value is a naive datetime; normalize_utc() then treats it as UTC.
    So we always store UTC here so the round-trip is consistent.
    """
    vn_dt = datetime.combine(d, time(h, m), tzinfo=VN)
    return vn_dt.astimezone(UTC)   # e.g. VN 08:00 → 01:00 UTC


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


class OvertimeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db_path = Path("test_overtime.db")
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

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_rule(
        self,
        *,
        overtime_enabled: bool = True,
        minimum_minutes: int = 30,
        start: time = time(8, 0),
        end: time = time(17, 0),
    ) -> CheckinRule:
        with SessionLocal() as db:
            rule = CheckinRule(
                latitude=10.7769,
                longitude=106.7009,
                radius_m=200,
                start_time=start,
                end_time=end,
                grace_minutes=10,
                checkout_grace_minutes=0,
                cross_day_cutoff_minutes=240,
                overtime_enabled=overtime_enabled,
                overtime_minimum_minutes=minimum_minutes,
                active=True,
            )
            db.add(rule)
            db.commit()
            db.refresh(rule)
            return rule

    def _make_user(self, email: str = "emp@test.com", role: str = "employee") -> User:
        with SessionLocal() as db:
            user = User(email=email, password_hash=hash_password("pw"), role=role)
            db.add(user)
            db.commit()
            db.refresh(user)
            return user

    def _make_employee(self, user_id: int, code: str = "E001") -> Employee:
        with SessionLocal() as db:
            emp = Employee(code=code, full_name="Test Employee", user_id=user_id)
            db.add(emp)
            db.commit()
            db.refresh(emp)
            return emp

    def _make_log(
        self,
        *,
        employee_id: int,
        work_date: date,
        happened_at: datetime,
        log_type: str = "OUT",
        snapshot_start: time | None = None,
        snapshot_end: time | None = None,
    ) -> AttendanceLog:
        with SessionLocal() as db:
            log = AttendanceLog(
                employee_id=employee_id,
                type=log_type,
                time=happened_at,
                work_date=work_date,
                lat=10.7769,
                lng=106.7009,
                is_out_of_range=False,
                punctuality_status="ON_TIME" if log_type == "IN" else None,
                checkout_status="ON_TIME" if log_type == "OUT" else None,
                snapshot_start_time=snapshot_start,
                snapshot_end_time=snapshot_end,
            )
            db.add(log)
            db.commit()
            db.refresh(log)
            return log

    def _make_ot_record(
        self,
        *,
        employee_id: int,
        work_date: date,
        raw_minutes: int,
        approved_minutes: int | None = None,
        status: str = "PENDING",
        admin_id: int | None = None,
    ) -> OvertimeRecord:
        with SessionLocal() as db:
            record = OvertimeRecord(
                employee_id=employee_id,
                work_date=work_date,
                raw_minutes=raw_minutes,
                approved_minutes=approved_minutes,
                status=status,
                source="AUTO_CHECKOUT",
                shift_start_snapshot=time(8, 0),
                shift_end_snapshot=time(17, 0),
                is_weekend=False,
                is_holiday=False,
                admin_id=admin_id,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return record

    # ── Auto-create tests ─────────────────────────────────────────────────────

    def test_auto_create_creates_pending_when_ot_exceeds_threshold(self) -> None:
        """Checkout at 18:00 VN with 08:00-17:00 shift → 60 min OT, record created."""
        self._make_rule(minimum_minutes=30)
        user = self._make_user()
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )
        checkout = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 18),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, checkout.id)
            cin = db.get(AttendanceLog, checkin.id)
            record = auto_create_pending_ot(db, out, checkin_log=cin)
            # Assert before commit — object is in-session and not yet expired.
            self.assertIsNotNone(record)
            self.assertEqual(record.status, "PENDING")
            self.assertEqual(record.employee_id, emp.id)
            self.assertEqual(record.raw_minutes, 60)
            self.assertIsNone(record.approved_minutes)
            db.commit()

    def test_auto_create_returns_none_when_below_threshold(self) -> None:
        """Checkout at 17:20 VN → 20 min OT, below 30 min threshold → no record."""
        self._make_rule(minimum_minutes=30)
        user = self._make_user()
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )
        checkout = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 17, 20),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, checkout.id)
            cin = db.get(AttendanceLog, checkin.id)
            result = auto_create_pending_ot(db, out, checkin_log=cin)

        self.assertIsNone(result)

    def test_auto_create_returns_none_when_ot_disabled(self) -> None:
        """overtime_enabled=False → auto_create always returns None."""
        self._make_rule(overtime_enabled=False)
        user = self._make_user()
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )
        checkout = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 18),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, checkout.id)
            cin = db.get(AttendanceLog, checkin.id)
            result = auto_create_pending_ot(db, out, checkin_log=cin)

        self.assertIsNone(result)

    def test_auto_create_idempotent(self) -> None:
        """Calling auto_create twice for same employee+date returns same record id."""
        self._make_rule()
        user = self._make_user()
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )
        checkout = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 18),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, checkout.id)
            cin = db.get(AttendanceLog, checkin.id)
            r1 = auto_create_pending_ot(db, out, checkin_log=cin)
            r1_id = r1.id  # read before commit (flush already set the PK)
            db.commit()

        with SessionLocal() as db:
            out = db.get(AttendanceLog, checkout.id)
            cin = db.get(AttendanceLog, checkin.id)
            r2 = auto_create_pending_ot(db, out, checkin_log=cin)
            r2_id = r2.id
            db.commit()

        self.assertEqual(r1_id, r2_id)
        with SessionLocal() as db:
            self.assertEqual(db.query(OvertimeRecord).count(), 1)

    def test_auto_create_tags_weekend(self) -> None:
        """OT record created on Saturday sets is_weekend=True."""
        self._make_rule()
        user = self._make_user()
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=SATURDAY,
            happened_at=_utc(SATURDAY, 8), log_type="IN",
        )
        checkout = self._make_log(
            employee_id=emp.id, work_date=SATURDAY, happened_at=_utc(SATURDAY, 18),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, checkout.id)
            cin = db.get(AttendanceLog, checkin.id)
            record = auto_create_pending_ot(db, out, checkin_log=cin)
            self.assertIsNotNone(record)
            self.assertTrue(record.is_weekend)
            db.commit()

    def test_auto_create_tags_holiday(self) -> None:
        """OT record created on a public holiday sets is_holiday=True."""
        self._make_rule()
        with SessionLocal() as db:
            db.add(PublicHoliday(date=TUESDAY, name="Test Holiday"))
            db.commit()

        user = self._make_user()
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )
        checkout = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 18),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, checkout.id)
            cin = db.get(AttendanceLog, checkin.id)
            record = auto_create_pending_ot(db, out, checkin_log=cin)
            self.assertIsNotNone(record)
            self.assertTrue(record.is_holiday)
            db.commit()

    def test_auto_create_returns_none_for_in_log(self) -> None:
        """auto_create_pending_ot only triggers on OUT logs, not IN."""
        self._make_rule()
        user = self._make_user()
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )

        with SessionLocal() as db:
            cin = db.get(AttendanceLog, checkin.id)
            result = auto_create_pending_ot(db, cin)

        self.assertIsNone(result)

    def test_auto_create_returns_none_without_active_rule(self) -> None:
        """No active CheckinRule → auto_create returns None."""
        user = self._make_user()
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )
        checkout = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 18),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, checkout.id)
            cin = db.get(AttendanceLog, checkin.id)
            result = auto_create_pending_ot(db, out, checkin_log=cin)

        self.assertIsNone(result)

    def test_auto_create_writes_created_audit(self) -> None:
        """CREATED audit entry is written when OT record is first created."""
        self._make_rule()
        user = self._make_user()
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )
        checkout = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 18),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, checkout.id)
            cin = db.get(AttendanceLog, checkin.id)
            record = auto_create_pending_ot(db, out, checkin_log=cin)
            ot_id = record.id
            db.commit()

        with SessionLocal() as db:
            audits = (
                db.query(OvertimeAudit)
                .filter(OvertimeAudit.overtime_id == ot_id)
                .all()
            )

        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].action, "CREATED")
        self.assertEqual(audits[0].to_status, "PENDING")
        self.assertIsNone(audits[0].actor_id)

    # ── Approve workflow tests ─────────────────────────────────────────────────

    def test_approve_pending_sets_status_and_minutes(self) -> None:
        """PENDING → APPROVED with approved_minutes correctly set."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=60)

        with SessionLocal() as db:
            result = approve_overtime(
                db, record.id, approved_minutes=60, admin_id=admin.id, admin_note=None,
            )
            self.assertEqual(result.status, "APPROVED")
            self.assertEqual(result.approved_minutes, 60)
            self.assertEqual(result.admin_id, admin.id)
            self.assertIsNotNone(result.decided_at)
            db.commit()

    def test_approve_rejected_record_raises_400(self) -> None:
        """Cannot approve a REJECTED record."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(
            employee_id=emp.id, work_date=TUESDAY, raw_minutes=60,
            status="REJECTED", approved_minutes=0, admin_id=admin.id,
        )

        with SessionLocal() as db, self.assertRaises(HTTPException) as ctx:
            approve_overtime(db, record.id, approved_minutes=60, admin_id=admin.id, admin_note=None)

        self.assertEqual(ctx.exception.status_code, 400)

    def test_approve_large_delta_without_note_raises_400(self) -> None:
        """|approved - raw| > 30 and no note → 400 (note required for transparency)."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=60)

        with SessionLocal() as db, self.assertRaises(HTTPException) as ctx:
            approve_overtime(
                db, record.id,
                approved_minutes=120,  # delta = 60 > 30 threshold
                admin_id=admin.id,
                admin_note=None,
            )

        self.assertEqual(ctx.exception.status_code, 400)

    def test_approve_large_delta_with_note_succeeds(self) -> None:
        """|delta| > 30 but note provided → approval succeeds."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=60)

        with SessionLocal() as db:
            result = approve_overtime(
                db, record.id,
                approved_minutes=120,
                admin_id=admin.id,
                admin_note="Xác nhận theo bảng phân công thực tế",
            )
            self.assertEqual(result.status, "APPROVED")
            self.assertEqual(result.approved_minutes, 120)
            db.commit()

    def test_approve_writes_approved_audit(self) -> None:
        """Approving creates APPROVED audit entry with correct from/to fields."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=60)

        with SessionLocal() as db:
            approve_overtime(db, record.id, approved_minutes=60, admin_id=admin.id, admin_note=None)
            db.commit()

        with SessionLocal() as db:
            audit = (
                db.query(OvertimeAudit)
                .filter(OvertimeAudit.overtime_id == record.id, OvertimeAudit.action == "APPROVED")
                .first()
            )

        self.assertIsNotNone(audit)
        self.assertEqual(audit.from_status, "PENDING")
        self.assertEqual(audit.to_status, "APPROVED")
        self.assertEqual(audit.from_minutes, 60)
        self.assertEqual(audit.to_minutes, 60)

    # ── Reject workflow tests ──────────────────────────────────────────────────

    def test_reject_pending_sets_rejected_and_zero_minutes(self) -> None:
        """PENDING → REJECTED with approved_minutes=0."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=60)

        with SessionLocal() as db:
            result = reject_overtime(db, record.id, admin_id=admin.id, admin_note="Không hợp lệ")
            self.assertEqual(result.status, "REJECTED")
            self.assertEqual(result.approved_minutes, 0)
            self.assertIsNotNone(result.decided_at)
            db.commit()

    def test_reject_without_note_raises_400(self) -> None:
        """Rejection requires non-empty admin_note."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=60)

        with SessionLocal() as db, self.assertRaises(HTTPException) as ctx:
            reject_overtime(db, record.id, admin_id=admin.id, admin_note="")

        self.assertEqual(ctx.exception.status_code, 400)

    def test_reject_blank_whitespace_note_raises_400(self) -> None:
        """Whitespace-only note is treated as empty."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=60)

        with SessionLocal() as db, self.assertRaises(HTTPException) as ctx:
            reject_overtime(db, record.id, admin_id=admin.id, admin_note="   ")

        self.assertEqual(ctx.exception.status_code, 400)

    def test_reject_approved_record_raises_400(self) -> None:
        """Cannot reject an already APPROVED record."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(
            employee_id=emp.id, work_date=TUESDAY, raw_minutes=60,
            status="APPROVED", approved_minutes=60, admin_id=admin.id,
        )

        with SessionLocal() as db, self.assertRaises(HTTPException) as ctx:
            reject_overtime(db, record.id, admin_id=admin.id, admin_note="reason")

        self.assertEqual(ctx.exception.status_code, 400)

    def test_reject_writes_rejected_audit(self) -> None:
        """Rejecting creates REJECTED audit entry."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=60)

        with SessionLocal() as db:
            reject_overtime(db, record.id, admin_id=admin.id, admin_note="Không đúng quy định")
            db.commit()

        with SessionLocal() as db:
            audit = (
                db.query(OvertimeAudit)
                .filter(OvertimeAudit.overtime_id == record.id, OvertimeAudit.action == "REJECTED")
                .first()
            )

        self.assertIsNotNone(audit)
        self.assertEqual(audit.to_minutes, 0)

    # ── Edit workflow tests ────────────────────────────────────────────────────

    def test_edit_approved_record_changes_minutes(self) -> None:
        """Editing APPROVED record updates approved_minutes, status stays APPROVED."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(
            employee_id=emp.id, work_date=TUESDAY, raw_minutes=60,
            status="APPROVED", approved_minutes=60, admin_id=admin.id,
        )

        with SessionLocal() as db:
            result = edit_approved_overtime(
                db, record.id,
                approved_minutes=90,
                admin_id=admin.id,
                admin_note="Điều chỉnh theo xác nhận của trưởng nhóm",
            )
            self.assertEqual(result.status, "APPROVED")
            self.assertEqual(result.approved_minutes, 90)
            db.commit()

    def test_edit_pending_record_raises_400(self) -> None:
        """Cannot edit a PENDING record; must use approve/reject instead."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=60)

        with SessionLocal() as db, self.assertRaises(HTTPException) as ctx:
            edit_approved_overtime(
                db, record.id, approved_minutes=90, admin_id=admin.id, admin_note="reason",
            )

        self.assertEqual(ctx.exception.status_code, 400)

    def test_edit_without_note_raises_400(self) -> None:
        """Edit always requires admin_note for audit trail."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(
            employee_id=emp.id, work_date=TUESDAY, raw_minutes=60,
            status="APPROVED", approved_minutes=60, admin_id=admin.id,
        )

        with SessionLocal() as db, self.assertRaises(HTTPException) as ctx:
            edit_approved_overtime(db, record.id, approved_minutes=90, admin_id=admin.id, admin_note="")

        self.assertEqual(ctx.exception.status_code, 400)

    def test_edit_writes_edited_audit_with_from_to_minutes(self) -> None:
        """Edit creates EDITED audit entry tracking from/to minutes."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        record = self._make_ot_record(
            employee_id=emp.id, work_date=TUESDAY, raw_minutes=60,
            status="APPROVED", approved_minutes=60, admin_id=admin.id,
        )

        with SessionLocal() as db:
            edit_approved_overtime(
                db, record.id,
                approved_minutes=90,
                admin_id=admin.id,
                admin_note="làm thêm 30 phút theo yêu cầu PM",
            )
            db.commit()

        with SessionLocal() as db:
            audit = (
                db.query(OvertimeAudit)
                .filter(OvertimeAudit.overtime_id == record.id, OvertimeAudit.action == "EDITED")
                .first()
            )

        self.assertIsNotNone(audit)
        self.assertEqual(audit.from_minutes, 60)
        self.assertEqual(audit.to_minutes, 90)
        self.assertEqual(audit.from_status, "APPROVED")
        self.assertEqual(audit.to_status, "APPROVED")

    # ── Exception integration tests ────────────────────────────────────────────

    def test_exception_approval_creates_approved_record_directly(self) -> None:
        """MISSED_CHECKOUT approval with late actual_checkout creates APPROVED record."""
        self._make_rule()
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )
        out_log = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 8),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, out_log.id)
            cin = db.get(AttendanceLog, checkin.id)
            record = create_or_approve_from_exception(
                db,
                attendance_log=out,
                checkin_log=cin,
                actual_checkout_time=_utc(TUESDAY, 18),
                approved_minutes=60,
                admin_id=admin.id,
                admin_note=None,
            )
            self.assertIsNotNone(record)
            self.assertEqual(record.status, "APPROVED")
            self.assertEqual(record.source, "EXCEPTION_APPROVAL")
            self.assertEqual(record.approved_minutes, 60)
            self.assertEqual(record.raw_minutes, 60)
            db.commit()

    def test_exception_returns_none_when_checkout_at_shift_end(self) -> None:
        """Actual checkout exactly at shift end → 0 OT → no record created."""
        self._make_rule(start=time(8, 0), end=time(17, 0))
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )
        out_log = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 8),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, out_log.id)
            cin = db.get(AttendanceLog, checkin.id)
            result = create_or_approve_from_exception(
                db,
                attendance_log=out,
                checkin_log=cin,
                actual_checkout_time=_utc(TUESDAY, 17),  # exactly at shift end → 0 OT
                approved_minutes=0,
                admin_id=admin.id,
                admin_note=None,
            )

        self.assertIsNone(result)

    def test_exception_updates_existing_pending_to_approved(self) -> None:
        """If PENDING record exists, exception approval updates it in-place."""
        self._make_rule()
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        existing = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=50)
        existing_id = existing.id
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )
        out_log = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 18),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, out_log.id)
            cin = db.get(AttendanceLog, checkin.id)
            record = create_or_approve_from_exception(
                db,
                attendance_log=out,
                checkin_log=cin,
                actual_checkout_time=_utc(TUESDAY, 18),
                approved_minutes=60,
                admin_id=admin.id,
                admin_note="xác nhận thực tế",
            )
            # Same record id, now APPROVED
            self.assertEqual(record.id, existing_id)
            self.assertEqual(record.status, "APPROVED")
            self.assertEqual(record.approved_minutes, 60)
            db.commit()

        with SessionLocal() as db:
            self.assertEqual(db.query(OvertimeRecord).count(), 1)

    def test_exception_returns_none_when_ot_disabled(self) -> None:
        """overtime_enabled=False → create_or_approve_from_exception returns None."""
        self._make_rule(overtime_enabled=False)
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )
        out_log = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 18),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, out_log.id)
            cin = db.get(AttendanceLog, checkin.id)
            result = create_or_approve_from_exception(
                db,
                attendance_log=out,
                checkin_log=cin,
                actual_checkout_time=_utc(TUESDAY, 18),
                approved_minutes=60,
                admin_id=admin.id,
                admin_note=None,
            )

        self.assertIsNone(result)

    # ── Bulk approve tests ─────────────────────────────────────────────────────

    def test_bulk_approve_as_is_uses_raw_minutes(self) -> None:
        """Strategy 'as_is': approved_minutes = raw_minutes for each record."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        r1 = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=60)
        r2 = self._make_ot_record(employee_id=emp.id, work_date=SATURDAY, raw_minutes=45)

        with SessionLocal() as db:
            approved_count, skipped = bulk_approve(
                db, ids=[r1.id, r2.id], strategy="as_is", admin_id=admin.id, admin_note=None,
            )
            db.commit()

        self.assertEqual(approved_count, 2)
        self.assertEqual(skipped, [])
        with SessionLocal() as db:
            self.assertEqual(db.get(OvertimeRecord, r1.id).approved_minutes, 60)
            self.assertEqual(db.get(OvertimeRecord, r2.id).approved_minutes, 45)

    def test_bulk_approve_round_up_30_rounds_up(self) -> None:
        """Strategy 'round_up_30': 31 min → 60, 30 min → 30 (already multiple)."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        r1 = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=31)
        r2 = self._make_ot_record(employee_id=emp.id, work_date=SATURDAY, raw_minutes=30)

        with SessionLocal() as db:
            bulk_approve(db, ids=[r1.id, r2.id], strategy="round_up_30", admin_id=admin.id, admin_note=None)
            db.commit()

        with SessionLocal() as db:
            self.assertEqual(db.get(OvertimeRecord, r1.id).approved_minutes, 60)
            self.assertEqual(db.get(OvertimeRecord, r2.id).approved_minutes, 30)

    def test_bulk_approve_skips_non_pending_records(self) -> None:
        """Bulk approve skips records not in PENDING state and returns their ids."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        pending = self._make_ot_record(employee_id=emp.id, work_date=TUESDAY, raw_minutes=60)
        rejected = self._make_ot_record(
            employee_id=emp.id, work_date=SATURDAY, raw_minutes=60,
            status="REJECTED", approved_minutes=0, admin_id=admin.id,
        )

        with SessionLocal() as db:
            approved_count, skipped = bulk_approve(
                db, ids=[pending.id, rejected.id], strategy="as_is", admin_id=admin.id, admin_note=None,
            )
            db.commit()

        self.assertEqual(approved_count, 1)
        self.assertIn(rejected.id, skipped)

    # ── round_up_to_30 pure function tests ────────────────────────────────────

    def test_round_up_to_30_various_values(self) -> None:
        self.assertEqual(round_up_to_30(0), 0)    # zero stays zero
        self.assertEqual(round_up_to_30(1), 30)   # any positive → at least 30
        self.assertEqual(round_up_to_30(29), 30)  # rounds up to 30
        self.assertEqual(round_up_to_30(30), 30)  # exact multiple unchanged
        self.assertEqual(round_up_to_30(31), 60)  # one over → next multiple
        self.assertEqual(round_up_to_30(60), 60)
        self.assertEqual(round_up_to_30(61), 90)
        self.assertEqual(round_up_to_30(90), 90)
        self.assertEqual(round_up_to_30(91), 120)

    # ── Payable minutes map tests ──────────────────────────────────────────────

    def test_payable_map_includes_only_approved_records(self) -> None:
        """Only APPROVED records appear in the payable map."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        self._make_ot_record(
            employee_id=emp.id, work_date=TUESDAY, raw_minutes=60,
            status="APPROVED", approved_minutes=60, admin_id=admin.id,
        )
        self._make_ot_record(employee_id=emp.id, work_date=SATURDAY, raw_minutes=45)  # PENDING

        with SessionLocal() as db:
            result = fetch_payable_minutes_map(db)

        self.assertIn((emp.id, TUESDAY), result)
        self.assertNotIn((emp.id, SATURDAY), result)
        self.assertEqual(result[(emp.id, TUESDAY)], 60)

    def test_payable_map_excludes_rejected(self) -> None:
        """REJECTED records are not payable."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        self._make_ot_record(
            employee_id=emp.id, work_date=TUESDAY, raw_minutes=60,
            status="REJECTED", approved_minutes=0, admin_id=admin.id,
        )

        with SessionLocal() as db:
            result = fetch_payable_minutes_map(db)

        self.assertNotIn((emp.id, TUESDAY), result)
        self.assertEqual(len(result), 0)

    def test_payable_map_date_range_filter_excludes_outside(self) -> None:
        """from_date/to_date filter excludes records outside the range."""
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)
        self._make_ot_record(
            employee_id=emp.id, work_date=TUESDAY, raw_minutes=60,
            status="APPROVED", approved_minutes=60, admin_id=admin.id,
        )
        self._make_ot_record(
            employee_id=emp.id, work_date=SATURDAY, raw_minutes=30,
            status="APPROVED", approved_minutes=30, admin_id=admin.id,
        )

        with SessionLocal() as db:
            result = fetch_payable_minutes_map(db, from_date=SATURDAY, to_date=SATURDAY)

        self.assertNotIn((emp.id, TUESDAY), result)
        self.assertIn((emp.id, SATURDAY), result)
        self.assertEqual(result[(emp.id, SATURDAY)], 30)

    def test_payable_map_employee_ids_filter(self) -> None:
        """employee_ids filter restricts results to specified employees only."""
        user1 = self._make_user("emp1@test.com")
        user2 = self._make_user("emp2@test.com")
        admin = self._make_user("admin@test.com", "admin")
        emp1 = self._make_employee(user1.id, "E001")
        emp2 = self._make_employee(user2.id, "E002")
        self._make_ot_record(
            employee_id=emp1.id, work_date=TUESDAY, raw_minutes=60,
            status="APPROVED", approved_minutes=60, admin_id=admin.id,
        )
        self._make_ot_record(
            employee_id=emp2.id, work_date=TUESDAY, raw_minutes=45,
            status="APPROVED", approved_minutes=45, admin_id=admin.id,
        )

        with SessionLocal() as db:
            result = fetch_payable_minutes_map(db, employee_ids=[emp1.id])

        self.assertIn((emp1.id, TUESDAY), result)
        self.assertNotIn((emp2.id, TUESDAY), result)

    def test_plan_b_worked_minutes_equals_regular_plus_approved_ot(self) -> None:
        """Plan B: worked = regular_minutes + approved_OT (not raw OT or total).

        Scenario: 08:00-18:00 checkout → 60 min raw OT. Admin approves only 30 min.
        worked_minutes should be 540 (regular) + 30 (approved) = 570, not 600 (raw total).
        """
        user = self._make_user()
        admin = self._make_user("admin@test.com", "admin")
        emp = self._make_employee(user.id)

        shift_start, shift_end = time(8, 0), time(17, 0)
        # Use UTC times directly (same as what the service sees after DB round-trip)
        checkin_at = _utc(TUESDAY, 8)
        checkout_at = _utc(TUESDAY, 18)

        regular_minutes, raw_ot, _ = split_regular_overtime_minutes(
            TUESDAY, checkin_at, checkout_at, shift_start, shift_end,
        )
        self.assertEqual(regular_minutes, 540)
        self.assertEqual(raw_ot, 60)

        # Admin approves only 30 min (not full 60)
        self._make_ot_record(
            employee_id=emp.id, work_date=TUESDAY, raw_minutes=raw_ot,
            status="APPROVED", approved_minutes=30, admin_id=admin.id,
        )

        with SessionLocal() as db:
            payable_map = fetch_payable_minutes_map(db)

        payable_ot = payable_map.get((emp.id, TUESDAY), 0)
        worked_minutes = regular_minutes + payable_ot  # Plan B

        self.assertEqual(payable_ot, 30)
        self.assertEqual(worked_minutes, 570)   # 9.5h — not 10h (raw)
        self.assertNotEqual(worked_minutes, 600)  # 600 would be wrong

    # ── Rule snapshot isolation tests ─────────────────────────────────────────

    def test_rule_snapshot_preserved_after_rule_change(self) -> None:
        """Changing active rule does not affect OT record's shift snapshot."""
        self._make_rule(start=time(8, 0), end=time(17, 0))
        user = self._make_user()
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
        )
        checkout = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 18),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, checkout.id)
            cin = db.get(AttendanceLog, checkin.id)
            record = auto_create_pending_ot(db, out, checkin_log=cin)
            ot_id = record.id
            db.commit()

        # Admin changes rule to a different shift
        with SessionLocal() as db:
            db.query(CheckinRule).update({"active": False})
            db.add(CheckinRule(
                latitude=10.7769, longitude=106.7009, radius_m=200,
                start_time=time(9, 0), end_time=time(18, 0),
                grace_minutes=10, checkout_grace_minutes=0,
                cross_day_cutoff_minutes=240,
                overtime_enabled=True, overtime_minimum_minutes=30,
                active=True,
            ))
            db.commit()

        with SessionLocal() as db:
            ot = db.get(OvertimeRecord, ot_id)

        # Snapshot must still reflect original shift (08:00-17:00), not new rule
        self.assertEqual(ot.shift_start_snapshot, time(8, 0))
        self.assertEqual(ot.shift_end_snapshot, time(17, 0))

    def test_snapshot_used_to_compute_correct_raw_ot(self) -> None:
        """OT minutes stored in record match re-derivation using snapshot times."""
        self._make_rule(start=time(8, 0), end=time(17, 0))
        user = self._make_user()
        emp = self._make_employee(user.id)
        checkin = self._make_log(
            employee_id=emp.id, work_date=TUESDAY,
            happened_at=_utc(TUESDAY, 8), log_type="IN",
            snapshot_start=time(8, 0), snapshot_end=time(17, 0),
        )
        checkout = self._make_log(
            employee_id=emp.id, work_date=TUESDAY, happened_at=_utc(TUESDAY, 18),
            snapshot_start=time(8, 0), snapshot_end=time(17, 0),
        )

        with SessionLocal() as db:
            out = db.get(AttendanceLog, checkout.id)
            cin = db.get(AttendanceLog, checkin.id)
            record = auto_create_pending_ot(db, out, checkin_log=cin)
            raw_minutes = record.raw_minutes
            shift_start_snap = record.shift_start_snapshot
            shift_end_snap = record.shift_end_snapshot
            db.commit()

        # 08:00-17:00 shift, checkout 18:00 → 60 min OT
        self.assertEqual(raw_minutes, 60)

        # Re-derive using the stored snapshot — must yield the same value
        _, ot_from_snapshot, _ = split_regular_overtime_minutes(
            TUESDAY,
            _utc(TUESDAY, 8),
            _utc(TUESDAY, 18),
            shift_start_snap,
            shift_end_snap,
        )
        self.assertEqual(ot_from_snapshot, 60)


if __name__ == "__main__":
    unittest.main()
