# V11 SCAN: bots that scan the whole crypto universe (V11_SCAN_PAPER_V1)

Requested on 2026-09-29: some bots should scan all the crypto coins for trades, not one coin each. V6–V9 keep running
unchanged; their freezes and experiments are untouched.

## Research first (90 days, 30 coins, before anything was frozen)

**Universe** (`scripts/v11_scan_data.py`):
- Bybit linear USDT perpetuals that are crypto (Bybit's `symbolType` empty or `innovation`). Tokenised stocks, ETFs and
  commodities are excluded; so are stablecoins.
- Listed at least 120 days, ranked by the **median** daily turnover of the last 30 days (not today's, which favours
  whatever pumped today). Top 30.
- **The 30 coins:** BTC ETH SOL ZEC XRP HYPE NEAR DOGE ENA SUI ADA ARB UNI 1000PEPE PUMPFUN TAO LINK AKE WLD BNB USELESS
  ONDO AAVE LIT TRUMP AVAX XMR FARTCOIN LTC XPL.
- 95 days of 1m bars, plus funding, open interest, account ratio and the premium index.
- Choosing coins by today's liquidity and then testing backwards is a mild look-ahead; with the top 30 by 30-day
  median turnover it is small, and it would flatter the results rather than hide an edge.

**Split:** 5 days of warm-up, then ~45 days DISCOVERY and ~45 days CONFIRMATION. Every decision rule was fixed before
its run.

### 1. Rule-based scanners (`scripts/v11_scan_study.py` → `docs/V11_SCAN_STUDY.json`)

**Setup:**
- **Families:** 8 in all.
  - The V8 rules applied to every coin: V8.1 pullback, V8.2 breakout, V8.3 VWAP snap.
  - Five scanner-native families: relative-strength breakout, relative-strength pullback, volume shock, capitulation
    snap-back, BTC lead-lag.
- **Book:** 3 slots, best score first, one position per coin.
- **Costs:** taker 0.055% plus each coin's half spread (max of 1 bp or 1.5× the spread observed on its book), charged
  per side.
- **Pass rule:** net R > 0 and PF ≥ 1.1 in both halves, with at least 2 trades a day.

**Result: NONE passed.** The V8 rules scanned across 30 coins lose −0.20R to −0.29R per trade, the same as the
single-coin V8 bots. Scanning more coins does not create an edge: gross R is about 0 for every family, and costs decide
the rest.

| family (book) | discovery net R | confirmation net R |
|---|---|---|
| V8.1 / V8.2 / V8.3 scanned (ALL) | −0.29 / −0.26 / −0.26 | −0.20 / −0.20 / −0.23 |
| RS breakout (TOP) | −0.125 | −0.005 |
| RS pullback (TOP) | −0.223 | +0.041 |
| capitulation snap (ALL) | −0.174 | −0.074 |
| BTC lead-lag (ALL) | −0.130 | −0.196 |

### 2. What predicts the next 1–24 h? (`scripts/v11_feature_study.py` → `docs/V11_FEATURE_STUDY.json`)

**Method:**
- **Metric:** cross-sectional rank IC of 14 features against forward returns relative to the universe, at 1, 4, 8 and
  24 h, with t-statistics over non-overlapping instants.
- **Features:** momentum and reversal over 1–72 h, the idiosyncratic 4h move, volume, range position, volatility,
  funding, premium, open-interest change, and the long/short account ratio.
- **Trading check:** a long-short book (the 3 best-ranked coins vs the 3 worst) after costs.

**Result:**
- **One robust effect:** a **1-hour cross-sectional reversal.** The last 1–4 h return predicts the next hour with
  IC ≈ −0.04, |t| 5–6 in both halves; the idiosyncratic 4h move is about the same.
- **It does not pay:** the long-short book earns about 3 bp gross per position against a ~13 bp taker round trip.
- **Robust after costs: NONE.**

### 3. Extreme moves only (`scripts/v11_reversal_study.py` → `docs/V11_REVERSAL_STUDY.json`)

**Test:** does the reversal pay after large idiosyncratic moves (|z| ≥ 2–4)?

**Result: NOT CONFIRMED.** Extreme moves reversed in the second half but kept running in the first. The effect depends
on the regime; nothing was tradeable.

### 4. Engine check (`scripts/v11_activity_check.py` → `docs/V11_ACTIVITY_CHECK.json`)

**Method:** the four chosen scanners through the live ReplayEngine (the same execution, fees and sizing as the forward
bots) on the same 90 days. Books reset daily and halts are off, so every family trades the whole window.

**Result:** the engine reproduces the study, and all four lose after costs.
- **RS breakout:** ≈ 6 trades a day, −0.09R per trade.
- **RS pullback:** ≈ 9 a day, −0.20R.
- **Capitulation:** ≈ 49 a day, −0.26R. The engine models wider spreads in fast markets than the study did.
- **1h reversal:** ≈ 76 a day, −0.06R.

## Field (the fallback the rule fixed in advance: the best variants, labelled unproven)

Four bots (`app/strategies/v11/scan.py`). Each is ONE **20 USDT** book over all 30 coins, the operator's size, as in V8.
The first session (2026-09-29 09:14–11:30 UTC, experiment `v11x-33cbb7994031`) used 1000 USDT books and is history.

Bybit's minimum orders at 20 USDT:
- **BTC** is never legal: 0.001 BTC needs more than 2% risk.
- **ETH** needs 1–2% risk, so only strong setups take it.
- **ZEC** (and ETH) are out on the reversal bot's wide stops.

The sizing tiers refuse these as MIN_NOTIONAL; the other coins trade.

| bot | decides | rule | exits | slots |
|---|---|---|---|---|
| V11.1-SCAN RS breakout | 15m | a top-20% coin by 4h return (vs the universe) closes above its 4h high on ≥ 1.5× volume, 1h uptrend (mirror: bottom 20%); only scores ≥ the study's discovery 67th percentile | stop 0.8–3%, 2R, 4 h | 8 |
| V11.2-SCAN RS pullback | 15m | a top-20% coin in a 15m and 1h uptrend dips to its 15m EMA20 and resumes (mirror for the weakest); TOP cut as above | stop 0.8–3%, 2R, 4 h | 8 |
| V11.3-SCAN capitulation | 5m | a 1h fall of ≥ 4 ATR with climax volume, then a reversal bar (mirror: a blow-off top) | stop 0.6–3%, 2R, 2 h | 8 |
| V11.4-SCAN 1h reversal | 1h | every hour, short the 2 coins that rose most over 4 h and buy the 2 that fell most | 2 ATR(1h) tail stop (1.5–8%), 3R, **58 min** | 4 |

**How a scanner decides:**
- The decision is taken **once per bar**, on the **anchor coin's (BTCUSDT) candle**. The feed always delivers BTC last
  in each minute, so every coin's candle for that instant is already in.
- A coin whose candle is missing at the instant (a data gap) is not scanned.
- Candidates go best score first. The RiskManager fills the free slots: one position per coin, a coin in its one-bar
  cooldown is refused.

## Market data (`app/live/scan_market.py`): one ordered tape

The V6–V8 Bybit feed delivers each coin on its own queue. A multi-coin book instead needs every coin's bar before it
decides, and a restart must re-derive the same decisions. So V11 has its own read-only feed:
- **History:** the bars stored by earlier sessions, replayed a day at a time in canonical order.
- **Catch-up:** REST 1m klines from the last stored minute to now.
- **Live:** at every minute close + 2 s, one tickers call (bid/ask and funding for every coin) plus each coin's new
  klines, fetched in parallel.
- **Minute release:** a minute is released when all 30 coins are in, or 15 s after it closed with whatever arrived. A
  coin's late bar is then delivered before its next bar, so per-coin order always holds.
- **Costs:** the observed half-spread is recorded with every live bar and priced into fills, as in V6–V8. A funding
  settlement freezes the last predicted rate on the first bar at or after it.

## Execution, gates, continuity

- **Execution:** as V8. The decision instant is the bar close; the fill comes after the 60 s window. Bybit taker fees,
  the observed spread, and funding at each settlement are charged.
- **Sizing:** AGGRESSIVE_V6 legal tiers, 1% base and 2% maximum, with the engine's daily loss halt and capital floor.
- **Gates (`app/live/v11_worker.py`, ScanGateV11):** a decision later than 55 s is SKIP, and so is stale data **on the
  candidate's coin**.
- **Replay:** decisions are recorded per (bot, coin, instant, side) and replayed on restart.
- **Continuity:** identities carry the coin (`<bot>@<symbol>`), so two coins traded at the same instant never collide.
  Trade rows get the coin in their id (`multi_symbol`). Single-coin bots (V6–V9) are unchanged.

## Elimination and qualification

- **Eliminated:** after 24 h live, fewer than 3 closed trades in the last 24 h; or, at any time, 25% below the start.
  Eliminations are recorded and re-applied on restart.
- **QUALIFIED:** ≥ 2 days, ≥ 40 trades, net > 0, PF ≥ 1.2, max DD ≤ 15%, no halt. This is a gate, not a claim.
- **No GO LIVE:** the live mirror follows a single coin and refuses V11 bots.

## Freeze and operation

- `python scripts/v11_freeze.py` writes `docs/V11_FREEZE.json`. It pins the V11 code (strategies, config, feed,
  worker), the parameters, the profile (universe, execution, books, rules), the V6 shared-dependency freeze, and the
  Bybit rules and funding intervals of all 30 coins. A mismatch refuses to trade.
- `V11_FORWARD_ENABLED=true` starts the worker at boot, on its own database (`v11-forward.db`).
- **Public, read-only:**
  - `/api/public/competition/v11`
  - `/api/public/competition/v11/candles?symbol=…`
  - `/api/public/competition/v11/bot/{key}`
- The dashboard's **V11 Scan** tab shows the chart of any universe coin with the scanners' positions, each bot's coins
  traded, and this study's verdict.

## Take-profit ladder (operator's request, 2026-09-30; `app/strategies/v11/ladder.py`)

Every scanner now exits with the operator's ladder instead of a single target:
- **TP1** closes 25% of the position and moves the stop to entry.
- **TP2** closes 50% and moves the stop to TP1.
- **TP3** closes the last 25%.

The fractions are of the original quantity. The move to entry happens the instant TP1 prints; the move to TP1 happens at
the next close of the scanner's decision timeframe. Time stops and structural stops are unchanged.

**Placement** (`scripts/v11_ladder_study.py` → `docs/V11_LADDER_STUDY.json`): the same 90 days at 20 USDT, through the
live engine. The TP distances were chosen on the DISCOVERY half from 0.5/1/2R, 0.75/1.5/2.5R, 1/1.5/2R and 1/2/3R.
0.5 / 1.0 / 2.0 R won for all four:

| scanner | BASE R/trade (win) | ladder R/trade (win) |
|---|---|---|
| V11.1 RS breakout | −0.087 (40%) | −0.078 (51%) |
| V11.2 RS pullback | −0.171 (36%) | −0.116 (49%) |
| V11.3 capitulation | −0.274 (40%) | −0.252 (38%), 67 → 89 trades a day |
| V11.4 1h reversal | −0.065 (43%) | −0.061 (44%) |

The ladder helps a little and raises the win rate, but no scanner is profitable over the 90 days. New freeze and new
experiment; the 3-slot and 8-slot single-target runs are history.

**On screen:** the scanners are pinned on the home screen in their own section, winning or not.

## Pre-trade gap check (operator's request, 2026-09-30; `app/live/scan_engine.py`)

A scanner's market order fills about 60 s after its decision. After violent moves, the price can already be PAST TP1 by
then: the engine would open the trade and hand back TP1 and TP2 at once, for nothing but fees. It can also be THROUGH
THE STOP, which would open and stop out at once. Example: V11.3 shorted TAO planned at 312.20, filled at 307.96 after a
crash, and made −0.002 in fees.

**The check:** immediately before a queued order fills, the fill's reference price (that bar's open) is compared with the
signal's own levels.
- **Long:** skipped if open ≥ TP1 (`gap_past_tp1`) or open ≤ stop (`gap_past_stop`).
- **Short:** skipped if open ≤ TP1 or open ≥ stop.

