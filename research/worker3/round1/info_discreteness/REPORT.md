# info_discreteness (Worker 3 round 1): FAIL

The agent could not write this file, so it was saved from its final message.

## Idea
Momentum quality: does a smooth price path predict better continuation than the raw return alone? Tested candidates:
- frog-in-the-pan (Da, Gurun & Warachka 2014);
- Clenow's trend score (slope × R²);
- Sharpe-ratio momentum;
- residual momentum.

## DEV results (160 configs)
- Frog-in-the-pan doesn't work in crypto: its best t was 1.78, against 2.97 for plain 14-day momentum.
- Sharpe momentum was rejected before TEST, because its correlation with Worker 1's 14-day rotation leg was 0.88.

**Selected** (with a materiality rule decided on DEV): Clenow trend score over 30 days, long top 10 / short bottom 10 of
the point-in-time top-40, weekly rebalance, 1x.

## Results at 1x
| | median mo | geo mo | mean mo | months up | max DD | t | trades/mo |
|---|---|---|---|---|---|---|---|
| DEV | +0.28% | +3.22% | +3.69% | 24/47 | 30.4% | 2.44 | 33.4 |
| TEST | −0.49% | +1.99% | +2.92% | 12/24 | 37.2% | 0.95 | 37.3 |
| Full | +0.31% | +2.85% | +3.48% | 36/70 | 37.2% | 2.40 | 35.0 |

## Checks
7/8 pass. Check 7 FAILS: the largest single position is 30.7% of profit.

## Correlation and portfolio
- Monthly correlation over TEST: 0.10 with Worker 1, −0.06 with Worker 2.
- Adding it to W1+W2 in equal thirds drops the mix from 20/24 to 18/24 months up, and the geometric month from +4.48% to
  +3.88%.

## Verdict
**FAIL**:
- (a) 7 of 8 checks;
- (b) geometric month +1.99% and median −0.49% are below Worker 1's;
- (c) 12/24 months up and 37% max drawdown.

Only (d) passes. The signal is real and low-correlation, but too weak and lumpy.
