# Agent 06: intraday volatility-expansion breakouts. Verdict: **FAIL**

**The idea.** When intraday volatility expands, price may follow through. I tested three ways to catch that on 15m bars of
6 liquid Binance perps (BTC, ETH, SOL, DOGE, XRP, AVAX):
- session opening-range breakouts at the Asia (00:00), London (07:00) and US (13:30 UTC) opens;
- Bollinger-inside-Keltner squeezes;
- breaks of the prior day's high or low.

Every version used a trailing ATR stop, a time exit, and could add a 10-day trend filter. That made 360 variants, all
designed on DEV (2021-01 to 2024-10). Entries are taker orders. A breakout is a stop entry, and the only way to get a
maker fill is a limit order on the retest, which mostly fills on the trades that fail. So I did not assume maker fills.

**DEV result.** 150 of the 360 variants had a positive gross edge, but only 21 stayed positive after the 0.14%
round-trip cost, which is about what noise would give. No variant passed the pre-declared rule: 300 or more trades and
a profit in both DEV halves. The best one is the US-open range (13:30-14:30 UTC), traded with the trend, with a 5-ATR
trail and a 24h time limit. It made +16 bp per trade in 2021-22 and -4 bp in 2023-24, a +0.68% mean month at 1x.
Its gross edge is about 22 bp per trade. Even if trading cost nothing, it would make roughly +1-2% a month at 1x.

**TEST (2024-11 to 2026-10), pre-registered primary A1_us_orb_L1: US-open ORB at 1x, 1,925 trades**

| month | % | month | % | month | % | month | % |
|---|---|---|---|---|---|---|---|
| 2024-11 | +6.24 | 2025-05 | +0.75 | 2025-11 | +3.82 | 2026-05 | -2.64 |
| 2024-12 | -6.53 | 2025-06 | -0.21 | 2025-12 | -2.68 | 2026-06 | -2.48 |
| 2025-01 | -10.67 | 2025-07 | +1.81 | 2026-01 | +8.47 | 2026-07 | -4.00 |
| 2025-02 | +25.84 | 2025-08 | +1.35 | 2026-02 | +6.37 | 2026-08 | +1.34 |
| 2025-03 | -4.63 | 2025-09 | -3.00 | 2026-03 | -11.24 | 2026-09 | +3.65 |
| 2025-04 | -5.40 | 2025-10 | +7.69 | 2026-04 | -7.18 | 2026-10 | +0.20 |

- **At baseline costs:** mean month +0.29%, median -0.01%, worst month -11.24%. Max drawdown 28.2%, net +0.57% over
  2 years, win rate 35%.
- **At doubled costs:** mean month -1.57%.
- **Per trade:** +17 bp gross, +2.5 bp net.
- **Concentration:** total profit is close to zero, so the largest trade makes up 734% of it.

All pre-registered attempts:

| attempt | TEST mean month | median | worst | max DD | net | mean at 2x costs |
|---|---|---|---|---|---|---|
| A1 US ORB 1x (primary) | +0.29% | -0.01% | -11.2% | 28% | +0.6% | -1.57% |
| A2 US ORB 5x | +0.43% | -5.17% | -47.0% | 88% | -72.9% | -8.55% |
| A3 squeeze-80 1x (exploratory) | -0.19% | -0.21% | -0.7% | 2.8% | -2.8% | -0.22% |
| A4 squeeze-80 5x | -0.95% | -1.03% | -3.4% | 13.5% | -13.5% | -1.10% |

The squeeze variant made only 19 TEST trades, at -90 bp each. Its DEV result came from 72 trades.

**Leverage for +20% a month.** No leverage gets there, so the number is null.
- **DEV leverage sweep:** the mean month peaks at +1% at 2x. 5x loses 94% of the account, and 10x or more wipes it out.
- **TEST leverage sweep:** 3x ends -33% and 5x ends -73%. 10x ends -99.7% and 20x ends -100%, with 23 liquidations.
  The sweep shows +7% as the mean month at 20x, but that is an artifact: it averages percentage moves on an account
  that is already near zero.
- **Simple scaling:** at roughly +0.3-0.7% a month at 1x, 20% a month would need about 30-60x. At that leverage a
  normal 1.5-3% adverse move against the 5-ATR trail liquidates the position.
- **Ruin risk:** I bootstrapped the DEV trades over 24 months. The chance of losing 90% or more is 0.8% at 3x, 23% at
  5x, 92% at 10x and 100% at 20x.

**Caveats.**
- The 6 coins were picked with today's knowledge, which biases the test in the strategy's favour.
- Equity is booked at each trade's exit. Trades last 24h at most, so the drawdown shown slightly understates the real
  intraday drawdown.

**Verdict: FAIL.** Intraday breakouts on liquid perps have a real but tiny gross edge, about 15-20 bp per trade. Taker
costs take most of it. Leverage scales the noise faster than the edge and only speeds up ruin. +20% a month is out of
reach by about two orders of magnitude.

Files:
- `run.py`: run with `dev` and then `test`.
- `engine.py` and `dev_scan.py`
- `prereg_params.json` and `PREREG.md`
- `dev_select.json` and `dev_scan.json`
- `results.json`
