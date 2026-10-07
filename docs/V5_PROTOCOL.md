# V5 — HOURLY / DAILY FUTURES ARENA — protocol

Written before any V5 bot was replayed. V1, V2, V3, V3.1 and V4 are frozen research history and untouched; V5 has
its own strategies (`app/strategies/v5/`), data (Bybit-native, `docs/V5_DATA_AUDIT.md`), tables, runner and documents.

## 0. What failed in V1-V4 (the reason for V5)

* **No raw edge.** Every program's CONTROL books lost money BEFORE any fee (V1 multi-year gross -5,047 USDT, V3 DEV
  -320, V4 DEV and HOLDOUT gross expectancy -0.04 to -0.15 R per setup on every timeframe and family). Costs only set
  the speed of the loss.
* **Short-timeframe chart patterns were the only idea tested** — EMA/RSI/breakout/pullback/flow shapes on 1m-30m
  bars, held minutes to a few hours. Four generations of re-shaping the same idea did not produce a gross edge.
* **Filters hid the problem instead of fixing it.** V4 lost 45x less than V3 only because its gate refused ~98% of
  setups; per trade it lost the same.
* **Exits, account size and Jev were not the cause** (tested against random entries, 50 / 100 USDT twins and matched
  random actions). Jev never discriminated winners from losers (every AUC interval contains 0.5).

V5 therefore changes the SOURCE of the edge (futures positioning: funding, open interest, basis, account ratio) and
the HORIZON (hourly / 4-hourly decisions, 4 hours to 3 days holds), not the filter.

## 1. Two classes

| class | signal (decides on) | context | typical hold | activity target (never forced) |
|---|---|---|---|---|
| HOURLY | every closed 1h candle | 4h, 1D | 4h - 24h | ~0.5 - 3 trades/day |
| SWING | every closed 4h candle | 1D, 1W regime | 12h - 72h | ~1 - 5 trades/week |

