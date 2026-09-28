"""Deterministic, bar-by-bar portfolio replay with conservative fill assumptions."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any, Protocol

from sqlalchemy.orm import Session

from .domain import TradeProposal
from .market_structure import Candle
from .models import Backtest
from .models import BacktestTrade as BacktestTradeRecord
from .risk import InstrumentRiskSpec, calculate_position_size


@dataclass(frozen=True, slots=True)
class MarketBar:
    symbol: str
    timeframe: str
    timestamp: datetime
    index: int
    candle: Candle
    spread: Decimal = Decimal("0")
    session: str = "unknown"
    regime: str = "unknown"

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.timeframe.strip() or self.index < 0:
            raise ValueError("bar symbol/timeframe must be set and index non-negative")
        if not self.session.strip() or not self.regime.strip():
            raise ValueError("bar session and regime labels must not be blank")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("bar timestamp must be timezone-aware")
        if not self.spread.is_finite() or self.spread < 0:
            raise ValueError("spread must be finite and non-negative")


class ReplayStrategy(Protocol):
    def __call__(
        self, timestamp: datetime, history: dict[tuple[str, str], tuple[MarketBar, ...]]
    ) -> Iterable[TradeProposal]: ...


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_equity: Decimal = Decimal("10000")
    risk_fraction: Decimal = Decimal("0.0025")
    maximum_total_risk_fraction: Decimal = Decimal("0.01")
    maximum_positions: int = 3
    slippage_ticks: Decimal = Decimal("0")
    maximum_daily_loss_fraction: Decimal = Decimal("0.01")
    maximum_weekly_loss_fraction: Decimal = Decimal("0.02")
    maximum_drawdown_fraction: Decimal = Decimal("0.05")

    def __post_init__(self) -> None:
        if not self.initial_equity.is_finite() or self.initial_equity <= 0:
            raise ValueError("initial equity must be finite and positive")
        for name in ("risk_fraction", "maximum_total_risk_fraction"):
            value = getattr(self, name)
            if not value.is_finite() or not 0 < value <= Decimal("0.01"):
                raise ValueError(f"{name} must be in (0, 1%]")
        for name in (
            "maximum_daily_loss_fraction",
            "maximum_weekly_loss_fraction",
            "maximum_drawdown_fraction",
        ):
            value = getattr(self, name)
            if not value.is_finite() or not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        if self.maximum_positions < 1:
            raise ValueError("maximum_positions must be positive")
        if not self.slippage_ticks.is_finite() or self.slippage_ticks < 0:
            raise ValueError("slippage_ticks must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    signal_id: str
    symbol: str
    strategy_key: str
    session: str
    regime: str
    direction: str
    entry_time: datetime
    exit_time: datetime
    entry: Decimal
    exit: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    volume: Decimal
    pnl: Decimal
    r_multiple: Decimal
    commission: Decimal
    exit_reason: str


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    net_return: Decimal
    cagr: Decimal | None
    maximum_drawdown: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal
    average_r: Decimal
    win_rate: Decimal
    trade_count: int
    sharpe: Decimal
    sortino: Decimal
    recovery_factor: Decimal | None
    maximum_consecutive_losses: int
    monthly_pnl: dict[str, Decimal]
    by_symbol: dict[str, dict[str, Decimal | int]]
    by_strategy: dict[str, dict[str, Decimal | int]]
    by_session: dict[str, dict[str, Decimal | int]]
    by_regime: dict[str, dict[str, Decimal | int]]


@dataclass(frozen=True, slots=True)
class BacktestResult:
    initial_equity: Decimal
    ending_equity: Decimal
    data_hash: str
    trades: tuple[BacktestTrade, ...]
    metrics: BacktestMetrics


@dataclass(slots=True)
class _OpenTrade:
    proposal: TradeProposal
    volume: Decimal
    risk_cash: Decimal
    entry: Decimal
    entry_time: datetime
    timeframe: str
    session: str
    regime: str
    commission: Decimal


def run_backtest(
    bars: Iterable[MarketBar],
    *,
    strategy: ReplayStrategy,
    instrument_specs: dict[str, InstrumentRiskSpec],
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Replay sorted events; strategy sees only bars at or before each event.

    OHLC values represent bid prices. Signals fill at the next bar's executable
    open. Long entries pay ask, exits sell bid, and shorts cover at ask.
    If OHLC touches both exits,
    the stop is assumed first. Pending signals expire and open positions close
    at the final close, so no position disappears from reported equity.
    """

    cfg = config or BacktestConfig()
    data = tuple(bars)
    if not data:
        raise ValueError("backtest requires at least one bar")
    if any(bar.symbol not in instrument_specs for bar in data):
        raise ValueError("instrument metadata missing for one or more symbols")
    ordered = tuple(
        sorted(data, key=lambda item: (item.timestamp, item.symbol, item.timeframe, item.index))
    )
    if len({(b.symbol, b.timeframe, b.index) for b in ordered}) != len(ordered):
        raise ValueError("duplicate symbol/timeframe/bar index in input data")

    history: dict[tuple[str, str], list[MarketBar]] = defaultdict(list)
    last_by_key: dict[tuple[str, str], MarketBar] = {}
    pending: dict[tuple[str, str], TradeProposal] = {}
    open_trades: dict[str, _OpenTrade] = {}
    completed: list[BacktestTrade] = []
    equity = cfg.initial_equity
    peak_equity = equity
    daily_reference = equity
    weekly_reference = equity
    active_day: date | None = None
    active_week: tuple[int, int] | None = None
    seen_signal_ids: set[str] = set()
    exposure = Decimal("0")
    data_hasher = sha256()
    for bar in ordered:
        utc_time = bar.timestamp.astimezone(UTC)
        week = (utc_time.isocalendar().year, utc_time.isocalendar().week)
        if active_day != utc_time.date():
            active_day = utc_time.date()
            daily_reference = equity
        if active_week != week:
            active_week = week
            weekly_reference = equity
        prior_bar = last_by_key.get((bar.symbol, bar.timeframe))
        if prior_bar is not None and bar.index <= prior_bar.index:
            raise ValueError("bar indices must increase with event time for each symbol/timeframe")
        data_hasher.update(
            f"{bar.symbol}|{bar.timeframe}|{bar.timestamp.isoformat()}|{bar.index}|"
            f"{bar.candle.open},{bar.candle.high},{bar.candle.low},{bar.candle.close}|"
            f"{bar.spread}|{bar.session}|{bar.regime}\n".encode()
        )
        key = (bar.symbol, bar.timeframe)
        # Close positions before considering new signals for this bar.
        active = open_trades.get(bar.symbol)
        if active is not None and bar.timeframe == active.timeframe:
            closed = _check_exit(active, bar, instrument_specs[bar.symbol], cfg)
            if closed is not None:
                completed.append(closed)
                equity += closed.pnl
                exposure -= active.risk_cash
                del open_trades[bar.symbol]
                peak_equity = max(peak_equity, equity)

        candidate = pending.pop(key, None)
        if candidate is not None and candidate.expires_at > bar.timestamp:
            if (
                candidate.symbol not in open_trades
                and len(open_trades) < cfg.maximum_positions
                and exposure < equity * cfg.maximum_total_risk_fraction
                and max(Decimal("0"), daily_reference - equity)
                < daily_reference * cfg.maximum_daily_loss_fraction
                and max(Decimal("0"), weekly_reference - equity)
                < weekly_reference * cfg.maximum_weekly_loss_fraction
                and (peak_equity - equity) / peak_equity < cfg.maximum_drawdown_fraction
            ):
                spec = instrument_specs[bar.symbol]
                long_trade = candidate.direction.value == "bullish"
                slip = cfg.slippage_ticks * spec.tick_size
                entry = Decimal(str(bar.candle.open)) + (bar.spread if long_trade else Decimal("0"))
                entry += slip if long_trade else -slip
                try:
                    volume, risk_cash = calculate_position_size(
                        equity=equity,
                        risk_fraction=cfg.risk_fraction,
                        entry=entry,
                        stop_loss=candidate.stop_loss,
                        spec=replace(
                            spec,
                            slippage_ticks=(
                                spec.slippage_ticks
                                + cfg.slippage_ticks
                                + bar.spread / spec.tick_size
                            ),
                        ),
                    )
                except ValueError:
                    volume, risk_cash = Decimal("0"), Decimal("0")
                if volume > 0 and exposure + risk_cash <= equity * cfg.maximum_total_risk_fraction:
                    # Reject gaps that invalidate stop/target geometry.
                    if (
                        candidate.stop_loss < entry < candidate.take_profit
                        if long_trade
                        else candidate.take_profit < entry < candidate.stop_loss
                    ):
                        commission = spec.round_trip_commission_per_lot * volume / 2
                        open_trades[bar.symbol] = _OpenTrade(
                            candidate,
                            volume,
                            risk_cash,
                            entry,
                            bar.timestamp,
                            bar.timeframe,
                            bar.session,
                            bar.regime,
                            commission,
                        )
                        exposure += risk_cash
                        entry_trade = open_trades[bar.symbol]
                        entry_bar_exit = _check_exit(entry_trade, bar, spec, cfg)
                        if entry_bar_exit is not None:
                            completed.append(entry_bar_exit)
                            equity += entry_bar_exit.pnl
                            peak_equity = max(peak_equity, equity)
                            exposure -= entry_trade.risk_cash
                            del open_trades[bar.symbol]

        history[key].append(bar)
        last_by_key[key] = bar
        snapshot = {item_key: tuple(value) for item_key, value in history.items()}
        for proposal in strategy(bar.timestamp, snapshot):
            if proposal.generated_at > bar.timestamp or proposal.expires_at <= bar.timestamp:
                continue
            if proposal.symbol not in instrument_specs:
                continue
            proposal_key = (proposal.symbol, bar.timeframe)
            if (
                proposal_key == key
                and proposal.symbol not in open_trades
                and proposal.signal_id not in seen_signal_ids
            ):
                pending[proposal_key] = proposal
                seen_signal_ids.add(proposal.signal_id)

    # Mark remaining positions to the final close with configured slippage.
    for symbol, active in tuple(open_trades.items()):
        final_bar = last_by_key.get((symbol, active.timeframe))
        if final_bar is None:
            continue
        spec = instrument_specs[symbol]
        long_trade = active.proposal.direction.value == "bullish"
        slip = cfg.slippage_ticks * spec.tick_size
        exit_price = Decimal(str(final_bar.candle.close)) + (
            -slip if long_trade else final_bar.spread + slip
        )
        pnl = _pnl(active, exit_price, spec)
        completed.append(_trade(active, final_bar.timestamp, exit_price, pnl, "end_of_data"))
        equity += pnl

    completed.sort(key=lambda trade: (trade.exit_time, trade.entry_time, trade.signal_id))
    metrics = calculate_metrics(
        cfg.initial_equity,
        equity,
        completed,
        start_time=ordered[0].timestamp,
        end_time=ordered[-1].timestamp,
    )
    return BacktestResult(
        cfg.initial_equity, equity, data_hasher.hexdigest(), tuple(completed), metrics
    )


