"""add_fcm_token_to_users

Revision ID: b5c6d7e8f9a1
Revises: a3b5c7d9e1f2
Create Date: 2026-04-15 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5c6d7e8f9a1"
down_revision: Union[str, None] = "a3b5c7d9e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("fcm_token", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "fcm_token")
