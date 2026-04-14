"""add phone to employees

Revision ID: c8d9e0f1a2b3
Revises: b7d2f8c1a4e9
Create Date: 2026-04-13 14:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b7d2f8c1a4e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("employees")}
    if "phone" in columns:
        return

    op.add_column("employees", sa.Column("phone", sa.String(length=32), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("employees")}
    if "phone" in columns:
        op.drop_column("employees", "phone")
