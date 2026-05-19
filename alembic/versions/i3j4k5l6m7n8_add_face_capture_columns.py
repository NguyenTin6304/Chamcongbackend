"""add face capture columns to attendance_logs (Phase 4.1)

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2026-05-18 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'i3j4k5l6m7n8'
down_revision: Union[str, None] = 'h2i3j4k5l6m7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('attendance_logs', sa.Column(
        'face_image_path', sa.String(500), nullable=True
    ))
    op.add_column('attendance_logs', sa.Column(
        'face_check_status', sa.String(30), nullable=True
    ))
    op.add_column('attendance_logs', sa.Column(
        'face_captured_at', sa.DateTime(timezone=True), nullable=True
    ))


def downgrade() -> None:
    op.drop_column('attendance_logs', 'face_captured_at')
    op.drop_column('attendance_logs', 'face_check_status')
    op.drop_column('attendance_logs', 'face_image_path')
