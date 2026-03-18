"""add exception resolution fields

Revision ID: a9b8c7d6e5f4
Revises: f7a8b9c0d1e2
Create Date: 2026-03-18 10:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FK_NAME = "fk_attendance_exceptions_resolved_by_users"


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _has_fk(inspector, table_name: str, constrained_column: str, referred_table: str) -> bool:
    for fk in inspector.get_foreign_keys(table_name):
        cols = fk.get("constrained_columns") or []
        if cols == [constrained_column] and fk.get("referred_table") == referred_table:
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("attendance_exceptions"):
        return

    if not _has_column(inspector, "attendance_exceptions", "actual_checkout_time"):
        op.add_column("attendance_exceptions", sa.Column("actual_checkout_time", sa.DateTime(timezone=True), nullable=True))
        inspector = sa.inspect(bind)

    if not _has_column(inspector, "attendance_exceptions", "resolved_by"):
        op.add_column("attendance_exceptions", sa.Column("resolved_by", sa.Integer(), nullable=True))
        inspector = sa.inspect(bind)

    if _has_column(inspector, "attendance_exceptions", "resolved_by") and not _has_fk(
        inspector,
        "attendance_exceptions",
        "resolved_by",
        "users",
    ):
        op.create_foreign_key(
            FK_NAME,
            "attendance_exceptions",
            "users",
            ["resolved_by"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("attendance_exceptions"):
        return

    existing_fk_names = {fk.get("name") for fk in inspector.get_foreign_keys("attendance_exceptions")}
    if FK_NAME in existing_fk_names:
        op.drop_constraint(FK_NAME, "attendance_exceptions", type_="foreignkey")
        inspector = sa.inspect(bind)

    if _has_column(inspector, "attendance_exceptions", "resolved_by"):
        op.drop_column("attendance_exceptions", "resolved_by")
        inspector = sa.inspect(bind)

    if _has_column(inspector, "attendance_exceptions", "actual_checkout_time"):
        op.drop_column("attendance_exceptions", "actual_checkout_time")
