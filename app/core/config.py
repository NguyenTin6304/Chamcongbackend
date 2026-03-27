from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        case_sensitive=False,
        extra="ignore",
    )

    DATABASE_URL: str
    SECRET_KEY: str = Field(min_length=16)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS_NO_REMEMBER: int = 1
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 15
    PASSWORD_RESET_CLEANUP_ENABLED: bool = True
    PASSWORD_RESET_CLEANUP_INTERVAL_HOURS: int = 24
    PASSWORD_RESET_USED_TOKEN_RETENTION_DAYS: int = 1
    # Keep False by default so schema is managed by Alembic migrations.
    AUTO_CREATE_TABLES: bool = False

    MAIL_ENABLED: bool = False
    MAIL_PROVIDER: str = "noop"
    SMTP_HOST: str = "smtp.zoho.com"
    SMTP_PORT: int = 587
    SMTP_TLS: bool = True
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_TIMEOUT_SEC: int = 8
    SMTP_RETRY_ATTEMPTS: int = 2
    SMTP_RETRY_DELAY_SEC: float = 1.0
    # Backward-compatible keys sometimes used in Render env.
    MAIL_TIMEOUT_SEC: int = 8
    MAIL_RETRY_ATTEMPTS: int = 2
    MAIL_RETRY_DELAY_SEC: float = 1.0
    MAIL_FROM: str = ""
    MAIL_FALLBACK_PROVIDER: str = "none"
    MAIL_HTTP_ENDPOINT: str = ""
    MAIL_HTTP_API_KEY: str = ""
    MAIL_HTTP_TIMEOUT_SEC: int = 8
    RESEND_API_KEY: str = ""
    RESEND_ENDPOINT: str = "https://api.resend.com/emails"
    RESEND_TIMEOUT_SEC: int = 8
    RESEND_RETRY_ATTEMPTS: int = 2
    RESEND_RETRY_DELAY_SEC: float = 1.0
    RESET_PASSWORD_URL_BASE: str = "http://localhost:62601/#/reset-password"

    RECAPTCHA_ENABLED: bool = False
    RECAPTCHA_SECRET_KEY: str = ""
    RECAPTCHA_MIN_SCORE: float = 0.5
    RECAPTCHA_VERIFY_TIMEOUT_SEC: int = 5
    RECAPTCHA_EXPECTED_ACTION: str = ""
    # Optional comma-separated hostnames. Example: "localhost,chamcongweb.vercel.app"
    RECAPTCHA_ALLOWED_HOSTNAMES: str = ""
    RISK_POLICY_VERSION: str = "v1"


settings = Settings()



