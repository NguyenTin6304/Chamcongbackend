"""add face_not_captured_deadline_hours to exception_policies (Phase 4.1)

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-05-19 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'j4k5l6m7n8o9'
down_revision: Union[str, None] = 'i3j4k5l6m7n8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c["name"] for c in inspector.get_columns("exception_policies")]
    if "face_not_captured_deadline_hours" not in cols:
        op.add_column('exception_policies', sa.Column(
            'face_not_captured_deadline_hours', sa.Integer(), nullable=True
        ))


def downgrade() -> None:
    op.drop_column('exception_policies', 'face_not_captured_deadline_hours')
