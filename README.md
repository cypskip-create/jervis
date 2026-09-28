# Quant MT5 Platform

Research-first multi-market trading platform centered on MT5. The initial repository phase establishes architecture and safety contracts. It does not place broker orders; fresh installs default to paper mode.

## Project status

Phases 0 through 3 have a tested baseline. PostgreSQL concurrency verification and market-specific session/key-level providers remain deployment/research work. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for acceptance criteria and [ARCHITECTURE.md](ARCHITECTURE.md) for service boundaries.

## Requirements

- Python 3.12+
- Git
- Node.js 20+ when the dashboard phase begins
- PostgreSQL and a Windows MT5 terminal only for later integration/deployment phases

## Development setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Configuration reads `config/defaults.toml`, then optional `.env`, then process environment. Copy `.env.example` to `.env` for local overrides; never put secrets in tracked files. Execution mode is `paper` by default. Live mode is intentionally unsupported.

Apply the local database migration with `alembic upgrade head`. Set `TRADING_PLATFORM_DATABASE_URL` to use a PostgreSQL URL and install `.[postgres]`; local development defaults to SQLite.

## Repository map

```text
src/trading_platform/  shared package and validated settings
migrations/            Alembic migration environment and revisions
config/                safe non-secret defaults
docs/                  future runbooks and design references
tests/                 deterministic unit/integration tests
mt5/                   future native MT5 artifacts/adapters
dashboard/             future React application
```

See [STRATEGIES.md](STRATEGIES.md), [RISK_MANAGEMENT.md](RISK_MANAGEMENT.md), and [DATABASE_DESIGN.md](DATABASE_DESIGN.md) for the initial contracts.
See [MARKET_STRUCTURE.md](MARKET_STRUCTURE.md) for exact pivot, sweep, retest and regime definitions.