def chronological_split(
    bars: Iterable[MarketBar], *, in_sample_end: datetime, out_of_sample_end: datetime
) -> tuple[tuple[MarketBar, ...], tuple[MarketBar, ...], tuple[MarketBar, ...]]:
    """Partition data into non-overlapping, chronological IS/OOS/final sets."""

    if in_sample_end.tzinfo is None or out_of_sample_end.tzinfo is None:
        raise ValueError("split boundaries must be timezone-aware")
    if out_of_sample_end <= in_sample_end:
        raise ValueError("out-of-sample end must follow in-sample end")
    parts: list[list[MarketBar]] = [[], [], []]
    for bar in sorted(bars, key=lambda item: item.timestamp):
        parts[
            0 if bar.timestamp < in_sample_end else 1 if bar.timestamp < out_of_sample_end else 2
        ].append(bar)
    if any(not part for part in parts):
        raise ValueError("each chronological split must contain at least one bar")
    return tuple(tuple(part) for part in parts)  # type: ignore[return-value]


def walk_forward_windows(
    bars: Iterable[MarketBar], *, train_bars: int, test_bars: int, step_bars: int | None = None
) -> tuple[tuple[tuple[MarketBar, ...], tuple[MarketBar, ...]], ...]:
    """Create rolling train/test windows in bar-count units."""

    data = tuple(sorted(bars, key=lambda item: item.timestamp))
    step = step_bars or test_bars
    if min(train_bars, test_bars, step) < 1:
        raise ValueError("walk-forward window sizes must be positive")
    timestamps = tuple(sorted({bar.timestamp for bar in data}))
    windows = tuple(
        (
            tuple(bar for bar in data if bar.timestamp in timestamps[start : start + train_bars]),
            tuple(
                bar
                for bar in data
                if bar.timestamp in timestamps[start + train_bars : start + train_bars + test_bars]
            ),
        )
        for start in range(0, len(timestamps) - train_bars - test_bars + 1, step)
    )
    if not windows:
        raise ValueError("not enough bars for one walk-forward window")
    return windows