A real bot can make the same check on the live price before sending its order.

**How it is built:** a V11-only subclass of the shared engine, pinned by V11's freeze. The shared engine module is
unchanged, so the V6–V9 freezes are untouched.

**Effect** (90 days, the L050 ladder, `docs/V11_GAP_GUARD.json`):
- **V11.3:** skips 126 of about 8,000 orders (121 past TP1, 5 through the stop); −0.2524 → −0.2506 R/trade.
- **V11.1 and V11.2:** 1 and 2 orders skipped; results unchanged.
- **V11.4:** none skipped.

It removes pointless trades; it does not make the scanners profitable. New freeze and new experiment.

## Equally spaced ladder, and the +JEV twins (operator's request, 2026-09-30)

**The ladder:**
- **Closing amounts:** 25% / 50% / 25% is how much of the POSITION each take-profit closes, not a distance.
- **Spacing:** the three TPs are now EQUALLY spaced: TP1 = k, TP2 = 2k, TP3 = 3k in R of the planned risk.
- **Stops:** after TP1 the stop goes to entry; after TP2 it goes to TP1, a third of the way from entry to TP3.
- **Choosing k** (`scripts/v11_ladder_study.py --equal` → `docs/V11_EQUAL_LADDER.json`): the same 90 days with the gap
  check on; k was chosen on the DISCOVERY half from 0.5, 0.67, 0.75 and 1.0.

