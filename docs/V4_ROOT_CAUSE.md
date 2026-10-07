# Root-cause analysis before V4

Every number below is real and reproducible: the aggregate counts come from the unified results index
(`app/core/results_index.py`, the same data as `/public/inspection.json`), the exit and entry analysis from
`app/competition/exit_analyzer.py` run read-only on the 33,480 closed CONTROL trades of the V3 (`v3-e39e94890b`)
and V3.1 (`v31-46d2e9af7d`) **DEVELOPMENT** runs over the 1m tape they were replayed on (2025-11..2026-04).
No TEST month was read for this analysis.

## 1. Why 754 evaluated bots failed (current result of each bot, one primary cause)

| root cause | bots | share |
|---|---:|---:|
| NO_GROSS_EDGE — loses before any fee | 432 | 57% |
| NO_TRADES | 78 | 10% |
| FEE_DESTROYED — gross positive, net negative | 75 | 10% |
| MIN_NOTIONAL — orders below the exchange minimum at 10 / 20 USDT | 48 | 6% |
| LOW_ACTIVITY — too few trades to judge | 40 | 5% |
| LIQUIDATION (V1-era 10 USDT lives) | 30 | 4% |
| EXIT_DESTROYS_EDGE (V3 analyzer label) | 30 | 4% |
| NOT_EVALUATED | 12 | 2% |
| PROFIT_CONCENTRATION | 7 | 1% |
| MARGINAL_EDGE | 2 | 0.3% |

128 bots were gross-profitable, 52 net-profitable (16 of them with >= 30 trades), 0 qualified.

## 2. Performance decomposition (CONTROL books; gross -> fees -> slippage -> funding -> net, USDT)

| program / stage | bots | trades | gross | fees | slippage | net | diagnosis |
|---|---:|---:|---:|---:|---:|---:|---|
| V1 multi-year | 69 | 78,199 | -5,047.3 | 3,301.4 | 1,643.0 | -9,991.7 | NO RAW EDGE |
| V1 discovery | 29 | 2,364 | -23.1 | 50.4 | 13.9 | -87.5 | NO RAW EDGE |
| V2 development | 104 | 7,633 | -140.9 | 98.7 | 24.9 | -265.3 | NO RAW EDGE |
| V2 test | 26 | 511 | -4.9 | 9.5 | 2.9 | -17.3 | NO RAW EDGE |
| V2 multi-year | 3 | 1,099 | -2.3 | 7.9 | 2.9 | -13.2 | NO RAW EDGE |
| V3 development | 300 | 31,990 | -320.1 | 649.7 | 148.9 | -1,118.8 | NO RAW EDGE |
| V3.1 test | 55 | 1,030 | -14.6 | 18.6 | 5.0 | -38.5 | NO RAW EDGE |

**Every program is gross-negative.** The costs did not destroy an edge; they decided how fast books with no
edge lost. In V3 the fees (650 USDT) were twice the gross loss (320 USDT).

### Family x timeframe (DEVELOPMENT, gross bps of turnover)

V3 (6 families x 5 timeframes): **27 of 30 cells gross-negative**. The three positive ones: S33 pullback 30m
(+5.6 bps), S35 flow momentum 30m (+4.9), S35 15m (+1.2) — all smaller than the ~15 bps round trip.
V3.1 (3 x 4): 10 of 12 negative; S33.1 pullback-regime 15m +25.0 bps (181 trades, net +2.37 USDT — the only
cell that survived costs, and again in the V3.1 TEST, +3.70) and S33.1 5m +4.6 bps (net -2.95).
Fades were the worst (V3 S36 fast mean reversion 15m -35 bps, 30m -48 bps; S34 range rejection -3 to -11).

### Edge quality (V3 DEVELOPMENT, pooled)

