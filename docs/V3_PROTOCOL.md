# V3 — AGGRESSIVE INTRADAY JEV ARENA: pre-registered protocol

Written 2026-09-23, **before any V3 strategy existed or any V3 result was seen**. Nothing below is
changed after results; a change is a new protocol version with a stated reason. V1, V2, the Jev V1
experiment (527331f5d0c7), the V2 DEVELOPMENT/TEST runs, the V2 multi-year validation (9688867c33bc,
0/3 passed) and the running forward experiment (fx-f0a160e7bec7) are untouched.

## The question

    WHICH STRATEGY + WHICH COIN + WHICH SMALL TIMEFRAME + WHICH JEV POLICY + WHICH RISK PROFILE
    produces an aggressive but survivable after-cost edge -- and does Jev make that bot better?

If no bot qualifies, the answer is **ADVANCED SET = NONE**. That is a correct result.

## Venue

Real-money venue: **BYBIT LINEAR** (confirmed by the operator 2026-09-23). V3 is therefore costed
and filtered as Bybit: taker 0.055% / maker 0.02% (FeeSchedule `bybit_linear`), Bybit tick size,
minimum order quantity, quantity step and minimum order value from Bybit's public instruments
endpoint, snapshotted into the run. The historical **price tape** is Binance USD-M 1m klines from the
verified public archive (data.binance.vision, SHA-256 checked), with Binance funding from the same
archive; both are labelled wherever a result is shown. Bybit execution validation remains a
separate gate before any live promotion.

## Identity

A competitor is: strategy version · coin · signal timeframe · context timeframes · execution
timeframe (1m) · Jev policy (none / JEV_POLICY_V2) · risk profile (AGGRESSIVE_V3) · leverage ceiling
(20x) · parameter fingerprint. Display keys: `S31-SUI-5m-JEV2@20x` and its matched
`S31-SUI-5m-CONTROL@20x`. One bot = one coin, forever.

## Strategy families (V3.0)

| id | family | hypothesis |
|---|---|---|
| S31 | momentum breakout | a break of a compressed signal-TF range, with the context trend and a volume surge, extends far enough to beat costs |
| S32 | volatility expansion | the first wide, decisive bar out of a Bollinger-inside-Keltner squeeze starts a directional move |
| S33 | pullback continuation | in a context trend, a pullback to the signal-TF EMA with an RSI reset and a resumption bar continues toward the prior swing |
| S34 | range rejection | in a flat context, a rejection wick at a validated range extreme reverts toward the range midpoint |
| S35 | flow momentum (microstructure) | a burst of one-sided taker flow (Binance taker-buy volume) on a volume and trade-count spike, breaking a local extreme, continues briefly |
| S36 | fast mean reversion | a stretched move (> 2.5 ATR from the EMA, RSI extreme) that the context does not support, closing off its extreme, reverts toward the EMA |

Every V3 entry states `expected_move_pct` (derived from its own target/objective structure — never a
constant) and `signal_quality` in [0, 1] (mean of named, bounded factors). Stops are at least 0.50%
of price (the frozen fee gate refuses a trade whose round trip exceeds 25% of R; at Bybit's 0.055%
taker that is any stop under 0.44%) and at most 2.0%.

## Timeframes

| signal | context (fast, slow) | role |
|---|---|---|
| 3m | 15m, 1h | primary |
| 5m | 15m, 1h | primary |
| 15m | 1h, 4h | primary |
| 30m | 1h, 4h | benchmark / control timeframe |
| 1m | 5m, 15m | EXPERIMENTAL, control scan only (execution quality: next-1m-open proxy, no sub-minute data; never advanced) |

Execution/base data is always the 1m tape: higher timeframes are aggregated from CLOSED 1m bars, a
signal uses only closed bars, and an order fills after signal + order latency at the next 1m bar's
open — never inside the bar that produced the signal.

## Data split (docs/DATASET_SPLIT_V3.md)

* DEVELOPMENT 2025-11-01 → 2026-04-30 (181 days; warmup 2025-10, no entries).
* TEST (holdout, declared, NOT run in this phase) 2026-05-01 → 2026-08-31 (warmup 2026-04).
* FORWARD: only data after a bot's freeze.