| scanner | k | ladder (R) | R/trade (90 d) | the unequal 0.5/1/2 it replaces |
|---|---|---|---|---|
| V11.1 | 0.67 | 0.67 / 1.33 / 2.0 | −0.075 | −0.078 |
| V11.2 | 0.5 | 0.5 / 1.0 / 1.5 | −0.108 | −0.116 |
| V11.3 | 0.5 | 0.5 / 1.0 / 1.5 | −0.247 | −0.252 |
| V11.4 | 0.5 | 0.5 / 1.0 / 1.5 | −0.061 | −0.061 |

**The +JEV twins** (`app/ai/jev/v11.py`, `JevGateV11` in `app/live/v11_worker.py`). Every scanner has a twin on the
same candidates, data, capital, execution and risk. For each candidate the twin asks Jev whether conditions CONTRADICT,
SUPPORT or STRONGLY SUPPORT the trade (JEV_PROMPT_V11_SCAN, JEV_STATE_V11: the candidate coin's 5m / 15m / 1h, the setup
and its ladder, costs, health).
- **Skip:** CONTRADICT, a failure, or an answer after H + 55 s means the twin skips the trade.
- **Take:** otherwise it trades at the scanner's size, with the ladder stretched by Jev's confidence
  c = P(SUPPORT) + P(STRONGLY SUPPORT): k × (0.5 + c), clamped to 0.75–1.5×. So c = 50% keeps the scanner's TPs,
  80% reaches 1.3× further, and 100% reaches 1.5×.
