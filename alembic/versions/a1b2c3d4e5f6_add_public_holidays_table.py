"""add public_holidays table

Revision ID: a1b2c3d4e5f6
Revises: f2c4d6e8a1b3
Create Date: 2026-04-24 10:00:00.000000
"""

from __future__ import annotations

import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "b5c6d7e8f9a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "public_holidays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date", name="uq_public_holidays_date"),
    )

    # Seed VN 2026 public holidays
    holidays_2026 = [
        ("2026-01-01", "Tết Dương lịch"),
        ("2026-01-28", "Tết Nguyên Đán (28 tháng Chạp)"),
        ("2026-01-29", "Tết Nguyên Đán (29 tháng Chạp)"),
        ("2026-01-30", "Tết Nguyên Đán (Mùng 1)"),
        ("2026-01-31", "Tết Nguyên Đán (Mùng 2)"),
        ("2026-02-01", "Tết Nguyên Đán (Mùng 3)"),
        ("2026-02-02", "Tết Nguyên Đán (Mùng 4)"),
        ("2026-04-18", "Giỗ Tổ Hùng Vương (10/3 âm lịch)"),
        ("2026-04-30", "Ngày Giải phóng miền Nam"),
        ("2026-05-01", "Ngày Quốc tế Lao động"),
        ("2026-09-02", "Ngày Quốc khánh"),
        ("2026-09-03", "Ngày Quốc khánh (bù)"),
    ]

    op.bulk_insert(
        sa.table(
            "public_holidays",
            sa.column("date", sa.Date()),
            sa.column("name", sa.String()),
        ),
        [
            {"date": datetime.date.fromisoformat(d), "name": n}
            for d, n in holidays_2026
        ],
    )


def downgrade() -> None:
    op.drop_table("public_holidays")
