# PREREG: Worker 2 round 2 / attention (abnormal-volume shocks) — written 2026-10-25 BEFORE any TEST run

## Data and status
- Survivorship-free hourly panel of r4_09_session (`base.py` / `hpanel.py`), point-in-time top-40 universe minus BTC/ETH.
- Only DEV (2021-01-01..2024-10-31) has been used so far: `dev_explore.py`, `dev_explore2.py` (event studies),
  `dev_grid.py` (144 configs: mode mom/rev/long/short x thr 3/5/8 x hold 1/2/3/5 d x zmin 0/1/2, K=5),
  `dev_grid2.py` (72 configs of mode=mom: thr 3/5 x zmin 1/1.5/2 x hold 2/3/4 d x K 1/2/3/5). Outputs in dev_grid*.json.
  TEST (2024-11-01..2026-10-21) has not been touched by any script in this folder.

## What DEV said (first principles + data)
- Reversal ("rev") of a volume shock loses in every DEV config (t to -3): high-volume moves do NOT overreact on 1-5 days.
- Continuation ("mom") wins when the shock also moved price: 24 h volume >= 3x the coin's own 30-day median AND
  |24 h return| >= 1-2 of the coin's own vol units. The edge appears from hold 2 d and is flat 3-4 d. Plain long-only
  after volume shocks is also positive on DEV but is market beta (2022 -36..-75%), so it was rejected.
- Volume shock alone (no price move, |z| < 1) carries no edge; it is attention WITH a directional move that persists.

## The ONE selected config (code: core.py, params below; nothing else is tuned)
- Every day at 00:00 UTC, for each coin in that day's top-40 (ex BTC, ETH):
  vr = quote volume of the last 24 h / median of the coin's previous 30 non-overlapping 24 h blocks (Lb=30);
  z = log(close of last hour / open 24 h ago) / (sigma * sqrt(24)), sigma = std of hourly log returns of the last 480 h (W=480).
- Trigger: vr >= 3.0 (thr) and |z| >= 1.5 (zmin). Side = sign(z) (continuation, long up-shocks, short down-shocks).
- Enter at the 00:00 UTC open, exit 72 h later (X = 3 days) at the open, market orders.
- Sizing at 1x: each day's cohort gets equity/3; it is split into max(n, K=3) slots; each slot is scaled by
  m = min(1, sig_ref / sigma) with sig_ref = 1.329% (DEV median sigma of coins with vr >= 3; fixed, never re-fitted).
  Gross exposure is therefore <= 1x; unused equity stays in cash.
- Costs: fee 0.05% + slip 0.02% per side, funding from the panel; doubled-cost run too. Daily mark-to-market curve.
- DEV result of this config: median month +1.68%, geo +4.20%, mean +4.72%, max DD 21.0%, t 2.95, 1339 trades,
  years 2021 +184%, 2022 +16%, 2023 +95%, 2024(Jan-Oct) +3.4%. Note the DEV median is already below the +2.22% bar.

## Check-5 neighbour grid (pre-declared; each changed alone, all else = selected)
- thr: 1.5, 2.25, 3.75, 4.5
- zmin: 0.75, 1.125, 1.875, 2.25
- hold X (days): 1.5, 2.25, 3.75, 4.5
- K: 2, 4, 5 (the +-25/50% of 3, rounded and de-duplicated)
- Lb (volume-norm days): 15, 22, 38, 45
- W (vol window, h): 240, 360, 600, 720
- rebalance/entry time (other rebalance times): 06:00, 12:00, 18:00 UTC
=> 26 neighbours, PASS if >= 70% are net positive on DEV and on TEST at baseline costs.

## Checks (exactly ROBUSTNESS.md, 1x)
1 DEV and TEST net > 0, baseline and doubled costs. 2 >= 4/6 years positive and 2022-01..2026-10 positive.
3 t-stat of the monthly mean (all months 2021-01..2026-10) >= 2 and 3-month block bootstrap (5,000, seed 7) p5 > 0.
4 positive after deleting the 3 best months. 5 grid above. 6 200 random versions: same days, same number of
positions, same sides, same hold and costs, random alts from that day's universe sized by the same vol rule; beat >= 95%.
7 remove the top-5 profit coins from the universe and re-run: still > 0; largest single position <= 10% of total
dollar profit (also report the log-growth share for info). 8 geo month > 0 and >= half the mean month.

## Also reported (no decisions from them)
Worker 1 correlation (monthly 2022-01..2026-10 and full, daily), 2025-01..2026-10 at doubled costs, 2026 YTD,
October 2026, trades/month, 500 USDT feasibility (smallest order vs 5 USDT min notional).
TEST is run ONCE by run.py. Any later change is a new, labelled attempt.
