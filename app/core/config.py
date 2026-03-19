from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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
    MAIL_FROM: str = ""
    RESET_PASSWORD_URL_BASE: str = "http://localhost:62601/#/reset-password"

    class Config:
        env_file = ".env"


settings = Settings()
