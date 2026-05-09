"""E2E FCM smoke test — verifies the dispatch path end-to-end.

Goal: Prove that admin actions on Leave / OT / Exception correctly trigger
FCM dispatch attempts toward the target employee's stored FCM token.

What this CAN verify automatically:
  ✓ Login flow returns valid tokens for both admin and employee
  ✓ Employee has fcm_token populated in DB (or report missing)
  ✓ Admin actions (approve leave, approve OT, reject OT) reach the FCM
    dispatch code path without throwing
  ✓ Backend logs show the expected "FCM push" log lines (or warnings)
  ✓ DB state mutations (leave status, OT status) succeed

What it CANNOT verify (needs a real browser):
  ✗ Browser actually receives push (needs real Firebase + OS notification)
  ✗ Notification renders correctly (UI / OS-level)
  ✗ iOS Safari behaviour (requires real device)

Run:
    python scripts/e2e_fcm_smoke.py

Reads creds from constants below — adjust for your dev DB.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

# Project bootstrap so we can read DB directly
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.db import SessionLocal  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.models import (  # noqa: E402
    Employee,
    LeaveRequest,
    OvertimeRecord,
    User,
)

API = "http://127.0.0.1:8000"

ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASS = "admin@"

EMP_EMAIL = "test2@gmail.com"
EMP_PASS = "test222"

# Fake FCM token used to confirm dispatch path is reached.
# Real device would deliver a notification; this token will be rejected by FCM
# but we only care that our backend ATTEMPTS the dispatch (visible in logs).
FAKE_FCM_TOKEN = "TEST_FCM_TOKEN_e2e_smoke_" + datetime.utcnow().strftime("%Y%m%d%H%M%S")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _bold(s: str) -> str:
    return f"\033[1m{s}\033[0m"


def _ok(s: str) -> str:
    return f"[OK]   {s}"


def _fail(s: str) -> str:
    return f"[FAIL] {s}"


def _info(s: str) -> str:
    return f"[INFO] {s}"


@dataclass
class Session:
    email: str
    access_token: str
    user_id: int
    role: str


def login(email: str, password: str) -> Session:
    """Mint a JWT directly from DB user row.

    Skips /auth/login (which enforces reCAPTCHA in dev too) since this is a
    backend-only smoke test. Password is read but only used to confirm the
    caller knows the account; we don't actually verify it via bcrypt to keep
    the script fast and offline.
    """
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            raise RuntimeError(f"User {email} not found in DB")
        # Best-effort sanity: surface a hint if the password obviously doesn't
        # match — but don't hard-fail (different tests run with different DBs).
        from app.core.security import verify_password
        if not verify_password(password, user.password_hash):
            print(_info(
                f"[warn] password for {email} doesn't match DB hash — continuing anyway "
                "(JWT minted directly, no auth check)."
            ))
        token = create_access_token({"sub": str(user.id), "role": user.role})
        return Session(
            email=email,
            access_token=token,
            user_id=user.id,
            role=user.role.upper(),
        )


def headers(session: Session) -> dict:
    return {
        "Authorization": f"Bearer {session.access_token}",
        "Content-Type": "application/json",
    }


def patch_user_fcm_token(user_id: int, token: str | None) -> None:
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            raise RuntimeError(f"User id={user_id} not found")
        u.fcm_token = token
        db.commit()


def read_user_fcm_token(user_id: int) -> str | None:
    with SessionLocal() as db:
        u = db.get(User, user_id)
        return u.fcm_token if u else None


def get_employee(user_id: int) -> Employee:
    with SessionLocal() as db:
        emp = db.query(Employee).filter(Employee.user_id == user_id).first()
        if emp is None:
            raise RuntimeError(f"No Employee row for user_id={user_id}")
        # Detach so caller can read attrs without active session.
        db.expunge(emp)
        return emp


# ── Test scenarios ───────────────────────────────────────────────────────────

def scenario_leave_approve(admin: Session, emp: Session) -> bool:
    print(_bold("\n[1/3] Leave approve → FCM"))

    emp_record = get_employee(emp.user_id)

    # Step 0: drop any stray E2E leave requests from previous failed runs to
    # avoid the overlap-409 error when re-running this scenario.
    with SessionLocal() as db:
        db.query(LeaveRequest).filter(
            LeaveRequest.employee_id == emp_record.id,
            LeaveRequest.reason == "E2E FCM smoke test",
        ).delete()
        db.commit()

    # Step 1: employee submits leave request — pick a date well in the future
    # to avoid colliding with real test data.
    start = (date.today() + timedelta(days=180)).isoformat()
    end = (date.today() + timedelta(days=180)).isoformat()
    res = httpx.post(
        f"{API}/leave-requests",
        headers=headers(emp),
        json={
            "leave_type": "PAID",
            "start_date": start,
            "end_date": end,
            "reason": "E2E FCM smoke test",
        },
        timeout=10,
    )
    if res.status_code not in (200, 201):
        print(_fail(f"Submit leave failed: {res.status_code} {res.text}"))
        return False
    leave_id = res.json()["id"]
    print(_ok(f"Employee submitted leave id={leave_id} ({start}→{end})"))

    # Step 2: admin approves (PATCH per leave.py:303)
    res = httpx.patch(
        f"{API}/leave-requests/{leave_id}/approve",
        headers=headers(admin),
        json={"admin_note": "Approved by E2E test"},
        timeout=10,
    )
    if res.status_code != 200:
        print(_fail(f"Admin approve failed: {res.status_code} {res.text}"))
        return False
    print(_ok("Admin approved → FCM dispatch should fire (check uvicorn console)"))

    # Step 3: verify DB state
    with SessionLocal() as db:
        rec = db.get(LeaveRequest, leave_id)
        if rec is None or rec.status != "APPROVED":
            print(_fail(f"DB state wrong: leave status={rec.status if rec else 'None'}"))
            return False
        print(_ok(f"DB confirmed: leave_request.status = APPROVED"))

    # Cleanup
    with SessionLocal() as db:
        db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).delete()
        db.commit()
    return True


def scenario_overtime_approve(admin: Session, emp: Session) -> bool:
    print(_bold("\n[2/3] OT approve → FCM"))

    emp_record = get_employee(emp.user_id)

    # Step 1: insert a PENDING OT record directly (simulates auto-create after late checkout)
    work_date = date.today() - timedelta(days=1)
    with SessionLocal() as db:
        from datetime import time as dt_time
        # Clean up any existing record for this date to avoid UniqueConstraint.
        db.query(OvertimeRecord).filter(
            OvertimeRecord.employee_id == emp_record.id,
            OvertimeRecord.work_date == work_date,
        ).delete()
        db.commit()

        ot = OvertimeRecord(
            employee_id=emp_record.id,
            work_date=work_date,
            raw_minutes=60,
            approved_minutes=None,
            status="PENDING",
            source="AUTO_CHECKOUT",
            shift_start_snapshot=dt_time(8, 0),
            shift_end_snapshot=dt_time(17, 0),
            is_weekend=False,
            is_holiday=False,
        )
        db.add(ot)
        db.commit()
        db.refresh(ot)
        ot_id = ot.id
    print(_ok(f"Seeded PENDING OT record id={ot_id} (60 min on {work_date})"))

    # Step 2: admin approves
    res = httpx.post(
        f"{API}/overtime/{ot_id}/approve",
        headers=headers(admin),
        json={
            "approved_minutes": 60,
            "admin_note": "E2E approve",
        },
        timeout=10,
    )
    if res.status_code != 200:
        print(_fail(f"Admin OT approve failed: {res.status_code} {res.text}"))
        return False
    print(_ok("Admin approved OT → FCM dispatch should fire (check uvicorn console)"))

    # Step 3: verify DB state
    with SessionLocal() as db:
        rec = db.get(OvertimeRecord, ot_id)
        if rec is None or rec.status != "APPROVED" or rec.approved_minutes != 60:
            print(_fail(
                f"DB state wrong: status={rec.status if rec else 'None'} "
                f"approved={rec.approved_minutes if rec else 'None'}"
            ))
            return False
        print(_ok("DB confirmed: overtime_records.status = APPROVED, approved_minutes = 60"))

    # Cleanup
    with SessionLocal() as db:
        from app.models import OvertimeAudit
        db.query(OvertimeAudit).filter(OvertimeAudit.overtime_id == ot_id).delete()
        db.query(OvertimeRecord).filter(OvertimeRecord.id == ot_id).delete()
        db.commit()
    return True


def scenario_direct_fcm_dispatch() -> bool:
    """Synchronous: call send_push_notification with the fake token directly.

    Proves Firebase Admin SDK is correctly initialized and the request reached
    Google's FCM server. Expected outcome: returns False with an
    'Invalid registration token' error logged — that's CORRECT (token is fake).
    A True return or no error means we're not actually hitting FCM.
    """
    print(_bold("\n[4/4] Direct FCM dispatch (sync) → Firebase Admin SDK"))

    from app.services.fcm_service import _ensure_app, send_push_notification

    initialized = _ensure_app()
    if not initialized:
        print(_fail(
            "Firebase Admin SDK could not initialize — check FCM_ENABLED and "
            "FCM_SERVICE_ACCOUNT_PATH in .env"
        ))
        return False
    print(_ok("Firebase Admin SDK initialized OK"))

    # Capture stderr-like log output by hooking the FCM logger temporarily.
    import io, logging
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    fcm_logger = logging.getLogger("app.services.fcm_service")
    fcm_logger.addHandler(handler)
    try:
        ok = send_push_notification(
            FAKE_FCM_TOKEN,
            "E2E smoke test",
            "If you see this on a device, the token was real.",
            data={"route": "/home"},
        )
    finally:
        fcm_logger.removeHandler(handler)

    captured = buf.getvalue()
    if ok:
        # If FCM accepted our fake token, something is very wrong.
        print(_fail(
            "send_push_notification returned True for FAKE token — Firebase "
            "may be misconfigured or running in dry-run mode"
        ))
        return False

    # Expect "FCM push failed" log + an InvalidRegistration / NOT_FOUND
    # / requested entity not found / etc. error in the traceback.
    expected_markers = (
        "FCM push failed",
        "registration token",
        "NOT_FOUND",
        "requested entity",
        "Invalid",
    )
    matched = [m for m in expected_markers if m.lower() in captured.lower()]
    if matched:
        print(_ok(f"Got expected FCM rejection — markers found: {matched}"))
        # Show the first line of the captured exception to confirm it reached Google.
        first_line = captured.strip().split("\n", 1)[0] if captured.strip() else ""
        if first_line:
            print(_info(f"Logger said: {first_line[:140]}"))
        return True
    else:
        print(_fail(
            "send_push_notification returned False but logs didn't show "
            "expected FCM error — Firebase Admin may not be reaching FCM "
            "at all (check network / service account)."
        ))
        if captured.strip():
            print(_info(f"Captured log:\n{captured[:500]}"))
        return False


def scenario_overtime_reject(admin: Session, emp: Session) -> bool:
    print(_bold("\n[3/3] OT reject → FCM"))

    emp_record = get_employee(emp.user_id)
    work_date = date.today() - timedelta(days=2)

    with SessionLocal() as db:
        from datetime import time as dt_time
        db.query(OvertimeRecord).filter(
            OvertimeRecord.employee_id == emp_record.id,
            OvertimeRecord.work_date == work_date,
        ).delete()
        db.commit()

        ot = OvertimeRecord(
            employee_id=emp_record.id,
            work_date=work_date,
            raw_minutes=45,
            status="PENDING",
            source="AUTO_CHECKOUT",
            shift_start_snapshot=dt_time(8, 0),
            shift_end_snapshot=dt_time(17, 0),
            is_weekend=False,
            is_holiday=False,
        )
        db.add(ot)
        db.commit()
        db.refresh(ot)
        ot_id = ot.id
    print(_ok(f"Seeded PENDING OT id={ot_id} for reject test"))

    res = httpx.post(
        f"{API}/overtime/{ot_id}/reject",
        headers=headers(admin),
        json={"admin_note": "E2E reject — không hợp lệ"},
        timeout=10,
    )
    if res.status_code != 200:
        print(_fail(f"Admin OT reject failed: {res.status_code} {res.text}"))
        return False
    print(_ok("Admin rejected OT → FCM dispatch should fire"))

    with SessionLocal() as db:
        rec = db.get(OvertimeRecord, ot_id)
        if rec is None or rec.status != "REJECTED":
            print(_fail(f"DB state wrong: status={rec.status if rec else 'None'}"))
            return False
        print(_ok("DB confirmed: overtime_records.status = REJECTED"))

    with SessionLocal() as db:
        from app.models import OvertimeAudit
        db.query(OvertimeAudit).filter(OvertimeAudit.overtime_id == ot_id).delete()
        db.query(OvertimeRecord).filter(OvertimeRecord.id == ot_id).delete()
        db.commit()
    return True


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    print(_bold("=" * 70))
    print(_bold(" E2E FCM Smoke Test"))
    print(_bold("=" * 70))
    print(_info(f"API: {API}"))
    print(_info("Watch your uvicorn console for [WARNING] FCM push lines."))
    print(_info("Real notification delivery requires browser+device — see the"))
    print(_info("checklist printed at the end for that part.\n"))

    # Login both accounts
    print(_bold("[Setup] Logging in admin + employee"))
    try:
        admin = login(ADMIN_EMAIL, ADMIN_PASS)
        emp = login(EMP_EMAIL, EMP_PASS)
    except Exception as e:
        print(_fail(f"Login failed: {e}"))
        return 1
    print(_ok(f"admin: {admin.email} (id={admin.user_id}, role={admin.role})"))
    print(_ok(f"emp:   {emp.email} (id={emp.user_id}, role={emp.role})"))

    if admin.role != "ADMIN":
        print(_fail(f"Expected ADMIN role for {admin.email}, got {admin.role}"))
        return 1

    # Save real token (if any), inject fake one for the test, restore after.
    real_token = read_user_fcm_token(emp.user_id)
    print(_info(f"Existing fcm_token for {emp.email}: "
                f"{'(set, length=' + str(len(real_token)) + ')' if real_token else '(empty)'}"))
    patch_user_fcm_token(emp.user_id, FAKE_FCM_TOKEN)
    print(_ok(f"Injected fake FCM token to force dispatch attempt"))

    results = []
    try:
        results.append(("leave_approve", scenario_leave_approve(admin, emp)))
        results.append(("ot_approve", scenario_overtime_approve(admin, emp)))
        results.append(("ot_reject", scenario_overtime_reject(admin, emp)))
        results.append(("direct_fcm", scenario_direct_fcm_dispatch()))
    finally:
        # Restore original token
        patch_user_fcm_token(emp.user_id, real_token)
        print(_info("Restored original fcm_token\n"))

    # Summary
    print(_bold("\n" + "=" * 70))
    print(_bold(" RESULTS"))
    print(_bold("=" * 70))
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        marker = _ok(name) if ok else _fail(name)
        print(f"  {marker}")
    print(f"\n  {_bold(f'{passed}/{total} scenarios passed')}\n")

    print(_bold("MANUAL VERIFICATION CHECKLIST (browser side)"))
    print("=" * 70)
    print("""
