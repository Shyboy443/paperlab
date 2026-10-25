> Archived v1 approximation, superseded by [the corrected published-source experiment](QQQ_VALUE_REVERSAL_V2.md). Its results do not evaluate the source-matched v2 rules.

# QQQ session value-area reversal

Source: https://www.youtube.com/watch?v=dUczefIYKIU (LuxAlgo).
Rules frozen on 2026-10-08 before evaluation. This is the operator-approved **QQQ ETF adaptation**,
not a claim to reproduce the video's Nasdaq futures trades or its proprietary indicator.

Video references: 00:13–00:57 volume/profile reversal; 07:45–08:30 diminishing outside volume and
stronger opposing return; 14:31–14:51 previous session is the default; 17:02–18:12 five-bar expiry,
engulfing candle, stop beyond the excursion; 18:18–18:40 opposite value-area boundary or optional
risk-multiple/POC target. We choose the opposite boundary only, with 70% value area (video default).

## Explicit rules and assumptions

- QQQ regular stock sessions, using Alpaca's actual calendar (including early closes). 09:30
  America/New_York anchor; no futures evening-session substitution. Daylight saving is respected.
- Four fixed experiments: 15/30-minute candles × previous/developing session profile. Default:
  15-minute previous session. No selection or tuning based on results.
- 64 equally spaced profile rows. Each completed candle's volume is allocated uniformly over its
  high–low range. This OHLCV approximation cannot identify true volume at each price or order-book
  absorption. Candle direction classifies volume; it is not aggressor buy/sell volume.
- Value area expands contiguously from the maximum-volume row until it contains at least 70% of
  volume; ties expand toward the lower row. Prior session must have every expected calendar bar.
  Developing profile uses only prior completed bars and needs three bars to warm up.
- Arm only after an inside close, followed by a close outside the boundary. Freeze that profile's
  boundaries for the excursion. Two consecutive outside candles in the breakout direction must
  have falling volume. Re-entry must close inside within five candles of the first outside close,
  be an opposing body engulfing the immediately preceding body, and have more volume than that
  preceding outside candle. Long below the low; mirror for shorts above the high.
- Fill on the immediately following bar's open; skip a gap or next-day entry. Stop one cent beyond
  the excursion extreme including the signal candle; target the opposite frozen boundary. Skip if
  a next-open gap is already beyond stop/target. Stop takes priority if both touched; adverse stop
  gaps fill at the worse open. No entry-candle high/low is used before entry.
  Simulated fills are rounded adversely to the stock's one-cent price increment.
- Flatten at the last regular-session candle's close; no overnight inventory or funding. One whole-
  share position per independent simulated $10,000 book; 0.5% equity stop risk; gross <= 100%.
  Shorts are simulated, without a broker locate guarantee. Intraday borrow fee is assumed zero.
- Conservative cost allowance: 1 bp fee + 2 bps adverse slippage per executed side, not an assertion
  about an actual Alpaca fee tier. Run a separate doubled-fee/slippage stress for every configuration.
- Drawdown >=20% halts that paper experiment; it never automatically resets its wallet.

## Validation and forward evidence

Fixed retrospective split: 2022–2023 development; 2024–2025 held-out comparison. Both have been
observed by prior research; this is **retrospective**, not newly unseen market evidence. Each
period starts with its own wallet. QQQ buy-and-hold is a benchmark, including overnight exposure
and the same cost allowance. Reports include monthly net, CAGR, Sharpe (zero cash benchmark),
max drawdown, worst month, positive months, trades, costs, and doubled-cost results.

The new forward start is the first successful Railway data refresh after deployment. Startup bars
only warm profiles; **no pre-start fills**. SIP bars are requested with an end at least 16 minutes
old; they are processed only once fully complete. Next-bar fills and stop/target recovery are
**delayed-feed candle simulations**, stored with observation time. They are not historical-price
orders sent to a broker or evidence of executable real-time fills. Missed/gapped sessions pause
new entries; a recovered stop uses adverse observed bar gaps. Contract/tick absorption is absent.

This program has no order-submission method. Existing live Alpaca stock strategy, account holdings,
crypto research, frozen rules and saved histories are not modified. State persists on Railway's
data volume. No attractive backtest qualifies it for real-money trading.

Run `python scripts/qqq_value_reversal.py --help` for reproducible Alpaca download and backtest.
Historical results are cached on Railway's persistent volume. The hosted page separates those results
from prospective delayed-feed paper books and labels the source and observation timestamps.
