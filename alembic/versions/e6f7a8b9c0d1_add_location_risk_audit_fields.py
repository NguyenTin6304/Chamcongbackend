"""add location risk audit fields

Revision ID: e6f7a8b9c0d1
Revises: b2c3d4e5f6a7
Create Date: 2026-03-27 14:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "attendance_logs", "risk_score"):
        op.add_column("attendance_logs", sa.Column("risk_score", sa.Integer(), nullable=True))
    if not _has_column(inspector, "attendance_logs", "risk_level"):
        op.add_column("attendance_logs", sa.Column("risk_level", sa.String(length=10), nullable=True))
    if not _has_column(inspector, "attendance_logs", "risk_flags"):
        op.add_column("attendance_logs", sa.Column("risk_flags", sa.Text(), nullable=True))
    if not _has_column(inspector, "attendance_logs", "risk_policy_version"):
        op.add_column("attendance_logs", sa.Column("risk_policy_version", sa.String(length=32), nullable=True))
    if not _has_column(inspector, "attendance_logs", "ip"):
        op.add_column("attendance_logs", sa.Column("ip", sa.String(length=64), nullable=True))
    if not _has_column(inspector, "attendance_logs", "ua_hash"):
        op.add_column("attendance_logs", sa.Column("ua_hash", sa.String(length=64), nullable=True))
    if not _has_column(inspector, "attendance_logs", "accuracy_m"):
        op.add_column("attendance_logs", sa.Column("accuracy_m", sa.Float(), nullable=True))

    inspector = sa.inspect(bind)
    if not _has_column(inspector, "attendance_exceptions", "resolved_note"):
        op.add_column("attendance_exceptions", sa.Column("resolved_note", sa.Text(), nullable=True))

    op.execute(
        sa.text(
            "UPDATE attendance_exceptions "
            "SET exception_type = 'SUSPECTED_LOCATION_SPOOF' "
            "WHERE exception_type = 'GPS_RISK'"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    op.execute(
        sa.text(
            "UPDATE attendance_exceptions "
            "SET exception_type = 'GPS_RISK' "
            "WHERE exception_type = 'SUSPECTED_LOCATION_SPOOF'"
        )
    )

    if _has_column(inspector, "attendance_exceptions", "resolved_note"):
        op.drop_column("attendance_exceptions", "resolved_note")
        inspector = sa.inspect(bind)

    for column_name in (
        "accuracy_m",
        "ua_hash",
        "ip",
        "risk_policy_version",
        "risk_flags",
        "risk_level",
        "risk_score",
    ):
        if _has_column(inspector, "attendance_logs", column_name):
            op.drop_column("attendance_logs", column_name)
            inspector = sa.inspect(bind)
