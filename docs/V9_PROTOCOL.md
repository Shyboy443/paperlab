# V9 STOCKS: paper scalpers on US stocks and ETFs (V9_STOCKS_PAPER_V1)

Requested on 2026-09-27, after the Alpaca paper keys were added. The request was to test bots on the markets Alpaca
supports.

- **Alpaca has no forex.** It trades US stocks and ETFs, options and crypto spot.
- **Crypto on Alpaca was skipped.** Its crypto fees start at 0.25% per market order (tier 1; maker 0.15%), about 5×
  Bybit (0.055%) and Binance (0.05%), and Alpaca allows no shorting or leverage on crypto.

V6, V7 and V8 are untouched and keep running.

## Market and data

- **Alpaca's free IEX feed:** 1m bars and best bid/ask over the WebSocket, with REST for warm-up and gap repair. It
  runs only on the account's keys: the dashboard's encrypted key store (Alpaca paper), or the
  `ALPACA_PAPER_API_KEY`/`_SECRET` variables. The keys are used for market data only.
- **Regular sessions only,** 09:30–16:00 New York, taken from Alpaca's trading calendar (holidays and half days
  included). Pre-market and after-hours bars are dropped.
- **Quiet minutes:** IEX prints only when it trades. A session minute with no IEX bar 15 s after it ends is filled
  flat at the last close, with volume 0. This happens only while the stream is healthy; after an outage the gap is
  repaired from REST first. Flat fills are stored like real bars, so a restart re-derives the same books.
- **Spread:** market orders pay the IEX half-spread capped at 1 bp, or the engine's model without quotes.
  The cap is a modelling assumption, not an observed consolidated NBBO spread. Resting limit targets
  require trade-through and fill at their limit. Both order types pay the fee proxy.

## Field

**Symbols** (10): SPY, QQQ, AAPL, NVDA, TSLA, AMD, MSFT, META, AMZN, GOOGL.

**Bots:** 3 families × 10 symbols = 30 CONTROL bots, each with a matched +JEV twin, 60 bots in total.

**Books:** 1,000 USD per bot, whole shares, and at most 3× paper buying power.

## Strategies (`app/strategies/v9/stocks.py`)

Stock-specific versions of V8's three 5m families (`app/strategies/v8/arena.py`):

| id | setup |
|---|---|
| V9.1 | micro-pullback: genuine overlap with the bar's EMA20 band within today's session, then a resumption close in the 15m trend; rejects an opposing 1h trend |
| V9.2 | micro-breakout: a close outside today's prior 6-bar range on ≥ 1.1× median volume, not against the 1h trend; requires real-volume history |
| V9.3 | VWAP snap-back: a prior-bar 1.8–4 ATR stretch from the current-session HLC3/volume average, then a reversal with ≥ 0.75R room to that average |

**Session gate:** a candidate is accepted only between 10 minutes after the open and 50 minutes before that session's
close (half days included). With the 45-minute time stop, **nothing is ever held overnight.**

**Exits:**
- **Stop:** the structural extreme + 0.2 ATR, clamped to 0.25–1.0%. Commission-free stocks don't need V8's
  fee-driven 0.45% floor.
- **Target:** 1.5R for V9.1/V9.2; current-session VWAP proxy for V9.3.
- **Time stop:** 45 minutes, explicitly capped one minute before the calendar close.
- **Cooldown:** one 5m bar.

## Execution, costs, risk

- **Timing:** V8's 60 s decision window. The fill is at the open of the first 1m bar at or after the close + 60 s. A
  decision later than 55 s is LATE; stale data pauses a symbol.
- **Costs:** 0.2 bps per filled side approximates regulatory costs; it does not reproduce the exact sell-side fee schedule.
- **Risk:** AGGRESSIVE_V6 legal tiers (1% base, 2% cap), a 3× leverage ceiling, and the engine's daily-loss and
  drawdown halts.

## Jev (`app/ai/jev/v9.py`)

- **Question:** JEV_PROMPT_V9_STOCKS asks about a stock scalp in the regular session.
- **State:** JEV_STATE_V9 is JEV_STATE_V8 plus the minutes left before the close.
- **Policy and timing:** the same SKIP/TAKE/ATTACK policy and timing as V8. An answer after H + 55 s is SKIP.

## Elimination and qualification

**Eliminated:**
- **Drawdown:** a bot 25% below its start leaves at once.
- **Inactivity:** judged right after each session's close, never over a weekend. A CONTROL with fewer than 3 closed
  trades over its last 3 sessions leaves, and its twin leaves with it. A twin is never judged for inactivity.

**Qualified:** all of the following.
- ≥ 4 sessions and ≥ 20 closed trades;
- net > 0;
- PF ≥ 1.2;
- max drawdown ≤ 15%;
- no halt.

QUALIFIED is a gate for the operator's go-live button, not a winner claim.

## Pre-freeze activity check (`docs/V9_ACTIVITY_CHECK.json`)

This legacy activity check replayed 10 sessions (2026-09-14 to 2026-09-25) through the original engine.
Its counts describe that version, not the 2026-10-07 repair.

