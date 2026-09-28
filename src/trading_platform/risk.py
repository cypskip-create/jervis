"""Central, strategy-independent approval and conservative position sizing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy.orm import Session

from .domain import TradeProposal
from .market_structure import Direction
from .repositories import ReservationRejected, reserve_risk


def _positive(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class InstrumentRiskSpec:
    """Broker metadata, with tick value already converted to account currency."""

    tick_size: Decimal
    tick_value_per_lot: Decimal
    minimum_volume: Decimal
    maximum_volume: Decimal
    volume_step: Decimal
    round_trip_commission_per_lot: Decimal = Decimal("0")
    slippage_ticks: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for name in (
            "tick_size",
            "tick_value_per_lot",
            "minimum_volume",
            "maximum_volume",
            "volume_step",
        ):
            _positive(getattr(self, name), name)
        if self.maximum_volume < self.minimum_volume:
            raise ValueError("maximum_volume must be >= minimum_volume")
        for name in ("round_trip_commission_per_lot", "slippage_ticks"):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class RiskLimits:
    risk_per_trade_fraction: Decimal = Decimal("0.0025")
    maximum_daily_loss_fraction: Decimal = Decimal("0.01")
    maximum_weekly_loss_fraction: Decimal = Decimal("0.02")
    maximum_drawdown_fraction: Decimal = Decimal("0.05")
    maximum_open_positions: int = 3
    maximum_portfolio_risk_fraction: Decimal = Decimal("0.01")
    maximum_symbol_risk_fraction: Decimal = Decimal("0.005")
    maximum_asset_risk_fraction: Decimal = Decimal("0.01")
    maximum_currency_exposure_fraction: Decimal = Decimal("0.25")
    maximum_correlated_risk_fraction: Decimal = Decimal("0.01")
    minimum_reward_risk: Decimal = Decimal("2.0")

    def __post_init__(self) -> None:
        bounded = (
            "risk_per_trade_fraction",
            "maximum_daily_loss_fraction",
            "maximum_weekly_loss_fraction",
            "maximum_drawdown_fraction",
            "maximum_portfolio_risk_fraction",
            "maximum_symbol_risk_fraction",
            "maximum_asset_risk_fraction",
            "maximum_currency_exposure_fraction",
            "maximum_correlated_risk_fraction",
        )
        for name in bounded:
            value = getattr(self, name)
            if not value.is_finite() or not 0 < value <= 1:
                raise ValueError(f"{name} must be finite and in (0, 1]")
        _positive(self.minimum_reward_risk, "minimum_reward_risk")
        if self.risk_per_trade_fraction > Decimal("0.01"):
            raise ValueError("risk_per_trade_fraction may not exceed 1%")
        if self.maximum_open_positions < 1:
            raise ValueError("maximum_open_positions must be at least 1")


@dataclass(frozen=True, slots=True)
class PortfolioState:
    equity: Decimal
    open_risk_cash: Decimal = Decimal("0")
    daily_loss_cash: Decimal = Decimal("0")
    weekly_loss_cash: Decimal = Decimal("0")
    peak_equity: Decimal | None = None
    daily_reference_equity: Decimal | None = None
    weekly_reference_equity: Decimal | None = None
    open_positions: int = 0
    symbol_risk_cash: dict[str, Decimal] = field(default_factory=dict)
    asset_risk_cash: dict[str, Decimal] = field(default_factory=dict)
    currency_exposure_cash: dict[str, Decimal] = field(default_factory=dict)
    correlation_risk_cash: dict[str, Decimal] = field(default_factory=dict)
    # Exposure represented by manual/untracked positions only. Filled and
    # pending managed positions are counted from durable reservation rows.
    unreserved_open_positions: int = 0
    unreserved_symbol_risk_cash: dict[str, Decimal] = field(default_factory=dict)
    unreserved_asset_risk_cash: dict[str, Decimal] = field(default_factory=dict)
    unreserved_currency_exposure_cash: dict[str, Decimal] = field(default_factory=dict)
    unreserved_correlation_risk_cash: dict[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _positive(self.equity, "equity")
        for name in ("open_risk_cash", "daily_loss_cash", "weekly_loss_cash"):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        for name in ("peak_equity", "daily_reference_equity", "weekly_reference_equity"):
            value = getattr(self, name)
            if value is not None:
                _positive(value, name)
        if self.open_positions < 0:
            raise ValueError("open_positions must be non-negative")
        if self.unreserved_open_positions < 0:
            raise ValueError("unreserved_open_positions must be non-negative")
        for name in (
            "symbol_risk_cash", "asset_risk_cash", "currency_exposure_cash",
            "correlation_risk_cash", "unreserved_symbol_risk_cash",
            "unreserved_asset_risk_cash", "unreserved_currency_exposure_cash",
            "unreserved_correlation_risk_cash",
        ):
            for key, value in getattr(self, name).items():
                if not key or not value.is_finite():
                    raise ValueError(f"{name} values must have keys and finite Decimals")
                if "currency" not in name and value < 0:
                    raise ValueError(f"{name} values must be non-negative")


@dataclass(frozen=True, slots=True)
class RiskEnvironment:
    now: datetime
    mode: str
    global_enabled: bool
    emergency_stop: bool
    symbol_enabled: bool
    strategy_enabled: bool
    market_open: bool
    quote_fresh: bool
    spread: Decimal
    maximum_spread: Decimal
    asset_class_enabled: bool = True
    duplicate_signal: bool = False

    def __post_init__(self) -> None:
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("risk evaluation time must be timezone-aware")
        if not self.spread.is_finite() or self.spread < 0:
            raise ValueError("spread must be finite and non-negative")
        if not self.maximum_spread.is_finite() or self.maximum_spread <= 0:
            raise ValueError("maximum_spread must be finite and positive")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reason: str
    volume: Decimal = Decimal("0")
    risk_cash: Decimal = Decimal("0")
    reward_risk: Decimal = Decimal("0")


def loss_per_lot(
    entry: Decimal,
    stop_loss: Decimal,
    spec: InstrumentRiskSpec,
) -> Decimal:
    """Estimate stop loss cash including round-trip commission and slippage."""

    _positive(entry, "entry")
    _positive(stop_loss, "stop_loss")
    price_loss = abs(entry - stop_loss) / spec.tick_size * spec.tick_value_per_lot
    return (
        price_loss
        + spec.round_trip_commission_per_lot
        + (spec.slippage_ticks * spec.tick_value_per_lot)
    )


def calculate_position_size(
    *,
    equity: Decimal,
    risk_fraction: Decimal,
    entry: Decimal,
    stop_loss: Decimal,
    spec: InstrumentRiskSpec,
) -> tuple[Decimal, Decimal]:
    """Return step-rounded volume and its modeled cash risk, never over budget."""

    _positive(equity, "equity")
    if not risk_fraction.is_finite() or not 0 < risk_fraction <= Decimal("0.01"):
        raise ValueError("risk_fraction must be finite and in (0, 1%]")
    per_lot = loss_per_lot(entry, stop_loss, spec)
    if not per_lot.is_finite() or per_lot <= 0:
        raise ValueError("estimated loss per lot must be finite and positive")
    budget = equity * risk_fraction
    raw_volume = budget / per_lot
    volume = (raw_volume / spec.volume_step).to_integral_value(
        rounding=ROUND_FLOOR
    ) * spec.volume_step
    if volume < spec.minimum_volume:
        raise ValueError("minimum broker volume exceeds the configured risk budget")
    maximum_allowed = (spec.maximum_volume / spec.volume_step).to_integral_value(
        rounding=ROUND_FLOOR
    ) * spec.volume_step
    if maximum_allowed < spec.minimum_volume:
        raise ValueError("broker maximum volume and step do not permit a valid lot")
    volume = min(volume, maximum_allowed)
    risk_cash = volume * per_lot
    if risk_cash > budget:
        raise ValueError("rounded position size exceeds the configured risk budget")
    return volume, risk_cash


def fx_currency_exposure_per_lot(
    *,
    canonical_symbol: str,
    direction: Direction,
    price: Decimal,
    contract_size: Decimal,
    account_conversion: Mapping[str, Decimal],
) -> dict[str, Decimal]:
    """Return signed base/quote notionals in account currency for one FX lot.

    `account_conversion[currency]` is the account-currency value of one unit
    of that currency, supplied by a fresh, separately validated conversion
    source. Missing conversion rates fail closed.
    """

    if len(canonical_symbol) != 6 or not canonical_symbol.isalpha():
        raise ValueError("FX canonical symbol must contain two three-letter currencies")
    _positive(price, "price")
    _positive(contract_size, "contract_size")
    base, quote = canonical_symbol[:3].upper(), canonical_symbol[3:].upper()
    if base not in account_conversion or quote not in account_conversion:
        raise ValueError("account-currency conversion is required for both FX currencies")
    base_rate, quote_rate = account_conversion[base], account_conversion[quote]
    _positive(base_rate, f"{base} conversion")
    _positive(quote_rate, f"{quote} conversion")
    sign = Decimal("1") if direction == Direction.BULLISH else Decimal("-1")
    return {
        base: contract_size * base_rate * sign,
        quote: contract_size * price * quote_rate * -sign,
    }


class RiskEngine:
    """Pure approval gate; strategies never receive execution capabilities."""

    def evaluate(
        self,
        proposal: TradeProposal,
        *,
        limits: RiskLimits,
        portfolio: PortfolioState,
        environment: RiskEnvironment,
        instrument: InstrumentRiskSpec,
    ) -> RiskDecision:
        def reject(reason: str) -> RiskDecision:
            return RiskDecision(False, reason, reward_risk=proposal.reward_risk)

        if environment.mode not in {"paper", "demo"}:
            return reject("execution mode is not enabled by this risk engine")
        if not environment.global_enabled:
            return reject("global bot disabled")
        if environment.emergency_stop:
            return reject("emergency stop active")
        if not environment.symbol_enabled:
            return reject(f"{proposal.symbol} disabled")
        if not environment.asset_class_enabled:
            return reject(f"{proposal.asset_class} asset class disabled")
        if not environment.strategy_enabled:
            return reject(f"{proposal.strategy_key} disabled")
        if not environment.market_open:
            return reject("market closed")
        if not environment.quote_fresh:
            return reject("quote stale")
        if environment.spread > environment.maximum_spread:
            return reject("spread exceeds configured maximum")
        if environment.duplicate_signal:
            return reject("duplicate signal")
        if environment.now < proposal.generated_at:
            return reject("proposal timestamp is in the future")
        if environment.now >= proposal.expires_at:
            return reject("proposal expired")
        if proposal.reward_risk < limits.minimum_reward_risk:
            return reject("reward/risk below configured minimum")
        if portfolio.open_positions >= limits.maximum_open_positions:
            return reject("maximum simultaneous positions reached")
        daily_reference = portfolio.daily_reference_equity or portfolio.equity
        weekly_reference = portfolio.weekly_reference_equity or portfolio.equity
        if portfolio.daily_loss_cash >= daily_reference * limits.maximum_daily_loss_fraction:
            return reject("maximum daily loss reached")
        if portfolio.weekly_loss_cash >= weekly_reference * limits.maximum_weekly_loss_fraction:
            return reject("maximum weekly loss reached")
        if portfolio.peak_equity is not None:
            drawdown = max(
                Decimal("0"), (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity
            )
            if drawdown >= limits.maximum_drawdown_fraction:
                return reject("maximum account drawdown reached")

        try:
            volume, risk_cash = calculate_position_size(
                equity=portfolio.equity,
                risk_fraction=limits.risk_per_trade_fraction,
                entry=proposal.entry,
                stop_loss=proposal.stop_loss,
                spec=instrument,
            )
        except ValueError as exc:
            return reject(str(exc))

        total_limit = portfolio.equity * limits.maximum_portfolio_risk_fraction
        if portfolio.open_risk_cash + risk_cash > total_limit:
            return reject("total portfolio risk limit would be exceeded")
        symbol_limit = portfolio.equity * limits.maximum_symbol_risk_fraction
        if portfolio.symbol_risk_cash.get(proposal.symbol, Decimal("0")) + risk_cash > symbol_limit:
            return reject("symbol risk limit would be exceeded")
        asset_limit = portfolio.equity * limits.maximum_asset_risk_fraction
        if (
            portfolio.asset_risk_cash.get(proposal.asset_class, Decimal("0")) + risk_cash
            > asset_limit
        ):
            return reject("asset-class risk limit would be exceeded")

        for currency, per_lot in proposal.currency_exposure_per_lot.items():
            projected = (
                portfolio.currency_exposure_cash.get(currency, Decimal("0")) + per_lot * volume
            )
            if abs(projected) > portfolio.equity * limits.maximum_currency_exposure_fraction:
                return reject(f"{currency} exposure limit would be exceeded")
        for group in proposal.correlation_groups:
            projected = portfolio.correlation_risk_cash.get(group, Decimal("0")) + risk_cash
            if projected > portfolio.equity * limits.maximum_correlated_risk_fraction:
                return reject(f"correlated exposure limit would be exceeded: {group}")

        return RiskDecision(True, "approved", volume, risk_cash, proposal.reward_risk)

    def approve_and_reserve(
        self,
        session: Session,
        proposal: TradeProposal,
        *,
        account_id: str,
        limits: RiskLimits,
        portfolio: PortfolioState,
        environment: RiskEnvironment,
        instrument: InstrumentRiskSpec,
    ) -> RiskDecision:
        """Evaluate, then reserve total portfolio risk in the current DB transaction."""

        decision = self.evaluate(
            proposal,
            limits=limits,
            portfolio=portfolio,
            environment=environment,
            instrument=instrument,
        )
        if not decision.approved:
            return decision
        try:
            exposure_baseline = {
                **{
                    f"symbol:{symbol}": value
                    for symbol, value in portfolio.unreserved_symbol_risk_cash.items()
                },
                **{
                    f"asset:{asset}": value
                    for asset, value in portfolio.unreserved_asset_risk_cash.items()
                },
                **{
                    f"currency:{currency}": value
                    for currency, value in portfolio.unreserved_currency_exposure_cash.items()
                },
                **{
                    f"correlation:{group}": value
                    for group, value in portfolio.unreserved_correlation_risk_cash.items()
                },
            }
            exposure_commitments = {
                f"symbol:{proposal.symbol}": decision.risk_cash,
                f"asset:{proposal.asset_class}": decision.risk_cash,
                **{
                    f"currency:{currency}": amount_per_lot * decision.volume
                    for currency, amount_per_lot in proposal.currency_exposure_per_lot.items()
                },
                **{
                    f"correlation:{group}": decision.risk_cash
                    for group in proposal.correlation_groups
                },
            }
            exposure_limits = {
                f"symbol:{proposal.symbol}": (
                    portfolio.equity * limits.maximum_symbol_risk_fraction
                ),
                f"asset:{proposal.asset_class}": (
                    portfolio.equity * limits.maximum_asset_risk_fraction
                ),
                **{
                    f"currency:{currency}": (
                        portfolio.equity * limits.maximum_currency_exposure_fraction
                    )
                    for currency in proposal.currency_exposure_per_lot
                },
                **{
                    f"correlation:{group}": (
                        portfolio.equity * limits.maximum_correlated_risk_fraction
                    )
                    for group in proposal.correlation_groups
                },
            }
            reserve_risk(
                session,
                account_id=account_id,
                signal_id=proposal.signal_id,
                amount=decision.risk_cash,
                maximum_total=portfolio.equity * limits.maximum_portfolio_risk_fraction,
                expires_at=proposal.expires_at,
                now=environment.now,
                exposure_commitments=exposure_commitments,
                exposure_baseline=exposure_baseline,
                exposure_limits=exposure_limits,
                currently_open_positions=portfolio.unreserved_open_positions,
                maximum_positions=limits.maximum_open_positions,
            )
        except ReservationRejected as exc:
            return RiskDecision(False, str(exc), reward_risk=decision.reward_risk)
        return decision
