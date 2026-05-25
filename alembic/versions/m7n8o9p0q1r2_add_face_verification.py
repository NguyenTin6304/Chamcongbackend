"""add face verification (Phase 4.2)

Adds embedding + verification columns to attendance_logs and creates the
employee_face_references table that holds 1 reference face per employee.

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-05-22 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'm7n8o9p0q1r2'
down_revision: Union[str, None] = 'l6m7n8o9p0q1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1) Add embedding + verification columns to attendance_logs.
    log_cols = {c["name"] for c in inspector.get_columns("attendance_logs")}
    if "face_embedding" not in log_cols:
        op.add_column(
            "attendance_logs",
            sa.Column("face_embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
    if "face_match_score" not in log_cols:
        op.add_column(
            "attendance_logs",
            sa.Column("face_match_score", sa.Float(), nullable=True),
        )
    if "face_verify_status" not in log_cols:
        op.add_column(
            "attendance_logs",
            sa.Column("face_verify_status", sa.String(length=30), nullable=True),
        )

    # 2) employee_face_references — one row per employee (UNIQUE).
    existing_tables = set(inspector.get_table_names())
    if "employee_face_references" not in existing_tables:
        op.create_table(
            "employee_face_references",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "employee_id",
                sa.Integer(),
                sa.ForeignKey("employees.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column(
                "log_id_source",
                sa.Integer(),
                sa.ForeignKey("attendance_logs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "face_embedding",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column(
                "set_by_admin_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )


def downgrade() -> None:
    op.drop_table("employee_face_references")
    op.drop_column("attendance_logs", "face_verify_status")
    op.drop_column("attendance_logs", "face_match_score")
    op.drop_column("attendance_logs", "face_embedding")
