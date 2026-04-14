import re

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    full_name: str = Field(min_length=2, max_length=255)
    phone: str = Field(min_length=10, max_length=20)

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        name = " ".join(value.strip().split())
        if len(name) < 2:
            raise ValueError("Họ và tên phải có ít nhất 2 ký tự")
        if any(ch.isdigit() for ch in name):
            raise ValueError("Họ và tên không được chứa số")
        return name

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        phone = re.sub(r"[\s.-]", "", value.strip())
        if not re.fullmatch(r"\d{10,11}", phone):
            raise ValueError("Số điện thoại phải chứa từ 10 đến 11 chữ số")
        return phone


class RegisterResponse(BaseModel):
    id: int
    email: EmailStr
    role: str
    full_name: str | None = None
    phone: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    remember_me: bool = True
    recaptcha_token: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    access_expires_in_minutes: int | None = None
    refresh_expires_in_days: int | None = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserMeResponse(BaseModel):
    id: int
    email: EmailStr
    role: str
    full_name: str | None = None
    phone: str | None = None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=6, max_length=128)


class MessageResponse(BaseModel):
    message: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)
