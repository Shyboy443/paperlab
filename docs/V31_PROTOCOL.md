# V3.1 — AGGRESSIVE EDGE (protocol `V31_AGGRESSIVE_EDGE_PROTOCOL_V1`)

Written 2026-09-23 (UTC), before any V3.1 DEVELOPMENT arena ran. Everything below is pre-registered:
thresholds, gates, the edge model, the Jev V3 policy and the TEST procedure. A change after a result
exists is a new version, never an edit.

## 0. What is preserved

* **V3 is frozen** (docs/V3_RESULTS_FREEZE.md): run `v3-e39e94890b`, its strategies (V3.0), Jev V2
  answers, thresholds and results. `scripts/run_v3_arena.py` refuses every phase but `status` on it.
  V3.1 has its own tables (`v31_runs`, `v31_bots`, schema 13) and never writes a V3 row.
* Jev V1 (`JEV_POLICY_V1`) and V2 (`JEV_PROMPT_V2`/`JEV_STATE_V2`/`JEV_POLICY_V2`) are untouched;
  V3.1 uses the new `JEV_PROMPT_V3`/`JEV_STATE_V3`/`JEV_POLICY_V3`, cached under their own keys.
* Venue BYBIT_LINEAR (taker 0.055%, maker 0.02%, Bybit instrument filters), price tape Binance USD-M
  1m (SHA-verified), 20 USDT isolated books, 20x ceiling, the RiskManager as final authority.
* Nothing trades real money. A V3.1 live forward only starts for an ADVANCED bot (section 12).

## 1. V3 root-cause analysis (DEVELOPMENT only; `app/competition/v31_diagnostics.py`)

Every CONTROL trade of the frozen V3 run (31,990 trades, 300 bots) against the same 1m tape. Signed
drift = price move after entry in the trade's direction, bps.

| tf | trades | median hold | gross bps/trade | cost bps | drift 15m / 1h / 4h / 12h | verdict |
|---|---|---|---|---|---|---|
| 1m (exp.) | 7,955 | 12 min | −5.2 | 12.7 | −0.6 / −3.1 / −2.9 / +24.7 | EXIT DESTROYS EDGE |
| 3m | 8,418 | 22 min | −5.1 | 13.3 | −2.6 / −6.7 / −8.7 / +0.2 | ENTRY EDGE BELOW COST |
| 5m | 8,000 | 29 min | −7.5 | 13.6 | −2.6 / −6.0 / −10.6 / −0.7 | ENTRY HAS NO EDGE |
| 15m | 5,372 | 68 min | −5.4 | 14.9 | −3.3 / −4.0 / +5.4 / +9.3 | ENTRY EDGE BELOW COST |
| 30m | 2,245 | 119 min | −6.1 | 16.0 | +0.7 / −0.9 / +3.8 / −6.6 | ENTRY EDGE BELOW COST |

* **Why 3m/5m have no gross edge:** their entries point the wrong way at every horizon up to 4 hours
  (−6 to −11 bps at 1–4h). The problem is the entry, not the exit and not only the cost: a better exit
  cannot rescue a trigger that has no directional information at its own horizon.
* **Holding period:** trades held < 15 min lost −0.45 to −0.93R on average on every timeframe; > 4h
  trades were positive (+0.56 to +0.84R) — survivorship-confounded, but consistent with the drift.
* **Exits:** ~45% of trades stopped at −1.2 to −1.5R; trails won +1.3 to +1.5R; break-even exits
  −0.02 to −0.23R; 12–18% of trades that reached +1R finished at or below zero.
* **Families** (drift 15m / 1h / 4h / 12h): S35 flow momentum 30m +15 / +20 / +24 / +33 and 15m
  +3 / +1 / +17 / +20 (HOLD TOO SHORT: V3 exited after a median 33–62 min); S33 pullback 15m +25 bps
  at 12h (EXIT DESTROYS EDGE); S31 breakouts 15m/30m +11/+14 at 4h; S36 fast mean-reversion fades were
  strongly **wrong-way**: 15m −19 / −35 / −54 / −74, 30m −17 / −31 / −64 / −101 — i.e. the moves S36
  faded kept going. S34 range rejection negative everywhere. No 3m/5m family had positive 1–4h drift.

