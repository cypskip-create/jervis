"""Shared trading domain values passed between strategy, risk and execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .market_structure import Direction


def _finite_positive(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class TradeProposal:
    signal_id: str
    symbol: str
    asset_class: str
    strategy_key: str
    strategy_version: str
    setup_id: str
    direction: Direction
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    generated_at: datetime
    expires_at: datetime
    reasons: tuple[str, ...]
    currency_exposure_per_lot: dict[str, Decimal] = field(default_factory=dict)
    correlation_groups: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "signal_id",
            "symbol",
            "asset_class",
            "strategy_key",
            "strategy_version",
            "setup_id",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        for name in ("entry", "stop_loss", "take_profit"):
            _finite_positive(getattr(self, name), name)
        if (
            self.generated_at.tzinfo is None
            or self.generated_at.utcoffset() is None
            or self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
        ):
            raise ValueError("proposal timestamps must be timezone-aware")
        if self.expires_at <= self.generated_at:
            raise ValueError("proposal expiry must follow generation time")
        if self.direction == Direction.BULLISH:
            if not self.stop_loss < self.entry < self.take_profit:
                raise ValueError("long proposal requires stop < entry < target")
        elif not self.take_profit < self.entry < self.stop_loss:
            raise ValueError("short proposal requires target < entry < stop")
        if not all(
            currency.strip() and value.is_finite()
            for currency, value in self.currency_exposure_per_lot.items()
        ):
            raise ValueError("currency exposures must have names and finite values")

    @property
    def reward_risk(self) -> Decimal:
        return abs(self.take_profit - self.entry) / abs(self.entry - self.stop_loss)


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    state: str
    reasons: tuple[str, ...]
    proposal: TradeProposal | None = None
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionQuote:
    bid: Decimal
    ask: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        _finite_positive(self.bid, "bid")
        _finite_positive(self.ask, "ask")
        if self.ask < self.bid:
            raise ValueError("ask must be >= bid")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("quote timestamp must be timezone-aware")

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid
