# PREREG - r4_05_leadlag (written 2026-10-23 before any TEST run; not edited afterwards)

## Data
- Universe: Binance USDT-M perps from the data.binance.vision archive INCLUDING delisted coins (agent 07's 1h cache, 901 perps,
  TradFi/index contracts dropped via r2_rotation/data.py). Stored: every coin that was ever top-50 by trailing-30-day quote volume
  at a month start (432 coins). Tradable at bar t only if its point-in-time rank (30 completed days before t's day, >=30 days of
  history) is in [1, rmax) - rank 0 is the top coin, normally BTC, which is the leader and never traded.
- 15m: 85 coins (union of top-12 at every quarter start, delisted included) downloaded for the DEV comparison; the selected
  configs are on 1h bars.

## Rule (engine.py)
Signal at the close of 1h bar t. Leader move m = L-bar log return (BTC, or the equal-weight basket of the 5 highest-volume coins).
Trigger |m| > z * sigma (30-day std of the leader's 1h returns) * sqrt(L), restricted to down (m<0) or up (m>0) moves.
beta_i = 30-day rolling OLS beta of coin i on the leader; gap_i = beta_i*m - r_i(L).
- side 'lead' (fade overshoot): take the K coins with the most negative sign(m)*gap (moved MORE than beta implies) AGAINST the
  leader's move; with dirs=down that means LONG the 5 alts that dumped most beyond beta.
- side 'lag' (catch-up): take the K coins with the largest sign(m)*gap WITH the leader's move (long the laggards after a pump).
Entry at the next bar's open, exit at the open H bars later, equal weight 1/K of equity per coin, 1x, one event at a time
(new triggers ignored while a position is open), compounding. Costs 0.05% fee + 0.02% slip per side, funding paid/received
at each stamp in the hold; missing bars (delisting) -> exit at last close. Liquidation at 1/lev - 0.5% on bar highs/lows.

## Selection on DEV only (dev_grid.py, 816 configs: tf {1h,15m} x leader {BTC,TOP5} x side {lag,lead} x dirs {both,down,up}
x L x z {2,3} x H x rmax; K=5)
Rule fixed before looking: among configs with DEV geo month > 0 at doubled costs and >= 3 of 4 DEV years positive (71 eligible),
the highest DEV geometric mean month.
- **A (primary): tf=1h, leader=BTC, side=lead, dirs=down, L=1, z=2.0, H=4, K=5, rmax=50.**
  DEV: mean 9.61%/mo, median 4.43, geo 7.63, max DD 53%, years 2021 +1665%, 2022 -5.8%, 2023 +54%, 2024 +15%.
- **B (secondary, the family's literal catch-up idea):** best 'lag' config under the same rule:
  tf=1h, leader=TOP5, side=lag, dirs=up, L=1, z=3.0, H=4, K=5, rmax=50. DEV geo 1.78, max DD 69%.
- Only 36/408 catch-up configs are net positive on DEV; 183/408 fade configs are (114/136 of the 'down' fade configs).

## TEST (2024-11-01..2026-10-21), run once with run.py
Both attempts are reported. Checks: ROBUSTNESS.md 1-8 at 1x over 2021-01..2026-10.
Neighbour grid (check 5): L, z, H, K, rmax each at x0.5, x0.75, x1.25, x1.5 (ints rounded, duplicates dropped; rmax above 50 is
limited by the stored top-50 candidate list), plus the other bar size (15m with the same clock-time L/H, rmax<=20) and one bar
of extra latency (delay=1). Random benchmark (check 6): 200 runs, same trigger times, same K and direction, random eligible coins.
Leverage table 1,1.5,2,3,4,5,6,8,10x (never above 10x).
