"""add exception notification log

Revision ID: b7d2f8c1a4e9
Revises: a1c9e7d5b3f1
Create Date: 2026-04-07 10:15:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7d2f8c1a4e9"
down_revision: Union[str, Sequence[str], None] = "a1c9e7d5b3f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("attendance_exception_notifications"):
        return

    op.create_table(
        "attendance_exception_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exception_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), nullable=True),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("recipient_role", sa.String(length=20), nullable=False),
        sa.Column("dedupe_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="QUEUED"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["exception_id"], ["attendance_exceptions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_attendance_exception_notifications_dedupe_key"),
    )
    op.create_index(
        "ix_attendance_exception_notifications_exception_id",
        "attendance_exception_notifications",
        ["exception_id"],
    )
    op.create_index(
        "ix_attendance_exception_notifications_event_type",
        "attendance_exception_notifications",
        ["event_type"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("attendance_exception_notifications"):
        return

    op.drop_index("ix_attendance_exception_notifications_event_type", table_name="attendance_exception_notifications")
    op.drop_index("ix_attendance_exception_notifications_exception_id", table_name="attendance_exception_notifications")
    op.drop_table("attendance_exception_notifications")
