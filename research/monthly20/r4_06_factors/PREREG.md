# PREREG - r4_06_factors: non-momentum cross-sectional factors (written 2026-10-23, after the DEV grid, BEFORE any TEST run)

## What was done on DEV (2021-01-01..2024-10-31) only
`dev.py` ran 240 configs (all kept in `dev_grid.json`): factor in {lowvol, beta (BAB), ivol, max (lottery, shorted),
skew, combo, volg+, volg-, size+, size-} x lookback L {14,30,60} (volume growth {3,7,14}) x k per leg {5,10} x
{dollar-neutral, beta-neutral} x universe top-{40,80} by 30-day quote volume; weekly rebalance, 1x gross, baseline costs.
Selection rule (written in dev.py before it ran): DEV net > 0 at baseline AND doubled costs, then highest DEV t-stat
of the monthly mean. 52/240 configs were positive at both costs; the best DEV t-stat was only 1.87.
Honest note: with 240 tries a DEV t of 1.87 is what the best of 240 noise strategies would also show; the multiple-testing
bar would be about t >= 2.0 x sqrt(ln(240)+1) = 5.0. This is reported, not used to stop the test.

## The ONE selected config (frozen)
- Universe: survivorship-free archive panel (683 crypto USDT perps incl. delisted, `r2_rotation/data.py`),
  re-picked at every rebalance: top 40 by trailing 30-day quote volume known at the open (`engine_r2.universe_mask`).
- Score `combo`, L = 14 days, known at the open of day i from daily close-to-close returns of days i-14..i-1:
  mean of the cross-sectional percentile ranks (over all coins with data) of five "low-risk" scores:
  -stdev(r), -beta to BTC, -residual (idiosyncratic) stdev vs BTC, -max daily return, -skewness. All five required (else no score).
- Long the 5 highest combo scores (calm, low-beta, no lottery days), short the 5 lowest, equal weight inside a leg.
- Beta-neutral: legs sized so that gross = 1x equity and mean-beta(long) x long gross = mean-beta(short) x short gross
  (betas = 14-day BTC beta known at the open, clipped >= 0.1). Coins without a beta are not eligible.
- Rebalance every 7 days at 00:00 UTC open (start 2021-01-01 for full / DEV, 2024-11-01 for TEST), fill at the open.
- Costs 0.05% fee + 0.02% slip per side on traded notional, archive funding every day, 1% of prior day quote volume cap,
  liquidation rule from PROTOCOL.md (only bites above 1x). Engine: `engine_r4.py` = engine_r2 + beta-neutral leg sizing.
- DEV result at 1x: mean month +3.79%, median +1.99%, geo +2.92%, net +287%, max DD 41.3%, doubled-cost net +231%.

## TEST (2024-11-01..2026-10-21): run ONCE, baseline and doubled costs. No re-tuning afterwards.

## The 8 ROBUSTNESS.md checks (1x, full 2021-01..2026-10, 70 months) - implemented as in r2_rotation/run.py
1. DEV and TEST net > 0 at baseline and doubled costs.  2. >= 4 of 6 calendar years positive and 2022-01..2026-10 positive.
3. t-stat of the 70 monthly returns >= 2.0 and 3-month block bootstrap (5,000 draws, seed 7) 5th pct of the mean > 0.
4. Net positive after removing the 3 best months.
5. Neighbour grid, one parameter moved at a time from (L=14, k=5, univ=40, rebal=7), all else as selected:
   L in {7, 10, 18, 21}; k in {3, 4, 6, 8}; univ in {20, 30, 50, 60}; rebal in {1, 3, 5, 10, 14}  -> 17 neighbours.
   PASS if >= 70% are net > 0 on BOTH DEV and TEST at baseline costs.
   (Info only, not counted: the same config dollar-neutral.)
6. 500 random versions: same universe, k=5 per leg, same eligibility (coin must have a combo score and a beta), same
   beta-neutral leg sizing, rebalance dates and costs, random coin picks (seeds 1000..1499). PASS if the strategy's full
   net beats >= 95% of them.
7. Remove the 5 coins with the largest P&L from the panel (factors and universe recomputed without them): full net > 0;
   and no single position contributes > 10% of total net profit.
8. Full-period geometric mean month > 0 and >= 0.5 x arithmetic mean month.

## Leverage
After the 1x judgement: the same config at 1, 1.5, 2, 3, 4, 5, 6, 8, 10x (full, DEV, TEST), with mean / median / geo month,
max DD, worst month and liquidations; the lowest leverage <= 10x whose arithmetic mean month is >= 20% on TEST and on
2021-2026 is reported, or "not reachable at <= 10x". Never above 10x.
Also 2026 YTD (calendar months 2026-01..2026-10) at 1x.
