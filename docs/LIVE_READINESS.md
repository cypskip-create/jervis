# Live readiness review — NOT READY

**Decision:** NO-GO. Live trading is unsupported by application settings and remains unavailable. This review does not grant permission to trade or switch execution mode.

The project has no supplied broker, terminal, account, tick/contract specification, or demo execution evidence. A mock adapter test or healthy dashboard cannot establish broker correctness. Do not connect this foundation to a live account.

## Evidence gates

Every item needs a dated artifact, its source, the reviewer, and a recorded result. “Implemented” or “unit tests pass” alone is not evidence of broker-specific correctness.

- [ ] Broker account/server identity and demo/live mode independently verified; exact symbol aliases, sessions, contract size, tick size/value, volume constraints, margin and stop/freeze levels recorded.
- [ ] Quotes and bars compared against broker terminal data; UTC/time-zone, missing bars, stale ticks, spread spikes and market closures validated.
- [ ] Currency conversion and risk sizing validated for every enabled instrument and account currency, including conversion outages and adverse movement.
- [ ] Broker demo executions cover accepted/rejected/partial fills, requotes, slippage, stops/targets, trading hours, and commission/swap treatment.
- [ ] Durable order-intent/idempotency and uncertain-timeout behavior proven across process restart; open orders, deals and positions reconcile without duplicates or silent closes.
- [ ] Disconnect, terminal crash, VPS restart, network partition, database recovery and disaster-restore drills passed with position mismatches failing closed.
- [ ] Chronological holdout, multiple walk-forward windows, costs, slippage, regime/session breakdowns and overfit review accepted by an independent reviewer.
- [ ] Portfolio, daily/weekly loss, drawdown, concentration, correlation, max-position and emergency procedures rehearsed with explicit thresholds and tested stop behavior.
- [ ] HTTPS, secrets management/rotation, access review, audit retention, host patching, firewall, backups, monitoring and alert receipt verified.
- [ ] Independent operator review and explicit authorization record completed for a separately defined limited pilot, with capital, symbols, duration, and immediate stop conditions.

## Current implementation gaps

- Phase 10 supplies an optional Windows adapter that checks a configured demo server/account, quote age, and basic duplicate open-order tags. It has not been exercised against any terminal or broker. Its tag lookup is not a durable idempotency ledger, and its current order method must not be run unattended.
- Phase 11 supplies a Compose deployment and written backup/recovery runbook. Docker was not available in the development environment, so image build, Compose validation, HTTPS proxy, PostgreSQL concurrency, and restore drill remain unverified.
- Phase 12 supplies a fail-closed evidence checklist/report only. No evidence gate is approved. Settings rejects `mode=live`; completing a report does not change that behavior.

## Review command

Run `python -m trading_platform.readiness` for the current checklist summary or add `--json` for automation. It defaults to `NOT_READY`. Any future review must attach real evidence and an independent sign-off; the tool does not activate or configure execution.

## Pilot authorization boundary

Even after every gate has evidence, stop at a separate change review. Define a demo-to-live migration plan, hard capital and daily-loss limits, symbols, operator schedule, alert response, and written authorization. Require code review and deployment approval for any future live-capable implementation. Keep the platform unavailable for live execution until that separate process is complete.
