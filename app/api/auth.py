from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserMeResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_token(db: Session, email: str, password: str) -> TokenResponse:
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token({"sub": str(user.id), "role": user.role})
    return TokenResponse(access_token=token)


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    try:
        user = User(
            email=payload.email,
            password_hash=hash_password(payload.password),
            role="USER",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return RegisterResponse(id=user.id, email=user.email, role=user.role)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email already exists")


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    # Main login endpoint for frontend/mobile: JSON body {email, password}
    return _issue_token(db, payload.email, payload.password)


@router.post("/login-form", response_model=TokenResponse, deprecated=True)
def login_form(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # Legacy compatibility endpoint: form-data (username/password).
    return _issue_token(db, form_data.username, form_data.password)


@router.post("/login-json", response_model=TokenResponse, deprecated=True)
def login_json(payload: LoginRequest, db: Session = Depends(get_db)):
    # Deprecated alias of /auth/login for backward compatibility.
    return _issue_token(db, payload.email, payload.password)


@router.get("/me", response_model=UserMeResponse)
def me(current_user: User = Depends(get_current_user)):
    return UserMeResponse(id=current_user.id, email=current_user.email, role=current_user.role)