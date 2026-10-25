# Gold Scalping Strategy (1-Min TF): does it hold on 2 years of gold? (pre-registered 2026-10-20)

**Input:** the operator's PDF "Gold Scalping Strategy (1-Min TF)".

**The strategy, as stated in the PDF:**
- Sessions: the London open and the New York open, in Pakistan time.
- Indicators: 9 EMA, 21 EMA and RSI(14).
- Entry: an EMA cross, RSI on the trend side of 50, a break of recent support or resistance and a strong confirmation
  candle.
- Exit: take profit 10–20 pips, stop loss 10–15 pips, or close on the opposite signal.
- Trade 3–5 setups a day and risk 1–2% per trade.
- Optional extras: the "Mayfair FX Scalper" filter, avoiding news, and support/resistance zones.

**It names its asset (gold), so only XAUUSD is tested** (operator, 2026-10-20: "if the report says a specific asset,
only research that").

## Fixed before any result was seen

- **Rules:** `app/ytstrats/gold.py`.
  - Sessions in UTC: 07:00–10:00 and 13:00–16:00 (Pakistan time is UTC+5 all year). The signal candle must close
    inside one.
  - **Buy:**
    - the 9 EMA crossed above the 21 EMA within the last 5 candles and is still above;
    - RSI(14) > 50;
    - the close is above the highest high of the previous 10 candles ("breaks recent resistance");
    - the candle is bullish with a body of at least 60% of its range ("strong confirmation candle").
  - **Sell:** the mirror image.
  - **Exits:**
    - 1 pip = 0.10 USD (MT5's 10 points), as the PDF uses for gold.
    - All 9 pairs are reported: take profit 10, 15 or 20 pips × stop 10, 12 or 15 pips.
    - An opposite signal closes at the next open.
  - At most 5 entries per day, one position at a time.
  - Left out because they can't be made mechanical: the Mayfair filter, news avoidance and hand-drawn zones.
- **Data:** Dukascopy's public 1-minute XAUUSD BID candles (UTC), with no account.
  - Year 1: 2024-10-20 → 2025-10-20.
  - Year 2: 2025-10-20 → 2026-10-20.
  - Checked against the operator's MT5 (Fusion Markets) 1-minute gold where the two overlap (July–October 2026).
- **Costs, from the operator's broker** (MT5 1-minute data, July–October 2026):
  - mean spread 0.057 USD;
  - commission 0.0225 USD per ounce per side (2.25 USD per lot);
  - 0.02 USD slippage per side.
  - A buy fills at the ask; exits trigger on the side of the book they trade on.
- **Same-candle rule:** a 1-minute candle touching both the stop and the target counts as the stop. There is no finer
  data.
- **Sensitivity, not part of the verdict:** the same 15/12 rule with 1 pip = 1 USD (a convention some gold traders
  use), reported separately.

## Decision rule (fixed now)

A TP/SL pair is **PROVEN on gold** only if, with all costs, all of these hold:

1. Year 1: mean net R > 0 and profit factor ≥ 1.10;
2. Year 2: mean net R > 0, profit factor ≥ 1.15 and at least 100 trades;
3. Year 2: the lower end of the bootstrapped 95% interval of the mean net R is above 0;
4. Year 2: net positive in at least half of its months.

**Multiple testing:** 9 pairs are tested, so even a pass is only a **candidate for a forward paper test**, not a live
bot. Nothing here is changed after the results are seen.

## Preliminary result (2026-10-20): the 3 months available, not the verdict

The pre-registered 2-year test needs 1-minute gold back to October 2024:
- Dukascopy throttled the download after one day.
- The operator's MT5 keeps only 100,000 bars ("Max. bars in chart"), which covers 2026-07-11 → 2026-10-19.

Run on that window with the fixed rules (`app/ytstrats/gold_results.json`, `GET /api/public/gold-scalp`):
- **All 9 TP/SL pairs lose:** −17.8 to −27.9R per 100 trades after costs (about 353 trades each, PF 0.57–0.73).
- **They also lose on the price move alone** (−7.5 to −13.7R per 100). Entering after a strong 1-minute breakout candle
  tends to buy the top of the burst.
- **Costs** are 0.09–0.14R per trade at these 1.0–1.5 USD stops.
- **Same-candle ties** (2–8% of trades) don't explain it. Even if every tie counted as a win, the best pair wins 47%,
  below the 50% it needs before costs.
- **Sensitivity** (1 pip = 1 USD, TP 15 / SL 12): about break-even (+1.1R per 100, PF 1.03).

To finish the pre-registered test: set MT5's Tools → Options → Charts → "Max. bars in chart" to Unlimited, then run
`python scripts/mt5_export.py --m1 XAUUSD --start 2024-10-01` and `python scripts/gold_scalp_study.py`.

## Final result (2026-10-20): the pre-registered two years, all 9 pairs FAIL

MT5's bar cap was raised to 5,000,000. The full 1-minute XAUUSD history (2024-09-30 → 2026-10-20, about 726,000 bars,
already in UTC) was then exported and the same code re-run.
- **All 9 TP/SL pairs fail every check:** −15.2 to −30.6R per 100 trades in year 2 (about 1,285 trades each, PF
  0.54–0.76).
- **Year 1 agrees.**
- The preliminary 3-month result is confirmed: the strategy loses even on the price move alone.
