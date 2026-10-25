# Agent 03: funding-rate strategies on Binance USDT perps. Verdict: **FAIL**

**The idea.** Funding is the one return in crypto that doesn't need a price forecast. I tried three ways to use it.
(a) **Carry:** long spot and short the perp on the 10 coins (out of the top-40 by volume) with the highest 7-day funding, rotated daily. Leverage of 1x, 3x, 10x and 30x is borrowed at 10% a year.
(b) **Contrarian cross-section:** short the most crowded longs and long the most crowded shorts (XS_LS). There is also a long-only "buy the most negative funding" version (XS_LONGNEG).
(c) **Leverage on the carry book.**

All rules were fixed on DEV (124-config grid in `dev_grid.json`) and frozen in `PREREG.md`. The 7 attempts then ran once on TEST.

**How it was tested.** Daily bars, fills at the next open, 0.07% per side per leg, and funding on every stamp held. Liquidation is checked on daily highs and lows. For carry, the check uses the spot-versus-perp basis. Size is capped at 1% of the day's volume. The universe is re-picked each month point-in-time, from today's symbol list, so it carries survivorship bias.

| attempt (TEST 2024-11..2026-10) | mean/mo | median | worst mo | max DD | net | trades | liq. | 2x-cost mean |
|---|---|---|---|---|---|---|---|---|
| CARRY_1x | +0.17% | 0.00% | -0.05% | 0.2% | +4.2% | 89 | 0 | +0.07% |
| CARRY_3x | -0.25% | -0.07% | -10.6% | 13.0% | -6.4% | 90 | 1 | -0.54% |
| CARRY_10x | -1.10% | -0.23% | -21.4% | 40.3% | -26.5% | 92 | 3 | -2.07% |
| **CARRY_30x (primary)** | **-2.19%** | **-0.70%** | **-23.4%** | **64.1%** | **-46.3%** | 94 | 5 | -5.45% |
| XS_LS_1x | +1.72% | +0.29% | -30.7% | 54.1% | +15.0% | 914 | 23 | +1.24% |
| XS_LS_2x | +1.06% | -4.76% | -48.3% | 79.0% | -34.9% | 919 | 70 | +0.29% |
| XS_LONGNEG_1x | +17.78% | -7.11% | -62.9% | 84.7% | +343.9% | 364 | 0 | +17.04% |

Monthly TEST table, primary and the strongest of the rest:

| month | 24-11 | 24-12 | 25-01 | 25-02 | 25-03 | 25-04 | 25-05 | 25-06 | 25-07 | 25-08 | 25-09 | 25-10 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CARRY_30x | 17.2 | 15.1 | -5.4 | -2.3 | -0.3 | 0.0 | -2.9 | -0.6 | -18.2 | -14.8 | -1.9 | -23.4 |
| XS_LONGNEG | 72.5 | 1.1 | -9.9 | -41.9 | -21.0 | -6.6 | -19.3 | -19.1 | -2.0 | -18.4 | 107.1 | -24.5 |

| month | 25-11 | 25-12 | 26-01 | 26-02 | 26-03 | 26-04 | 26-05 | 26-06 | 26-07 | 26-08 | 26-09 | 26-10* |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CARRY_30x | -0.3 | 0.0 | 0.0 | 0.0 | 0.0 | 1.3 | 3.1 | -0.8 | -2.7 | -3.8 | -7.9 | -4.2 |
| XS_LONGNEG | -20.6 | 51.1 | 98.8 | 67.4 | -62.9 | 71.5 | -17.9 | 177.6 | 44.5 | 19.5 | -7.7 | -12.7 |

(*October 2026 is a partial month.)

**Why carry dies.**
- Funding was rich in 2021: the top-10 coins paid about 80% a year. It has been thin since: 6-30% a year for the top 10, and below zero averaged over the whole universe in 2025-26. Carry at 1x made +5.3% a month in 2021 but only +0.6% a month in 2022-24, and +0.17% a month on TEST. Most TEST months it sits in cash.
- Leverage multiplies a small yield but also the basis blowouts. On TEST these were BANANAS31 in July 2025 (perp squeezed 12% over spot), the HEMI and PUMP wicks in the 10 Oct 2025 crash, and UNI and 1MBABYDOGE. They liquidated hedged legs at 3x and up.
- The 10% a year borrow on the spot leg eats the rest. **No leverage from 1x to 50x reaches 20% a month on TEST, at either cost level.** On DEV, 30x was needed, and only because of 2021.

**Unhedged shorts are worse.** An isolated short perp held 7 days is liquidated 7% of the time at 2x, 15% at 3x, 30% at 5x, 55% at 10x and 75% at 20x. These rates are about the same on DEV and TEST.

**XS_LONGNEG is a lottery, not an edge.**
- Its mean month of +17.8% comes from a few squeeze months. The median month is -7%, the drawdown 85%, and the top 3 trades are 109% of all profit, which breaks the single-trade rule.
- Post-hoc, about 1.25x would lift the mean to 20%. But at 1.25x the drawdown is 91%; at 2x the mean month is +46% while the account loses 45%; at 3x it is wiped out.
- On DEV it was also 2021 short-squeeze beta.

**Verdict: FAIL.**
- The primary attempt loses money (-2.19% a month at baseline, -5.45% at doubled costs).
- Nothing passes the PASS rules.
- Leverage needed for 20% a month: none works for carry (null). The contrarian long book needs about 1.25x on paper, with a drawdown of about 91% and one liquidation; by 3x it is wiped out.

Files: `run.py` (`dev` / `test`), `fetch.py` (downloads through common.py), `PREREG.md`, `dev_grid.json`, `results.json`.
