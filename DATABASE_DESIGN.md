# Database Design

PostgreSQL is the production target. Local SQLite can support isolated development but must not weaken locking, numeric precision or migration semantics. Use Alembic migrations and SQLAlchemy 2.x; store timestamps in UTC and use decimal/numeric values for money and prices.

## Core entities

- `accounts`: broker/server/account identifier (credential-free), currency, mode, metadata.
- `symbols`: canonical symbol, asset class, precision/tick metadata source, enabled state and disable policy.
- `symbol_mappings`: account/broker-specific mapping, validity interval, enabled state.
- `strategies`, `strategy_versions`, `strategy_configs`: immutable version and parameter snapshot/hash; active config pointer is mutable and audited.
- `signals`: unique signal ID, canonical symbol, strategy version/config, timestamps, direction, candidate levels, RR, evidence JSON and state. Rejected proposals remain records with reason codes.
- `risk_snapshots`, `risk_reservations`: portfolio/exposure state, evaluated limits and atomic reservation lifecycle.
- `orders`: idempotency key, signal reference, request/response, status and broker ticket identifiers.
- `positions`: broker ticket, canonical/mapped symbol, side, volume, entry/SL/TP, current management state and reconciliation timestamps.
- `trades`: completed lifecycle with actual fills, fees, swap, P&L/R, MFE/MAE, exit reason and immutable strategy/config references.
- `market_states`, `decision_events`, `system_events`, `audit_events`: timestamped state transitions, decisions, health and operator mutations.
- `bot_settings`: versioned configuration revisions; secret values are never stored here.
- `backtests`, `backtest_trades`, `optimization_runs`: data provenance, split/walk-forward boundaries, code/config/data hashes and metrics.

## Integrity and concurrency

Use unique constraints on signal IDs, order idempotency keys and broker ticket/account combinations. A transaction locks the account risk row (or equivalent serializable reservation boundary) while calculating and inserting reservations. Reservation states are `held`, `submitted`, `filled`, `released`, `expired`; reconciliation resolves ambiguous submissions before any retry. Keep append-only audit/decision records; corrections append new events.

## Data handling

Use `NUMERIC` for prices, volumes and monetary values. Persist source time, ingest time and timezone-aware timestamps. JSONB is suitable for extensible evidence/metadata, not for core relational identities. Secrets and credentials belong in an OS secret store/environment, never in database settings, logs, frontend payloads or git.

## Initial migrations

Phase 1 creates only the package/configuration foundation. Database connectivity and first migrations follow after domain contracts and risk reservation semantics are implemented and reviewable.
