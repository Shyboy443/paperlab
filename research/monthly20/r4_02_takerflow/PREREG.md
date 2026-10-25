# PREREG: r4_02_takerflow (written 2026-10-23, before any TEST run)

Family: order flow / taker imbalance from Binance USDT-perp klines (taker-buy quote volume / total quote volume).

## Data and universe
- Hourly archive bars (data.binance.vision via agent 07's cache) incl. delisted coins, non-crypto contracts dropped
  (r2_rotation/data.py rule); aggregated to UTC daily bars (`data_tf.py`). 683 contracts.
- Universe at each bar: top 40 by trailing 30-day quote volume known at the bar's open; >= 30 days of history, >= 80% bars present.

## DEV exploration (2021-01..2024-10 only; every variant in `dev_explore.json`, `dev_diag.json`)
Scores: taker share level (`share`), share minus own 30-day share (`fdev`), net taker flow / normal volume (`fvol`),
divergence rank(fdev) - rank(return) (`div`); cross-sectional long10/short10 and per-coin time-series rules
(flow, flow-vs-price divergence, confirmation), long/short and long-only, follow and fade, on 1d/4h/1h bars,
lookbacks 1-14 days (1d), 1-18 bars (4h), 4-24 bars (1h). Result on DEV: only the **cross-sectional, multi-day, follow**
versions of `share`/`fvol` are strongly positive; intraday (1h, short 4h lookbacks) and time-series rules are flat or
negative after costs. The effect survives removing same-lookback momentum (residual of rank(share) on rank(return):
DEV mean +2.5..+3.0%/month). Long leg alone is a high-beta lottery (DD 79-87%); short leg alone loses; it only works as a spread.
Note: 2024 (DEV's last year) was flat (+10.9% / -4.5% depending on rebalance) - possible decay.

## Selected config (the ONE TEST run)
- Every 3 days (daily bars, rebalance at 00:00 UTC on bars 0,3,6,... of the panel which starts 2020-12-01), score =
  sum(taker-buy quote) / sum(quote volume) over the last **3** closed daily bars.
- Long the **10** highest, short the **10** lowest of the top-**40** universe; equal weight, 50% gross each side, **1x**.
- Fill at the next bar's open; fee 0.05% + slip 0.02% per side; funding per position; size cap 1% of previous bar's
  quote volume; liquidation at 1/lev - 0.5% on bar high/low (only matters >1x). Doubled-cost run too.
- Choice rule: best DEV geometric month among the 1d cross-sectional `share` configs whose lookback/rebalance neighbours
  were all positive on DEV. DEV (real engine): mean +4.80%/month, median +3.32, geo +4.44, max DD 19.8%, 2x-cost net +421%.

## Check-5 neighbour grid (one parameter changed at a time; `prereg.json`)
- lookback L: 1, 2, 4, 5 days; k (each side): 5, 8, 12, 15; rebalance: 1, 2, 4, 5, 7 days; universe: 20, 30, 50, 60;
  same 3-day horizon on 4h bars (L=18, rb=18). 18 neighbours; PASS if >= 70% are net positive on DEV and TEST.
- Check 6: 200 random versions dealing the same weights to random coins of the same universe at the same rebalance times.

## Leverage
Judged at 1x first. Then 1, 1.5, 2, 3, 5, 7, 10x (never above 10x) for the 20%/month question.

## Expectation stated before TEST
A 20% mean month is not reachable at 1x (DEV mean 4.8%); at DEV volatility it needs ~4-5x. Expect TEST to be weaker than DEV.
