# Scribd round 3: NR7, IBS, intraday momentum and a Bitcoin thesis (pre-registered 2026-10-20)

**Operator, after round 2 (1 of 13 passed, not consistently):** "keep going, research more strategies on scribd".

## Sources read (operator's logged-in Scribd)

**Searches:** "larry connors short term trading strategies", "larry williams volatility breakout", "NR7 narrow range
strategy", "bitcoin trading strategy backtest", "internal bar strength IBS strategy", "gold swing trading strategy
daily chart", "ichimoku trading strategy rules". About 20 documents were opened. The rules were written down in a
working file outside the repo before any data was loaded.

**Not tested:**
- **Link indexes:** 100 Free Trading Strategies (857092740).
- **A paid manual:** Connors' Bollinger Bands %b Swing System (58880653). It is 49 pages and "all rights reserved",
  skipped as in earlier rounds.
- **No parameters:** Bernstein's volatility-breakout slides (250566543) and LBR's volatility-breakout essay
  (238177650).
- **Discretionary / order flow:** the 15-minute ES ORB by Betteridge (899267861), which uses day-type judgement and
  cumulative delta.
- **Partial notes:** Larry Williams seminar notes (428042198), which refer to pages of a manual.
- **Formulas lost in the scan:** the Ichimoku back-test (409877311), whose entry inequalities are unreadable.
- **Scrambled beyond recovery:**
  - The gold strategies guide (521977383) is also discretionary.
  - The RSI(3) + IBS rule inside 343659347: its comparison signs can't be decoded consistently.
- **Undefined trailing stop:** the ".Pride." NR7 stop-and-reverse post (inside 382606844).

## The rules tested (`app/ytstrats/round3.py`; each docstring is the rule as tested)

| Key | Document | Market named → tested on | Bars |
|---|---|---|---|
| `nr7_forex` | NR7 Forex Trading Strategy (428753972) | "any" pair → 7 FX majors | daily, MT5 2015 → 2026 |
| `nr7_etf` | NR4 and NR7 Trading Strategies, StockCharts (418172887) | "stocks, indices and ETFs" → SPY, QQQ, DIA, IWM | daily, Yahoo 2000 → 2026 |
| `nr7_nifty` | Trading NR7 Setup, AW10 draft system (382606844) | NIFTY → Nifty 50 index (^NSEI) | daily, Yahoo 2007 → 2026 |
| `intraday_momentum` | Beat the Market, Zarattini/Aziz/Barbon (861126103) | SPY → US500 CFD | 1-minute, MT5 2020-11 → 2026-10 |
| `btc_new_long` | Backtesting of Trading Strategies for Bitcoin, ETH thesis (435132310), "New Long" | BTC/USD → Binance BTCUSDT | 1h, 2019-06 → 2026-10 |
| `btc_nls2` | the same thesis, final "New Long-Short 2" | the same | 1h |
| `ibs_spy_ews` | The Internal Bar Strength Indicator, Kinlay (343659347) | SPY and EWS | daily, Yahoo 2000 → 2026 |
| `ibs_qqq` | the same document, Llewelyn James' rule | the ETF in its worked example → QQQ | daily, Yahoo 2000 → 2026 |

**`ibs_qqq`:** the font scramble hid the ticker. The example's prices for 2016-01-13 (high 106.23, low 101.74, close
101.90) match QQQ exactly, and no other candidate matches.

### Exact rules (fixed now)

**NR7, the narrowest range of the last 7 bars (the bar itself included), is shared by the three NR7 rules below.**

- **`nr7_forex`:**
  - The bar after the NR7 bar carries a buy stop 2 pips above the NR7 high and a sell stop 2 pips below its low. The
    first one reached fills; the orders live for that bar only.
  - Stop: 2 pips beyond the other side. Target: 3 R.
  - The document's "near support / Fibonacci / action zone" filter is discretionary and dropped.
