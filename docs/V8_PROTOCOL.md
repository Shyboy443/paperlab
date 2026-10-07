# V8 SCALP: aggressive paper scalpers (V8_SCALP_PAPER_V1)

Requested on 2026-09-27: very aggressive bots making quick scalps, at least 10 trades a day, eliminating bots that
don't trade, and a fast route to qualification. V6 and V7 keep running unchanged; their freezes and experiments are
untouched.

This program is separate from the rejected feasibility prototype in `app/strategies/v8/scalp.py`
(`docs/V8_SCALP_ASSESSMENT.md`). That prototype's week of scalping lost money on every bot. V8 is expected to face
the same cost problem, and the forward paper test will measure it.

## Field

- 3 families × 6 coins (ETH, SOL, XRP, DOGE, ARB, ENA) = **18 CONTROL bots**, 20 USDT each, isolated books.
- Each CONTROL has a matched **+JEV twin**, for 36 bots in total. The twin sees the same candidates, data, capital,
  execution and risk. Added 2026-09-27 at the user's request; the 18-bot experiment `v8x-76775b357c93` is frozen
  history.

## Jev V8 (`app/ai/jev/v8.py`, JEV_PROMPT_V8_SCALP / JEV_STATE_V8)

- **What it sees:** the twin asks Jev about the CONTROL's candidate. The state holds only closed candles:
  - the 5m / 15m / 1h trend ladder;
  - the last hour of 5m price action;
  - relative volume;
  - the round-trip cost in R;
  - the sizing tier and the bot's health.
- **What it answers:** CONTRADICT → SKIP, SUPPORT → TAKE, STRONGLY SUPPORT → ATTACK. ATTACK means 2% risk, and only if
  the RiskManager accepts it. The policy is Jev V6's, unchanged.
- **Timing:** each call has a 20 s timeout and 1 retry. An answer after H + 55 s is SKIP (DEADLINE), and a failed call
  is SKIP. The fill stays at H + 60 s for both bots of a pair.
- **Elimination:** a twin is never eliminated for inactivity, because skipping is its job. It leaves with its CONTROL
  (CONTROL_ELIMINATED) or at its own 25% loss.
- **Switches:** `V8_FORWARD_JEV=false` runs the controls only. The Jev model is part of the experiment identity.

## Strategies (`app/strategies/v8/arena.py`)

Every family decides on each closed 5m candle, using only its own coin's 5m, 15m and 1h candles. No positioning feed
is used, so a restart re-derives identical decisions.

| id | family | entry |
|---|---|---|
| V8.1 | SCALP_PULLBACK | a 5m touch of EMA20 (±0.35 ATR) in the 15m trend (1h not opposing), then a close through the previous bar |
| V8.2 | SCALP_BREAKOUT | a close outside the prior 6-bar range on ≥ 1.1× median volume, not against the 1h trend |
| V8.3 | SCALP_VWAP_REVERSION | a 1.8–4 ATR stretch from the rolling 4h VWAP, then a reversal bar |

Exits:

- **Stop:** the structural extreme + 0.2 ATR, clamped to 0.45–1.2%. Below 0.45%, Bybit's taker round trip is more than
  25% of R and the engine's fee guard refuses the order.
- **Target:** 1.5R.
- **Time stop:** 45 minutes.
- **Cooldown:** one 5m bar.

## Execution and risk

- Fills at the open of the first 1m bar at or after the decision instant + 60 s.
- A decision later than 55 s is LATE; stale data pauses a coin.
- Costs: Bybit taker fees, the observed spread, and funding at each settlement.
- Sizing uses the AGGRESSIVE_V6 legal tiers (1% base, 2% maximum). The engine keeps its daily-loss halt and its
  capital floor.

## Activity check (before the freeze, counts only)

`scripts/v8_activity_check.py` replays the last 7 days of Bybit 1m bars through the live engine and counts executed
trades (`docs/V8_ACTIVITY_CHECK.json`).

- **First pass:** median 9.6 trades per bot per day.
- **One documented loosening** of V8.1/V8.2: the touch zone went 0.25 → 0.35 ATR, the close location 0.55/0.60 →
  0.50/0.55, the range 8 → 6 bars, and the volume threshold 1.2× → 1.1×.
