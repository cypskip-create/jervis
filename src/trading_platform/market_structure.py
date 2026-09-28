"""Objective OHLC structure primitives with explicit confirmation timing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class PivotKind(StrEnum):
    HIGH = "high"
    LOW = "low"


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class LiquiditySide(StrEnum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class Regime(StrEnum):
    TRENDING = "trending"
    RANGING = "ranging"
    ABNORMAL_VOLATILITY = "abnormal_volatility"
    NEUTRAL = "neutral"


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

    def __post_init__(self) -> None:
        if self.index < 0 or self.confirmation_index < self.index:
            raise ValueError("pivot confirmation index cannot precede its non-negative pivot index")
        if not isfinite(self.price):
            raise ValueError("pivot price must be finite")


@dataclass(frozen=True, slots=True)
class LevelCluster:
    """A deterministic price cluster formed from already-confirmed pivots."""

    kind: PivotKind
    price: float
    pivots: tuple[Pivot, ...]
    last_confirmation_index: int

    @property
    def touches(self) -> int:
        return len(self.pivots)

    @property
    def is_equal_level(self) -> bool:
        return self.touches >= 2


@dataclass(frozen=True, slots=True)
class RegimeResult:
    regime: Regime
    normalized_atr: float
    ema_separation: float
    directional_persistence: float


@dataclass(frozen=True, slots=True)
class RegimeParameters:
    atr_window: int = 14
    fast_ema: int = 9
    slow_ema: int = 21
    min_trend_separation: float = 0.001
    min_trend_persistence: float = 0.60
    max_range_persistence: float = 0.25
    max_range_separation: float = 0.0005
    abnormal_normalized_atr: float = 0.02

    def __post_init__(self) -> None:
        if self.atr_window < 1 or self.fast_ema < 1 or self.slow_ema <= self.fast_ema:
            raise ValueError("EMA and ATR windows must be positive and slow_ema > fast_ema")
        if not 0 <= self.max_range_persistence < self.min_trend_persistence <= 1:
            raise ValueError("persistence thresholds must satisfy 0 <= range < trend <= 1")
        if (
            min(
                self.min_trend_separation,
                self.max_range_separation,
                self.abnormal_normalized_atr,
            )
            <= 0
        ):
            raise ValueError("separation and volatility thresholds must be positive")
        if not all(
            isfinite(value)
            for value in (
                self.min_trend_separation,
                self.min_trend_persistence,
                self.max_range_persistence,
                self.max_range_separation,
                self.abnormal_normalized_atr,
            )
        ):
            raise ValueError("regime thresholds must be finite")


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

    if not isfinite(level) or not isfinite(buffer) or buffer < 0:
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

    if not isfinite(level) or not isfinite(min_penetration) or min_penetration < 0:
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


def cluster_pivots(
    pivots: Sequence[Pivot],
    *,
    tolerance: float,
    minimum_touches: int = 1,
) -> list[LevelCluster]:
    """Cluster same-kind prices within tolerance, without transitive chaining.

    Each cluster is anchored at its lowest sorted price; every member must be
    within tolerance of that anchor. Returned price is the arithmetic mean.
    Since inputs are confirmed pivots, the cluster becomes usable no earlier
    than its latest member's confirmation index.
    """

    if not isfinite(tolerance) or tolerance < 0 or minimum_touches < 1:
        raise ValueError("tolerance must be non-negative and minimum_touches >= 1")
    result: list[LevelCluster] = []
    for kind in PivotKind:
        ordered = sorted(
            (pivot for pivot in pivots if pivot.kind == kind), key=lambda pivot: pivot.price
        )
        current: list[Pivot] = []
        anchor: float | None = None
        for pivot in ordered:
            if anchor is None or pivot.price - anchor <= tolerance:
                current.append(pivot)
                if anchor is None:
                    anchor = pivot.price
            else:
                if len(current) >= minimum_touches:
                    result.append(_make_cluster(kind, current))
                current = [pivot]
                anchor = pivot.price
        if len(current) >= minimum_touches:
            result.append(_make_cluster(kind, current))
    return sorted(result, key=lambda cluster: (cluster.kind.value, cluster.price))


def _make_cluster(kind: PivotKind, pivots: Sequence[Pivot]) -> LevelCluster:
    return LevelCluster(
        kind=kind,
        price=sum(pivot.price for pivot in pivots) / len(pivots),
        pivots=tuple(sorted(pivots, key=lambda pivot: pivot.confirmation_index)),
        last_confirmation_index=max(pivot.confirmation_index for pivot in pivots),
    )


def detect_retest(
    candle: Candle,
    *,
    level: float,
    direction: Direction,
    tolerance: float,
    bos_index: int,
    current_index: int,
    expiry_bars: int,
) -> bool:
    """Return true for an in-window revisit of a broken level after BOS.

    This tests contact only; confirmation and entry are separate state-machine
    steps. A bullish retest must hold the level within the lower tolerance band,
    while a bearish retest must hold below the upper band.
    """

    if not isfinite(level) or not isfinite(tolerance) or tolerance < 0 or expiry_bars < 1:
        raise ValueError("tolerance must be non-negative and expiry_bars >= 1")
    if current_index <= bos_index or current_index > bos_index + expiry_bars:
        return False
    touched = candle.low <= level + tolerance and candle.high >= level - tolerance
    if not touched:
        return False
    if direction == Direction.BULLISH:
        return candle.close >= level - tolerance
    return candle.close <= level + tolerance


def classify_regime(
    candles: Sequence[Candle],
    *,
    parameters: RegimeParameters | None = None,
) -> RegimeResult:
    """Classify the trailing completed window using explicit numeric rules.

    Normalized ATR is mean true range over `atr_window` divided by the latest
    close. EMA separation is absolute fast/slow EMA difference divided by close.
    Directional persistence is absolute net close movement divided by total
    absolute close movement over `slow_ema` changes. Abnormal volatility takes
    precedence; trending requires both EMA separation and persistence; ranging
    requires low persistence and low separation; all remaining cases are neutral.
    """

    parameters = parameters or RegimeParameters()
    required = max(parameters.slow_ema + 1, parameters.atr_window + 1)
    if len(candles) < required:
        raise ValueError(f"at least {required} completed candles are required")
    closes = [bar.close for bar in candles]
    fast = _ema(closes, parameters.fast_ema)
    slow = _ema(closes, parameters.slow_ema)
    close = closes[-1]
    if close <= 0:
        raise ValueError("latest close must be positive for normalized regime metrics")
    true_ranges = [
        max(
            bar.high - bar.low,
            abs(bar.high - candles[index - 1].close),
            abs(bar.low - candles[index - 1].close),
        )
        for index, bar in enumerate(
            candles[-parameters.atr_window :], start=len(candles) - parameters.atr_window
        )
    ]
    normalized_atr = sum(true_ranges) / parameters.atr_window / close
    separation = abs(fast - slow) / close
    changes = [
        closes[index] - closes[index - 1]
        for index in range(len(closes) - parameters.slow_ema, len(closes))
    ]
    total_movement = sum(abs(change) for change in changes)
    persistence = abs(sum(changes)) / total_movement if total_movement else 0.0

    if normalized_atr >= parameters.abnormal_normalized_atr:
        regime = Regime.ABNORMAL_VOLATILITY
    elif (
        separation >= parameters.min_trend_separation
        and persistence >= parameters.min_trend_persistence
    ):
        regime = Regime.TRENDING
    elif (
        persistence <= parameters.max_range_persistence
        and separation <= parameters.max_range_separation
    ):
        regime = Regime.RANGING
    else:
        regime = Regime.NEUTRAL
    return RegimeResult(regime, normalized_atr, separation, persistence)


def _ema(values: Sequence[float], period: int) -> float:
    alpha = 2 / (period + 1)
    current = values[0]
    for value in values[1:]:
        current = alpha * value + (1 - alpha) * current
    return current
