import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.attendance import router as attendance_router
from app.api.auth import router as auth_router
from app.api.employees import router as employees_router
from app.api.groups import router as groups_router
from app.api.reports import router as reports_router
from app.api.rules import router as rules_router
from app.api.users import router as users_router
from app.core.config import settings
from app.core.db import Base, engine

app = FastAPI(title="Attendance API")
logger = logging.getLogger(__name__)

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


@app.on_event("startup")
def startup_create_tables() -> None:
    # Keep Alembic as source of truth. Enable only for quick local demos.
    if settings.AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)


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
app.include_router(attendance_router)
app.include_router(reports_router)
app.include_router(rules_router)
app.include_router(auth_router)
app.include_router(users_router)
