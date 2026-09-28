# Risk Management

Risk is a central gate, independent of strategy. Defaults are conservative and PAPER-only; risk values must be explicitly set for any account. No live order path exists in the initial implementation.

## Approval sequence

1. Validate mode, global stop, account identity, symbol/strategy/class switches and disable policy.
2. Validate fresh executable quote, market hours, spread, symbol trading constraints and candidate expiry.
3. Validate direction, finite positive entry/SL/TP, stop side, achievable target and minimum RR.
4. Compute worst-case monetary loss to stop from broker tick size/value, contract/currency conversion, volume step, commission and configured slippage allowance. Reject unknown or invalid instrument metadata; never guess tick value.
5. Check max per-trade, daily/weekly loss, drawdown, concurrent trades, portfolio reserved/open risk, class/symbol/currency exposure, margin and correlation policy.
6. Atomically reserve risk against a fresh portfolio snapshot. Filled and pending platform reservations remain the durable source for managed risk and exposure; snapshot baseline fields contain only manual/untracked positions so exposure is not counted twice. Concurrent proposals cannot each consume the same headroom.
7. Immediately revalidate mutable switches/quote/limits before execution. Submit only once with idempotency key; reconcile outcome before retry.

## Limits

Configuration supports risk/trade, daily and weekly loss, peak-equity drawdown, simultaneous positions, total portfolio risk, asset-class and symbol risk, currency exposure and correlated exposure. Limits, reset timezone, realized/unrealized treatment, open-risk treatment and breach response are explicit. Breach defaults to blocking new trades and raising an event; it must not silently liquidate positions.

## Sizing

`risk_budget = equity × risk_fraction`; `volume = risk_budget / estimated_loss_per_lot_at_SL`, then floor to broker volume step and clamp only if configured constraints still keep risk within budget. If minimum lot exceeds budget, reject. Include fees and conservative slippage where known. Unknown conversion/tick metadata, zero tick value, stale quote, non-finite values, or invalid stop means reject.

## FX exposure

Represent signed base/quote currency notionals normalized to account currency, then aggregate by currency and optionally apply configured correlation groups. An EURUSD long adds EUR and reduces USD exposure under the chosen convention; the convention and conversion source must be consistent and displayed. Exposure limits are checked atomically with reservations.

## Position management and disable behavior

Baseline is original SL/TP. Break-even, partial close, structure trail, ATR trail and fixed trail are separate experiment modules, individually configured and replayable. Never widen a stop. `disable_new` blocks entries and leaves positions under current management; `disable_after_position` blocks future entries after current exposure resolves; `disable_and_close` requires explicit confirmation and an audited command.

## Emergency stop and modes

Global stop blocks new trades. A close-all action is a separate stronger command and confirmation. Modes are `backtest`, `paper`, `demo`, `live`; fresh configuration is paper. `live` is rejected until a future explicitly reviewed implementation and operator procedure exist.
