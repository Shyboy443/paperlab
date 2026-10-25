# Scribd round 4: high-return momentum and rotation systems (pre-registered 2026-10-20)

**Operator, after round 3 (ibs_qqq passed):** "i want highest return results keep going". This round looks for the
documents that promise the highest returns: monthly ETF rotation and momentum systems. Every rule's **compounded
annual return (CAGR) and maximum drawdown** are reported next to the pass/fail verdict, with buy-and-hold SPY over
the same months.

## Sources read (operator's logged-in Scribd)

**Searches:** "dual momentum ETF rotation strategy", "TQQQ leveraged ETF strategy", "connors double seven cumulative
rsi", "crypto trend following strategy backtest results", "turn of the month trading strategy backtest", "overnight
trading strategy SPY close to open". About 15 documents were opened; the rules were written down before any data was
loaded.

**Not tested:**
- **Discretionary:** TQQQ Swing Trading Strategy (631793561), which uses IBD's distribution days and judgement.
- **Instrument closed:** Maximum Yield Rotation (250728701). It is driven by ZIV, an inverse-VIX note closed in 2020,
  and its ranking formula is given elsewhere.
- **Unreadable text layer:** Enhanced Dual Momentum quarterly (706328485).
- **Books (> 40 pages):**
  - Buy The Fear Sell The Greed, Connors (744799952), 215 p;
  - There Is Magic in RSI (512359031), 103 p;
  - 10 Most Profitable Trading Strategies (820178565), 61 p, a Kindle book.

## The rules tested (`app/ytstrats/round4.py`)

| Key | Document | Markets named | Rebalance |
|---|---|---|---|
| `gmr_diy` | A Global Market Rotation Strategy With An Annual Performance Of 41.4% Since 2003, Grossmann (399509251) | MDY, IEV, EEM, ILF, EPP, EDV | monthly |
| `bond_rotation_diy` | The Sleep Well Bond Rotation Strategy, Grossmann (250728700) | AGG, BOND, CWB, JNK, TLH | monthly |
| `gem` | Gary Antonacci's Momentum Trend Research, Global Equities Momentum (1071527952) | S&P 500, MSCI ACWI ex-US, US aggregate bonds, T-bills → SPY, VEU, AGG, BIL | monthly |
| `alvarez_trend_mom` | Trend-Following Plus Momentum in ETFs, Alvarez Quant Trading (439780519; font-scrambled, decoded) | AGG, DBC, EEM, EFA, GLD, HYG, IWM, LQD, QQQ, SPY, TLT, VNQ | monthly |

### Exact rules (fixed now)

**Shared mechanics:**
- Signals use adjusted closes on the last trading day of each month.
- The trade is made at the next trading day's open and held until the next month's first open.
- One "trade" = one month's holding-period return.

**The rules:**
- **`gmr_diy`:** hold the one ETF of the six with the best 3-month return. This is the author's own
  "do-it-yourself" version, which he says gives about 34% a year. The full version's volatility weighting is
  ambiguous and not tested.
- **`bond_rotation_diy`:** hold the one bond ETF of the five with the best 3-month return, the author's
  "do-it-yourself" version.
- **`gem`:**
  1. Compare SPY's and VEU's 12-month total returns.
  2. If the winner's 12-month return beats BIL's (T-bills), hold the winner.
  3. Otherwise hold AGG.
- **`alvarez_trend_mom`:** each of the 12 ETFs separately, at each month end.
  - **Trend rule:** close > its 10-month simple average of month-end closes.
  - **Momentum rule:** 10-month return > 0.
  - **Weight:** both true → 100%, one true → 50%, neither → 0%.
  - Rebalanced at the next open. A month with 0% weight is not a trade.
  - The portfolio's CAGR assumes 1/12 of the capital per ETF.

## Data, costs and windows (fixed now)

- **Data:** Yahoo Finance daily, adjusted for splits and dividends.
- **Costs:** 0.05% of the traded amount per side, charged on every switch or weight change. Holding the same ETF
  costs nothing.
