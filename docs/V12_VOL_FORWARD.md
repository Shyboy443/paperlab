# Renata V12-VOL: pre-registered forward paper test

Written 2026-10-10, before the test starts.

| | |
|---|---|
| Status | **Paper only.** Nothing in this program can place an order. |
| Code | `app/lab12/forward.py`, with the signal from `app/lab12/families.py` and fills from `app/lab12/engine.py` |
| Freeze | `docs/V12_VOL_FREEZE.json` |
| Page | `/public/renata-v12-vol` |
| API | `GET /api/public/v12vol` (read-only) |

## Why this test exists

The V12 research (`docs/V12_RESEARCH.md`) found no strategy that cleared all three pre-registered stages. One rule
was positive on all three disjoint historical windows, but it never reached significance and failed the DEV threshold:

| Rule | DEV | VAL | FINAL |
|---|---|---|---|
| E vol-expansion follow, 24h hold, + C regime | +0.049R (t 1.64) | +0.032R (t 1.20) | +0.063R (t 1.67) |

It was noticed only after every historical window had been seen, so no untouched history is left to test it on. The
only honest test that remains is on **future** data, with the rule fixed in advance. This document fixes it.

## The rule (unchanged from the research)

Every hour, on the 1h bar that just closed, for each of the 30 coins (the V11 list, Bybit USDT perpetuals):

1. **Signal:** range ≥ 2.5 × ATR14(1h) of the previous bar, **and** quote volume ≥ 3 × its trailing 7-day hourly mean.
2. **Side:** set by where the bar closed within its range.
   - Top quarter: long.
   - Bottom quarter: short.
   - Anywhere else: no trade.
3. **Regime filter (book VOL only):**
   - Longs are allowed only while BTC is above a rising 168h EMA.
   - Shorts are allowed only while BTC is below a falling one.
   - The filter uses closed hours only.
4. **Entry:** at the open of the minute after the decision (HH:01).
5. **Stop:** a guard stop of 1.5 × ATR(1h) × √24, clamped to 1–25% of entry. This distance is the R unit. Stops are
   checked on closed 5m bars, stop first; a bar that opens beyond the stop fills at its open.
6. **Time exit:** at the close of the 24th hour.
7. **Costs:** Bybit taker fee of 5.5 bp per side, plus slippage per side of:
   - 2 bp on BTC, ETH, SOL, XRP, BNB and DOGE;
   - 4 bp on the other coins.

   Real Bybit funding is charged or credited for every settlement between entry and exit.
8. **Portfolio:**
   - at most 6 open positions, and at most one per coin;
   - when there are more signals than free slots, the largest range/ATR goes first;
   - notional per trade = book equity / 6;
   - the starting book is 1,000 USDT.

The signal code is the research function itself, `families.vol_expansion`, run on a `families.panel` built from
Bybit's closed 5m klines. Tests (`tests/test_v12vol.py`) prove that the live signals and the live trade path equal the
research engine on the same data.

## Books

| Book | Rule | Judged? |
|---|---|---|
| **VOL** | the rule above, with the regime filter | **yes, the only judged book** |
| VOL-RAW | the same rule without the regime filter | no, comparison only |

## Dates

- **Start:** first decision at 2026-10-11 00:00 UTC.
- **End:** last decision at 2027-04-11 00:00 UTC (six months). Open trades then run to their normal exit.
- **Halves:** decisions before or after 2027-01-10 00:00 UTC.

There is no early stop for good or bad results. An operational fault (bad data, a bug) is fixed only by a new,
re-frozen experiment with new dates, and is reported as such.

## Pass rules (judged once, at the end, on VOL only)

The test passes only if **all** of these hold:

1. expectancy > 0 R per trade;
2. daily t-statistic ≥ 2.0, counting every calendar day of the test (days without trades count as 0);
3. profit factor ≥ 1.10;
4. ≥ 150 closed trades (research rate: about 1.2–1.6 trades a day, so roughly 220–280 are expected);
5. expectancy still > 0 with doubled fees and slippage;
6. expectancy > 0 in both halves.

A PASS is paper evidence only. It does not start live trading. Live capital always needs the operator's explicit
approval, and any live version would first need the limitations below addressed.

## What is measured alongside

The following are reported, but never used to change the rule:

- realistic fill quality: at each on-time entry, the quoted bid/ask is snapshotted, and the page shows the half-spread
  and the entry price vs the mid, against the modelled 2/4 bp;
- fees and funding in USDT;
- drawdown in R;
- stop share;
- results by BTC regime and by coin;
- the VOL-RAW comparison.

## What is not allowed

- **Changing anything frozen.** The freeze covers the code, coins, dates, costs, portfolio limits and criteria (a
  fingerprint over `engine.py`, `families.py` and `forward.py`, plus these settings). A mismatch keeps both books shut
  (`FROZEN_MISMATCH`). A changed rule is a new experiment with new dates.
- Optimizing any parameter against the forward results.
- Reading an interim result as a verdict.

## Robustness to deploys

Every input is closed exchange history: 5m and 1m klines, and funding. A decision missed while the server restarts is
processed late, giving the identical result, and is flagged `catch_up`. Only its live spread snapshot is lost.

## Known limitations (to address before any live decision)

- **Survivorship:** the coin list is today's V11 list.
- **No order-book simulation:** fills are the next-minute open plus fixed slippage. The spread snapshots measure this
  but don't replace it.
- **Gaps in Bybit's kline data:** a missing 5m bar pauses a position's evaluation until the bar appears; it is never
  skipped. The research instead discarded trades with missing bars.
- **Rounding:** live bars are float32, as in the research. Results near a threshold can differ from a float64
  computation.
