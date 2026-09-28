import unittest

from trading_platform.market_structure import (
    Candle,
    Direction,
    LiquiditySide,
    Pivot,
    PivotKind,
    Regime,
    RegimeParameters,
    classify_regime,
    close_breaks_structure,
    cluster_pivots,
    confirmed_pivots,
    detect_liquidity_sweep,
    detect_retest,
    is_choch,
)


def candle(open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(open_, high, low, close)


class MarketStructureTests(unittest.TestCase):
    def test_pivot_is_unavailable_until_right_confirmation_bars_close(self) -> None:
        bars = [
            candle(4, 5, 3, 4),
            candle(5, 8, 4, 6),
            candle(6, 12, 5, 8),
            candle(8, 9, 6, 7),
            candle(7, 8, 5, 6),
        ]
        self.assertEqual(confirmed_pivots(bars, left=2, right=2, as_of_index=3), [])
        pivots = confirmed_pivots(bars, left=2, right=2, as_of_index=4)
        self.assertEqual(len(pivots), 1)
        self.assertEqual(pivots[0].kind, PivotKind.HIGH)
        self.assertEqual((pivots[0].index, pivots[0].confirmation_index), (2, 4))

    def test_equal_extreme_does_not_create_pivot(self) -> None:
        bars = [
            candle(4, 5, 3, 4),
            candle(5, 8, 4, 6),
            candle(6, 10, 5, 8),
            candle(8, 10, 6, 7),
            candle(7, 8, 5, 6),
        ]
        pivots = confirmed_pivots(bars, left=2, right=2)
        self.assertFalse(any(pivot.kind == PivotKind.HIGH for pivot in pivots))

    def test_bos_and_choch_require_close_beyond_level(self) -> None:
        wick_only = candle(9, 12, 8, 9.5)
        close_break = candle(9, 12, 8, 10.5)
        self.assertFalse(close_breaks_structure(wick_only, level=10, direction=Direction.BULLISH))
        self.assertTrue(
            is_choch(
                close_break,
                level=10,
                prior_direction=Direction.BEARISH,
            )
        )

    def test_sweep_requires_wick_breach_and_reclaim(self) -> None:
        self.assertTrue(
            detect_liquidity_sweep(candle(11, 12, 9, 10.5), level=10, side=LiquiditySide.SELL_SIDE)
        )
        self.assertFalse(
            detect_liquidity_sweep(candle(11, 12, 9, 9.5), level=10, side=LiquiditySide.SELL_SIDE)
        )

    def test_invalid_ohlc_and_parameters_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Candle(1, 0, 2, 1)
        with self.assertRaises(ValueError):
            confirmed_pivots([], left=0)

    def test_level_clusters_require_reactions_and_do_not_chain(self) -> None:
        pivots = [
            Pivot(PivotKind.HIGH, 1, 3, 10.00),
            Pivot(PivotKind.HIGH, 5, 7, 10.08),
            Pivot(PivotKind.HIGH, 9, 11, 10.16),
        ]
        clusters = cluster_pivots(pivots, tolerance=0.1, minimum_touches=2)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].touches, 2)
        self.assertTrue(clusters[0].is_equal_level)
        self.assertEqual(clusters[0].last_confirmation_index, 7)

    def test_retest_requires_post_bos_window_and_level_contact(self) -> None:
        arguments = dict(
            level=10,
            direction=Direction.BULLISH,
            tolerance=0.2,
            bos_index=5,
            expiry_bars=3,
        )
        self.assertFalse(detect_retest(candle(10, 11, 9.9, 10.5), current_index=5, **arguments))
        self.assertTrue(detect_retest(candle(10.4, 10.6, 9.95, 10.1), current_index=7, **arguments))
        self.assertFalse(
            detect_retest(candle(10.4, 10.6, 9.95, 10.1), current_index=9, **arguments)
        )

    def test_regime_rules_cover_trend_range_and_abnormal_volatility(self) -> None:
        parameters = RegimeParameters(
            atr_window=5,
            fast_ema=3,
            slow_ema=6,
            min_trend_separation=0.001,
            min_trend_persistence=0.6,
            max_range_persistence=0.25,
            max_range_separation=0.0005,
            abnormal_normalized_atr=0.05,
        )
        trend = [
            candle(100 + index * 0.5, 101 + index * 0.5, 99.8 + index * 0.5, 100.5 + index * 0.5)
            for index in range(20)
        ]
        self.assertEqual(classify_regime(trend, parameters=parameters).regime, Regime.TRENDING)

        range_bars = [
            candle(100, 100.2, 99.8, 100 + (0.05 if index % 2 else 0)) for index in range(20)
        ]
        self.assertEqual(classify_regime(range_bars, parameters=parameters).regime, Regime.RANGING)

        volatile = [candle(100, 104, 96, 100) for _ in range(20)]
        self.assertEqual(
            classify_regime(volatile, parameters=parameters).regime,
            Regime.ABNORMAL_VOLATILITY,
        )

        neutral_parameters = RegimeParameters(
            atr_window=5,
            fast_ema=3,
            slow_ema=6,
            min_trend_separation=0.1,
            min_trend_persistence=0.95,
            max_range_persistence=0.1,
            max_range_separation=0.000001,
            abnormal_normalized_atr=0.05,
        )
        self.assertEqual(
            classify_regime(trend, parameters=neutral_parameters).regime, Regime.NEUTRAL
        )


if __name__ == "__main__":
    unittest.main()
