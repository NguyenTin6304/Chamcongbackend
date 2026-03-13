from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional
from uuid import uuid4

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def create_access_token(data: dict, expires_minutes: Optional[int] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    to_encode.update({"exp": expire, "typ": "access", "jti": str(uuid4())})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(
    data: dict,
    expires_days: Optional[int] = None,
    token_id: Optional[str] = None,
) -> tuple[str, datetime, str]:
    to_encode = data.copy()
    jti = token_id or str(uuid4())
    expire = datetime.now(timezone.utc) + timedelta(
        days=expires_days or settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    to_encode.update({"exp": expire, "typ": "refresh", "jti": jti})
    token = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, expire, jti


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def decode_access_token(token: str) -> dict:
    payload = decode_token(token)
    token_type = payload.get("typ")
    if token_type and token_type != "access":
        raise ValueError("Invalid token type")
    return payload


def decode_refresh_token(token: str) -> dict:
    payload = decode_token(token)
    token_type = payload.get("typ")
    if token_type != "refresh":
        raise ValueError("Invalid token type")
    if not payload.get("jti"):
        raise ValueError("Missing jti")
    return payload

