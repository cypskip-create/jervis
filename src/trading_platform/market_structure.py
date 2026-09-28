"""Objective OHLC structure primitives with explicit confirmation timing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Sequence


class PivotKind(StrEnum):
    HIGH = "high"
    LOW = "low"


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class LiquiditySide(StrEnum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


@dataclass(frozen=True, slots=True)
class Candle:
    """One completed OHLC candle. Index order is chronological."""

    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close)
        if not all(value == value and abs(value) != float("inf") for value in values):
            raise ValueError("OHLC values must be finite")
        if self.high < self.low:
            raise ValueError("high must be greater than or equal to low")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("high/low must contain open and close")


@dataclass(frozen=True, slots=True)
class Pivot:
    """A pivot and the first candle index at which it is knowable."""

    kind: PivotKind
    index: int
    confirmation_index: int
    price: float


def confirmed_pivots(
    candles: Sequence[Candle],
    *,
    left: int = 2,
    right: int = 2,
    as_of_index: int | None = None,
) -> list[Pivot]:
    """Return strict pivots whose right-hand confirmation bars have closed.

    Ties reject the candidate. A pivot at index ``i`` is only emitted when
    ``i + right <= as_of_index``; by default, the last supplied candle is the
    current completed candle. Callers processing a live stream should pass only
    completed bars and should not backdate a pivot's availability to ``index``.
    """

    if left < 1 or right < 1:
        raise ValueError("left and right must both be at least 1")
    if as_of_index is None:
        as_of_index = len(candles) - 1
    if as_of_index < -1 or as_of_index >= len(candles):
        raise ValueError("as_of_index must identify a supplied candle or be -1")

    pivots: list[Pivot] = []
    first = left
    last = min(len(candles) - right - 1, as_of_index - right)
    for index in range(first, last + 1):
        candle = candles[index]
        neighbors = (*candles[index - left : index], *candles[index + 1 : index + right + 1])
        neighbor_highs = [bar.high for bar in neighbors]
        neighbor_lows = [bar.low for bar in neighbors]
        if all(candle.high > value for value in neighbor_highs):
            pivots.append(Pivot(PivotKind.HIGH, index, index + right, candle.high))
        if all(candle.low < value for value in neighbor_lows):
            pivots.append(Pivot(PivotKind.LOW, index, index + right, candle.low))
    return pivots


def close_breaks_structure(
    candle: Candle,
    *,
    level: float,
    direction: Direction,
    buffer: float = 0.0,
) -> bool:
    """Test a close beyond a structure level; a wick through is insufficient."""

    if buffer < 0:
        raise ValueError("buffer must be non-negative")
    if direction == Direction.BULLISH:
        return candle.close > level + buffer
    return candle.close < level - buffer


def detect_liquidity_sweep(
    candle: Candle,
    *,
    level: float,
    side: LiquiditySide,
    min_penetration: float = 0.0,
) -> bool:
    """Detect a same-candle wick breach and reclaim of an external level.

    Sell-side liquidity requires a low below the level and a close back above
    it. Buy-side liquidity is the inverse. A zero penetration permits any
    strictly positive crossing; equality alone is not a sweep.
    """

    if min_penetration < 0:
        raise ValueError("min_penetration must be non-negative")
    if side == LiquiditySide.SELL_SIDE:
        return candle.low < level - min_penetration and candle.close > level
    return candle.high > level + min_penetration and candle.close < level


def is_choch(
    candle: Candle,
    *,
    level: float,
    prior_direction: Direction,
    buffer: float = 0.0,
) -> bool:
    """A close-confirmed break against the stated prior structure direction."""

    intended = Direction.BEARISH if prior_direction == Direction.BULLISH else Direction.BULLISH
    return close_breaks_structure(candle, level=level, direction=intended, buffer=buffer)


def direction_from_label(value: Literal["bullish", "bearish"]) -> Direction:
    """Typed conversion helper for deserialized configuration values."""

    return Direction(value)
