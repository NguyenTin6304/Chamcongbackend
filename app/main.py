import logging
import threading

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.attendance import router as attendance_router
from app.api.auth import router as auth_router
from app.api.employees import router as employees_router
from app.api.geofences import router as geofences_router
from app.api.groups import router as groups_router
from app.api.leave import router as leave_router
from app.api.reports import router as reports_router
from app.api.rules import router as rules_router
from app.api.users import router as users_router
from app.core.config import settings
from app.core.db import Base, SessionLocal, engine
from app.services.auth.password_reset_service import cleanup_password_reset_tokens

app = FastAPI(title="Attendance API")
logger = logging.getLogger(__name__)

_cleanup_stop_event = threading.Event()
_cleanup_thread: threading.Thread | None = None


# Allow local Flutter web (localhost with any port) and Vercel deployments.
# Avoid using "*" with credentials because browsers can block those requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$|^https://([a-zA-Z0-9-]+\.)*vercel\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _password_reset_cleanup_loop() -> None:
    interval_hours = max(1, int(settings.PASSWORD_RESET_CLEANUP_INTERVAL_HOURS))
    interval_seconds = interval_hours * 3600
    retention_days = max(0, int(settings.PASSWORD_RESET_USED_TOKEN_RETENTION_DAYS))

    while not _cleanup_stop_event.is_set():
        try:
            with SessionLocal() as db:
                deleted = cleanup_password_reset_tokens(db, used_retention_days=retention_days)
                db.commit()
            if deleted > 0:
                logger.info("password_reset_tokens cleanup deleted %s rows", deleted)
        except Exception:  # pragma: no cover - defensive background loop
            logger.exception("password_reset_tokens cleanup failed")

        if _cleanup_stop_event.wait(interval_seconds):
            break


@app.on_event("startup")
def startup_create_tables() -> None:
    # Keep Alembic as source of truth. Enable only for quick local demos.
    if settings.AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)

    if settings.PASSWORD_RESET_CLEANUP_ENABLED:
        global _cleanup_thread
        if _cleanup_thread is None or not _cleanup_thread.is_alive():
            _cleanup_stop_event.clear()
            _cleanup_thread = threading.Thread(
                target=_password_reset_cleanup_loop,
                name="password-reset-cleanup",
                daemon=True,
            )
            _cleanup_thread.start()


@app.on_event("shutdown")
def shutdown_background_jobs() -> None:
    global _cleanup_thread
    _cleanup_stop_event.set()
    if _cleanup_thread and _cleanup_thread.is_alive():
        _cleanup_thread.join(timeout=3)
    _cleanup_thread = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"message": "API running"}


def _status_code_to_error_code(status_code: int) -> str:
    mapping = {
        status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
        status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
        status.HTTP_403_FORBIDDEN: "FORBIDDEN",
        status.HTTP_404_NOT_FOUND: "NOT_FOUND",
        status.HTTP_409_CONFLICT: "CONFLICT",
        status.HTTP_422_UNPROCESSABLE_ENTITY: "VALIDATION_ERROR",
        status.HTTP_500_INTERNAL_SERVER_ERROR: "INTERNAL_SERVER_ERROR",
    }
    return mapping.get(status_code, f"HTTP_{status_code}")


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        error_payload = detail
    else:
        error_payload = {
            "code": _status_code_to_error_code(exc.status_code),
            "message": str(detail),
        }

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": error_payload,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request data",
                "details": exc.errors(),
            },
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception):
    logger.exception("Unhandled exception", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Unexpected server error",
            },
        },
    )


app.include_router(employees_router)
app.include_router(groups_router)
app.include_router(geofences_router)
app.include_router(attendance_router)
app.include_router(leave_router)
app.include_router(reports_router)
app.include_router(rules_router)
app.include_router(auth_router)
app.include_router(users_router)
