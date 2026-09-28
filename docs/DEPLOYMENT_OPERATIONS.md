# Deployment and operations runbook

This reference deployment runs the API, dashboard, and PostgreSQL in Docker Compose. It binds published ports to loopback only. Put a maintained HTTPS reverse proxy in front of `127.0.0.1:8080` before remote access. The Windows MT5 terminal and its optional Python adapter remain a separate Windows-host service; they are not inside this Linux container stack.

## Host requirements and secrets

- Linux VPS with Docker Engine and the Docker Compose v2 plugin.
- A DNS name and TLS certificate at the external reverse proxy.
- A deployment-only `.env` beside `docker-compose.yml`; never commit it or copy development credentials into it.
- PostgreSQL password encoded as hex to avoid URL-reserved characters in the SQLAlchemy URL.
- Random HMAC key of at least 32 bytes, for example generated as hex with `openssl rand -hex 32`.

Create the deployment `.env` with `POSTGRES_PASSWORD` and `TRADING_PLATFORM_AUTH_SIGNING_KEY`. Restrict it to the service account (`chmod 600 .env`). Do not put broker passwords or terminal credentials in this file. `docker-compose.yml` keeps paper mode and the global execution switch off.

## Start and verify

```sh
docker compose config
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8080/api/v1/health
```

The API container applies Alembic migrations before starting the API. Keep a single API instance during startup/migration; do not scale API replicas until migrations have a separate one-shot deployment step. Bootstrap the first administrator inside the API container with `docker compose exec api python -m trading_platform.create_admin`. Create a strong unique password and store it in the operator password manager. Seed disabled references with `docker compose exec api python -m trading_platform.seed_reference_data`.

Route the HTTPS proxy to `127.0.0.1:8080`, preserve the `Upgrade`, `Connection`, and `Sec-WebSocket-Protocol` headers for `/api/v1/live`, and set a long read timeout for the authenticated WebSocket. The frontend and API share an origin through Nginx. Keep proxy request-size limits and rate limits on authentication routes. The health endpoint is unauthenticated and reports service/database health only; it is not a trading-readiness signal.

## Routine operations

- Check `docker compose ps`, `docker compose logs --since=15m api dashboard postgres`, disk space, database volume capacity, HTTPS expiry, and failed login/proxy metrics each operating day.
- Alert on unhealthy containers, database connection errors, unexpected process restarts, filesystem pressure, and missing backups. Keep the alert receiver outside this stack.
- Review the API audit events for operator changes. Keep user accounts least-privileged; only administrators reset emergency stop.
- Before an image upgrade, take and verify a database backup, review the migration diff, and schedule a maintenance window. Upgrade one API instance at a time. Afterward check health, login, read-only dashboard pages, and control-state persistence.
- No container setting or passing health check authorizes live mode. Keep this deployment in paper mode.

## Backup and restore drill

Create encrypted, off-host backups at least daily, with retention and access controls appropriate to the account data. Example manual PostgreSQL backup:

```sh
umask 077
docker compose exec -T postgres pg_dump -U jervis -Fc jervis > jervis-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Encrypt and copy the dump off-host immediately. A backup is unproven until a scheduled restore drill succeeds on an isolated host. For a drill, start an isolated PostgreSQL instance, restore with `pg_restore --clean --if-exists --no-owner`, point a temporary API at it, apply/check schema version, verify users and audit/control records, and exercise read-only API endpoints. Record duration, dump checksum, schema revision, and reviewer. Never restore over production as part of a drill.

## Restart and failure recovery

1. Keep the global execution control off and stop strategy producers.
2. Inspect API, database, host, and reverse-proxy logs; identify whether the outage left commands or operator actions uncertain.
3. Restore database connectivity and restart services with `docker compose up -d`; confirm database and API health.
4. For the separate Windows MT5 demo host, reconnect only after confirming the expected demo server and allow-listed account. Query broker open positions/orders, compare with persisted records, and resolve every mismatch manually before resuming demo operation.
5. Convert the demo position snapshot through `MT5DemoAdapter.list_external_positions` and pass it to the shared `reconcile_positions` transaction. Resolve every discovered, missing, or changed position explicitly; audit the recovery actions and leave the global switch off until reviewed.

The current MT5 adapter has no durable cross-process order-intent ledger and this repository has not completed a broker demo recovery drill. Therefore do not use its order method unattended; the adapter is isolated and is not reachable through the dashboard. Production recovery and broker restart reconciliation remain an operational acceptance item.

## Windows MT5 demo host

Install the project with `pip install -e '.[mt5]'` in a supported 64-bit Windows Python environment and separately install/configure the MT5 terminal. Log in to a broker demo account through the terminal UI. Configure `TRADING_PLATFORM_MT5_DEMO_SERVER`, the required positive `TRADING_PLATFORM_MT5_DEMO_ACCOUNT_IDS` allow-list, and optionally `TRADING_PLATFORM_MT5_TERMINAL_PATH`. `TRADING_PLATFORM_MT5_MAX_QUOTE_AGE_SECONDS` defaults to 5 and may be raised only to account for a measured, documented clock offset; the adapter caps it at 60 seconds. The adapter contains no username/password fields. Validate quotes, symbol contract sizes, tick values, volume steps, stop-level rules, execution filling modes, currency conversion, and reconciliation against that specific broker before any demo order experiment. The adapter is not a substitute for the central risk service.
