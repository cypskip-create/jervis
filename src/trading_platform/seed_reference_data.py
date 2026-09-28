"""Seed the dashboard's supported symbols and strategy definitions, disabled by default."""

from __future__ import annotations

from sqlalchemy import select

from .database import make_engine, session_factory
from .models import Strategy, Symbol
from .settings import load_settings

SYMBOLS = (
    ("XAUUSD", "gold"),
    ("NAS100", "index"),
    ("EURUSD", "forex"),
    ("GBPUSD", "forex"),
    ("USDJPY", "forex"),
    ("AUDUSD", "forex"),
    ("USDCAD", "forex"),
    ("USDCHF", "forex"),
)
STRATEGIES = (
    ("gold", "Gold structural pullback"),
    ("nasdaq_sweep", "NASDAQ liquidity sweep"),
    ("fx_liquidity_pullback", "Forex liquidity pullback"),
    ("fx_trend_continuation", "Forex trend continuation"),
)


def main() -> None:
    factory = session_factory(make_engine(load_settings()))
    with factory.begin() as session:
        for canonical, asset_class in SYMBOLS:
            if session.scalar(select(Symbol.id).where(Symbol.canonical == canonical)) is None:
                session.add(Symbol(canonical=canonical, asset_class=asset_class, enabled=False))
        for key, name in STRATEGIES:
            if session.scalar(select(Strategy.id).where(Strategy.key == key)) is None:
                session.add(Strategy(key=key, display_name=name, enabled=False))
    print("Reference records are present. Symbols and strategies remain disabled by default.")


if __name__ == "__main__":
    main()
