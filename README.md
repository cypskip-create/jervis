# Quant MT5 Platform

Research-first multi-market trading platform centered on MT5. The initial repository phase establishes architecture and safety contracts. It does not place broker orders; fresh installs default to paper mode.

## Project status

Phases 0 through 9 have a baseline: centralized risk, proposal-only strategy engines, paper execution/reconciliation, deterministic backtesting, an authenticated operations API, and a React dashboard. Broker integration, PostgreSQL production validation, and market-specific session/key-level providers remain future work. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for acceptance criteria and [ARCHITECTURE.md](ARCHITECTURE.md) for service boundaries.

## Requirements

- Python 3.12+
- Git
- Node.js 20+ for the dashboard
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

## API and dashboard

Copy `.env.example` to `.env`, replace `TRADING_PLATFORM_AUTH_SIGNING_KEY` with at least 32 random bytes, and set the database URL and allowed dashboard origin. Apply migrations and seed disabled reference records:

```powershell
alembic upgrade head
python -m trading_platform.seed_reference_data
python -m trading_platform.create_admin
uvicorn trading_platform.api:app --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd dashboard
npm install
npm run dev
```

The initial admin is created interactively; its password is stored as a scrypt hash. API tokens expire after eight hours. Keep the API bound to localhost behind an HTTPS reverse proxy when accessed remotely, restrict CORS to the dashboard origin, and keep the signing key outside source control. Emergency-stop reset requires an administrator. The “disable and close” policy remains unavailable until a broker execution adapter can safely validate and perform closes.

## Backtesting

Use `trading_platform.backtesting.run_backtest` with timezone-aware, bid OHLC bars and an explicit proposal callback. The callback receives only history through the current timestamp. Proposals fill at the next bar open, spread is applied to executable sides, slippage and round-trip commission are modeled, and when a bar touches both stop and target the stop wins. Remaining trades are marked out at the last available close. Each result includes a canonical input data hash, trade journal, portfolio/trade/day/week/drawdown risk gates, chronological IS/OOS/final splitting helpers, rolling walk-forward windows, and return/drawdown/cost metrics grouped by instrument, strategy, session, and regime. It is a bar-based research simulator, not tick-level fill modeling. Maximum drawdown uses closed trade equity and can understate intratrade drawdown; CAGR is omitted for periods shorter than a year. Persist results with `persist_backtest` inside the caller's database transaction.

## Repository map

```text
src/trading_platform/  shared package and validated settings
migrations/            Alembic migration environment and revisions
config/                safe non-secret defaults
docs/                  future runbooks and design references
tests/                 deterministic unit/integration tests
mt5/                   future native MT5 artifacts/adapters
dashboard/             React/TypeScript operations interface
```

See [STRATEGIES.md](STRATEGIES.md), [RISK_MANAGEMENT.md](RISK_MANAGEMENT.md), and [DATABASE_DESIGN.md](DATABASE_DESIGN.md) for the initial contracts.
See [MARKET_STRUCTURE.md](MARKET_STRUCTURE.md) for exact pivot, sweep, retest and regime definitions.
