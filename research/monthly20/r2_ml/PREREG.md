# PREREG — r2_ml: robustness re-test of agent 10's ML walk-forward long/short (written 2026-10-23, before any run)

## What is re-tested (fixed, copied from 10_ml_walkforward/PREREG.md)
Same features, target (24 h forward cross-sectional rank), HistGradientBoostingRegressor(max_iter=150,
learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=2000, l2_regularization=1.0, random_state=0), monthly
retrain on an expanding window (every 3rd 4h bar, purged, 1-day embargo), daily rebalance at 00:00 UTC, long top 3 /
short bottom 3, equal notional, 0.05% fee + 0.02% slippage per side, funding, liquidation and 1%-of-volume cap.
Leverage 1x for all 8 checks.

## The only change: universe with delisted coins
- 1h bars from the public archive data.binance.vision (agent 07's cache from 2020-12, agent 04's for 2020-09..11,
  a backfill of 2020-01..08 for the 51 perps that existed then), aggregated to 4h. Funding from agents 04/07 archive
  caches (plus the common API cache for 2026-10 where present).
- Every USDT perp that ever traded, minus TradFi / index contracts (exchangeInfo underlyingType != COIN or
  contractType != PERPETUAL; plus the old BLUEBIRD/FOOTBALL indices).
- Point-in-time top 40 by trailing 30-day quote volume at each month start (>= 80% of 4h bars present in the 30 days).
- Hours with zero quote volume (dead post-delisting tails) count as missing. A coin whose data ends while held is
  closed at its last traded price.

## Pre-declared checks (ROBUSTNESS.md), at 1x
- Full period 2021-01..2026-10 is one continuous walk-forward (TEST months train on all earlier data, as before).
- Check 5 neighbour grid (declared now): k in {2,3,5,8} x horizon/rebalance H in {12h, 24h, 48h} x training
  window in {expanding, rolling 12 months} = 24 configs minus the base = 23 neighbours, plus the base with
  random_state=1 = 24 neighbours. PASS if >= 70% are net positive on DEV and on TEST at baseline costs.
- Check 6: 300 random versions: at each of the strategy's decision times, k=3 random longs and k=3 random shorts
  from the same eligible set (coins with a score), same fills, costs, funding, liquidation and caps. PASS if the
  strategy's full-period net return beats >= 95% of them.
- Check 7: the 5 coins with the largest summed position P&L in the base run are removed from the universe and the
  whole walk-forward is re-run (the next coins by volume fill the top 40).
- Leverage table: 1x / 1.5x / 2x over 2021-2026 and TEST, with the liquidation model.
