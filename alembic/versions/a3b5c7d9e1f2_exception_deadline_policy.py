"""exception_deadline_policy

Revision ID: a3b5c7d9e1f2
Revises: 861494085a32
Create Date: 2026-04-14 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3b5c7d9e1f2'
down_revision: Union[str, Sequence[str], None] = '861494085a32'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create exception_policies singleton table
    op.create_table(
        'exception_policies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('default_deadline_hours', sa.Integer(), nullable=False, server_default='72'),
        sa.Column('auto_closed_deadline_hours', sa.Integer(), nullable=True),
        sa.Column('missed_checkout_deadline_hours', sa.Integer(), nullable=True),
        sa.Column('location_risk_deadline_hours', sa.Integer(), nullable=True),
        sa.Column('large_time_deviation_deadline_hours', sa.Integer(), nullable=True),
        sa.Column('grace_period_days', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
    )

    # Insert the singleton default row
    op.execute(
        "INSERT INTO exception_policies (id, default_deadline_hours, grace_period_days) "
        "VALUES (1, 72, 30)"
    )

    # Add extended_deadline_at to attendance_exceptions for per-exception deadline override
    op.add_column(
        'attendance_exceptions',
        sa.Column('extended_deadline_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('attendance_exceptions', 'extended_deadline_at')
    op.drop_table('exception_policies')
