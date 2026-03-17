"""add cutoff minutes to rules and groups

Revision ID: f7a8b9c0d1e2
Revises: e4f5a6b7c8d9
Create Date: 2026-03-16 12:15:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("groups", sa.Column("cross_day_cutoff_minutes", sa.Integer(), nullable=True))

    op.add_column("checkin_rules", sa.Column("cross_day_cutoff_minutes", sa.Integer(), nullable=True))
    op.execute("UPDATE checkin_rules SET cross_day_cutoff_minutes = 240 WHERE cross_day_cutoff_minutes IS NULL")
    op.alter_column("checkin_rules", "cross_day_cutoff_minutes", nullable=False)


def downgrade() -> None:
    op.drop_column("checkin_rules", "cross_day_cutoff_minutes")
    op.drop_column("groups", "cross_day_cutoff_minutes")
