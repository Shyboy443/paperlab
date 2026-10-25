# Divergence research desk — frozen rule DIV-SR-9

Code: `app/divergence/model.py` (`RULES` there is the machine-readable copy of this page). Paper only.

History of the rule:

- **DIV-SR-1** compared any two adjacent 2/2 wiggles and read RSI on the swing candle itself.
- **DIV-SR-2** (2026-10-11) used real swings and four divergence types.
- **DIV-SR-3** (2026-10-11) added a minimum swing gap, a prior trend for reversals, no-chase checks and a pass/fail
  record of every rule. This followed the operator's SOL report: an equal low marked "regular bullish".
- **DIV-SR-4** (2026-10-11) followed the operator's BTC 1h chart of 7–8 Oct:
  - Swings are measured on **closing prices**, not wick ends.
  - Each new swing is compared with **every earlier** same-side swing, not only the previous one. This catches the
    82,978 → 80,718 regular bullish divergence the old rule missed.
  - Equal highs/lows are **not recorded** at all; the old rule drew 82,723.7 → 82,751.1 as if it were a divergence.
  - Support and resistance are **major 4h levels** built from swing highs and lows over the whole history, drawn on
    the chart.

- **DIV-SR-5** (2026-10-11) fixed the volume check. On the operator's BTC 1h divergence (82,978 → 80,718), the 15m
  break candle (22:30, volume 591) was compared with the mean of the previous 20 candles. That mean still held the
  17:00–17:45 crash candles (up to 8,000), so any normal candle after a capitulation failed. Volume now confirms when
  buying volume beats selling volume on the lower timeframe from the swing-2 low to the break: 19,922 vs 6,656 here.

- **DIV-SR-6** (2026-10-11) moved the stop to the previous swing extreme for the divergence type. Before, it sat
  0.1 ATR beyond swing 2's own wick, which could be inside the pattern when an earlier wick went lower (operator's BTC
  chart).

- **DIV-SR-7** (2026-10-11) added a lower-timeframe trend check and widened the no-chase distance to 2 ATR. On a
  BTC 1h short of 12 June, the 15m "break" closed above a rising 15m EMA50, and the operator noted the 15m was not
  in a downtrend. The rule-set the operator chose (B) still skips a trade when an opposite swing sits before the 2R
  target.

  Turning that room rule into a warning was also tested. It would have taken the operator's BTC 8 Oct long, but over
  two years it gave 602 trades and −8.2R, against 52 trades and +8.7R with the room rule kept.

- **DIV-SR-8** (2026-10-11) added a context check at entry, after the operator flagged three trades: an ETH 4h long
  taken under a fresh top, and BTC shorts taken into support. It follows common divergence-trading guidance: don't
  trade against an active opposite divergence; hidden divergences follow the higher-timeframe trend; support and
  resistance matter.

- **DIV-SR-9** (2026-10-11): the operator wanted more trades. The lower-timeframe trend check and the opposing-
  divergence check are now ⚠ warnings instead of blocks. Over two years this gives 95 trades, 41% wins and +16.2R,
  against 29 trades and +8.8R.

Each version keeps its own database (`divergence-desk.db`, `-div-sr-2.db` … `-div-sr-9.db`), so earlier books are never
changed.

These definitions were chosen before looking at returns, and no number was tuned on the replay. Profitable historical
examples do not establish an edge.

## Inputs and timing

- Completed Binance USDT perpetual 15-minute OHLCV. UTC timestamps label opens.
- Aggregate complete, UTC-aligned buckets into 1-hour and 4-hour candles. A
  bucket missing any 15-minute component is discarded.
- Wilder RSI(14) on closes, Wilder ATR(14) on true ranges. Restart indicator
  warmup and pivot history after a gap; never forward-fill missing prices.
- Price swings use **closes**:
  - a swing low is a close below the closes of the 5 bars before it and the 3 bars after it (highs mirrored);
  - equal closes do not count;
  - a swing is **known only at the close of its third right-side bar**.

  The swing's price is that close, and its RSI is the RSI of the same candle. The candle's wick is kept only to place
  the stop. Lower-timeframe trigger swings use closes too, with 2 bars on each side.
- Both mappings run: 4-hour divergence → 1-hour confirmation; 1-hour divergence
  → 15-minute confirmation. Higher/lower-timeframe charts have different
  availability times; the chart's pivot marker is not an executable entry.

## Setup

Each new swing is compared with **every earlier swing on the same side** that is 5–60 divergence-timeframe bars
older. For each divergence type, the **earliest** valid earlier swing is kept, which gives the longest clean
divergence. A valid pair needs:

- **RSI difference:** at least 3 points, using the RSI on the two swing candles.
- **Swing gap:** the two closes differ by at least 0.25 × ATR(14), using the ATR at swing 2. Anything smaller is an
  equal high/low and **not a divergence**.
- **Clean lines:**
  - no close between the swings is above (for highs) or below (for lows) the straight line between the two swing
    closes;
  - no RSI value between them crosses the line between the two RSI values.

