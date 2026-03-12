"""add matched geofence column to attendance logs

Revision ID: f1a2b3c4d5e6
Revises: e8b1c2d3f4a5
Create Date: 2026-03-11 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e8b1c2d3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "attendance_logs", "matched_geofence_name"):
        op.add_column(
            "attendance_logs",
            sa.Column("matched_geofence_name", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "attendance_logs", "matched_geofence_name"):
        op.drop_column("attendance_logs", "matched_geofence_name")
