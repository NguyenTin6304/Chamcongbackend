"""add group and geofence tables

Revision ID: e8b1c2d3f4a5
Revises: d4f7a8c1e2b3
Create Date: 2026-03-11 15:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e8b1c2d3f4a5"
down_revision: Union[str, Sequence[str], None] = "d4f7a8c1e2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def _has_fk(inspector, table_name: str, local_col: str, referred_table: str) -> bool:
    for fk in inspector.get_foreign_keys(table_name):
        if fk.get("referred_table") == referred_table and fk.get("constrained_columns") == [local_col]:
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "groups"):
        op.create_table(
            "groups",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=50), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", name="uq_groups_code"),
        )
        inspector = sa.inspect(bind)

    if not _has_table(inspector, "group_geofences"):
        op.create_table(
            "group_geofences",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("group_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("latitude", sa.Float(), nullable=False),
            sa.Column("longitude", sa.Float(), nullable=False),
            sa.Column("radius_m", sa.Integer(), nullable=False, server_default="200"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name="fk_group_geofences_group_id_groups"),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = sa.inspect(bind)
    elif not _has_fk(inspector, "group_geofences", "group_id", "groups"):
        op.create_foreign_key(
            "fk_group_geofences_group_id_groups",
            "group_geofences",
            "groups",
            ["group_id"],
            ["id"],
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, "group_geofences", "ix_group_geofences_group_id"):
        op.create_index("ix_group_geofences_group_id", "group_geofences", ["group_id"], unique=False)

    if not _has_column(inspector, "employees", "group_id"):
        op.add_column("employees", sa.Column("group_id", sa.Integer(), nullable=True))
        inspector = sa.inspect(bind)

    if not _has_fk(inspector, "employees", "group_id", "groups"):
        op.create_foreign_key(
            "fk_employees_group_id_groups",
            "employees",
            "groups",
            ["group_id"],
            ["id"],
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, "employees", "ix_employees_group_id"):
        op.create_index("ix_employees_group_id", "employees", ["group_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "employees"):
        if _has_index(inspector, "employees", "ix_employees_group_id"):
            op.drop_index("ix_employees_group_id", table_name="employees")
            inspector = sa.inspect(bind)

        if _has_fk(inspector, "employees", "group_id", "groups"):
            op.drop_constraint("fk_employees_group_id_groups", "employees", type_="foreignkey")
            inspector = sa.inspect(bind)

        if _has_column(inspector, "employees", "group_id"):
            op.drop_column("employees", "group_id")
            inspector = sa.inspect(bind)

    if _has_table(inspector, "group_geofences"):
        if _has_index(inspector, "group_geofences", "ix_group_geofences_group_id"):
            op.drop_index("ix_group_geofences_group_id", table_name="group_geofences")
        op.drop_table("group_geofences")
        inspector = sa.inspect(bind)

    if _has_table(inspector, "groups"):
        op.drop_table("groups")