## Coins (selection rule, applied to live venue metadata, never to results)

USDT perpetual trading on BOTH Binance USD-M and Bybit linear; listed on Binance by 2025-06-30;
never used by any PaperLab experiment (excludes BTC ETH SOL BNB XRP DOGE ADA LINK AVAX LTC); tick
≤ 1.10 bp of price on both venues; Bybit smallest legal order ≤ 16 USDT; Bybit 24h turnover ≥ 50M
USDT; then the 10 largest by Binance 24h quote volume. Result (2026-09-23): ZEC, UNI, HYPE, SUI, ARB,
TAO, PENGU, ENA, AAVE, ZRO.

## Risk profile AGGRESSIVE_V3

Starting balance 20 USDT · normal risk 1.0% · strong setup 1.5% · ATTACK up to 2.0% · hard cap
2.0% per trade · leverage ceiling 20x, each position gets only the leverage it needs.

| state | ORDINARY | STRONG (q ≥ 0.70, e/c ≥ 3) | EXCEPTIONAL (q ≥ 0.85, e/c ≥ 3) |
|---|---|---|---|
| NORMAL | 1.0% | 1.5% | 1.5% |
| ATTACK (≥10 recent trades, recent exp ≥ +0.10R, DD ≤ 10%) | 1.0% | 1.5% | 2.0% |
| DEFENSIVE (DD ≥ 18% or recent exp ≤ −0.25R) | 0.5% | 0.5% | 0.5% |
| HALTED (DD ≥ 30% from peak) | 0 | 0 | 0 |

The existing safety controls are preserved unchanged: RiskManager floor at 75% of the starting
balance, the fee gate (fees ≤ 25% of R), exchange minimums, margin checks. A +JEV bot sizes at
normal risk × the Jev multiplier × its health factor (DEFENSIVE 0.5, HALTED 0), under the same cap.

## Pipeline and cost gate

    strategy candidate → legality / min-notional → deterministic cost gate → JEV → RiskManager → execution

EDGE_COST_RATIO = expected_move_bps / expected round-trip cost bps, where the round trip is two
Bybit taker fees + the execution model's slippage on both legs (half of the one-tick spread, its
volatility widening and size impact — exactly what a replay fill is charged). A candidate
needs ≥ 2.0 to be considered at all; STRONG/ATTACK sizing (control) and a Jev ATTACK both need ≥ 3.0.
Jev is never called for a candidate that failed legality or the cost gate, and never every candle.

## JEV_POLICY_V2 (JEV_PROMPT_V2, JEV_STATE_V2) — V1 stays frozen

Jev (typesafe/jev-1.13, pinned) answers three typed questions about a timestamp-safe state (no dates,
no absolute prices, only closed bars and settled funding): `win` (noul: will this trade close with a
net profit after costs?), `action` (choice SKIP / DEFENSIVE / NORMAL / ATTACK) and `setup_quality`
(score 0–4). The state carries the signal (direction, family, quality, expected move, stop and
target distance, reward/risk), the fast market (1m/3m/5m/15m returns, momentum, acceleration, ATR
and realized volatility, range expansion, relative volume, taker-flow ratio, distance to local
support/resistance), the multi-timeframe context, execution (spread, fee, slippage, round-trip cost,
minimum legal notional, proposed notional, effective leverage), derivatives (settled funding and its
recent direction; open interest is NOT available historically and is not fabricated) and bot health.

Deterministic translation: level = Jev's `action` choice; ATTACK requires EDGE_COST_RATIO ≥ 3.0
(else NORMAL). Multipliers on normal risk: SKIP 0.00×, DEFENSIVE 0.50×, NORMAL 1.00×, ATTACK 1.50×,
ATTACK 2.00× when Jev's own P(ATTACK) ≥ 0.60. A failed request is SKIP (never control behaviour).
Jev never bypasses the RiskManager: the resized order goes back through it.

## Baselines for every Jev bot

CONTROL (same strategy, no Jev, AGGRESSIVE_V3 tiers) · ALWAYS-TAKE (the Jev pipeline with a constant
NORMAL verdict) · ALWAYS-SKIP (never trades: 0 net) · RANDOM-FILTER (20 deterministic seeds; each
candidate gets a random action drawn from the Jev bot's own action distribution, through the same
deterministic ATTACK rule, i.e. the same acceptance rate and sizing mix) plus a 2,000-permutation
trade-level test of Jev's multipliers against its candidates' outcomes.

