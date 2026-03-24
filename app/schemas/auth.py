from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class RegisterResponse(BaseModel):
    id: int
    email: EmailStr
    role: str


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


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=6, max_length=128)


class MessageResponse(BaseModel):
    message: str
