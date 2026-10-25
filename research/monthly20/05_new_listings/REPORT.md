# Agent 05: new listings. Verdict: FAIL

**Idea.** Newly listed Binance perps usually bleed after launch. The pre-registered rule was: short every new
USDT-M perp 1 h after its first bar, hold 7 days, and use 5 equal isolated-margin slots. The rule was tuned on DEV
listings (2021-01..2024-10, 268 listings, 209 grid configs). PREREG.md says "186"; that was a miscount, and all 209
configs are in results.json. The rule was then run once on TEST (2024-11..2026-10, 552 listings).

**Survivorship handled at the source.** I did not use perp_symbols(), which lists only coins still trading. Bars and
funding come from the public archive data.binance.vision, which still holds delisted symbols. That gives 900 USDT-M
symbols in total. One exchangeInfo snapshot (status SETTLING) marks 90 DEV and 51 TEST crypto listings as delisted.
That is 34% of the DEV listings and 15% of the TEST crypto listings. A web search confirms a steady stream of perp
delistings in 2025-26 (OL, HIPPO, VINE, PROMPT, B3, DEGEN, and others).

**Finding: the bias runs against the short.** Delisted coins fell much harder than survivors:

| | survivors (DEV) | delisted (DEV) |
|---|---|---|
| 30-day median return after listing | -12% | -21% |
| 1-day mean return after listing | +0.5% | -3.3% |

Rule A on survivors only:

| window | survivors only | full survivorship-free universe |
|---|---|---|
| DEV net | +18% | +295% |
| TEST net | +13% | +108% |

So a study built on survivors only would have *understated* this edge.

**Mistake I found after the TEST run.** The archive also contains 212 TradFi perps listed in 2025-26: stocks, ETFs,
gold and pre-IPO contracts. My universe rule did not exclude them. I report the pre-registered run as it is.
Re-running the same rules on crypto listings only is counted as a new, not pre-registered attempt.

**Shape of the effect.** These are entries 1 h after listing.

| horizon | median return | mean return |
|---|---|---|
| 7 days | -10% (DEV), -5% (TEST, all) | about -2% |
| 90 days | about -25% to -35% | positive |

The median falls but the mean is positive at 90 days, because of a fat right tail. 30-43% of new coins rise by more
than 50% at some point in their first 30 days (TRUMP rose 248% within a week, VINE 156%). Funding in the first month
is small, about +/-0.01-0.02% per stamp.

Spot listings (499 USDT pairs) drift down even harder. On TEST the 30-day median is -19% and the 90-day median is
-53%. About 70% of them are lower 30 days later. Spot cannot be shorted, though, and the perp result already covers
the coins that also have a perp. Two other ideas failed on DEV: a "bounce" long after a -30%/-50% drop, and buying
at listing.

## TEST monthly returns (%)

| month | A 1x (prereg) | A crypto-only (post-TEST) | B 2x (prereg) |
|---|---|---|---|
| 2024-11 | -26.6 | -26.6 | -42.0 |
| 2024-12 | +12.7 | +12.7 | -49.3 |
| 2025-01 | +152.5 | +152.5 | +307.0 |
| 2025-02 | -9.4 | -9.4 | +13.7 |
| 2025-03 | +15.2 | +15.2 | +30.9 |
| 2025-04 | -48.0 | -48.0 | -39.6 |
| 2025-05 | +58.6 | +58.6 | +119.4 |
| 2025-06 | -32.0 | -32.0 | -13.2 |
| 2025-07 | -33.0 | -33.0 | -18.4 |
| 2025-08 | -28.5 | -28.5 | -48.0 |
| 2025-09 | -11.6 | -11.6 | -20.1 |
| 2025-10 | +120.6 | +120.6 | +234.9 |
| 2025-11 | -12.0 | -12.0 | -59.5 |
| 2025-12 | +26.3 | +27.2 | -5.8 |
| 2026-01 | +51.8 | +57.8 | +117.7 |
| 2026-02 | +13.7 | +1.9 | -25.2 |
| 2026-03 | -4.7 | -6.3 | -13.0 |
| 2026-04 | +5.1 | +5.0 | -20.0 |
| 2026-05 | -2.9 | -3.4 | -26.9 |
| 2026-06 | -2.6 | +4.6 | +19.3 |
| 2026-07 | +29.2 | +5.9 | +52.3 |
| 2026-08 | -11.2 | +7.1 | -17.6 |
| 2026-09 | -2.7 | +6.8 | -12.0 |
| 2026-10 (part) | -10.1 | -10.1 | -10.2 |

## Every attempt, on TEST

The first three rows are pre-registered. The "crypto" rows were run after TEST.

| attempt | mean month | median month | compounded month | worst month | max DD | net | trades | liquidations | doubled-cost mean / net |
|---|---|---|---|---|---|---|---|---|---|
| **A** 1x, stop +50% | +10.4% | -2.8% | +3.1% | -48.0% | 85.6% | +108% | 369 | 0 | +10.0% / +87% |
| B 2x, stop +25% | +19.8% | -13.1% | -0.6% | -59.6% | 90.6% | -13% | 396 | 3 | +18.7% / -31% |
| C 5x ("chase 20%") | +51.0% | -27.1% | -25.0% | -94.7% | 100% | -99.9% | 413 | 190 | wiped out |
| A crypto | +10.6% | +3.3% | +3.4% | -48.0% | 85.6% | +123% | 288 | 0 | +10.3% / +105% |
| B crypto | +20.5% | -7.5% | +0.6% | -59.6% | 90.6% | +15% | 311 | 4 | +19.7% / -4% |
| C crypto | +54.0% | -26.0% | -21.5% | -94.7% | 100% | -99.7% | 325 | 186 | wiped out |

For A, the 3 biggest trades make 75% of the profit, and the biggest single trade makes 28%. All profit comes from
three crash months: Jan-25 (AI-agent and meme listings), May-25 and Oct-25. On DEV, A made +3.7%/month on average
(compounded +3.0%) with a 46% max drawdown.

## Leverage needed for 20%/month, and how often it blows up

The arithmetic mean month is misleading here, because one +150% month hides many losing months. The mean of B
crosses 20% at 2x, but B compounds to about 0%/month and ends -13% after a 90% drawdown.

Leverage sweep of rule A on TEST (diagnostic, not pre-registered):

| leverage | net | liquidations |
|---|---|---|
| 1x | +108% | 0 |
| 2x | -34% | 78 |
| 3x | -82% | 116 |
| 5x | -99.9% | 190 |
| 10x | -100% | wiped out within weeks |

On DEV, 10x was also wiped out. Leverage cannot reach a *compounded* +20%/month at any level. Above 1x, squeezes on
new coins liquidate 20-45% of the trades. **leverage_needed_for_20pct = none (null).**

**Verdict: FAIL.** The honest edge is real but small and lumpy. It is about +3%/month compounded at 1x, with an 86%
drawdown. It comes from a few crash months, and it fails the 20% target by a factor of about 6.

Reproduce: `python fetch.py` (archive download into data/monthly20_05_new_listings), then
`python run.py dev`, `python run.py test`, `python run.py spot`, `python run.py final`.
