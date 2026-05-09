"""add leave quota fields

Revision ID: e5f6a7b8c9d0
Revises: d3e4f5a6b7c8
Create Date: 2026-05-04
"""

from alembic import op
import sqlalchemy as sa

revision = 'e5f6a7b8c9d0'
down_revision = 'd3e4f5a6b7c8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Per-employee leave quota (NULL = unlimited)
    op.add_column('employees', sa.Column('annual_leave_days', sa.Float(), nullable=True))

    # Company-wide default when creating new employees
    op.add_column('checkin_rules', sa.Column(
        'default_annual_leave_days', sa.Float(), nullable=False, server_default='12.0',
    ))


def downgrade() -> None:
    op.drop_column('employees', 'annual_leave_days')
    op.drop_column('checkin_rules', 'default_annual_leave_days')