gross -0.010 USDT and -0.080 R per trade · **-5.4 bps of turnover gross, -18.9 bps net** · -0.49 USDT gross per
fee dollar · -0.006 USDT gross per bot-day. By timeframe the gross is -4.6 to -7.3 bps everywhere (1m -5.0,
3m -4.8, 5m -7.3, 15m -4.6, 30m -5.3); what differs is the fee load: 1m/3m/5m trade 3-4x as often.

## 3. Exits — tested against baselines, not in isolation

| question | V3 DEV | V3.1 DEV | verdict |
|---|---|---|---|
| stops too tight? stopped trades that later reached +2R within 12h | 52.7% | 24.2% | random entries with the same stop: 51.4% / 21.5% -> **no** |
| winners cut early? mean drift over the 4h after a winning exit | -0.09 R | +0.14 R | under the +0.3R bar -> **no** |
| losers held too long? median hold loser vs winner | 21 vs 42 min | 94 vs 521 min | losers are cut faster -> **no** |
| targets too close? winners' median MFE vs realised | 2.07 R vs 1.49 R | 3.08 R vs 1.88 R | the move does not continue after the exit -> not a real loss |
| profit left on the table (mean MFE - realised) | 1.10 R winners | 1.94 R winners | the tail of the path, not a repeatable gain |

**Counterfactual exits on the same entries** (stop 1x / 1.5x / 2x / 3x the original, time exit 30-360 min, costs
included): all 20 variants are negative pooled (best V3 -0.077 R, best V3.1 -0.046 R). Wider stops only lower the
cost per R; they never create an edge. **Holding-time buckets** look monotone (V3: <5m -0.86 R ... >4h +0.67 R),
but that is survivorship — a trade still open after 4h is one the stop did not take — so it is not advice.

**1h-trend alignment** barely matters for the V3/V3.1 entries (V3: with the 1h trend -0.285 R, against -0.323 R,
mixed -0.250 R): alignment alone is not an edge; V4 keeps it as the direction layer, not as the edge.

## 4. Other causes

* **Account size**: 48 bots failed on exchange minimums — at 10-20 USDT a half-size order is often illegal
  (V3's DEFENSIVE size was a hidden veto). V4: TAKE or ATTACK, never less; coins must pass a 20 USDT sizing test.
* **Jev**: discrimination was never distinguishable from chance — AUC 0.512 (V1 policy, 9,188 decisions),
  0.511 [0.496, 0.527] (V2), 0.500 (V3.1 DEV), 0.546 [0.499, 0.593] (V3.1 TEST); selection alpha vs the matched
  random action -2.52 / -0.32 USDT. Its apparent wins were lower exposure on losing strategies.
* **Expected-edge gate (V3.1)**: uncalibrated — passed candidates realised -0.250 R, refused ones -0.237 R. Six
  nested buckets fitted noise.
* **Maker entries (V3.1)**: saved ~15% of the loss (-71.8 -> -60.7 USDT DEV), turned nothing profitable.
* **Coins**: the best V3 bot (S31-ZEC-30m, +0.48 R, t = 1.62 against a best-of-660 luck bar of 3.79) lost on all 9
  other coins; ZEC fell 35% in the window and 35 of its 50 trades were shorts. Coin choice by PnL is the trap.

## 5. Improvement priority (applied to V4)

1. **Positive gross expectancy** — nothing else matters without it. V4 families are built around the only
   DEV cells with positive gross (pullback continuation, flow momentum on 15m / 30m structure), the 1h trend as
   direction, and a raw-edge gate (gross expectancy > 0) that every bot must pass.
2. **Positive after-cost expectancy** — a structural stop >= 0.6% keeps the round trip <= ~25% of R; a causal
   family x timeframe expected-edge gate refuses setups whose family has not been net-positive lately.
3. **Sufficient sample** — >= 30 trades and bootstrap P(mean <= 0) <= 10%.
4. **Acceptable drawdown** — halt at 30%, no ATTACK at 18%.
5. **Activity** — per-timeframe participation minimums.
6. **Jev** — only after 1-5, judged only by selection alpha vs the matched random action. Jev is never used to
   rescue a strategy with no underlying edge.
