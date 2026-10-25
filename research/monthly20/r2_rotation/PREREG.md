# PREREG - round-2 robustness check of cross-sectional momentum rotation (written 2026-10-23, before any run on the new data)

## Main candidate (frozen = agent 02's P4 rules, but at 1x)
- Universe: re-picked at every daily bar = top 40 USDT perps by trailing 30-day quote volume, listed (first archived bar)
  >= 30 days before, >= 80% of the 30 daily bars present. Coins: EVERY crypto USDT perp in the data.binance.vision
  archive (683 coins incl. delisted ones: LUNA, SRM, FTT, BTCST, ...). TradFi and index contracts dropped (see data.py).
- Score: 14-day return known at the day's open (close[d-1]/close[d-15]-1).
- Long the top 10, short the bottom 10, equal weight, gross 1x equity (0.5x per leg), rebalanced every 7 days at the
  00:00 UTC open, starting 2021-01-01 (DEV), 2024-11-01 (TEST) or 2021-01-01 (full run, used for checks 2-8).
- Costs 0.05% fee + 0.02% slippage per side, funding from the archive (+0.01%/8h if none), liquidity cap 1% of the previous
  day's quote volume, isolated-margin liquidation rule as in agent 02's engine (at 1x: a short is wiped at a +99.5% move).
- Engine = agent 02's engine.run, copied, with per-coin / per-position P&L attribution added (no logic changes).

## Check details fixed here
1. DEV 2021-01..2024-10 and TEST 2024-11..2026-10 run separately (fresh 10,000 USDT each), net > 0 at 1x and at 2x costs.
2. Calendar-year returns compounded from the full run's months; >= 4 of 6 years > 0; full run months 2022-01..2026-10 compounded > 0.
3. 70 monthly returns of the full run: t = mean / (sd / sqrt(70)) >= 2.0; moving-block bootstrap, 3-month blocks,
   5,000 draws, seed 7: 5th percentile of the mean month > 0.
4. Full-run months with the 3 best deleted, compounded: > 0.
5. Neighbour grid, one parameter moved at a time (18 neighbours):
   lookback {7, 10, 18, 21, 28} days; k per side {5, 8, 12, 15}; rebalance {1, 3, 5, 10, 14} days; universe size {20, 30, 50, 60}.
   PASS if >= 70% (13 of 18) are net positive on DEV AND on TEST at baseline costs, 1x.
   Family members reported but NOT counted in check 5 (different structure): long-only top-10 and top-3 with lookback
   7/14/28 and weekly/daily rebalance.
6. 500 random versions: at every rebalance 10 random longs and 10 random shorts from the same eligible universe,
   same rebalance days, costs, funding, caps. PASS if the strategy's full-run net return beats >= 95% of them.
7. (a) P&L attributed per coin over the full run (dollars); delete the 5 most profitable coins from the archive entirely
   and re-run: PASS needs net > 0. (b) A "position" = one coin held continuously on one side (opened, possibly carried
   across several rebalances, then closed). PASS needs the largest single position <= 10% of the full-run net dollar profit.
   Also reported: the same with P&L measured as % of equity at the time (not compounding-weighted), and per rebalance-period slices.
8. Full run: geometric mean month > 0 and >= 0.5 x arithmetic mean month.

ROBUST = all 8 pass. Leverage table (1x / 1.5x / 2x; DEV, TEST, full) reported whatever the verdict.
Sanity: the engine is first re-run on agent 02's own panel to reproduce its posted P4 1x numbers, then on the
survivor-only subset of the new archive panel, so the effect of adding delisted coins is visible.
