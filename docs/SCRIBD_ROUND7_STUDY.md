# Scribd round 7: volatility ETFs, calendar seasonality and the lunar effect (pre-registered 2026-10-21)

**Operator:** "keep going research more strategies". This round covers families not tested before: inverse-volatility
systems (the highest claimed returns on Scribd), turn-of-the-month and pre-holiday seasonality, and the lunar-phase
effect. Each is tested on its own market, after its publication date.

## Sources read

**Searches:** "stock market seasonality trading strategy", "gap trading strategy rules", "pairs trading strategy
backtest", "VXX volatility ETF trading strategy". About 15 documents were opened. The rules were written down before
any data was loaded.

**Not tested:**
- **Needs VIX-futures term-structure history** (the daily price of the 1st and 2nd VIX futures since 2009), which
  free data does not provide:
  - The VXX Trading System / Trading the VXX, Len Yates (354198361, 217354772): McMillan's term-structure system and
    its proprietary YVI;
  - Grossmann's "contango rule" and "roll-yield futures" trades (inside 250728698).
- **Options course:** VXX long-put methodology (810405775).
- **Discretionary:**
  - Gap Down / Gap Up at the Open, Bear Bull Traders (691263953): level-2, VWAP judgement;
  - COT Weekly Trading Strategy (486807770): hand-drawn Fibonacci swing stops.
- **Too few trades:** Minton's year-end Russell 2000 window, one trade a year.
- **Image-only:** Definitive Guide to Pairs Trading (642354311).

## Rules tested (`app/ytstrats/round7.py`)

