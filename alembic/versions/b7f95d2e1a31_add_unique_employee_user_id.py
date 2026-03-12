"""add unique constraint for employees.user_id

Revision ID: b7f95d2e1a31
Revises: 86ce84c0b91f
Create Date: 2026-03-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b7f95d2e1a31"
down_revision: Union[str, Sequence[str], None] = "86ce84c0b91f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_employees_user_id",
        "employees",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_employees_user_id", "employees", type_="unique")