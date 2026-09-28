import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.domain import StrategyDecision
from trading_platform.market_structure import Candle, Direction, LiquiditySide
from trading_platform.strategies import (
    ConfirmationMode,
    ForexContinuationStrategy,
    ForexLiquidityStrategy,
    GoldStrategy,
    LiquidityLevel,
    NasdaqStrategy,
    StrategyConfig,
    StrategyFrame,
    StructuralEvent,
    candle_confirmation,
)

NOW = datetime.now(UTC)


def frame(index: int, candle: Candle, **changes: object) -> StrategyFrame:
    values: dict[str, object] = {
        "symbol": "NAS100",
        "asset_class": "index",
        "timestamp": NOW + timedelta(minutes=index),
        "bar_index": index,
        "timeframe": "M5",
        "candle": candle,
        "context_direction": Direction.BULLISH,
        "target_level": 112.0,
    }
    values.update(changes)
    return StrategyFrame(**values)  # type: ignore[arg-type]


def config(**changes: object) -> StrategyConfig:
    values: dict[str, object] = {
        "minimum_rr": Decimal("2"),
        "maximum_setup_bars": 20,
        "zone_tolerance": 0.5,
        "sweep_penetration": 0.05,
        "stop_buffer": 0.05,
        "retest_tolerance": 0.15,
        "confirmation_mode": ConfirmationMode.DISPLACEMENT,
    }
    values.update(changes)
    return StrategyConfig(**values)  # type: ignore[arg-type]


def bullish_bos(index: int, when: datetime, timeframe: str = "M5") -> StructuralEvent:
    return StructuralEvent("bos", Direction.BULLISH, 102.0, index, when, timeframe)


def bullish_choch(index: int, when: datetime, timeframe: str = "M5") -> StructuralEvent:
    return StructuralEvent("choch", Direction.BULLISH, 101.0, index, when, timeframe)