- **Timing:** each call has a 10 s timeout and no retry; candidates of one instant are asked in turn.
- **Replay:** a restart re-applies the stretch from the recorded confidence, and Jev is never asked about the past.
- **Elimination:** a twin is exempt from the inactivity rule.

**Field:** 4 scanners + 4 twins = 8 bots. The Jev model is part of the experiment identity (`V11_FORWARD_JEV=false` runs
the scanners only). New freeze and new experiment.

## Why a trade that "hit TP2" lost (2026-10-01), and two exit fixes that were tested and not adopted

**The trade:** a V11.3 DOGE short, planned at 0.09510 and filled at 0.094965. The price had already moved 0.14% in
the 60 s window.
- **TP1:** 0.094815, hit in the first minute; 25% closed for +0.013 and the stop moved to entry (+6 bp).
- **TP2:** 0.094529, never touched. The low was 0.09455, 0.00002 short; on the 5m chart it looks like a touch.
- **Exit:** price came back, and the other 75% closed at break-even for +0.012.
- **Result:** price P&L +0.025, fees 0.036 (0.055% in and out on 32 USDT), net −0.011. Not a bug.

**Two fixes tested** (`scripts/v11_ladder_study.py --exits` → `docs/V11_EXIT_FIXES.json`; 90 days, chosen ladders,
gap check on):
- **BEF:** a break-even that pays the fees (entry ± 15 bp).
- **ANCH:** take-profits re-measured from the fill.
- **BOTH:** both together.

They raise the win rate (with BOTH, V11.1 50 → 52%, V11.2 49 → 60%, V11.3 38 → 55%) and would have made this trade
profitable. But they do not improve R per trade:

| scanner | BASE | BEF | ANCH | BOTH |
|---|---|---|---|---|
| V11.1 | −0.076 | −0.074 | −0.083 | −0.081 |
| V11.2 | −0.108 | −0.109 | −0.117 | −0.117 |
| V11.3 | −0.248 | −0.255 | −0.244 | −0.251 |
| V11.4 | −0.061 | −0.061 | −0.061 | −0.061 |

