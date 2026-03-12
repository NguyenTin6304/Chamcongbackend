"""add group time rule fields

Revision ID: 9c7e6d5b4a3f
Revises: f1a2b3c4d5e6
Create Date: 2026-03-11 21:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9c7e6d5b4a3f"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "groups", "start_time"):
        op.add_column("groups", sa.Column("start_time", sa.Time(), nullable=True))
    if not _has_column(inspector, "groups", "grace_minutes"):
        op.add_column("groups", sa.Column("grace_minutes", sa.Integer(), nullable=True))
    if not _has_column(inspector, "groups", "end_time"):
        op.add_column("groups", sa.Column("end_time", sa.Time(), nullable=True))
    if not _has_column(inspector, "groups", "checkout_grace_minutes"):
        op.add_column("groups", sa.Column("checkout_grace_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "groups", "checkout_grace_minutes"):
        op.drop_column("groups", "checkout_grace_minutes")
    if _has_column(inspector, "groups", "end_time"):
        op.drop_column("groups", "end_time")
    if _has_column(inspector, "groups", "grace_minutes"):
        op.drop_column("groups", "grace_minutes")
    if _has_column(inspector, "groups", "start_time"):
        op.drop_column("groups", "start_time")
