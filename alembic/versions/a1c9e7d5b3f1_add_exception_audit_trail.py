"""add exception audit trail

Revision ID: a1c9e7d5b3f1
Revises: f2c4d6e8a1b3
Create Date: 2026-04-06 13:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1c9e7d5b3f1"
down_revision: Union[str, Sequence[str], None] = "f2c4d6e8a1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("attendance_exception_audits"):
        return

    op.create_table(
        "attendance_exception_audits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exception_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("previous_status", sa.String(length=20), nullable=True),
        sa.Column("next_status", sa.String(length=20), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["exception_id"], ["attendance_exceptions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_attendance_exception_audits_exception_id"), "attendance_exception_audits", ["exception_id"], unique=False)
    op.create_index(op.f("ix_attendance_exception_audits_event_type"), "attendance_exception_audits", ["event_type"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("attendance_exception_audits"):
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("attendance_exception_audits")}
    idx_exception = op.f("ix_attendance_exception_audits_exception_id")
    idx_event_type = op.f("ix_attendance_exception_audits_event_type")

    if idx_event_type in existing_indexes:
        op.drop_index(idx_event_type, table_name="attendance_exception_audits")
    if idx_exception in existing_indexes:
        op.drop_index(idx_exception, table_name="attendance_exception_audits")

    op.drop_table("attendance_exception_audits")