The tighter break-even also stops trades that would have gone on to TP2 or TP3. **Not adopted**: the live engine is
unchanged, and the options stay in the study script only.

**Adopted the same day** (operator, after two more such trades on LTC, "these hit TP but show in losses"):
- **Engine:** after TP1 the stop moves to **entry + 0.15%** (`BE_COVER_BPS`), which pays the round-trip fees, instead
  of entry + 6 bp. The engine does this the instant TP1 prints.
- **Ladder:** it applies the same lock at its next decision bar, in case TP1 printed before the engine's trigger (a
  fill worse than planned).
- **Guarantee:** a trade that has hit TP1 can no longer end below zero after fees, short of slippage through the stop.
- **90 days, live configuration** (`--final` → `docs/V11_LIVE_EXITS.json`):

  | scanner | R/trade | win rate (before) |
  |---|---|---|
  | V11.1 | −0.073 | 51% (50%) |
  | V11.2 | −0.108 | 57% (49%) |
  | V11.3 | −0.255 | 50% (38%) |
  | V11.4 | −0.061 | 44% |

  R per trade is about unchanged.
- New freeze and new experiment.

## TP1 size and the stop after TP1 (operator's question, 2026-10-01)

**The question:** would a bigger TP1 (30%) pay the fees, or should the stop after TP1 sit a little past entry, towards
TP1? `scripts/v11_tp1_study.py` → `docs/V11_TP1_STUDY.json` tested both, on the same 90 days with the live
configuration (equal ladders, gap check, 20 USDT books):
- **TP1 share:** F25 = 25 / 50 / 25 (live), F30 = 30 / 50 / 20, F35 = 35 / 45 / 20.
- **Stop after TP1:** FEES = entry + 0.15% (live), T33 = a third of the way to TP1, T50 = half the way (never less than
  FEES), applied at the end of the 1m bar in which TP1 filled.
- **Decision rule (fixed before running):** per scanner, the best net R per trade on the DISCOVERY half among variants
  where at most 2% of TP1-hit trades end negative.

| scanner | live F25/FEES: R/trade, TP1-hit trades ending negative | chosen | R/trade, TP1-hit ending negative |
|---|---|---|---|
| V11.1 | −0.079, 2.7% | **35 / 45 / 20 + FEES** | −0.078, 0.7% |
| V11.2 | −0.110, 9.6% | **35 / 45 / 20 + T50** | −0.102, 2.8% |
| V11.3 | −0.262, 20.3% | **30 / 50 / 20 + T33** (no variant met 2%) | −0.261, 16.7% |
| V11.4 | −0.061, 1.6% | **35 / 45 / 20 + T50** | −0.061, 0.3% |

**What it says:**
- **Bigger TP1:** this is what helps most. At 35%, three to four times fewer TP1-hit trades end in a loss, at the same
  R per trade.
- **Locking the stop further:** helps V11.2 and V11.4 a little. For V11.1 it costs R, because it stops trades that
  would have reached TP2.
- **Profitability:** neither change makes a scanner profitable.

**Why V11.3 still loses on TP1-hit trades** (diagnostic of F35/T50: 627 of 4,492):
- **Entry slippage:** entries fill 10–45 bp worse than the signal price in a capitulation minute, so TP1 sits much
  closer to the fill than planned.
