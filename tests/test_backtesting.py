import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.backtesting import (
    MarketBar,
    chronological_split,
    run_backtest,
    walk_forward_windows,
)
from trading_platform.domain import TradeProposal
from trading_platform.market_structure import Candle, Direction
from trading_platform.risk import InstrumentRiskSpec

START = datetime(2025, 1, 1, tzinfo=UTC)
SPEC = InstrumentRiskSpec(
    tick_size=Decimal("0.01"),
    tick_value_per_lot=Decimal("1"),
    minimum_volume=Decimal("0.01"),
    maximum_volume=Decimal("5"),
    volume_step=Decimal("0.01"),
    round_trip_commission_per_lot=Decimal("2"),
)


def bar(index: int, *, high: float = 100.5, low: float = 99.8, open_: float = 100) -> MarketBar:
    return MarketBar(
        "EURUSD",
        "M5",
        START + timedelta(minutes=5 * index),
        index,
        Candle(open_, high, low, open_),
        Decimal("0.02"),
        "london",
        "trending",
    )


def proposal() -> TradeProposal:
    return TradeProposal(
        signal_id="test-proposal-1",
        symbol="EURUSD",
        asset_class="forex",
        strategy_key="test",
        strategy_version="1.0",
        setup_id="test",
        direction=Direction.BULLISH,
        entry=Decimal("100"),
        stop_loss=Decimal("99"),
        take_profit=Decimal("102"),
        generated_at=START,
        expires_at=START + timedelta(minutes=30),
        reasons=("fixture",),
    )


class BacktestTests(unittest.TestCase):
    def test_next_bar_entry_costs_and_hash_are_deterministic(self) -> None:
        data = (bar(0), bar(1, high=100.7), bar(2, high=102.2))
        snapshots: list[int] = []

        def strategy(now, history):
            visible = history[("EURUSD", "M5")]
            snapshots.append(len(visible))
            return (proposal(),) if len(visible) == 1 else ()

        first = run_backtest(data, strategy=strategy, instrument_specs={"EURUSD": SPEC})
        second = run_backtest(data, strategy=strategy, instrument_specs={"EURUSD": SPEC})
        self.assertEqual(first.data_hash, second.data_hash)
        self.assertEqual(first.trades[0].entry_time, data[1].timestamp)
        self.assertEqual(first.trades[0].exit_reason, "take_profit")
        self.assertGreater(first.trades[0].commission, 0)
        self.assertEqual(first.metrics.trade_count, 1)
        self.assertIn("london", first.metrics.by_session)
        self.assertIn("trending", first.metrics.by_regime)
        self.assertIsNone(first.metrics.cagr)
        self.assertEqual(snapshots[0], 1)

    def test_same_bar_stop_and_target_select_stop(self) -> None:
        result = run_backtest(
            (bar(0), bar(1, high=102.5, low=98.5)),
            strategy=lambda now, history: (
                (proposal(),) if len(history[("EURUSD", "M5")]) == 1 else ()
            ),
            instrument_specs={"EURUSD": SPEC},
        )
        self.assertEqual(result.trades[0].exit_reason, "stop_loss")

    def test_chronological_and_walk_forward_splits_keep_synchronous_events_together(self) -> None:
        bars = tuple(bar(i) for i in range(8))
        in_sample, oos, final = chronological_split(
            bars,
            in_sample_end=START + timedelta(minutes=20),
            out_of_sample_end=START + timedelta(minutes=30),
        )
        self.assertEqual((len(in_sample), len(oos), len(final)), (4, 2, 2))
        windows = walk_forward_windows(bars, train_bars=4, test_bars=2)
        self.assertEqual(len(windows), 2)
        self.assertLess(windows[0][0][-1].timestamp, windows[0][1][0].timestamp)


if __name__ == "__main__":
    unittest.main()
