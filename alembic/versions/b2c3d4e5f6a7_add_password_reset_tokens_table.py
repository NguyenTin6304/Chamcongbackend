"""add password reset tokens table

Revision ID: b2c3d4e5f6a7
Revises: a9b8c7d6e5f4
Create Date: 2026-03-19 13:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("password_reset_tokens"):
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("token_hash", sa.String(length=128), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
        )

    inspector = sa.inspect(bind)
    idx_user_id = op.f("ix_password_reset_tokens_user_id")
    idx_expires = op.f("ix_password_reset_tokens_expires_at")

    if not _has_index(inspector, "password_reset_tokens", idx_user_id):
        op.create_index(idx_user_id, "password_reset_tokens", ["user_id"], unique=False)
    if not _has_index(inspector, "password_reset_tokens", idx_expires):
        op.create_index(idx_expires, "password_reset_tokens", ["expires_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("password_reset_tokens"):
        return

    idx_user_id = op.f("ix_password_reset_tokens_user_id")
    idx_expires = op.f("ix_password_reset_tokens_expires_at")

    if _has_index(inspector, "password_reset_tokens", idx_expires):
        op.drop_index(idx_expires, table_name="password_reset_tokens")
        inspector = sa.inspect(bind)
    if _has_index(inspector, "password_reset_tokens", idx_user_id):
        op.drop_index(idx_user_id, table_name="password_reset_tokens")

    op.drop_table("password_reset_tokens")
