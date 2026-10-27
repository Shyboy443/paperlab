# residual_reversal (Worker 3 round 1): FAIL, and not useful as a portfolio add-on

The agent could not write this file, so it was saved from its final message.

## Idea
Residual returns are daily returns with BTC's influence removed, using a beta estimated from the last 60 days only. The
rule sorts the top-40 perps (survivorship-free hourly panel) on these residual returns.

Sources (see PREREG.md):
- equity: Blitz et al. 2013, residual reversal; Blitz et al. 2011, residual momentum;
- crypto: Liu, Tsyvinski & Wu 2022; Dobrynskaya 2023 (SSRN 3913263); Drogen, Hoffstein & Otte 2023 (SSRN 4322637);
  FRL 2021 "Up or down?"; Fieberg et al. 2023.

## DEV grid (94 configs, `dev_grid.json`)
- **Residual reversal is dead:** 0 of 30 configs qualified and 29 of 30 lost money (t as low as −4.8). At 1–7 days,
  crypto residual moves keep going instead of reversing.
- **Residual momentum works on DEV:** 25 of 32 configs qualified. But plain momentum without the BTC adjustment did as
  well or better, so the adjustment adds nothing.
- **Selected:** 14-day residual momentum, no skip, 60-day beta, long 8 / short 8, equal weight, weekly rebalance.

## Results at 1x
| | median mo | geo mo | mean mo | months up | max DD | t | trades/mo |
|---|---|---|---|---|---|---|---|
| DEV | +3.93% | +3.53% | +3.93% | 28/47 | 25.1% | 2.82 | 102 |
| TEST | +4.75% | +4.45% | +5.49% | 16/24 | 50.9% | 1.79 | 103 |
| Full | +3.96% | +3.89% | +4.52% | 44/70 | 50.9% | 3.24 | 104 |

## Checks
7/8 pass. Check 7 FAILS: one PIPPIN position made 24.5% of total profit (the limit is 10%).

## Other numbers
- 2026 YTD: −17.8%.
- Monthly correlation over TEST: 0.53 with Worker 1, −0.12 with Worker 2.

## As a third leg (TEST)
Adding it to W1+W2 in equal thirds:
- geometric month rises from +4.48% to +4.66%;
- but months up fall from 20/24 to 17/24, and monthly drawdown rises from 5.9% to 15.5%.

So it does not improve the portfolio.

## Verdict
**FAIL**:
- (a) 7 of 8 checks;
- (b) geometric month +4.45% is below +5.33%;
- (c) 16/24 months up, max drawdown 50.9%.

Only (d) passes: it works with 500 USDT.
