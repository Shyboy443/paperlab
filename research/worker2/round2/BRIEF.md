# Worker 2, research round 2: shared brief (2026-10-25)

The user asked for a new research round to find a better **Worker 2**: a second live crypto bot that behaves unlike
Worker 1 (= r4_07 ensemble: weekly cross-sectional rotation + ML + 60-day momentum).

## What Worker 2 is now (the bar to beat)
- Afternoon session momentum: alts that move >= 2 vol-units 00:00-16:00 UTC are ridden 16:00 -> 02:00 UTC, max 5,
  1x, vol-scaled sizes. Code: `research/worker2/session/core.py`, results in `session/results.json`.
- At 1x: 8/8 luck checks; TEST (2024-11..2026-10) median month +2.22%, geometric +3.02%, max DD 17.3%; the edge has
  decayed from ~+10%/month geometric (2021) to ~+2-3% (2025-26). October 2026 paper: -12.7%.
- Improvement round (residual momentum, no-shorts-in-uptrend) did not beat it: `research/worker2/improve/REPORT.md`.

A new strategy REPLACES Worker 2 only if it (a) passes all 8 checks at 1x, (b) has TEST median month >= +2.22% and
TEST geometric month >= +3.02%, (c) has |monthly correlation| with Worker 1 <= 0.3 over 2022-01..2026-10, and
(d) is tradable with 500 USDT on Binance USD-M perps at 1x with market orders (min notional ~5 USDT per order).

## Read first
- `research/monthly20/ROBUSTNESS.md` (the 8 checks, exact), `research/monthly20/PROTOCOL.md`, `research/monthly20/common.py`.
- `research/monthly20/R4_BRIEF.md` "What was already tried" plus round 4 results (in its folders' REPORT/results).
  Do NOT repeat: leveraged trend, 14d rotation, funding carry, crash buying, new listings, intraday breakouts,
  pump scanner, pairs stat-arb, seasonality/events, ML walk-forward, short-term 1h reversal, taker flow,
  retail L/S positioning, perp premium, BTC lead-lag, low-vol/beta factors, vol compression, liquidation-cascade fade,
  the 16:00-02:00 session move.
- Data, survivorship-free, already cached (reuse, do not re-download):
  - hourly panel of all USDT perps incl. delisted, top-40/top-20 point-in-time universes, funding:
    `research/monthly20/r4_09_session/base.py` (via `research/monthly20/hpanel.py`): O, Hh, L, C, QV, U40, U20, FUND, FCUM, hvol().
  - daily panel: `research/monthly20/r2_rotation/data.py` / `engine_r2.py`.
  - Binance metrics (OI, long/short, taker): `data/monthly20_r4_positioning/`, `data/monthly20_r4_takerflow/`.
- Worker 1 monthly returns for the correlation check: `research/monthly20/r4_07_ensemble/results.json` key
  `monthly_1x`; daily book `_rbook_full_1x.npy` (how session/run.py uses them: lines ~125-190).

## Rules (same as round 4)
1. **Pre-register** in your folder's PREREG.md BEFORE running TEST: rule, DEV-only grid, the ONE selected config,
   the neighbour grid for check 5. DEV = 2021-01..2024-10, TEST = 2024-11-01..2026-10-21, run TEST once.
2. Costs: fee 0.05% + slippage 0.02% per side, funding included; doubled-cost run too. 1x only (no leverage study).
3. All 8 checks exactly as ROBUSTNESS.md. Report median and geometric month first, then mean.
4. Also report: Worker 1 correlation (monthly and daily), 2025-01..2026-10 at doubled costs, 2026 YTD,
   October 2026, trades per month, and whether 500 USDT works (smallest order vs 5 USDT min notional).
5. Write only inside your own folder `research/worker2/round2/<your_name>/` (and a new cache dir
   `data/w2r2_<your_name>/` if you must download). Research only: no orders, no keys, never read .env or secrets,
   never touch `app/`, never kill processes you did not start. Prefer data.binance.vision over the REST API.
6. Keep runtime under ~60 min. A clean FAIL is a valid result; do not hide losing variants or tune on TEST.

## Final message (short)
- Idea in one sentence + selected config.
- Table DEV / TEST / full at 1x: median, geometric, mean month, max DD, t-stat, trades/month.
- 8 checks n/8 with the failing numbers; Worker 1 correlation; 500 USDT feasibility.
- Verdict vs the replacement bar (a)-(d): REPLACE / NO.
