# Worker 3 research round 1 (2026-10-26): no candidate passes

The bar is in BRIEF.md: 8/8 checks, TEST geometric month > +5.33%, TEST median month > +3.84%, ≥ 18/24 months up,
max DD ≤ 23%, works from 500 USDT.

| Agent | Idea | Checks | TEST median / geo month | Months up | Max DD | W1 corr (TEST) | Verdict |
|---|---|---|---|---|---|---|---|
| meta_allocator | relative-momentum mix of W1/W2 | 7/8 (random allocators) | +4.11 / +4.60 | 17/24 | 11.9% | 0.78 | FAIL |
| residual_reversal | 14d residual momentum (reversal dead) | 7/8 (PIPPIN 24.5%) | +4.75 / +4.45 | 16/24 | 50.9% | 0.53 | FAIL |
| options_iv | Deribit DVOL 7d change | 4/8 | −0.99 / −2.25 | 7/24 | 44.6% | 0.31 | FAIL |
| lit_scout | stablecoin float timer | 4/8 | 0.00 / +0.33 | 9/24 | 50.1% | 0.23 | FAIL |
| info_discreteness | Clenow trend-score L/S | 7/8 (30.7% one position) | −0.49 / +1.99 | 12/24 | 37.2% | 0.10 | FAIL |
| token_unlocks | short before insider cliffs, BTC hedge | 6/8 | +5.73 / −0.39 | 15/24 | 69.2% | −0.26 | FAIL |

**No candidate is PORTFOLIO-ONLY.** Each third leg lowers months up or raises drawdown compared with the plain 50/50
W1+W2 mix (median +3.54%, geo +4.48%, 20/24 months up, monthly DD 5.9%).

## Lessons for round 2
- **1x cap:** under it, a mix can only beat Worker 1's geometric month with real timing skill, and none was found.
- **Short horizons:** crypto residual moves continue rather than revert over 1–7 days.
- **Leaks from 2021–24 into 2025–26:** options-vol and stablecoin signals flipped or faded in 2025–26.
- **Squeeze risk:** short-side event trades in thin new tokens carry fat-tail squeezes.

## Post-hoc leads
These are not eligible; each would need a forward test:
- trend only when DVOL z-score < 0.5 (options_iv);
- unlock shorts with a squeeze filter.