- **How the engine fills stops:** the shared engine walks each 1m bar as open → adverse extreme → favourable extreme →
  close, and fills a stop at the path price where it is found (the bar's extreme or its close), not at the stop level.
  When TP1 prints at a bar's high and the bar closes under the lock, the rest exits at that close. The median last leg
  of those losers is −0.015% from entry; the worst 10% are −0.25% or lower.
- **Not changed:** this is the frozen shared engine (V6–V9 pin it). A stop/TP fill at the level itself would be a V11
  engine change and a new study.

**Adopted:**
- `app/strategies/v11/ladder.py`: `SHARES` and `LOCK_AFTER_TP1` per scanner.
- `app/live/scan_engine.py`: records each position's TP1 and lock at the fill, and lifts the stop at the end of TP1's
  1m bar.
- Jev's state reports the real closing shares.
- The chart labels show each scanner's shares.
- New freeze and new experiment.

## Exits fill at their level, and the settings re-chosen (operator: "use the best settings and do it", 2026-10-01)

**Engine** (`app/live/scan_engine.py`, V11 only; the shared engine is unchanged):
- **Stops and take-profits fill at their own level.** When a 1m bar's path (open → adverse → favourable → close) passes
  the stop or the next TP, the exit is priced from that level, plus the execution model's spread and slippage. Before,
  it was priced from the bar's extreme or close.
- **Gaps:** a bar that opens beyond a level still fills at the open.
- **Ladder stop moves:** the stop moves the moment a TP fills. After TP1 it goes to `tp1_lock`; after TP2 it goes onto
  TP1.
- **Comparisons:** `level_fills = False` restores the old fills.
- **Effect on the old model's two biases:** stops were filled too pessimistically and TPs too optimistically; both are
  gone.

**Stage 1: TP1 share × stop after TP1** (`scripts/v11_tp1_study.py --level` → `docs/V11_TP1_LEVEL_STUDY.json`):
- 90 days; TP1 closing 25 / 30 / 35 / 40 / 50%, three stop locks.
- Results on the new fills are compared with the previous picks on the old fills.

| scanner | R/trade, old → new fills | TP1-hit trades ending negative | chosen |
|---|---|---|---|
| V11.1 | −0.079 → −0.080 | 0.7% → 0.0% | 25/50/25, stop at entry + fees |
| V11.2 | −0.101 → −0.096 | 2.7% → 0.4% | 25/50/25, stop half way to TP1 |
| V11.3 | −0.261 → −0.183 | 15.9% → 4.9% | 25/50/25, stop a third of the way (no variant met 2%) |
| V11.4 | −0.061 → −0.060 | 0.3% → 0.0% | 25/50/25, stop half way to TP1 |

- **TP1 share:** with level fills, every share from 25% to 50% is within noise, and 25% is best on the discovery half for
  every scanner. The earlier preference for 35% came from stops being filled at the bar's extreme.

**Stage 2: TP spacing** (`--spacing` → `docs/V11_SPACING_LEVEL_STUDY.json`):
- k ∈ {0.5, 0.67, 0.75, 1.0}, using each scanner's stage-1 pick.
- A different k had to beat the current one by 0.005 R on discovery to replace it. None did: V11.1's 0.5 beat 0.67 by
  only 0.001 R. The spacing is unchanged.

**Result and status:**
- Still no scanner is profitable after fees.
- **Freeze `81460b7126ced4c9`**, new experiment.
- **Studies** now run with `V11_STUDY_WORKERS=7`. 12 workers ran this machine out of memory.

## "Bots not trading" check, the breakout stop, and time stops (2026-10-01)

**Live audit:**
- **Feed:** all 30 coins have every 1m bar for 7 days, with none missing.
- **Replay:** replaying the stored live bars through the deployed scanners for 48 hours reproduced the live candidates
  exactly: V11.1 had 4, V11.2 had about 31.
- **Jev twins:** 121 answers, no errors or timeouts, median 345 ms. The 83 setups Jev skipped averaged −0.26 R for the
  scanner.
- **Jev rule, kept:** the skip follows the single most likely answer. 7 skips had support just above 50%; 5 of those 7
  lost.

**Why V11.1 is quiet:**
- **Funnel, 48 hours live:** 22 breakouts passed every filter and the score cut. 18 were refused because the stop sat
  3.4–25% away (cap 3%). The stop is the 3-bar low − 0.25 ATR, which is far away after a big breakout candle.
- **Test** (`scripts/v11_breakout_stop_study.py` → `docs/V11_BREAKOUT_STOP_STUDY.json`): stop under the broken 4h level
  (LEVEL) against the live 3-bar low (LOW3).
  - LOW3: 6.9/day, −0.080 R per trade, −9.2 USDT.
  - LEVEL: 14.0/day, −0.144 R per trade, −34.5 USDT.
  - Not adopted. The 3% cap is protecting V11.1.

**Time stops** (`scripts/v11_hold_study.py` → `docs/V11_HOLD_STUDY.json`):
- **Share of live exits:** 47% of live V11 trades end on the timer, at −0.02 R on average.
- **Holds tested:** ×1 / ×2 / ×4 / none, over 90 days.
- **Effect:** at most 0.001–0.003 R per trade.
- **Rule outcome:** the pre-registered rule passed V11.2 ×2 and V11.3 none, both inside noise.
- **Decision:** the operator chose to keep V11 running unchanged (no restart). V11.4's 58-minute hold is the effect's own
  horizon; holding longer is worse there.

**V8, for reference** (`scripts/v8_hold_study.py` → `docs/V8_HOLD_STUDY.json`, 60 days):
- **Effect of removing the 30–45 minute timer:** total net R goes from −3739 to −2387 and PF from 0.52 to 0.68,
  because there are fewer, longer trades. R per trade barely moves (V8.3 gets worse per trade).
- **Status:** still losing.
- **Decision:** the operator kept V8 frozen, so Gamma (V8.3-ENA-5M, QUALIFIED, live mirror on testnet) keeps its record.

## Zap (V11.3): low profits, high losses; improved (2026-10-02)

**Live, first 24 h, 91 trades:**
- **Results:** 78% wins but PF 0.70. Gross +2.20 USDT, fees 2.32, net −1.13.
- **Wins:** +0.21 R on average; 51 of the 71 are "TP1, then the protective stop" at +0.13 R.
- **Losses:** −0.99 R on average.
- **Fees:** 0.14 R a trade, because the 0.6% minimum stop makes a 0.11% round trip a large slice of R.
- **Clustering:** up to 8 trades on one 5m bar (a market-wide flush).

**Study** (`scripts/v11_zap_study.py` → `docs/V11_ZAP_STUDY.json`): 90 days, the live configuration, one change at a
time. A change is adopted only if it beats BASE's net R per trade in BOTH halves.

| V11.3 variant | net R per trade (half 1 / half 2 / all) | fees R | net USDT | verdict |
|---|---|---|---|---|
| BASE (live) | −0.201 / −0.164 / −0.183 | 0.151 | −272.8 | |
| MAKER_TP (TPs as resting limits) | −0.185 / −0.145 / −0.166 | 0.139 | −248.8 | passes |
| MINSTOP10 (1.0% minimum stop) | −0.141 / −0.113 / −0.127 | 0.103 | −181.9 | passes |
| STRICT5 (flushes ≥ 5 ATR only) | −0.242 / −0.184 / −0.213 | 0.152 | −162.2 | fails |
| TOP2 (≤ 2 candidates per bar) | −0.206 / −0.159 / −0.184 | 0.152 | −229.7 | fails |
| **MAKER_TP + MINSTOP10** | **−0.130 / −0.098 / −0.114** | 0.094 | **−163.9** | **adopted** (beats both singles) |
| MAKER_TP + 1.5% stop (follow-up) | −0.075 / −0.103 / −0.089 | 0.066 | −108.8 | not adopted: worse than 1.0% in half 2 |
| MAKER_TP + 2.0% stop (follow-up) | −0.043 / −0.149 / −0.095 | 0.051 | −107.8 | not adopted: worse than 1.0% in half 2 |

**Correction (2026-10-03):** the two follow-up rows are wrong in half 2. Each held one bogus ARB end-of-replay trade
(see "Correction: end-of-replay exits priced on BTC's bar" at the end). Corrected, both beat 1.0% in both halves, and
the rule picks 2.0%.

**MAKER_TP on the other scanners** (each passes in both halves):

| scanner | R per trade before | R per trade after |
|---|---|---|
| V11.1 | −0.080 | −0.073 |
| V11.2 | −0.096 | −0.083 |
| V11.4 | −0.060 | −0.058 |

**Adopted:**
- **Take-profits:** for every V11 scanner, TPs are resting limit orders. They fill at the TP price at the maker fee
  (0.020%, no spread) once price trades 0.5 bp through them (`ScanReplayEngine(maker_tp=True)`). Stops and time exits
  stay market orders.
- **Zap's minimum stop:** 1.0% (was 0.6%).
- **Expected effect:** Zap's 90-day loss falls by about 40%.
- **Still not profitable:** this cuts costs; it does not create an edge.

**Freezes:**
- New V11 freeze `be4557d25c7c32af` and a new experiment.
- V12 re-frozen `6b2ed84b509ec5f4`. It now pins only the V11 pieces Bizzy runs on (the feed and engine modules, the
  scanner base and the worker's gates), so V11-only changes no longer restart Bizzy.

## Juno / Kilo minimum stops (2026-10-02)

`scripts/v11_scanner_cost_study.py` → `docs/V11_SCANNER_COST_STUDY.json`, on top of the resting take-profits.

| scanner | change | R per trade | 90 days |
|---|---|---|---|
| V11.1 Juno | min stop 0.8% → **1.2%** | −0.073 → −0.058 | −8.3 → −6.3 USDT |
| V11.2 Kilo | min stop 0.8% → **1.5%** | −0.083 → −0.062 | −17.2 → −12.3 USDT (second half +0.013 R) |

- **Rejected:** TOP2 (at most 2 candidates per bar), worse for both.
- **Aria (V11.4):** not tested; its fees are 0.03 R a trade.
- **Freeze:** new V11 freeze, new experiment.

## Correction: end-of-replay exits priced on BTC's bar (re-check 2026-10-03)

**The bug:**
- **Where:** `ReplayEngine._flatten` (end of a replay) and `_reset_book` (daily reset) close what is still open against
  the LAST bar the engine processed.
- **Effect on the V11 tape:** at the end of a replay that bar is BTCUSDT's. An alt's exit is charged BTC's half tick
  plus 0.02 × BTC's ATR in the alt's own price units: 1.55 USD per coin on a 5m position, 10.02 USD on a 1h position.
  ARB at 0.2018 was "sold" at −1.348.
- **Why the studies kept these trades:** the exit is labelled `time` ("end of replay"), and the studies drop only
  `reset` exits.
- **Live trading:** never takes this path. The frozen engine is unchanged; V13 fixes it in its own subclass.

**The check** (`scripts/v11_end_of_run_recheck.py` → `docs/V11_END_OF_RUN_RECHECK.json`):
- **Scope:** every variant whose adopt rule uses the second half: Zap (main and `--wider`), Juno/Kilo, and the
  time-stop study.
- **Method:** replayed through each study's own `run()`, with the minimum stops as they were then. All 38 original
  results reproduced exactly.
- **Comparison:** the end-of-replay trades were then dropped (V13's rule) or priced on the coin's own last bar.

**One decision changes: the Zap wider-stop follow-up.**
- **As recorded:** 1.5% and 2.0% were rejected as "worse than 1.0% in half 2". That was a single ARB trade at −124 R
  (1.5%) and −281 R (2.0%); on ARB's own bar it was about +0.7 R.
- **Corrected:** both pass, and the rule picks the **2.0% minimum stop**.

  | Zap (resting TPs) | net R per trade, half 1 / half 2 / all | 90 days |
  |---|---|---|
  | 1.0% (live) | −0.130 / −0.098 / −0.114 | −163.9 USDT |
  | 1.5% | −0.075 / −0.066 / −0.071 | −86.7 USDT |
  | 2.0% | −0.042 / −0.055 / −0.049 | −53.9 USDT |

  It still loses: this cuts cost, it is not an edge.
- **Adopted (operator, 2026-10-03):** `CapParams.min_stop_pct` 0.010 → **0.020**. Zap's stops now run 2–3% (the cap
  stays 3%). V11 is re-frozen as `bd8aec0dfc357140` (was `67fd3920e6891057`), which means a new experiment. Only
  `app.strategies.v11.scan` and V11.3's params changed. V12 and V13 pin only the scanner base, so they still verify.

**Unchanged:**
- resting take-profits for all four scanners;
- Zap's 1.0% over 0.6%;
- STRICT5 and TOP2 rejected;
- Juno 1.2% and Kilo 1.5% (no V11.1 or V11.2 run had such a trade);
- the time-stop rule's outcomes.

**Overstated:** "holding longer is worse" for V11.4.
- **What happened:** two bogus shorts (LIT −62 R, LINK −14 R) had pushed ×4 and no-timer to −0.131 and −0.160 in
  half 2.
- **Corrected:** −0.070 and −0.068, against ×1's −0.062. Still worse in half 2, so the rule keeps ×1.
- **Whole window:** ×4 (−0.058) and no timer (−0.044) are slightly better than ×1 (−0.060).

**Reset exits:**
- **Where they count:** only the time-stop study counts them.
- **Error:** they are priced on 1000PEPEUSDT's bar, about 0.01 R per reset trade too good.
- **Effect:** at most 0.002 R per trade on any result.

**Cannot change:** studies that choose on the discovery half (ladder, spacing, TP1, level fills, breakout stop). An
end-of-replay trade opens on the tape's last day (2026-09-28), long after the discovery half ends (2026-08-15).
