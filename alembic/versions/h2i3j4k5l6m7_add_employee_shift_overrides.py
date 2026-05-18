"""add employee_shift_overrides table (Phase 3B)

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2026-05-18 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "h2i3j4k5l6m7"
down_revision: Union[str, Sequence[str], None] = "g1h2i3j4k5l6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "employee_shift_overrides"):
        op.create_table(
            "employee_shift_overrides",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("shift_id", sa.Integer(), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(
                ["employee_id"],
                ["employees.id"],
                name="fk_employee_shift_overrides_employee_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["shift_id"],
                ["shifts.id"],
                name="fk_employee_shift_overrides_shift_id",
            ),
            sa.PrimaryKeyConstraint("id"),
            # One override per employee — keeps the resolve logic and overlap
            # validation trivial. Relax later if multi-row history is needed.
            sa.UniqueConstraint("employee_id", name="uq_employee_shift_overrides_employee_id"),
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, "employee_shift_overrides", "ix_employee_shift_overrides_employee_id"):
        op.create_index(
            "ix_employee_shift_overrides_employee_id",
            "employee_shift_overrides",
            ["employee_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "employee_shift_overrides"):
        if _has_index(inspector, "employee_shift_overrides", "ix_employee_shift_overrides_employee_id"):
            op.drop_index(
                "ix_employee_shift_overrides_employee_id",
                table_name="employee_shift_overrides",
            )
            inspector = sa.inspect(bind)
        op.drop_table("employee_shift_overrides")
