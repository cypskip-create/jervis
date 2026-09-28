"""Paper-only idempotent execution, baseline SL/TP management and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import ExecutionQuote, TradeProposal
from .market_structure import Direction
from .models import Order, Position, RiskReservation, Signal, Trade
from .repositories import release_risk
from .risk import InstrumentRiskSpec, RiskDecision


@dataclass(frozen=True, slots=True)
class PaperExecutionResult:
    status: str
    order_id: str | None
    position_id: str | None = None
    filled_price: Decimal | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PaperExecutionRequest:
    proposal: TradeProposal
    account_id: str
    symbol_id: str
    risk_decision: RiskDecision
    environment_mode: str
    global_enabled: bool
    emergency_stop: bool
    symbol_enabled: bool
    strategy_enabled: bool
    market_open: bool
    quote: ExecutionQuote
    quote_max_age: timedelta
    maximum_spread: Decimal
    instrument: InstrumentRiskSpec


class PaperExecutor:
    """Execution adapter that can create paper positions only."""

    def __init__(self, *, slippage_ticks: Decimal = Decimal("0")) -> None:
        if not slippage_ticks.is_finite() or slippage_ticks < 0:
            raise ValueError("paper slippage must be finite and non-negative")
        self.slippage_ticks = slippage_ticks

    def execute(
        self, session: Session, request: PaperExecutionRequest, *, now: datetime
    ) -> PaperExecutionResult:
        proposal = request.proposal
        idempotency_key = proposal.signal_id
        existing = session.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
        if existing is not None:
            position = session.scalar(
                select(Position).where(Position.signal_id == proposal.signal_id)
            )
            return PaperExecutionResult(
                existing.status,
                existing.id,
                position.id if position is not None else None,
                position.entry_price if position is not None else None,
                "idempotent replay; prior order result returned",
            )

        rejection = self._rejection_reason(request, now)
        signal = session.get(Signal, proposal.signal_id)
        if signal is None:
            return PaperExecutionResult("rejected", None, reason="signal record not persisted")
        reservation = session.scalar(
            select(RiskReservation)
            .where(RiskReservation.signal_id == proposal.signal_id)
            .with_for_update()
        )
        if reservation is None:
            return PaperExecutionResult(
                "rejected", None, reason="active risk reservation not found"
            )
        if rejection is None and not request.risk_decision.approved:
            rejection = "central risk approval missing"
        if rejection is None and (
            signal.symbol_id != request.symbol_id
            or signal.account_id not in (None, request.account_id)
        ):
            rejection = "signal account or symbol does not match execution request"
        if rejection is None and (
            reservation is None
            or reservation.state != "held"
            or reservation.account_id != request.account_id
            or reservation.amount < request.risk_decision.risk_cash
        ):
            rejection = "active risk reservation missing or insufficient"
        if rejection is None and (
            not request.risk_decision.volume.is_finite()
            or request.risk_decision.volume <= 0
            or not request.risk_decision.risk_cash.is_finite()
            or request.risk_decision.risk_cash <= 0
        ):
            rejection = "risk approval contains invalid size or cash risk"

        if rejection is not None:
            order = self._record_order(
                session,
                request,
                idempotency_key=idempotency_key,
                status="rejected",
                response={"reason": rejection},
            )
            if (
                reservation is not None
                and reservation.account_id == request.account_id
                and reservation.state in {"held", "submitted"}
            ):
                release_risk(session, signal_id=proposal.signal_id, now=now)
            return PaperExecutionResult("rejected", order.id, reason=rejection)

        fill = self._fill_price(request)
        if not _levels_valid_at_fill(proposal, fill):
            rejection = "executable quote has already crossed the proposed stop or target"
            order = self._record_order(
                session,
                request,
                idempotency_key=idempotency_key,
                status="rejected",
                response={"reason": rejection, "fill_candidate": str(fill)},
            )
            release_risk(session, signal_id=proposal.signal_id, now=now)
            return PaperExecutionResult("rejected", order.id, reason=rejection)

        volume = request.risk_decision.volume
        actual_risk = _actual_loss_per_lot(fill, proposal.stop_loss, request.instrument) * volume
        if actual_risk > request.risk_decision.risk_cash:
            rejection = "current executable price would exceed the approved risk reservation"
            order = self._record_order(
                session,
                request,
                idempotency_key=idempotency_key,
                status="rejected",
                response={"reason": rejection, "actual_risk": str(actual_risk)},
            )
            release_risk(session, signal_id=proposal.signal_id, now=now)
            return PaperExecutionResult("rejected", order.id, reason=rejection)

        order = self._record_order(
            session,
            request,
            idempotency_key=idempotency_key,
            status="submitted",
            response={"paper_fill_candidate": str(fill)},
        )
        session.flush()
        position = Position(
            account_id=request.account_id,
            symbol_id=request.symbol_id,
            signal_id=proposal.signal_id,
            broker_ticket=f"PAPER-{proposal.signal_id}",
            direction=_direction_label(proposal.direction),
            volume=volume,
            entry_price=fill,
            stop_loss=proposal.stop_loss,
            take_profit=proposal.take_profit,
            max_favorable_price=fill,
            max_adverse_price=fill,
            opened_at=now,
            last_reconciled_at=now,
            state="open",
        )
        session.add(position)
        session.flush()
        order.status = "filled"
        order.broker_order_id = position.broker_ticket
        order.response = {
            "status": "filled",
            "paper_ticket": position.broker_ticket,
            "fill_price": str(fill),
            "volume": str(volume),
            "risk_cash": str(actual_risk),
        }
        reservation.state = "filled"
        return PaperExecutionResult("filled", order.id, position.id, fill)

    @staticmethod
    def _rejection_reason(request: PaperExecutionRequest, now: datetime) -> str | None:
        proposal = request.proposal
        if request.environment_mode != "paper":
            return "paper executor accepts paper mode only"
        if not request.global_enabled:
            return "global bot disabled"
        if request.emergency_stop:
            return "emergency stop active"
        if not request.symbol_enabled:
            return "symbol disabled"
        if not request.strategy_enabled:
            return "strategy disabled"
        if not request.market_open:
            return "market closed"
        if now.tzinfo is None or now.utcoffset() is None:
            return "execution timestamp is not timezone-aware"
        if now >= proposal.expires_at:
            return "proposal expired"
        if request.quote.observed_at > now:
            return "quote timestamp is in the future"
        age = now - request.quote.observed_at
        if age > request.quote_max_age:
            return "quote stale"
        if request.quote_max_age <= timedelta(0):
            return "quote maximum age must be positive"
        if not request.maximum_spread.is_finite() or request.maximum_spread <= 0:
            return "maximum spread must be finite and positive"
        if request.quote.spread <= 0 or request.quote.spread > request.maximum_spread:
            return "spread invalid or exceeds configured maximum"
        return None

    def _fill_price(self, request: PaperExecutionRequest) -> Decimal:
        slip = self.slippage_ticks * request.instrument.tick_size
        if request.proposal.direction == Direction.BULLISH:
            return request.quote.ask + slip
        return request.quote.bid - slip

    @staticmethod
    def _record_order(
        session: Session,
        request: PaperExecutionRequest,
        *,
        idempotency_key: str,
        status: str,
        response: dict[str, str],
    ) -> Order:
        order = Order(
            account_id=request.account_id,
            signal_id=request.proposal.signal_id,
            idempotency_key=idempotency_key,
            status=status,
            request={
                "mode": request.environment_mode,
                "symbol": request.proposal.symbol,
                "direction": _direction_label(request.proposal.direction),
                "entry": str(request.proposal.entry),
                "stop_loss": str(request.proposal.stop_loss),
                "take_profit": str(request.proposal.take_profit),
                "risk_cash": str(request.risk_decision.risk_cash),
            },
            response=response,
        )
        session.add(order)
        session.flush()
        return order


def process_paper_quotes(
    session: Session,
    *,
    account_id: str,
    updates: dict[str, tuple[ExecutionQuote, InstrumentRiskSpec]],
    now: datetime,
    maximum_quote_age: timedelta,
) -> list[str]:
    """Update paper excursions and close on original SL/TP using executable side."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("paper quote processing time must be timezone-aware")
    if maximum_quote_age <= timedelta(0):
        raise ValueError("maximum quote age must be positive")
    closed: list[str] = []
    positions = session.scalars(
        select(Position).where(Position.account_id == account_id, Position.state == "open")
    ).all()
    for position in positions:
        update = updates.get(position.symbol_id)
        if update is None:
            continue
        quote, spec = update
        quote_age = now - quote.observed_at
        opened_at = position.opened_at
        if opened_at.tzinfo is None or opened_at.utcoffset() is None:
            # SQLite drops timezone metadata for DateTime(timezone=True).
            opened_at = opened_at.replace(tzinfo=UTC)
        if (
            quote_age < timedelta(0)
            or quote_age > maximum_quote_age
            or quote.observed_at <= opened_at
        ):
            continue
        long_position = position.direction == "long"
        executable = quote.bid if long_position else quote.ask
        if long_position:
            position.max_favorable_price = max(position.max_favorable_price, executable)
            position.max_adverse_price = min(position.max_adverse_price, executable)
            stopped = position.stop_loss is not None and executable <= position.stop_loss
            targeted = position.take_profit is not None and executable >= position.take_profit
        else:
            position.max_favorable_price = min(position.max_favorable_price, executable)
            position.max_adverse_price = max(position.max_adverse_price, executable)
            stopped = position.stop_loss is not None and executable >= position.stop_loss
            targeted = position.take_profit is not None and executable <= position.take_profit
        position.last_reconciled_at = quote.observed_at
        if stopped or targeted:
            _close_paper_position(
                session,
                position,
                exit_price=executable,
                closed_at=quote.observed_at,
                exit_reason="stop_loss" if stopped else "take_profit",
                spec=spec,
            )
            closed.append(position.id)
    return closed


