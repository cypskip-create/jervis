import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trading_platform.models import (
    Account,
    Base,
    Order,
    RiskReservation,
    Signal,
    Strategy,
    StrategyConfig,
    StrategyVersion,
    Symbol,
)
from trading_platform.repositories import ReservationRejected, release_risk, reserve_risk


class PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.account = Account(
            broker="paper",
            server="local",
            account_ref="acct-1",
            currency="USD",
            mode="paper",
        )
        symbol = Symbol(canonical="EURUSD", asset_class="forex")
        strategy = Strategy(key="fx_test", display_name="FX Test")
        self.session.add_all([self.account, symbol, strategy])
        self.session.flush()
        version = StrategyVersion(strategy_id=strategy.id, version="1.0.0")
        self.session.add(version)
        self.session.flush()
        config = StrategyConfig(strategy_version_id=version.id, config_hash="a" * 64, parameters={})
        self.session.add(config)
        self.session.flush()
        self.signal = Signal(
            signal_key="sig-1",
            account_id=self.account.id,
            symbol_id=symbol.id,
            strategy_version_id=version.id,
            strategy_config_id=config.id,
            direction="long",
            decision="approved",
            market_time=datetime.now(UTC),
        )
        self.session.add(self.signal)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_reservation_stores_exact_decimal_and_enforces_limit(self) -> None:
        now = datetime.now(UTC)
        reservation = reserve_risk(
            self.session,
            account_id=self.account.id,
            signal_id=self.signal.id,
            amount=Decimal("12.34567890"),
            maximum_total=Decimal("20"),
            expires_at=now + timedelta(minutes=5),
            now=now,
        )
        self.session.commit()
        saved = self.session.scalar(
            select(RiskReservation).where(RiskReservation.id == reservation.id)
        )
        self.assertEqual(saved.amount, Decimal("12.34567890"))

        second_signal = self._add_signal("sig-2")
        with self.assertRaisesRegex(ReservationRejected, "limit would be exceeded"):
            reserve_risk(
                self.session,
                account_id=self.account.id,
                signal_id=second_signal.id,
                amount=Decimal("8"),
                maximum_total=Decimal("20"),
                expires_at=now + timedelta(minutes=5),
                now=now,
            )

    def test_duplicate_signal_cannot_reserve_twice(self) -> None:
        now = datetime.now(UTC)
        args = dict(
            account_id=self.account.id,
            signal_id=self.signal.id,
            amount=Decimal("1"),
            maximum_total=Decimal("5"),
            expires_at=now + timedelta(minutes=1),
            now=now,
        )
        reserve_risk(self.session, **args)
        self.session.commit()
        with self.assertRaisesRegex(ReservationRejected, "already has"):
            reserve_risk(self.session, **args)

    def test_filled_reservation_counts_until_explicitly_released(self) -> None:
        now = datetime.now(UTC)
        reservation = reserve_risk(
            self.session,
            account_id=self.account.id,
            signal_id=self.signal.id,
            amount=Decimal("4"),
            maximum_total=Decimal("5"),
            expires_at=now + timedelta(minutes=1),
            now=now,
        )
        reservation.state = "filled"
        reservation.expires_at = now - timedelta(minutes=1)
        self.session.commit()
        second_signal = self._add_signal("sig-2")
        with self.assertRaisesRegex(ReservationRejected, "limit would be exceeded"):
            reserve_risk(
                self.session,
                account_id=self.account.id,
                signal_id=second_signal.id,
                amount=Decimal("2"),
                maximum_total=Decimal("5"),
                expires_at=now + timedelta(minutes=1),
                now=now,
            )

        release_risk(self.session, signal_id=self.signal.id, now=now)
        self.session.commit()
        third_signal = self._add_signal("sig-3")
        allowed = reserve_risk(
            self.session,
            account_id=self.account.id,
            signal_id=third_signal.id,
            amount=Decimal("2"),
            maximum_total=Decimal("5"),
            expires_at=now + timedelta(minutes=1),
            now=now,
        )
        self.assertEqual(allowed.amount, Decimal("2"))
        released = self.session.scalar(
            select(RiskReservation).where(RiskReservation.signal_id == self.signal.id)
        )
        self.assertEqual(released.expires_at, now - timedelta(minutes=1))
        self.assertEqual(released.resolved_at, now)

    def test_order_idempotency_key_is_unique(self) -> None:
        common = dict(
            account_id=self.account.id,
            signal_id=self.signal.id,
            idempotency_key="signal-sig-1",
            status="created",
        )
        self.session.add_all([Order(**common), Order(**common)])
        with self.assertRaises(IntegrityError):
            self.session.commit()

    def _add_signal(self, key: str) -> Signal:
        signal = Signal(
            signal_key=key,
            account_id=self.signal.account_id,
            symbol_id=self.signal.symbol_id,
            strategy_version_id=self.signal.strategy_version_id,
            strategy_config_id=self.signal.strategy_config_id,
            direction="long",
            decision="approved",
            market_time=datetime.now(UTC),
        )
        self.session.add(signal)
        self.session.flush()
        return signal


if __name__ == "__main__":
    unittest.main()
