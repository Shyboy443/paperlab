# Worker 3 research round 3 (2026-10-26): no candidate passes

| Agent | Idea | Checks | TEST median / geo month | Months up | Max DD | Verdict |
|---|---|---|---|---|---|---|
| hks_periodicity | same 12h block continuation L/S | 3/8 | +7.88 / +7.54 | 15/24 | 36.9% | FAIL (DEV ~0; 2022-24 lost; 5 thin coins) |
| exchange_events | short 48h after delist/tag/Upbit events | 5/8 | +9.43 / +1.55 | 15/24 | 78.2% | FAIL (squeezes) |
| tug_of_war | Europe-vs-total session momentum reversal | 1/8 | −3.87 / −4.34 | 10/24 | 78.7% | FAIL |
| spec_ratio | perp/spot volume ratio L/S | 0/8 | −1.80 / −1.61 | 9/24 | 47.9% | FAIL |
| realized_skew | realized kurtosis / skew L/S | 0/8 | −2.44 / −3.38 | 7/24 | 59.7% | FAIL (DEV fail) |

## After 3 rounds
- **Families tested:** 16 (6 + 5 + 5). None passes the bar.
- **None is PORTFOLIO-ONLY under the rules either.**
- **What keeps happening:** most families worked in 2021–24 and faded or flipped in 2025–26. The two with strong TEST
  numbers (hks_periodicity, exchange_events) were weak on DEV and depend on a few thin new coins.
- **Multiple testing:** the same TEST window has now been used for 16 families. A future "pass" on it has a materially
  higher chance of being luck.
- **Next step:** forward paper tests of the best leads, which give genuinely new out-of-sample data, rather than more
  backtest rounds on the same window.

## Leads worth a forward paper test
- high52 Donchian-30 L/S as a third leg (round 2): it improves W1+W2 on TEST.
- W1 with momentum-crash protection (round 2): 18/24 TEST months up.
- hks 12h periodicity (round 3): strong in 2025–26 only.
