# Round 4 brief: new strategy families (shared by all 10 round-4 agents)

The user wants better results than rounds 1-3, from **strategy families not yet tried**, with **leverage capped at 10x**.
They do NOT want lottery bots: a strategy only counts if it is not luck.

Read first: `PROTOCOL.md`, `ROBUSTNESS.md`, `common.py` in this folder; `r2_rotation/engine_r2.py` and `r2_rotation/data.py`
show a survivorship-free daily panel with delisted coins. Reuse the caches in `data/monthly20_cache/`,
`data/monthly20_04_crash_reversal/`, `data/monthly20_05_new_listings/`, `data/monthly20_07_pump_scanner/` before downloading.
Only public, keyless data (data.binance.vision archive, Binance public REST). The archive also has daily
`metrics` files (open interest, long/short ratios, taker volume) and `premiumIndexKlines` / `markPriceKlines` for UM futures.

## What was already tried (do not repeat)
Round 1 (all FAIL vs 20%/month): leveraged trend, cross-sectional 14d momentum rotation, funding carry, crash buying,
new listings, intraday breakouts, pump scanner / buy-the-dump, pairs stat-arb, seasonality/events, ML walk-forward.
Round 2 luck checks (1x): rotation 7/8 (2026 YTD -26.7%), ML 7/8, 60-day momentum 5/8, EMA5/100+ADX 2/8 (luck).
Round 3: regime gates on rotation (BTC>100d, dispersion, breadth...) - none reliable.

## Rules
1. **Pre-register.** Before touching TEST (2024-11-01..2026-10-21), write `PREREG.md` in your folder: the rule, the
   parameter grid, the ONE selected config chosen on DEV (2021-01..2024-10) only, and the neighbour grid for check 5.
   TEST is run once. No re-tuning after seeing TEST.
2. **Costs:** fee 0.05% + slippage 0.02% per side, funding included, doubled-cost run too. Liquidation as in PROTOCOL.md.
3. **Judge at 1x first** with all 8 checks in ROBUSTNESS.md (delisted coins included where the strategy trades alts).
   Then report the leverage (1x..10x max, never above) that would give a 20% arithmetic mean month, and what that
   leverage does to max drawdown, the worst month, geometric mean month and liquidations. Leverage over 10x is not allowed.
4. **Report both** the arithmetic mean month (user's target: +20%, i.e. 240%/yr simple) and the median and geometric
   mean month. Count months 2021-01..2026-10 by calendar day (a daily bar counts on the day it covers, not the day it
   closes; see `r2_rotation/year2026.py`).
5. Also report **2026 year-to-date** (calendar months) for the selected config at 1x.
6. Write everything to your own folder only: `run.py` (or similar), `PREREG.md`, `results.json`, `REPORT.md`.
   If a file write is blocked, put the report in your final message instead. Never ask the main session to write files.
7. Research only: no orders, no API keys, no credentials, no edits outside your folder (except new cache dirs under
   `data/monthly20_r4_<name>/`). Never kill processes you did not start. Do not touch `app/` or any IBS bot files.
8. Keep runtime reasonable (aim < 60 min). Be honest: a clean FAIL is a valid result. Do not hide losing variants.

## Final message format (short)
- Idea in one sentence, selected config.
- Table: DEV / TEST / 2021-2026 at 1x: arithmetic mean month, median month, geometric mean month, max DD, t-stat.
- 8 checks PASS/FAIL list (n/8).
- Leverage needed for 20%/month (or "not reachable at <=10x"), with DD/liquidations at that leverage.
- 2026 YTD at 1x.
- Verdict: ROBUST / NOT ROBUST, one line why.
