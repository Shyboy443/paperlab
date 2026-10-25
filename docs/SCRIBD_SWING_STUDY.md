# Scribd round 2: swing, trend and session-breakout strategies (pre-registered 2026-10-20)

**Why a second round:** the operator said "if no winner still then keep going to the Scribd and research a new
strategy". Round 1 (docs/SCRIBD_SCALPING_STUDY.md) found that every 1–15 minute indicator scalp is at zero before
costs. This round looks at slower, well-known mechanical systems, over about ten years of history instead of two.

## Sources read (operator's logged-in Scribd)

Searched: "turtle trading rules", "swing trading strategy", "rsi 2 strategy connors", "london breakout strategy",
"mean reversion trading strategy", "holy grail raschke adx", "inside bar breakout strategy daily", "trend following
system rules moving average", "opening range breakout strategy". About 30 documents were opened.

The rules were written down in my own words in a working file outside the repo, **before any test**. Not tested:
- **Discretionary:**
  - London Open Breakout (335334759): directional bias and the pullback are judged by eye.
  - Rise and Fall (765110659): binary options, judged by eye.
  - Top 3 SMA strategies (475741308).
- **No exit given and no opposite signal to use:**
  - Galen Woods' two-step RSI-2 (279414915).
  - The inside-bar article (512106570).
- **Says itself it is incomplete:** Forex Opening Range Breakout (655539928).
- **Under-specified or needs data we don't have:**
  - The RSI-2 stock portfolio slides (805313411): many unnamed variants.
  - S&P 1500 weekly trend system (475740765): needs a survivorship-free S&P 1500 history.
- **Link lists with no rules:** 907585904 and 763659243 (QuantifiedStrategies indexes).
- **Long paid manuals or books:** skipped, as in round 1 (London Forex Rush 270967136, The Complete Breakout Trader
  370404372).

## The rules tested (`app/ytstrats/swing.py`; each docstring is the rule as tested)

| Key | Document | Market the document names → tested on | Bars |
|---|---|---|---|
| `turtle_s1` | Original Turtle Trading Rules (466279786), System 1 | diversified liquid futures → every MT5 market below | daily |
| `turtle_s2` | the same, System 2 | the same | daily |
| `rsi2_sp500` | S&P 500 Trading with 2-Period RSI (439781936) | S&P 500 → US500 | daily |
| `r2_improved` | The Improved R2 Strategy, Connors (100141147) | S&P 500 → US500 | daily |
| `rsi2_15_85` | RSI Trading Strategy 91% Win Rate, first rule (896242411) | SPY → US500 | daily |
| `rsi2_rsi30` | the same, RSI(30) > 50 version (AFL code given) | SPY → US500 | daily |
| `holy_grail` | Holy Grail Trade, LBR (596991201), rules from the slides | EUR example, pips → the 7 FX majors | daily |
| `ma_breakout` | Moving Average Breakout (683919166) | stocks (10-cent filter) → the 30 Dow stocks | daily |
| `triple_ma` | The Triple Moving Average System (410843959) | stocks → the 30 Dow stocks | daily |
| `london_bo` | London Breakout Strategy (427438395) | "GBP pairs" → every GBP pair the broker lists | 5m |
| `london_breakfast` | London Free Breakfast (423041768) | GBPUSD ("the cable") | 15m |
| `big_ben_fade` | Big Ben Breakout Strategy (450251566) | GBPUSD, GBPJPY, EURGBP, EURUSD, USDJPY, AUDUSD | 5m |
| `pips20_daily` | 20 Pips Daily Forex Breakout (442430072; font-scrambled, decoded) | "any currency pair" → 7 FX majors | 5m |

### Exact rules (fixed now)

- **Turtle (both systems).**
  - N = 20-day Wilder ATR, seeded with the 20-day average true range.
  - Entry: intraday at the 20-day (System 1) or 55-day (System 2) high/low of the PREVIOUS days. If the market opens
    beyond the level, the fill is at the open.
  - Units: add one unit every ½ N beyond the last fill, up to 4. On each add, every unit's stop moves to 2 N from the
    newest fill.
  - Stop: 2 N from entry.
  - Exit: the 10-day (System 1) or 20-day (System 2) opposite breakout, intraday.
  - System 1 skips a 20-day breakout when the last 20-day breakout, taken or not, was a winner.
    - A winner means the 1-unit, 2 N-stop, 10-day-exit trade from it ended in profit.
    - A skipped breakout is still entered at the 55-day breakout (the failsafe).
  - Inside a daily bar, the path is the neutral one: green O→L→H→C, red O→H→L→C. The first level reached on it acts
    first.
  - Not modelled:
    - the portfolio unit caps (correlated groups, 12 per direction);
    - the 20%-per-10%-drawdown notional cut;
    - the "Whipsaw" variant.
  - Each market is a separate book. One trade = one position with all its units. The result per trade is the sum
    over its units of the move as a % of the first fill.