def _close_paper_position(
    session: Session,
    position: Position,
    *,
    exit_price: Decimal,
    closed_at: datetime,
    exit_reason: str,
    spec: InstrumentRiskSpec,
) -> Trade:
    if position.signal_id is None:
        raise ValueError("cannot journal a paper close without its originating signal")
    signal = session.get(Signal, position.signal_id)
    if signal is None:
        raise ValueError("paper position signal is missing")
    direction = Decimal("1") if position.direction == "long" else Decimal("-1")
    price_delta = (exit_price - position.entry_price) * direction
    gross = price_delta / spec.tick_size * spec.tick_value_per_lot * position.volume
    commission = spec.round_trip_commission_per_lot * position.volume
    realized = gross - commission
    favorable_delta = (position.max_favorable_price - position.entry_price) * direction
    adverse_delta = (position.max_adverse_price - position.entry_price) * direction
    mfe = max(
        Decimal("0"), favorable_delta / spec.tick_size * spec.tick_value_per_lot * position.volume
    )
    mae = max(
        Decimal("0"), -adverse_delta / spec.tick_size * spec.tick_value_per_lot * position.volume
    )
    trade = Trade(
        position_id=position.id,
        signal_id=signal.id,
        strategy_version_id=signal.strategy_version_id,
        strategy_config_id=signal.strategy_config_id,
        actual_entry=position.entry_price,
        actual_exit=exit_price,
        volume=position.volume,
        commission=commission,
        swap=Decimal("0"),
        realized_pnl=realized,
        realized_r=_realized_r(session, signal.id, realized),
        exit_reason=exit_reason,
        opened_at=position.opened_at,
        closed_at=closed_at,
        mfe=mfe,
        mae=mae,
    )
    session.add(trade)
    position.state = "closed"
    release_risk(session, signal_id=signal.id, now=closed_at)
    return trade


