"""add shifts table (Phase 3A)

Revision ID: g1h2i3j4k5l6
Revises: f6a7b8c9d0e1
Create Date: 2026-05-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g1h2i3j4k5l6"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "shifts"):
        op.create_table(
            "shifts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("group_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("start_time", sa.Time(), nullable=False),
            sa.Column("end_time", sa.Time(), nullable=False),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name="fk_shifts_group_id_groups"),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, "shifts", "ix_shifts_group_id"):
        op.create_index("ix_shifts_group_id", "shifts", ["group_id"], unique=False)
        inspector = sa.inspect(bind)

    # Partial unique index — at most one default Shift per group.
    # Postgres-specific: WHERE clause filters NULL/false out of the index.
    if not _has_index(inspector, "shifts", "uq_shifts_group_default"):
        op.create_index(
            "uq_shifts_group_default",
            "shifts",
            ["group_id"],
            unique=True,
            postgresql_where=sa.text("is_default IS TRUE"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "shifts"):
        if _has_index(inspector, "shifts", "uq_shifts_group_default"):
            op.drop_index("uq_shifts_group_default", table_name="shifts")
            inspector = sa.inspect(bind)
        if _has_index(inspector, "shifts", "ix_shifts_group_id"):
            op.drop_index("ix_shifts_group_id", table_name="shifts")
            inspector = sa.inspect(bind)
        op.drop_table("shifts")
