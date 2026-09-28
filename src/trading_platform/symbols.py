"""Canonical instrument registry and broker-specific symbol mapping."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class AssetClass(StrEnum):
    GOLD = "gold"
    INDEX = "index"
    FOREX = "forex"


@dataclass(frozen=True, slots=True)
class Instrument:
    canonical: str
    asset_class: AssetClass


DEFAULT_INSTRUMENTS: tuple[Instrument, ...] = (
    Instrument("XAUUSD", AssetClass.GOLD),
    Instrument("NAS100", AssetClass.INDEX),
    Instrument("US100", AssetClass.INDEX),
    Instrument("USTEC", AssetClass.INDEX),
    Instrument("EURUSD", AssetClass.FOREX),
    Instrument("GBPUSD", AssetClass.FOREX),
    Instrument("USDJPY", AssetClass.FOREX),
    Instrument("AUDUSD", AssetClass.FOREX),
    Instrument("USDCAD", AssetClass.FOREX),
    Instrument("USDCHF", AssetClass.FOREX),
)


class SymbolRegistry:
    """Resolve canonical symbols without assuming broker naming conventions."""

    def __init__(self, instruments: tuple[Instrument, ...] = DEFAULT_INSTRUMENTS) -> None:
        canonical = [instrument.canonical for instrument in instruments]
        if len(canonical) != len(set(canonical)):
            raise ValueError("canonical instrument names must be unique")
        self._instruments = {item.canonical: item for item in instruments}

    def get(self, canonical: str) -> Instrument:
        try:
            return self._instruments[canonical.upper()]
        except KeyError as exc:
            raise ValueError(f"unknown canonical symbol: {canonical}") from exc

    def broker_symbol(self, canonical: str, mappings: Mapping[str, str]) -> str:
        """Require an explicit mapping; no implicit pass-through to broker orders."""

        instrument = self.get(canonical)
        try:
            broker_name = mappings[instrument.canonical].strip()
        except KeyError as exc:
            raise ValueError(f"no broker mapping configured for {instrument.canonical}") from exc
        if not broker_name:
            raise ValueError(f"empty broker mapping configured for {instrument.canonical}")
        return broker_name
