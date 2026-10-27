# Worker 3 (built rule, unchanged): long-term, luck and 200 USDT checks (2026-10-27)

The script is verify.py and the numbers are in results.json. Nothing was chosen here: this is the same rule that is
built in app/worker3.

## Long-term (1x, all costs, 10k)
| | 2021-01..2026-10 (perps, continuous) | 2018-2020 (spot, never used to pick) |
|---|---|---|
| Typical (median) month | +3.4% | +3.2% |
| Compounded month | +6.8% | +5.0% |
| Months up | 42/71 | 24/37 |
| Every year | 2021 +316, 2022 +14, 2023 +81, 2024 +71, 2025 +308, 2026 (10 mo) +75 | 2018 +48, 2019 +39, 2020 +200 |
| Rolling 12 months positive | 56/60 (worst −15%) | 26/26 (worst +1.7%) |
| Rolling 6 months positive | 56/66 (worst −17%) | 29/32 |
| Worst month / max drop | −13.5% / 40% | −18.4% / 28.5% |
| Longest time below a peak | 469 days | 193 days |

## Luck
- **Block bootstrap** (30-day blocks, 5,000 runs): the chance of a losing period is 0.0% (perps) and 0.2% (spot).
  The 5th-percentile CAGR is +64% and +30%.
- **Deflated Sharpe on the 2021-26 perps, corrected for ~100 / 500 / 1000 configs tried across rounds:** 0.94 / 0.85 /
  0.80. If every config ever tried counts, it falls below the usual 0.95 bar.
- **Deflated Sharpe on the 2018-20 spot:** 0.85 at 100 configs. Only a handful of ideas ever touched this data, so the
  real correction is smaller.
- **Earlier checks** (round 5):
  - HOLDOUT 8/8;
  - TEST 7/8, where check 7 fails because RAVE is 15% of profit;
  - beats 100% of random-coin books;
  - all neighbour settings are positive.

## 200 USDT (Binance minimums: 5 USDT, ETH 20, BTC 100 skipped; daily re-sizes below the minimum are skipped)
| | 2021-26 compounded month | Months up | Max DD | Rolling 12m positive | Doubled costs |
|---|---|---|---|---|---|
| 10k | +6.78% | 42/71 | 40.0% | 56/60 | - |
| 200 USDT | +6.73% | 44/71 | 39.9% | 55/60 | +5.73%/month |
| 200 USDT, 2018-20 spot | +4.37% (10k: +5.04%) | 21/37 | 32.1% | 25/26 | +3.70%/month |

**Live switch:** the minimum was lowered to 200 USDT (app/worker3/live.py). It already skips orders below the exchange
minimum, the same way this simulation does.
