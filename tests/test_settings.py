import unittest
from unittest.mock import patch

from pydantic import ValidationError

from trading_platform.settings import Settings, load_settings


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

    def test_settings_load_toml_then_environment_without_unknown_config_key(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "TRADING_PLATFORM_CONFIG": "config/defaults.toml",
                "TRADING_PLATFORM_MAX_OPEN_POSITIONS": "7",
                "TRADING_PLATFORM_UNRELATED": "ignored",
            },
            clear=True,
        ):
            settings = load_settings()
        self.assertEqual(settings.app_name, "Quant MT5 Platform")
        self.assertEqual(settings.mode, "paper")
        self.assertEqual(settings.max_open_positions, 7)


if __name__ == "__main__":
    unittest.main()