- **`nr7_etf`:**
  - On the day after the NR7 day, buy above its high or sell below its low (stop entries, that day only, the first
    level reached).
  - Stop: the other side of the NR7 day.
  - Exit at the first profitable close, the entry day included (Crabel's exit, as the article states).
- **`nr7_nifty`:**
  - On Day 1, buy above the Day 0 high or sell below the Day 0 low (stop entries, the first reached).
  - Initial stop: the other side of Day 0.
  - Trailing stop, as the draft's own example has it: on day k the stop is the low (long) or high (short) of day
    k−2. It only moves in the trade's favour.
  - A gap through the stop exits at the open. No target.
- **`intraday_momentum`** (the paper's "Current band + VWAP" model):
  - US regular session, 09:30–16:00 New York time.
  - σ(HH:MM) is the mean, over the previous 14 days, of |close at HH:MM ÷ the 09:30 open − 1|.
  - Upper band = max(today's open, yesterday's 16:00 close) × (1 + σ). Lower band = min(...) × (1 − σ).
  - Decisions only at HH:00 and HH:30, 10:00 → 15:30:
    - above the upper band → long;
    - below the lower band → short.
  - Trailing stop, checked at the same times:
    - a long exits below max(upper band, VWAP);
    - a short exits above min(lower band, VWAP).
    - After an exit, the opposite side may open at the same check.
  - Everything is closed at 16:00. Full size (the volatility sizing doesn't change any trade's sign).
  - VWAP uses the CFD's tick volume, an approximation of SPY's volume.
- **`btc_new_long`** (thesis "New Long", 1-hour candles):
  - **Indicators:**
    - Bollinger(20, 2);
    - "vol condition" = SMA10(volume) > SMA50(volume);
    - BBW = (upper − lower) / middle;
    - VLI fast = SMA200(BBW), VLI slow = SMA1000(BBW), VLI top = SMA1000(BBW) + 2·STD1000(BBW);
    - "low volatility level" = VLI fast < VLI slow.
  - **Entry:** a close crossing DOWN through the upper band, with the vol condition and close > SMA20. Then:
    - if BBW < VLI top: in a low volatility level, long if SMA50 > SMA200. Otherwise (a high level), long unless
      SMA200 > SMA100 > SMA50.
    - if BBW ≥ VLI top: long if SMA100 > SMA200. That trade's stop is the low of the signal candle, and a +1% stop is
      added once the profit is over 3%.
  - **Exit:**
    - a close crossing down through the lower band, with the vol condition;
    - a stop 5% below the entry candle's low;
    - stop-wins: once the close-to-close profit exceeds 20/25/30/35/40%, a stop at +15/20/25/30/35%.
  - The thesis fills at the signal candle's close. Here the fill is the next candle's open, minutes later.
- **`btc_nls2`** (the thesis' final strategy): `btc_new_long` plus "New Short 2".
  - **The short:**
    - A close crossing UP through the lower band, with BBW < VLI slow at that moment, arms a short.
    - It fires when SMA100 crosses below SMA50, if BBW < VLI top then. It is disarmed by an upper-band cross-down.
  - **Short stops:**
    - the highest high of the last 20 candles; of the last 10 once the profit is over 10%; of the last 5 over 15%;
    - stop-wins at +20/25/30/35% after 25/30/35/40% profit.
  - One position at a time. An opposite entry closes and reverses.
- **`ibs_spy_ews`:**
  - IBS = (close − low) / (high − low).
  - IBS < 0.2 → buy at the close; IBS > 0.8 → sell short at the close. Closed at the next close.
  - Each signal day is its own one-day trade.
- **`ibs_qqq`:** IBS < 0.10 → buy at the close. Exit at the first close above the previous day's high. Long only.

## Data, costs and windows (fixed now)

- **Daily ETFs and Nifty:** Yahoo Finance, split- and dividend-adjusted, as in round 2's stock data.
- **US500 1-minute:** the operator's MT5 (Fusion Markets), converted to UTC; New York time via US daylight saving.
- **BTC:** Binance spot BTCUSDT 1-hour candles (public data).
- **Costs per side** (a share of the price):

  | Market | Per side |
  |---|---|
  | SPY, QQQ, DIA, IWM | 0.01% |
  | EWS | 0.05% |
  | Nifty | 0.03% |
  | FX majors | the MT5 cost of round 2: half spread + 0.002% + 0.0025% |
  | US500 | half spread + 0.002% |
  | BTC | 0.07% (0.05% fee + 0.02% slippage, as for crypto in rounds 1–2) |

  Overnight financing is not modelled. It matters only for the multi-day FX and CFD holds, and US500 here is closed
  every day.
- **Windows:**

  | Data | A | B |
  |---|---|---|
  | Yahoo daily | 2000-01-01 → 2013-01-01 | 2013-01-01 → 2026-10-21 |
  | MT5 daily FX | 2015 → 2021 | 2021 → 2026-10-21 |
  | US500 1-minute | 2020-11-01 → 2023-11-01 | 2023-11-01 → 2026-10-21 |
  | BTC 1-hour | 2019-06-01 → 2023-01-01 | 2023-01-01 → 2026-10-21 |

  - Nifty data starts in 2007, so its window A is shorter.
  - BTC's two windows are both out-of-sample for the thesis, whose data ends in May 2019.

## Decision rule (fixed now): per-trade returns in % of price, all costs

A rule **passes** only if all of these hold:

1. Window A: mean > 0 and profit factor ≥ 1.10.
2. Window B: mean > 0, profit factor ≥ 1.15 and at least 100 trades. The minimum is 30 for a rule whose document
   names a single market: `nr7_nifty`, `intraday_momentum`, both BTC rules and `ibs_qqq`.
3. Window B: the lower end of the bootstrapped 95% interval of the mean is above 0.
4. Window B: positive on at least half of the markets it trades.

**Reported but not part of the verdict:**
- the result before costs;
- buy-and-hold over the same windows for the long-biased rules;
- a random-timing comparison for the long-only daily rules.

**Multiple testing:** with 8 rules, a pass makes a rule only a candidate for a **forward paper test**. A random-walk
check of every simulator is run before any real data. Nothing here is changed after the results are seen.

## Random-walk check (run before any real data)

All 8 simulators were run with zero cost on a driftless random price martingale.
- With 2,000 steps per daily candle, every daily rule is within |z| < 1.9. NR7 rules: −0.3 to −0.4.
- The 1-minute momentum model reads z −0.7; the Bitcoin rules +0.7 and +1.1.
- Coarser test candles (200 steps) gave the stop-entry NR7 rules z +3.8 to +7.8. This comes from the generator's own
  jumps past a level, worth about 0.02% per trade, and it vanishes as the generator gets finer. It is the same lesson
  as round 2.

## Result (run 2026-10-20): 1 of 8 passes, and it survives every extra check

`app/ytstrats/round3_results.json`, served at `GET /api/public/scribd-round3`. Per-trade results are % of price after
costs.

| Rule | A: trades / mean / PF | B: trades / mean / PF | B before costs | Verdict |
|---|---|---|---|---|
| NR7 forex (7 majors) | 989 / +0.020% / 1.05 | 998 / −0.045% / 0.89 | −0.035% | FAIL |
| NR7 index ETFs | 1,746 / +0.033% / 1.07 | 1,916 / −0.017% / 0.95 | +0.003% | FAIL |
| NR7 NIFTY (AW10) | 134 / +0.41% / 1.56 | 364 / +0.06% / 1.12 | +0.12% | FAIL (B: PF < 1.15, CI) |
| SPY intraday momentum → US500 | 663 / +0.045% / 1.33 | 724 / −0.003% / 0.97 | +0.006% | FAIL (after publication) |
| BTC thesis New Long | 140 / +0.06% / 1.03 | 136 / +0.58% / 1.48 | +0.72% | FAIL (window A) |
| BTC thesis New Long-Short 2 | 171 / −0.05% / 0.97 | 191 / +0.34% / 1.32 | +0.48% | FAIL (window A) |
| IBS one-day, SPY + EWS | 3,081 / +0.20% / 1.41 | 3,363 / −0.017% / 0.96 | +0.04% | FAIL (gone after 2013) |
| **IBS < 0.10, QQQ (Llewelyn James)** | **259 / +0.71% / 1.80** | **242 / +0.85% / 2.88** | +0.87% | **PASS** |

**`ibs_qqq`: extra checks, not part of the verdict.**
- **Timing.** It beats random entry days with the same holding times in both windows.
  - 2000–2012: +0.67% per trade, z 3.1. QQQ itself fell 28% over that window.
  - 2013–2026: +0.55%, z 4.0.
- **After the article (2016-02 → 2026-10):** 188 trades, +0.95% per trade, PF 3.03. This is genuinely out-of-sample.
- **Other index ETFs:**

  | ETF | 2000–2012 PF | 2013–2026 PF |
  |---|---|---|
  | SPY | 1.89 | 2.75 |
  | DIA | 1.73 | 1.96 |
  | IWM | 1.36 | 2.01 |

- **The operator's broker (MT5 CFDs, 2015–2026):**
  - NAS100: +0.79% per trade, PF 2.37. With the 6.14% a year long financing: +0.69%, PF 2.12.
  - US500: PF 1.73, or 1.48 with financing.
- **Costs:** at 0.05% or 0.10% per side, window B stays at +0.77% / +0.67% per trade.
- **By year:** 22 of 27 years are positive (2000 → 2026). The losing years are 2001 (−20% summed), 2005, 2007 and 2018
  (each under 1%).
- **Risk:**
  - Long only, with no stop.
  - The worst trade is −22% and the mean hold 5.7 days, at most 27.
  - In the market 29% of the time.
  - Fully compounded since 2000 it multiplies capital ×39.7, with a 38.6% maximum drawdown.

**Reading.**
- `ibs_qqq` is the first rule across four rounds (YouTube, gold, Scribd scalping, Scribd swing) to pass the fixed
  test and every robustness check. It is a well-documented effect: short-term mean reversion in US equity index ETFs
  after a close near the day's low.
- It is still one pass among 8 rules this round. Per the pre-registration it earns a **forward paper test**, not
  live money.
- Several other documents show the familiar pattern of an edge that faded after it was published or tested:
  - the IBS one-day rule (PF 1.41 → 0.96 after 2013);
  - the SPY intraday-momentum paper (PF 1.33 in 2020–23, 0.97 since);
  - NR7 on NIFTY (1.56 → 1.12).
