# PREREG - r4_07_ensemble (written 2026-10-23 after the DEV run `python run.py dev`, BEFORE any TEST run of the ensemble)

## Idea
Combine the three round-2 streams that were closest to robust into one book, with causal weights, then a causal
volatility target (leverage cap 10x): R = momentum rotation (r2_rotation, 14d return, top-40, long 10 / short 10,
weekly), M = ML walk-forward (r2_ml A_hgb_H6_k3, cached walk-forward predictions, long 3 / short 3, daily),
T = 60-day momentum (r2_trend T2, sign of 60d return, monthly top-50, inverse-vol weights, monthly resize),
optional B = BTC 60-day momentum sign. Honest caveat: the legs were chosen because of round-2 results that already
included the TEST window, so TEST is not fully "unseen" for the legs; only the ensemble/weighting is new.

## Engine (ens.py)
One position-level account on r2_trend's survivorship-free daily panel (archive, delisted coins included, non-crypto
excluded). Sleeve budget = L x w_i x equity; net units per coin; fee 0.05% + slippage 0.02% per side on net traded
notional; funding; net notional <= 1% of the previous day's quote volume; isolated-margin liquidation per coin at
1/L - 0.5% adverse from entry on the day's high/low (whole margin lost), also at 1x; delisted coins closed at last close.
Legs re-size exactly when the round-2 strategy would (R every 7 days from the run start, M daily, T on flips + 1st of
month); every sleeve is re-sized when the book weights or the leverage change.

## DEV grid (2021-01..2024-10, 1x) and selection rule (fixed before running it: highest DEV t-stat of the monthly mean)
| legs | weights | DEV mean | median | geo | max DD | t |
|---|---|---|---|---|---|---|
| RMT | EW | 3.08 | 2.62 | 2.83 | 23.4 | 2.90 |
| **RMT** | **IV** | **3.16** | **1.87** | **2.96** | **20.2** | **3.32** |
| RMT | ERC | 1.79 | 1.59 | 1.60 | 32.3 | 1.95 |
| RMTB | EW | 3.00 | 4.66 | 2.68 | 28.9 | 2.50 |
| RMTB | IV | 3.22 | 3.55 | 2.97 | 27.4 | 2.99 |
| RMTB | ERC | 3.22 | 2.77 | 2.93 | 32.2 | 2.80 |

## SELECTED (frozen)
- Legs R + M + T (no BTC leg), leg parameters exactly as in round 2.
- Weights: inverse volatility, w_i proportional to 1 / sd(daily standalone 1x leg returns over the trailing 60 days,
  rows before day d only), equal weight until 20 observations; re-set every 7 days from 2021-01-01.
- 1x-equivalent book = weights sum to 1, L = 1. All 8 checks are run on this book.
- Volatility target (judged separately, never above 10x): L_d = min(10, sigma* / realised vol of the 1x book's daily
  returns over the trailing 30 days, rows before d), changed only when it moves by > 20% (relative).
  Primary sigma* = 0.75 (75%/yr) = the highest DEV geometric mean month in the DEV sweep {0.25..2.0}
  (DEV: mean 7.19, geo 4.85, DD 61.9%, 661 liquidations). Reported over the grid sigma* {0.25, 0.5, 0.75, 1, 1.5, 2, 3}
  and constant L {1, 1.5, 2, 3, 5, 7.5, 10}. DEV already shows 20%/month is not reached at any setting <= 10x.

## The 8 checks (ROBUSTNESS.md), at 1x
1. DEV and TEST run separately (fresh equity), net > 0 at baseline and doubled costs.
2. Calendar years 2021..2026 compounded from the full run; >= 4 of 6 positive; 2022-01..2026-10 > 0.
3. 70 months: t >= 2.0; moving-block bootstrap (3-month blocks, 5,000 draws, seed 7) 5th percentile of the mean > 0.
4. Full run without its 3 best months, compounded > 0.
5. Neighbours (22), each one change from the selected config, PASS if >= 70% are net > 0 on DEV and on TEST:
   weight lookback {30, 45, 75, 90}; weight re-set every {1, 3, 14, 30} days; weights {EW, ERC}; legs RMTB (IV);
   rotation lookback {7, 10, 18, 21}; ML k {2, 5}; ML model seed 1; momentum lookback {30, 45, 75, 90}.
6. 200 random versions: every leg picks coins at random with its own structure (R: 10 random longs / 10 shorts from
   the same eligible set on the same days; M: 3 / 3 random from coins with a prediction; T: each month the side
   pattern re-assigned to random coins of that month's universe), same weight path as the real book, same costs.
   PASS if the real full-period net beats >= 95% of them.
7. The 5 coins with the largest summed position P&L in the full 1x book are removed from every leg (R and T refill
   their universes; M simply loses those coins' predictions, no refill, because its model would have to be re-trained)
   and the book is re-run with re-computed weights: net > 0. And the largest single position (one coin, from open to
   close/flip of the net position) <= 10% of the full-run total position P&L.
8. Full run geometric mean month > 0 and >= 0.5 x arithmetic mean month.
Also reported: each leg alone, leg correlations, 2026 YTD, and the leverage / vol-target tables (DEV, TEST, full)
with mean / median / geo / worst month, max DD, liquidations and wipe-outs.