A pair that fails any of these is not a divergence and is not recorded or drawn. A pair that passes is recorded, drawn, and
checked against the rules below. Every rule's pass/fail and its numbers are stored on the setup as `checks`.

| Type | Price | RSI | Side | Also needs |
|---|---|---|---|---|
| Regular bullish | lower low (close) | higher low | long | a swing RSI <= 40 |
| Regular bearish | higher high (close) | lower high | short | a swing RSI >= 60 |
| Hidden bullish | higher low (close) | lower low | long | |
| Hidden bearish | lower high (close) | higher high | short | |

### Filters on the divergence timeframe (any failure = FILTERED, never traded)

1. **Swing gap (all four types):** swing 2 must differ from swing 1 by at least 0.25 × ATR(14), using the ATR at
   swing 2. Anything smaller is an equal high or low.
2. **Regular types: prior trend to reverse.** At swing 1:
   - bullish: the close is below EMA50, and EMA50 is below its value 10 bars earlier;
   - bearish: mirrored.

   A failure is recorded as "No trend to reverse".
3. **Regular types: at a major 4h support or resistance level.**
   - Major levels are built from 4h swing closes (highs **and** lows, so an old resistance can be support) over the
     stored history.
   - The closes are taken in price order. A swing joins the current cluster when it is within 0.5 × the 4h ATR of the
     previous swing, and a cluster is at most one 4h ATR wide.
   - A cluster of 2 or more swings is a level: its price is the mean close and its band runs from the lowest to the
     highest close.
   - For the filter, only levels whose swings were all **known before swing 1 formed** count, with the 4h ATR taken
     at that time.
   - Swing 2's close must be within 0.5 × the 4h ATR of a level's band.

   The level used and its swings (time, price, high/low, known time) are logged in `zone`. For example, BTC 1h on
   8 Oct: swing 2 closed at 80,718.1, 456 from the 4h level 80,262. That level is made of the 79,991, 80,274, 80,286,
   80,301 and 80,458 swings from 25 Aug to 20 Sep.
4. **Hidden types: trend.** At the bar whose close makes swing 2 known:
   - bullish: the close is above EMA50, and EMA50 is above its value 10 bars earlier;
   - bearish: mirrored.

### Lower-timeframe confirmation (structure and volume)

When the divergence becomes known, the desk freezes the latest confirmed opposing lower-timeframe swing as the
trigger: a high for a long, a low for a short. Lower-timeframe swings use 2 bars on each side and must be no older than
20 lower-timeframe bars. The setup is INVALID if price has already closed beyond the trigger.

The **first** later lower-timeframe close beyond the trigger is the structure break. Volume must confirm it:

- take the lower-timeframe candles from the close of the swing-2 candle up to and including the break candle;
- add up the volume of the candles that closed in the trade's direction (up candles for a long), and of the candles
  that closed against it;
- the first total must be at least the second.

The lower timeframe must also trend the trade's way: the break candle closes above (long) or below (short) the
lower-timeframe EMA50 (15m for 1h divergences, 1h for 4h ones).

If the volume check or the trend check fails, or a candle is missing, the setup is INVALID; there is no waiting for a
second break. The
break candle, both volume totals and their ratio are stored as `trigger_candle`.

The setup also ends:

- **EXPIRED:** no break within 6 divergence-timeframe bars.
- **INVALID:** the stop is hit first, or there is a data gap.

### No chasing (any failure = SKIPPED, never traded)

At the confirmation close (the entry reference):

1. **Distance:** the entry must be within 2 ATR of swing 2 (1.5 ATR until DIV-SR-6).
2. **Room to target:** no confirmed same-timeframe swing of the opposite side may sit strictly between the entry and
   the 2R target. For a long that is a swing high; for a short, a swing low. The search covers swings known by the
   confirmation, from the last 60 divergence-timeframe bars.

The lower-timeframe stage is also evaluated for FILTERED setups so their label shows every rule, but only a setup
that passes everything is CONFIRMED and becomes a signal. What the lower timeframe would have done for a non-traded
setup is stored under `hypothetical`.

### Context at entry (DIV-SR-8; a failure = SKIPPED)

1. **No opposite divergence still active.** Take the minor (2/2 close) swings of the divergence timeframe from the last
   60 bars, as known at the confirmation. A long is skipped while the highest swing high was made on a lower RSI than
   an earlier, lower swing high whose RSI was at least 60, unless a close has since broken above that top. Shorts are
   mirrored: the lowest swing low, a higher RSI, and an earlier RSI of at most 40.
2. **Hidden divergences follow the next timeframe up** (4h for 1h divergences, daily for 4h ones). Its last closed
   candle must close beyond its EMA50, and the EMA50 must slope the same way over 10 candles.
3. **Major 4h level before the target:** shown as a ⚠ warning only. Blocking on it left 13 trades in two years.

### Statuses

