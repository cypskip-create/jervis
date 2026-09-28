"""Typed, broker-neutral market data contracts and freshness validation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Protocol

from .market_structure import Candle


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    observed_at: datetime
    source: str

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.source.strip():
            raise ValueError("symbol and source must not be blank")
        if not isfinite(self.bid) or not isfinite(self.ask):
            raise ValueError("quote prices must be finite")
        if self.bid <= 0 or self.ask < self.bid:
            raise ValueError("quote requires positive bid and ask >= bid")
        if not isfinite(self.ask - self.bid):
            raise ValueError("quote spread must be finite")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class MarketBar:
    symbol: str
    timeframe: str
    opened_at: datetime
    candle: Candle
    volume: float | None = None
    source: str = ""

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.timeframe.strip():
            raise ValueError("symbol and timeframe must not be blank")
        if self.opened_at.tzinfo is None or self.opened_at.utcoffset() is None:
            raise ValueError("opened_at must be timezone-aware")
        if self.volume is not None and (not isfinite(self.volume) or self.volume < 0):
            raise ValueError("volume must be non-negative")


class MarketDataPort(Protocol):
    """Minimal read-only contract implemented by historical/live providers."""

    def get_quote(self, broker_symbol: str) -> Quote | None: ...

    def get_bars(
        self, broker_symbol: str, timeframe: str, *, limit: int
    ) -> Sequence[MarketBar]: ...


def require_fresh_quote(
    quote: Quote,
    *,
    now: datetime,
    max_age: timedelta,
) -> None:
    """Raise if quote age is negative, stale, or freshness settings are invalid."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    age = now.astimezone(UTC) - quote.observed_at.astimezone(UTC)
    if age < timedelta(0):
        raise ValueError("quote timestamp is in the future")
    if age > max_age:
        raise ValueError(f"stale quote: age {age.total_seconds():.3f}s exceeds limit")
