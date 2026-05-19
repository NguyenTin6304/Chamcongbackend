"""fix time_rule_source column length to VARCHAR(50) for EMPLOYEE_SHIFT_OVERRIDE value

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-05-19 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'k5l6m7n8o9p0'
down_revision: Union[str, None] = 'j4k5l6m7n8o9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # EMPLOYEE_SHIFT_OVERRIDE = 23 chars, old VARCHAR(20) caused StringDataRightTruncation
    op.alter_column(
        'attendance_logs',
        'time_rule_source',
        type_=sa.String(50),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        'attendance_logs',
        'time_rule_source',
        type_=sa.String(20),
        existing_nullable=True,
    )
