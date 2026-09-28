# Implementation Plan

Each phase is independently reviewable. Do not enable live trading as a consequence of passing tests. Tests are added as each behavior is implemented; tests use deterministic fixtures and mocked broker/database boundaries.

| Phase | Deliverable | Acceptance criteria |
|---|---|---|
| 0. Architecture and bootstrap | Architecture, strategy, risk, database and plan docs; repository/package/config skeleton | Fresh checkout has safe paper defaults, no secrets, clear setup instructions, and module boundaries documented |
| 1. Configuration and domain contracts | Validated settings, modes, symbol registry/mapping, shared types and structured logging | Invalid/unsafe config fails closed; canonical symbols do not assume broker names; config tests pass |
| 2. Market data and objective structure | Quote/bar ports, freshness, pivots, levels, BOS/CHoCH/sweeps/retests/regime | Definitions documented and parameterized; no look-ahead; edge-case unit tests pass |
| 3. Persistence | SQLAlchemy models, Alembic, repositories, audit/decision records | Migrations create schema; uniqueness and numeric/timestamp constraints tested |
| 4. Central risk | Sizing, limits, exposure, atomic reservations | Invalid metadata rejects; concurrent approvals cannot oversubscribe; sizing tests pass |
| 5. Strategy state machines | Gold, NASDAQ, FX A/B produce explainable proposals | Transition tests cover expiry, invalidation, no chase, neutral context and RR rejection |
| 6. Paper execution and reconciliation | Idempotent simulator, lifecycle, restart reconciliation contracts | Duplicate/stale requests never create duplicate positions; broker mismatch fails safe |
| 7. Backtesting | Multi-timeframe event loop, costs, portfolio risk, splits and trade management | No future data access; deterministic replay and metrics validated against fixtures |
| 8. API and authentication | FastAPI read/command endpoints, auth, audit and WebSockets | Authorization, validation and command audit tests pass; frontend never receives secrets |
| 9. Dashboard | Operations pages, symbol/strategy controls, health, state visibility, analytics | Responsive UI; dangerous close actions explicitly confirmed; persisted controls survive restart |
| 10. MT5 adapter | Windows terminal adapter, health, execution and reconciliation | Validated against demo account and mocks; stale/duplicate/disconnect cases fail safe |
| 11. Deployment and operations | VPS runbook, monitoring, backup/migrations, HTTPS-ready config | Recovery drills documented; service restarts reconcile positions; secrets externalized |
| 12. Live readiness review | Separate explicit review and gated operator procedure | No live enablement until broker-specific correctness, demo evidence and explicit authorization are complete |

## Phase 1 executable checks

- Install package and development dependencies on Python 3.12+.
- Validate default mode is paper and live mode is rejected.
- Validate symbol mapping ambiguity/unknown symbols and conservative config bounds.
- Run `python -m unittest discover -s tests -v`; add formatting, lint and type checks as those tools are provisioned.

## Environment discovery (2026-09-28)

The provided GitHub repository was empty and is now connected on `main`. Node.js v24.16.0 is available. System `python` and Docker are not on PATH; the bundled Python 3.12 runtime is available and is used for local checks. The Python package foundation is authored for Python 3.12+; Docker remains a later deployment concern.

## Progress

- **Phase 0:** architecture, strategy state machines, risk policy, database design and phase plan are committed.
- **Phase 1 foundation:** validated environment/TOML settings, paper-safe defaults, live-mode rejection, canonical symbol registry/mapping and unit tests are committed.
- **Phase 2 in progress:** strict confirmed pivots with explicit availability index, close-based structural break/CHoCH and same-candle wick/reclaim sweep primitives are implemented with tests. Regime classification, level clustering, equal highs/lows, retests, quote/bar adapters and broader no-look-ahead replay checks remain outstanding.
