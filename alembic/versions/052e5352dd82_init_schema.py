"""init_schema

Revision ID: 052e5352dd82
Revises:
Create Date: 2026-03-04 16:25:44.745775

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '052e5352dd82'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "checkin_rules" not in existing_tables:
        op.create_table(
            "checkin_rules",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("latitude", sa.Float(), nullable=False),
            sa.Column("longitude", sa.Float(), nullable=False),
            sa.Column("radius_m", sa.Integer(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=50), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    if "users" in inspector.get_table_names():
        users_indexes = {idx["name"] for idx in inspector.get_indexes("users")}
        users_email_index = op.f("ix_users_email")
        if users_email_index not in users_indexes:
            op.create_index(users_email_index, "users", ["email"], unique=True)

    if "employees" not in existing_tables:
        op.create_table(
            "employees",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=50), nullable=False),
            sa.Column("full_name", sa.String(length=255), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if "employees" in inspector.get_table_names():
        employees_indexes = {idx["name"] for idx in inspector.get_indexes("employees")}
        employees_code_index = op.f("ix_employees_code")
        if employees_code_index not in employees_indexes:
            op.create_index(employees_code_index, "employees", ["code"], unique=True)

    if "attendance_logs" not in existing_tables:
        op.create_table(
            "attendance_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("type", sa.String(length=10), nullable=False),
            sa.Column(
                "time",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("lat", sa.Float(), nullable=False),
            sa.Column("lng", sa.Float(), nullable=False),
            sa.Column("distance_m", sa.Float(), nullable=True),
            sa.Column("is_out_of_range", sa.Boolean(), nullable=False),
            sa.Column("address_text", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "attendance_logs" in existing_tables:
        op.drop_table("attendance_logs")

    if "employees" in existing_tables:
        employees_indexes = {idx["name"] for idx in inspector.get_indexes("employees")}
        employees_code_index = op.f("ix_employees_code")
        if employees_code_index in employees_indexes:
            op.drop_index(employees_code_index, table_name="employees")
        op.drop_table("employees")

    if "users" in existing_tables:
        users_indexes = {idx["name"] for idx in inspector.get_indexes("users")}
        users_email_index = op.f("ix_users_email")
        if users_email_index in users_indexes:
            op.drop_index(users_email_index, table_name="users")
        op.drop_table("users")

    if "checkin_rules" in existing_tables:
        op.drop_table("checkin_rules")