@dataclass(frozen=True, slots=True)
class ExternalPosition:
    account_id: str
    symbol_id: str
    broker_ticket: str
    direction: str
    volume: Decimal
    entry_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    opened_at: datetime

    def __post_init__(self) -> None:
        if not self.account_id or not self.symbol_id or not self.broker_ticket:
            raise ValueError("external position identity fields must not be blank")
        if self.direction not in {"long", "short"}:
            raise ValueError("external position direction must be long or short")
        for name in ("volume", "entry_price"):
            value = getattr(self, name)
            if not value.is_finite() or value <= 0:
                raise ValueError(f"external position {name} must be finite and positive")
        if self.opened_at.tzinfo is None or self.opened_at.utcoffset() is None:
            raise ValueError("external position time must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    discovered_external: tuple[str, ...]
    missing_external: tuple[str, ...]
    changed: tuple[str, ...]

    @property
    def safe_to_trade(self) -> bool:
        return not (self.discovered_external or self.missing_external or self.changed)


def reconcile_positions(
    session: Session,
    *,
    account_id: str,
    observed: tuple[ExternalPosition, ...],
    now: datetime,
) -> ReconciliationReport:
    """Broker snapshot is authoritative; discrepancies block new entries.

    Unknown broker positions are persisted as `untracked` so portfolio/risk
    consumers can include them. Missing broker positions are retained and
    marked `missing_at_broker`; they are never silently deleted or journaled as
    closed without a confirmed deal/history record.
    """

    tickets = [item.broker_ticket for item in observed]
    if len(tickets) != len(set(tickets)):
        raise ValueError("broker snapshot contains duplicate position tickets")
    if any(item.account_id != account_id for item in observed):
        raise ValueError("broker snapshot contains a position for another account")
    local = session.scalars(
        select(Position).where(
            Position.account_id == account_id,
            Position.state.in_(("open", "untracked", "missing_at_broker")),
        )
    ).all()
    by_ticket = {position.broker_ticket: position for position in local}
    observed_by_ticket = {item.broker_ticket: item for item in observed}
    discovered: list[str] = []
    missing: list[str] = []
    changed: list[str] = []

    for ticket, position in by_ticket.items():
        external = observed_by_ticket.get(ticket)
        if external is None:
            if position.state != "missing_at_broker":
                position.state = "missing_at_broker"
            position.last_reconciled_at = now
            missing.append(ticket)
            continue
        drift = (
            position.direction != external.direction
            or position.volume != external.volume
            or position.entry_price != external.entry_price
            or position.stop_loss != external.stop_loss
            or position.take_profit != external.take_profit
        )
        if drift:
            position.direction = external.direction
            position.volume = external.volume
            position.entry_price = external.entry_price
            position.stop_loss = external.stop_loss
            position.take_profit = external.take_profit
            position.state = "untracked" if position.signal_id is None else "open"
            changed.append(ticket)
        elif position.state == "missing_at_broker":
            position.state = "open" if position.signal_id is not None else "untracked"
            changed.append(ticket)
        if position.signal_id is None:
            position.state = "untracked"
            if ticket not in changed:
                changed.append(ticket)
        position.last_reconciled_at = now
        observed_by_ticket.pop(ticket)

    for ticket, external in observed_by_ticket.items():
        session.add(
            Position(
                account_id=account_id,
                symbol_id=external.symbol_id,
                signal_id=None,
                broker_ticket=ticket,
                direction=external.direction,
                volume=external.volume,
                entry_price=external.entry_price,
                stop_loss=external.stop_loss,
                take_profit=external.take_profit,
                max_favorable_price=external.entry_price,
                max_adverse_price=external.entry_price,
                opened_at=external.opened_at,
                last_reconciled_at=now,
                state="untracked",
            )
        )
        discovered.append(ticket)
    return ReconciliationReport(tuple(discovered), tuple(missing), tuple(changed))


def _actual_loss_per_lot(fill: Decimal, stop_loss: Decimal, spec: InstrumentRiskSpec) -> Decimal:
    return abs(fill - stop_loss) / spec.tick_size * spec.tick_value_per_lot + (
        spec.round_trip_commission_per_lot
    )


def _realized_r(session: Session, signal_id: str, realized: Decimal) -> Decimal | None:
    reservation = session.scalar(
        select(RiskReservation).where(RiskReservation.signal_id == signal_id)
    )
    if reservation is None or reservation.amount <= 0:
        return None
    return realized / reservation.amount


def _levels_valid_at_fill(proposal: TradeProposal, fill: Decimal) -> bool:
    if proposal.direction == Direction.BULLISH:
        return proposal.stop_loss < fill < proposal.take_profit
    return proposal.take_profit < fill < proposal.stop_loss


def _direction_label(direction: Direction) -> str:
    return "long" if direction == Direction.BULLISH else "short"
