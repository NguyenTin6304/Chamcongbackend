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


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "groups", "cross_day_cutoff_minutes"):
        op.add_column("groups", sa.Column("cross_day_cutoff_minutes", sa.Integer(), nullable=True))
        inspector = sa.inspect(bind)

    if not _has_column(inspector, "checkin_rules", "cross_day_cutoff_minutes"):
        op.add_column("checkin_rules", sa.Column("cross_day_cutoff_minutes", sa.Integer(), nullable=True))
        inspector = sa.inspect(bind)

    if _has_column(inspector, "checkin_rules", "cross_day_cutoff_minutes"):
        op.execute("UPDATE checkin_rules SET cross_day_cutoff_minutes = 240 WHERE cross_day_cutoff_minutes IS NULL")
        op.alter_column("checkin_rules", "cross_day_cutoff_minutes", nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "checkin_rules", "cross_day_cutoff_minutes"):
        op.drop_column("checkin_rules", "cross_day_cutoff_minutes")
        inspector = sa.inspect(bind)

    if _has_column(inspector, "groups", "cross_day_cutoff_minutes"):
        op.drop_column("groups", "cross_day_cutoff_minutes")