Execution always AFTER the signal candle closes, on the 1m Bybit tape (latency 400 ms -> next 1m bar's open), through
the same RiskManager, execution model and exit engine as every PaperLab replay.

## 2. Data and windows

Bybit-native only (`docs/V5_DATA_AUDIT.md`): 1m klines (execution; 1h/4h/1D/1W aggregated from them), funding
settlements, 1h open interest, 1h premium index (basis), 1h long/short account ratio, instrument filters. No SURROGATE
series. Windows (`docs/V5_DATASET_MAP.md`): DEVELOPMENT 2025-03-01 -> 2026-08-31 (warm-up from 2024-12-01);
PSEUDO-HOLDOUT 2023-09-01 -> 2025-02-28 (warm-up from 2023-06-01), pre-registered and downloaded only after the
freeze; FORWARD VALIDATION on live Bybit data after the freeze (the only untouched test, required for QUALIFIED).

Positioning features are read CAUSALLY: at a decision at time t only settlements / snapshots stamped <= t are visible
(funding rates only once settled; OI, basis and ratio snapshots with timestamp <= t).

## 3. Universe (objective, never PnL) — `app/competition/v5_universe.py`

From the 30 days before a window's first trading day: candidates = the 40 Bybit linear USDT perpetuals with the highest
median daily turnover. Gates: listed >= 90 days before the window; Bybit 1m tape, funding and 1h OI all exist from the
warm-up start; turnover >= 20 M USDT/day; half spread (tick / 2) <= 2 bps; median 4h range >= 100 bps; round-trip taker
cost <= 15% of the median 4h range; a TAKE order (1% of 20 USDT at risk) at a typical V5 stop (1.5 x the median 4h
range, 1%-6%) clears the exchange minimum value and quantity with a quantity step <= 25% of the order and <= 20x
leverage. Score = mean percentile of turnover, cost ratio and step granularity. Universe = the best 10. BTC / ETH are not
special-cased (they fail the 20 USDT sizing test and appear only in capacity analysis if at all).

**Amendment 0 (before any V5 replay, not based on any result).** As first written, the sizing gate tested a TAKE
order at the TYPICAL V5 stop; on the DEVELOPMENT scoring month (2025-02, 4h ranges 350-715 bps) that excluded 38 of 40
coins, because a 1% risk (0.20 USDT) at a ~4-6% stop is a 3-5 USDT order, below Bybit's 5 USDT minimum. That contradicts
the protocol's own design, where an undersized trade is SKIPPED and reported MIN_NOTIONAL_LIMITED and the 50 / 100 USDT
capacity twins separate NO EDGE from SMALL-ACCOUNT CONSTRAINED. The gate now tests STRUCTURAL feasibility: a TAKE order
at the tightest V5 stop (1%, i.e. 20 USDT notional) must clear the minimum value, minimum quantity and a quantity step
<= 25% of the order (BTC, ETH, SOL, SUI, LTC and BNB still fail). Whether a typical stop is legal at 20 USDT is recorded
per coin (`typical_stop_legal_at_20`), not gated. DEVELOPMENT universe (scored 2025-01-30..2025-02-28): POPCAT, LDO,
ENA, WLD, WIF, APT, GALA, TAO, RUNE, VIRTUAL.

## 4. Families (8) — each one market hypothesis

| id | family | hypothesis (what it exploits) | expected hold | trades / month (H / S) | favourable move sought | should fail when |
|---|---|---|---|---|---|---|
| V5.1 | POSITIONING_TREND | a 1D+4h trend that NEW positioning is joining (price and OI rising together) persists for hours to days; a trend on falling OI is short covering and fades | 8-48 h | 20-40 / 5-10 | 2-6% | choppy regimes; OI rising on hedged / basis positions |
| V5.2 | FUNDING_OI_CROWDING_REVERSAL | an extreme funding percentile + OI at a local high + price failing to extend = one side crowded and paying; the unwind reverses price | 8-48 h | 2-8 / 1-4 | 3-8% | strong trends where crowding persists |
| V5.3 | COMPRESSION_BREAKOUT_OI | a multi-hour volatility compression resolved by a breakout WITH rising OI and relative volume is new money, not a stop run | 8-48 h | 3-10 / 1-4 | 3-8% | range regimes with false breaks |
| V5.4 | TREND_PULLBACK_POSITIONING | a 1D/4h trend pullback to value, while funding is NOT crowded and OI is not collapsing, resumes | 8-48 h | 10-30 / 3-8 | 2-5% | trend exhaustion |
| V5.5 | DELEVERAGING_REVERSAL (OI proxy) | a sharp OI drop together with a large same-direction price move is forced deleveraging; once it stops, price partially mean-reverts | 4-24 h | 2-6 / 1-3 | 3-10% | genuine regime breaks where the cascade continues |
| V5.6 | CARRY_AWARE_TREND | trend premium net of carry: take Donchian trend breaks only when the expected funding over the hold does not eat the edge (favour trades that RECEIVE funding) | 24-72 h | 4-12 / 2-6 | 4-10% | whipsaw ranges |
| V5.7 | STRUCTURAL_RANGE_REVERSAL | in a flat 1D regime, a rejected test of the multi-day range boundary WITH positioning crowded into the boundary reverts toward the range middle | 8-48 h | 2-8 / 1-4 | 3-6% | real breakouts |
| V5.8 | MOMENTUM_CONTINUATION_OI | a top-decile multi-hour impulse carried by rising OI, followed by a controlled consolidation, continues | 8-48 h | 3-10 / 1-4 | 3-8% | exhaustion |

**Why V1-V4 never tested these:** every V1-V4 family used OHLCV only (plus Binance taker flow in two families),
decided on <= 30m bars and held <= 12 h; none used funding, open interest, basis or account ratio, and none held for
days with funding as a design variable. Stops are structural and ATR-based on the signal timeframe (clamped 0.8%-8%,
refused beyond); a risk-sized order below the exchange minimum is SKIPPED (MIN_NOTIONAL_LIMITED), never enlarged.

## 5. Costs (the competition pays everything)

The authoritative `BYBIT_LINEAR` FeeSchedule (maker 0.020%, taker 0.055%; no private fee constants in strategies), the
execution model (half spread from tick + volatility, latency), Bybit instrument filters, paper liquidation at the
position's leverage, and funding charged / received at every actual Bybit settlement instant while a position is open.
Every result reports gross PnL, maker fees, taker fees, slippage, funding paid, funding received and net PnL.

## 6. Risk — AGGRESSIVE_V5 (20 USDT official; 50 / 100 USDT capacity diagnostics only)

TAKE 1.0% of equity at risk; HIGH CONVICTION 1.5% (reserved); ATTACK 2.0% (Jev STRONGLY SUPPORT only, a healthy bot and
a legal order). 20x leverage ceiling (margin capacity, not risk). No ATTACK at >= 18% below peak; halt at 30%.

## 7. Stages and gates (pre-registered)

**Stage 1 — RAW DISCOVERY (DEVELOPMENT, no Jev, no gate).** 8 families x 10 coins x 2 classes = 160 ungated CONTROL
bots (one position at a time), baseline exit = structural stop + class time stop (HOURLY 24 h, SWING 72 h), no target,
no trail. A family x class has a RAW EDGE only if ALL hold, pooled over its coins: trades >= 100 (HOURLY) / >= 40
(SWING); mean GROSS R > 0 with bootstrap P(mean <= 0) <= 5%; gross R > 0 in >= 2 of the 3 six-month DEVELOPMENT
sub-periods; gross R > 0 on >= 50% of its coins with >= 10 trades; still >= 0 without its best 5% of trades.
MFE / MAE at 1h, 2h, 4h, 8h, 12h, 24h, 48h, 72h after entry are measured for every trade (DEVELOPMENT only).

**Amendment 1 (before any DEVELOPMENT result; from a 2-month ENA smoke test of the plumbing only).** At 20 USDT most
hourly / daily setups are refused by the exchange minimum (1% risk = 0.20 USDT at a 4-8% structural stop is a
2.5-5 USDT order), so a raw-edge verdict read from the 20 USDT books alone would judge a family on a censored,
non-representative subset of its entries. Stage 1 therefore runs every CONTROL twice: at 20 USDT (the official book,
reporting MIN_NOTIONAL_LIMITED) and at 100 USDT (a CAPACITY twin where every setup is legal). **The family raw-edge
gate is read from the 100 USDT twins** (per-trade R is size-independent: fees and R both scale with notional); the
economic stage and every bot gate stay at 20 USDT, and only 20 USDT books can advance. A family whose entries have a raw
edge but whose 20 USDT books cannot trade enough is reported SMALL-ACCOUNT CONSTRAINED, not advanced.

**Stage 2 — ECONOMIC DISCOVERY (DEVELOPMENT, raw-edge survivors only).** Exit selected per family x class on
DEVELOPMENT from {time stop 8 / 12 / 24 / 48 / 72 h} x {no target, 2R, 3R} by mean NET R on the same entries; then the
survivors re-run with that exit. A family x class passes if pooled NET R > 0 with bootstrap P(mean <= 0) <= 10% and net
R > 0 in >= 2 of 3 sub-periods. Conservative maker-first entry is studied alongside; the taker baseline decides.

**Amendment 2 (before any DEVELOPMENT result; written while building the Stage 2 code).** (a) Each class selects its
time stop only inside its own hold range (§1): HOURLY from {8, 12, 24} h, SWING from {12, 24, 48, 72} h; a plan
replaces the pre-registered baseline only if it beats it by >= 0.02 R of pooled net R. The grid is a counterfactual on
the Stage 1 entries (the trade's own stop, checked first inside a bar; its own costs; the actual funding settlements),
read from the 100 USDT twins; the re-run decides. (b) The replay engine sends every strategy entry as a MARKET order —
it has no resting-limit path for strategy entries — so a maker-first entry cannot be simulated without new execution
code. Instead of a simulated maker study, Stage 2 reports the BEST-CASE maker saving per family x class: every entry
filled as maker at the signal price with no spread (the taker-maker fee difference plus the entry half of the measured
slippage, in R). A family whose net R stays <= 0 even with that bound cannot be rescued by maker entries; the taker
baseline decides in every case, and all V5 fees are reported as taker fees (maker fees = 0 by construction).

**Stage 3 — JEV (DEVELOPMENT, Stage 2 survivors only; no Jev call is spent on a family that loses before Jev).** Per
surviving bot: +JEV5, ALWAYS-TAKE, ALWAYS-SKIP, 20 RANDOM twins with the Jev bot's own action distribution, CAPACITY
50 / 100.

**Stage 4 — FREEZE -> PSEUDO-HOLDOUT.** Sources, parameters, exits, universe rule, Jev prompt / policy, risk profile,
fees and execution frozen (`docs/V5_FREEZE.md`); the holdout pre-registered (`docs/V5_TEST_PREREGISTRATION.json`)
before its data is downloaded; every family runs (survivors with their frozen exits). If the holdout fails, the answer
is V6 — V5 is never patched.

**Bot gates (ADVANCED = every gate in DEVELOPMENT and PSEUDO-HOLDOUT):** its family x class passed Stages 1-2;
trades >= 30; activity HOURLY >= 0.25/day, SWING >= 1/week (otherwise POSITIVE_EDGE_LOW_ACTIVITY at best); gross R > 0;
net PnL > 0; net R > 0; PF >= 1.10; bootstrap P(mean net R <= 0) <= 10%; max drawdown <= 30% and never halted; no
liquidation; net > 0 without its best 3 trades and the top 3 <= 60% of gross profit. Jev bots also: beat the matched
random 90th percentile with positive selection alpha; beat the CONTROL by >= 0.50 USDT; AUC 95% CI above 0.50;
skip <= 80%; API errors <= 2%. An ADVANCED bot then needs multi-year (continuous 20 USDT, no resets), Monte Carlo,
stress (fees +25% / +50%, slippage 1.5x / 2x, adverse funding, delayed entry, widened spread, combinations) and FORWARD
VALIDATION before QUALIFIED. A 50 / 100 USDT result never qualifies a 20 USDT bot.

## 8. Jev V5

Downstream of legality, a raw-edge family and positive setup expectancy. Question: *"Given that this setup belongs to a
strategy with positive historical raw edge, do current positioning, trend, volatility and funding conditions CONTRADICT,
SUPPORT or STRONGLY SUPPORT the setup?"* CONTRADICT -> SKIP, SUPPORT -> TAKE (1%), STRONGLY SUPPORT -> ATTACK (2%).
No DEFENSIVE size. State: 1h / 4h / 1D trends, funding, funding percentile and change, OI and OI change 1h / 4h / 24h,
price/OI divergence, basis, volatility regime, relative volume, the thesis, the family's DEVELOPMENT expected gross edge,
the estimated round-trip cost and the expected funding over the planned hold, bot drawdown. Never: future returns,
the trade's result, holdout results or leaderboard ranks. Every ATTACK is compared with the same trade at normal size.

## 9. Runtime focus

The 27 V1 paper bake-off books and the V2 forward-shadow bots are frozen failed experiments (V1: no season or
multi-year pass; V2: 0 of 3 survivors passed multi-year). Their workers stop (engine `BAKEOFF_ENABLED=false`,
`LIVE_SHADOW_ENABLED=false`); the engine itself (market feed, RiskManager, kill switch, router in dry run) keeps running;
every database record, report and reproducibility path is kept (`docs/V5_RUNTIME_AUDIT.md`).

## 10. Rule

A bot only succeeds if GROSS EDGE - FEES - SLIPPAGE +/- FUNDING leaves a positive NET edge, with enough activity. No fee-free
winner, no loosened gate, no PnL-picked coin, no holdout tuning, no leverage to hide weakness. If none succeed:
**ADVANCED SET = NONE.**
