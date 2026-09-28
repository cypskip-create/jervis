import unittest
from datetime import UTC, datetime, timedelta

from trading_platform.market_data import MarketBar, Quote, require_fresh_quote
from trading_platform.market_structure import Candle


class MarketDataTests(unittest.TestCase):
    def test_quote_validates_spread_and_awareness(self) -> None:
        now = datetime.now(UTC)
        quote = Quote("EURUSD", 1.1, 1.1002, now, "fixture")
        self.assertAlmostEqual(quote.spread, 0.0002)
        with self.assertRaises(ValueError):
            Quote("EURUSD", 1.1, 1.0, now, "fixture")
        with self.assertRaises(ValueError):
            Quote("EURUSD", 1.1, 1.2, datetime.now(), "fixture")

    def test_quote_freshness_rejects_stale_and_future_values(self) -> None:
        now = datetime.now(UTC)
        quote = Quote("EURUSD", 1.1, 1.1002, now - timedelta(seconds=3), "fixture")
        require_fresh_quote(quote, now=now, max_age=timedelta(seconds=5))
        with self.assertRaisesRegex(ValueError, "stale quote"):
            require_fresh_quote(quote, now=now, max_age=timedelta(seconds=1))

        future = Quote("EURUSD", 1.1, 1.1002, now + timedelta(seconds=1), "fixture")
        with self.assertRaisesRegex(ValueError, "future"):
            require_fresh_quote(future, now=now, max_age=timedelta(seconds=5))

    def test_market_bar_requires_timezone_and_nonnegative_volume(self) -> None:
        with self.assertRaises(ValueError):
            MarketBar("EURUSD", "M5", datetime.now(), Candle(1, 2, 0.5, 1), 10)
        with self.assertRaises(ValueError):
            MarketBar("EURUSD", "M5", datetime.now(UTC), Candle(1, 2, 0.5, 1), -1)


if __name__ == "__main__":
    unittest.main()
