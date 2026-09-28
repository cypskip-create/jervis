"""persist risk exposure commitments

Revision ID: b91794e642eb
Revises: f9720ba48def
Create Date: 2026-09-28 20:03:50.757727
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b91794e642eb'
down_revision = 'f9720ba48def'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "risk_reservations",
        sa.Column("exposure_commitments", sa.JSON(), nullable=True),
    )
    op.execute(
        "UPDATE risk_reservations SET exposure_commitments = '{}' "
        "WHERE exposure_commitments IS NULL"
    )
    with op.batch_alter_table("risk_reservations") as batch_op:
        batch_op.alter_column(
            "exposure_commitments",
            existing_type=sa.JSON(),
            nullable=False,
        )


def downgrade() -> None:
    op.drop_column("risk_reservations", "exposure_commitments")
