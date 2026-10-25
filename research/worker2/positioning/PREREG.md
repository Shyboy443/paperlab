# Worker 2 candidate: r4_03 positioning with a concentration fix. Pre-registration

Written 2026-10-25, after reproducing r4_03 exactly (`repro.py`, `repro.json`) and BEFORE running any variant.
Do not edit after the first variant run. Any later change is a new attempt and must be reported.

## Honest status of the test
- TEST (2024-11-01..2026-10-21) was already run once by the r4 round on the unfixed rule. We know it passed 7/8 checks
  and failed check 7: one position made 21.4% of total profit, and the top-5 coins (LAB, AKE, PIPPIN, MYX, SIREN) are all
  recent, very volatile listings. That knowledge shaped the choice of fix. So this is a **weaker test than the original**:
  it can show whether the fix removes the concentration, not that the edge is new and untouched.
- No parameter below was tuned on TEST, and no variant was run on any window before this file was written.

## Unchanged from r4_03 (its PREREG.md)
Signal (`-log` of the 3-day mean of the daily-mean global account long/short ratio), universe (point-in-time top-40
USDT perps by 30-day quote volume, delisted coins included, needs >= 20 eligible), 5 longs (lowest ratio) and 5 shorts
(highest ratio), rebalance every 7 days anchored at 2021-01-01, fills at the next day's open, 1x, costs 0.05% fee +
0.02% slippage per side, daily funding, size cap 1% of the previous day's quote volume. Effective DEV is 2021-12..2024-10
(metrics archive starts 2021-12); 2021-01..11 count as 0% months.

## The ONE concentration fix (primary rule: `ivcap_trim`)
Reasoning from first principles: the failure is that a single wild coin can make a fifth of all profit. Two things cause
that: (a) every coin gets the same 10% of equity no matter how violent it is, and (b) a winner is left to grow for up to
7 days before it is cut back. The fix treats both, with no free parameters picked from results:
1. **Equal-risk sizing.** At each weekly rebalance, each picked coin's weight is proportional to 1/vol, where vol = the
   standard deviation of its daily close-to-close log returns over the last 30 days known at the close before the fill
   (at least 15 returns, otherwise the coin is not eligible). Weights are normalised so each side sums to 50% of equity,
   then each position is **capped at 10% of equity** (the original equal weight, so no position is ever larger than in
   r4_03). Weight cut by the cap is not redistributed (gross can be below 50%/50%).
2. **Daily trim.** At every daily open, if a position's absolute value is above **1.25 x** its target weight x current
   equity, it is cut back to the target (a reduce-only order, costs charged). This applies to longs that rose and to
   shorts whose coin rose. Positions are never topped up between rebalances. A trim does not end the position for the
   position-level accounting in check 7 (same coin, same holding period).

## Neighbours of the fix (sensitivity only; at most 2)
- `ivcap_trim_b150`: trim band 1.5 x instead of 1.25 x.
- `ivcap_trim_v60`: vol lookback 60 days (at least 30 returns) instead of 30.

## The 8 checks (ROBUSTNESS.md, at 1x, 2021-01..2026-10 = 70 months)
1. DEV and TEST net positive at baseline and doubled costs.
2. >= 4 of 6 calendar years positive and 2022-01..2026-10 positive.
3. t-stat of the 70-month mean >= 2.0 and 3-month block bootstrap (5,000 draws, seed 7) 5th percentile of the mean > 0.
4. Net positive after deleting the 3 best months.
5. Neighbour grid = the r4_03 grid (k in {1,2,4,5}; kL=kS in {3,4,6,8}; rebalance every {1,3,5,10,14} days), each run
   WITH the primary fix, plus the 2 fix neighbours above = 15 neighbours; >= 70% must be net positive on DEV and TEST.
6. 300 random versions (seed 42): same dates, same eligible coins, 5 random longs and 5 random shorts, same equal-risk
   sizing, cap, trim and costs. The strategy's full net must beat >= 95% of them.
7. (a) Net positive when the 5 coins with the most profit are **removed from the universe** (the ranking then picks
   other coins in their place; the r4_03 way, which just zeroes their weights, is reported too), and (b) no single
   position (one coin, one holding period) makes more than 10% of total profit (USD, as r4_03 measured it).
8. Geometric mean month > 0 and >= half the arithmetic mean month.
Overall PASS only if all 8 pass for the primary rule. The neighbours do not replace the primary rule.

## Also reported (no selection on any of it)
- DEV / TEST / full at 1x: median, geometric, worst month, max drawdown, months up, trades (median and geometric first).
- Monthly and daily correlation with Worker 1 (r4_07 ensemble 1x daily book `_rbook_full_1x.npy`), and a 50/50
  combination (daily rebalanced, and monthly rebalanced).
- Size check at 500 USDT: share of positions whose opening order is below Binance USD-M minimum notional
  (5 USDT; ETHUSDT 20; BTCUSDT 100).
- Live-data feasibility of `/futures/data/globalLongShortAccountRatio` (one public GET, no keys).