| Key | Document | Market named → tested on | Window A / B (B = after publication) |
|---|---|---|---|
| `grossmann_vxx_bb` | Strategies for Trading Inverse Volatility, Grossmann, Dec 2013 (250728698) | VXX → VIXY (same S&P 500 VIX short-term futures index; Yahoo's VXX history starts in 2018) | 2011-01 → 2014-01 / 2014-01 → 2026-10 |
| `grossmann_vxx_sma` | the same document, its "5 and 15-day SMA" variant | VIXY | the same |
| `fosback_seasonal` | The Amazing Story of Stock Market Seasonality, Minton (40367053), Fosback's Seasonality Indicator (1976) | S&P 500 → SPY | 1993-02 → 2010-01 / 2010-01 → 2026-10 |
| `kaeppel_monthly10` | the same document, Kaeppel's "Monthly 10" (2008) | Dow → DIA | 1998-01 → 2009-01 / 2009-01 → 2026-10 |
| `lunar_phase` | Are Investors Moonstruck? Yuan, Zheng and Zhu, 2001 (372211422) | 48 countries' indexes → the 31 country / US index ETFs of round 6 | ETF start → 2002-01 / 2002-01 → 2026-10 |

### Exact rules (fixed now)

- **`grossmann_vxx_bb`:** Bollinger(20, 1.4) on VIXY's close.
  - Close below the middle line (SMA20) → short at the next open.
  - Close above the upper band → cover at the next open.
  - The optional "upper band + 1%" stop is not used; the document calls it optional.
- **`grossmann_vxx_sma`:** SMA5 crosses below SMA15 → short at the next open. SMA5 crosses above SMA15 → cover at the
  next open.
- **`fosback_seasonal`:** hold SPY on the last trading day and the first four trading days of each month, and on the
  two trading days before each market holiday.
  - **Holidays** (Minton's list): Presidents' Day, Good Friday, Memorial Day, Independence Day, Labor Day,
    Thanksgiving, Christmas, New Year's Day. Computed by calendar rule (Saturday → Friday, Sunday → Monday), never
    from gaps in the data.
  - **Each run of consecutive holding days is one trade,** bought at the close before the first held day and sold at
    the close of the last held day.
- **`kaeppel_monthly10`:**
  - DIA is held on the last two and first four trading days of each month, plus trading days 9–12 of each month.
  - The document applies days 9–12 from 1980 on, which covers the whole test.
  - Trades are formed the same way as in `fosback_seasonal`.
- **`lunar_phase`:**
  - Each trading day belongs to the phase whose centre (new moon or full moon) is nearest. This gives the paper's two
    15-day windows.
  - **New-moon window → long. Full-moon window → short.** Each window is one trade, close to close.
  - New-moon times come from the mean synodic month (29.530589 days) counted from the new moon of 2000-01-06 18:14
    UTC. That is accurate to well under a day, which is enough for 15-day windows.

## Data, costs, windows

- **Data:** Yahoo daily, adjusted for splits and dividends.
- **Costs per side:**
  - SPY, DIA, QQQ, IWM: 0.01%;
  - country ETFs and VIXY: 0.05%.
  - The borrow fee for shorting VIXY is **not** modelled. That flatters the volatility rules.
- **Windows:** as in the table above.

## Decision rule (fixed now)

Per-trade returns in % after costs. A rule passes only if all of these hold:

1. Window A: mean > 0 and PF ≥ 1.10.
2. Window B: mean > 0, PF ≥ 1.15 and enough trades: ≥ 100, or ≥ 30 for the single-market rules (both VIXY rules,
   `fosback_seasonal`, `kaeppel_monthly10`).
3. Window B: the lower end of the bootstrapped 95% interval of the mean is above 0.
4. Window B: positive on at least half of its markets.

**Reported:**
- the compounded return (CAGR) and maximum drawdown in each window, next to buy-and-hold of the same ETF;
- for the seasonal rules, the share of time in the market.

A random-walk check runs before real data. A pass is a forward paper-test candidate. Nothing is changed after the
results are seen.

## Random-walk check (before real data)

Daily price martingale, 0.4% daily steps, 80 seeds:

| Rule | z |
|---|---|
| VXX Bollinger | +0.8 |
| VXX SMA | +0.2 |
| Fosback | +0.1 |
| Monthly 10 | −0.5 |
| Lunar | +0.6 |

A first draft with 1% daily steps let the random price drift towards zero, where percentage moves explode. That gave
Fosback z +2.3, an artefact of the test, not of the simulator.

## Result (run 2026-10-21): two formal passes, neither is a new winner

`app/ytstrats/round7_results.json`, served at `GET /api/public/scribd-round7`. Yahoo's cached SPY starts in 1999, so
Fosback's window A is 1999–2009.

| Rule | A: trades / mean / PF / CAGR | B: trades / mean / PF / CAGR / max DD | Buy and hold in B | Verdict |
|---|---|---|---|---|
| Grossmann VXX short, Bollinger (claims 96% a year) | 14 / +11.4% / 4.77 / 52.7% | 75 / +3.50% / 1.52 / 9.0% / 64.7% | — | FAIL (B CI) |
| Grossmann VXX short, SMA 5/15 | 23 / +7.9% / 6.56 / 71.4% | 116 / +2.50% / 1.61 / 15.7% / 45.3% | — | FAIL (B CI) |
| **Fosback seasonality, SPY** (in market 28%) | 178 / +0.29% / 1.38 / 2.8% | 267 / +0.32% / 1.56 / 4.9% / 14.9% | 14.0% / 33.7% | **PASS** |
| **Kaeppel Monthly 10, DIA** (in market 48%) | 263 / +0.41% / 1.55 / 9.6% | 428 / +0.35% / 1.57 / 8.2% / 21.5% | 12.8% / 36.7% | **PASS** |
| Lunar phases, 31 ETFs | 2,267 / +0.31% / 1.17 | 18,092 / −0.06% / 0.97 | — | FAIL |

**Extra checks for the two passes** (not part of the verdict).
- **Random timing.** The same holding lengths on random days, in the same index:

  | Rule | Before publication | After publication |
  |---|---|---|
  | Fosback | +0.20% per trade, z 1.1 | +0.06%, z 0.5 |
  | Monthly 10 | +0.37%, z 2.4 | +0.10%, z 1.0 |

  After publication most of their profit is simply being invested in a rising market. Compare `ibs_qqq`: z 4.0 after
  its article.
- **Leverage, 2010–2026:**
  - Fosback days traded in UPRO (3× S&P): 11.4% a year, 43.8% drawdown, vs UPRO buy and hold 29.8% / 76.8%.
  - Monthly 10 days traded in UDOW (3× Dow): 20.5% a year, 44.7% drawdown, vs UDOW buy and hold 25.0% / 80.3%.
  - They cut the drawdown, but return less than simply holding.

**Reading.**
- The highest claimed returns (inverse-volatility shorts, "96% a year") collapsed after publication. Their 45–65%
  drawdowns would have wiped out most traders (and borrow fees are not even counted).
- The turn-of-the-month effect still exists, but now earns little more than the market's own drift.
- The lunar effect is gone.
- **The best winner after seven rounds is still round 3's `ibs_qqq`.**
