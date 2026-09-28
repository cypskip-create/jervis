"""Normalized SQLAlchemy persistence schema for decisions and trade lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("broker", "server", "account_ref"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    broker: Mapped[str] = mapped_column(String(120), nullable=False)
    server: Mapped[str] = mapped_column(String(120), nullable=False)
    account_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="paper")
    risk_lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    canonical: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disable_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="disable_new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SymbolMapping(Base):
    __tablename__ = "symbol_mappings"
    __table_args__ = (
        UniqueConstraint("account_id", "broker_symbol"),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from", name="ck_mapping_validity_interval"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    symbol_id: Mapped[str] = mapped_column(ForeignKey("symbols.id", ondelete="RESTRICT"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id", ondelete="RESTRICT"))
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    source_revision: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    strategy_version_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="RESTRICT")
    )
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        CheckConstraint("direction IN ('long', 'short')", name="ck_signal_direction"),
        CheckConstraint(
            "decision IN ('approved', 'rejected', 'pending')", name="ck_signal_decision"
        ),
        Index("ix_signals_symbol_created", "symbol_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    signal_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    symbol_id: Mapped[str] = mapped_column(ForeignKey("symbols.id", ondelete="RESTRICT"))
    strategy_version_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="RESTRICT")
    )
    strategy_config_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_configs.id", ondelete="RESTRICT")
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    decision: Mapped[str] = mapped_column(String(12), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80))
    reason: Mapped[str | None] = mapped_column(Text)
    entry_candidate: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    rr: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    market_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RiskSnapshot(Base):
    __tablename__ = "risk_snapshots"
    __table_args__ = (
        CheckConstraint("equity > 0", name="ck_risk_snapshot_equity_positive"),
        CheckConstraint("open_risk >= 0", name="ck_risk_snapshot_open_risk_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    equity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    open_risk: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    currency_exposure: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    asset_exposure: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RiskReservation(Base):
    __tablename__ = "risk_reservations"
    __table_args__ = (
        UniqueConstraint("signal_id", name="uq_risk_reservation_signal"),
        CheckConstraint("amount > 0", name="ck_risk_reservation_amount_positive"),
        CheckConstraint(
            "state IN ('held', 'submitted', 'filled', 'released', 'expired')",
            name="ck_risk_reservation_state",
        ),
        Index("ix_risk_reservations_account_state", "account_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    exposure_commitments: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="held")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
        Index("ix_orders_account_status", "account_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("account_id", "broker_ticket"),
        CheckConstraint("volume > 0", name="ck_position_volume_positive"),
        CheckConstraint("direction IN ('long', 'short')", name="ck_position_direction"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    symbol_id: Mapped[str] = mapped_column(ForeignKey("symbols.id", ondelete="RESTRICT"))
    signal_id: Mapped[str | None] = mapped_column(ForeignKey("signals.id"))
    broker_ticket: Mapped[str] = mapped_column(String(120), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    max_favorable_price: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    max_adverse_price: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="open")


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint("volume > 0", name="ck_trade_volume_positive"),
        CheckConstraint("closed_at >= opened_at", name="ck_trade_time_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    position_id: Mapped[str | None] = mapped_column(ForeignKey("positions.id"))
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id", ondelete="RESTRICT"))
    strategy_version_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="RESTRICT")
    )
    strategy_config_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_configs.id", ondelete="RESTRICT")
    )
    actual_entry: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    actual_exit: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=0)
    swap: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=0)
    slippage: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    realized_r: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    mfe: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    mae: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    exit_reason: Mapped[str] = mapped_column(String(80), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketState(Base):
    __tablename__ = "market_states"
    __table_args__ = (Index("ix_market_states_symbol_time", "symbol_id", "observed_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    symbol_id: Mapped[str] = mapped_column(ForeignKey("symbols.id", ondelete="RESTRICT"))
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(120))
    before: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class BotSetting(Base):
    __tablename__ = "bot_settings"
    __table_args__ = (UniqueConstraint("key", "revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Backtest(Base):
    __tablename__ = "backtests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    strategy_version_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="RESTRICT")
    )
    strategy_config_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_configs.id", ondelete="RESTRICT")
    )
    data_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    split_definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    backtest_id: Mapped[str] = mapped_column(ForeignKey("backtests.id", ondelete="CASCADE"))
    signal_key: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol_id: Mapped[str] = mapped_column(ForeignKey("symbols.id", ondelete="RESTRICT"))
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class OptimizationRun(Base):
    __tablename__ = "optimization_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id", ondelete="RESTRICT"))
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    search_space: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    results: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
