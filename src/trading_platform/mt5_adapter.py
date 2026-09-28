"""Optional, fail-closed adapter for an already logged-in Windows MT5 terminal.

The package is imported lazily so research, paper execution, and CI do not need
MetaTrader5 installed. Credentials are never accepted by this adapter; log in to
the terminal separately and configure an expected demo server before connecting.
"""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from .domain import TradeProposal
from .execution import ExternalPosition
from .market_data import Quote, require_fresh_quote


class MT5AdapterError(RuntimeError):
    """Terminal, account, or broker response was not safe to use."""


@dataclass(frozen=True, slots=True)
class MT5Config:
    expected_server: str
    allowed_account_ids: frozenset[int] = frozenset()
    terminal_path: str | None = None
    max_quote_age: timedelta = timedelta(seconds=5)
    deviation_points: int = 10

    def __post_init__(self) -> None:
        if not self.expected_server.strip():
            raise ValueError("an expected demo server is required")
        if not self.allowed_account_ids or any(value <= 0 for value in self.allowed_account_ids):
            raise ValueError("at least one positive demo account id must be allow-listed")
        if self.max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be positive")
        if self.deviation_points < 0:
            raise ValueError("deviation_points must be non-negative")


@dataclass(frozen=True, slots=True)
class MT5AccountStatus:
    login: int
    server: str
    currency: str
    balance: Decimal
    equity: Decimal


