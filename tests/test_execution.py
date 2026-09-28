import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_platform.domain import ExecutionQuote, TradeProposal
from trading_platform.execution import (
    ExternalPosition,
    PaperExecutionRequest,
    PaperExecutor,
    process_paper_quotes,
    reconcile_positions,
)
from trading_platform.market_structure import Direction
from trading_platform.models import (
    Account,
    Base,
    Order,
    Position,
    RiskReservation,
    Signal,
    Strategy,
    StrategyConfig,
    StrategyVersion,
    Symbol,
    Trade,
)
from trading_platform.risk import InstrumentRiskSpec, RiskDecision


class PaperExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.now = datetime.now(UTC)
        self.account = Account(
            broker="paper", server="local", account_ref="paper-1", currency="USD", mode="paper"
        )
        self.symbol = Symbol(canonical="EURUSD", asset_class="forex", enabled=True)
        strategy = Strategy(key="fx_a", display_name="FX liquidity")
        self.session.add_all([self.account, self.symbol, strategy])
        self.session.flush()
        version = StrategyVersion(strategy_id=strategy.id, version="forex_v1.0.0")
        self.session.add(version)
        self.session.flush()
        config = StrategyConfig(strategy_version_id=version.id, config_hash="b" * 64, parameters={})
        self.session.add(config)
        self.session.flush()
        self.signal_id = str(uuid4())
        self.signal = Signal(
            id=self.signal_id,
            signal_key=self.signal_id,
            account_id=self.account.id,
            symbol_id=self.symbol.id,
            strategy_version_id=version.id,
            strategy_config_id=config.id,
            direction="long",
            decision="approved",
            market_time=self.now,
        )
        self.session.add(self.signal)
        self.session.flush()
        self.reservation = RiskReservation(
            account_id=self.account.id,
            signal_id=self.signal_id,
            amount=Decimal("10.2"),
            state="held",
            expires_at=self.now + timedelta(minutes=1),
        )
        self.session.add(self.reservation)
        self.session.commit()

        self.proposal = TradeProposal(
            signal_id=self.signal_id,
            symbol="EURUSD",
            asset_class="forex",
            strategy_key="fx_a",
            strategy_version="forex_v1.0.0",
            setup_id="fx_liquidity_pullback",
            direction=Direction.BULLISH,
            entry=Decimal("100"),
            stop_loss=Decimal("99"),
            take_profit=Decimal("102"),
            generated_at=self.now,
            expires_at=self.now + timedelta(minutes=1),
            reasons=("test setup",),
        )
        self.spec = InstrumentRiskSpec(
            tick_size=Decimal("0.01"),
            tick_value_per_lot=Decimal("1"),
            minimum_volume=Decimal("0.1"),
            maximum_volume=Decimal("1"),
            volume_step=Decimal("0.1"),
            round_trip_commission_per_lot=Decimal("1"),
        )
        self.decision = RiskDecision(
            True, "approved", Decimal("0.1"), Decimal("10.2"), Decimal("2")
        )
        self.request = self.make_request()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def make_request(self, **changes: object) -> PaperExecutionRequest:
        values: dict[str, object] = {
            "proposal": self.proposal if hasattr(self, "proposal") else None,
            "account_id": self.account.id,
            "symbol_id": self.symbol.id,
            "risk_decision": self.decision if hasattr(self, "decision") else None,
            "environment_mode": "paper",
            "global_enabled": True,
            "emergency_stop": False,
            "symbol_enabled": True,
            "strategy_enabled": True,
            "market_open": True,
            "quote": ExecutionQuote(Decimal("100"), Decimal("100.01"), self.now),
            "quote_max_age": timedelta(seconds=5),
            "maximum_spread": Decimal("0.05"),
            "instrument": self.spec if hasattr(self, "spec") else None,
        }
        values.update(changes)
        return PaperExecutionRequest(**values)  # type: ignore[arg-type]

    def test_paper_fill_is_idempotent_and_requires_all_gates(self) -> None:
        executor = PaperExecutor()
        result = executor.execute(self.session, self.request, now=self.now)
        self.assertEqual(result.status, "filled")
        self.session.commit()
        replay = executor.execute(self.session, self.request, now=self.now)
        self.assertEqual(replay.position_id, result.position_id)
        self.assertEqual(
            self.session.scalar(select(Position).where(Position.state == "open")).id,
            result.position_id,
        )
        self.assertEqual(self.session.scalar(select(Order)).status, "filled")

    def test_stale_disabled_or_nonpaper_request_is_recorded_and_released(self) -> None:
        stale_quote = ExecutionQuote(
            Decimal("100"), Decimal("100.01"), self.now - timedelta(minutes=1)
        )
        result = PaperExecutor().execute(
            self.session, self.make_request(quote=stale_quote), now=self.now
        )
        self.assertEqual(result.status, "rejected")
        self.assertIn("stale", result.reason)
        self.assertEqual(self.session.scalar(select(Order)).status, "rejected")
        self.assertEqual(self.session.scalar(select(RiskReservation)).state, "released")

    def test_actual_slippage_risk_must_fit_reservation(self) -> None:
        quote = ExecutionQuote(Decimal("100.1"), Decimal("100.2"), self.now)
        request = self.make_request(quote=quote)
        result = PaperExecutor().execute(self.session, request, now=self.now)
        self.assertEqual(result.status, "rejected")
        self.assertIn("exceed", result.reason)
        self.assertIsNone(self.session.scalar(select(Position).where(Position.state == "open")))

    def test_take_profit_closes_and_journals_trade(self) -> None:
        executor = PaperExecutor()
        result = executor.execute(self.session, self.request, now=self.now)
        self.session.commit()
        position = self.session.get(Position, result.position_id)
        updates = {
            self.symbol.id: (
                ExecutionQuote(Decimal("102.1"), Decimal("102.2"), self.now + timedelta(minutes=5)),
                self.spec,
            )
        }
        closed = process_paper_quotes(
            self.session,
            account_id=self.account.id,
            updates=updates,
            now=self.now + timedelta(minutes=5),
            maximum_quote_age=timedelta(seconds=5),
        )
        self.session.commit()
        self.assertEqual(closed, [position.id])
        self.assertEqual(position.state, "closed")
        trade = self.session.scalar(select(Trade).where(Trade.position_id == position.id))
        self.assertEqual(trade.exit_reason, "take_profit")
        self.assertEqual(trade.actual_exit, Decimal("102.1"))
        self.assertGreater(trade.mfe, Decimal("0"))
        self.assertEqual(self.session.scalar(select(RiskReservation)).state, "released")

    def test_quote_at_or_before_entry_cannot_close_new_position(self) -> None:
        result = PaperExecutor().execute(self.session, self.request, now=self.now)
        self.session.commit()
        position = self.session.get(Position, result.position_id)
        updates = {
            self.symbol.id: (
                ExecutionQuote(
                    Decimal("90"), Decimal("90.1"), self.now - timedelta(seconds=1)
                ),
                self.spec,
            )
        }
        closed = process_paper_quotes(
            self.session,
            account_id=self.account.id,
            updates=updates,
            now=self.now,
            maximum_quote_age=timedelta(minutes=5),
        )
        self.assertEqual(closed, [])
        self.assertEqual(position.state, "open")

    def test_reconciliation_fails_safe_on_missing_and_discovers_external_position(self) -> None:
        PaperExecutor().execute(self.session, self.request, now=self.now)
        self.session.commit()
        missing = reconcile_positions(
            self.session, account_id=self.account.id, observed=(), now=self.now
        )
        self.assertFalse(missing.safe_to_trade)
        self.assertEqual(missing.missing_external, (f"PAPER-{self.signal_id}",))
        self.session.commit()

        external = ExternalPosition(
            account_id=self.account.id,
            symbol_id=self.symbol.id,
            broker_ticket="MANUAL-77",
            direction="short",
            volume=Decimal("0.2"),
            entry_price=Decimal("101"),
            stop_loss=Decimal("102"),
            take_profit=Decimal("99"),
            opened_at=self.now,
        )
        found = reconcile_positions(
            self.session, account_id=self.account.id, observed=(external,), now=self.now
        )
        self.assertFalse(found.safe_to_trade)
        self.assertEqual(found.discovered_external, ("MANUAL-77",))
        self.session.commit()
        still_untracked = reconcile_positions(
            self.session, account_id=self.account.id, observed=(external,), now=self.now
        )
        self.assertFalse(still_untracked.safe_to_trade)
        self.assertEqual(still_untracked.changed, ("MANUAL-77",))


if __name__ == "__main__":
    unittest.main()