- **`rsi2_sp500`.** Close > SMA200 and RSI(2) < 10 → buy. Exit when close > SMA10. No stop (the document uses none).
- **`r2_improved`.** All of these must hold, then buy on day 3:
  - close > SMA200;
  - day 1 RSI(2) < 65;
  - RSI(2) falls on day 2 and again on day 3.

  Exit when RSI(2) > 75. No stop.
- **`rsi2_15_85`.** RSI(2) crosses below 15 → buy. Exit when RSI(2) > 85. No stop.
- **`rsi2_rsi30`.** RSI(30) > 50 and RSI(2) < 15 → buy. Exit when RSI(2) > 85. No stop.
- The documents say "buy/sell at the close". The engine reads the close and fills at the next bar's open. For a
  24-hour CFD that is the same price minutes later.
- **`holy_grail`.**
  - Setup:
    - ADX(14) > 30 and below the previous day's ADX ("downturn in ADX");
    - the trend is the side with the larger DMI (+DI > −DI = up);
    - the bar's range touches the 20 EMA.
  - Long entry: buy stop 3 pips above the 2-event high (the high of the touching bar and the bar before it).
    - It is valid for the next bar and re-armed every bar while the setup holds.
  - Stop: 3 pips below the 2-event low.
  - Target 1: the highest high of the 20 bars before the touching bar (the previous trend high).
  - The trade is closed in full at Target 1. Target 2 (Fibonacci, optional) and the discretionary pattern trigger are
    not used.
  - Shorts are the mirror image.
- **`ma_breakout`.**
  - Long setup:
    - the close crosses above SMA20;
    - then the first two bars whose lows are above SMA20 (the crossing bar counts if its low is above). A close back
      below SMA20 cancels the setup.
  - Entry: buy stop at the higher high of those two bars + 0.10 USD. It stays armed while the close stays above SMA20.
  - Short: the mirror image (two highs below SMA20, sell stop 0.10 USD under the lower low).
  - No exit is given, so the opposite signal closes the trade and reverses it (the round-1 convention).
- **`triple_ma`** (long only; "sell" on a stock means exit). Uses the 5/10/20-day SMAs.
  - Buy on a bar where 5 > 10 > 20 are aligned and SMA50 is not declining (SMA50 today ≥ yesterday).
  - Exit when 5 < 10 < 20 (the confirmed sell with the 5-day filter).
