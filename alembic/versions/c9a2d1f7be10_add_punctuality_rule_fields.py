"""add punctuality rule fields and checkin status

Revision ID: c9a2d1f7be10
Revises: b7f95d2e1a31
Create Date: 2026-03-10 11:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9a2d1f7be10"
down_revision: Union[str, Sequence[str], None] = "b7f95d2e1a31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "checkin_rules", "start_time"):
        op.add_column(
            "checkin_rules",
            sa.Column("start_time", sa.Time(), nullable=False, server_default="08:00:00"),
        )

    if not _has_column(inspector, "checkin_rules", "grace_minutes"):
        op.add_column(
            "checkin_rules",
            sa.Column("grace_minutes", sa.Integer(), nullable=False, server_default="30"),
        )

    if not _has_column(inspector, "attendance_logs", "punctuality_status"):
        op.add_column(
            "attendance_logs",
            sa.Column("punctuality_status", sa.String(length=20), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "attendance_logs", "punctuality_status"):
        op.drop_column("attendance_logs", "punctuality_status")
    if _has_column(inspector, "checkin_rules", "grace_minutes"):
        op.drop_column("checkin_rules", "grace_minutes")
    if _has_column(inspector, "checkin_rules", "start_time"):
        op.drop_column("checkin_rules", "start_time")
