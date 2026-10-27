# leads_holdout (Worker 3 round 4): neither lead holds on the 2018–2020 holdout

The agent could not write this file, so it was saved from its final message. PREREG.md was written before the run, and
the holdout was run once.

One stub-month fix was disclosed: the run first stopped at the portfolio step. After the fix, A's results are identical
and B's pass/fail and net results are identical.

## A. Worker 1 + V2 crash protection: NO
| Holdout 1x | median | geo | mean | months up | max DD | t |
|---|---|---|---|---|---|---|
| W1 | 3.12 | 2.69 | 2.94 | 25/36 | 24.4% | 2.44 |
| W1 + V2 | 3.00 | 2.58 | 2.83 | 25/36 | 24.4% | 2.35 |

- V2 passes 8/8 checks, but loses head-to-head to plain W1. The cut only removed exposure during the 2019 recovery.
- Plain W1 reproduces the backfill exactly and passes all 7 applicable checks on the holdout.

## B. hks 12h periodicity: PARTLY, drop it
| Holdout | median | geo | months up | max DD | t |
|---|---|---|---|---|---|
| Net 1x | 1.10 | 0.65 | 19/36 | 59.9% | 0.69 |
| Net, 2x costs | −1.42 | −2.81 | 15/36 | 73.8% | −1.08 |
| Gross (no costs) | 2.93 | 4.23 | 22/36 | 46.4% | 2.30 |

- Checks: 1/8.
- Costs take 3.6 pp a month (about 460 trades a month). The effect is real before costs but not tradable at taker costs.

## Holdout portfolios (monthly)
| | median | geo | months up | max DD (month-end) | t |
|---|---|---|---|---|---|
| W1 | 3.12 | 2.69 | 25/36 | 18.5% | 2.44 |
| W2 | 2.10 | 3.56 | 26/36 | 14.1% | 2.69 |
| **W1 + W2 50/50** | 2.85 | **3.30** | **26/36** | **11.5%** | **4.28** |
| W1 + W2 + B thirds | 2.71 | 2.60 | 23/36 | 11.6% | 3.06 |

W1/W2 monthly correlation on the holdout: −0.29.

## Lessons
- **W1 + W2 50/50 is again the best combination**, now confirmed on unseen data.
- **B's TEST diversification came from thin 2025–26 coins** and did not repeat.
- **2018 spot had only 6–18 USDT pairs**, so 2019–2020 is the meaningful part of the holdout for top-40 strategies.
