# Worker 3, research round 2: shared brief (2026-10-26)

The operator wants **Worker 3**: a live crypto bot that is BETTER than both running bots, found by looping research
(internet allowed) until a candidate clears the bar below, without curve-fitting.

## The bots to beat (TEST = 2024-11-01..2026-10-21, 1x, all costs)
| | median month | geometric month | months up | max DD |
|---|---|---|---|---|
| Worker 1 = r4_07 ensemble (weekly rotation + ML + 60d momentum) | +3.84% | +5.33% | 17/24 | 23.3% |
| Worker 2 = r4_09 session momentum rule P (16:00 -> 02:00 UTC alts) | +2.22% | +3.02% | ~15/24 | 17.3% |
Sources: `research/monthly20/r4_07_ensemble/results.json` (`test_1x`, `monthly_1x`), `research/worker2/session/results.json`.
A 50/50 Worker 1 + Worker 2 mix won 20/24 TEST months (`research/worker1_backfill/`, `research/worker2/backfill/`).

## The bar (all must hold, judged at 1x)
- (a) 8/8 luck checks exactly as `research/monthly20/ROBUSTNESS.md`;
- (b) TEST geometric month > +5.33% AND TEST median month > +3.84% (beats Worker 1, hence Worker 2);
- (c) TEST months up >= 18/24 and TEST max DD <= 23% (stretch goal: 22/24 months up);
- (d) tradable on Binance USD-M perps at 1x with market orders from 500 USDT (min notional ~5 USDT per order),
  doubled costs still positive over 2025-01..2026-10.
A candidate that is not better alone but makes a W1+W2+X portfolio better on (b)-(c) is also reported (say so).

## Read first
- `research/monthly20/ROBUSTNESS.md` (the 8 checks), `research/monthly20/PROTOCOL.md`, `research/monthly20/common.py`.
- Already tried (do NOT repeat; see `research/monthly20/R4_BRIEF.md` and `research/worker2/round2/REPORT.md`):
  leveraged trend, 14d rotation, funding carry, crash buying, new listings, intraday breakouts, pump scanner, pairs
  stat-arb, seasonality/events, ML walk-forward, short-term 1h reversal, taker flow, retail L/S positioning, perp
  premium, BTC lead-lag, low-vol/beta factors, vol compression, liquidation-cascade fade, the 16:00-02:00 session move,
  other intraday session windows, MAX/lottery, attention shocks, funding-clock drift, OI-confirmed momentum,
  and Worker 3 round 1 (research/worker3/round1/REPORT.md): W1/W2 allocators, daily/weekly residual reversal and
  residual momentum, Deribit DVOL timing, stablecoin-float timer, turnover-volatility factor, frog-in-the-pan /
  trend-score momentum, token-unlock shorts. Read round 1's lessons before starting.
- Data already cached (survivorship-free; reuse, do not re-download):
  - hourly panel of all USDT perps incl. delisted, point-in-time top-40/top-20 universes, funding:
    `research/monthly20/r4_09_session/base.py` (via `research/monthly20/hpanel.py`): O, Hh, L, C, QV, U40, U20, FUND, FCUM, hvol().
  - daily panel: `research/monthly20/r2_rotation/data.py` / `engine_r2.py`.
  - Binance metrics (OI, long/short, taker): `data/monthly20_r4_positioning/`, `data/monthly20_r4_takerflow/`.
  - Worker 1 daily book `research/monthly20/r4_07_ensemble/_rbook_full_1x.npy`; Worker 2 code `research/worker2/session/core.py`.

## Rules
1. Use the internet (WebSearch/WebFetch: SSRN, arXiv, papers, quant blogs, public APIs) to find and justify the idea.
   Cite the sources in PREREG.md. Prefer ideas with published out-of-sample evidence.
2. **Pre-register** in your folder's PREREG.md BEFORE running TEST: rule, DEV-only grid, the ONE selected config, the
   neighbour grid for check 5. DEV = 2021-01..2024-10, TEST = 2024-11-01..2026-10-21; run TEST ONCE.
3. Costs: fee 0.05% + slippage 0.02% per side, funding included; doubled-cost run too. 1x only.
4. Report median and geometric month first, then mean; months up; max DD; t-stat; trades/month; Worker 1 and Worker 2
   monthly correlation; 2026 YTD; October 2026; whether 500 USDT works.
5. Write only inside `research/worker3/round2/<your_name>/` (new downloads into `data/w3r2_<your_name>/`). Research only:
   no orders, no keys, never read .env or secrets, never touch `app/`, never kill processes you did not start.
6. Keep runtime under ~75 min. A clean FAIL is a valid result; never hide losing variants, never tune on TEST.
7. Write REPORT.md in your folder; if the file tool is refused, put the full report in your final message.

## Final message (short)
- Idea + sources + selected config; table DEV / TEST / full at 1x (median, geo, mean, months up, max DD, t, trades/mo);
- checks n/8 with failing numbers; W1/W2 correlation; 500 USDT feasibility;
- verdict vs the bar (a)-(d): PASS / PORTFOLIO-ONLY / FAIL.
