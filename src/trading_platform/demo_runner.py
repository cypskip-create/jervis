"""Read-only MT5 demo market observer and gold strategy monitor.

This process intentionally has no order-routing path. It polls completed bars,
records the strategy's observable state, and prints proposal candidates only.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal

from dotenv import load_dotenv
from sqlalchemy import select

from .database import make_engine, session_factory
from .domain import StrategyDecision
from .market_structure import (
    Direction,
    PivotKind,
    Regime,
    classify_regime,
    confirmed_pivots,
)
from .models import MarketState, Symbol
from .mt5_adapter import MT5DemoAdapter, config_from_environment
from .settings import load_settings
from .strategies.engines import GoldStrategy, StrategyConfig, StrategyFrame

LOG = logging.getLogger("jervis.demo_observer")
_LAST_DECISION: dict[str, tuple[datetime, StrategyDecision]] = {}


def _ema(values: list[float], period: int) -> float:
    alpha = 2.0 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def analyze_gold(
    adapter: MT5DemoAdapter, canonical: str, broker: str, strategy: GoldStrategy
) -> dict[str, object]:
    quote = adapter.get_quote(broker)
    htf = adapter.get_bars(broker, "M15", limit=250)
    ltf = adapter.get_bars(broker, "M5", limit=250)
    if len(htf) < 30 or len(ltf) < 30:
        raise RuntimeError("MT5 returned fewer than 30 completed bars for M5 or M15")
    htf_candles = [bar.candle for bar in htf]
    ltf_candles = [bar.candle for bar in ltf]
    regime = classify_regime(htf_candles)
    closes = [c.close for c in htf_candles]
    fast = _ema(closes[-100:], 9)
    slow = _ema(closes[-100:], 21)
    previous_slow = _ema(closes[-101:-1], 21)
    direction: Direction | None = None
    if regime.regime == Regime.TRENDING:
        if fast > slow and slow > previous_slow:
            direction = Direction.BULLISH
        elif fast < slow and slow < previous_slow:
            direction = Direction.BEARISH

    htf_pivots = confirmed_pivots(htf_candles, left=2, right=2)
    ltf_pivots = confirmed_pivots(ltf_candles, left=2, right=2)
    supports = tuple(p.price for p in htf_pivots if p.kind == PivotKind.LOW)[-12:]
    resistances = tuple(p.price for p in htf_pivots if p.kind == PivotKind.HIGH)[-12:]
    candle = ltf_candles[-1]
    long_stops = [p for p in htf_pivots if p.kind == PivotKind.LOW and p.price < candle.close]
    short_stops = [p for p in htf_pivots if p.kind == PivotKind.HIGH and p.price > candle.close]
    long_targets = [p for p in htf_pivots if p.kind == PivotKind.HIGH and p.price > candle.close]
    short_targets = [p for p in htf_pivots if p.kind == PivotKind.LOW and p.price < candle.close]
    confirm_kind = PivotKind.HIGH if direction == Direction.BULLISH else PivotKind.LOW
    confirmation = [p for p in ltf_pivots if p.kind == confirm_kind and p.price != candle.close]
    stop = (
        max(long_stops, key=lambda p: p.price).price
        if direction == Direction.BULLISH and long_stops
        else min(short_stops, key=lambda p: p.price).price
        if direction == Direction.BEARISH and short_stops
        else None
    )
    target = (
        min(long_targets, key=lambda p: p.price).price
        if direction == Direction.BULLISH and long_targets
        else max(short_targets, key=lambda p: p.price).price
        if direction == Direction.BEARISH and short_targets
        else None
    )
    frame = StrategyFrame(
        symbol=canonical,
        asset_class="gold",
        timestamp=ltf[-1].opened_at,
        bar_index=int(ltf[-1].opened_at.timestamp() // 300),
        timeframe="M5",
        candle=candle,
        previous_candle=ltf_candles[-2],
        context_direction=direction,
        ema_fast=fast,
        ema_slow=slow,
        ema_slope=slow - previous_slow,
        support_levels=supports,
        resistance_levels=resistances,
        confirmation_level=confirmation[-1].price if confirmation else None,
        structural_invalidation=stop,
        target_level=target,
    )
    previous = _LAST_DECISION.get(canonical)
    if previous is not None and previous[0] == frame.timestamp:
        decision = previous[1]
    else:
        decision = strategy.analyze(frame)
        _LAST_DECISION[canonical] = (frame.timestamp, decision)
    proposal = decision.proposal
    return {
        "state": decision.state,
        "reasons": list(decision.reasons),
        "evidence": decision.evidence,
        "regime": regime.regime.value,
        "context": direction.value if direction else "neutral",
        "quote_bid": quote.bid,
        "quote_ask": quote.ask,
        "quote_at": quote.observed_at.isoformat(),
        "bar_at": ltf[-1].opened_at.isoformat(),
        "close": candle.close,
        "proposal_candidate": (
            {
                "direction": proposal.direction.value,
                "entry": str(proposal.entry),
                "stop_loss": str(proposal.stop_loss),
                "take_profit": str(proposal.take_profit),
                "reward_risk": str(proposal.reward_risk),
                "expires_at": proposal.expires_at.isoformat(),
            }
            if proposal
            else None
        ),
        "execution": "disabled_observe_only",
    }


def run(*, once: bool = False, poll_seconds: float = 5.0) -> None:
    load_dotenv(override=False)
    settings = load_settings()
    if settings.global_enabled:
        raise RuntimeError("observer refuses to start while dashboard global execution is enabled")
    adapter = MT5DemoAdapter(config_from_environment())
    factory = session_factory(make_engine(settings))
    strategy = GoldStrategy(StrategyConfig(minimum_rr=Decimal("1.5"), zone_tolerance=0.5))
    try:
        account = adapter.connect()
        with factory() as session:
            symbol = session.scalar(select(Symbol).where(Symbol.canonical == "XAUUSD"))
            if symbol is None:
                raise RuntimeError(
                    "XAUUSD canonical symbol is missing; run seed_reference_data first"
                )
            from .models import Account, SymbolMapping

            account_row = session.scalar(
                select(Account).where(
                    Account.server == account.server,
                    Account.account_ref == str(account.login),
                    Account.mode == "demo",
                )
            )
            if account_row is None:
                raise RuntimeError("connected demo account is not registered in the local database")
            mapping = session.scalar(
                select(SymbolMapping).where(
                    SymbolMapping.account_id == account_row.id,
                    SymbolMapping.symbol_id == symbol.id,
                    SymbolMapping.enabled.is_(True),
                    SymbolMapping.valid_until.is_(None),
                )
            )
            if mapping is None:
                raise RuntimeError("no active XAUUSD broker symbol mapping exists for this demo")
            symbol_id, broker = symbol.id, mapping.broker_symbol
        LOG.info(
            "Connected read-only observer to demo server %s; symbol %s -> %s",
            account.server,
            "XAUUSD",
            broker,
        )
        previous_bar: str | None = None
        while True:
            observed_at = datetime.now(UTC)
            try:
                result = analyze_gold(adapter, "XAUUSD", broker, strategy)
            except Exception:
                LOG.exception("Observer cycle failed; execution remains disabled")
                if once:
                    raise
                time.sleep(poll_seconds)
                continue
            bar_at = str(result["bar_at"])
            if bar_at != previous_bar:
                with factory.begin() as session:
                    session.add(
                        MarketState(
                            symbol_id=symbol_id,
                            timeframe="M5",
                            state=str(result["state"]),
                            evidence=result,
                            observed_at=observed_at,
                        )
                    )
                LOG.info(
                    "M5 %s regime=%s context=%s state=%s candidate=%s",
                    bar_at,
                    result["regime"],
                    result["context"],
                    result["state"],
                    result["proposal_candidate"],
                )
                previous_bar = bar_at
            if once:
                break
            time.sleep(poll_seconds)
    finally:
        adapter.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Observe XAUUSD on the allow-listed MT5 demo account"
    )
    parser.add_argument("--once", action="store_true", help="read one market snapshot and exit")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if args.poll_seconds < 1 or args.poll_seconds > 300:
        parser.error("--poll-seconds must be between 1 and 300")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(once=args.once, poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    main()
