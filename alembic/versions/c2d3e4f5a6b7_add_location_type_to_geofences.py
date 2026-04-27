"""add location_type to group_geofences

Revision ID: c2d3e4f5a6b7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "group_geofences",
        sa.Column(
            "location_type",
            sa.String(10),
            nullable=False,
            server_default="SITE",
        ),
    )
    op.create_check_constraint(
        "ck_group_geofences_location_type",
        "group_geofences",
        "location_type IN ('VP', 'SITE')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_group_geofences_location_type",
        "group_geofences",
        type_="check",
    )
    op.drop_column("group_geofences", "location_type")