What V3.1 takes from this: every entry is a continuation entry aligned with the higher-timeframe trend;
small timeframes only time an entry into a 15m/1h/4h structure; exits let winners run for hours; fades
are dropped. Research facts carried over unchanged (not solved by lowering any threshold): 1m/3m/5m
negative on average; 30m least bad; some 15m bots profitable but not robust; Jev V2 almost always
DEFENSIVE; DEFENSIVE 0.5x pushed 20 USDT orders below the exchange minimum; Jev V2 AUC ≈ random;
Jev V2's "improvement" was lower exposure; the V3 expected-move cost gate was ineffective.

## 2. Strategies (`app/strategies/v31/`, frozen in docs/V31_FREEZE.md)

Common machinery (`base.py`): signal timeframes 3m / 5m / 15m / 30m (30m = benchmark), contexts
3m/5m → 15m + 1h, 15m/30m → 1h + 4h, plus 1h (runner) and 4h (regime) and the 1m execution tape.
No 1m signal bots. Stops structural, clamped to 0.6%–2.5%. Exit: partial at 2–2.5R, break-even only
after 1.5–2R, the runner trailed on 2–2.5 × ATR(1h), 12-hour maximum hold. Every signal records its
signal quality (mean of named factors), regime (4h trend vs the trade: WITH / AGAINST / FLAT), volatility
band (1h ATR% rank over 100 hours: LOW / MID / HIGH), thesis and expected hold.

