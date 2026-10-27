# PREREG: merged_signal (Worker 3, round 5)

Part 1 written 2026-10-27 BEFORE any candidate was run (only the two reference books W1 and H had been run on DEV, to
check the engine). Part 2 (DEV table + selected config) is appended after the DEV run and before TEST / HOLDOUT.

## Idea
Run ONE book that merges Worker 1's signals (r4_07 ensemble: R = 14-day-return rotation top-40 L10/S10 weekly,
M = ML walk-forward L3/S3 daily, T = 60-day momentum sign, inverse-vol legs) with the high52 signal (30-day range score
(close - 30d low) / (30d high - 30d low), round4/high52_v2) instead of running them as two separate books.

## Literature
- George, T. & Hwang, C.-Y. (2004), "The 52-Week High and Momentum Investing", Journal of Finance 59(5): nearness to
  the 52-week high explains most of momentum profits and its profits do not reverse; in a joint horse race the 52-week
  high measure dominates past-return momentum. -> motivates A (rank-average of return and nearness-to-high) and B
  (nearness-to-high as a filter on momentum picks).
- Bhootra, A. & Hur, J. (2013), "The timing of 52-week high price and momentum", Journal of Banking & Finance 37(10):
  stocks whose 52-week high was set RECENTLY outperform those near an old high (a recency effect on top of nearness).
  -> motivates A3 (adds a "days since the 30-day high" rank).
- The crypto Donchian/range work of rounds 2-4 is context only. (Citations from memory; the papers were not re-read
  here, so treat the one-line summaries as approximate.)
- Lookback is 30 days, not 52 weeks: that is the round-4 high52 lead's length, and crypto's horizon is shorter.

## Engine and data (unchanged Worker 1 engine)
- `research/monthly20/r4_07_ensemble/ens.py` simulate(): net units per coin, fee 0.05% + slippage 0.02% per side,
  funding, 1% of prior-day volume cap, 1x isolated-margin liquidation rule, delisted coins closed at last close.
- DEV/TEST: W1's perp panel (data/monthly20_r2_trend/panel.npz) + cached ML predictions.
- HOLDOUT 2018-01..2020-12: W1's spot backfill panel (data/w1_backfill/panel.npz, funding 0, shorts on spot ASSUMED
  possible) + its spot walk-forward ML predictions; legs without a signal yet get weight 0 (worker1_backfill rule).
- Leg weights: inverse vol of each leg's standalone 1x daily returns, trailing 60 days, re-set every 7 days (= W1),
  causal (rows < d). EW / ERC only where the config says so.
