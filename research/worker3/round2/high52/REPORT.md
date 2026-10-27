# high52 (Worker 3 round 2): FAIL, 7/8 checks; a lead for a third leg

The agent could not write this file, so it was saved from its final message.

## Idea
Traders anchor on recent highs, so coins trading near their high keep outperforming. Sources:
- George & Hwang 2004 (J. Finance);
- Jia et al. 2026 (J. Banking & Finance), crypto 52-week-high anchoring;
- Fičura 2023;
- the Donchian channel position.

## DEV grid (192 configs)
- The 365-day versions are weak, and long-only versions draw down 46–89%.
- The Donchian position beats the George-Hwang ratio.
- The selection rule was fixed before the grid ran.

**Selected:** Donchian position, L = 30. Every day, long the 10 coins closest to their 30-day high and short the 10
closest to their 30-day low, from the point-in-time top-40. 1x, rebalanced at the 00:00 UTC open.

## Results at 1x
| | median mo | geo mo | mean mo | months up | max DD | t | trades/mo |
|---|---|---|---|---|---|---|---|
| DEV | +2.16% | +3.41% | +3.90% | 26/47 | 31.3% | 2.55 | 129 |
| **TEST** | **+3.91%** | **+5.23%** | +5.97% | **15/24** | **36.3%** | 2.27 | 120 |
| Full | +2.29% | +4.08% | +4.67% | 41/70 | 36.3% | 3.43 | 128 |
| TEST, doubled costs | +2.99% | +4.26% | +4.99% | 15/24 | 36.9% | 1.91 | |

- 2026 YTD: −2.64%. October 2026: −0.92%.

## Checks
7/8 pass:
- check 5: 16/16 neighbours positive on both DEV and TEST;
- check 6: beats 100% of random books.

Check 7 FAILS: five single positions are each above 10% of profit:
- PIPPIN 16.3%;
- BEAT short 13.9%;
- RAVE 13.1%;
- BEAT long 11.1%;
- MYX 10.6%.

It is still +664% with the top 5 coins removed.

## Correlation
- Worker 1: daily 0.39, monthly 0.15 over TEST.
- Worker 2: monthly 0.08 over TEST.

## Portfolio (TEST, monthly)
| Mix | median | geo | months up | monthly DD |
|---|---|---|---|---|
| W1 + W2 + this, equal thirds | +4.84% | +4.90% | 20/24 | 4.3% |
| W1 + W2, 50/50 | +3.54% | +4.48% | 20/24 | 5.9% |

The three-way mix is better than 50/50 on median, geometric month and drawdown, with the same months up.

## Verdict
**FAIL:**
- (a) check 7;
- (b) geometric month +5.23% is below +5.33%;
- (c) 15/24 months up and 36.3% max drawdown.

It is not formally PORTFOLIO-ONLY because it fails check 7, but it is the best lead so far for a forward-tested third
leg.
