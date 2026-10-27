# merged_signal (Worker 3 round 5): PORTFOLIO (just missed STRONG)

The agent could not write this file, so it was saved from its final message.

PREREG.md part 1 was written before any candidate ran. Part 2 (the DEV table, the selected config, the neighbours and
the portfolio rule) was written before TEST and HOLDOUT. Files: `lib.py`, `configs.py`, `dev.py`, `dev.json`,
`dev.log`, `evaluate.py`, `evaluate.log`, `results.json`.

## Idea and selected config
Worker 1 (the r4_07 ensemble) and high52 are merged into one book on W1's own engine, which was not changed.

**Sources:**
- George-Hwang 2004 (JF): nearness to the 52-week high explains momentum.
- Bhootra-Hur 2013 (JBF): a recent 52-week high matters more than an old one.

**Pre-registered: 16 candidates.**
- A: rank merges;
- B: high52 as a filter on W1's picks;
- C: high52-style holding of W1's winners;
- D: high52 as a fourth leg;
- E: combinations.

There were also 2 references, 27 neighbours and 200+200 random books. W1 reproduces exactly on DEV (geo 2.96) and on
the holdout (geo 2.69).

**Selection rule (DEV only):** DEV max DD ≤ 25%, t ≥ 2.5 and doubled-cost net > 0; then the highest DEV geo month.

**Selected: E1_rank_w_plus_H**, four inverse-vol legs:
- RANK = 0.5 × rank(14-day return) + 0.5 × rank(30-day range), top-40, long 10 / short 10, rebalanced weekly;
- W1's ML leg;
- W1's 60-day momentum leg;
- the high52 leg: long 10 / short 10, rebalanced daily.

**DEV lessons:**
- Filters (B) and holding rules (C) do not help; B2 and B3 reach only +1.0 to +1.2 geo.
- The recency rank hurts.
- The whole gain comes from the added high52 leg. D1 (four legs, no rank merge) has a DEV geo of +3.34, about the
  same as E1.

## Results at 1x
| Window | Median | Geo | Mean | Up | Max DD | t | Net 1x / 2x cost |
|---|---|---|---|---|---|---|---|
| DEV | +3.43 | +3.38 | +3.61 | 32/46 | 22.5% | 3.46 | +361% / +206% |
| TEST | **+7.13** | **+5.72** | +6.09 | 16/24 | 18.5% | 3.23 | +280% / +222% |
| HOLDOUT | +3.89 | **+3.59** | +3.86 | 25/36 | 24.4% | 3.09 | +256% / +179% |
| W1 TEST | +3.84 | +5.33 | +5.86 | 17/24 | 23.3% | 2.62 | +248% |
| W1 HOLDOUT | +3.12 | +2.69 | +2.94 | 25/36 | 24.4% | 2.44 | +160% |

**Holdout notes:**
- By year: 2018 +8.8%, 2019 +37.9%, 2020 +137.1%.
- Before 2019-02 there are fewer than 20 eligible coins, so the book equals W1 and has the same 24.4% drawdown.
- All the gain over W1 comes from 2019-02..2020-12.

## Checks: 8/8 on TEST and 8/8 on HOLDOUT
| Check | TEST version (2021–26) | HOLDOUT |
|---|---|---|
| 1. Net, 1x / 2x cost | +361/+206 (DEV), +280/+222 (TEST) | +256 / +179 |
| 2. Years positive | 6/6 | 3/3 |
| 3. t / bootstrap 5th percentile | 4.72 / +2.72 | 3.09 / +1.65 |
| 4. Without the best 3 months | +806% | +118% |
| 5. Neighbours positive | 27/27 | 27/27 |
| 6. Beats random books | 100% | 100% |
| 7. Without the top 5 coins / largest position | +923% / 7.0% | +207% / 6.1% |
| 8. Geo / mean | 4.18 / 4.46 | 3.59 / 3.86 |

**Against the STRONG bar:** it misses only one item, TEST months up (16/24 against the required 18; W1 has 17).

## Correlation and portfolio (monthly)
Correlation with W1 is 0.80 on DEV, 0.91 on TEST and 0.94 on HOLDOUT, so X is an upgraded W1. Correlation with W2 is
0.16 / −0.07 / −0.18.

| Window | Mix | Median | Geo | Up | Month-end DD |
|---|---|---|---|---|---|
| TEST | W1+W2 50/50 | +3.54 | +4.48 | 20/24 | 5.9% |
| TEST | W1+W2+X thirds | +3.73 | +4.93 | 17/24 | 3.9% |
| TEST | best DEV mix (W1 0 / W2 0.6 / X 0.4) | +3.91 | +4.35 | 20/24 | 5.0% |
| TEST | W2+X 50/50 (not pre-registered) | +4.47 | +4.62 | 20/24 | 4.4% |
| HOLDOUT | W1+W2 50/50 | +2.85 | +3.30 | 26/36 | 11.5% |
| HOLDOUT | W1+W2+X thirds | +4.35 | +3.44 | 26/36 | 12.6% |
| HOLDOUT | best DEV mix (0 / 0.6 / 0.4) | +3.11 | +3.73 | 28/36 | 11.0% |
| HOLDOUT | W2+X 50/50 (not pre-registered) | +3.16 | +3.74 | 26/36 | 11.5% |

## Running it from 500 USDT
It does not work from 500 USDT as built. About 51% of held coin-days fall below the exchange minimum order size, and the
median position is about 5.5 USDT. The agent says W1 has the same problem (48%); that claim still needs to be checked
against how the live W1 handles orders below the minimum.

## Caveats
- TEST is spent, and high52 itself was found on data that included TEST.
- The holdout gain comes from about 23 months, mostly 2020.
- The ML leg uses W1's cached walk-forward predictions.

## Verdict: PORTFOLIO
It passes 8/8 on both windows and beats W1 on TEST geo, median and drawdown and on holdout geo. It misses STRONG only on
TEST months up.

Next step: a forward paper test of X as a W1 replacement ("W1 + high52 leg").