class StrategyTests(unittest.TestCase):
    def test_gold_requires_aligned_context_pullback_and_confirmed_break(self) -> None:
        strategy = GoldStrategy(config())
        location = frame(
            1,
            Candle(100.5, 101, 99.8, 100.2),
            symbol="XAUUSD",
            asset_class="gold",
            ema_fast=101.0,
            ema_slow=100.0,
            ema_slope=0.1,
            support_levels=(100.0,),
            structural_invalidation=99.0,
            confirmation_level=101.0,
            target_level=110.0,
        )
        self.assertEqual(strategy.analyze(location).state, "WAIT_CONFIRMATION")
        decision = strategy.analyze(
            frame(
                2,
                Candle(101.2, 102.5, 101.1, 102.4),
                symbol="XAUUSD",
                asset_class="gold",
                ema_fast=101.0,
                ema_slow=100.0,
                ema_slope=0.1,
                support_levels=(100.0,),
                structural_invalidation=99.0,
                confirmation_level=101.0,
                target_level=110.0,
            )
        )
        self.assertIsNotNone(decision.proposal)
        self.assertGreaterEqual(decision.proposal.reward_risk, Decimal("2"))

    def test_gold_neutral_or_unaligned_context_blocks_setup(self) -> None:
        strategy = GoldStrategy(config())
        decision = strategy.analyze(
            frame(
                1,
                Candle(100, 101, 99, 100.2),
                symbol="XAUUSD",
                asset_class="gold",
                context_direction=None,
                ema_fast=101,
                ema_slow=100,
                ema_slope=0.1,
                support_levels=(100,),
            )
        )
        self.assertIsNone(decision.proposal)
        self.assertEqual(decision.state, "CONTEXT")

    def test_nasdaq_full_sequence_emits_only_after_confirmation(self) -> None:
        strategy = NasdaqStrategy(config())
        swept = strategy.analyze(
            frame(
                100,
                Candle(100.5, 101, 99.8, 100.2),
                liquidity_levels=(LiquidityLevel(100, LiquiditySide.SELL_SIDE, "equal lows"),),
            )
        )
        self.assertEqual(swept.state, "WAIT_CHOCH")
        self.assertIsNone(swept.proposal)

        choch_time = NOW + timedelta(minutes=102)
        choch = strategy.analyze(
            frame(102, Candle(100.2, 101.5, 100, 101.3), choch=bullish_choch(102, choch_time))
        )
        self.assertEqual(choch.state, "WAIT_BOS")

        bos_time = NOW + timedelta(minutes=103)
        bos = strategy.analyze(
            frame(103, Candle(101.4, 102.5, 101.2, 102.3), bos=bullish_bos(103, bos_time))
        )
        self.assertEqual(bos.state, "WAIT_RETEST")

        retest = strategy.analyze(frame(104, Candle(102.1, 102.3, 101.9, 102.05)))
        self.assertEqual(retest.state, "WAIT_CONFIRMATION")
        self.assertIsNone(retest.proposal)

        confirmed = strategy.analyze(
            frame(
                105,
                Candle(102.1, 103.4, 102.0, 103.2),
                previous_candle=Candle(102.5, 102.7, 101.8, 102.0),
                target_level=112,
            )
        )
        self.assertEqual(confirmed.state, "PROPOSED")
        self.assertIsNotNone(confirmed.proposal)
        self.assertEqual(confirmed.proposal.strategy_key, "nasdaq_sweep")

    def test_nasdaq_does_not_chase_missed_retest_and_expires(self) -> None:
        strategy = NasdaqStrategy(config(maximum_setup_bars=3))
        strategy.analyze(
            frame(
                10,
                Candle(100.5, 101, 99.8, 100.2),
                liquidity_levels=(LiquidityLevel(100, LiquiditySide.SELL_SIDE, "PDL"),),
            )
        )
        strategy.analyze(
            frame(
                11,
                Candle(100, 101.3, 99.9, 101.2),
                choch=bullish_choch(11, NOW + timedelta(minutes=10, seconds=30)),
            )
        )
        strategy.analyze(
            frame(
                12,
                Candle(101.3, 102.4, 101.2, 102.3),
                bos=bullish_bos(12, NOW + timedelta(minutes=11, seconds=30)),
            )
        )
        no_retest = strategy.analyze(frame(13, Candle(104, 105, 103.8, 104.8)))
        self.assertIn("will not be chased", no_retest.reasons[0])
        expired = strategy.analyze(frame(14, Candle(104, 105, 103.8, 104.8)))
        self.assertEqual(expired.state, "EXPIRED")

    def test_forex_a_uses_m15_choch_and_m5_bos(self) -> None:
        strategy = ForexLiquidityStrategy(config())
        strategy.analyze(
            frame(
                100,
                Candle(100.5, 101, 99.8, 100.2),
                symbol="EURUSD",
                asset_class="forex",
                liquidity_levels=(LiquidityLevel(100, LiquiditySide.SELL_SIDE, "Asian low"),),
            )
        )
        m15_time = NOW + timedelta(minutes=101)
        self.assertEqual(
            strategy.analyze(
                frame(
                    101, Candle(100.2, 101.4, 100.1, 101.2), choch=bullish_choch(8, m15_time, "M15")
                )
            ).state,
            "WAIT_BOS",
        )
        m5_time = NOW + timedelta(minutes=102)
        self.assertEqual(
            strategy.analyze(
                frame(102, Candle(101.3, 102.5, 101.2, 102.3), bos=bullish_bos(102, m5_time))
            ).state,
            "WAIT_RETEST",
        )

    def test_forex_continuation_completes_without_a_liquidity_sweep(self) -> None:
        strategy = ForexContinuationStrategy(config())
        start = frame(
            1,
            Candle(100.3, 100.5, 99.9, 100.2),
            symbol="EURUSD",
            asset_class="forex",
            support_levels=(100,),
        )
        self.assertEqual(strategy.analyze(start).state, "WAIT_COMPRESSION_OR_REJECTION")
        compression = frame(
            2,
            Candle(100.1, 100.3, 100, 100.2),
            symbol="EURUSD",
            asset_class="forex",
            support_levels=(100,),
            compression_atr=0.5,
        )
        self.assertEqual(strategy.analyze(compression).state, "WAIT_BOS")
        bos = strategy.analyze(
            frame(
                3,
                Candle(100.4, 101.5, 100.3, 101.3),
                symbol="EURUSD",
                asset_class="forex",
                support_levels=(100,),
                bos=bullish_bos(3, NOW + timedelta(minutes=3)),
            )
        )
        self.assertEqual(bos.state, "WAIT_RETEST")
        self.assertIsNone(bos.proposal)
        retest = strategy.analyze(
            frame(
                4, Candle(101.9, 102.2, 101.8, 102.05), symbol="EURUSD",
                asset_class="forex", target_level=112,
            )
        )
        self.assertEqual(retest.state, "WAIT_CONFIRMATION")
        confirmed = strategy.analyze(
            frame(
                5, Candle(102.1, 103.5, 102.0, 103.2),
                previous_candle=Candle(102.2, 102.3, 101.8, 102.0),
                symbol="EURUSD", asset_class="forex", target_level=112,
                structural_invalidation=99,
            )
        )
        self.assertEqual(confirmed.state, "PROPOSED")
        self.assertEqual(confirmed.proposal.strategy_key, "fx_trend_continuation")

    def test_confirmation_models_are_explicit_and_strategy_only_proposes(self) -> None:
        previous = Candle(10, 10.5, 9.5, 9.7)
        bullish_engulf = Candle(9.6, 10.7, 9.5, 10.6)
        self.assertTrue(candle_confirmation(
            bullish_engulf, previous, Direction.BULLISH,
            config(confirmation_mode=ConfirmationMode.ENGULFING),
        ))
        self.assertTrue(candle_confirmation(
            Candle(10, 10.8, 9.8, 10.7), previous, Direction.BULLISH,
            config(confirmation_mode=ConfirmationMode.DISPLACEMENT),
        ))
        self.assertFalse(candle_confirmation(
            Candle(10, 10.4, 9.8, 10.2), previous, Direction.BULLISH,
            config(confirmation_mode=ConfirmationMode.DISPLACEMENT),
        ))
        self.assertTrue(candle_confirmation(
            Candle(10.4, 10.5, 9.3, 10.45), previous, Direction.BULLISH,
            config(confirmation_mode=ConfirmationMode.REJECTION),
        ))
        strategy = ForexContinuationStrategy(config(confirmation_mode=ConfirmationMode.ENGULFING))
        self.assertIsInstance(strategy.analyze(frame(2, previous)), StrategyDecision)


if __name__ == "__main__":
    unittest.main()
