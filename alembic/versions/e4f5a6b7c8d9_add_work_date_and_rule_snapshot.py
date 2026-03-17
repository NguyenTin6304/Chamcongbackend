"""add work_date and rule snapshot columns to attendance_logs

Revision ID: e4f5a6b7c8d9
Revises: d1e2f3a4b5c6
Create Date: 2026-03-16 11:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("attendance_logs", sa.Column("work_date", sa.Date(), nullable=True))
    op.add_column("attendance_logs", sa.Column("snapshot_start_time", sa.Time(), nullable=True))
    op.add_column("attendance_logs", sa.Column("snapshot_end_time", sa.Time(), nullable=True))
    op.add_column("attendance_logs", sa.Column("snapshot_grace_minutes", sa.Integer(), nullable=True))
    op.add_column("attendance_logs", sa.Column("snapshot_checkout_grace_minutes", sa.Integer(), nullable=True))
    op.add_column("attendance_logs", sa.Column("snapshot_cutoff_minutes", sa.Integer(), nullable=True))
    op.add_column("attendance_logs", sa.Column("time_rule_source", sa.String(length=20), nullable=True))
    op.add_column("attendance_logs", sa.Column("time_rule_fallback_reason", sa.String(length=100), nullable=True))

    op.create_index("ix_attendance_logs_work_date", "attendance_logs", ["work_date"], unique=False)

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            UPDATE attendance_logs
            SET work_date = DATE(TIMEZONE('Asia/Ho_Chi_Minh', time - INTERVAL '4 hour'))
            WHERE work_date IS NULL
            """
        )


def downgrade() -> None:
    op.drop_index("ix_attendance_logs_work_date", table_name="attendance_logs")

    op.drop_column("attendance_logs", "time_rule_fallback_reason")
    op.drop_column("attendance_logs", "time_rule_source")
    op.drop_column("attendance_logs", "snapshot_cutoff_minutes")
    op.drop_column("attendance_logs", "snapshot_checkout_grace_minutes")
    op.drop_column("attendance_logs", "snapshot_grace_minutes")
    op.drop_column("attendance_logs", "snapshot_end_time")
    op.drop_column("attendance_logs", "snapshot_start_time")
    op.drop_column("attendance_logs", "work_date")