| family | hypothesis | why it should overcome V3 | expected hold | expected move | raw setups / coin-day (DEV counts) |
|---|---|---|---|---|---|
| S33.1 PULLBACK REGIME CONTINUATION | a pullback to EMA20 inside a trend BOTH context timeframes agree on, resuming on an expanding bar, continues for hours | V3 S33 had +25 bps 12h drift on 15m but exits closed it in 45–180 min; adds two-timeframe alignment, an expansion resumption bar and a long-hold runner | 1–8 h | 2R+ on the runner | 3m ~7 · 5m ~4 · 15m ~1 · 30m ~0.3 |
| S35.1 FLOW PERSISTENCE MOMENTUM | unusually persistent one-sided taker flow (3-bar z ≥ 1.0 vs the coin's last 100 windows) on unusually high volume (top 30%), breaking the 12-bar range with the slow trend, continues for hours | V3 S35 had the most consistent positive drift (15m/30m) and was exited fastest (HOLD TOO SHORT); persistence judged on the coin's own scale; trend filter; long hold | 1–10 h | 2R+ on the runner | 3m ~6 · 5m ~3.5 · 15m ~0.8 · 30m ~0.25 |
| S37 IMPULSE CONTINUATION (new) | a FRESH impulse bar (first close ≥ 2 ATR beyond EMA20, strong close, RSI(7) ≥ 70, 4h not opposite) starts a multi-hour move; 3m/5m only time it after a 15m impulse | V3 S36 faded exactly these stretches and was the most wrong-way family; S37 trades the continuation (with a strong-close condition S36 never had) | 2–12 h | several R when it works | 15m ~1.6 · 30m ~0.8 · 3m ~1.2 · 5m ~0.9 |

ZEC and PENGU (V3's only positive cells) are hypotheses, not tuning targets: nothing in V3.1 is
specific to a coin; the edge model may learn coin effects from DEVELOPMENT evidence only.

**DEVELOPMENT data looked at before the freeze** (disclosed): the V3 root-cause analysis above; raw
signal COUNTS of the three families on ZEC, SUI, PENGU and AAVE (no outcome simulated or printed),
which led to one count-only recalibration (S33.1: EMA touch 0.5 ATR, lookback 8, RSI reset 48, expansion
1.0 ATR, close location 0.6, volume ≥ average; S35.1: flow and volume measured as a z-score / percentile
of the coin's own history instead of fixed thresholds, z ≥ 1.0, top 30%, 12-bar break; S37: only a
fresh impulse); and two pipeline smoke tests (S33.1 ZEC 15m, Nov–Dec 2025, which showed that bot's
20 trades). No parameter was chosen by PnL.

## 3. EXPECTED NET EDGE model and the EDGE GATE (`app/competition/v31_edge.py`)

* **Evidence**: the OBSERVE phase replays every family × coin × timeframe (120 RAW bots) from
  2025-10-11 with an observer in the gate slot. Each candidate that is legal at TAKE size is simulated in
  the engine's shadow book (same fills, fees, spread, exits); nothing trades. Candidates are then
  SEQUENCED as an ungated bot would take them (flat and out of cooldown) so overlapping signals of one
  move count once. RAW bots never compete.
* **Buckets / fallback chain**: exact bucket (strategy, timeframe, coin, quality band LOW < 0.40 ≤ MID
  < 0.60 ≤ HIGH, regime, volatility band) → strategy × timeframe × coin → strategy × timeframe →
  strategy family → *insufficient evidence*. Hierarchical shrinkage with k = 30 pseudo-observations;
  each level's prior is its parent's estimate from the parent's OTHER observations (leave-child-out, no
  double counting); the family level shrinks to zero edge. A family with < 30 resolved observations
  gives INSUFFICIENT_EVIDENCE = refused.
* **Prediction per candidate**: expected gross R of its bucket; P(target), P(stop), average winner and
  loser R (reported); the candidate's own execution cost in R = (2 taker fees + 2 modelled half-spreads)
  / stop distance; predicted net R = gross − cost; standard error from n_eff (own observations + at most
  k of the parent's others); expected edge in bps; EDGE HEADROOM = expected gross edge − expected
  execution cost (bps, and gross/cost as a ratio).
* **Gate (deterministic, before sizing, legality and Jev)**: PASS if predicted net R ≥ +0.05R.
  ATTACK-eligible if (net − 1 se) ≥ +0.10R AND gross ≥ 2 × cost AND signal quality ≥ 0.60.
  EXCEPTIONAL if also (net − 1 se) ≥ +0.20R and quality ≥ 0.85.
* **Causality**: in DEVELOPMENT a candidate at time t only sees observations that EXITED before t. TEST
  uses the DEVELOPMENT ledger (exits ≤ 2026-04-30), frozen and fingerprinted; no TEST outcome ever
  enters it. Thresholds are frozen here.
* **Calibration** is reported per run: predicted vs realized net R for every CONTROL candidate (the real
  trade, or for a refused one the shadow trade), slope, Pearson, Spearman, passed vs refused.

## 4. Sizing — AGGRESSIVE_V31 (`app/competition/v31_config.py`)

TAKE 1.0% · ATTACK 1.5% · STRONG_ATTACK 2.0% (hard cap) of equity at risk; 20x ceiling, leverage used
only as needed; RiskManager floor unchanged. **No DEFENSIVE size** (a half-size order at 20 USDT is often
not a legal order). Bot health: NO_ATTACK when ≥ 18% below the peak or the last 20 trades average
< −0.25R (≥ 10 trades); HALTED (no new trades) at 30% below the peak. The same deterministic ATTACK rule
(`attack_tier`: edge ATTACK-eligible + health OK; STRONG needs EXCEPTIONAL) serves CONTROL, +JEV3 and
RANDOM. An ATTACK the RiskManager would refuse falls back to TAKE (never a veto).

## 5. JEV V3 (`app/ai/jev/v3.py`)

* Asked only about candidates that passed the edge gate and legality. Pipeline:
  `candidate → edge gate → Jev action → sizing → exchange legality → RiskManager`.
* Question 1 (`support`, noul): "Given that this setup has already passed deterministic legality,
  execution-cost and positive-edge checks, determine whether current market conditions SUPPORT or
  CONTRADICT the strategy's setup." Question 2 (`action`, choice): SKIP = the evidence materially
  contradicts the setup · TAKE = the setup remains valid (the normal action) · ATTACK = multiple
  independent conditions strongly reinforce it. The prompt says a long-hold continuation strategy can win
  under half its trades and keep a positive expectancy, so a possible loss alone is not a reason to skip.
* State (JEV_STATE_V3) = JEV_STATE_V2 + thesis, direction, the expected-net-edge verdict, the multi-
  timeframe ladder (4h, 1h, 30m/15m, 5m/3m, 1m) with its alignment to the trade, the regime, the volatility
  band; spread, slippage, funding, drawdown and recent bot health. Nothing after the decision time; no
  outcome, holdout result or leaderboard.
* Policy: SKIP 0 · TAKE 1.0 · ATTACK 1.5 (through `attack_tier`) · STRONG_ATTACK 2.0 when Jev's own
  P(ATTACK) ≥ 0.60 AND the edge model marks the setup EXCEPTIONAL. A failed request is SKIP (as V1/V2).
  Jev never shrinks an order: MIN_NOTIONAL_AFTER_JEV is structurally 0 and is counted anyway.
* P(support) and P(SKIP / TAKE / ATTACK) are stored per decision; confidence → realized expectancy is
  analyzed (AUC with 95% CI; realized R per P(support) band and per action; SKIP outcomes are shadow trades).

## 6. The arena (`app/competition/v31_arena.py`, `v31_run.py`)

| role | what it is |
|---|---|
| RAW | evidence only (section 3) |
| CONTROL | edge gate + AGGRESSIVE_V31 deterministic sizing; every family × coin × timeframe (120) |
| +JEV3 | the same pipeline + Jev V3 on every candidate that passed the edge gate |
| ALWAYS TAKE | the same pipeline, every candidate TAKE |
| ALWAYS SKIP | never trades (0.00) |
| RANDOM MATCHED-ACTION | 20 deterministic seeds; SKIP / TAKE / ATTACK drawn with the +JEV3 bot's own chosen rates, through the same ATTACK rule and legality |
| CAPACITY | CONTROL at 50 and 100 USDT for every field pair — diagnostic only, never qualifies |

* **Field (neutral, deterministic, before any Jev answer)**: for every family × timeframe slot, the 3
  coins with the most EXECUTED entries (legal, positive edge, RiskManager-approved) among CONTROL bots
  with ≥ 30 entries; ties alphabetical; never PnL. Minimum 20 pairs, target 30+ (up to 36).
  A shortfall is reported, never filled by looking at results.
* **JEV SELECTION ALPHA** = +JEV3 net − median net of its 20 matched random-action twins (also in R).
* **ATTACK EFFECTIVENESS**: ATTACK trades, their expectancy vs TAKE trades, net PnL vs the counterfactual
  NORMAL (the same trade at TAKE size = net / multiplier).
* **PARTICIPATION FUNNEL** per bot: raw setups → legal (RiskManager dry run at TAKE size) → positive
  expected edge → Jev TAKE/ATTACK → executed.

## 7. MAKER-FIRST experiment (`app/competition/v31_maker.py`, diagnostic)

Post hoc on the 1m tape for every executed CONTROL entry: post a limit at the taker fill instant, wait 5
or 15 minutes. OPTIMISTIC maker: limit at the decision price, filled on a touch. CONSERVATIVE maker: limit
one half-spread better, filled only if the tape trades through it by max(1 tick, 1 bp). Unfilled → taker
at the end of the wait only if price is within 0.25 × the stop distance of the limit, else MISSED (the
runaway winners). A filled maker entry keeps the real exit path and pays the maker fee on entry. Verdict:
profitable only with the optimistic maker = FAIL.

## 8. Participation, holding, concentration

* Participation gate (trades/day): 3m ≥ 2 · 5m ≥ 1.5 · 15m ≥ 0.5 · 30m ≥ 0.25; adequate sample ≥ 30
  trades. A positive-expectancy, profitable bot that fails only participation is LOW_ACTIVITY_EDGE.
* Concentration: PnL without the best trade, without the best 3, and the top-3 share of gross profit.
* Holding: median hold and gross / net / R per bucket (< 5m, 5–15m, 15–60m, 1–4h, > 4h) per timeframe;
  exit mix, give-back after +1R, post-exit drift; entry drift at 15m / 1h / 4h / 12h and MFE / MAE.

## 9. QUALIFICATION V3.1 (not weakened from V3)

Every bot: ≥ 30 trades · participation gate · net PnL > 0 · net expectancy > 0 · PF ≥ 1.10 · max
drawdown ≤ 30% and never halted · no liquidation · positive without the best 3 trades AND top 3 ≤ 60% of
gross profit. +JEV3 additionally: skip rate ≤ 80% · net above the matched random action's 90th
percentile with positive selection alpha · net ≥ CONTROL + 0.50 USDT · P(support) AUC 95% CI above 0.50 ·
API errors ≤ 2% · p95 latency ≤ 2,000 ms.

States: ADVANCE (every gate) · LOW_ACTIVITY_EDGE · INSUFFICIENT_SAMPLE · FAIL. Bot Analyzer V3.1
diagnoses (all that apply, root cause first): LIQUIDATION, MIN NOTIONAL LIMITED, JEV OVER FILTERED,
ENTRY HAS NO EDGE / EXIT DESTROYS EDGE / HOLD TOO SHORT (gross ≤ 0, by the entry/exit path verdict),
COST DESTROYED, DRAWDOWN, TOO LITTLE ACTIVITY, INSUFFICIENT SAMPLE, PROFIT CONCENTRATED, MARGINAL EDGE,
JEV NO SELECTION ALPHA.

## 10. Data

* DEVELOPMENT: 2025-10 (warmup; RAW evidence from 2025-10-11) → trading 2025-11-01 → 2026-04-30.
* TEST: 2026-05-01 → 2026-08-31 (2026-04 as indicator warmup only). Not downloaded and not inspected
  while V3.1 is developed. Downloaded into its own archive directory (the V3 archive is left as it is)
  only after the pre-registration below is written.

## 11. TEST pre-registration (docs/V31_TEST_PREREGISTRATION.json, written before the download)

Frozen and fingerprinted: source fingerprints (docs/V31_FREEZE.md), Jev V3 prompt / policy, the risk
profile, the edge gate thresholds AND its evidence (DEVELOPMENT RAW ledger fingerprint), maker / taker
assumptions, the candidate field (pairs), qualification thresholds, bot identities and the TEST config
fingerprint (without the dataset fingerprint, unknown before the download). `scripts/run_v31_arena.py
--role TEST` refuses to run if anything differs. TEST runs CONTROL for all 120 family × coin × timeframe
bots (no selection happens on TEST) and +JEV3 / ALWAYS TAKE / RANDOM / CAPACITY for the frozen field.

## 12. ADVANCED SET and forward

ADVANCED SET = bots that pass every QUALIFICATION V3.1 gate on BOTH DEVELOPMENT and TEST. If it is
empty it stays NONE — no winner is manufactured. No V3.1 live forward starts unless a bot advanced; an
advanced bot would only enter the continuous forward shadow (paper), and live capital always needs a
human operator.

## Amendment 1 (2026-09-23 21:48 UTC, during the DEVELOPMENT run, before any Jev V3 answer was read)

The pre-registered field rule (up to 3 coins per family × timeframe slot with ≥ 30 executed entries)
qualified **15 pairs** — below the minimum of 20 (the edge gate lets few candidates through, so few
CONTROL bots reach 30 trades in six months; 30m had a single pair). Decided from the CONTROL bots'
executed-entry counts only (no PnL, no Jev answer): the per-pair floor is lowered to the HIGHEST floor
at which the field reaches the pre-registered target of 30 pairs — floor **10** → **30 pairs** (3m 9 ·
5m 8 · 15m 9 · 30m 4); top 3 per slot, ties alphabetical, unchanged. The 15 added pairs are EXTENDED:
every gate still applies to them, including the 30-trade adequate-sample gate, so an extended pair can
inform the Jev question but can never advance on a thin sample. The pre-registered outcome (15 pairs,
BELOW_MINIMUM) stays on record in the run's field (`preregistered`, `amendment`).