- **Trades:** a median of 3.8 per bot per session. V9.3 does 4–9, V9.1 about 3–4, V9.2 about 2–3.
- **Overnight:** 0 positions held.
- **Flat-filled minutes:** 0–6.5% of the session (AMD is the highest).
- **Zero-trade sessions:** most bots had at least one. That is why inactivity is judged over 3 sessions, not 1.

The check counted trades only; nothing was tuned for profit.

## Honest limits

- **Session length:** the US session is 6.5 hours, so stock bots trade less per day than the 24/7 crypto scalpers.
- **Quotes:** IEX is one exchange, not the NBBO. The 1 bp cap may understate or overstate real routing costs;
  historical replays without quotes use an OHLCV model. There is no demonstrated live-fill accuracy.
- **Live accounts:** shorting needs a margin account with at least $2,000 of equity. A smaller Alpaca account can
  mirror long trades only.

## Stock mechanics repair (2026-10-07)

The original freeze and strategy/feed sources are retained under `docs/archive/`
and the original forward experiment remains in SQLite. A changed freeze starts
a separate experiment; prior gains are not carried into a repaired book.

- The isolated `StockReplayEngine` walks crossed stop/target levels instead of
  awarding the subsequent minute high/low. Opening stop gaps still fill at the
  opening price plus market friction. When both levels are touched, the adverse
  path remains first. Resting targets require 0.5bp trade-through and fill at the
  target price, without fictitious price improvement. Both order types pay the
  same 0.2bp regulatory-fee proxy; this is not a precise broker fee calculation.
- Feed repair and healthy-stream silence filling cannot seed today's session
  with yesterday's closing price. Filling starts after today's first real bar.
- Synthetic zero-volume signal bars cannot create entries. Pullback and range
  windows stay within the current session, and an EMA touch requires a bar to
  overlap its contemporaneous EMA band rather than merely be below it.
- The VWAP candidate uses a regular-session HLC3/volume proxy, tests the prior
  bar against its prior VWAP, and takes profit at the session average only when
  at least 0.75R remains. The rolling four-hour average was not a session VWAP.
- A filled position's deadline is capped one minute before the actual calendar
  close, including half days. AI counterfactuals use the same exit mechanics.
- Historical V9 result paths include the freeze fingerprint and test window.
  Old results cannot silently masquerade as the output of changed stock rules.

The bounded repair comparison is defined in `scripts/v9_fix_validation.py`.
It compares original mechanics, corrected mechanics, session signal changes and
a single wider-stop candidate across all ten symbols. Ranking uses the first
year only. Two subsequent six-month periods test that choice. These historical
dates were already inspected in earlier research and are not an independent
unseen test; the repaired forward experiment must provide fresh evidence.

The 120-comparison repair study finished successfully. The session correctness
repairs are deployed for paper probation; the wider-stop parameter is not adopted.
No family passed the profitability checks. See `V9_STOCK_REPAIR_AUDIT.md` for
measured changes and `stock-bot-audit/V9_FIX_VALIDATION_RESULTS.json` for all runs.

The earlier parameter grid reduced losses but did not demonstrate profitability.
It is retained as historical evidence, not labelled a successful repair. Raising
leverage or removing costs is not an acceptable way to turn it into a winner.

## Fix: instant "liquidations" at 4x (2026-10-02)

- **Symptom:** in experiment `v9x-102aa42b8c91`, 396 of 609 trades ended as `liq` in the same minute they opened, at
  about the entry price (≈ −0.03 R each: fees for nothing).
- **Cause:** the engine's paper liquidation sits at 1/leverage − maintenance margin. Stocks use 25% (Reg T), so at
  the 4x cap the distance is 0 and the liquidation price is the entry. Every 4x position was liquidated at once; no 1–3x
  position ever was.
- **Fix:** `LEVERAGE_CAP = 3`. The liquidation now sits 8.3% away, beyond any 0.25–1% stop. A trade that wanted more
  than 3x is sized down to 3x (less than 1% risk) rather than refused.
- New freeze and new experiment. The old experiment is history and is contaminated by the bug.

## Fix: the IEX spread overstated stock costs (2026-10-02)

- **Symptom:** in the first experiment the non-liquidated trades paid a median 13 bp a round trip in "slippage", 33×
  their fees. By symbol: TSLA and AMD 25 bp, META 16, GOOGL 14, MSFT 13; but NVDA 1.9 and QQQ 3.1.
- **Cause:** every fill paid the observed IEX half-spread (capped at 15 bp). Alpaca's free plan only carries the IEX
  book, which is not the NBBO. Its stored median half-spreads were TSLA 15 (63% at the cap), AMD 15 (58%), META 8.9,
  GOOGL 7.1 and MSFT 4.1 bp, while these mega-caps trade 1 cent wide (~0.1–0.5 bp a side).
- **Fix:** `MAX_HALF_SPREAD_BPS` goes from 15 to 1.0. Tight IEX quotes (SPY and QQQ ~0.3 bp) are kept as observed;
  wider ones are charged 1 bp, still several times the real spread.
- New freeze and new experiment, made before the session opened.
