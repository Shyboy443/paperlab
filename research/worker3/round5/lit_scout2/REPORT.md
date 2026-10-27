# lit_scout2 (Worker 3 round 5): both ideas FAIL

The agent could not write this file, so it was saved from its final message.

Each idea was chosen on DEV only and pre-registered in PREREG.md; TEST and HOLDOUT were each run once. 24 configs in
total: 12 TSD, 6 CTR, and 6 in an earlier CTR pass that required every feature to exist. Both passes are logged.

## Shortlist
1. **TSD**, built. Source: Zarattini, Pagani, Barbon, "Catching Crypto Trends" (SSRN 2025). A long-only Donchian
   ensemble with volatility sizing.
2. **CTREND**, built as a reconstruction, because the full text could not be fetched. Source: Fieberg et al., JFQA 2025.
   A trend factor from many technical signals, combined by ridge regression.
3. MAX × trend, not built: already tried.
4. Weekend vs weekday trend, not built: the evidence is weak.
5. Volume-weighted and dual momentum, not built: no peer-reviewed crypto out-of-sample evidence, and the idea overlaps
   with TSD and with W1.

## Selected configs
- **TSD:**
  - lookbacks 5..360 days;
  - entry on a close above the L-day maximum, with the channel midpoint as the trailing stop;
  - weight = signal × min(2, 0.5 / 90-day volatility) / 20;
  - point-in-time top-20, gross ≤ 1x, 25% re-size band.
- **CTR:** price/moving-average ranks for 3..200 days, combined by weekly ridge regressions averaged over 104 weeks;
  long 10 / short 10 of the top-40, weekly rebalance.

## Results at 1x
| | Window | Median | Geo | Mean | Up | Max DD | t | Trades/mo | Net, 2x cost |
|---|---|---|---|---|---|---|---|---|---|
| TSD | DEV | −0.52 | +0.40 | +0.47 | 18/46 | 19.9% | 0.85 | 28 | +18.1% |
| TSD | TEST | −0.20 | +0.51 | +0.60 | 10/24 | 14.6% | 0.65 | 30 | +11.8% |
| TSD | HOLDOUT | −0.22 | +0.81 | +0.88 | 16/36 | 12.6% | 1.43 | 24 | +32.5% |
| CTR | DEV | 0.00 | +1.15 | +1.42 | 22/46 | 30.9% | 1.27 | 42 | +47.3% |
| CTR | TEST | +3.19 | +0.08 | +1.20 | 13/24 | 64.8% | 0.40 | 51 | −6.7% |
| CTR | HOLDOUT | 0.00 | +1.73 | +1.88 | 15/36 | 19.4% | 1.99 | 21 | +75.4% |

- TSD is only about 14% invested on average.
- CTR's 2026 calendar year so far is −51%.

## Checks
- **TSD, 2021–26: 5/8.** Fails 3 (t 1.07), 4 (−2.6% without the 3 best months) and 7 (one position is 18.9% of
  profit).
- **TSD, holdout: 6/8.** Fails 3 (t 1.43) and 7 (16.9%).
- **CTR, 2021–26: 2/8.** Its profit leans on a single coin: RIVER is 101% of profit.
- **CTR, holdout: 6/8.**

## Portfolio
| Mix | TEST geo / median / up / DD | HOLDOUT geo / median / up / DD |
|---|---|---|
| W1+W2 50/50 | 4.48 / 3.54 / 20/24 / 5.9% | 3.30 / 2.85 / 26/36 / 11.5% |
| W1+W2+TSD in thirds | 3.18 / 1.83 / 18/24 / 4.8% | 2.50 / 2.26 / 25/36 / 7.7% |
| W1+W2+CTR in thirds | 3.24 / 3.51 / 17/24 / 22.6% | 2.82 / 1.90 / 26/36 / 7.8% |

The DEV-chosen mix gives the new leg a weight of 0 for both ideas.

## Verdict
- **TSD: FAIL.** The edge is real but tiny, about +0.5% a month.
- **CTR: FAIL.** The DEV edge did not survive TEST.

## Sources
- https://ideas.repec.org/p/chf/rpseri/rp2580.html
- https://concretumgroup.com/catching-crypto-trends-a-tactical-approach-for-bitcoin-and-altcoins/
- https://www.cxoadvisory.com/?p=46798
- https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/trend-factor-for-the-cross-section-of-cryptocurrency-returns/4C1509ACBA33D5DCAF0AC24379148178
