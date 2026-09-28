# Market Data and Structure Baseline

This phase adds broker-neutral quote/bar contracts and deterministic structure calculations. All strategy-facing calculations consume completed candles; live providers must filter out the currently forming candle before passing bars into structure logic.

## Data contracts

- `Quote` carries canonical or mapped symbol, bid, ask, timezone-aware observation time and source. Prices must be finite and positive, ask must be at least bid. `require_fresh_quote` rejects a future timestamp or age beyond the configured limit.
- `MarketBar` carries symbol, timeframe, timezone-aware open time, validated OHLC candle, optional nonnegative finite volume and source.
- `MarketDataPort` is read-only and has no MT5 dependency. Historical and live adapters implement the same provider contract.

## Confirmed pivots

For `left=L` and `right=R`, a high pivot at index `i` must be strictly greater than every high at `[i-L, i)` and `(i, i+R]`; a low pivot is strictly lower than every low in those ranges. Any equal extreme rejects the candidate. The pivot becomes available at `i+R`, never at `i`. The caller supplies the current completed-bar index as `as_of_index`; unconfirmed pivots are excluded.

## Break and sweep

- Bullish BOS: completed close `> level + buffer`; bearish BOS: close `< level - buffer`. A wick alone does not count.
- CHoCH uses the same close-break predicate in the direction opposite the supplied previous structure direction. Selecting the relevant structure level remains the strategy state machine's responsibility and must be recorded.
- Sell-side sweep: low `< liquidity_level - penetration` and close `> liquidity_level`. Buy-side is the inverse. This baseline defines sweep and reclaim within the same completed candle. Multi-candle reclaim is a separate future configurable model.

## Level clusters and equal highs/lows

Only confirmed pivots are clustered. Pivots are sorted by price separately by kind. Each cluster is anchored at its lowest member and may contain a later price only if its distance from that anchor is no greater than `tolerance`; this avoids transitive chaining. Cluster price is the arithmetic mean. Equal-level status means at least two pivot touches; callers should use `minimum_touches` to require stronger significance. A cluster is usable no earlier than the latest constituent pivot's confirmation index.

## Retest

A retest is contact with the band `[level-tolerance, level+tolerance]` strictly after BOS and no later than `bos_index + expiry_bars`. Bullish close must remain at or above the lower band; bearish close must remain at or below the upper band. This function identifies contact/hold only. Entry still requires a distinct configurable confirmation event.

## Regime baseline

For trailing completed bars, normalized ATR is mean true range over `atr_window` divided by latest close. True range is the maximum of high-low, absolute high-previous-close and absolute low-previous-close. EMA uses `alpha=2/(period+1)` initialized from the oldest close in supplied history. EMA separation is `abs(fast-slow)/latest_close`. Directional persistence is `abs(sum(close changes))/sum(abs(close changes))` over the latest `slow_ema` changes, or zero when unchanged.

Precedence: abnormal if normalized ATR meets/exceeds `abnormal_normalized_atr`; otherwise trending if EMA separation and persistence each meet their minimum; otherwise ranging if persistence and separation are each at/below their maximum; otherwise neutral. All thresholds and windows are explicit `RegimeParameters`. These are baseline labels, not empirically validated trading rules.

## Limits and research parameters

The current utilities do not yet rank levels by recency, higher timeframe, reactions beyond pivot count, session provenance or prior-day/week levels. Those belong in a later level provider. Parameters requiring empirical comparison include pivot window sizes, equality/cluster tolerance, significant touch count, BOS/sweep buffers, regime windows/thresholds and retest tolerance/expiry. Historical code must query these functions using only bars at or before each decision time.
