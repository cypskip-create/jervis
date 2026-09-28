"""Fail-closed checklist for evidence required before a separate live review."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ReadinessEvidence:
    """Evidence status must be reviewed and backed by artifacts by an operator."""

    broker_and_symbol_specifications_reviewed: bool = False
    demo_execution_and_reconciliation_signed_off: bool = False
    disconnect_restart_and_duplicate_drills_passed: bool = False
    out_of_sample_and_walk_forward_reviewed: bool = False
    cost_slippage_and_currency_conversion_validated: bool = False
    risk_limits_and_emergency_procedure_reviewed: bool = False
    backup_restore_and_monitoring_drills_passed: bool = False
    security_and_access_reviewed: bool = False
    independent_operator_review_complete: bool = False
    explicit_live_authorization_recorded: bool = False


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    status: str
    live_mode_available: bool
    completed: tuple[str, ...]
    blockers: tuple[str, ...]


def review_readiness(evidence: ReadinessEvidence | None = None) -> ReadinessReport:
    """Return a NO-GO unless every separately reviewed evidence gate is complete."""
    record = evidence or ReadinessEvidence()
    fields = asdict(record)
    completed = tuple(name for name, verified in fields.items() if verified)
    blockers = tuple(name for name, verified in fields.items() if not verified)
    # Completing this checklist never changes Settings.mode or enables live trading.
    return ReadinessReport(
        status="READY_FOR_SEPARATE_REVIEW" if not blockers else "NOT_READY",
        live_mode_available=False,
        completed=completed,
        blockers=blockers,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable report")
    args = parser.parse_args()
    report = review_readiness()
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(f"Readiness: {report.status}")
        print("Live mode available: no")
        print("Evidence still required:")
        for item in report.blockers:
            print(f"- {item.replace('_', ' ')}")


if __name__ == "__main__":
    main()