- high52 leg H on this engine: top-40 universe by 30d quote volume (leg_rotation rule), long top-10 / short bottom-10
  by the range score, daily resize. Reference: DEV geo +3.37% (round 4's own engine: +3.48%), close enough.
- Reproduction: W1 on DEV here = geo 2.96 / median 1.87 / DD 20.21 / t 3.32, identical to r4_07 dev.json.

## Candidates (16) — total config count = 16 (+2 references W1, H, which cannot be selected)
| id | what |
|---|---|
| A1_rank_w | R replaced by rank-average 0.5 x pct-rank(14d return) + 0.5 x pct-rank(range30), L10/S10, weekly; + M + T |
| A2_rank_d | same as A1 but resized daily |
| A3_rank_rec_w | A1 with a third equal rank: recency of the 30d high (Bhootra-Hur) |
| B1_filt50_all | W1 legs; drop any long with range < 0.5 and any short with range > 0.5 (to cash) |
| B2_filt70_all | same with X = 0.7 (longs need range >= 0.7, shorts <= 0.3) |
| B3_filt70_longs | X = 0.7, longs only (shorts untouched) |
| B4_Rrefill50 | R picks its 10 longs from coins with range >= 0.5 (shorts from <= 0.5) by 14d return, weekly; + M + T |
| B5_Rrefill70 | same with 0.7 / 0.3 |
| C1_Rhold70 | R with high52-style holding: a long stays while range >= 0.7 (short while <= 0.3), empty slots refilled daily from 14d-return ranks |
| C2_Rhold85 | same with 0.85 / 0.15 |
| C3_Tlongs_near_high50 | T's longs only while range >= 0.5 (keep momentum winners only while near their highs); R, M unchanged |
| D1_RMTH_IV | one book, four legs R M T H, inverse-vol |
| D2_RMTH_EW | same, equal weight |
| D3_RMTH_ERC | same, equal risk contribution |
| E1_rank_w_plus_H | A1 legs + H, inverse-vol |
| E2_filt50_plus_H | B1 legs (filtered R M T) + unfiltered H, inverse-vol |

## Selection rule (fixed now, DEV only)
Among the 16 candidates, keep those with DEV max DD <= 25%, DEV t-stat >= 2.5 and DEV net at doubled costs > 0.
Select the one with the highest DEV geometric mean month. Ties (within 0.01) -> lower DEV max DD.
If no candidate is eligible, select the highest DEV geo month anyway and say so.
The selected config is then run ONCE on TEST (2024-11..2026-10) and ONCE on HOLDOUT (2018-01..2020-12), with the 8
checks on both windows (TEST version = ROBUSTNESS.md over 2021-01..2026-10; HOLDOUT version = high52_v2's adaptation:
years 2018-2020 >= 2/3 positive and 2019-2020 > 0, 36-month t / bootstrap, etc.).

## Insights from TEST/HOLDOUT reports that I use
- "high52 is a long-side, BTC-uptrend effect": used only to motivate B3 (filter longs only) and C3; both are judged
  on DEV like every other candidate. Round 4 says the long leg made 97% of DEV profit, i.e. it is already a DEV fact.

---
# Part 2 (appended after `python dev.py`, BEFORE any TEST or HOLDOUT run of any candidate)

## DEV results (2021-01..2024-10, 1x, all costs; daily max DD; source dev.json / dev.log)
| config | median | geo | mean | up | max DD | t | net 2x cost | eligible |
|---|---|---|---|---|---|---|---|---|
| ref W1 | +1.87 | +2.96 | +3.16 | 29/46 | 20.2 | 3.32 | +143% | ref |
| ref H (high52 alone) | +2.77 | +3.37 | +3.86 | 24/46 | 33.6 | 2.52 | +190% | ref |
| A1_rank_w | +2.77 | +3.04 | +3.27 | 30/46 | 23.8 | 3.18 | +153% | yes |
| A2_rank_d | +1.96 | +2.95 | +3.20 | 30/46 | 24.9 | 2.97 | +121% | yes |
| A3_rank_rec_w | +3.20 | +2.48 | +2.70 | 27/46 | 27.7 | 2.64 | +98% | no (DD) |
| B1_filt50_all | +3.27 | +2.48 | +2.72 | 30/46 | 26.4 | 2.61 | +131% | no (DD) |
| B2_filt70_all | +1.30 | +1.23 | +1.45 | 26/46 | 32.5 | 1.45 | +43% | no |
| B3_filt70_longs | +1.61 | +1.03 | +1.38 | 28/46 | 28.5 | 1.08 | +18% | no |
| B4_Rrefill50 | +2.09 | +2.75 | +2.97 | 32/46 | 25.5 | 2.93 | +117% | no (DD) |
| B5_Rrefill70 | +2.61 | +2.91 | +3.14 | 30/46 | 20.3 | 3.06 | +133% | yes |
| C1_Rhold70 | +1.68 | +2.89 | +3.11 | 33/46 | 24.3 | 3.07 | +125% | yes |
| C2_Rhold85 | +1.87 | +2.62 | +2.86 | 31/46 | 23.5 | 2.72 | +93% | yes |
| C3_Tlongs_near_high50 | +2.64 | +2.99 | +3.15 | 30/46 | 16.8 | 3.72 | +147% | yes |
| D1_RMTH_IV | +3.47 | +3.34 | +3.53 | 34/46 | 21.3 | 3.73 | +200% | yes |
| D2_RMTH_EW | +2.42 | +3.15 | +3.36 | 31/46 | 21.9 | 3.38 | +169% | yes |
| D3_RMTH_ERC | +1.74 | +2.46 | +2.67 | 29/46 | 25.6 | 2.71 | +91% | no (DD) |
| **E1_rank_w_plus_H** | **+3.43** | **+3.38** | **+3.61** | **32/46** | **22.5** | **3.46** | **+206%** | **yes -> SELECTED** |
| E2_filt50_plus_H | +4.41 | +3.00 | +3.22 | 32/46 | 27.1 | 3.24 | +182% | no (DD) |

Context (not a candidate): W1 and H as two SEPARATE books 50/50 (monthly) on DEV: median +3.54, geo +3.28, 31/46 up,
month-end DD 17.7%; DEV monthly correlation W1 vs H = 0.34.
Honest reading of DEV: the filters (B) and holding rules (C) do NOT improve W1; the gain comes from adding the high52
leg to the book (D1, E1). E1 beats D1 by only 0.04%/month on DEV; that is noise-level, the rule picks E1.

## SELECTED (frozen): E1_rank_w_plus_H
Four legs in one book, inverse-vol weights (std of standalone 1x daily returns, 60 days, re-set every 7 days, causal):
- RANK: top-40 universe (leg_rotation rule); score = 0.5 x pct-rank(14-day return) + 0.5 x pct-rank(30-day range
  score); long 10 / short 10, 0.05 each of the sleeve; resized every 7 days from the run start (W1's R cadence).
- M: W1's ML leg unchanged (L3/S3 daily). T: W1's 60-day momentum leg unchanged.
- H: high52 leg (range30, top-40, L10/S10, daily).
1x, fee 0.05% + slip 0.02% per side, funding on perps; doubled-cost run too.

## Check 5 neighbour grid (27 neighbours; each changes ONE thing; PASS if >= 70% are net > 0 on DEV and TEST,
and on the HOLDOUT version: >= 70% net > 0 on the holdout)
- range lookback L (RANK and H legs): 15, 22, 38, 45
- rotation return lookback lb: 7, 10, 18, 21
- k (RANK and H legs): 5, 8, 12, 15
- rank mix weight on the return rank: 0.25, 0.75
- weight lookback: 30, 45, 75, 90; weight re-set every 1, 3, 14, 30 days
- weights EW, ERC
- RANK leg resized daily instead of weekly
- T lookback: 30, 90

## Checks 6 / 7
- 6: 200 random books: every leg picks coins at random with its own structure (same counts, days, universes; W1 legs
  use r4_07's random modes), same weight path as the real book, same costs. PASS if real net beats >= 95%.
- 7: remove the 5 coins with the largest summed position P&L from every leg (universes refill; ML loses them), re-run
  with recomputed weights: net > 0; and the largest single position <= 10% of total position P&L.

## Portfolio rule (W1 + W2 + X), monthly series
- thirds, and the "best DEV mix": weights on a 0.1 grid (each >= 0, sum 1) maximising the DEV (2021-01..2024-10)
  geometric mean month subject to a DEV month-end max DD <= that of W1+W2 50/50 on DEV. The chosen weights are then
  applied unchanged to TEST and HOLDOUT. Compared with W1+W2 50/50 on TEST and HOLDOUT.
