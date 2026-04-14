from datetime import time

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.sql import func

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), default="USER", nullable=False)
    full_name = Column(String(255), nullable=True)
    phone = Column(String(32), nullable=True)
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


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(128), unique=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


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
    # Work-date cutoff in minutes from 00:00 VN (e.g. 240 = 04:00).
    cross_day_cutoff_minutes = Column(Integer, nullable=True)
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
    phone = Column(String(32), nullable=True)
    # Nullable + unique allows many NULL rows in PostgreSQL, but prevents 1 user linked to many employees.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, unique=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True, index=True)
    active = Column(Boolean, default=True, nullable=False, server_default="true")
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    @property
    def resigned_at(self):
        """Alias for deleted_at — exposed in API responses as resigned_at."""
        return self.deleted_at

    @property
    def joined_at(self):
        """Alias for created_at — exposed in API responses as joined_at."""
        return self.created_at


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
    # Fallback work-date cutoff in minutes from 00:00 VN.
    cross_day_cutoff_minutes = Column(Integer, nullable=False, default=240)
    active = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AttendanceLog(Base):
    __tablename__ = "attendance_logs"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", "type", name="uq_attendance_logs_employee_work_date_type"),
    )

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    type = Column(String(10), nullable=False)  # IN/OUT
    time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Business day based on VN timezone + cross-day cutoff.
    work_date = Column(Date, nullable=True, index=True)

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
    risk_score = Column(Integer, nullable=True)
    risk_level = Column(String(10), nullable=True)
    risk_flags = Column(Text, nullable=True)
    risk_policy_version = Column(String(32), nullable=True)
    ip = Column(String(64), nullable=True)
    ua_hash = Column(String(64), nullable=True)
    accuracy_m = Column(Float, nullable=True)

    # Snapshot at check-in time to keep payroll calculation stable even if rules change later.
    snapshot_start_time = Column(Time, nullable=True)
    snapshot_end_time = Column(Time, nullable=True)
    snapshot_grace_minutes = Column(Integer, nullable=True)
    snapshot_checkout_grace_minutes = Column(Integer, nullable=True)
    snapshot_cutoff_minutes = Column(Integer, nullable=True)
    time_rule_source = Column(String(20), nullable=True)
    time_rule_fallback_reason = Column(String(100), nullable=True)

    address_text = Column(Text, nullable=True)


class AttendanceException(Base):
    __tablename__ = "attendance_exceptions"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    source_checkin_log_id = Column(Integer, ForeignKey("attendance_logs.id"), nullable=False, unique=True)
    exception_type = Column(String(50), nullable=False, index=True)  # MISSED_CHECKOUT/AUTO_CLOSED/SUSPECTED_LOCATION_SPOOF
    work_date = Column(Date, nullable=False)
    # Workflow status is normalized by the Phase 2 migration/validator.
    status = Column(String(20), nullable=False, default="PENDING_EMPLOYEE")
    detected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)
    employee_explanation = Column(Text, nullable=True)
    employee_submitted_at = Column(DateTime(timezone=True), nullable=True)
    admin_note = Column(Text, nullable=True)
    admin_decided_at = Column(DateTime(timezone=True), nullable=True)
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(Text, nullable=True)
    resolved_note = Column(Text, nullable=True)
    actual_checkout_time = Column(DateTime(timezone=True), nullable=True)
    extended_deadline_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class AttendanceExceptionAudit(Base):
    __tablename__ = "attendance_exception_audits"

    id = Column(Integer, primary_key=True)
    exception_id = Column(Integer, ForeignKey("attendance_exceptions.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    previous_status = Column(String(20), nullable=True)
    next_status = Column(String(20), nullable=False)
    actor_type = Column(String(20), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_email = Column(String(255), nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ExceptionPolicy(Base):
    __tablename__ = "exception_policies"

    id = Column(Integer, primary_key=True, default=1)
    default_deadline_hours = Column(Integer, nullable=False, default=72)
    auto_closed_deadline_hours = Column(Integer, nullable=True)
    missed_checkout_deadline_hours = Column(Integer, nullable=True)
    location_risk_deadline_hours = Column(Integer, nullable=True)
    large_time_deviation_deadline_hours = Column(Integer, nullable=True)
    grace_period_days = Column(Integer, nullable=False, default=30)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)


class AttendanceExceptionNotification(Base):
    __tablename__ = "attendance_exception_notifications"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_attendance_exception_notifications_dedupe_key"),
    )

    id = Column(Integer, primary_key=True)
    exception_id = Column(Integer, ForeignKey("attendance_exceptions.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    recipient_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    recipient_email = Column(String(255), nullable=False)
    recipient_role = Column(String(20), nullable=False)
    dedupe_key = Column(String(160), nullable=False)
    status = Column(String(20), nullable=False, default="QUEUED")
    sent_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