- **Result:** median **10.7 per bot per day**. ARB and ENA are quieter, at 4–6 per day for V8.1/V8.2.

The check also showed several V8.3 bots reaching the engine's 25% loss halt within the week. That is the cost
problem, stated plainly.

## Elimination (automatic, recorded)

A bot is eliminated in either case:

- after 24 h live, with fewer than 5 closed trades in its last 24 h;
- at any time, 25% below its starting capital.

An eliminated bot opens nothing new and manages any open position to its exit. The elimination is stored as an event
and re-applied on every restart.

## Fast qualification (the gate for the operator's go-live button)

QUALIFIED requires all of the following:

- ≥ 2 days live;
- ≥ 40 closed trades;
- net > 0 after all costs;
- profit factor ≥ 1.2;
- max drawdown ≤ 15%;
- no halt.

This is a gate, not a claim that the bot has an edge.

## Freeze and operation

- `python scripts/v8_freeze.py` writes `docs/V8_FREEZE.json`. It pins the V8 code, parameters and profile, the V6
  shared-dependency freeze, and the Bybit rules for the six coins. A mismatch refuses to trade.
- `V8_FORWARD_ENABLED=true` starts the worker at boot, on its own database (`v8-forward.db`).
- Public read-only data:
  - `/api/public/competition/v8`
  - `/api/public/competition/v8/candles`
  - `/api/public/competition/v8/bot/{key}`

## Exit study: TP1/TP2/TP3 ladders and breakeven stops (2026-09-28, `docs/V8_EXIT_STUDY.json`)

**Question (from the operator):** many losers were in profit first, then reversed into the stop. Would partial
take-profits plus moving the stop to entry, then into profit, fix that?

**Live evidence:** of 327 closed live trades, 212 were losers.
- 34% of the losers had been at ≥ +0.5R and 18% at ≥ +0.75R; only 7.5% had reached +1R.
- 56% of the losers ended at the 45-minute time stop, not the stop-loss.

**Method:** a replay of 60 days (2026-07-30 to 2026-09-28) through the live engine.
- The same V8 entries on all 18 family × coin pairs, about 13–15k trades per variant.
- Only the exit changes between variants.
- Books reset daily and halts were off, so every variant traded the whole window.
- The decision rule was fixed before the run: net R must be better in both halves, with PF ≥ BASE.

| exit | win rate | avg R/trade | PF |
|---|---|---|---|
| BASE (1.5R target, current) | 34.6% | −0.284 | 0.52 |
| breakeven at +0.75R | 32.7% | −0.299 | 0.45 |
| LADDER3 0.75/1.5/2.5R, stop → entry → +0.75R | 39.5% | −0.290 | 0.43 |
| LADDER2 1R/2R, stop → entry | 37.2% | −0.286 | 0.48 |
| TIGHT3 0.5/1/1.5R, stop → entry → +0.5R | 40.9% | −0.284 | 0.36 |

**Decision: BASE stays.**
- The ladders raise the win rate (to 39–41%) but not the profit.
- The breakeven stop is hit thousands of times, cutting off trades that would have reached the target, while the
  partial targets shrink the average win.
- The underlying problem is the entries: about −0.28R per trade before any exit rule, of which about 0.2R is taker
  fees plus spread. No exit scheme turns that into a profit.

## Maker-order study (2026-09-28, `docs/V8_MAKER_STUDY.json`)

**Method:** the same 60 days of V8 signals, replayed through a small execution simulator. Its market-order version
reproduced the engine within 0.04R per trade (−0.249R vs −0.284R, with the same 34.5% win rate).
- **Limit entries:** post-only at the signal price, resting 5 minutes. They fill only on a trade-through; a miss means
  no trade.
- **Limit target:** the target rests as a limit order.
- **Stops and time exits:** always market orders.
- **Fees:** Bybit taker 0.055%, maker 0.020%, plus a 1 bp half-spread on every taker fill.

