# Worker 2 round 2 / lottery (MAX / skewness): FAIL (0/8). Verdict: NO

**Idea.** Retail overpays for lottery coins, so short the top-40 coins with the biggest recent max return / skewness and
go long the least lottery-like. Weekly, market-neutral, 1x, equal-risk legs, funding included (PREREG.md).

**DEV grid (36 configs, dev_grid.json).** No edge: only 1/36 passed the pre-declared filter (net > 0 at both costs
and median > 0). The winner had DEV t = 0.71. Selected: max1d (largest daily return over the last 14 days), k = 8 per
leg, weekly. **The MAX sort is mostly a low-vol sort**: its rank correlation with 14-day vol is 0.74, 67% of the short
leg is also in the top-8 by vol, and its monthly correlation with the same book sorted on plain low-vol is 0.78.

| 1x, baseline costs | median mo | geo mo | mean mo | max DD | t | trades/mo |
|---|---|---|---|---|---|---|
| DEV 2021-01..2024-10 | +0.31% | +0.59% | +1.12% | 50.0% | 0.71 | 98 |
| TEST 2024-11..2026-10 | +2.08% | -0.29% | +0.27% | 50.3% | 0.12 | 98 |
| Full | +0.94% | +0.29% | +0.84% | 58.1% | 0.66 | 100 |

Checks: 0/8. TEST net is -6.8% (-12.5% at doubled costs). Years: 2022 -7%, 2025 -8%, 2026 -16%. Bootstrap p5 is -1.39.
Without the 3 best months the result is -46%. Only 2/12 neighbours are positive on both halves. It beats only 90% of
random books. Without the top-5 coins it is -15%, and one 1000SHIB position made 108% of the total profit. The geometric
month is 0.29 against a mean of 0.84. Other numbers: 2025-01..2026-10 at doubled costs +21.4%; 2026 YTD -0.1%;
October 2026 +3.3%. Worker 1 correlation: 0.16 monthly over 2022-26, -0.24 daily. For 500 USDT, the smallest order is
2.15 USDT, which is below the 5 USDT minimum.

**Info only, post-hoc (NOT eligible): the reverse sort.** On DEV the vol-scaled lottery score smax (= mean of the
5 biggest days / daily vol) was strongly negative, meaning the lottery coins *out*performed. This held for every L and k
(t -1.5 .. -2.5). Pre-registered as info only, the reversed book (long high smax, short low smax, L14 k8 weekly) gave on
TEST: median +3.74%, geo +3.60%, mean +4.02%, max DD 22.7%. Full period: t 2.65, 7/8 checks. It fails check 7: one
BEATUSDT position made 12.4% of the profit. Its Worker 1 monthly correlation is 0.43 (it behaves like momentum/attention,
so it overlaps Worker 1). Its smallest order is 2.7 USDT. It was chosen after seeing the DEV grid and is the opposite of
the hypothesis, so a fresh pre-registered forward test would be needed. Files: results_info_reversed_smax.json, run_info.py.

Files: core.py (engine), dev.py, lowvol_dev.py, run.py, results.json, PREREG.md.
