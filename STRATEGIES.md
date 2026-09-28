# Strategy State Machines (research baselines)

All strategies are proposal generators. Every threshold below is configuration, not a claim of edge. No setup may use an unconfirmed future pivot. A pivot with `left=L, right=R` becomes usable only after R bars have closed; historical signals are timestamped no earlier than that confirmation bar.

## Shared objective definitions

- **Confirmed pivot high/low:** bar extreme strictly exceeds/is below the corresponding `L` bars before and `R` bars after; tie policy (`reject` by default) is configurable. Right-side bars are confirmation delay, never available at the pivot timestamp.
- **Equal highs/lows:** two or more confirmed pivots within `tolerance = max(min_ticks, ATR × tolerance_atr)` over a configurable lookback; cluster count is configurable.
- **Break of structure (BOS):** completed close beyond the latest relevant confirmed pivot by `break_buffer` ticks/ATR. Wick-only crossing does not qualify.
- **Change of character (CHoCH):** first qualifying close through the opposing structural pivot after a directional context/sweep; requires a configurable prior structure direction.
- **Liquidity sweep:** quote/bar extreme crosses a known liquidity level by a minimum buffer and closes back on the unswept side within that bar (or configured reclaim window). Wick excursion is recorded.
- **Retest:** after a confirmed close break, price revisits the broken level within a tolerance band before expiry; no entry on the BOS bar unless the configured minimum delay is zero.
- **Displacement:** body/range and close-location thresholds, optionally normalized by ATR; all values configurable. **Rejection:** wick/body ratio plus close location relative to candle range. **Engulfing:** current real body contains prior real body and closes in the intended direction.
- **Support/resistance:** clusters of confirmed pivots/reactions, ranked by reaction count, recency and timeframe. Minimum reactions, clustering tolerance and recency decay are parameters; a single arbitrary candle is insufficient.
- **Ranging/trending/abnormal:** quantified using normalized ATR, structure direction/persistence and configurable trend-strength thresholds. Undefined evidence is neutral.

## Gold (`gold_v1.0.0`)

Configurable context timeframe(s), setup M15 and execution M5 by default. States: `CONTEXT → WAIT_PULLBACK → WAIT_CONFIRMATION → PROPOSED → INVALID/EXPIRED`.

1. **CONTEXT:** require aligned higher-timeframe structure and EMA(9/21) ordering, slope and minimum separation. Neutral/mixed structure blocks trend-following setup.
2. **WAIT_PULLBACK:** price enters a support/resistance zone built from confirmed multi-timeframe pivots/reactions. Optional Fibonacci confluence derives retracement from the most recent confirmed impulse swing; candidate levels default 0.618/0.786/0.886. Fib touch alone never advances state.
3. **WAIT_CONFIRMATION:** require configured M15/M5 close-based confirmation (structure break/displacement/rejection). Record each gate and its evidence.
4. **PROPOSED:** entry after confirmation, structural invalidation beyond the relevant swing/zone plus spread/volatility buffer. Target is the next opposing structural/liquidity zone. Reject if achievable RR is below configurable baseline 2.5. No target stretching to pass RR.
5. Invalidate on context reversal, structural invalidation, maximum setup age, or target becoming unreachable under configured rules.

Empirical parameters: EMA periods/slope window; pivot L/R; zone clustering/reaction count; fib swing selection/level tolerance; confirmation model; buffers; expiry; minimum RR; target selection and session filters.

## NASDAQ (`nasdaq_sweep_v1.0.0`)

States: `CONTEXT → WAIT_SWEEP → WAIT_CHOCH → WAIT_BOS → WAIT_RETEST → WAIT_CONFIRMATION → PROPOSED → INVALID/EXPIRED`.

