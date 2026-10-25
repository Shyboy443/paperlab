# PREREG - agent 03, funding-rate strategies (written 2026-10-23 from DEV only, before any TEST run)

Engine: `run.py` as it stands now. Daily bars (perp + spot, Binance), decision at 00:00 UTC on closed bars and funding
stamps before it, fill at that day's open. Costs 0.05% fee + 0.02% slippage per side per leg (doubled for the stress run).
Funding paid/received on every stamp held across. Liquidation: position wiped when its loss reaches margin - 0.5% of
notional, checked on daily highs/lows (carry: worst of spot-high-vs-perp-high and spot-low-vs-perp-low basis).
Notional per leg capped at 1% of the previous day's quote volume (both venues for carry). Carry with leverage L > 1
borrows (L-1) x margin in USDT for the spot leg at 10% APR (portfolio-margin style). Equal slots, sized at
equity/slots when opened. Universe: top-U USDT perps by 30-day perp quote volume, re-picked at each month start
(point in time; today's symbol list only = survivorship bias). Start equity 10,000.

DEV grid (124 configs) is in `dev_grid.json`. Chosen from DEV:

| name | family | params | leverage |
|---|---|---|---|
| CARRY_1x | carry long spot / short perp | U=40, D=7 (7-day mean funding), K=10, min_f=0.0001 per 8h, hold while rank <= 20 and funding >= 0.00005, daily rebalance | 1 |
| CARRY_3x | same | same | 3 |
| CARRY_10x | same | same | 10 |
| CARRY_30x | same (PRIMARY: 30x is the smallest DEV leverage with mean month >= 20%) | same | 30 |
| XS_LS_1x | contrarian cross-section: short the 5 highest-funding perps, long the 5 most negative | U=40, D=3, K=5, rebalance every 72h | 1 |
| XS_LS_2x | same | same | 2 |
| XS_LONGNEG_1x | long the 3 most-negative-funding perps (crowded shorts), no short leg | U=80, D=3, K=3, rebalance every 72h | 1 |

Verdict rule (PROTOCOL): PASS only if the PRIMARY (CARRY_30x) TEST mean month >= +20% at baseline costs, mean > 0 at
doubled costs, not wiped out, and no single trade > half the profit. All seven are reported whatever they show.
Leverage needed for 20%/month on TEST is read from a ladder (1..50x) run after the attempts, reported as analysis
only (not a new strategy).

DEV notes known before TEST: carry DEV mean is 5.3%/month in 2021 but 0.6% in 2022-24 (median ~0); XS long-only
looks like 2021 short-squeeze beta (top-3 trades > 100% of profit); XS long-short 2022-24 median is negative.
