# Worker 3, round 4: improve the leads and validate on an UNSEEN holdout (2026-10-26)

Three rounds tested 16 families on the same TEST window (2024-11..2026-10); none passed the bar
(research/worker3/round1..3/REPORT.md). That window is now spent: anything tuned after seeing it is curve-fitting.
This round learns more about the best leads and validates them on data no Worker 3 idea has ever touched:

**HOLDOUT = 2018-01-01..2020-12-31** (Binance SPOT klines, all USDT pairs incl. delisted, from the backfill:
`data/w2_backfill/{1d,1h}` built by `research/worker2/backfill/fetch.py`; daily panel helpers in
`research/worker1_backfill/` with `data/w1_backfill/panel.npz`). Worker 1 and Worker 2 holdout series already exist:
`research/worker1_backfill/results.json` (key `backfill_1x`), `research/worker2/backfill/results.json`
(key `backfill_2018_2020`). Spot prices stand in for perps: no funding; shorts are assumed possible at the same
costs (say so; a top-40 by volume universe point-in-time; rebuild it the same way the backfills did).

## Protocol
1. DEV (2021-01..2024-10) is the ONLY data used to choose anything new. TEST (2024-11..2026-10) may be REPORTED
   for context but must not influence any choice (it is already seen).
2. Pre-register in PREREG.md: every variant, the selection rule, and the holdout comparison, BEFORE running the holdout.
   Run the holdout ONCE.
3. 1x, fee 0.05% + slippage 0.02% per side (doubled-cost run too); report median/geo/mean month, months up, max DD, t,
   trades/month; the 8 checks of research/monthly20/ROBUSTNESS.md on the holdout where they apply.
4. Write only in `research/worker3/round4/<your_name>/` (+ `data/w3r4_<your_name>/`). Research only: no orders, keys,
   .env, app/ edits, or killing processes you did not start. Prefer reusing cached data over downloading.
5. Write REPORT.md; if the file tool refuses, put the full report in your final message.

## The question this round answers
Does the lead hold on 2018-2020? If yes, it is a credible forward-paper-test candidate. If not, we drop it.
