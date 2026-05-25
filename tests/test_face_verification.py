"""Phase 4.2 — Face verification tests.

We monkeypatch `extract_embedding` so the heavy insightface model is never
loaded. Tests run against SQLite (matches the Phase 4.1 setup).
"""
import io
import os
import shutil
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./test_face_verification.db"
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-16")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
os.environ["RECAPTCHA_ENABLED"] = "false"

from app.core.config import settings as _app_settings  # noqa: E402
_app_settings.RECAPTCHA_ENABLED = False
_app_settings.FACE_UPLOAD_DIR = "test_uploads_verify/face"
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
    EmployeeFaceReference,
    User,
)


# SQLite needs bool_or aggregate when other modules touch it during app startup.
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


def _make_image_bytes(width: int = 320, height: int = 320, color: int = 128) -> bytes:
    img = Image.new("L", (width, height), color=color)
    if color == 128:
        pixels = img.load()
        for x in range(width):
            level = 60 + int((x / max(1, width - 1)) * 140)
            for y in range(height):
                pixels[x, y] = level
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# A canonical reference embedding used by tests. The values themselves don't
# matter — only the relative angle to the upload-time embeddings matters for
# cosine similarity.
_REF_EMBEDDING = [1.0] + [0.0] * 511
_MATCH_EMBEDDING = [1.0] + [0.0] * 511                   # cos = 1.0
_LOW_CONF_EMBEDDING = [0.7, 0.7] + [0.0] * 510           # cos ≈ 0.707
_MISMATCH_EMBEDDING = [0.3, 0.95] + [0.0] * 510          # cos ≈ 0.301


class FaceVerificationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db_path = Path("test_face_verification.db")
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
            db.query(EmployeeFaceReference).delete()
            db.query(AttendanceExceptionAudit).delete()
            db.query(AttendanceException).delete()
            db.query(AttendanceLog).delete()
            db.query(Employee).delete()
            db.query(User).delete()
            db.commit()
        if self.upload_dir.exists():
            shutil.rmtree(self.upload_dir, ignore_errors=True)

    # ── Fixtures ───────────────────────────────────────────────────────────

    def _create_employee_with_token(
        self, email: str, role: str = "employee"
    ) -> tuple[int, int, str]:
        """Return (employee_id, log_id, token)."""
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

            return emp.id, log.id, create_access_token({"sub": str(user.id)})

    def _seed_reference(self, employee_id: int, embedding: list[float]) -> None:
        with SessionLocal() as db:
            ref = EmployeeFaceReference(
                employee_id=employee_id,
                log_id_source=None,
                face_embedding=embedding,
                set_by_admin_id=None,
            )
            db.add(ref)
            db.commit()

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _upload(self, log_id: int, token: str, image_bytes: bytes = None):
        return self.client.post(
            "/face/upload",
            headers=self._auth_headers(token),
            data={"log_id": str(log_id)},
            files={"file": ("selfie.jpg", image_bytes or _make_image_bytes(), "image/jpeg")},
        )

    # ── Tests ──────────────────────────────────────────────────────────────

    def test_upload_extracts_embedding_stored_in_log(self) -> None:
        emp_id, log_id, token = self._create_employee_with_token("emp_emb@test.com")
        with patch(
            "app.api.face.extract_embedding",
            return_value=_MATCH_EMBEDDING,
        ):
            response = self._upload(log_id, token)
        self.assertEqual(response.status_code, 200, response.text)
        with SessionLocal() as db:
            log = db.query(AttendanceLog).filter(AttendanceLog.id == log_id).first()
            self.assertIsNotNone(log.face_embedding)
            self.assertEqual(len(log.face_embedding), 512)

    def test_verify_no_reference_returns_skipped(self) -> None:
        emp_id, log_id, token = self._create_employee_with_token("emp_noref@test.com")
        with patch(
            "app.api.face.extract_embedding",
            return_value=_MATCH_EMBEDDING,
        ):
            response = self._upload(log_id, token)
        body = response.json()
        self.assertEqual(body["face_verify_status"], "SKIPPED")
        self.assertIsNone(body["face_match_score"])

    def test_verify_match_high_score(self) -> None:
        emp_id, log_id, token = self._create_employee_with_token("emp_match@test.com")
        self._seed_reference(emp_id, _REF_EMBEDDING)
        with patch(
            "app.api.face.extract_embedding",
            return_value=_MATCH_EMBEDDING,
        ):
            response = self._upload(log_id, token)
        body = response.json()
        self.assertEqual(body["face_verify_status"], "MATCH")
        self.assertGreaterEqual(body["face_match_score"], 0.80)

        # No exception should have been created.
        with SessionLocal() as db:
            exc = (
                db.query(AttendanceException)
                .filter(AttendanceException.source_checkin_log_id == log_id)
                .first()
            )
            self.assertIsNone(exc)

    def test_verify_low_confidence_creates_exception(self) -> None:
        emp_id, log_id, token = self._create_employee_with_token("emp_low@test.com")
        self._seed_reference(emp_id, _REF_EMBEDDING)
        with patch(
            "app.api.face.extract_embedding",
            return_value=_LOW_CONF_EMBEDDING,
        ):
            response = self._upload(log_id, token)
        body = response.json()
        self.assertEqual(body["face_verify_status"], "LOW_CONFIDENCE")
        self.assertGreaterEqual(body["face_match_score"], 0.60)
        self.assertLess(body["face_match_score"], 0.80)

        with SessionLocal() as db:
            exc = (
                db.query(AttendanceException)
                .filter(AttendanceException.source_checkin_log_id == log_id)
                .first()
            )
            self.assertIsNotNone(exc)
            self.assertEqual(exc.exception_type, "FACE_LOW_CONFIDENCE")
            self.assertEqual(exc.status, "PENDING_EMPLOYEE")

    def test_verify_mismatch_creates_exception(self) -> None:
        emp_id, log_id, token = self._create_employee_with_token("emp_mis@test.com")
        self._seed_reference(emp_id, _REF_EMBEDDING)
        with patch(
            "app.api.face.extract_embedding",
            return_value=_MISMATCH_EMBEDDING,
        ):
            response = self._upload(log_id, token)
        body = response.json()
        self.assertEqual(body["face_verify_status"], "MISMATCH")
        self.assertLess(body["face_match_score"], 0.60)

        with SessionLocal() as db:
            exc = (
                db.query(AttendanceException)
                .filter(AttendanceException.source_checkin_log_id == log_id)
                .first()
            )
            self.assertIsNotNone(exc)
            self.assertEqual(exc.exception_type, "FACE_MISMATCH")

    def test_quality_low_skips_verification(self) -> None:
        """QUALITY_LOW images store embedding but verify_status=SKIPPED."""
        emp_id, log_id, token = self._create_employee_with_token("emp_ql@test.com")
        self._seed_reference(emp_id, _REF_EMBEDDING)
        tiny_image = _make_image_bytes(100, 100, color=128)  # below 200x200
        with patch(
            "app.api.face.extract_embedding",
            return_value=_MATCH_EMBEDDING,
        ):
            response = self._upload(log_id, token, image_bytes=tiny_image)
        body = response.json()
        self.assertEqual(body["face_check_status"], "QUALITY_LOW")
        self.assertEqual(body["face_verify_status"], "SKIPPED")
        # No exception created for QUALITY_LOW skipped verification.
        with SessionLocal() as db:
            exc = (
                db.query(AttendanceException)
                .filter(AttendanceException.source_checkin_log_id == log_id)
                .first()
            )
            self.assertIsNone(exc)

    def test_set_reference_ok(self) -> None:
        emp_id, log_id, token = self._create_employee_with_token("emp_setref@test.com")
        with patch(
            "app.api.face.extract_embedding",
            return_value=_MATCH_EMBEDDING,
        ):
            self._upload(log_id, token)
        _, _, admin_token = self._create_employee_with_token("admin_set@test.com", role="ADMIN")
        with patch(
            "app.api.face.extract_embedding",
            return_value=_REF_EMBEDDING,
        ):
            response = self.client.post(
                f"/face/reference/{emp_id}",
                headers=self._auth_headers(admin_token),
                json={"log_id": log_id},
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["employee_id"], emp_id)
        self.assertEqual(body["log_id_source"], log_id)

        # Reference row exists.
        with SessionLocal() as db:
            ref = (
                db.query(EmployeeFaceReference)
                .filter(EmployeeFaceReference.employee_id == emp_id)
                .first()
            )
            self.assertIsNotNone(ref)
            self.assertEqual(len(ref.face_embedding), 512)

    def test_set_reference_wrong_employee_returns_404(self) -> None:
        emp_a, log_a, token_a = self._create_employee_with_token("a@test.com")
        emp_b, _, _ = self._create_employee_with_token("b@test.com")
        with patch(
            "app.api.face.extract_embedding",
            return_value=_MATCH_EMBEDDING,
        ):
            self._upload(log_a, token_a)
        _, _, admin_token = self._create_employee_with_token("admin_404@test.com", role="ADMIN")
        # Try to assign A's log as B's reference → must fail.
        response = self.client.post(
            f"/face/reference/{emp_b}",
            headers=self._auth_headers(admin_token),
            json={"log_id": log_a},
        )
        self.assertEqual(response.status_code, 404)

    def test_get_candidates_returns_top3_unique_days(self) -> None:
        emp_id, _, token = self._create_employee_with_token("emp_cand@test.com")

        # Mix IN/OUT to exercise the per-day de-dup. The base setup already
        # created an IN log on today (work_date=today), so we add OUT/IN on
        # other days to reach 4+ logs spanning 4 unique dates.
        from datetime import date, timedelta
        rows = [
            (0, "OUT"),  # same day as setup IN log
            (1, "IN"),
            (2, "IN"),
            (3, "IN"),
        ]
        with SessionLocal() as db:
            for i, (days_ago, log_type) in enumerate(rows):
                wd = date.today() - timedelta(days=days_ago)
                log = AttendanceLog(
                    employee_id=emp_id,
                    type=log_type,
                    time=datetime.now(timezone.utc) - timedelta(days=days_ago, hours=i),
                    work_date=wd,
                    lat=10.0,
                    lng=106.0,
                    distance_m=0.0,
                    is_out_of_range=False,
                    face_check_status="CAPTURED",
                    face_image_path=f"face/{wd.isoformat()}/{emp_id}/{i}_{log_type.lower()}.jpg",
                )
                db.add(log)
            db.commit()

        _, _, admin_token = self._create_employee_with_token("admin_cand@test.com", role="ADMIN")
        response = self.client.get(
            f"/face/candidates/{emp_id}",
            headers=self._auth_headers(admin_token),
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        candidates = body["candidates"]
        self.assertLessEqual(len(candidates), 3)
        # All unique work_dates.
        dates = [c["work_date"] for c in candidates]
        self.assertEqual(len(dates), len(set(dates)))
        # Current is None — no reference set yet.
        self.assertIsNone(body["current"])

    def test_delete_reference_ok(self) -> None:
        emp_id, _, _ = self._create_employee_with_token("emp_del@test.com")
        self._seed_reference(emp_id, _REF_EMBEDDING)
        _, _, admin_token = self._create_employee_with_token("admin_del@test.com", role="ADMIN")
        response = self.client.delete(
            f"/face/reference/{emp_id}",
            headers=self._auth_headers(admin_token),
        )
        self.assertEqual(response.status_code, 200)
        with SessionLocal() as db:
            ref = (
                db.query(EmployeeFaceReference)
                .filter(EmployeeFaceReference.employee_id == emp_id)
                .first()
            )
            self.assertIsNone(ref)

    def test_get_reference_not_set_returns_404(self) -> None:
        emp_id, _, _ = self._create_employee_with_token("emp_noref2@test.com")
        _, _, admin_token = self._create_employee_with_token("admin_noref@test.com", role="ADMIN")
        response = self.client.get(
            f"/face/reference/{emp_id}",
            headers=self._auth_headers(admin_token),
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