| Status | Meaning |
|---|---|
| WAITING | Recorded; waiting for the lower-timeframe break |
| FILTERED | A divergence-timeframe filter failed |
| INVALID | The trigger failed: no swing, already broken, stop first, no volume, or a gap |
| EXPIRED | No break in time |
| SKIPPED | Chasing |
| CONFIRMED | Traded on paper |

### History and chart

- **Scanner:** analyses the last 90 days of completed 15m candles (`HISTORY_DAYS`).
- **Chart:** loads the same history (15m capped at 3,000 candles) and can be scrolled and zoomed out. It shows:
  - the current major 4h support/resistance bands, on every timeframe;
  - every other divergence of the timeframe as a faint line (solid when it traded);
  - EMA50;
  - both swings with their price and RSI;
  - the support/resistance swings used;
  - the trigger level;
  - for a confirmed setup, the replayed entry, stop, 2R target and exit with its R (fees and slippage, no funding);
  - every rule passed or failed;
  - a button that opens the lower timeframe at the trigger candle, with the buying and selling volume since swing 2.

The old-versus-new replay is `python -m scripts.divergence.compare_sr2_sr3` (`docs/DIVERGENCE_SR3_COMPARISON.md`).

## Risk and replay

- Stop: 0.1 divergence-timeframe ATR beyond the previous swing extreme (`stop_basis` on the setup):
  - **regular** divergences: the lowest wick (bullish) or highest wick (bearish) of every candle from swing 1 to swing
    2, the extreme the reversal must hold;
  - **hidden** divergences: the extreme wick of swing 2's own swing (its 5 candles before and 3 after, all closed when
    the divergence is known), the higher low or lower high the trend must hold.
- Historical entry: next contiguous 15-minute open after the trigger candle
  closes; a missing entry candle cancels the fill. No trigger-candle entry.
- Actual historical fill includes 0.02% adverse slippage. Recompute the target
  from that fill to maintain 2:1 reward to stop distance. Net reward/risk is
  lower after fees, slippage, funding and adverse gaps.
- Risk size: 0.5% of current equity including planned stop slippage and both fees, capped
  at 1× equity notional (with room for entry commission). Stop gaps can exceed
  that planned loss. Entry and exit fees are 0.04% each.
- A candle hitting both stop and target is stopped first. A stop gap fills at
  the worse observed open. Favorable target gaps receive only the target price.
- Only one position per independent symbol/rule book. Do not sum these separate
  starting-equity books and label the result a realizable portfolio.
- Charge actual historical funding events only to a position already held
  before that event. Positive funding is a long debit and short credit. Use
  actual settlement mark price. Missing marks make funding unverified; no price is
  invented. Intrabar exit/settlement ambiguity charges debits and withholds credits.
- An unexpected missing-data gap while holding exits at the next observed
  open, labelled `data_gap`. Intermediate stop fills are unknown, so any such
  result needs a data-quality caveat.
- Force-close each book at the evaluation interval's final close with costs.
  The optional chronological 70/30 helper reserves one 15-minute bar at the
  boundary. Pre-boundary bars may initialize indicators, but signals confirmed
  before the OOS start cannot create OOS positions. Each interval starts fresh.

Prospective paper execution differs: the service observes the confirmed signal
and any review, then uses a fresh contemporaneous quote. It cannot claim the
earlier next-open historical price after processing/review latency. Its actual
observation and fill timestamps must remain in the persistent paper record.

## Report semantics

Trade `gross` is raw price P&L before slippage, commission and funding. Trade
`net = gross - slippage - fees + funding`. Net equity includes open mark-to-
market P&L and accrued costs/funding; gross equity removes the cumulative cost
and funding cash flows from those same trades.

`net_without_funding` is a cash-flow add-back on the same trades, not an
independently resized no-funding simulation. Run the book again with empty
funding to obtain that full counterfactual, clearly labelling the intentional
absence of funding data. `funding_data_available` alone does not certify the
pipeline has complete coverage; the download manifest must do so.

Monthly returns include partial calendar months at sample boundaries. Very
short-sample annualization is not a forecast; annual metrics are omitted for
intervals shorter than one day. Report signal/trade counts alongside returns,
and do not declare a sparse or empty holdout a successful validation.

## Interfaces

`app.divergence.model.analyze(bars, symbol, now_ms)` provides current setups,
causal signal history and 1h/4h indicator candles. Input rows are dictionaries
`{t, o, h, l, c, v}` with milliseconds and numeric OHLCV. `generate_signals`
returns full historical confirmations; callers must supply completed bars.
`aggregate`, `rsi_series` and `atr_series` support chart preparation.

`app.divergence.backtest.backtest(bars, signals, funding, ...)` returns
`metrics`, `trades`, `equity`, `monthly`, `skipped` and interval metadata.
Funding accepts `(timestamp, rate, mark)` tuples or API-shaped dictionaries.
`split_backtest` implements the frozen chronological split and embargo.
