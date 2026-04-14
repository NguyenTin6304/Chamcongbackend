"""add_active_to_employees

Revision ID: 02cfb150d0e9
Revises: d9e0f1a2b3c4
Create Date: 2026-04-13 16:22:24.439803

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '02cfb150d0e9'
down_revision: Union[str, Sequence[str], None] = 'd9e0f1a2b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('employees', sa.Column('active', sa.Boolean(), server_default='true', nullable=False))


def downgrade() -> None:
    op.drop_column('employees', 'active')
