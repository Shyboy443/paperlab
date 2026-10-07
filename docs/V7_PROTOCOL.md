# V7 active paper challenger

Requested on 2026-09-25: more frequent trades, retaining a maximum 2% planned
risk per paper bot per trade. This explicitly supersedes the earlier instruction
to defer V7 work. Frozen V6 continues independently with its existing positions,
identity, forward start and wallets. V7 cannot place exchange orders.

## Field and rules

- Eight CONTROL books: V7.1 and V7.2 on ARB, ENA, XRP and DOGE.
- Each starts with 20 virtual USDT; 160 total, separate from V6's 1,120.
- Signals on closed 15-minute candles, with hourly and four-hour trend context.
- V7.1: pullback near the 15-minute EMA20 followed by a break of the previous
  candle's extreme in the hourly trend direction; no opposing four-hour trend.
- V7.2: close outside the prior twelve-bar range, volume at least 1.2 times
  the median, a directional close, and no oversized breakout candle.
- Both refuse expected funding charges above 0.10R over the maximum hold and a
  substantial 24-hour open-interest unwind. Funding percentile influences quality
  but does not veto a trade whose absolute funding cost is small.
- Structural stops plus 0.25 ATR; minimum 0.6%, maximum 3% of entry price.
  A wider required stop is rejected, never squeezed to fit.
- Full-position target at 1.8R, maximum holding time six hours, two-bar cooldown.
- Existing V6 sizing policy: 1% ordinary risk, legal-minimum quality tiers up to
  2%, one position per bot, exchange filters and fee/risk guard retained.
  Leverage is only as needed. Gaps and execution costs can exceed planned risk.
- Daily loss halt, peak drawdown guard and permanent capital floor are inherited
  unchanged. No martingale, averaging down or automatic allocation changes.
- No Jev twins in this initial challenger. It is not a comparison of Jev quality.

## Execution and continuity

The existing paper replay engine, Bybit fees, funding, observed spreads, two-minute
decision-to-fill window and stale-data/downtime gates are reused unchanged.
V7 reads positioning only at the last recorded hourly watermark; a missing
watermark refuses the signal. It cannot use later REST arrivals to revise a
quarter-hour decision on restart.

V7 uses a separate `v7-forward.db` next to the main database. It has its own
market stream and persistent inputs, so V6 and V7 cannot overwrite one another's
watermarks, quotes, funding or wallets. `V7_FORWARD_ENABLED=true` enables it;
the default is off. Disabling it pauses the experiment, including exit management.

`V7_FREEZE.json` pins the V6 dependency manifest, V7 strategy and parameters,
profile and shared worker source. A mismatch refuses trading. The experiment
identity starts with `v7x-` and is independent of Jev configuration. Compatible
restarts re-derive books from their original forward start, retaining positions.

## Evidence and reporting

The home page displays V7 alongside V6. The read-only endpoint is
`/api/public/competition/v7`; it includes positions, costs, activity and health.
All entry fees appear immediately; closed-trade statistics remain separate.

`V7_ACTIVITY_CHECK.json` counts raw setups during 2026-09-18 through 2026-09-24.
`V7_REPLAY_CHECK.json` records the fixed-parameter replay on that same recent week,
including fees, modelled execution and funding. The initial checks are preserved
as `V7_INITIAL_*`. Their low activity led to one pre-deployment revision: replace
the funding-percentile veto with an absolute funding-cost budget. The initial
veto blocked long trades for 104–140 of 168 hours despite small absolute rates.
No parameter sweep was run. Both checks were observed during development.
These are retrospective checks, not an independent holdout or live results.
Historical REST arrival times and spreads are unavailable. Setups can overlap;
raw setup counts are not executed trade counts. No improvement in profitability
is claimed. Compare per-bot activity, after-cost returns and drawdown over a
shared future period; the books have different start times and total capital.

Promotion remains a separate decision after meaningful forward evidence. Keep
at least 30 closed trades per bot and 30 days as the existing maturity threshold;
that threshold alone does not establish a profitable edge.
