import unittest

from pydantic import ValidationError

from trading_platform.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_fresh_settings_are_paper_and_stopped(self) -> None:
        settings = Settings()
        self.assertEqual(settings.mode, "paper")
        self.assertFalse(settings.global_enabled)

    def test_live_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "live mode is not implemented"):
            Settings(mode="live")

    def test_unsafe_risk_bounds_are_rejected(self) -> None:
        for field, value in (
            ("risk_per_trade_fraction", 0),
            ("risk_per_trade_fraction", 0.02),
            ("max_open_positions", 0),
            ("max_drawdown_fraction", 0.5),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValidationError):
                    Settings(**{field: value})

    def test_backtest_cannot_be_globally_enabled(self) -> None:
        with self.assertRaisesRegex(ValidationError, "backtest mode"):
            Settings(mode="backtest", global_enabled=True)


if __name__ == "__main__":
    unittest.main()
