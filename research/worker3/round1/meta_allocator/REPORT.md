# meta_allocator (Worker 3 round 1): FAIL

The agent could not write this file, so it was saved from its final message.

## Selected config
The config was pre-registered from a 40-config grid run on DEV only (`dev_grid.json`).

**Selected:** `relmom, L=30, tilt=0.25, weekly`. Every 7 days it puts 75% in the sleeve with the better 30-day return
(lagged 2 days) and 25% in the other.

Only relative momentum beat 50/50 on DEV. Vol-target, inverse-vol, min-variance, drawdown-cut and BTC-sleeve configs
all lost to 50/50, because under a 1x cap they can only cut exposure.

## Results at 1x
| | median mo | geo mo | mean mo | months up | max DD | t |
|---|---|---|---|---|---|---|
| DEV | +4.73% | +5.20% | +5.39% | 35/46 | 10.8% | 5.75 |
| **TEST** | **+4.11%** | **+4.60%** | +4.83% | **17/24** | 11.9% | 3.36 |
| TEST, 50/50 mix | +3.55% | +4.35% | +4.55% | 20/24 | 11.4% | 3.35 |
| TEST, Worker 1 | +3.84% | +5.33% | +5.86% | 17/24 | 23.3% | 2.62 |
| TEST, Worker 2 | +2.23% | +3.02% | +3.36% | 16/24 | 17.3% | 1.93 |

## Checks
7/8 pass. Check 6 FAILS: the rule beats only 82.5% of random allocators (95% needed), so its timing skill cannot be told
apart from chance.

Check 5: all 11 neighbours are positive, but only 8 of 11 beat 50/50 on TEST.

## Other numbers
- Doubled-cost TEST geometric month: +3.67%.
- 2026 YTD: +31.6%. October 2026: −4.23%.
- Monthly correlation over TEST: 0.78 with Worker 1, 0.37 with Worker 2.

## Verdict
- (a) FAIL: 7 of 8 checks.
- (b) FAIL: TEST geometric month +4.60% is below +5.33%.
- (c) FAIL: 17/24 months up is below 18.
- (d) PASS: positive at doubled costs over 2025–26.

It is not PORTFOLIO-ONLY either, because the plain 50/50 mix stays better on months up (20/24) and on drawdown.

Without leverage, a mix of the two bots can't beat Worker 1's geometric month unless its timing is clearly skilful, and
here it wasn't. It also needs about 2,000 USDT or more (Worker 1's 500 USDT minimum, held in the 25% sleeve).

Files: `PREREG.md`, `data.py`, `alloc.py`, `grid.py`, `run.py`, `dev_grid.json`, `results.json`, `run_test.log`.
