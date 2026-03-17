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


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for column in [
        sa.Column("work_date", sa.Date(), nullable=True),
        sa.Column("snapshot_start_time", sa.Time(), nullable=True),
        sa.Column("snapshot_end_time", sa.Time(), nullable=True),
        sa.Column("snapshot_grace_minutes", sa.Integer(), nullable=True),
        sa.Column("snapshot_checkout_grace_minutes", sa.Integer(), nullable=True),
        sa.Column("snapshot_cutoff_minutes", sa.Integer(), nullable=True),
        sa.Column("time_rule_source", sa.String(length=20), nullable=True),
        sa.Column("time_rule_fallback_reason", sa.String(length=100), nullable=True),
    ]:
        if not _has_column(inspector, "attendance_logs", column.name):
            op.add_column("attendance_logs", column)
            inspector = sa.inspect(bind)

    if not _has_index(inspector, "attendance_logs", "ix_attendance_logs_work_date"):
        op.create_index("ix_attendance_logs_work_date", "attendance_logs", ["work_date"], unique=False)

    if bind.dialect.name == "postgresql" and _has_column(inspector, "attendance_logs", "work_date"):
        op.execute(
            """
            UPDATE attendance_logs
            SET work_date = DATE(TIMEZONE('Asia/Ho_Chi_Minh', time - INTERVAL '4 hour'))
            WHERE work_date IS NULL
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_index(inspector, "attendance_logs", "ix_attendance_logs_work_date"):
        op.drop_index("ix_attendance_logs_work_date", table_name="attendance_logs")
        inspector = sa.inspect(bind)

    for column_name in [
        "time_rule_fallback_reason",
        "time_rule_source",
        "snapshot_cutoff_minutes",
        "snapshot_checkout_grace_minutes",
        "snapshot_grace_minutes",
        "snapshot_end_time",
        "snapshot_start_time",
        "work_date",
    ]:
        if _has_column(inspector, "attendance_logs", column_name):
            op.drop_column("attendance_logs", column_name)
            inspector = sa.inspect(bind)
