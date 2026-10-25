# Agent 01: leveraged, volatility-targeted trend following. Verdict: FAIL

**The idea.** Take the trend rules that made about +10–17% a year unlevered and add leverage until the average month reaches +20%. The rules tested were Donchian breakout, EMA cross and momentum, long and short, with an ADX(14) ≥ 25 filter. Each position is sized to a volatility target (`tv`) and held as an isolated-margin lot. A lot is liquidated when its loss reaches 1/lev − 0.5%, checked on every bar's high and low. The test charges fees, slippage and funding, and caps size at 1% of the bar's volume. DEV covered 2021-01 to 2024-10 and was swept over 6 rules × 2 filters × 9 leverage levels on BIG10 daily and 4h bars. It also covered BTC/ETH/SOL on 4h and 1h, a point-in-time top-30/50 universe, and pyramiding with 2 or 4 adds.

**DEV findings.**
- The best compound growth on DEV was about +7% a month (geometric). Nothing reached the target.
- The best was Donchian-120 on 4h with ADX, at a 3.0 volatility target (87% drawdown). That is growth-optimal, so more leverage only lowers compound growth.
- A **+20% mean month** on DEV needed a 4.0 target. That is about 5–6x per open position (average gross 1.9x, peaks at 10x).
  - At that level the median month was −4% and the drawdown was 97%.
  - The mean was only that high because of a few huge months.
- Most of DEV's profit came from the 2021 bull market (about +13–39% a month in 2021, about 0–6% in 2022–24).
- Neither the broader universe, the 1h/4h bars nor pyramiding beat BIG10 daily or 4h on compound growth.

**TEST (2024-11 to 2026-10, run once)**, pre-registered attempts:

| attempt | mean | median | worst | geo | net | max DD | trades | liqs | mean at 2x costs |
|---|---|---|---|---|---|---|---|---|---|
| **A_target20** (Donch-120 4h ADX, tv 4, primary) | **+2.6%** | **−18.6%** | **−53.3%** | −7.7% | **−85%** | **97.3%** | 615 | 12 | +0.3% |
| B growth-optimal (same, tv 3) | +2.7% | −13.3% | −40.8% | −4.0% | −63% | 91.9% | 614 | 6 | +0.9% |
| C moderate (same, tv 1) | +2.0% | −3.9% | −14.2% | +0.9% | +25% | 47.6% | 612 | 1 | +1.4% |
| D EMA5/100 + ADX daily, fixed BIG10, tv 3 | +23.4% | +2.0% | −27.9% | +13.2% | +1844% | 68.2% | 145 | 6 | +23.0% |
| E mom60 daily, point-in-time top-50, tv 2 | +7.7% | +0.8% | −17.4% | +4.9% | +217% | 48.3% | 1967 | 23 | +7.3% |

A's monthly returns, Nov 2024 onward:
+77, −20, −43, +8, −41, −22, +36, −32, +108, −43, +10, −46, +20, −53, −3, −5, −18, −23, −24, +92, −42, +127, +28, −33.

**Why D does not count.**
- On the numbers alone, D clears the bar. Its biggest trade is 22% of the profit, and it still gains at doubled costs.
- But the BIG10 list was picked with today's knowledge. Only 4 of the 10 coins were in the real point-in-time top 10 by volume on 2024-11-01. That breaks the protocol's no-look-ahead rule for the universe.
- Run on a point-in-time universe (checked after TEST, not pre-registered), the same rule made **+8.2% / +10.0% / +8.5%** a month for the top-10/15/20, and its DEV compound growth was negative.
- One month, Nov 2024 (+277%), supplies half of D's mean: without it the mean falls to about 12%.
- Other checks after TEST:
  - The ADX filter is essential: without it, D fell to +5.5% a month with a 93% drawdown.
  - The 4h version lost money.
  - Neighbouring rules on BIG10 made +16–26% a month, but all used the same biased list.

**Leverage and risk of ruin** (Monte Carlo, 24-month paths bootstrapped from DEV months):
- At A's leverage (tv 4): 97% chance of a ≥50% drawdown and 58% chance of a ≥80% drawdown. 30% of paths end below the start. Only 50% average +20% a month.
- Scaling the tv-1 strategy's DEV days by L (1–6):

| L | chance of ruin | chance of ≥ +20% mean month while alive |
|---|---|---|
| 3 | 0% | 37% |
| 4 | 1% | 48% |
| 5 | 9% | 48% |
| 6 | 56% | 29% |

These bootstraps draw on DEV months, which include 2021, so they are optimistic. On real TEST, the 20%-target leverage lost 85%.

**Verdict: FAIL.** Trend following earns about +2–8% a month in compound terms in good regimes, and less at present. Leverage can raise the *arithmetic* mean month towards 20%, but only by making the median month deeply negative and the drawdown 90–97%. Leverage needed: about tv 4, i.e. 5–6x per position. The one figure above 20% (D) comes from a hindsight-chosen coin list.

Caveats:
- Survivorship bias: delisted coins (LUNA, FTT and others) are missing from the universe.
- The first TEST run had a bookkeeping bug that added a phantom 0% month for October 2024. It was fixed and rerun with no change to any strategy; the first output is kept as `results_first_run_phantom_month.json`.

Files: `run.py` (the `dev <stage>`, `test` and `sens` commands), `engine.py`, `PREREG.md`, `results.json`, the `dev_sweep_*.json` files and the sweep logs.
