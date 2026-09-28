"""Persistence operations whose transaction boundaries are explicit to callers."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import Account, RiskReservation


class ReservationRejected(RuntimeError):
    """A reservation cannot be safely created under current account risk."""


def reserve_risk(
    session: Session,
    *,
    account_id: str,
    signal_id: str,
    amount: Decimal,
    maximum_total: Decimal,
    expires_at: datetime,
    now: datetime | None = None,
    exposure_commitments: dict[str, Decimal] | None = None,
    exposure_baseline: dict[str, Decimal] | None = None,
    exposure_limits: dict[str, Decimal] | None = None,
    currently_open_positions: int | None = None,
    maximum_positions: int | None = None,
) -> RiskReservation:
    """Reserve headroom under an account-row lock in the caller's transaction.

    On PostgreSQL, ``FOR UPDATE`` serializes risk approvals per account. The
    reservation row and caller's related signal changes must be committed in
    the same transaction. SQLite is suitable for isolated development/tests but
    does not provide equivalent row-level locking for concurrent writers.
    """

    now = now or datetime.now(UTC)
    if now.tzinfo is None or expires_at.tzinfo is None:
        raise ValueError("reservation timestamps must be timezone-aware")
    if (
        not amount.is_finite()
        or not maximum_total.is_finite()
        or amount <= 0
        or maximum_total <= 0
        or amount > maximum_total
    ):
        raise ValueError("reservation amount and limit must be positive and amount <= limit")
    if expires_at <= now:
        raise ValueError("reservation expiry must be in the future")
    exposure_commitments = exposure_commitments or {}
    exposure_baseline = exposure_baseline or {}
    exposure_limits = exposure_limits or {}
    for key, value in (*exposure_commitments.items(), *exposure_baseline.items()):
        if not key or not value.is_finite():
            raise ValueError("exposure values must have keys and finite Decimal values")
    for key, value in exposure_limits.items():
        if not key or not value.is_finite() or value <= 0:
            raise ValueError("exposure limits must have keys and finite positive values")

    account = session.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if account is None:
        raise ReservationRejected("unknown account")

    duplicate = session.scalar(
        select(RiskReservation.id).where(RiskReservation.signal_id == signal_id)
    )
    if duplicate is not None:
        raise ReservationRejected("signal already has a risk reservation")

    committed = session.scalar(
        select(func.coalesce(func.sum(RiskReservation.amount), 0)).where(
            RiskReservation.account_id == account_id,
            or_(
                RiskReservation.state == "filled",
                (
                    RiskReservation.state.in_(("held", "submitted"))
                    & (RiskReservation.expires_at > now)
                ),
            ),
        )
    )
    if Decimal(committed or 0) + amount > maximum_total:
        raise ReservationRejected("total portfolio risk limit would be exceeded")

    outstanding = session.scalars(
        select(RiskReservation).where(
            RiskReservation.account_id == account_id,
            or_(
                RiskReservation.state == "filled",
                (
                    RiskReservation.state.in_(("held", "submitted"))
                    & (RiskReservation.expires_at > now)
                ),
            ),
        )
    ).all()
    if (currently_open_positions is None) != (maximum_positions is None):
        raise ValueError("currently_open_positions and maximum_positions must be supplied together")
    if currently_open_positions is not None and maximum_positions is not None:
        if currently_open_positions + len(outstanding) >= maximum_positions:
            raise ReservationRejected("maximum simultaneous positions would be exceeded")
    for key, proposed_value in exposure_commitments.items():
        if key not in exposure_limits:
            raise ValueError(f"no limit supplied for reserved exposure dimension {key}")
        outstanding_value = sum(
            (Decimal(item.exposure_commitments.get(key, "0")) for item in outstanding),
            Decimal("0"),
        )
        projected = exposure_baseline.get(key, Decimal("0")) + outstanding_value + proposed_value
        measured = abs(projected) if key.startswith("currency:") else projected
        if measured > exposure_limits[key]:
            raise ReservationRejected(f"{key} exposure limit would be exceeded")

    account.risk_lock_version += 1
    reservation = RiskReservation(
        account_id=account_id,
        signal_id=signal_id,
        amount=amount,
        exposure_commitments={key: str(value) for key, value in exposure_commitments.items()},
        state="held",
        expires_at=expires_at,
    )
    session.add(reservation)
    session.flush()
    return reservation


def release_risk(
    session: Session,
    *,
    signal_id: str,
    now: datetime | None = None,
) -> RiskReservation:
    """Release a reservation after the corresponding exposure has resolved."""

    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("resolution timestamp must be timezone-aware")

    reservation = session.scalar(
        select(RiskReservation).where(RiskReservation.signal_id == signal_id).with_for_update()
    )
    if reservation is None:
        raise ReservationRejected("signal has no risk reservation")
    if reservation.state == "released":
        return reservation
    if reservation.state == "expired":
        return reservation

    account = session.scalar(
        select(Account).where(Account.id == reservation.account_id).with_for_update()
    )
    if account is None:
        raise ReservationRejected("reservation account no longer exists")
    account.risk_lock_version += 1
    reservation.state = "released"
    reservation.resolved_at = now
    session.flush()
    return reservation