## Participation (AGGRESSIVE_PARTICIPATION)

Minimum closed trades over the window = max(absolute, rate × days / 30):

| TF | absolute | per 30 days | over DEV (181 d) |
|---|---|---|---|
| 1m | 150 | 75 | 453 |
| 3m | 100 | 50 | 302 |
| 5m | 60 | 30 | 181 |
| 15m | 30 | 15 | 91 |
| 30m | 20 | 10 | 61 |

(The per-30-day rates read the requested minimums as a ~60-day discovery window.) A +JEV bot must
also reach the same number of Jev-accepted trades and a skip rate ≤ 80%. Below that:
INSUFFICIENT_AGGRESSIVE_PARTICIPATION. Jev is never forced to trade to reach it.

## Discovery gates (ADVANCE → HOLDOUT TEST; never live)

Strategy (any bot): participation · net PnL > 0 after fees, spread, slippage, funding · net
expectancy > 0 R · profit factor ≥ 1.10 · max drawdown ≤ 30% and never halted · no liquidation ·
still positive without its best 3 trades.

Jev value (a +JEV bot additionally): net > matched CONTROL · net > ALWAYS-SKIP (0) · net above the
RANDOM-FILTER 90th percentile · AUC of P(win) with a 95% interval whose lower bound is above 0.50
(point estimate, interval and sample size always shown; no other AUC threshold) · skip rate ≤ 80% ·
API error rate ≤ 2% · p95 request latency ≤ 2,000 ms.

## Ranking (separate from the gates)

Percentile ranks, averaged, of: return / max(drawdown, 5%), net expectancy R, profit factor (capped
at 3), trades per 30 days (capped at 2× its timeframe's minimum), realized cost efficiency, Jev
contribution (vs control; 0 for controls) and regime consistency. A bot with drawdown ≥ 30% or a
liquidation is capped at 0.25 and flagged CATASTROPHIC, so a huge risky return cannot rank first.

## Field

Control scan: 6 families × {3m, 5m, 15m, 30m} × 10 coins = 240 CONTROL bots (+ 60 experimental 1m).
Jev field: one coin per family × timeframe (24 pairs, 6 per timeframe), chosen by ACTIVITY only — the
most legal, cost-gate-passing candidates in the control scan, each family using a different coin on
each timeframe (greedy in the order 3m, 5m, 15m, 30m; ties alphabetical) — never by PnL. A pair
needs at least its timeframe's participation minimum in such candidates, else it is not entered.
At least 10 active Jev bots are required, or the run reports INSUFFICIENT_COMPETITORS.

## Amendment 1 (2026-09-23, after the control scan's ACTIVITY counts, before any Jev result was read)

The pre-registered field rule filled only 9 of 24 family × timeframe slots (the most active coin in 15
slots had fewer legal, cost-gate-passing entries than its timeframe's participation minimum, e.g. 3m
slots at 182–265 vs 302). By the rule above that run is **INSUFFICIENT_COMPETITORS**, and that stays on
record. Because the question "does Jev make a bot better?" needs at least 10 (preferably 15–30) Jev
pairs, the field is EXTENDED: each empty slot takes its most active coin not already used by that
family if it has at least 30 such entries. Extended pairs are marked `extended`; nothing else changes —
every gate, including participation, applies to them unchanged, so an extended pair adds Jev evidence
but can only pass by meeting the same minimums. The decision used the scan's activity counts only; no
control PnL and no Jev answer was looked at before it.

## Research loop

GENERATE V3 → DEVELOPMENT ARENA → BOT ANALYZER → HYPOTHESIS → NEW VERSION IF NEEDED → FREEZE → TEST
→ FORWARD. The analyzer proposes a NEXT VERSION HYPOTHESIS per bot; it never edits a strategy. V3.0
is frozen (docs/V3_FREEZE.md) before the discovery runs; a V3.1 is developed on DEVELOPMENT data and
evaluated only on an untouched period. Forward results inspire a V4 hypothesis, never a patch.
