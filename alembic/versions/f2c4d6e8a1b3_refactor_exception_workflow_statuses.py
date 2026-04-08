"""refactor exception workflow statuses

Revision ID: f2c4d6e8a1b3
Revises: e6f7a8b9c0d1
Create Date: 2026-04-06 11:30:00.000000

Risk note:
- Legacy OPEN rows are mapped to PENDING_EMPLOYEE because the old workflow did not
  persist enough information to distinguish PENDING_EMPLOYEE vs PENDING_ADMIN.
- Legacy RESOLVED rows are mapped to APPROVED because the old workflow did not
  distinguish APPROVED vs REJECTED as terminal states.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2c4d6e8a1b3"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DECIDED_BY_FK = "fk_attendance_exceptions_decided_by_users"
STATUS_CHECK = "ck_attendance_exceptions_workflow_status"
WORKFLOW_STATUSES = (
    "PENDING_EMPLOYEE",
    "PENDING_ADMIN",
    "APPROVED",
    "REJECTED",
    "EXPIRED",
)


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _has_fk(inspector, table_name: str, constrained_column: str, referred_table: str) -> bool:
    for fk in inspector.get_foreign_keys(table_name):
        cols = fk.get("constrained_columns") or []
        if cols == [constrained_column] and fk.get("referred_table") == referred_table:
            return True
    return False


def _has_check(inspector, table_name: str, check_name: str) -> bool:
    return any((check.get("name") or "") == check_name for check in inspector.get_check_constraints(table_name))


def _normalize_legacy_status(status: str | None) -> str:
    normalized = (status or "").strip().upper()
    if normalized == "OPEN":
        return "PENDING_EMPLOYEE"
    if normalized == "RESOLVED":
        return "APPROVED"
    if normalized in WORKFLOW_STATUSES:
        return normalized
    return "PENDING_EMPLOYEE"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("attendance_exceptions"):
        return

    if not _has_column(inspector, "attendance_exceptions", "detected_at"):
        op.add_column(
            "attendance_exceptions",
            sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("now()")),
        )
        inspector = sa.inspect(bind)

    if not _has_column(inspector, "attendance_exceptions", "expires_at"):
        op.add_column(
            "attendance_exceptions",
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
        inspector = sa.inspect(bind)

    if not _has_column(inspector, "attendance_exceptions", "employee_explanation"):
        op.add_column("attendance_exceptions", sa.Column("employee_explanation", sa.Text(), nullable=True))
        inspector = sa.inspect(bind)

    if not _has_column(inspector, "attendance_exceptions", "employee_submitted_at"):
        op.add_column(
            "attendance_exceptions",
            sa.Column("employee_submitted_at", sa.DateTime(timezone=True), nullable=True),
        )
        inspector = sa.inspect(bind)

    if not _has_column(inspector, "attendance_exceptions", "admin_note"):
        op.add_column("attendance_exceptions", sa.Column("admin_note", sa.Text(), nullable=True))
        inspector = sa.inspect(bind)

    if not _has_column(inspector, "attendance_exceptions", "admin_decided_at"):
        op.add_column(
            "attendance_exceptions",
            sa.Column("admin_decided_at", sa.DateTime(timezone=True), nullable=True),
        )
        inspector = sa.inspect(bind)

    if not _has_column(inspector, "attendance_exceptions", "decided_by"):
        op.add_column("attendance_exceptions", sa.Column("decided_by", sa.Integer(), nullable=True))
        inspector = sa.inspect(bind)

    if _has_column(inspector, "attendance_exceptions", "decided_by") and not _has_fk(
        inspector,
        "attendance_exceptions",
        "decided_by",
        "users",
    ):
        op.create_foreign_key(
            DECIDED_BY_FK,
            "attendance_exceptions",
            "users",
            ["decided_by"],
            ["id"],
            ondelete="SET NULL",
        )
        inspector = sa.inspect(bind)

    rows = bind.execute(
        sa.text(
            """
            SELECT
                id,
                status,
                created_at,
                detected_at,
                expires_at,
                resolved_note,
                resolved_at,
                resolved_by,
                admin_note,
                admin_decided_at,
                decided_by
            FROM attendance_exceptions
            """
        )
    ).mappings()

    now_utc = datetime.now(timezone.utc)
    for row in rows:
        detected_at = row["detected_at"] or row["created_at"] or now_utc
        next_status = _normalize_legacy_status(row["status"])
        expires_at = row["expires_at"]
        if next_status == "PENDING_EMPLOYEE" and expires_at is None:
            expires_at = detected_at + timedelta(days=3)

        admin_note = row["admin_note"] or row["resolved_note"]
        admin_decided_at = row["admin_decided_at"] or row["resolved_at"]
        decided_by = row["decided_by"] or row["resolved_by"]

        bind.execute(
            sa.text(
                """
                UPDATE attendance_exceptions
                SET
                    status = :status,
                    detected_at = :detected_at,
                    expires_at = :expires_at,
                    admin_note = :admin_note,
                    admin_decided_at = :admin_decided_at,
                    decided_by = :decided_by
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "status": next_status,
                "detected_at": detected_at,
                "expires_at": expires_at,
                "admin_note": admin_note,
                "admin_decided_at": admin_decided_at,
                "decided_by": decided_by,
            },
        )

    with op.batch_alter_table("attendance_exceptions") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=20),
            nullable=False,
            server_default=sa.text("'PENDING_EMPLOYEE'"),
        )
        batch_op.alter_column("detected_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        if not _has_check(inspector, "attendance_exceptions", STATUS_CHECK):
            batch_op.create_check_constraint(
                STATUS_CHECK,
                "status IN ('PENDING_EMPLOYEE', 'PENDING_ADMIN', 'APPROVED', 'REJECTED', 'EXPIRED')",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("attendance_exceptions"):
        return

    bind.execute(
        sa.text(
            """
            UPDATE attendance_exceptions
            SET status = CASE
                WHEN status = 'APPROVED' THEN 'RESOLVED'
                WHEN status = 'REJECTED' THEN 'RESOLVED'
                WHEN status = 'EXPIRED' THEN 'OPEN'
                WHEN status = 'PENDING_ADMIN' THEN 'OPEN'
                ELSE 'OPEN'
            END
            """
        )
    )

    with op.batch_alter_table("attendance_exceptions") as batch_op:
        if _has_check(inspector, "attendance_exceptions", STATUS_CHECK):
            batch_op.drop_constraint(STATUS_CHECK, type_="check")
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=20),
            nullable=False,
            server_default=sa.text("'OPEN'"),
        )
        batch_op.alter_column("detected_at", existing_type=sa.DateTime(timezone=True), nullable=True)

    inspector = sa.inspect(bind)
    existing_fk_names = {fk.get("name") for fk in inspector.get_foreign_keys("attendance_exceptions")}
    if DECIDED_BY_FK in existing_fk_names:
        op.drop_constraint(DECIDED_BY_FK, "attendance_exceptions", type_="foreignkey")
        inspector = sa.inspect(bind)

    for column_name in (
        "decided_by",
        "admin_decided_at",
        "admin_note",
        "employee_submitted_at",
        "employee_explanation",
        "expires_at",
        "detected_at",
    ):
        if _has_column(inspector, "attendance_exceptions", column_name):
            op.drop_column("attendance_exceptions", column_name)
            inspector = sa.inspect(bind)
