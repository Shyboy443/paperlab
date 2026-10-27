# Worker 3, round 5: shared brief (2026-10-26)

The operator wants Worker 3 to have **much better results**:
- better strategies;
- better variants of the leads;
- the coins each rule suits best.

They also want this **without curve-fitting** (standing rule: no lottery bots).

## Where we are (read first)
- `research/worker3/round1..4/REPORT.md`: 16 families tested. None beats Worker 1 alone.
- **high52** (Donchian-30 L/S, top-40, daily) is the best lead:
  - TEST geo +5.23%;
  - 2018–2020 holdout geo +3.50%, 8/8 checks.

  It is mainly a long-side, BTC-uptrend effect: `research/worker3/round4/high52_v2/REPORT.md`, code in `lib.py` there.
- The bots to beat (1x, all costs). Worker 2 data is in `research/worker2/backfill/results.json`.

  | | TEST geo / median month | months up | max DD | HOLDOUT geo |
  |---|---|---|---|---|
  | Worker 1 | +5.33% / +3.84% | 17/24 | 23.3% | +2.69% |
  | Worker 2 | +3.02% / +2.22% | ~15/24 | 17.3% | +3.56% |
  | W1+W2 50/50 | +4.48% / +3.54% | 20/24 | 5.9% (month-end) | +3.30% |

## Why the protocol is stricter this time
- **TEST is spent.** The TEST window (2024-11..2026-10) has been seen by 16 families.
- **The holdout is partly used.** The 2018–2020 HOLDOUT has been used by 3 ideas.

So a new idea only counts if it is chosen on DEV and then passes on **BOTH** windows.

## Protocol
1. **DEV only.** DEV (2021-01..2024-10) is the ONLY data used to choose anything: rules, parameters, coins and weights.
   - Insights taken from the TEST or HOLDOUT reports must be re-verified on DEV before use. Say which ones you used.
2. **Pre-register.** Write PREREG.md BEFORE running TEST or HOLDOUT. It must contain:
   - every variant tried and the total config count;
   - the selection rule and the ONE selected config;
   - the neighbour grid for check 5.

   Then run TEST once and HOLDOUT once.
3. **Data and costs.**
   - TEST uses perps: hourly or daily panels, with funding.
   - HOLDOUT uses Binance spot for 2018–2020: `data/w1_backfill/panel.npz` and `data/w2_backfill/{1d,1h}`. Reuse
     `research/worker1_backfill/` and `research/worker3/round4/high52_v2/lib.py` (the holdout panel is cached in
     `data/w3r4_high52_v2/`). Shorts on spot are assumed possible; say so.
   - Costs: fee 0.05% + slippage 0.02% per side, plus a doubled-cost run. 1x only, so no leverage tricks.
4. **Report**, for DEV, TEST and HOLDOUT:
   - median, geo and mean month;
   - months up, max DD, t, trades per month;
   - the 8 checks of `research/monthly20/ROBUSTNESS.md` on TEST and HOLDOUT;
   - W1 and W2 monthly correlation;
   - the W1+W2+X portfolio, in thirds and as the best DEV-chosen mix;
   - whether it works from 500 USDT.
5. **Scope.**
   - Write only in `research/worker3/round5/<your_name>/`, plus `data/w3r5_<your_name>/`.
   - Research only: no orders, no keys, never read .env or secrets, never touch `app/`, never kill processes you did not
     start.
   - Keep runtime under about 90 min.
   - A clean FAIL is a valid result. Never hide losing variants.
6. **REPORT.md.** Write it in your folder. If the file tool is refused, put the full report in your final message.

## The bar
**STRONG**, all of these:
- TEST geo > +5.33% and median > +3.84%;
- TEST ≥ 18/24 months up and max DD ≤ 23%;
- HOLDOUT geo > +2.69% (beats W1 there too);
- 8/8 checks on both windows;
- doubled costs still positive on both windows.

**PORTFOLIO**: W1+W2+X beats W1+W2 50/50 on both windows. That means a higher geo month, or the same geo with a smaller
drawdown and at least as many months up.

Anything else is FAIL.

## Final message (short)
- the idea, its sources and the selected config;
- a DEV / TEST / HOLDOUT table at 1x;
- checks n/8 per window, with the failing numbers;
- the portfolio rows;
- the verdict: STRONG / PORTFOLIO / FAIL.
