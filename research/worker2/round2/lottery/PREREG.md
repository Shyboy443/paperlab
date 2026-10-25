# PREREG - Worker 2 round 2 / lottery (MAX / skewness) - written 2026-10-25 after the DEV grid, BEFORE any TEST run

## Hypothesis
Retail overpays for lottery-like coins: alts with the most extreme recent maximum return (or highest skewness) underperform
over the next week, the least lottery-like outperform. Weekly market-neutral book, 1x gross.

## Engine (core.py)
Survivorship-free hourly panel (r4_09_session/base.py, 431 coins ever in the top-40 incl. delisted, hourly funding).
Universe U40 = top-40 by trailing 30-day quote volume known at the 00:00 UTC open (BTC/ETH included).
Score known at the open of day d from bars before it. Long the k LOWEST scores, short the k HIGHEST.
Equal-risk sizing: inside a leg w ~ 1/sigma (480 h hourly vol at the open); legs scaled so long-leg risk = short-leg risk;
gross = 1x equity. Rebalance every R days at 00:00 UTC, fill at the open, exit at the next rebalance open; delisted coins
closed at last close. Costs 0.05% fee + 0.02% slippage per side on traded notional (drifted weights vs target), hourly
funding on entry notional. Doubled-cost run too. No volume cap (a 500 USDT account never hits one).

## DEV grid (dev.py, 2021-01..2024-10 only; all 36 rows in dev_grid.json)
feature {max1d, max5d, maxh, skewh, skewd, smax (=MAX5/vol), smaxh} x L {7,14,30} (daily features need L>=14) x k {5,8};
R = 7, equal-risk. Rule (written before running): DEV net > 0 at baseline AND doubled costs AND DEV median month > 0,
then highest DEV t-stat. Result: only 1/36 eligible; DEV t of the winner = 0.71 (no evidence of an edge on DEV).
Honest DEV observation: the vol-scaled lottery sorts (smax, smaxh) are strongly NEGATIVE on DEV (t -2.0 .. -2.5), i.e.
the OPPOSITE of the hypothesis (lottery coins outperformed). This was not in the hypothesis and is not selected.

## The ONE selected config (frozen)
feat = max1d (largest daily close-to-close return over the last 14 days), L = 14, k = 8 per leg, R = 7 days, equal-risk.
DEV 1x: median +0.31%, geo +0.59%, mean +1.12%, net +31.7%, doubled-cost net +14.0%, max DD 50.0%, t 0.71.

## Low-vol overlap (DEV, lowvol_dev.json)
max1d L14 vs 14-day hourly vol: mean cross-sectional rank corr 0.74; 67% of the short leg is also in the top-8 by vol;
monthly corr of the max1d book with the same book sorted on plain low-vol = 0.78. (smax: rank corr 0.25, skewh 0.43,
maxh 0.92.) So the selected MAX sort is largely a low-vol sort, as warned in the brief.

## TEST 2024-11-01..2026-10-21: run ONCE (baseline + doubled costs). No re-tuning.

## Checks (ROBUSTNESS.md, 1x, 2021-01..2026-10)
1-4, 8 exactly as written. 5: neighbours one at a time from (L=14, k=8, R=7): L {7,10,18,21}; k {4,6,10,12};
R {3,5,10,14} -> 12 neighbours, PASS if >= 70% net > 0 on both DEV and TEST at baseline costs.
6: 200 random books (seeds 0..199): same U40 eligibility, k=8 per leg, equal-risk sizing, rebalance dates, costs; random
coin picks. PASS if the strategy's full net beats >= 95%.
7: remove the 5 coins with the largest P&L (whole period) from the universe -> full net > 0; and no single position
(one coin, one continuous holding) > 10% of total profit.

## Info only (NOT eligible for selection or REPLACE): the reversed vol-scaled sort, LONG high smax / SHORT low smax
(L=14, k=8, R=7, equal-risk), run on TEST once alongside, to see whether the DEV "anti-lottery" observation holds.
Also reported: Worker 1 correlation (monthly 2022-01..2026-10 and daily), 2025-01..2026-10 at doubled costs,
2026 YTD, October 2026, trades/month, 500 USDT smallest-order feasibility.
