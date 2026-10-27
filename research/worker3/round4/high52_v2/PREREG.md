# PREREG: high52_v2 (Worker 3, round 4). Written 2026-10-26, BEFORE any diagnostic, DEV variant run or holdout run.

## Lead (frozen, round 2): ORIGINAL
rng (Donchian position) L=30, long 10 highest / short 10 lowest, point-in-time top-40 by 30d quote volume, daily rebalance
at 00:00 UTC open, 1x (50% gross per side), engine_r2 unchanged (fee 0.05% + slip 0.02% per side, 1% bar-volume cap).

## Step 1 LEARN (report only, nothing is selected from it)
DEV and TEST: long vs short leg; BTC above/below 100d SMA; BTC 30d realised-vol terciles; position P&L by coin age
(days since first bar) and liquidity tercile (rank in the top-40 by 30d quote volume); holding period of winners vs losers;
turnover and cost share (net vs zero-cost run).

## Step 2 IMPROVE: candidate set (fixed now), evaluated on DEV 2021-01..2024-10 ONLY
- A60 / A90 : only coins whose first bar is >= 60 / 90 days before the rebalance day.
- IV : inverse 30-day realised-vol weights within each leg, water-filled cap 1.5x equal weight (7.5% of equity).
- BL : score = mean of rng over L = 20, 30, 60.
- W7 : weekly rebalance (rebal = 7).
- A90+IV, A90+BL, BL+IV.
(The engine is a copy of engine_r2.run with an optional weight matrix; with equal weights it must reproduce engine_r2 exactly.)

## Selection rule (fixed now)
Eligible: DEV net > 0 at doubled cost, DEV max DD <= 40%, DEV geo month >= ORIGINAL DEV geo - 0.50 pp.
Variant 1 = eligible candidate with the LOWEST DEV largest-single-position share of DEV profit (the check-7 weakness);
ties -> higher DEV t. Variant 2 = among remaining eligible, the HIGHEST DEV monthly t-stat. At most 2 variants. If none
are eligible, only ORIGINAL goes to the holdout. TEST numbers are printed for context only and are not used.

## Step 3 HOLDOUT 2018-01-01..2020-12-31 (run ONCE, by holdout.py)
Binance SPOT daily klines incl. delisted (`data/w2_backfill/1d`, stable/fiat and leveraged tokens excluded with the
Worker 2 backfill's `excluded()` rule; zero-volume days = missing), point-in-time top-40 by 30d quote volume (same
universe_mask). Funding = 0; shorts assumed possible at the same costs (spot has no perp in 2018-2019: an assumption).
If fewer than 20 coins are eligible the engine holds cash (counted as 0% days); active months are reported.
ORIGINAL + selected variant(s): median/geo/mean month, months up, max DD, t, trades/month, at 1x and doubled cost.
Holdout checks (adapted to 36 months): 1 net > 0 at 1x and 2x cost; 2 positive in >= 2 of 3 years and over 2019-2020;
3 t >= 2 and 3-month block bootstrap p5 > 0; 4 net > 0 without 3 best months; 5 the same 16 neighbours, >= 70% net > 0;
6 beats >= 95% of 300 daily-redraw and 300 30-day-block random 10/10 books; 7 net > 0 without top-5 coins and largest
single position <= 10% of profit; 8 geo > 0 and >= half the mean.
## Step 4 PORTFOLIO
Holdout monthly: W1 (worker1_backfill backfill_1x) + W2 (worker2/backfill backfill_2018_2020 1x) + X equal thirds vs
W1+W2 50/50: median, geo, months up, monthly DD. Same for 2021-01..2026-10 with the perp series (context only).
Verdict: YES if ORIGINAL (or a variant) is net positive at 2x cost on the holdout with t >= 2 and >= 6/8 checks and the
three-way mix beats 50/50 on geo; PARTLY if net positive at 1x but not all of that; NO otherwise.

Addendum (2026-10-26, before the holdout run, after seeing ONLY the holdout universe size, no returns): the spot
top-40 has < 20 eligible coins until about 2019-02 (5 in 2018-01, 12 in 2018-07, 19 in 2018-12), so the 10/10 book is
in cash for most of 2018. Context row only (not a candidate, not used for the verdict): ORIGINAL with k = 5 (5/5),
which is also a check-5 neighbour, to show 2018. Holdout stats are also shown over active months only.