- **Windows** split each rule's history at its publication date where the data allows:

  | Rule | A | B |
  |---|---|---|
  | `gmr_diy` | 2008-04 → 2013-09 (EDV starts 2007-12; article Aug 2013) | 2013-09 → 2026-10 |
  | `bond_rotation_diy` | 2012-07 → 2019-07 (BOND starts 2012-03) | 2019-07 → 2026-10 (halves) |
  | `gem` | 2008-06 → 2015-01 (VEU/BIL start 2007; Antonacci's book 2014) | 2015-01 → 2026-10 |
  | `alvarez_trend_mom` | 2007-01 → 2019-11 (the article's own period) | 2019-11 → 2026-10 |

## Decision rule (fixed now)

Month returns in % after costs. A rule **passes** only if all of these hold:

1. Window A: mean > 0 and profit factor ≥ 1.10.
2. Window B: mean > 0, profit factor ≥ 1.15 and at least 50 traded months.
3. Window B: the lower end of the bootstrapped 95% interval of the mean month is above 0.
4. Window B: positive on at least half of its markets. For the rotation rules this is the strategy itself; for Alvarez,
   per ETF.

**Reported next to the verdict** (the operator wants the highest returns):
- CAGR and maximum drawdown per window;
- buy-and-hold SPY over the same months.

**Also reported, not part of any verdict:** round 3's `ibs_qqq` signals traded in TQQQ (3× QQQ, from 2010), to show
what leverage does to its return and drawdown.

**Multiple testing:** 4 rules. A pass is a candidate for a forward paper test, not live money. Nothing here is
changed after the results are seen.

## Result (run 2026-10-20): the "highest return" systems collapse after publication

`app/ytstrats/round4_results.json`, served at `GET /api/public/scribd-round4`. All figures are after costs.

| Rule | A: months / mean / PF / CAGR / max DD | B: months / mean / PF / CAGR / max DD | SPY in B | Verdict |
|---|---|---|---|---|
| GMR do-it-yourself (claims 41% a year) | 64 / +2.55% / 2.24 / **+29.7%** / 22% | 157 / +0.40% / 1.21 / **+2.9%** / 43% | +14.4% | FAIL (B CI) |
| Bond rotation do-it-yourself | 84 / +0.56% / 2.10 / +6.7% / 12% | 87 / +0.51% / 1.44 / +5.5% / 32% | +15.7% | FAIL (B CI) |
| Antonacci GEM | 79 / +0.89% / 1.89 / +10.3% / 19% | 141 / +0.66% / 1.57 / +7.2% / 23% | +13.7% | FAIL (B CI) |
| Alvarez trend + momentum (12 ETFs) | 1,275 / +0.45% / 1.41 / +4.0% / 8.8% | 770 / +0.71% / 1.68 / +6.6% / 7.5% | +16.0% | PASS: low return, low drawdown |

**Reading.**
- **GMR** compounded 29.7% a year before its article (2008–2013) and 2.9% a year after (2013–2026), with a 43%
  drawdown. It did far worse than simply holding SPY. The "41% a year" was a backtest that did not survive.
- **GEM and bond rotation** stay positive but lag SPY after publication, and their monthly means are not
  significantly above zero.
- **Alvarez** passes the fixed test as a low-risk overlay: 6.6% a year with a 7.5% maximum drawdown since 2019. It is
  not a high-return strategy.

## Leverage sensitivity of round 3's `ibs_qqq` (not a verdict)

The same QQQ signals (IBS < 0.10 → buy the close; exit at the first close above the previous high), with the trades
taken in TQQQ (3× QQQ), 2010-03 → 2026-10:

| | Rule in TQQQ | TQQQ buy and hold | Rule in QQQ | QQQ buy and hold |
|---|---|---|---|---|
| CAGR 2010–2018 | +26.2% | +50.5% | +9.7% | +18.5% |
| CAGR 2018–2026 | **+47.8%** | +33.5% | +17.6% | +19.7% |
| CAGR 2010–2026 | **+37.6%** | +41.3% | +13.9% | +19.2% |
| Max drawdown | 35% (at trade closes) | **82%** | 12% | 35% |
| Time in market | ~29% | 100% | ~29% | 100% |

- The worst single TQQQ trade lost 26%.
- Holding TQQQ outright made slightly more over the whole period but lost 82% at its worst. The rule made about the
  same with less than half that drawdown, and beat it after 2018.
- This uses 3× leverage on a rule that passed only on QQQ. Its risk is the next big gap down while holding: the
  worst QQQ trade was −22%, which is −50% or more in TQQQ.
