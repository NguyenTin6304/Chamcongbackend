from datetime import time

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, Time
from sqlalchemy.sql import func

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), default="USER", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    jti = Column(String(64), unique=True, nullable=False, index=True)
    token_hash = Column(String(128), unique=True, nullable=False)
    remember_me = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    replaced_by_jti = Column(String(64), nullable=True)


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    # Optional per-group time rule. If missing, system active rule is used as fallback.
    start_time = Column(Time, nullable=True)
    grace_minutes = Column(Integer, nullable=True)
    end_time = Column(Time, nullable=True)
    checkout_grace_minutes = Column(Integer, nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GroupGeofence(Base):
    __tablename__ = "group_geofences"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    radius_m = Column(Integer, nullable=False, default=200)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    full_name = Column(String(255), nullable=False)
    # Nullable + unique allows many NULL rows in PostgreSQL, but prevents 1 user linked to many employees.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, unique=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CheckinRule(Base):
    __tablename__ = "checkin_rules"

    id = Column(Integer, primary_key=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    radius_m = Column(Integer, nullable=False, default=200)
    # Shift start time in Vietnam timezone (HH:MM).
    start_time = Column(Time, nullable=False, default=time(8, 0))
    # Minutes allowed after start_time to still be ON_TIME.
    grace_minutes = Column(Integer, nullable=False, default=30)
    # Shift end time in Vietnam timezone (HH:MM).
    end_time = Column(Time, nullable=False, default=time(17, 30))
    # Minutes allowed after end_time to still be ON_TIME for checkout.
    checkout_grace_minutes = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AttendanceLog(Base):
    __tablename__ = "attendance_logs"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    type = Column(String(10), nullable=False)  # IN/OUT
    time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)

    distance_m = Column(Float, nullable=True)
    is_out_of_range = Column(Boolean, default=False, nullable=False)
    # Only set for IN logs: EARLY/ON_TIME/LATE.
    punctuality_status = Column(String(20), nullable=True)
    # Only set for OUT logs: EARLY/ON_TIME/LATE.
    checkout_status = Column(String(20), nullable=True)
    matched_geofence_name = Column(String(255), nullable=True)
    geofence_source = Column(String(20), nullable=True)
    fallback_reason = Column(String(100), nullable=True)
    address_text = Column(Text, nullable=True)


class AttendanceException(Base):
    __tablename__ = "attendance_exceptions"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    source_checkin_log_id = Column(Integer, ForeignKey("attendance_logs.id"), nullable=False, unique=True)
    exception_type = Column(String(50), nullable=False, index=True)  # MISSED_CHECKOUT/AUTO_CLOSED
    work_date = Column(Date, nullable=False)
    status = Column(String(20), nullable=False, default="OPEN")  # OPEN/RESOLVED
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
