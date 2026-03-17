"""add attendance exceptions table

Revision ID: d1e2f3a4b5c6
Revises: c3e7f8a9b0c1
Create Date: 2026-03-16 10:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c3e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("attendance_exceptions"):
        op.create_table(
            "attendance_exceptions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("source_checkin_log_id", sa.Integer(), nullable=False),
            sa.Column("exception_type", sa.String(length=50), nullable=False),
            sa.Column("work_date", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'OPEN'")),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
            sa.ForeignKeyConstraint(["source_checkin_log_id"], ["attendance_logs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_checkin_log_id"),
        )

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("attendance_exceptions")}
    idx_employee = op.f("ix_attendance_exceptions_employee_id")
    idx_exception_type = op.f("ix_attendance_exceptions_exception_type")

    if idx_employee not in existing_indexes:
        op.create_index(idx_employee, "attendance_exceptions", ["employee_id"], unique=False)
    if idx_exception_type not in existing_indexes:
        op.create_index(idx_exception_type, "attendance_exceptions", ["exception_type"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("attendance_exceptions"):
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("attendance_exceptions")}
        idx_employee = op.f("ix_attendance_exceptions_employee_id")
        idx_exception_type = op.f("ix_attendance_exceptions_exception_type")

        if idx_exception_type in existing_indexes:
            op.drop_index(idx_exception_type, table_name="attendance_exceptions")
        if idx_employee in existing_indexes:
            op.drop_index(idx_employee, table_name="attendance_exceptions")

        op.drop_table("attendance_exceptions")
