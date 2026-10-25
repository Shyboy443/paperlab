# PREREG: r4_01_reversal, short-term cross-sectional reversal (written 2026-10-23, after the DEV grid and BEFORE any TEST run)

## Rule (frozen)
- Universe: at every rebalance, the top-40 USDT perps by trailing 30-day quote volume, known at that day's 00:00 UTC open.
  This is r2's `universe_mask` on the survivorship-free archive panel (data.binance.vision via agent 07's cache; delisted
  coins included; TradFi and index contracts dropped). A coin also needs a bar at the rebalance hour.
- Score `z`, L = 1 hour: the coin's last-hour log return minus beta x BTC's last-hour log return, divided by the std of
  hourly residuals. Beta and std come from the trailing 720 hourly returns. The score at hour i uses closes up to hour i-1.
- Book: rebalance every R = 24 h at 00:00 UTC, filled at the hour's open. Long the 10 lowest scores and short the 10
  highest, equal weight, 0.5x equity per side (1x gross).
- Hysteresis band = 2.0: a held coin stays while its rank on its own side is within 2 x 10 = 20. Empty slots are filled
  from the extreme ranks.
- Costs: 0.05% fee + 0.02% slippage per side on every traded notional. Archive funding, or +0.01%/8h paid by longs where
  a coin has no history. Liquidation: a position is wiped at a 1/lev - 0.5% adverse move, checked on hourly highs/lows.
  Delisted coins are frozen at their last close and closed at the next rebalance.
- Engine: `engine_r4.run`. Units stay constant between rebalances.

## How it was selected (DEV 2021-01-01..2024-10-31 only)
Grid: score in {raw, resid, z, zv} x L in {1, 4, 12, 24, 72, 168} h x R in {1, 4, 8, 24} h x band in {1, 2}, with k=10
and univ=40 (192 rows, `dev_grid.json`). Zero-cost gross runs were done for band 1. The rule was fixed before the grid ran:
take the highest DEV mean month at baseline costs whose DEV net is > 0 at doubled costs; if none qualifies, take the highest
DEV mean month.
**None qualified.** Only 3 of 192 rows were net positive on DEV at baseline costs. The pick is therefore the fallback, the
best DEV row at baseline: DEV mean +1.05%/month, geometric +0.79%, max DD 32%. At doubled costs it is -1.09%/month.
DEV finding: the reversal is real only at the 1-hour horizon (gross +13..15%/month at R=1). Turnover there is about 36x
equity per day, so costs are about 76%/month and the net is about -45%/month. At 1-3 day horizons crypto shows
continuation (momentum), not reversal: the L = 24..168 h reversal books lose even before costs.

## Neighbour grid for check 5 (each run on DEV and TEST at baseline costs)
- L in {1 h with ±25/50% rounds back to 1, so it collapses to 2 h} -> the code tests round(L x {0.5, 0.75, 1.25, 1.5}),
  without duplicates and without L itself. For L = 1 that is only {2}.
- k in {5, 8, 12, 15}; R in {1, 2, 4, 8, 12, 48}; univ in {20, 30, 50, 60}; band in {1.0, 1.5, 2.5, 3.0}.
- Pass: >= 70% of the neighbours are net positive on both DEV and TEST.

## Other checks
As in ROBUSTNESS.md: 200 random-pick books (same k, R, band, universe, costs) for check 6. For check 7 the universe is
re-picked without the 5 most profitable coins. Leverage table: 1, 1.5, 2, 3, 5 and 10x (10x maximum).
TEST 2024-11-01..2026-10-21 is run once, by `run.py`. Nothing in this file is edited afterwards.
Expected outcome, stated in advance: FAIL. The DEV edge is +1%/month and does not survive doubled costs.
