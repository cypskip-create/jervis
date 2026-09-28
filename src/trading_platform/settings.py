"""Validated application settings with fail-closed execution mode handling."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ExecutionMode = Literal["backtest", "paper", "demo", "live"]


class Settings(BaseModel):
    """Runtime configuration. Secrets are deliberately not modeled here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    app_name: str = "Quant MT5 Platform"
    mode: ExecutionMode = "paper"
    database_url: str = "sqlite:///./dev.sqlite3"
    global_enabled: bool = False
    risk_per_trade_fraction: float = Field(default=0.0025, gt=0, le=0.01)
    max_daily_loss_fraction: float = Field(default=0.01, gt=0, le=0.05)
    max_weekly_loss_fraction: float = Field(default=0.02, gt=0, le=0.10)
    max_drawdown_fraction: float = Field(default=0.05, gt=0, le=0.20)
    max_open_positions: int = Field(default=3, ge=1, le=100)
    max_total_open_risk_fraction: float = Field(default=0.01, gt=0, le=0.05)

    @field_validator("app_name")
    @classmethod
    def app_name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("app_name must not be blank")
        return value

    @model_validator(mode="after")
    def live_mode_is_not_available(self) -> Settings:
        if self.mode == "live":
            raise ValueError("live mode is not implemented and cannot be enabled")
        if self.global_enabled and self.mode == "backtest":
            raise ValueError("global_enabled cannot be true in backtest mode")
        return self


def load_settings() -> Settings:
    """Load defaults, optional TOML overrides, .env, then process environment."""

    values: dict[str, object] = {}
    allowed = set(Settings.model_fields)
    config_path = Path(os.getenv("TRADING_PLATFORM_CONFIG", "config/defaults.toml"))
    if config_path.is_file():
        with config_path.open("rb") as config_file:
            document = tomllib.load(config_file)
        values.update(document.get("application", {}))
        values.update(document.get("risk", {}))

    dotenv_path = Path(".env")
    if dotenv_path.is_file():
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", maxsplit=1)
            if key.startswith("TRADING_PLATFORM_"):
                field = key.removeprefix("TRADING_PLATFORM_").lower()
                if field in allowed:
                    values[field] = value.strip().strip("\"'")

    for key, value in os.environ.items():
        if key.startswith("TRADING_PLATFORM_"):
            field = key.removeprefix("TRADING_PLATFORM_").lower()
            if field in allowed:
                values[field] = value

    return Settings.model_validate(values)
