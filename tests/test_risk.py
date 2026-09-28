import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.domain import TradeProposal
from trading_platform.market_structure import Direction
from trading_platform.risk import (
    InstrumentRiskSpec,
    PortfolioState,
    RiskEngine,
    RiskEnvironment,
    RiskLimits,
    calculate_position_size,
    fx_currency_exposure_per_lot,
)

NOW = datetime.now(UTC)
SPEC = InstrumentRiskSpec(
    tick_size=Decimal("0.01"),
    tick_value_per_lot=Decimal("1"),
    minimum_volume=Decimal("0.01"),
    maximum_volume=Decimal("10"),
    volume_step=Decimal("0.01"),
    round_trip_commission_per_lot=Decimal("2"),
    slippage_ticks=Decimal("0.5"),
)


def proposal(**changes: object) -> TradeProposal:
    fields: dict[str, object] = {
        "signal_id": "signal-1",
        "symbol": "EURUSD",
        "asset_class": "forex",
        "strategy_key": "fx_liquidity_pullback",
        "strategy_version": "forex_v1.0.0",
        "setup_id": "fx_liquidity_pullback",
        "direction": Direction.BULLISH,
        "entry": Decimal("100"),
        "stop_loss": Decimal("99"),
        "take_profit": Decimal("102"),
        "generated_at": NOW,
        "expires_at": NOW + timedelta(minutes=1),
        "reasons": ("test setup",),
        "currency_exposure_per_lot": {
            "EUR": Decimal("10000"),
            "USD": Decimal("-10000"),
        },
    }
    fields.update(changes)
    return TradeProposal(**fields)  # type: ignore[arg-type]


def environment(**changes: object) -> RiskEnvironment:
    fields: dict[str, object] = {
        "now": NOW,
        "mode": "paper",
        "global_enabled": True,
        "emergency_stop": False,
        "symbol_enabled": True,
        "strategy_enabled": True,
        "market_open": True,
        "quote_fresh": True,
        "spread": Decimal("0.01"),
        "maximum_spread": Decimal("0.05"),
    }
    fields.update(changes)
    return RiskEnvironment(**fields)  # type: ignore[arg-type]


class RiskEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RiskEngine()
        self.limits = RiskLimits()
        self.portfolio = PortfolioState(equity=Decimal("10000"), peak_equity=Decimal("10000"))

    def test_size_rounds_down_and_includes_commission_and_slippage(self) -> None:
        volume, cash_risk = calculate_position_size(
            equity=Decimal("10000"),
            risk_fraction=Decimal("0.0025"),
            entry=Decimal("100"),
            stop_loss=Decimal("99"),
            spec=SPEC,
        )
        self.assertEqual(volume, Decimal("0.24"))
        self.assertLessEqual(cash_risk, Decimal("25"))

    def test_minimum_volume_over_budget_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "minimum broker volume"):
            calculate_position_size(
                equity=Decimal("100"),
                risk_fraction=Decimal("0.0025"),
                entry=Decimal("100"),
                stop_loss=Decimal("99"),
                spec=SPEC,
            )

    def test_approval_and_each_safety_gate(self) -> None:
        decision = self.engine.evaluate(
            proposal(),
            limits=self.limits,
            portfolio=self.portfolio,
            environment=environment(),
            instrument=SPEC,
        )
        self.assertTrue(decision.approved, decision.reason)
        self.assertEqual(decision.volume, Decimal("0.24"))
        cases = [
            (environment(mode="live"), "mode"),
            (environment(global_enabled=False), "global bot"),
            (environment(emergency_stop=True), "emergency stop"),
            (environment(symbol_enabled=False), "disabled"),
            (environment(strategy_enabled=False), "disabled"),
            (environment(market_open=False), "market closed"),
            (environment(quote_fresh=False), "stale"),
            (environment(spread=Decimal("0.1")), "spread"),
            (environment(duplicate_signal=True), "duplicate"),
        ]
        for env, reason in cases:
            with self.subTest(reason=reason):
                rejected = self.engine.evaluate(
                    proposal(),
                    limits=self.limits,
                    portfolio=self.portfolio,
                    environment=env,
                    instrument=SPEC,
                )
                self.assertFalse(rejected.approved)
                self.assertIn(reason, rejected.reason)

    def test_rr_and_portfolio_limits_reject(self) -> None:
        low_rr = proposal(take_profit=Decimal("101"))
        decision = self.engine.evaluate(
            low_rr,
            limits=self.limits,
            portfolio=self.portfolio,
            environment=environment(),
            instrument=SPEC,
        )
        self.assertIn("reward/risk", decision.reason)

        portfolio = PortfolioState(
            equity=Decimal("10000"),
            open_risk_cash=Decimal("90"),
            symbol_risk_cash={"EURUSD": Decimal("40")},
            peak_equity=Decimal("10000"),
        )
        decision = self.engine.evaluate(
            proposal(),
            limits=self.limits,
            portfolio=portfolio,
            environment=environment(),
            instrument=SPEC,
        )
        self.assertIn("portfolio risk", decision.reason)

    def test_daily_weekly_drawdown_and_currency_exposure_limits(self) -> None:
        states = [
            PortfolioState(equity=Decimal("10000"), daily_loss_cash=Decimal("100")),
            PortfolioState(equity=Decimal("10000"), weekly_loss_cash=Decimal("200")),
            PortfolioState(equity=Decimal("10000"), peak_equity=Decimal("11000")),
            PortfolioState(
                equity=Decimal("10000"), currency_exposure_cash={"EUR": Decimal("2490")}
            ),
        ]
        for portfolio, expected in zip(
            states,
            ("daily loss", "weekly loss", "drawdown", "EUR exposure"),
            strict=True,
        ):
            decision = self.engine.evaluate(
                proposal(),
                limits=self.limits,
                portfolio=portfolio,
                environment=environment(),
                instrument=SPEC,
            )
            self.assertIn(expected, decision.reason)

    def test_fx_exposure_requires_conversions_and_flips_sign(self) -> None:
        exposure = fx_currency_exposure_per_lot(
            canonical_symbol="EURUSD",
            direction=Direction.BULLISH,
            price=Decimal("1.1"),
            contract_size=Decimal("100000"),
            account_conversion={"EUR": Decimal("1.1"), "USD": Decimal("1")},
        )
        self.assertEqual(exposure["EUR"], Decimal("110000.0"))
        self.assertEqual(exposure["USD"], Decimal("-110000.0"))
        with self.assertRaisesRegex(ValueError, "conversion is required"):
            fx_currency_exposure_per_lot(
                canonical_symbol="EURUSD",
                direction=Direction.BULLISH,
                price=Decimal("1.1"),
                contract_size=Decimal("100000"),
                account_conversion={"USD": Decimal("1")},
            )


if __name__ == "__main__":
    unittest.main()
