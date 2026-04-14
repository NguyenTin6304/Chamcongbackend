"""soft_delete_employees

Revision ID: 861494085a32
Revises: 02cfb150d0e9
Create Date: 2026-04-13 16:50:25.709990

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '861494085a32'
down_revision: Union[str, Sequence[str], None] = '02cfb150d0e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('employees', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('employees', 'deleted_at')
