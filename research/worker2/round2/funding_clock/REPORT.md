# Worker 2 round 2 / funding_clock: FAIL (5/8 checks, TEST negative)

**Idea.** Around each funding stamp (00/08/16 UTC), traders on the paying side close just before and reopen just after.
Rule FC_SEL (pre-registered in PREREG.md, chosen on DEV only): top-40 perps; f = last 8h-equivalent funding known 3 h
before the stamp; |f| >= 0.10%. Negative f: long s-3h -> stamp, then short stamp -> s+2h. Positive f: long stamp -> s+2h.
Slots K=5, 1x, market orders, 0.05% fee + 0.02% slip per side, exact funding (stamp counted only if held across it).

| 1x, baseline costs | median mo | geo mo | mean mo | max DD | t | trades/mo |
|---|---|---|---|---|---|---|
| DEV 2021-01..2024-10 | +3.22% | +8.18% | +9.88% | 22.3% | 2.97 | 229 |
| TEST 2024-11..2026-10 | -4.24% | -3.33% | -1.74% | 66.0% | -0.47 | 436 |
| Full | +1.92% | +5.13% | +6.89% | 53.6% | 2.66 | 300 |

- Doubled costs: TEST -97% net; 2025-01..2026-10 median -10.7%/month. 2026 YTD -9.3%; October 2026 -0.4% (4 trades).
- Conservative funding convention (stamps at the entry/exit bars charged when they cost): full period net -68%, so even
  the DEV edge depends on executing within seconds of the stamp.
- Checks: 1 FAIL (TEST -55.6%, 2x -97.2%), 2 pass, 3 pass (t 2.66, boot p5 +2.85), 4 pass, 5 FAIL (0/15 neighbours positive on
  TEST; all 15 positive on DEV), 6 pass (beats 100% of random coin picks, which lose -91% median), 7 FAIL (one position =
  16.5% of profit, SIRENUSDT 2026-04-04), 8 pass. 5/8.
- TEST legs: negative-funding legs -65.8%; positive-funding post-stamp leg +32.6% (median month ~0, t 0.7).
- Worker 1 correlation: monthly -0.26 (2022-01..2026-10), daily +0.03. 500 USDT: smallest order 12.50 USDT (40 positions max) - feasible.
- Why it died: in 2025-26 extreme negative funding became far more common (trades/month nearly doubled, many coins on
  1h/4h funding) and the pre/post-stamp drift no longer covers costs. The DEV pattern was real in 2021-24 but did not persist.

**Verdict: NO.** Fails (a) and (b); passes (c) and (d).
