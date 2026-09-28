"""Gold, NASDAQ and Forex setup machines. Strategies only emit proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from ..domain import StrategyDecision, TradeProposal
from ..market_structure import (
    Candle,
    Direction,
    LiquiditySide,
    close_breaks_structure,
    detect_liquidity_sweep,
    detect_retest,
)


class ConfirmationMode(StrEnum):
    REJECTION = "rejection"
    ENGULFING = "engulfing"
    DISPLACEMENT = "displacement"


class MachineStrategy(Protocol):
    key: str
    version: str

    def analyze(self, frame: StrategyFrame) -> StrategyDecision: ...


@dataclass(frozen=True, slots=True)
class LiquidityLevel:
    price: float
    side: LiquiditySide
    label: str

    def __post_init__(self) -> None:
        if not isfinite(self.price) or self.price <= 0 or not self.label.strip():
            raise ValueError("liquidity level needs a positive finite price and label")


@dataclass(frozen=True, slots=True)
class StructuralEvent:
    """A close-confirmed BOS/CHoCH emitted by an objective timeframe analyzer."""

    kind: str
    direction: Direction
    level: float
    bar_index: int
    confirmed_at: datetime
    timeframe: str

    def __post_init__(self) -> None:
        if self.kind not in {"bos", "choch"}:
            raise ValueError("structural event kind must be bos or choch")
        if not isfinite(self.level) or self.level <= 0 or self.bar_index < 0:
            raise ValueError("structural event level/index is invalid")
        if self.confirmed_at.tzinfo is None or not self.timeframe.strip():
            raise ValueError("structural event requires aware time and timeframe")


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    minimum_rr: Decimal
    maximum_setup_bars: int = 24
    zone_tolerance: float = 0.5
    sweep_penetration: float = 0.05
    break_buffer: float = 0.0
    stop_buffer: float = 0.05
    retest_tolerance: float = 0.1
    fib_tolerance: float = 0.1
    require_fibonacci: bool = False
    confirmation_mode: ConfirmationMode = ConfirmationMode.DISPLACEMENT
    rejection_wick_body_ratio: float = 1.5
    displacement_body_range_ratio: float = 0.65
    close_extreme_fraction: float = 0.25
    maximum_compression_atr: float = 0.65
    signal_ttl_seconds: int = 30

    def __post_init__(self) -> None:
        if not self.minimum_rr.is_finite() or self.minimum_rr <= 0:
            raise ValueError("minimum_rr must be finite and positive")
        if self.maximum_setup_bars < 1 or self.signal_ttl_seconds < 1:
            raise ValueError("setup expiry and signal TTL must be positive")
        thresholds = (
            self.zone_tolerance,
            self.sweep_penetration,
            self.break_buffer,
            self.stop_buffer,
            self.retest_tolerance,
            self.fib_tolerance,
            self.rejection_wick_body_ratio,
            self.displacement_body_range_ratio,
            self.close_extreme_fraction,
            self.maximum_compression_atr,
        )
        if not all(isfinite(value) and value >= 0 for value in thresholds):
            raise ValueError("strategy thresholds must be finite and non-negative")
        if not 0 < self.displacement_body_range_ratio <= 1:
            raise ValueError("displacement body/range ratio must be in (0, 1]")
        if not 0 < self.close_extreme_fraction <= 0.5:
            raise ValueError("close extreme fraction must be in (0, 0.5]")


@dataclass(frozen=True, slots=True)
class StrategyFrame:
    symbol: str
    asset_class: str
    timestamp: datetime
    bar_index: int
    timeframe: str
    candle: Candle
    previous_candle: Candle | None = None
    context_direction: Direction | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None
    ema_slope: float | None = None
    support_levels: tuple[float, ...] = ()
    resistance_levels: tuple[float, ...] = ()
    fibonacci_prices: tuple[float, ...] = ()
    confirmation_level: float | None = None
    liquidity_levels: tuple[LiquidityLevel, ...] = ()
    choch: StructuralEvent | None = None
    bos: StructuralEvent | None = None
    target_level: float | None = None
    structural_invalidation: float | None = None
    compression_atr: float | None = None

    def __post_init__(self) -> None:
        if (
            not self.symbol.strip()
            or not self.asset_class.strip()
            or not self.timeframe.strip()
            or self.bar_index < 0
        ):
            raise ValueError("strategy frame identity/index is invalid")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("strategy frame timestamp must be timezone-aware")
        values = (
            *self.support_levels,
            *self.resistance_levels,
            *self.fibonacci_prices,
        )
        if not all(isfinite(value) and value > 0 for value in values):
            raise ValueError("strategy levels must be finite and positive")
        for value in (
            self.ema_fast,
            self.ema_slow,
            self.ema_slope,
            self.confirmation_level,
            self.target_level,
            self.structural_invalidation,
            self.compression_atr,
        ):
            if value is not None and not isfinite(value):
                raise ValueError("strategy features must be finite")


class BaseStrategy:
    key = "base"
    version = "1.0.0"

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.state = "CONTEXT"
        self._started_index: int | None = None
        self._context: Direction | None = None
        self._evidence: dict[str, Any] = {}

    def analyze(self, frame: StrategyFrame) -> StrategyDecision:
        raise NotImplementedError

    def _reset(self, state: str = "CONTEXT") -> None:
        self.state = state
        self._started_index = None
        self._context = None
        self._evidence = {}

    def _expired(self, frame: StrategyFrame) -> bool:
        return (
            self._started_index is not None
            and frame.bar_index - self._started_index > self.config.maximum_setup_bars
        )

    def _proposal(
        self,
        frame: StrategyFrame,
        *,
        direction: Direction,
        entry: float,
        stop: float,
        target: float,
        reasons: tuple[str, ...],
    ) -> TradeProposal | None:
        try:
            proposal = TradeProposal(
                signal_id=_signal_id(self.key, frame.symbol, frame.bar_index, direction),
                symbol=frame.symbol,
                asset_class=frame.asset_class,
                strategy_key=self.key,
                strategy_version=self.version,
                setup_id=self.key,
                direction=direction,
                entry=Decimal(str(entry)),
                stop_loss=Decimal(str(stop)),
                take_profit=Decimal(str(target)),
                generated_at=frame.timestamp,
                expires_at=frame.timestamp + timedelta(seconds=self.config.signal_ttl_seconds),
                reasons=reasons,
            )
        except ValueError:
            return None
        if proposal.reward_risk < self.config.minimum_rr:
            return None
        return proposal

    def _decision(
        self,
        reasons: tuple[str, ...],
        *,
        proposal: TradeProposal | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> StrategyDecision:
        return StrategyDecision(self.state, reasons, proposal, evidence or dict(self._evidence))


class GoldStrategy(BaseStrategy):
    """HTF/EMA context, structural pullback, optional fib confluence, confirmation."""

    key = "gold"
    version = "gold_v1.0.0"

    def analyze(self, frame: StrategyFrame) -> StrategyDecision:
        if self.state in {"PROPOSED", "REJECTED", "EXPIRED", "INVALID"}:
            self._reset("WAIT_PULLBACK")
        direction = frame.context_direction
        if direction is None or not _ema_aligned(frame, direction):
            self._reset("CONTEXT")
            return self._decision(("higher-timeframe structure and EMA trend are not aligned",))
        if self._context not in (None, direction):
            self._reset("CONTEXT")
        self._context = direction

        if self.state in {"CONTEXT", "WAIT_PULLBACK"}:
            levels = (
                frame.support_levels if direction == Direction.BULLISH else frame.resistance_levels
            )
            location = _nearest_within(frame.candle.close, levels, self.config.zone_tolerance)
            fib = _nearest_within(
                frame.candle.close, frame.fibonacci_prices, self.config.fib_tolerance
            )
            if location is None:
                self.state = "WAIT_PULLBACK"
                return self._decision(("waiting for pullback to confirmed structural zone",))
            if self.config.require_fibonacci and fib is None:
                self.state = "WAIT_PULLBACK"
                return self._decision(
                    ("structural zone found; optional Fibonacci confluence missing",)
                )
            self.state = "WAIT_CONFIRMATION"
            self._started_index = frame.bar_index
            self._evidence.update({"zone": location, "fibonacci": fib, "trend": direction.value})
            return self._decision(("structural pullback zone reached",))

        if self._expired(frame):
            self._reset("EXPIRED")
            return self._decision(("gold setup expired before confirmation",))
        if frame.structural_invalidation is None or frame.target_level is None:
            return self._decision(("waiting for structural stop and opposing target levels",))
        invalid = (
            frame.candle.close <= frame.structural_invalidation
            if direction == Direction.BULLISH
            else frame.candle.close >= frame.structural_invalidation
        )
        if invalid:
            self._reset("INVALID")
            return self._decision(("structural invalidation reached",))
        confirmation_level = frame.confirmation_level
        if confirmation_level is None:
            return self._decision(("waiting for confirmed execution structure level",))
        # Gold confirmation must close through the latest opposing micro-structure.
        if not _directional_confirmation(
            frame.candle, direction, confirmation_level, self.config.break_buffer
        ):
            return self._decision(("waiting for close-confirmed execution structure break",))
        proposal = self._proposal(
            frame,
            direction=direction,
            entry=frame.candle.close,
            stop=frame.structural_invalidation,
            target=frame.target_level,
            reasons=(
                "HTF structure aligned",
                "EMA ordering and slope aligned",
                "structural pullback",
                "close-confirmed execution structure break",
            ),
        )
        if proposal is None:
            self._reset("REJECTED")
            return self._decision(
                ("invalid structural geometry or achievable RR below strategy minimum",)
            )
        self.state = "PROPOSED"
        self._evidence.update({"entry": str(proposal.entry), "rr": str(proposal.reward_risk)})
        return self._decision(proposal.reasons, proposal=proposal)


class LiquiditySweepStrategy(BaseStrategy):
    """Shared sweep → CHoCH → BOS → retest → confirmation machine."""

    key = "liquidity_sweep"
    version = "1.0.0"
    choch_timeframe = "M5"
    bos_timeframe = "M5"

    def analyze(self, frame: StrategyFrame) -> StrategyDecision:
        if self.state in {"PROPOSED", "REJECTED", "EXPIRED", "INVALID"}:
            self._reset("WAIT_SWEEP")
        direction = frame.context_direction
        if direction is None:
            self._reset("CONTEXT")
            return self._decision(("neutral context blocks trend-following entries",))
        if self._context not in (None, direction):
            self._reset("CONTEXT")
        self._context = direction
        if self._expired(frame):
            self._reset("EXPIRED")
            return self._decision(("liquidity sequence expired",))

        intended = direction
        required_side = (
            LiquiditySide.SELL_SIDE if intended == Direction.BULLISH else LiquiditySide.BUY_SIDE
        )
        if self.state in {"CONTEXT", "WAIT_SWEEP"}:
            for level in frame.liquidity_levels:
                if level.side == required_side and detect_liquidity_sweep(
                    frame.candle,
                    level=level.price,
                    side=level.side,
                    min_penetration=self.config.sweep_penetration,
                ):
                    self.state = "WAIT_CHOCH"
                    self._started_index = frame.bar_index
                    self._evidence.update(
                        {
                            "swept_level": level.price,
                            "sweep_label": level.label,
                            "sweep_time": frame.timestamp.isoformat(),
                            "sweep_high": frame.candle.high,
                            "sweep_low": frame.candle.low,
                        }
                    )
                    return self._decision((f"{level.label} liquidity swept and reclaimed",))
            self.state = "WAIT_SWEEP"
            return self._decision((f"waiting for {required_side.value} liquidity sweep",))

        if self.state == "WAIT_CHOCH":
            event = frame.choch
            if (
                event is not None
                and _valid_event(event, "choch", intended, self.choch_timeframe, frame)
                and event.confirmed_at > datetime.fromisoformat(str(self._evidence["sweep_time"]))
            ):
                self._evidence["choch_level"] = event.level
                self._evidence["choch_index"] = event.bar_index
                self._evidence["choch_time"] = event.confirmed_at.isoformat()
                self.state = "WAIT_BOS"
                return self._decision(("close-confirmed CHoCH observed",))
            return self._decision((f"waiting for {self.choch_timeframe} close-confirmed CHoCH",))

        if self.state == "WAIT_BOS":
            event = frame.bos
            if (
                event is not None
                and _valid_event(event, "bos", intended, self.bos_timeframe, frame)
                and event.confirmed_at
                > datetime.fromisoformat(str(self._evidence["choch_time"]))
            ):
                self._evidence["bos_level"] = event.level
                self._evidence["bos_index"] = event.bar_index
                self.state = "WAIT_RETEST"
                return self._decision(("subsequent close-confirmed BOS observed",))
            return self._decision((f"waiting for subsequent {self.bos_timeframe} BOS",))

        bos_level = float(self._evidence.get("bos_level", 0))
        bos_index = int(self._evidence.get("bos_index", -1))
        if self.state == "WAIT_RETEST":
            if detect_retest(
                frame.candle,
                level=bos_level,
                direction=intended,
                tolerance=self.config.retest_tolerance,
                bos_index=bos_index,
                current_index=frame.bar_index,
                expiry_bars=self.config.maximum_setup_bars,
            ):
                self._evidence["retest_index"] = frame.bar_index
                self.state = "WAIT_CONFIRMATION"
                return self._decision(("post-BOS retest observed; waiting for confirmation",))
            return self._decision(("waiting for retest; price will not be chased",))

        if self.state == "WAIT_CONFIRMATION":
            if frame.bar_index <= int(self._evidence["retest_index"]):
                return self._decision(("confirmation must occur after retest bar",))
            if not candle_confirmation(
                frame.candle,
                frame.previous_candle,
                intended,
                self.config,
            ):
                return self._decision(
                    (f"waiting for {self.config.confirmation_mode.value} confirmation",)
                )
            stop = (
                float(self._evidence["sweep_low"]) - self.config.stop_buffer
                if intended == Direction.BULLISH
                else float(self._evidence["sweep_high"]) + self.config.stop_buffer
            )
            target = frame.target_level
            if target is None:
                return self._decision(("waiting for next opposing liquidity target",))
            proposal = self._proposal(
                frame,
                direction=intended,
                entry=frame.candle.close,
                stop=stop,
                target=target,
                reasons=(
                    "liquidity swept",
                    "close-confirmed CHoCH",
                    "subsequent close-confirmed BOS",
                    "post-BOS retest",
                    f"{self.config.confirmation_mode.value} confirmed",
                ),
            )
            if proposal is None:
                self._reset("REJECTED")
                return self._decision(("structural target fails minimum achievable RR",))
            self.state = "PROPOSED"
            self._evidence.update({"entry": str(proposal.entry), "rr": str(proposal.reward_risk)})
            return self._decision(proposal.reasons, proposal=proposal)

        return self._decision(("setup already proposed; await a new sequence",))


class NasdaqStrategy(LiquiditySweepStrategy):
    key = "nasdaq_sweep"
    version = "nasdaq_v1.0.0"
    choch_timeframe = "M5"
    bos_timeframe = "M5"


class ForexLiquidityStrategy(LiquiditySweepStrategy):
    key = "fx_liquidity_pullback"
    version = "forex_v1.0.0"
    choch_timeframe = "M15"
    bos_timeframe = "M5"


class ForexContinuationStrategy(BaseStrategy):
    """Trend pullback continuation; sweep is intentionally not required."""

    key = "fx_trend_continuation"
    version = "forex_v1.0.0"

    def analyze(self, frame: StrategyFrame) -> StrategyDecision:
        if self.state in {"PROPOSED", "REJECTED", "EXPIRED", "INVALID"}:
            self._reset("WAIT_PULLBACK")
        direction = frame.context_direction
        if direction is None:
            self._reset("CONTEXT")
            return self._decision(("neutral context blocks continuation setup",))
        if self._context not in (None, direction):
            self._reset("CONTEXT")
        self._context = direction
        levels = frame.support_levels if direction == Direction.BULLISH else frame.resistance_levels
        location = _nearest_within(frame.candle.close, levels, self.config.zone_tolerance)

        if self.state in {"CONTEXT", "WAIT_PULLBACK"}:
            self.state = "WAIT_LOCATION" if location is None else "WAIT_COMPRESSION_OR_REJECTION"
            self._started_index = frame.bar_index if location is not None else None
            if location is None:
                return self._decision(("waiting for trend pullback into important level",))
            self._evidence["location"] = location
            return self._decision(("pullback reached important structural level",))
        if self._expired(frame):
            self._reset("EXPIRED")
            return self._decision(("continuation setup expired",))
        if self.state == "WAIT_LOCATION":
            if location is not None:
                self._started_index = frame.bar_index
                self._evidence["location"] = location
                self.state = "WAIT_COMPRESSION_OR_REJECTION"
                return self._decision(("pullback reached important structural level",))
            return self._decision(("waiting for important structural level",))
        if self.state == "WAIT_COMPRESSION_OR_REJECTION":
            compression = frame.compression_atr is not None and (
                frame.compression_atr <= self.config.maximum_compression_atr
            )
            rejection = candle_confirmation(
                frame.candle, frame.previous_candle, direction, self.config
            )
            if compression or rejection:
                self._evidence["compression_atr"] = frame.compression_atr
                self._evidence["rejection"] = rejection
                self._evidence["compression_index"] = frame.bar_index
                self.state = "WAIT_BOS"
                return self._decision(("quantified compression or level rejection observed",))
            return self._decision(("waiting for quantified compression or rejection",))
        if self.state == "WAIT_BOS":
            event = frame.bos
            if (
                event is not None
                and _valid_event(event, "bos", direction, "M5", frame)
                and event.bar_index > int(self._evidence["compression_index"])
            ):
                self._evidence.update({"bos_level": event.level, "bos_index": event.bar_index})
                self.state = "WAIT_RETEST"
                return self._decision(("close-confirmed continuation BOS observed",))
            return self._decision(("waiting for close-confirmed M5 BOS",))
        if self.state == "WAIT_RETEST":
            level = float(self._evidence["bos_level"])
            bos_index = int(self._evidence["bos_index"])
            if detect_retest(
                frame.candle,
                level=level,
                direction=direction,
                tolerance=self.config.retest_tolerance,
                bos_index=bos_index,
                current_index=frame.bar_index,
                expiry_bars=self.config.maximum_setup_bars,
            ):
                self._evidence["retest_index"] = frame.bar_index
                self.state = "WAIT_CONFIRMATION"
                return self._decision(("continuation retest observed",))
            return self._decision(("waiting for retest; price will not be chased",))
        if self.state == "WAIT_CONFIRMATION":
            if frame.bar_index <= int(self._evidence["retest_index"]):
                return self._decision(("confirmation must occur after retest bar",))
            if not candle_confirmation(frame.candle, frame.previous_candle, direction, self.config):
                return self._decision(("waiting for continuation confirmation",))
            if frame.target_level is None or frame.structural_invalidation is None:
                return self._decision(("waiting for structural stop and opposing target",))
            proposal = self._proposal(
                frame,
                direction=direction,
                entry=frame.candle.close,
                stop=frame.structural_invalidation,
                target=frame.target_level,
                reasons=(
                    "HTF trend aligned",
                    "important-level pullback",
                    "quantified compression/rejection",
                    "M5 BOS",
                    "retest",
                    f"{self.config.confirmation_mode.value} confirmed",
                ),
            )
            if proposal is None:
                self._reset("REJECTED")
                return self._decision(("structural target fails minimum achievable RR",))
            self.state = "PROPOSED"
            return self._decision(proposal.reasons, proposal=proposal)
        return self._decision(("setup already proposed; await a new sequence",))


def candle_confirmation(
    current: Candle,
    previous: Candle | None,
    direction: Direction,
    config: StrategyConfig,
) -> bool:
    """Mathematical rejection, engulfing or displacement confirmation."""

    candle_range = current.high - current.low
    if candle_range <= 0:
        return False
    body = abs(current.close - current.open)
    close_location = (current.close - current.low) / candle_range
    bullish = direction == Direction.BULLISH
    directional_close = current.close > current.open if bullish else current.close < current.open
    if config.confirmation_mode == ConfirmationMode.DISPLACEMENT:
        body_ratio = body / candle_range
        extreme_close = (
            close_location >= 1 - config.close_extreme_fraction
            if bullish
            else close_location <= config.close_extreme_fraction
        )
        return (
            directional_close
            and body_ratio >= config.displacement_body_range_ratio
            and extreme_close
        )
    if config.confirmation_mode == ConfirmationMode.REJECTION:
        wick = (
            (min(current.open, current.close) - current.low)
            if bullish
            else (current.high - max(current.open, current.close))
        )
        extreme_close = (
            close_location >= 1 - config.close_extreme_fraction
            if bullish
            else close_location <= config.close_extreme_fraction
        )
        return (
            directional_close and wick >= body * config.rejection_wick_body_ratio and extreme_close
        )
    if previous is None:
        return False
    previous_bearish = previous.close < previous.open
    previous_bullish = previous.close > previous.open
    current_body_low, current_body_high = sorted((current.open, current.close))
    previous_body_low, previous_body_high = sorted((previous.open, previous.close))
    engulfs = current_body_low <= previous_body_low and current_body_high >= previous_body_high
    return engulfs and (previous_bearish if bullish else previous_bullish) and directional_close


def _ema_aligned(frame: StrategyFrame, direction: Direction) -> bool:
    if frame.ema_fast is None or frame.ema_slow is None or frame.ema_slope is None:
        return False
    if direction == Direction.BULLISH:
        return frame.ema_fast > frame.ema_slow and frame.ema_slope > 0
    return frame.ema_fast < frame.ema_slow and frame.ema_slope < 0


def _nearest_within(price: float, levels: tuple[float, ...], tolerance: float) -> float | None:
    nearby = [level for level in levels if abs(price - level) <= tolerance]
    return min(nearby, key=lambda level: abs(price - level)) if nearby else None


def _directional_confirmation(
    candle: Candle, direction: Direction, level: float, buffer: float
) -> bool:
    return close_breaks_structure(candle, level=level, direction=direction, buffer=buffer)


def _valid_event(
    event: StructuralEvent | None,
    kind: str,
    direction: Direction,
    timeframe: str,
    frame: StrategyFrame,
) -> bool:
    return bool(
        event is not None
        and event.kind == kind
        and event.direction == direction
        and event.timeframe == timeframe
        and event.confirmed_at <= frame.timestamp
        and (event.timeframe != frame.timeframe or event.bar_index <= frame.bar_index)
    )


def _signal_id(key: str, symbol: str, bar_index: int, direction: Direction) -> str:
    raw = f"{key}:{symbol}:{bar_index}:{direction.value}"
    return str(uuid5(NAMESPACE_URL, raw))
