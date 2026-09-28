"""persist paper position excursions

Revision ID: f9720ba48def
Revises: 4d485769f601
Create Date: 2026-09-28 19:54:15.066613
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f9720ba48def"
down_revision = "4d485769f601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "positions",
        sa.Column("max_favorable_price", sa.Numeric(precision=24, scale=10), nullable=True),
    )
    op.add_column(
        "positions",
        sa.Column("max_adverse_price", sa.Numeric(precision=24, scale=10), nullable=True),
    )
    op.execute(
        "UPDATE positions SET max_favorable_price = entry_price, max_adverse_price = entry_price"
    )
    with op.batch_alter_table("positions") as batch_op:
        batch_op.alter_column(
            "max_favorable_price",
            existing_type=sa.Numeric(precision=24, scale=10),
            nullable=False,
        )
        batch_op.alter_column(
            "max_adverse_price",
            existing_type=sa.Numeric(precision=24, scale=10),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("positions") as batch_op:
        batch_op.drop_column("max_adverse_price")
        batch_op.drop_column("max_favorable_price")