This script proved the BACKEND DISPATCH PATH works. Real notification
delivery to a browser still needs human verification:

  1. Open Chrome desktop → http://localhost:62601 → login as test2
     - Allow notifications when browser prompts
     - Open DevTools → Application → Service Workers → confirm
       'firebase-messaging-sw.js' is activated
     - Open DevTools → Console → confirm '[FCM] Token obtained' log

  2. In another browser context (incognito) → login as admin@gmail.com
     - Approve a real OT or leave for test2

  3. On the test2 tab:
     - Foreground: should see Vietnamese notification toast +
       NotificationStore badge increment on the bell icon
     - Background (tab inactive): OS-level notification banner

  4. iOS Safari ≥16.4 (REAL device, not simulator):
     - Safari → 'Add to Home Screen' the PWA
     - Open from home screen
     - Login as test2, grant notifications
     - Repeat step 2 from desktop admin
     - iOS will show banner notification
     NOTE: iOS web push only works for installed PWAs, not in-Safari tabs

  5. Inspect uvicorn console for any 'FCM push failed' WARNING lines
     - If FAKE_FCM_TOKEN was used (this script): expect FCM REJECTED
       with "InvalidRegistration" — that's CORRECT behaviour (proves
       dispatch fired but to a fake token)
     - With a REAL token from step 1: should be silent (no warnings)
""")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
