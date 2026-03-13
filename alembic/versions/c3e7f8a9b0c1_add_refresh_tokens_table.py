"""add refresh_tokens table

Revision ID: c3e7f8a9b0c1
Revises: ab12cd34ef56
Create Date: 2026-03-13 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3e7f8a9b0c1"
down_revision: Union[str, Sequence[str], None] = "ab12cd34ef56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "refresh_tokens"):
        op.create_table(
            "refresh_tokens",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("jti", sa.String(length=64), nullable=False),
            sa.Column("token_hash", sa.String(length=128), nullable=False),
            sa.Column("remember_me", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("replaced_by_jti", sa.String(length=64), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_refresh_tokens_user_id_users"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("jti", name="uq_refresh_tokens_jti"),
            sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, "refresh_tokens", "ix_refresh_tokens_user_id"):
        op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], unique=False)
    if not _has_index(inspector, "refresh_tokens", "ix_refresh_tokens_jti"):
        op.create_index("ix_refresh_tokens_jti", "refresh_tokens", ["jti"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "refresh_tokens"):
        if _has_index(inspector, "refresh_tokens", "ix_refresh_tokens_jti"):
            op.drop_index("ix_refresh_tokens_jti", table_name="refresh_tokens")
        if _has_index(inspector, "refresh_tokens", "ix_refresh_tokens_user_id"):
            op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
        op.drop_table("refresh_tokens")
