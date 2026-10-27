# PREREG: leads_holdout (Worker 3 round 4). Written 2026-10-26 BEFORE any holdout run of a lead.

Holdout = 2018-01-01..2020-12-31 (36 months), Binance SPOT, delisted pairs included. Spot stands in for perps:
funding = 0, and shorts are assumed possible at the same costs (fee 0.05% + slippage 0.02% per side; a doubled-cost
run as well). 1x. Nothing below is tuned: both leads are run exactly as they were defined before. The holdout is run
ONCE, by `run.py` (script stages `repro` = reproduce the existing W1 backfill, then `holdout` = the one run).

## Lead A: Worker 1 + momentum-crash protection (w3r2 w1_upgrade V2)
- Machinery: `research/worker1_backfill` unchanged (spot daily panel `data/w1_backfill/panel.npz`, legs R/M/T built by
  r4_07 `ens.py`, re-pointed to the spot cache by `worker1_backfill/run.py`; leg-availability-aware inverse-vol weights
  `report.weight_path_avail`, 60d / every 7 days from 2018-01-01). The ML leg M uses the strictly causal spot
  walk-forward predictions that worker1_backfill already built (`pred_full_H6_all_s0.npz`, training rows end before each
  month); it is NOT re-trained here and it is NOT dropped (the backfill's R+T-only book is a sensitivity, not W1).
- Step 0: plain W1 must reproduce `worker1_backfill/results.json` key `backfill_1x` (every month within 0.01 pp)
  before V2 runs.
- V2 exactly as pre-registered in round 2 (`up.py v2_scale`): on each of W1's own re-set days (every 7 days from the
  run start 2018-01-01), bear = BTC close(d-1)/close(d-366) < 1 and rebound = BTC close(d-1)/close(d-31) > 1; if both,
  R and T weights x 0.5, M unchanged, cash idle. Defaults bear 365, reb 30, cut 0.5. Multiplies W1's weight path.
- Known data limit: the spot BTCUSDT series starts 2017-08-17, so the 365-day bear flag cannot be computed before
  ~2018-08; V2 = W1 on those days (no cut). (BTC was far above its 2017 level then, so the flag would have been off.)
- Head-to-head rule (the same rule V2 failed on DEV): V2 "holds" = holdout geo month > W1's AND median month >= W1's
  AND max DD <= W1's + 2 pp. YES = all three hold (1x) and V2 is not worse than W1 at doubled costs on geo;
  PARTLY = geo or median better but not all three; NO = neither geo nor median better.

## Lead B: hks 12h periodicity (w3r3 hks_periodicity)
- Rule `round3/hks_periodicity/lib.py` unchanged: B = 12 (00-12 / 12-24 UTC), K = 40, k = 3, buf = 3.0, continuation,
  long top 3 / short bottom 3 at 0.5/3 each, entry/exit at block opens, cost on turnover.
- Data: `data/w2_backfill/1h` via `worker2/backfill/w2bf.Panel` (top-40 by 30-day quote volume, point-in-time, the
  same rule as hpanel/engine_r2; stables/fiat/leveraged tokens excluded; BULL/BEAR excluded). lib.py's `base` module is
  replaced by a shim exposing the same names (O, C, U40, FCUM = 0, td, th, syms, ND, dayidx, FEE, SLIP).
- Known coverage limit: Binance spot had only ~4-16 USDT pairs in 2018 (top-40 average 3.6 in 2018-01, 16 by 2018-09,
  38 from 2019-07). The book needs >= 6 eligible coins with >= 32 of 40 past days, so it is flat early in 2018.
- Reported: 1x, doubled costs, and gross (cost 0), so the gross-vs-net split is visible.
- Verdict: YES = net > 0 at 1x and at doubled costs on the holdout AND >= 6 of the 8 checks pass (as adapted below);
  PARTLY = net > 0 at 1x but not the rest; NO = net <= 0 at 1x.

## The 8 checks, adapted to a 36-month holdout (both leads, the same way)
1. Net > 0 in both holdout halves (2018-01..2019-06 and 2019-07..2020-12), at 1x and at doubled costs.
2. Positive in >= 2 of the 3 years 2018/2019/2020, and positive over 2019-01..2020-12 (the first year removed).
3. t-stat of the 36 monthly returns >= 2.0 AND block-bootstrap (3-month blocks, 5,000 draws, seed 7) 5th pct of the mean > 0.
4. Net > 0 after deleting the 3 best months.
5. Neighbours net > 0 on the holdout for >= 70%: A = the 12 V2 neighbours of round 2 (bear {180,270,540,730},
   reb {14,21,45,60}, cut {0,0.25,0.75}, bear-only); B = the 15 round-3 neighbours (K {20,30,50,60}, k {2,4,5},
   buf {2,2.5,3.5,4}, B {6,8}).
6. 200 random versions: A = every leg's coins drawn at random (r4_07 procedure, seeds 5000+s) with V2's own weight
   path; B = random scores (seeds 1000+s). Beat >= 95%.
7. Net > 0 without the top-5 profit coins AND the biggest single position <= 10% of total profit.
8. Geo month > 0 and >= 0.5 x mean month.

## Portfolios on the holdout (monthly re-balanced mixes of the monthly series, 1x)
- W1 (backfill_1x) vs W1+V2.
- W1+W2 50/50 vs W1+W2+B in thirds, with W2 = `worker2/backfill/results.json` backfill_2018_2020 1x.
- Info: W1V2+W2 50/50. Reported: median/geo/mean month, months up, month-end max DD, monthly correlations.
