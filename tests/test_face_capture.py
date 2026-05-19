"""Phase 4.1 — Face capture tests."""
import io
import os
import shutil
import sqlite3
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./test_face_capture.db"
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-16")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
os.environ["RECAPTCHA_ENABLED"] = "false"

from app.core.config import settings as _app_settings  # noqa: E402
_app_settings.RECAPTCHA_ENABLED = False
_app_settings.FACE_UPLOAD_DIR = "test_uploads/face"
_app_settings.FACE_RETENTION_DAYS = 30

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from PIL import Image  # noqa: E402

from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AttendanceException,
    AttendanceExceptionAudit,
    AttendanceLog,
    Employee,
    User,
)
from app.scheduler import cleanup_old_face_images  # noqa: E402


# SQLite needs a bool_or aggregate to match Postgres queries used by reports.
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


def _make_image_bytes(width: int, height: int, color: int = 128) -> bytes:
    """Generate a grayscale JPEG of given size, with horizontal gradient when
    color==128 (normal lighting) so std-deviation stays well above the 15
    threshold even after JPEG compression. Solid-fill modes (very dark / very
    bright) deliberately keep std≈0 to hit the brightness check first.
    """
    img = Image.new("L", (width, height), color=color)
    if color == 128:
        # Horizontal gradient 60 → 200 → wide spread for a realistic photo.
        pixels = img.load()
        for x in range(width):
            level = 60 + int((x / max(1, width - 1)) * 140)
            for y in range(height):
                pixels[x, y] = level
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class FaceCaptureTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db_path = Path("test_face_capture.db")
        if cls.db_path.exists():
            cls.db_path.unlink()
        cls.upload_dir = Path(_app_settings.FACE_UPLOAD_DIR)
        if cls.upload_dir.exists():
            shutil.rmtree(cls.upload_dir, ignore_errors=True)

        engine.dispose()
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        engine.dispose()
        if cls.db_path.exists():
            try:
                cls.db_path.unlink()
            except PermissionError:
                pass
        if cls.upload_dir.exists():
            shutil.rmtree(cls.upload_dir, ignore_errors=True)

    def setUp(self) -> None:
        with SessionLocal() as db:
            db.query(AttendanceExceptionAudit).delete()
            db.query(AttendanceException).delete()
            db.query(AttendanceLog).delete()
            db.query(Employee).delete()
            db.query(User).delete()
            db.commit()
        if self.upload_dir.exists():
            shutil.rmtree(self.upload_dir, ignore_errors=True)

    # ── Fixtures ────────────────────────────────────────────────────────────

    def _create_employee_with_token(
        self, email: str, role: str = "employee"
    ) -> tuple[int, str]:
        """Create a user + employee + IN log, return (log_id, jwt_token)."""
        with SessionLocal() as db:
            user = User(email=email, password_hash=hash_password("pwd123"), role=role)
            db.add(user)
            db.commit()
            db.refresh(user)

            emp = Employee(
                code=email.split("@")[0],
                full_name="Tester",
                user_id=user.id,
            )
            db.add(emp)
            db.commit()
            db.refresh(emp)

            now_utc = datetime.now(timezone.utc)
            log = AttendanceLog(
                employee_id=emp.id,
                type="IN",
                time=now_utc,
                work_date=now_utc.date(),
                lat=10.7769,
                lng=106.7009,
                distance_m=10.0,
                is_out_of_range=False,
            )
            db.add(log)
            db.commit()
            db.refresh(log)

            return log.id, create_access_token({"sub": str(user.id)})

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    # ── Tests ───────────────────────────────────────────────────────────────

    def test_upload_valid_image_returns_ok(self) -> None:
        log_id, token = self._create_employee_with_token("emp1@test.com")
        image_bytes = _make_image_bytes(320, 320, color=128)

        response = self.client.post(
            "/face/upload",
            headers=self._auth_headers(token),
            data={"log_id": str(log_id)},
            files={"file": ("selfie.jpg", image_bytes, "image/jpeg")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["face_check_status"], "CAPTURED")
        self.assertIsNone(body.get("reason"))

        with SessionLocal() as db:
            log = db.query(AttendanceLog).filter(AttendanceLog.id == log_id).first()
            self.assertIsNotNone(log)
            self.assertEqual(log.face_check_status, "CAPTURED")
            self.assertIsNotNone(log.face_image_path)
            self.assertIsNotNone(log.face_captured_at)

    def test_upload_too_small_image_returns_quality_low(self) -> None:
        """Below 200×200 px → quality_low (not 400 — file still saved for admin review)."""
        log_id, token = self._create_employee_with_token("emp_small@test.com")
        image_bytes = _make_image_bytes(100, 100, color=128)

        response = self.client.post(
            "/face/upload",
            headers=self._auth_headers(token),
            data={"log_id": str(log_id)},
            files={"file": ("tiny.jpg", image_bytes, "image/jpeg")},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "quality_low")
        self.assertEqual(body["face_check_status"], "QUALITY_LOW")
        self.assertIn("nhỏ", body["reason"])  # "Ảnh quá nhỏ"

    def test_upload_too_dark_image_returns_quality_low(self) -> None:
        log_id, token = self._create_employee_with_token("emp_dark@test.com")
        image_bytes = _make_image_bytes(320, 320, color=10)  # very dark

        response = self.client.post(
            "/face/upload",
            headers=self._auth_headers(token),
            data={"log_id": str(log_id)},
            files={"file": ("dark.jpg", image_bytes, "image/jpeg")},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "quality_low")
        self.assertIn("tối", body["reason"])  # "Ảnh quá tối"

    def test_upload_nonexistent_log_returns_404(self) -> None:
        _, token = self._create_employee_with_token("emp_404@test.com")
        image_bytes = _make_image_bytes(320, 320)

        response = self.client.post(
            "/face/upload",
            headers=self._auth_headers(token),
            data={"log_id": "999999"},
            files={"file": ("selfie.jpg", image_bytes, "image/jpeg")},
        )
        self.assertEqual(response.status_code, 404)

    def test_upload_other_employee_log_returns_404(self) -> None:
        """Employee A cannot upload to employee B's log → returns 404 (ownership check)."""
        log_id_a, _token_a = self._create_employee_with_token("empA@test.com")
        _, token_b = self._create_employee_with_token("empB@test.com")
        image_bytes = _make_image_bytes(320, 320)

        response = self.client.post(
            "/face/upload",
            headers=self._auth_headers(token_b),
            data={"log_id": str(log_id_a)},
            files={"file": ("selfie.jpg", image_bytes, "image/jpeg")},
        )
        self.assertEqual(response.status_code, 404)

    def test_get_face_image_admin_ok(self) -> None:
        log_id, emp_token = self._create_employee_with_token("emp_img@test.com")
        image_bytes = _make_image_bytes(320, 320)
        self.client.post(
            "/face/upload",
            headers=self._auth_headers(emp_token),
            data={"log_id": str(log_id)},
            files={"file": ("selfie.jpg", image_bytes, "image/jpeg")},
        )

        # Now fetch as admin
        _, admin_token = self._create_employee_with_token("admin@test.com", role="ADMIN")
        response = self.client.get(
            f"/face/image/{log_id}",
            headers=self._auth_headers(admin_token),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertGreater(len(response.content), 0)

    def test_get_face_image_employee_returns_403(self) -> None:
        log_id, emp_token = self._create_employee_with_token("emp_forbid@test.com")
        image_bytes = _make_image_bytes(320, 320)
        self.client.post(
            "/face/upload",
            headers=self._auth_headers(emp_token),
            data={"log_id": str(log_id)},
            files={"file": ("selfie.jpg", image_bytes, "image/jpeg")},
        )

        # Employee trying to fetch the admin endpoint
        response = self.client.get(
            f"/face/image/{log_id}",
            headers=self._auth_headers(emp_token),
        )
        self.assertEqual(response.status_code, 403)

    def test_flag_no_camera_creates_exception(self) -> None:
        log_id, token = self._create_employee_with_token("emp_nocam@test.com")

        response = self.client.post(
            "/face/flag-no-camera",
            headers=self._auth_headers(token),
            data={"log_id": str(log_id)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["face_check_status"], "NOT_CAPTURED")

        with SessionLocal() as db:
            log = db.query(AttendanceLog).filter(AttendanceLog.id == log_id).first()
            self.assertEqual(log.face_check_status, "NOT_CAPTURED")

            exc = (
                db.query(AttendanceException)
                .filter(AttendanceException.source_checkin_log_id == log_id)
                .first()
            )
            self.assertIsNotNone(exc)
            self.assertEqual(exc.exception_type, "FACE_NOT_CAPTURED")
            self.assertEqual(exc.status, "PENDING_EMPLOYEE")

        # Idempotent: calling twice must not create a duplicate.
        self.client.post(
            "/face/flag-no-camera",
            headers=self._auth_headers(token),
            data={"log_id": str(log_id)},
        )
        with SessionLocal() as db:
            count = (
                db.query(AttendanceException)
                .filter(AttendanceException.source_checkin_log_id == log_id)
                .count()
            )
            self.assertEqual(count, 1)

    def test_cleanup_removes_old_files(self) -> None:
        """Date dirs older than FACE_RETENTION_DAYS are deleted."""
        base = Path(_app_settings.FACE_UPLOAD_DIR)
        base.mkdir(parents=True, exist_ok=True)

        old_date = (datetime.now(timezone.utc).date() - timedelta(days=45)).isoformat()
        fresh_date = datetime.now(timezone.utc).date().isoformat()

        old_dir = base / old_date / "42"
        fresh_dir = base / fresh_date / "42"
        old_dir.mkdir(parents=True)
        fresh_dir.mkdir(parents=True)
        (old_dir / "1_in.jpg").write_bytes(b"x")
        (fresh_dir / "2_in.jpg").write_bytes(b"y")

        cleanup_old_face_images()

        self.assertFalse(old_dir.exists(), "old directory should be removed")
        self.assertTrue(fresh_dir.exists(), "fresh directory should be kept")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
