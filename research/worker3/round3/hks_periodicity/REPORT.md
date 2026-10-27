# hks_periodicity (Worker 3 round 3): FAIL, 3/8 checks

The agent could not write this file, so it was saved from its final message.

## Idea
Same-block-of-day continuation per coin (Heston, Korajczyk & Sadka 2010), traded as a cross-sectional long/short book on
the point-in-time top-40.

Sources: HKS 2010; Long, Zaremba et al. 2020; FRL 2024; arXiv 2607.09426; arXiv 2109.12142. No published crypto test of
this exact design was found.

## DEV (197 configs)
- **Real before costs:** gross up to +10.4% a month, and specific to the same block (a control using other blocks scores
  much worse).
- **Killed by costs:** turnover is near total, so all every-block and one-block-per-day configs lose net.
- **Only one config was net positive on DEV:** 12-hour blocks, K = 40, k = 3, buffer 3, DEV geometric month +0.12%. It
  was pre-registered and run on TEST once.

**Disclosure:** this exact config was run once over the full period (TEST included) while timing the code, after it had
been selected on DEV.

## Results at 1x
| | median mo | geo mo | mean mo | months up | max DD | t | trades/mo |
|---|---|---|---|---|---|---|---|
| DEV | 0.00% | +0.12% | +0.68% | 23/47 | 63.5% | 0.42 | 498 |
| TEST | +7.88% | +7.54% | +9.58% | 15/24 | 36.9% | 2.12 | 464 |
| Full | +1.78% | +2.61% | +3.75% | 38/70 | 63.5% | 1.94 | 493 |

- DEV at doubled costs: −86%.
- 2026 YTD: +183.7%.
- Gross geometric month: TEST +10.9%, DEV +4.6%.

## Checks
3/8 pass. These fail:
- check 1: DEV at doubled costs;
- check 2: 2022–2024 negative;
- check 3: t 1.94;
- check 5: 46% of neighbours;
- check 7: −23% without the top 5 coins (LUNA, AKE, BEAT, LAB, RIVER).

## Correlation
Monthly correlation over TEST: −0.06 with Worker 1, 0.07 with Worker 2.

## Portfolio (post-hoc, not eligible)
Adding it to W1+W2 in thirds gives TEST median +4.68%, geo +5.98%, 19/24 months up, monthly DD 5.9%.

## Verdict
**FAIL**:
- (a) 3 of 8 checks;
- (c) 15/24 months up and 36.9% max drawdown;
- (d) mixed.

TEST beats (b), but the edge only exists in 2025–26, and the profit comes from a few thin new coins. If it is ever
reconsidered, it needs a forward test.
