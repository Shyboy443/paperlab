# PREREG: Worker 2 round 2 / funding_clock (written 2026-10-25, BEFORE any TEST run)

Family: price drift around Binance USD-M funding stamps (00/08/16 UTC), conditional on the last known funding rate.
Not carry (positions never deliberately held to collect a stamp), not the perp premium.
TEST (2024-11-01..2026-10-21) has NOT been touched: explore.py, dev_grid.py, dev_nb.py only use DEV days 2021-01..2024-10.

## What DEV showed (explore.py output, dev_grid_legs/combo, dev_nb.txt)
- Coins with strongly NEGATIVE last funding (shorts pay) rise in the ~3 h before the stamp (shorts cover) and fall in the
  ~2 h after it (shorts re-open); same sign in 2021, 2022, 2023, 2024 (per-year bucket means in explore.py).
- Coins with strongly POSITIVE funding rise in the first 1-2 h after the stamp (longs re-open); the pre-stamp side is noise.
- Any leg alone is weak after costs (t 1.7-2.4); low thresholds (|f| < 0.05%) lose heavily to costs.

## The rule (ONE selected config: "FC_SEL")
- Universe at each stamp s (00, 08, 16 UTC of UTC day d): the top-40 USDT perps by 30-day quote volume known at d 00:00
  (survivorship-free hourly panel, research/monthly20/r4_09_session/base.py), BTC/ETH included.
- Signal, known at the decision time s-3h: f = sum of funding stamps in the 8 hours before s-3h (the last 8h-equivalent
  rate; for 4h/1h-interval coins the sum of their stamps in that window). Nothing at or after s-3h is used.
- thr = 0.10% (|f| >= 0.001), same threshold both sides.
  - f <= -0.10%: LONG at the open of s-3h, close just before the stamp at s (price = open of the stamp hour);
    then SHORT just after the stamp (price = open of the stamp hour), close at the open of s+2h.
  - f >= +0.10%: LONG just after the stamp (open of the stamp hour), close at the open of s+2h.
- Sizing: each leg group (pre-group of stamp s, post-group of stamp s) gives each position equity / max(n, K=5), 1x,
  gross <= 1x, market orders. Notional capped at 1% of the entry bar's quote volume. Compounding per group.
- Costs: 0.05% fee + 0.02% slippage per side. Funding: exact stamps from the panel; a stamp is paid/received only if
  strictly inside (entry, exit) (live: close ~10 s before the stamp, open ~10 s after). Sensitivity also reported:
  conservative convention charging stamps at the entry and exit bars whenever they cost money (never credited).
- DEV result at 1x: median +3.22%/month, geo +8.18, mean +9.88, max DD 22.3%, t 2.97, 229 trades/month.
  Doubled cost: median +1.04, geo +4.11. Conservative funding: median +1.48, geo +4.43.

## Neighbour grid for check 5 (one parameter at a time; 15 neighbours)
- thr = 0.05%, 0.075%, 0.125%, 0.15%
- h1 (pre-entry hours before stamp) = 1, 2, 4, 5
- h2 (post hold hours, = rebalance frequency of the post leg) = 1, 3, 4
- K (slots) = 3, 4, 6, 8
DEV: 15 of 15 net positive.

## Checks and reporting
All 8 ROBUSTNESS checks at 1x on 2021-01..2026-10 (random null: 200 versions with the same groups, same number of
positions and the same sides per group, random coins from that stamp's universe; check 7 drops the 5 most profitable
coins from the universe and re-runs). Plus: Worker 1 correlation monthly (2022-01..2026-10) and daily, 2025-01..2026-10
at doubled costs, 2026 YTD, October 2026, trades/month, smallest order at 500 USDT, conservative-funding run.
Run once: `python run.py` -> results.json, run_output.txt. TEST is run exactly once; no changes after.
