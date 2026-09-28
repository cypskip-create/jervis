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
| 10. MT5 adapter | Optional Windows terminal adapter, demo-only account checks, quote and explicit execution call | Mock checks pass; broker demo and restart/reconciliation validation outstanding |
| 11. Deployment and operations | Compose stack, loopback bindings, health checks, VPS backup/recovery and HTTPS proxy runbook | Config/build and recovery drills documented; container/database restore and restart drills remain to be run |
| 12. Live readiness review | Fail-closed evidence checklist/report and gated operator procedure | Current decision is NOT READY; live mode remains rejected regardless of checklist output |

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
- **Phase 2 baseline complete:** quote/bar contracts, quote freshness, strict confirmed pivots with explicit availability index, close-based BOS/CHoCH, same-candle wick/reclaim sweeps, deterministic pivot clustering/equal-level labels, retest-window checks and quantitative regime classification are implemented and tested. Session-aware higher-level level providers and historical event replay remain future work.
- **Phase 3 baseline complete:** SQLAlchemy models, three Alembic revisions and transaction-scoped risk reservation/release operations are implemented. SQLite migration upgrade/downgrade and schema/repository tests pass. PostgreSQL integration and concurrent reservation testing against a real PostgreSQL service remain outstanding.
- **Phase 4 baseline complete:** Decimal position sizing, fail-closed account/symbol/strategy/quote gates, daily/weekly/drawdown and portfolio/class/symbol/currency/correlation exposure limits, and transactional risk/exposure/position-slot reservations are implemented. Concurrency semantics rely on PostgreSQL account-row locks; validation against a live PostgreSQL service remains outstanding.
- **Phase 5 baseline complete:** Gold, NASDAQ sweep, Forex liquidity (A), and separate Forex continuation (B) state machines emit explainable proposals only. Tests exercise transitions, expiry/no-chase, mathematical confirmations, context gates and RR checks. Market-specific session/key-level providers remain research work.
- **Phase 6 baseline complete:** Paper-only idempotent execution, quote/TTL/spread checks, SL/TP lifecycle with MFE/MAE and R journaling, and fail-safe position reconciliation are implemented. No MT5/broker order adapter exists yet.
- **Phase 7 baseline implemented:** Bid-OHLC chronological replay with as-of histories, next-bar fills, spread/commission/slippage, conservative stop-first same-bar resolution, central position sizing/portfolio risk caps, reproducible input hashes, chronological partitions, rolling walk-forward windows, trade journal and summary metrics. It is bar-based (not tick-level); reported drawdown is closed-trade based.
- **Phase 8 baseline implemented:** FastAPI health/read/command endpoints, scrypt account passwords, short-lived HMAC bearer tokens, operator/admin roles, persistent versioned controls, audit events, CORS allow-list and authenticated WebSocket snapshots. Admin creation is interactive and live mode remains unsupported. Production HTTPS, proxy rate limiting, and PostgreSQL concurrency validation remain deployment requirements.
- **Phase 9 baseline implemented:** Responsive React operations dashboard for health, global/emergency controls, asset-class/symbol/strategy switches, paper positions, and persisted backtest metrics. The TypeScript production build passes. Dangerous disable-and-close is explicitly rejected pending a validated close adapter; production browser accessibility review remains outstanding.
- **Phase 10 implementation baseline:** Optional/lazy Windows MT5 integration supports an expected demo server plus account allow-list, quote freshness, broker volume/fill checks, explicit market execution and known duplicate-tag detection. Mock tests cover safe gates. No terminal, broker, demo account, durable order-intent ledger, or broker reconciliation drill was available; do not use unattended.
- **Phase 11 implementation baseline:** Added API/dashboard/PostgreSQL Compose deployment, loopback-only host ports, container health checks, Nginx API/WebSocket proxy, and VPS backup, HTTPS, migration and restart runbook. Docker was unavailable in this environment, so image builds, Compose configuration, PostgreSQL, HTTPS, and recovery drills are unverified.
- **Phase 12 review:** **NOT READY / NO-GO.** Added an evidence checklist and report command. Broker-specific correctness, demo artifacts, operational/security drills, independent review, and explicit authorization are absent. Live mode remains rejected by settings; checklist completion does not enable it.