| execution | fill rate | avg R/trade (net) | avg R before costs | half 1 / half 2 |
|---|---|---|---|---|
| market in, market out (today) | 99.9% | −0.249 | −0.039 | −0.257 / −0.240 |
| limit in, market out | 73.1% | −0.194 | −0.052 | −0.193 / −0.195 |
| limit in, limit target | 73.1% | **−0.185** | −0.050 | −0.186 / −0.184 |
| market in, limit target | 99.9% | −0.238 | −0.038 | −0.248 / −0.228 |

**Verdict:**
- **Maker execution beats market in both halves,** saving about 0.06R per trade, but **no variant is profitable.**
- **Before costs, V8's entries are already slightly negative** (−0.04R/trade), and limit fills are a little worse
  still (−0.05R), because fills come more often when the price moves against the entry.
- **Cheaper execution cannot rescue entries without an edge.** Building a limit-order engine path (a new program, since
  the engine is frozen) is not justified by this result alone.

## +LADDER twins (2026-09-28, freeze `1e3152e4bac01894`, experiment `v8x-b335a35a429b`)

The operator asked for the TP ladder to be built despite the exit study above. It runs as a **third twin** of every
control (`app/strategies/v8/ladder.py`), so the live results decide.
- **Entries:** the control's entries, through the control gate.
- **Exits:**
  - **TP1 at +0.75R** closes ⅓ and moves the stop to entry + 0.15%, which covers fees.
  - **TP2 at +1.5R** closes ⅓ and moves the stop to +0.75R.
  - **TP3 at +2.5R** closes the last ⅓.
  - The 45-minute time stop and the initial structural stop are unchanged.

**Field:** 18 CONTROL + 18 +JEV + 18 +LADDER = 54 bots. The dashboard's "TP ladder vs control" card shows each pair's
net and win rate side by side.

**Not eligible for GO LIVE yet:** the live mirror copies whole positions with a fixed exchange stop. It cannot yet
mirror partial closes or stop moves.

**Expectation from the replay:** a higher win rate (about 39–40% vs 35%) with about the same or a slightly worse
result per trade. Forward data decides.

## Cost study and new exits (2026-10-02, operator: "do the same analysis for the other bots and improve them")

**Live diagnosis** (`v8x-b335a35a429b`, CONTROL bots, 1,860 trades):
- **Fees:** 0.20 R a trade, from 0.45% minimum stops and a 0.11% round trip.
- **Before vs after fees:** V8.3 was +2.85 USDT before fees and −16.66 after. V8.1 and V8.2 lost before fees too.

**Study** (`scripts/v8_cost_study.py` → `docs/V8_COST_STUDY.json`): the 60 cached days, CONTROL bots of every family ×
coin, one change at a time. A change passes only if its net R per trade beats BASE in BOTH halves; passing changes are
then combined. A follow-up tested 1.0% and 1.2% minimum stops against the 0.8% winner, under the same rule.

| family | BASE R/trade (60 d net) | adopted | after R/trade (60 d net) |
|---|---|---|---|
| V8.1 pullback | −0.283 (−160 USDT) | resting targets + level fills + **1.0%** min stop + **no time stop** | **−0.107** (−20 USDT) |
| V8.2 breakout | −0.250 (−153 USDT) | resting targets + level fills + **1.2%** min stop + **no time stop** | **−0.087** (−15 USDT) |
| V8.3 VWAP snap | −0.308 (−309 USDT) | resting targets + level fills + **1.2%** min stop (no time stop failed) | **−0.105** (−94 USDT) |

**Live implementation:**
- `app/live/v8_engine.py`: V11's scan engine without its gap check. Exits fill at their level, targets rest as limits
  (maker fee, 0.5 bp trade-through), and the ladder stop moves the moment a TP fills.
- `app/strategies/v8/arena.py`: per-family `Params`.
- **"No time stop" means:** held to the stop, the target or 1 minute before the UTC midnight. The study's books reset at
  midnight.

**Result and costs of the change:**
- Roughly 80% less loss over the 60 days; still no family is profitable.
- The operator accepted the restart: all 54 bots restart, and Gamma (V8.3-ENA-5M) must requalify on the new settings.
- **V9 is unaffected:** its stock families subclass V8's but keep their own Params (0.25% stops, 45 minutes, flat by
  the close), and a test pins that.

