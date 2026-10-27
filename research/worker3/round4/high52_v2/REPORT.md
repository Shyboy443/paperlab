# high52_v2 (Worker 3 round 4): the Donchian-30 lead HOLDS on 2018–2020 (YES, with caveats)

The agent could not write this file, so it was saved from its final message.

PREREG.md and its addendum were written before the holdout. The engine reproduces round 2's DEV result exactly. A
stub-month fix was audited: the results are identical with and without it.

## What we learned
1. **The long leg is the strategy.** Longs made 97% (DEV) and 118% (holdout) of profit; the short leg mostly works as a
   hedge.
2. **It is a risk-on effect.** With BTC above its 100-day average it makes about +5–10% a month (t ≈ 3); with BTC below
   it, about +1–2% (not significant).
3. **The check-7 failure is a 2025–26 new-listing artefact.**
   - On TEST, coins younger than 180 days made 150% of profit.
   - On DEV and the holdout, coins at least 1 year old made 60–70% of it.
   - On the holdout the largest single position is only 3.9% of profit.
4. **Profit comes from riding winners.** Positions held more than 10 days carry the profit; positions held 3 days or
   less lose money.
5. **Costs take about 15–23% of the gross return.** It survives doubled costs in every window.

## Improving it (DEV only)
9 candidates were tested. Only IV (inverse-vol weights) was eligible. It lowers concentration but gives up geo in every
window, so keep ORIG.

## Results at 1x
| | Window | median | geo | mean | up | max DD | t | net 1x | net 2x |
|---|---|---|---|---|---|---|---|---|---|
| ORIG | DEV | +2.41 | +3.48 | +3.99 | 25/46 | 31.3% | 2.54 | +383% | +205% |
| ORIG | TEST (context) | +3.95 | +5.23 | +5.98 | 16/24 | 36.3% | 2.26 | +240% | +172% |
| **ORIG** | **HOLDOUT** | 0.00 (13 cash months) | **+3.50** | +3.81 | 16/36 | **25.4%** | **2.75** | **+245%** | **+177%** |
| ORIG | Holdout, 23 active months | +6.79 | +5.53 | +5.96 | 16/23 | | 2.91 | | |
| IV | HOLDOUT | 0.00 | +3.35 | +3.62 | 17/36 | 22.2% | 2.80 | +227% | +160% |

The holdout is effectively 2019-02..2020-12, because the 10/10 book holds cash while fewer than 20 coins are eligible.
By year: 2019 +13.7%, 2020 +203% (the DeFi summer).

## Checks on the holdout
ORIG 8/8, IV 8/8. Details for ORIG:
- 16/16 neighbours positive;
- beats 100% of random books;
- +184% with the top 5 coins removed.

## Portfolio (monthly)
| Window | Mix | median | geo | up | monthly DD |
|---|---|---|---|---|---|
| Holdout | W1 + W2 50/50 | +2.85 | +3.30 | 26/36 | 11.5% |
| Holdout | **W1 + W2 + ORIG thirds** | +2.27 | **+3.43** | 26/36 | **7.8%** |
| 2021–26 | W1 + W2 50/50 | +4.81 | +4.74 | 53/70 | 7.2% |
| 2021–26 | W1 + W2 + ORIG thirds | +3.34 | +4.65 | 54/70 | 8.5% |

## Verdict
**YES**: ORIG meets the pre-registered YES rule. It is a credible forward paper-test candidate as a third leg.

Caveats:
- the holdout is effectively 23 months, with most of the profit in 2020;
- the edge is a long-side, BTC-uptrend effect;
- shorts are assumed possible on spot;
- its geo month (+3.5%) is below Worker 1's +5.33% bar, so it is not "better than W1" alone.
