from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from trading_platform.domain import TradeProposal
from trading_platform.market_structure import Direction
from trading_platform.mt5_adapter import (
    MT5AdapterError,
    MT5Config,
    MT5DemoAdapter,
    config_from_environment,
)


class FakeTerminal:
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010
    SYMBOL_TRADE_MODE_DISABLED = 0
    SYMBOL_TRADE_MODE_LONGONLY = 1
    SYMBOL_TRADE_MODE_SHORTONLY = 2
    SYMBOL_TRADE_MODE_CLOSEONLY = 3
    SYMBOL_TRADE_MODE_FULL = 4
    SYMBOL_FILLING_FOK = 1
    SYMBOL_FILLING_IOC = 2
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1

    def __init__(
        self,
        *,
        trade_mode: int = 0,
        existing: tuple[object, ...] = (),
        terminal_trade_allowed: bool = True,
    ) -> None:
        self.mode = trade_mode
        self.existing = existing
        self.terminal_trade_allowed = terminal_trade_allowed
        self.positions: tuple[object, ...] | None = ()
        self.history: tuple[object, ...] | None = ()
        self.request: dict[str, object] | None = None

    def initialize(self, **_: object) -> bool:
        return True

    def shutdown(self) -> None:
        pass

    def terminal_info(self) -> object:
        return SimpleNamespace(connected=True, trade_allowed=self.terminal_trade_allowed)

    def account_info(self) -> object:
        return SimpleNamespace(
            trade_allowed=True,
            trade_expert=True,
            trade_mode=self.mode,
            server="Demo-Server",
            login=42,
            currency="USD",
            balance=10000,
            equity=10000,
        )

    def symbol_info_tick(self, _: str) -> object:
        return SimpleNamespace(
            time_msc=int(datetime.now(UTC).timestamp() * 1000), bid=1.1, ask=1.1002
        )

    def symbol_select(self, *_: object) -> bool:
        return True

    def symbol_info(self, _: str) -> object:
        return SimpleNamespace(
            trade_mode=1,
            volume_step=0.01,
            volume_min=0.01,
            volume_max=100.0,
            filling_mode=self.SYMBOL_FILLING_IOC,
        )

    def positions_get(self, **_: object) -> tuple[object, ...]:
        if "symbol" in _:
            return self.existing
        return (*self.existing, *(self.positions or ()))

    def orders_get(self, **_: object) -> tuple[object, ...]:
        return ()

    def history_orders_get(self, *_: object) -> tuple[object, ...] | None:
        return self.history

    def order_send(self, request: dict[str, object]) -> object:
        self.request = request
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=1001)

    def last_error(self) -> str:
        return "mock error"


def proposal() -> TradeProposal:
    now = datetime.now(UTC)
    return TradeProposal(
        signal_id="signal-123",
        symbol="EURUSD",
        asset_class="forex",
        strategy_key="fx_a",
        strategy_version="v1",
        setup_id="pullback",
        direction=Direction.BULLISH,
        entry=Decimal("1.1"),
        stop_loss=Decimal("1.09"),
        take_profit=Decimal("1.12"),
        generated_at=now,
        expires_at=now + timedelta(minutes=1),
        reasons=("test",),
    )


