# Agent 04 - liquidation cascades and capitulation reversals: FAIL

**Idea.** When a liquidation cascade hits the whole alt market, forced sellers push prices below fair value for hours.
I bought the worst-hit liquid perps right after a market-wide crash: at least 30% of the top 60 down 5% or more in 4h.
I used the 10 worst coins and limit bids 2% below. The fill needed a real trade-through. I held for 24 hours.
A second rule bought single coins on capitulation: a 4h z-score of -3 or lower, a volume spike of 3x or more, and a
taker-buy share of 0.45 or less. Squeeze fades (shorts) were also tried on DEV and found nothing.

**Data.** Binance USD-M 1h bars and funding come from the public data.binance.vision archive. It includes **delisted
coins**, so there is no survivorship bias. The universe is the point-in-time top 60 by 30-day volume, rebalanced monthly.
I tried 160 variants on DEV (dev_grid.json, dev_robust.json). PREREG.md fixed 3 attempts before the single TEST run.

## TEST, 2024-11 .. 2026-10 (pre-registered, baseline costs)
| attempt | mean month | median | worst month | max DD | net 24 mo | trades | liquidations | x2-cost mean |
|---|---|---|---|---|---|---|---|---|
| A1 breadth crash 1x | +0.67% | -0.25% | -15.8% | 45.0% | +7.2% | 506 | 0 | +0.41% |
| **A2 breadth crash 2.5x (the 20% attempt)** | **-1.98%** | -0.65% | **-78.7%** (2025-10) | **96.6%** | **-83.7%** | 506 | 11 | -2.59% |
| A3 single-coin flush 1x | -1.29% | -0.56% | -11.2% | 39.6% | -28.6% | 439 | 0 | -1.50% |

A2 monthly (%), 2024-11 to 2026-10: +81.8, +1.0, -19.1, +0.3, -29.9, +41.6, +11.6, -33.1, +1.1, -3.2, -9.1, **-78.7**,
-38.0, -3.6, -3.6, +21.2, -1.3, 0, -1.4, +8.2, 0, -5.0, 0, +11.5.

A1 monthly (%): +28.9, +3.6, -5.4, +5.6, -12.2, +15.9, +4.8, -13.9, +0.5, -1.2, -3.6, -1.8, -15.8, -1.3, -1.4, +8.3,
-0.5, 0, -0.5, +3.4, 0, -2.0, 0, +4.6. A1's tiny profit comes from one trade (178% of net profit).

## DEV vs TEST
DEV (2021-01..2024-10) looked strong. A1 made a mean of +7.5% a month (6.7% geometric) with a 28% max DD.
A2 made +19.6% a month with a 66% DD. Almost all of this came from 2021: about 18% a month in 2021, but only 1-2% a
month in 2022-2023. On TEST the bounce edge after costs is about zero, and the strategy was crushed by cascades that
kept falling. In the 2025-10-10 crash, alts fell 40-70% within hours. That event alone liquidated the leveraged book.

## Leverage needed for +20% a month
- **No leverage reaches it on TEST.** The mean month is negative at every leverage from 1.5x to 5x.
- From 1.5x up, the book loses 67-98% and gets liquidated 7-31 times.
- On DEV, a 20% mean needed about 2.5-3x. That gave a 66-87% DD, and 4x or more wiped out on DEV too.
- Buying a crash with leverage is short the tail. At the leverage 20%/month needs, the book is ruined roughly once per
  cascade like 2025-10-10, which means about every 1-2 years.

## Caveats
- Funding for October 2026 is extrapolated, because the archive ends in 2026-09.
- Fills use hourly bars: stops are checked first within a bar, and liquidation is checked on the bar's low.
- The 1%-of-volume cap barely bound at these sizes.

**Verdict: FAIL.** Crash and capitulation reversal had a DEV edge driven by 2021 that did not survive out of sample.
It is not a path to +20% a month, and adding leverage makes it ruinous.
