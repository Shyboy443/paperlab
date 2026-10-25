# r4_03 positioning: pre-registration (written 2026-10-23 before any TEST run)

## Data and coverage (stated honestly)
- Source: data.binance.vision `futures/um/daily/metrics` (5-minute rows), public, no keys. Cached per day as one record
  (last OI snapshot of the day; day means of the four ratios) in `data/monthly20_r4_positioning/daily/`.
- Downloaded for every coin that was ever in the point-in-time top-40 universe (431 symbols incl. delisted ones such as
  LUNA, FTT, SRM), for the days it was in the universe plus a 40-day pad: 92,107 symbol-days.
- **Coverage:** the archive starts 2020-09-01 for BTCUSDT only and 2021-12-01 for every other coin.
  `count_long_short_ratio` (global accounts) covers the universe from 2022-02 (95% in 2021-12, 41% in 2022-01);
  top-trader ratios are missing for most of 2022 (full from 2023-01); taker ratio from 2022-06.
  So the effective DEV is **2021-12 .. 2024-10 (35 months), not 46**. The strategy is flat (0%) in 2021-01..2021-11 and
  those months are counted as 0% in the 70-month checks.

## DEV exploration (DEV only, `explore_dev.py`, all 294 variants kept in `dev_grid.json`)
Families tried on DEV: squeeze (OI up + price down, XS) 2/48 positive; retail_contra 40/48; retail_chg 27/48;
smart_vs_retail (top-trader position ratio vs retail account ratio) 40/48; taker_contra 0/48; OI flush event (long after
an OI drop with falling price) 9/54. Squeeze, taker and OI-flush ideas are dead on DEV and are not taken to TEST.

## The ONE selected rule (retail_contra_k3_5x5_w7)
- Universe at day i: top-40 USDT perps by 30-day quote volume known at day i's open (survivorship-free daily panel of
  `r2_rotation/data.py`, delisted coins included), with a metrics value available.
- Score at close of day d: `-log(mean of the daily-mean count_long_short_ratio over days d-2..d)` (needs >= 2 of 3 days).
  Lower retail long/short account ratio = retail more short = higher score.
- Every 7 days (schedule anchored at 2021-01-01) at the next day's open: long the 5 highest-score coins, short the
  5 lowest, 10% of equity each (50% gross long, 50% gross short), 1x. Needs >= 20 eligible coins, else flat.
- Costs 0.05% fee + 0.02% slippage per side on traded notional; daily funding (longs pay); doubled-cost run too.
  Size cap 1% of the previous day's quote volume. Liquidation per PROTOCOL.md when levered.
- DEV result (1x): mean month +3.84% (over 46 months incl. 11 flat), geo +3.44%, max DD 19.0%, t 2.71 (2.78 active),
  net +374%; doubled cost net +329%.
- Known caveat found on DEV: the score has cross-sectional Spearman ~0.41 with the 14-day return (retail shorts what
  has gone up), monthly correlation 0.64 with a 14d momentum rotation. The momentum-neutral residual was still positive on DEV
  (+1.2..1.7%/month), but the signal is partly a momentum proxy.

## Neighbour grid for check 5 (one parameter at a time; >= 70% must be net positive on both DEV and TEST)
- k (ratio averaging days) in {1, 2, 4, 5}
- kL = kS in {3, 4, 6, 8}
- rebalance every {1, 3, 5, 10, 14} days
DEV of all 13 neighbours is positive (net +143% .. +538%).

## Checks and leverage
All 8 checks of ROBUSTNESS.md at 1x over 2021-01..2026-10 (70 months; 2021-01..11 = 0%), 300 random-pick versions for
check 6 (same 5/5, same dates, same eligible coins, same costs). Then leverage 1,2,3,4,5,6,7,8,10x (never above 10):
mean/median/geo month, max DD, worst month, liquidations, full period and TEST. 2026 YTD at 1x.
TEST (2024-11-01..2026-10-21) is run once by `python run.py`. No re-tuning afterwards; any later change is a new attempt.
