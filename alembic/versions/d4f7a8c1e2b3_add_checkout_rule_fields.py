"""add checkout rule fields and checkout status

Revision ID: d4f7a8c1e2b3
Revises: c9a2d1f7be10
Create Date: 2026-03-11 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4f7a8c1e2b3"
down_revision: Union[str, Sequence[str], None] = "c9a2d1f7be10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "checkin_rules",
        sa.Column("end_time", sa.Time(), nullable=False, server_default="17:30:00"),
    )
    op.add_column(
        "checkin_rules",
        sa.Column("checkout_grace_minutes", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "attendance_logs",
        sa.Column("checkout_status", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("attendance_logs", "checkout_status")
    op.drop_column("checkin_rules", "checkout_grace_minutes")
    op.drop_column("checkin_rules", "end_time")
