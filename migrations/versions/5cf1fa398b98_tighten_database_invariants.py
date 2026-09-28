"""tighten database invariants

Revision ID: 5cf1fa398b98
Revises: a2da5ec6f67f
Create Date: 2026-09-28 18:32:52.897864
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "5cf1fa398b98"
down_revision = "a2da5ec6f67f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("symbol_mappings") as batch_op:
        batch_op.alter_column(
            "account_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_mapping_validity_interval",
            "valid_until IS NULL OR valid_until > valid_from",
        )
    with op.batch_alter_table("risk_snapshots") as batch_op:
        batch_op.create_check_constraint("ck_risk_snapshot_equity_positive", "equity > 0")
        batch_op.create_check_constraint("ck_risk_snapshot_open_risk_nonnegative", "open_risk >= 0")
    with op.batch_alter_table("positions") as batch_op:
        batch_op.create_check_constraint("ck_position_volume_positive", "volume > 0")
        batch_op.create_check_constraint("ck_position_direction", "direction IN ('long', 'short')")
    with op.batch_alter_table("trades") as batch_op:
        batch_op.create_check_constraint("ck_trade_volume_positive", "volume > 0")
        batch_op.create_check_constraint("ck_trade_time_order", "closed_at >= opened_at")


def downgrade() -> None:
    with op.batch_alter_table("trades") as batch_op:
        batch_op.drop_constraint("ck_trade_time_order", type_="check")
        batch_op.drop_constraint("ck_trade_volume_positive", type_="check")
    with op.batch_alter_table("positions") as batch_op:
        batch_op.drop_constraint("ck_position_direction", type_="check")
        batch_op.drop_constraint("ck_position_volume_positive", type_="check")
    with op.batch_alter_table("risk_snapshots") as batch_op:
        batch_op.drop_constraint("ck_risk_snapshot_open_risk_nonnegative", type_="check")
        batch_op.drop_constraint("ck_risk_snapshot_equity_positive", type_="check")
    with op.batch_alter_table("symbol_mappings") as batch_op:
        batch_op.drop_constraint("ck_mapping_validity_interval", type_="check")
        batch_op.alter_column(
            "account_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