def calculate_metrics(
    initial_equity: Decimal,
    ending_equity: Decimal,
    trades: Iterable[BacktestTrade],
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> BacktestMetrics:
    rows = tuple(trades)
    pnls = [trade.pnl for trade in rows]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_profit, gross_loss = sum(wins, Decimal("0")), -sum(losses, Decimal("0"))
    peak, balance, max_dd = initial_equity, initial_equity, Decimal("0")
    for pnl in pnls:
        balance += pnl
        peak = max(peak, balance)
        if peak:
            max_dd = max(max_dd, (peak - balance) / peak)
    returns: list[Decimal] = []
    return_equity = initial_equity
    for pnl in pnls:
        returns.append(pnl / return_equity if return_equity else Decimal("0"))
        return_equity += pnl
    mean = sum(returns, Decimal("0")) / len(returns) if returns else Decimal("0")
    variance = (
        sum(((item - mean) ** 2 for item in returns), Decimal("0")) / len(returns)
        if returns
        else Decimal("0")
    )
    downside = [item for item in returns if item < 0]
    downside_dev = (
        (sum((item**2 for item in downside), Decimal("0")) / len(downside)).sqrt()
        if downside
        else Decimal("0")
    )
    std_dev = variance.sqrt() if variance else Decimal("0")
    by_symbol: dict[str, dict[str, Decimal | int]] = {}
    by_strategy: dict[str, dict[str, Decimal | int]] = {}
    by_session: dict[str, dict[str, Decimal | int]] = {}
    by_regime: dict[str, dict[str, Decimal | int]] = {}
    monthly: dict[str, Decimal] = {}
    for trade in rows:
        for bucket, key in (
            (by_symbol, trade.symbol),
            (by_strategy, trade.strategy_key),
            (by_session, trade.session),
            (by_regime, trade.regime),
        ):
            item = bucket.setdefault(key, {"trades": 0, "pnl": Decimal("0")})
            item["trades"] = int(item["trades"]) + 1
            item["pnl"] = Decimal(item["pnl"]) + trade.pnl
        month = trade.exit_time.astimezone(UTC).strftime("%Y-%m")
        monthly[month] = monthly.get(month, Decimal("0")) + trade.pnl
    streak = current = 0
    for pnl in pnls:
        current = current + 1 if pnl < 0 else 0
        streak = max(streak, current)
    begin = start_time or (rows[0].entry_time if rows else None)
    finish = end_time or (rows[-1].exit_time if rows else None)
    years = (
        Decimal(str((finish - begin).total_seconds())) / Decimal("31557600")
        if begin is not None and finish is not None and finish > begin
        else Decimal("0")
    )
    cagr = (
        ((ending_equity / initial_equity).ln() / years).exp() - Decimal("1")
        if years >= 1 and ending_equity > 0
        else None
    )
    annualization = (Decimal(len(rows)) / years).sqrt() if years > 0 and rows else Decimal("0")
    return BacktestMetrics(
        net_return=(ending_equity - initial_equity) / initial_equity,
        cagr=cagr,
        maximum_drawdown=max_dd,
        profit_factor=gross_profit / gross_loss if gross_loss else None,
        expectancy=sum(pnls, Decimal("0")) / len(pnls) if pnls else Decimal("0"),
        average_r=sum((item.r_multiple for item in rows), Decimal("0")) / len(rows)
        if rows
        else Decimal("0"),
        win_rate=Decimal(len(wins)) / len(rows) if rows else Decimal("0"),
        trade_count=len(rows),
        sharpe=mean / std_dev * annualization if std_dev else Decimal("0"),
        sortino=mean / downside_dev * annualization if downside_dev else Decimal("0"),
        recovery_factor=(ending_equity - initial_equity) / (initial_equity * max_dd)
        if max_dd
        else None,
        maximum_consecutive_losses=streak,
        monthly_pnl=monthly,
        by_symbol=by_symbol,
        by_strategy=by_strategy,
        by_session=by_session,
        by_regime=by_regime,
    )


def persist_backtest(
    session: Session,
    result: BacktestResult,
    *,
    strategy_version_id: str,
    strategy_config_id: str,
    code_revision: str,
    split_definition: dict[str, str],
    symbol_id_by_canonical: dict[str, str],
) -> Backtest:
    """Persist one completed replay and its trades in the caller transaction."""

    if len(code_revision) > 64 or not code_revision.strip():
        raise ValueError("code_revision must contain 1 to 64 characters")
    metrics = _json_safe(asdict(result.metrics))
    record = Backtest(
        strategy_version_id=strategy_version_id,
        strategy_config_id=strategy_config_id,
        data_hash=result.data_hash,
        code_revision=code_revision,
        split_definition=split_definition,
        metrics=metrics,
        status="completed",
    )
    session.add(record)
    session.flush()
    for trade in result.trades:
        if trade.symbol not in symbol_id_by_canonical:
            raise ValueError(f"database symbol id missing for {trade.symbol}")
        session.add(
            BacktestTradeRecord(
                backtest_id=record.id,
                signal_key=trade.signal_id,
                symbol_id=symbol_id_by_canonical[trade.symbol],
                direction=trade.direction,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                pnl=trade.pnl,
                evidence={
                    "strategy_key": trade.strategy_key,
                    "session": trade.session,
                    "regime": trade.regime,
                    "entry": str(trade.entry),
                    "exit": str(trade.exit),
                    "stop_loss": str(trade.stop_loss),
                    "take_profit": str(trade.take_profit),
                    "volume": str(trade.volume),
                    "r_multiple": str(trade.r_multiple),
                    "commission": str(trade.commission),
                    "exit_reason": trade.exit_reason,
                },
            )
        )
    session.flush()
    return record


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _check_exit(
    active: _OpenTrade, bar: MarketBar, spec: InstrumentRiskSpec, cfg: BacktestConfig
) -> BacktestTrade | None:
    long_trade = active.proposal.direction.value == "bullish"
    slip = cfg.slippage_ticks * spec.tick_size
    stop = active.proposal.stop_loss
    target = active.proposal.take_profit
    if long_trade:
        low, high, open_price = map(
            Decimal, map(str, (bar.candle.low, bar.candle.high, bar.candle.open))
        )
        stop_hit, target_hit = low <= stop, high >= target
        exit_price, reason = (
            (min(stop, open_price), "stop_loss")
            if stop_hit
            else (max(target, open_price), "take_profit")
            if target_hit
            else (Decimal("0"), "")
        )
        exit_price -= slip
    else:
        low, high, open_price = map(
            Decimal, map(str, (bar.candle.low, bar.candle.high, bar.candle.open))
        )
        stop_hit, target_hit = high + bar.spread >= stop, low + bar.spread <= target
        exit_price, reason = (
            (max(stop, open_price + bar.spread), "stop_loss")
            if stop_hit
            else (min(target, open_price + bar.spread), "take_profit")
            if target_hit
            else (Decimal("0"), "")
        )
        exit_price += slip
    if not reason:
        return None
    pnl = _pnl(active, exit_price, spec)
    return _trade(active, bar.timestamp, exit_price, pnl, reason)


def _pnl(active: _OpenTrade, exit_price: Decimal, spec: InstrumentRiskSpec) -> Decimal:
    sign = Decimal("1") if active.proposal.direction.value == "bullish" else Decimal("-1")
    gross = (
        (exit_price - active.entry)
        * sign
        / spec.tick_size
        * spec.tick_value_per_lot
        * active.volume
    )
    return gross - active.commission - spec.round_trip_commission_per_lot * active.volume / 2


def _trade(
    active: _OpenTrade, exit_time: datetime, exit_price: Decimal, pnl: Decimal, reason: str
) -> BacktestTrade:
    r = pnl / active.risk_cash if active.risk_cash else Decimal("0")
    proposal = active.proposal
    return BacktestTrade(
        proposal.signal_id,
        proposal.symbol,
        proposal.strategy_key,
        active.session,
        active.regime,
        proposal.direction.value,
        active.entry_time,
        exit_time,
        active.entry,
        exit_price,
        proposal.stop_loss,
        proposal.take_profit,
        active.volume,
        pnl,
        r,
        active.commission * 2,
        reason,
    )
