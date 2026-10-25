# PREREG — agent 10, ML walk-forward (written 2026-10-23, before any TEST run)

Machine-readable copy: `prereg.json`. `run.py test` reads it; it refuses to run a second time.

## Universe (point-in-time)
At each calendar-month start, the 40 Binance USDT perps with the highest 30-day quote volume, using only data before
that time (same rule as `common.liquid_universe`, onboard-date filter, >= 24 of 30 daily bars). Today's symbol list
only, so delisted coins are missing (survivorship bias). A coin is tradeable at bar t if it is in that month's universe
and all features are finite.

## Features (closed 4h bars, decision at the close of bar t)
Per coin, then ranked cross-sectionally within the universe at bar t (pct rank − 0.5): log returns over 1, 3, 6, 18,
42, 180 bars; realised vol 6/42/180; vol ratio 6/42; quote-volume z (6 vs 180 bars); size (log 180-bar mean quote
volume); taker-buy share 6/42 bars; position in the 42-bar range; mean high/low range 6 bars; funding sum over 6 and
18 bars; residual return vs BTC over 6 and 42 bars. Regime features (not ranked, standardised on the training set):
BTC return 6/42/180 bars, BTC vol 42, universe breadth (mean 42-bar return), cross-sectional dispersion of 6-bar returns.

## Target and model (selected on DEV only)
- Target: cross-sectional pct rank of log(open[t+1+6] / open[t+1]) (24 h forward, entry at the next open).
- Model: sklearn HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, max_leaf_nodes=15,
  min_samples_leaf=2000, l2_regularization=1.0, random_state=0).
- Retrained at every month start on ALL earlier rows (expanding window, every 3rd bar) whose target window ended
  at least 1 day before the month start (purge + 1-day embargo). TEST months keep walking forward the same way
  (they train on DEV data plus earlier TEST months, never on the month being traded).
- DEV selection: grid {ridge, hgb} × H {4h, 24h} × window {all, 12 months} × k {3, 5, 8}, chosen by DEV
  Sharpe at 1x after costs among configs positive in both DEV halves. Winner: hgb, H=24h, all, k=3
  (DEV 1x: mean +5.04%/month, median +5.38%, geo +4.07%, max DD 45%; 2021-22 half +7.8%/month geo, 2023-24 half
  only +0.17% — the edge was already fading).

## Trading rules
- Rebalance daily at 00:00 UTC (decision on the 20:00 bar close, fill at the 00:00 open).
- Long the k=3 highest scores, short the k=3 lowest; equal notional L·E/6 per position, isolated margin E/6 each.
- Only the difference vs the drifted previous book is traded; 0.05% fee + 0.02% slippage per side (stress: doubled).
- Funding paid/received per stamp held. Liquidation: a position whose loss reaches 1/L − 0.5% on a 4h bar high/low
  loses its whole margin. Notional per position capped at 1% of the decision bar's 4h quote volume.

## Pre-registered TEST attempts (all reported)
| name | config | leverage L | why |
|---|---|---|---|
| A_hgb_H6_k3_1x | above | 1 | the honest base |
| B_hgb_H6_k3_3x | above | 3 | DEV growth-optimal (geo +6.2%/month, DD 85%) |
| C_hgb_H6_k3_5x | above | 5 | DEV leverage closest to +20%/month mean (DEV mean +18.9%, geo +2.8%, DD 99%) |

No leverage reaches +20%/month mean on DEV without a wipe-out (7x: wiped; DEV bootstrap ruin 44% at 5x, 81% at 7x).
PASS (protocol): TEST mean month ≥ +20% at baseline costs, mean > 0 at doubled costs, not wiped, top single trade
≤ 50% of profit.
