# realized_skew (Worker 3 round 3): FAIL, DEV fail, 0/8 checks

The agent could not write this file, so it was saved from its final message.

## Idea
Weekly realized skewness and kurtosis from hourly returns, traded as a cross-sectional long/short book.

Sources:
- Amaya et al. 2015 (JFE);
- Jia, Liu & Yan 2021 (FRL), crypto.

## DEV grid (48 configs)
- Skew with the literature sign: all 12 configs lose. In crypto, high skew is followed by higher returns.
- Kurtosis: flat, with DD above 38% in every row.
- No literature-sign row qualified, so the family is a DEV FAIL. As pre-registered, the best-t row still ran on TEST once.

Realized skew has a 0.65–0.67 rank correlation with a MAX sort, so it is mostly a smoothed version of the MAX family that
already failed.

## Results at 1x
Selected: kurt, W = 14 days, 10/10, top-40, weekly.

| | median mo | geo mo | mean mo | months up | max DD | t |
|---|---|---|---|---|---|---|
| DEV | +0.27% | +0.65% | +0.92% | 27/47 | 53.2% | 0.85 |
| TEST | −2.44% | −3.38% | −3.02% | 7/24 | 59.7% | −1.70 |
| Full | −0.29% | −0.74% | −0.42% | 33/70 | 80.7% | −0.43 |

## Checks
0/8 pass.

## Secondary diagnostic (not eligible)
Reverse skew: TEST geometric month −0.65%, 12/24 months up.

## Verdict
**FAIL** on (a)–(d). Adding it to W1+W2 drops the mix to 15/24 months up.