class MT5AdapterTests(unittest.TestCase):
    def test_only_matching_demo_account_is_accepted(self) -> None:
        adapter = MT5DemoAdapter(MT5Config("Demo-Server", frozenset({42})), FakeTerminal())
        self.assertEqual(adapter.connect().login, 42)

    def test_read_only_connect_works_with_terminal_trading_disabled(self) -> None:
        terminal = FakeTerminal(terminal_trade_allowed=False)
        adapter = MT5DemoAdapter(MT5Config("Demo-Server", frozenset({42})), terminal)
        self.assertEqual(adapter.connect().login, 42)

    def test_order_is_blocked_when_terminal_algo_trading_is_disabled(self) -> None:
        terminal = FakeTerminal(terminal_trade_allowed=False)
        adapter = MT5DemoAdapter(MT5Config("Demo-Server", frozenset({42})), terminal)
        with self.assertRaisesRegex(MT5AdapterError, "terminal trading is disabled"):
            adapter.place_demo_market_order(proposal(), "EURUSD", Decimal("0.1"))
        self.assertIsNone(terminal.request)

    def test_rejects_live_account_before_order_send(self) -> None:
        terminal = FakeTerminal(trade_mode=FakeTerminal.ACCOUNT_TRADE_MODE_REAL)
        adapter = MT5DemoAdapter(MT5Config("Demo-Server", frozenset({42})), terminal)
        with self.assertRaisesRegex(MT5AdapterError, "only MT5 demo"):
            adapter.place_demo_market_order(proposal(), "EURUSD", Decimal("0.1"))
        self.assertIsNone(terminal.request)

    def test_rejects_duplicate_signal_and_non_positive_volume(self) -> None:
        tag = "J" + hashlib.sha256(b"signal-123").hexdigest()[:10]
        terminal = FakeTerminal(existing=(SimpleNamespace(comment=tag),))
        adapter = MT5DemoAdapter(MT5Config("Demo-Server", frozenset({42})), terminal)
        with self.assertRaisesRegex(MT5AdapterError, "already has"):
            adapter.place_demo_market_order(proposal(), "EURUSD", Decimal("0.1"))
        terminal.existing = ()
        with self.assertRaisesRegex(MT5AdapterError, "positive"):
            adapter.place_demo_market_order(proposal(), "EURUSD", Decimal("0"))

    def test_demo_market_order_has_broker_stop_and_target(self) -> None:
        terminal = FakeTerminal()
        adapter = MT5DemoAdapter(MT5Config("Demo-Server", frozenset({42})), terminal)
        result = adapter.place_demo_market_order(proposal(), "EURUSD", Decimal("0.1"))
        self.assertEqual(result.order, 1001)
        assert terminal.request is not None
        self.assertEqual(terminal.request["sl"], 1.09)
        self.assertEqual(terminal.request["tp"], 1.12)

    def test_stale_quote_is_rejected(self) -> None:
        terminal = FakeTerminal()
        terminal.symbol_info_tick = lambda _: SimpleNamespace(
            time_msc=int((datetime.now(UTC) - timedelta(seconds=30)).timestamp() * 1000),
            bid=1.1,
            ask=1.1002,
        )
        adapter = MT5DemoAdapter(MT5Config("Demo-Server", frozenset({42})), terminal)
        with self.assertRaisesRegex(ValueError, "stale quote"):
            adapter.get_quote("EURUSD")

    def test_unavailable_order_history_fails_closed(self) -> None:
        terminal = FakeTerminal()
        terminal.history = None
        adapter = MT5DemoAdapter(MT5Config("Demo-Server", frozenset({42})), terminal)
        with self.assertRaisesRegex(MT5AdapterError, "history is unavailable"):
            adapter.place_demo_market_order(proposal(), "EURUSD", Decimal("0.1"))
        self.assertIsNone(terminal.request)

    def test_requires_demo_account_allowlist(self) -> None:
        with self.assertRaisesRegex(ValueError, "allow-listed"):
            MT5Config("Demo-Server")

    def test_environment_configures_server_allowlist_and_quote_age(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "TRADING_PLATFORM_MT5_DEMO_SERVER": "Demo-Server",
                "TRADING_PLATFORM_MT5_DEMO_ACCOUNT_IDS": "42",
                "TRADING_PLATFORM_MT5_MAX_QUOTE_AGE_SECONDS": "10",
            },
            clear=True,
        ):
            config = config_from_environment()
        self.assertEqual(config.expected_server, "Demo-Server")
        self.assertEqual(config.allowed_account_ids, frozenset({42}))
        self.assertEqual(config.max_quote_age, timedelta(seconds=10))

    def test_environment_rejects_unbounded_quote_age(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "TRADING_PLATFORM_MT5_DEMO_SERVER": "Demo-Server",
                "TRADING_PLATFORM_MT5_DEMO_ACCOUNT_IDS": "42",
                "TRADING_PLATFORM_MT5_MAX_QUOTE_AGE_SECONDS": "61",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(MT5AdapterError, "at most 60"):
                config_from_environment()

    def test_position_snapshot_maps_to_shared_reconciliation_contract(self) -> None:
        terminal = FakeTerminal()
        terminal.positions = (
            SimpleNamespace(
                symbol="EURUSD",
                ticket=123,
                type=terminal.POSITION_TYPE_BUY,
                volume=0.1,
                price_open=1.1,
                sl=0.0,
                tp=1.2,
                time=int(datetime.now(UTC).timestamp()),
            ),
        )
        adapter = MT5DemoAdapter(MT5Config("Demo-Server", frozenset({42})), terminal)
        observed = adapter.list_external_positions(
            account_id="account-db-id", symbol_id_by_broker_symbol={"EURUSD": "symbol-db-id"}
        )
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].broker_ticket, "123")
        self.assertEqual(observed[0].direction, "long")
        self.assertIsNone(observed[0].stop_loss)


if __name__ == "__main__":
    unittest.main()