1. **CONTEXT:** 4H confirmed HH/HL or LH/LL swing sequence defines bullish/bearish; mixed/ranging is neutral and blocks trend mode. 1H levels include confirmed pivots, PDH/PDL, prior-week high/low and configured reaction zones. 15M bias must agree unless counter-trend mode is explicitly enabled (off by default).
2. **WAIT_SWEEP:** require wick penetration and reclaim of a meaningful level: longs take sell-side liquidity below a low; shorts take buy-side liquidity above a high. Sweep alone never enters.
3. **WAIT_CHOCH:** after sell-side sweep require a 5M close above the relevant confirmed bearish structural high for long; after buy-side sweep require close below relevant bullish structural low for short. Exact reference-pivot selection is deterministic and recorded.
4. **WAIT_BOS:** require a subsequent close beyond the next qualifying structure in the intended direction within `bos_expiry_bars`.
5. **WAIT_RETEST:** revisit BOS level/zone within configured tick/ATR tolerance and before retest expiry. Never chase a missed retest.
6. **WAIT_CONFIRMATION:** require one configured rejection, engulfing or displacement model. Then propose entry; SL beyond sweep extreme plus buffer; TP at next opposing liquidity/structure. Reject if genuine target gives RR under configurable default 2.0 (2.0/3.0 research variants).
7. Any opposite structural break, expired sequence, changed context or invalid stop/target resets/invalidate state.

Empirical parameters: swing L/R; trend sequence definition; level salience and equality tolerance; sweep buffer/reclaim; CHoCH/BOS pivot selection and buffers; BOS/retest expiry; retest tolerance; confirmation thresholds; target ranking; stop buffer; min RR; session and volatility filters.

## Forex (`forex_v1.0.0`)

Separate setup IDs: `fx_liquidity_pullback` and `fx_trend_continuation`; report and backtest independently. Timeframe defaults D1 context, H4 regime/trend, H1 structure, M15 setup, M5 execution. Sessions use IANA time zones and DST-aware boundaries.

Common regime states: `TRENDING`, `RANGING`, `ABNORMAL_VOLATILITY`, `NEUTRAL`. Classification combines structure persistence, normalized ATR and EMA slope/separation under explicit configurable thresholds. Abnormal volatility blocks entries by default.

Common context/location levels: PDH/PDL, prior-week high/low, session highs/lows, confirmed H1 pivots/equal highs-lows, clustered S/R and optional round-number bands. Level provenance and source timeframe are retained.

- **Setup A liquidity/pullback:** `CONTEXT → WAIT_LOCATION → WAIT_SWEEP → WAIT_CHOCH_M15 → WAIT_BOS_M5 → WAIT_RETEST → WAIT_CONFIRMATION → PROPOSED`. Require aligned H4/H1 trend, meaningful pullback location, opposite-side liquidity sweep, close-confirmed M15 CHoCH/displacement, M5 BOS, retest and confirmation. Fib 0.618/0.786/0.886 is optional confluence only.
- **Setup B continuation:** `CONTEXT → WAIT_PULLBACK → WAIT_LOCATION → WAIT_COMPRESSION_OR_REJECTION → WAIT_BOS → WAIT_RETEST → WAIT_CONFIRMATION → PROPOSED`. Require aligned trend, important level, quantified compression/rejection, close BOS, retest and confirmation. A sweep is not mandatory.
- **Proposal:** structural invalidation plus spread/volatility buffer; target nearest genuine opposing structure/liquidity; enforce configurable achievable RR. Reject when regime, quote freshness, session/news policy or risk disallows entry.

Empirical parameters: regime windows/thresholds; timeframe hierarchy; pivot and equal-level definitions; level ranking; session timezone/window; pullback depth/location; compression/rejection/displacement; confirmation; optional fib tolerance; buffers; expiry; minimum RR; news windows/provider.

## Reproducibility

Every decision stores state transitions, evidence values, strategy version, parameter snapshot/hash, data timestamps and rejection reason. Backtests must replay only information available at each decision time. Definitions and parameters are unit-test targets before live use.