class MT5DemoAdapter:
    """Read market data and optionally route pre-approved proposals to a demo.

    The caller must still perform strategy, risk, and approval checks. No live
    accounts are accepted, and this adapter is deliberately not wired into the
    dashboard command path.
    """

    def __init__(self, config: MT5Config, terminal: Any | None = None) -> None:
        self.config = config
        self._terminal = terminal

    @property
    def terminal(self) -> Any:
        if self._terminal is None:
            try:
                self._terminal = importlib.import_module("MetaTrader5")
            except ImportError as exc:
                raise MT5AdapterError(
                    "MetaTrader5 Python package is optional and available on Windows only"
                ) from exc
        return self._terminal

    def connect(self) -> MT5AccountStatus:
        terminal = self.terminal
        initialized = (
            terminal.initialize(path=self.config.terminal_path)
            if self.config.terminal_path
            else terminal.initialize()
        )
        if not initialized:
            raise MT5AdapterError(f"MT5 initialize failed: {terminal.last_error()}")
        try:
            return self.account_status()
        except Exception:
            terminal.shutdown()
            raise

    def disconnect(self) -> None:
        self.terminal.shutdown()

    def account_status(self) -> MT5AccountStatus:
        terminal = self.terminal
        info = terminal.terminal_info()
        account = terminal.account_info()
        if info is None or account is None:
            raise MT5AdapterError("terminal or account information is unavailable")
        if not info.connected or not info.trade_allowed:
            raise MT5AdapterError("terminal is disconnected or trading is disabled")
        if not getattr(account, "trade_allowed", False) or not getattr(
            account, "trade_expert", False
        ):
            raise MT5AdapterError("account does not permit expert trading")
        if account.trade_mode != terminal.ACCOUNT_TRADE_MODE_DEMO:
            raise MT5AdapterError("only MT5 demo accounts are permitted")
        if account.server != self.config.expected_server:
            raise MT5AdapterError("connected account server does not match configured demo server")
        if self.config.allowed_account_ids and account.login not in self.config.allowed_account_ids:
            raise MT5AdapterError("connected demo account is not allow-listed")
        return MT5AccountStatus(
            login=int(account.login),
            server=str(account.server),
            currency=str(account.currency),
            balance=Decimal(str(account.balance)),
            equity=Decimal(str(account.equity)),
        )

    def get_quote(self, broker_symbol: str) -> Quote:
        tick = self.terminal.symbol_info_tick(broker_symbol)
        if tick is None:
            raise MT5AdapterError(f"no quote available for {broker_symbol}")
        timestamp = datetime.fromtimestamp(tick.time_msc / 1000, tz=UTC)
        quote = Quote(
            symbol=broker_symbol,
            bid=float(tick.bid),
            ask=float(tick.ask),
            observed_at=timestamp,
            source="mt5-demo",
        )
        require_fresh_quote(quote, now=datetime.now(UTC), max_age=self.config.max_quote_age)
        return quote

    def list_external_positions(
        self, *, account_id: str, symbol_id_by_broker_symbol: dict[str, str]
    ) -> tuple[ExternalPosition, ...]:
        """Return a demo-account position snapshot for the shared reconciler."""
        account = self.account_status()
        if not account_id.strip():
            raise MT5AdapterError("persisted account id must not be blank")
        positions = self.terminal.positions_get()
        if positions is None:
            raise MT5AdapterError("MT5 open-position snapshot is unavailable")
        observed: list[ExternalPosition] = []
        for position in positions:
            symbol_id = symbol_id_by_broker_symbol.get(str(position.symbol))
            if symbol_id is None:
                raise MT5AdapterError(f"no canonical symbol mapping for {position.symbol}")
            if position.type == self.terminal.POSITION_TYPE_BUY:
                direction = "long"
            elif position.type == self.terminal.POSITION_TYPE_SELL:
                direction = "short"
            else:
                raise MT5AdapterError(f"unsupported MT5 position type: {position.type}")
            observed.append(
                ExternalPosition(
                    account_id=account_id,
                    symbol_id=symbol_id,
                    broker_ticket=str(position.ticket),
                    direction=direction,
                    volume=Decimal(str(position.volume)),
                    entry_price=Decimal(str(position.price_open)),
                    stop_loss=Decimal(str(position.sl)) if position.sl else None,
                    take_profit=Decimal(str(position.tp)) if position.tp else None,
                    opened_at=datetime.fromtimestamp(position.time, tz=UTC),
                )
            )
        if len({item.broker_ticket for item in observed}) != len(observed):
            raise MT5AdapterError("MT5 position snapshot contains duplicate tickets")
        # Ensure the snapshot belongs to the account we just checked, even though
        # the local persistence key is a separate UUID.
        if str(account.login) not in {str(value) for value in self.config.allowed_account_ids}:
            raise MT5AdapterError("position snapshot account no longer matches the allow-list")
        return tuple(observed)

    def place_demo_market_order(
        self, proposal: TradeProposal, broker_symbol: str, volume: Decimal
    ) -> Any:
        """Send a market order only after demo/account/fresh-quote checks.

        This is intentionally an explicit adapter call, not a dashboard command.
        Broker execution must be reconciled before the caller considers it filled.
        """
        terminal = self.terminal
        self.account_status()
        if not volume.is_finite() or volume <= 0:
            raise MT5AdapterError("order volume must be finite and positive")
        if not terminal.symbol_select(broker_symbol, True):
            raise MT5AdapterError(f"broker symbol is unavailable: {broker_symbol}")
        symbol = terminal.symbol_info(broker_symbol)
        if symbol is None or symbol.trade_mode == terminal.SYMBOL_TRADE_MODE_DISABLED:
            raise MT5AdapterError(f"broker symbol is not enabled for trading: {broker_symbol}")
        is_buy = proposal.direction.value in {"bullish", "long"}
        trade_mode = symbol.trade_mode
        if trade_mode == terminal.SYMBOL_TRADE_MODE_CLOSEONLY or (
            trade_mode == terminal.SYMBOL_TRADE_MODE_LONGONLY and not is_buy
        ) or (trade_mode == terminal.SYMBOL_TRADE_MODE_SHORTONLY and is_buy):
            raise MT5AdapterError("broker symbol does not permit the proposal direction")
        step = Decimal(str(symbol.volume_step))
        minimum = Decimal(str(symbol.volume_min))
        maximum = Decimal(str(symbol.volume_max))
        if step <= 0 or minimum <= 0 or maximum < minimum:
            raise MT5AdapterError("broker volume rules are invalid")
        if volume < minimum or volume > maximum or (volume - minimum) % step != 0:
            raise MT5AdapterError("order volume violates broker minimum, maximum, or step")
        quote = self.get_quote(broker_symbol)
        now = datetime.now(UTC)
        if proposal.expires_at <= now or proposal.generated_at > now:
            raise MT5AdapterError("proposal is expired or has a future generation time")
        client_tag = "J" + hashlib.sha256(proposal.signal_id.encode()).hexdigest()[:10]
        positions = terminal.positions_get()
        orders = terminal.orders_get()
        for item in (*tuple(positions or ()), *tuple(orders or ())):
            if getattr(item, "comment", "") == client_tag:
                raise MT5AdapterError("signal already has a broker position or pending order")
        history = terminal.history_orders_get(proposal.generated_at.astimezone(UTC), now)
        if history is None:
            raise MT5AdapterError(
                "broker order history is unavailable; refusing uncertain duplicate"
            )
        if any(getattr(item, "comment", "") == client_tag for item in history):
            raise MT5AdapterError("signal already exists in broker order history")
        order_type = terminal.ORDER_TYPE_BUY if is_buy else terminal.ORDER_TYPE_SELL
        filling_flags = int(symbol.filling_mode)
        if filling_flags & terminal.SYMBOL_FILLING_IOC:
            filling = terminal.ORDER_FILLING_IOC
        elif filling_flags & terminal.SYMBOL_FILLING_FOK:
            filling = terminal.ORDER_FILLING_FOK
        else:
            raise MT5AdapterError("broker symbol has no supported IOC/FOK filling mode")
        request = {
            "action": terminal.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": float(volume),
            "type": order_type,
            "price": quote.ask if is_buy else quote.bid,
            "sl": float(proposal.stop_loss),
            "tp": float(proposal.take_profit),
            "deviation": self.config.deviation_points,
            "type_time": terminal.ORDER_TIME_GTC,
            "type_filling": filling,
            "comment": client_tag,
        }
        result = terminal.order_send(request)
        if result is None:
            raise MT5AdapterError(f"MT5 order_send returned no result: {terminal.last_error()}")
        if result.retcode not in {
            terminal.TRADE_RETCODE_DONE,
            getattr(terminal, "TRADE_RETCODE_DONE_PARTIAL", -1),
        }:
            raise MT5AdapterError(f"MT5 rejected order with retcode {result.retcode}")
        return result


def config_from_environment() -> MT5Config:
    """Build demo-only configuration from non-secret environment variables."""
    import os

    account_ids = frozenset(
        int(item.strip())
        for item in os.environ.get("TRADING_PLATFORM_MT5_DEMO_ACCOUNT_IDS", "").split(",")
        if item.strip()
    )
    server = os.environ.get("TRADING_PLATFORM_MT5_DEMO_SERVER", "")
    if not server.strip():
        raise MT5AdapterError("TRADING_PLATFORM_MT5_DEMO_SERVER must name the demo server")
    return MT5Config(
        expected_server=server,
        allowed_account_ids=account_ids,
        terminal_path=os.environ.get("TRADING_PLATFORM_MT5_TERMINAL_PATH") or None,
    )
