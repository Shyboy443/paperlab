# PREREG: r4_09_session (written 2026-10-23, before any TEST run)

Family: intraday session / clock structure on 1h bars. TEST (2024-11-01..2026-10-21) has NOT been run by anything in
this folder. explore.py, dev_grid.py, sanity*.py and `run.py dev` only read DEV days (2021-01..2024-10).

## What DEV showed (explore_dev.txt, dev_grid.txt, dev_selected.txt)
- Unconditional clock effects (US open, Asia open, Europe, funding stamps 00/08/16, weekend, Monday) on BTC/ETH: small,
  mostly sign-flipping between 2021-22 and 2023-24, as round-1 agent 09 found. Not used.
- Day-clustered, the clear effect is **conditional intraday momentum in alts**: a large (vol-scaled) move between
  00:00 UTC and the afternoon continues into the evening and through the 00:00 UTC Asia open. The same rule on
  BTC/ETH is ~0 (DEV +0.34%/month). Holding through 22:00-02:00 UTC carries most of the gain.

## The rule (ONE selected config, chosen on DEV by top monthly t-stat in dev_grid.txt, centre of a positive plateau)
- Universe each UTC day d: the top-40 USDT perps by 30-day quote volume known at d 00:00 (survivorship-free panel,
  delisted coins included, r2_rotation/data.py), minus BTCUSDT and ETHUSDT.
- z = ln(close of hour 15 / open of hour 00) / (std of hourly log returns over the previous W=480 h * sqrt(16)).
- If |z| >= k=2.0: at the open of 16:00 UTC go long (z>0) or short (z<0); exit at the open of 02:00 UTC next day (X=26).
  If the coin stops trading inside the hold, exit at its last close.
- Sizing: each position gets equity / max(n_signals_that_day, K=5), 1x, isolated. Gross <= 1x. Notional capped at
  1% of the entry bar's quote volume. Compounding daily.
- Costs: 0.05% fee + 0.02% slippage per side, funding stamps inside the hold; doubled-cost rerun.
- Liquidation at lev L: adverse excursion on bar highs/lows >= 1/L - 0.5% loses the margin.
- DEV result of this config at 1x: mean +9.52%/month, median +8.08, geo +8.83, max DD 22.4%, t 5.15, 2,414 trades.

## Attempts that will be run on TEST (once)
1. **IM_ALT_H16_k2_X26** (selected, judged by the 8 checks).
2. **IM_BE_H16_k2_X26**: same rule on BTC+ETH only, K=2 (the BTC/ETH version; DEV +0.34%/month, expected ~0).

## Neighbour grid for check 5 (one parameter at a time, others at the selected value; 20 neighbours)
- H (signal end / entry hour) = 8, 12, 20, 24, with the 10 h hold kept (X = H+10)
- k = 1.0, 1.5, 2.5, 3.0
- hold length (rebalance frequency) X-H = 5, 8, 12, 15 h
- K (slots) = 3, 4, 6, 8
- W (vol lookback, h) = 240, 360, 600, 720
DEV: 19 of 20 are net positive (H=8 is -8%).

## Checks, leverage and reporting
All 8 checks of ROBUSTNESS.md at 1x, implemented in run.py (random null: 200 versions with the same days, same number of
longs and shorts per day, same windows and costs, random coins from that day's universe; check 7 removes the 5 most
profitable coins from the universe and re-runs). Leverage table 1..10x on TEST and 2021-2026, with liquidations and the
1% volume cap. Leverage needed for a +20% arithmetic TEST month = lowest of 1, 1.5, 2, 3, 4, 5, 6, 8, 10 that reaches it.
Months are calendar UTC months; a trade is booked on its signal day (it covers 16:00 d .. 02:00 d+1).
No re-tuning after TEST. Any later change is a new, labelled post-TEST attempt.