- **`london_bo`.**
  - Asian range: 00:00 → 08:00 London local time.
  - At 08:00 London: buy stop at the range high + 5 pips and sell stop at the range low − 5 pips. The first fill
    cancels the other.
  - Orders are live until 16:00 London.
  - Stop 20 pips, trailing 20 pips behind the best price. Target 40 pips. The "open or 40, judgement call" case is
    taken as 40.
  - Early exit: two consecutive H1 candles against the trade close after the entry → exit at the next open.
  - Skip the day if the Asian range is > 100 pips, except on GBPUSD and GBPJPY.
  - Any trade still open is closed at 21:00 UTC (a bound the document doesn't give).
- **`london_breakfast`.**
  - Asian box: 00:00 → 08:00 London.
  - From 08:00 to 16:00 London, the FIRST 15m close beyond the box gives the trade, in that direction, at the next
    open.
  - Stop: the breakout candle's other extreme.
  - Target: 40 pips, the midpoint of the document's 30–50. 30 and 50 are reported as sensitivity, not as the
    verdict.
  - One trade a day, closed at 21:00 UTC if still open.
- **`big_ben_fade`.**
  - Range: the candle BODIES (open/close) from 00:00 to 07:00 London.
  - Pre-open breakout: the first 5m close beyond the body range between 07:00 and 08:00 London.
  - Fade: between 08:00 and 09:00 London, the first 5m close back inside the range → enter against the breakout at
    the next open.
  - Target: the range size projected from the far side of the range.
  - Time stop: at 09:00 London, exit unless the price has traded back to the 07:00 London price (where the pre-open
    move began).
  - There is no price stop (the document uses the time stop instead). The trade is closed at 21:00 UTC if still
    open.
- **`pips20_daily`.**
  - The daily candle is the broker's day (New York close).
  - Buy stop 3 pips above the previous day's high and sell stop 3 pips below its low. The document's "<->" decodes
    to "2-3"; the larger value is used.
  - Stop 20 pips, target 20 pips.
  - An order touched during the Asian session (before 07:00 London) is not taken that day: the document advises
    against Asian-session breakouts.
  - Each order can fill once a day, one position at a time. A trade still open when the broker day ends is closed
    at that day's last close.

## Data, costs and windows (fixed now)

- **MT5 markets:** the operator's MT5 (Fusion Markets, read-only), converted to UTC.
  - D1 since 2015-01-01 for the 7 majors, AUDJPY, GBPJPY, XAUUSD, XAGUSD, US30, US500, NAS100, UK100 and GER40.
  - 1-minute candles since 2015 for the session rules, as far back as the broker's history goes.
    - The London breakout and 20-pips rules are walked on 1-minute candles.
    - Big Ben is built from 5-minute candles and the London Breakfast from 15-minute candles.
- **Stocks:** daily Yahoo Finance data for the 30 current Dow Jones Industrial Average members, 2015 → 2026.
  - **This list has survivorship bias:** today's members are by construction stocks that did well. That flatters
    long-only rules. A stock pass would therefore need a survivorship-free re-test before it counts.
- **Costs per side, as a share of the price:**
  - MT5 FX and metals: half the mean recorded spread + 0.002% slippage + 0.0025% commission.
  - MT5 indices: half the mean spread + 0.002% slippage (no commission).
  - Stocks: 0.03% (commission + slippage).
  - **Not modelled:** the overnight financing (swap) on CFDs. It makes the multi-day FX/index results slightly
    optimistic.
- **Windows:** A = 2015-01-01 → 2021-01-01, B = 2021-01-01 → 2026-10-20. Where a market's data starts later, A is
  simply shorter.
  - **Data-forced change for the four session rules:** the broker's 1-minute history turned out to start only in
    October 2020; before that MT5 holds placeholder candles. The rule above would have left window A with two months.
    The session rules therefore split the six years in halves: A = 2020-11-01 → 2023-11-01, B = 2023-11-01 →
    2026-10-20. This change was made after the London Breakfast's first run was seen. That rule loses on every
    sub-period of 2021–2026 and even before costs, so the split cannot flip it. No other session result had been
    seen.

## Random-walk check (run before any real data)

`scripts/scribd_swing_study.py`'s simulators were run with zero costs on driftless random prices. The expectation
is a mean of zero per trade.
- **The first draft had a bug.** After a stop-entry fill, the rest of that candle skipped the candle's extreme. It is
  fixed.
- **With a near-continuous random walk** (an arithmetic price martingale, 400 steps per daily candle): every rule is
  within |z| < 1.7. Examples: Turtle 1 z −1.0, Turtle 2 −1.3, MA breakout −1.1, the RSI-2 rules −0.4 to +0.4.
- **With coarse test candles,** stop entries and stops showed small positive values (z up to +5). These were caused
  by the test generator's own jumps, and they vanish as it gets finer.
- **London breakout on 5-minute candles** kept +2 pips of bias. Its 20-pip stops, trail and entries fall inside one
  candle too often. So the London breakout and 20-pips rules use 1-minute candles. This was decided here, before any
  real result.
  - On 1-minute random walks: London breakout z +1.6, 20-pips −0.1.
  - Breakfast (15m) +1.2, Big Ben (5m) +1.1.

## Decision rule (fixed now): returns per trade in % of price, all costs

A rule **passes** only if all of these hold:

1. **Window A:** mean > 0 and profit factor ≥ 1.10.
2. **Window B:** mean > 0, profit factor ≥ 1.15 and enough trades.
   - At least 100 trades.
   - The exception is a rule whose document names a single market (the four S&P 500 RSI-2 rules, the London
     Breakfast): at least 30.
3. **Window B:** the lower end of the bootstrapped 95% interval of the mean is above 0.
4. **Window B:** positive on at least half of the markets it trades.

**Multiple testing:** 13 rules are tested, so one could pass by chance. A pass makes a rule only a candidate for a
**forward paper test**, never a live bot directly. Buy-and-hold over the same days is reported next to the
long-only S&P rules, because a long-only rule in a rising market can look good just by being in it. Nothing here is
changed after the results are seen.

## Result (run 2026-10-20): 1 of 13 passes the fixed rule, and it is not a consistent winner

`app/ytstrats/swing_results.json`, served at `GET /api/public/scribd-swing` and shown as the "Scribd swing & trend" tab
of the Strategy check page. Per-trade results are % of price after costs. A and B are the windows above.

| Rule | A: trades / mean / PF | B: trades / mean / PF | B before costs | Verdict |
|---|---|---|---|---|
| Turtle System 1 (16 markets) | 1,015 / −0.21% / 0.93 | 1,003 / −0.60% / 0.80 | −0.56% | FAIL |
| Turtle System 2 | 620 / −0.17% / 0.95 | 644 / −1.25% / 0.68 | −1.21% | FAIL |
| S&P RSI-2 < 10, exit > SMA10 | 41 / −0.25% / 0.74 | 53 / +0.82% / 3.49 | +0.82% | FAIL (window A) |
| Connors Improved R2 | 48 / −0.20% / 0.75 | 53 / +0.68% / 3.12 | +0.69% | FAIL (window A) |
| **RSI(2) crosses < 15, exit > 85** | 72 / +0.11% / 1.14 | 79 / +1.08% / 3.57 | +1.09% | **PASS** |
| RSI(30) > 50 & RSI(2) < 15 | 46 / −0.11% / 0.88 | 58 / +1.05% / 3.94 | +1.06% | FAIL (window A) |
| Holy Grail (7 FX majors, daily) | 78 / −0.26% / 0.66 | 77 / +0.00% / 1.01 | +0.02% | FAIL |
| MA breakout (Dow 30) | 1,532 / +0.07% / 1.02 | 1,614 / −0.23% / 0.93 | −0.17% | FAIL |
| Triple MA (Dow 30, long only) | 888 / +1.46% / 1.75 | 818 / +1.02% / 1.40 | +1.08% | PASS, not counted (survivorship) |
| London breakout (7 GBP pairs, 1m) | 1,951 / −0.012% / 0.80 | 1,945 / −0.023% / 0.62 | −0.008% | FAIL |
| London Free Breakfast (GBPUSD, 15m) | 298 / −0.022% / 0.76 | 311 / −0.026% / 0.66 | −0.016% | FAIL |
| Big Ben fade (6 pairs, 5m) | 722 / −0.015% / 0.89 | 705 / −0.012% / 0.89 | −0.001% | FAIL |
| 20 Pips Daily (7 majors, 1m) | 2,883 / −0.008% / 0.92 | 2,566 / −0.003% / 0.97 | +0.008% | FAIL |

**Read with the formal pass: extra checks, not part of the verdict.**
- **Timing.** The rule's trades were compared with random entry days that have the same holding times, on US500.
  - 2021–2026: the RSI-2 rules beat random timing by +0.4 to +0.75% per trade (z 1.9–3.2).
  - 2015–2020: all four RSI-2 rules did WORSE than random timing (−0.16 to −0.47% per trade).
  - The edge exists in one half of the decade only.
- **Overnight financing (swap).** The broker charges 6.14% a year on a long US500 CFD (MT5 symbol info, 2026-10-20);
  the pre-registration left swaps out.
  - Charged per night held, it turns `rsi2_15_85`'s window A negative: −0.074% per trade, PF 0.92.
  - Window B stays positive: +0.89%, PF 2.79.
- **Triple MA on Dow stocks** did worse than random entry days in both windows (−0.50% per trade). Its profit is the
  stocks' own rise, and the list has survivorship bias.
- **Multiple testing.** 13 rules were tested, so one formal pass is about what chance produces.

**Conclusion.**
- No rule here is a consistent winner.
- The S&P 500 RSI-2 dip-buy (`rsi2_15_85`) is the only candidate. It worked strongly in 2021–2026. It did not beat
  random timing in 2015–2020, and with the broker's financing it lost there. At most it earns a forward paper test.
- Trend following (Turtles 1 and 2) lost on FX and indices and made money only on gold. The Holy Grail is at zero.
- The four London-session breakouts are at or below zero before costs and lose after them, the same pattern as the
  round-1 scalps.
