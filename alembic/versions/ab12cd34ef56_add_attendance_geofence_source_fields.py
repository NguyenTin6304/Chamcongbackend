"""add attendance geofence source fields

Revision ID: ab12cd34ef56
Revises: 9c7e6d5b4a3f
Create Date: 2026-03-12 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ab12cd34ef56"
down_revision: Union[str, Sequence[str], None] = "9c7e6d5b4a3f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "attendance_logs", "geofence_source"):
        op.add_column("attendance_logs", sa.Column("geofence_source", sa.String(length=20), nullable=True))

    if not _has_column(inspector, "attendance_logs", "fallback_reason"):
        op.add_column("attendance_logs", sa.Column("fallback_reason", sa.String(length=100), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "attendance_logs", "fallback_reason"):
        op.drop_column("attendance_logs", "fallback_reason")

    if _has_column(inspector, "attendance_logs", "geofence_source"):
        op.drop_column("attendance_logs", "geofence_source")
