import unittest

from trading_platform.market_structure import (
    Candle,
    Direction,
    LiquiditySide,
    PivotKind,
    close_breaks_structure,
    confirmed_pivots,
    detect_liquidity_sweep,
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
        self.assertFalse(
            close_breaks_structure(wick_only, level=10, direction=Direction.BULLISH)
        )
        self.assertTrue(is_choch(
            close_break,
            level=10,
            prior_direction=Direction.BEARISH,
        ))

    def test_sweep_requires_wick_breach_and_reclaim(self) -> None:
        self.assertTrue(
            detect_liquidity_sweep(
                candle(11, 12, 9, 10.5), level=10, side=LiquiditySide.SELL_SIDE
            )
        )
        self.assertFalse(
            detect_liquidity_sweep(
                candle(11, 12, 9, 9.5), level=10, side=LiquiditySide.SELL_SIDE
            )
        )

    def test_invalid_ohlc_and_parameters_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Candle(1, 0, 2, 1)
        with self.assertRaises(ValueError):
            confirmed_pivots([], left=0)


if __name__ == "__main__":
    unittest.main()
