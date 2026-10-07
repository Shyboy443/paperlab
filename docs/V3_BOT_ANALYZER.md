# V3 Bot Analyzer

Code: `app/competition/v3_analyzer.py` (per bot and per arena), fed by `app/competition/v3_arena.py`
(the replay record) and orchestrated by `app/competition/v3_run.py`. It only READS finished bots.

## Inputs (one record per bot, kept in `v3_bots`)

* every closed trade: entry/exit time, hold, gross at decision prices, fees, slippage, funding, net,
  R (net / initial risk), exit kind, signal quality, planned EDGE_COST_RATIO, sizing state and tier,
  risk, leverage, Jev level and multiplier;
* every gate decision (+JEV2 and the baselines): signal time, side, P(win), Jev's chosen action, the
  final level after the deterministic rules, multiplier, latency, error, and the candidate's outcome —
  the real trade if taken, the engine's shadow trade (same exits, control size) if skipped;
* activity counters: signals, cost-gate rejections, exchange-minimum refusals, other RiskManager
  refusals, halts; the equity curve (downsampled).

## Per-bot blocks

| block | what it answers |
|---|---|
| ACTIVITY | candidates/day, Jev calls/day, accepted/day, trades/day and per 30 days, average hold, median time between trades |
| EDGE | gross vs net expectancy in USDT, R and bps of notional; gross and net PF; win rate; net without the best 3 trades; largest winner's share |
| COST | fees, spread+slippage, funding; average cost per trade; round trip in bps of notional; cost share of gross winners; cost-to-edge; realized vs planned edge/cost |
| QUALITY | results by signal-quality band (0–0.30, 0.30–0.45, 0.45–0.60, 0.60–0.75, 0.75–1.0) |
| JEV | P(win) quartiles; chosen and final action mix; skip and ATTACK rates; AUC of P(win) and of the action (Mann–Whitney, 95% Hanley–McNeil interval, n); calibration by P(win) band; accepted and skipped winners/losers and mean R; latency p50/p95/p99; errors; cost; a 2,000-permutation test of the multipliers against the outcomes |
| REGIME | by daily trend (TREND_UP / TREND_DOWN / RANGE) and volatility (HIGH / NORMAL / LOW) regime, the V2 multi-year definitions, from daily closes up to the window's end only |
| TIME OF DAY | by UTC session: ASIA 00–08, EUROPE 08–13, US 13–21, LATE_US 21–24 |
| GATES | the pre-registered discovery gates (docs/V3_PROTOCOL.md), each with actual value and threshold |

## Failure mode (one, the ROOT cause)

Evaluated in this order, so a symptom never hides its cause:

1. LIQUIDATION
2. MIN_NOTIONAL_CONSTRAINED — most signals below the exchange minimum and too few trades
3. JEV_OVER_FILTERING — a +JEV2 bot skipping more than 80% of what reached Jev
4. NO_GROSS_EDGE — loses before any cost (then any halt or low activity is a consequence)
5. OVERTRADING / SLIPPAGE_DESTROYED / FEE_DESTROYED — gross positive, net not
6. DRAWDOWN_FAILURE — net positive but ≥ 30% drawdown or halted
7. TOO_LOW_ACTIVITY — net positive but below the participation minimum
8. PROFIT_CONCENTRATION, REGIME_DEPENDENT, MARGINAL_EDGE, JEV_BAD_DISCRIMINATION, JEV_NO_VALUE
9. ROBUST — every gate passed (shown as HEALTHY)

LATENCY_SENSITIVE needs a latency-stress replay and is reserved for bots that are otherwise ROBUST.

## NEXT VERSION HYPOTHESIS

Each failure mode maps to a hypothesis phrased with the bot's own numbers (best quality band, cost vs
gross bps, dominant regime, worst session, Jev's skipped-candidate expectancy …). It is a proposal
only: a change becomes V3.1, developed on DEVELOPMENT data and evaluated only on data it has never
seen. The analyzer never edits a strategy or re-runs one with a change.

## Arena level

Leaderboards (Jev field; every scanned control), failure-mode counts, strategy × timeframe and
coin × timeframe matrices, CONTROL vs +JEV2 against ALWAYS-TAKE / ALWAYS-SKIP / RANDOM-FILTER, pooled
Jev decisions / AUC / calibration / latency, a timeframe verdict per family, the ranking score (kept
separate from the gates, catastrophic bots capped) and the ADVANCED SET (possibly NONE).