## V8.3 hold: 45 minutes → 3 hours (2026-10-03)

The operator asked to "fix the V8.3 time limit issue". Live, 33 of V8.3's first 40 control trades ended at the
45-minute limit. With a 1.2% stop and a 1.8% target, 45 minutes was short.

**Study** (`scripts/v8_snap_hold_study.py`, `docs/V8_SNAP_HOLD_STUDY.json`): pre-registered, 99 days × 6 coins, V8.3
exactly as it runs live (the live engine, maker targets). Only the hold changes.

| Hold | Trades per coin-day | Net R per trade (half 1 / half 2) | Total USDT (fees) |
|---|---|---|---|
| 45 min (base) | 13.0 | −0.103 / −0.102 | −148 (130) |
| 90 min | 9.4 | −0.104 / −0.116 | −119 |
| **3 h** | **6.8** | **−0.098 / −0.101** | **−81 (66)** |
| 6 h | 5.2 | −0.129 / −0.129 | −79 |
| day close | 4.8 | −0.117 / −0.130 | −72 |

- **Adopted: 3 hours.** It was the only variant better in both halves. Per trade the gain is small. Total losses nearly
  halve because it takes half as many trades, so fees halve.
- **The setup still loses.**
- **Inactivity bar for V8.3:** 2 closed trades in 24 h (the others stay at 5). On 3-hour holds the study's coins traded
  fewer than 5 times on 0–4 of 94 days, but ETH did so on 76.
- **New freezes and experiments:** V8 `d72a48d6ed985907`. V9 `74c71de6f52aa2d3` pins `app/strategies/v8/arena.py` and so
  restarts too, though its own parameters are unchanged (V9.3 keeps 45 minutes).

## V8.1 / V8.2: "fix them" — nothing to adopt (2026-10-04)

The operator asked to "fix the V8.1 and V8.2 bots". In the V8 run started 2026-10-03 (a weekend), the V8.1 controls
won 2 of 12 trades and V8.2 won 0 of 14.

**Step 1: six single changes** (`scripts/v8_trend_fix_study.py`, `docs/V8_TREND_FIX_STUDY.json`). Pre-registered,
99 days × 6 coins, the live engine and settings.

| Family | Base (net R per trade) | Changes tested |
|---|---|---|
| V8.1 | −0.135 | T10, T20, H180, H360, HTF, BE1 |
| V8.2 | −0.102 | the same six |

- **Changes:** T10 / T20 = target 1.0R / 2.0R; H180 / H360 = time limit 3 h / 6 h; HTF = V14's higher-timeframe rule;
  BE1 = break-even at +1R.
- **Result: none was better in both halves.** Near misses were BE1 for V8.1 and HTF for V8.2.
- **One pattern:** shorts lost far more than longs (V8.1 −0.158 vs −0.113R; V8.2 −0.171 vs −0.037R).

**Step 2: confirmation on an independent window** (`--confirm`, `docs/V8_TREND_FIX_CONFIRM.json`).
- **Data:** Bybit 1m, 2025-01-01 .. 2026-06-26, the same six coins, about 13,000 trades per family
  (`research/ingest_history.py` → `data/research/history_v8.duckdb`).
- **Candidates:** LONGONLY (both families) and the two near misses.
- **Result: all failed.**
  - V8.1: base −0.079R; LONGONLY −0.121; BE1 −0.093.
  - V8.2: base −0.073R; LONGONLY −0.121; HTF −0.078.
- **The pattern reversed:** over these 18 months shorts did better than longs (V8.1 −0.044 vs −0.122R; V8.2 −0.043 vs
  −0.110R). It was the recent rising market, not an edge.

**Conclusion:**
- V8.1 and V8.2 lose about 0.07–0.14R a trade in every configuration tested, on both windows.
- No parameter fix exists among these. They stay unchanged (no re-freeze, no restart).
- The remaining options are the operator's: keep them running as they are, or retire them.
