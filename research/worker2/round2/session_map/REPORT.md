# Worker 2 round 2 / session_map: REPORT (2026-10-25)

**Idea.** Map every intraday session handover (entry hour E every 2 h, look-back 4-24 h, hold 4-12 h, momentum or
reversal, all/weekday/weekend days = 864 cells) on DEV, keep only cells that clear a Bonferroni bar (daily t >= 3.85),
then pick one by DEV geo month. Code: core2.py, scan_dev.py, run_test.py. Pre-registration: PREREG.md.

**The map (DEV 2021-01..2024-10).** No edge at the Asia (00:00) or Europe (06:00-10:00) handovers, no edge in any reversal
cell (many significantly negative), weekend-only cells weak. Every cell with t > 2 is *momentum entered 12:00-22:00 UTC*,
i.e. the US session, which is Worker 2's own neighbourhood.

**Selected:** E=14, H=8, X=12, mom, all days (06:00-14:00 UTC move >= 2 vol-units, ride 14:00 -> 02:00), k 2, K 5, W 480,
vol-scaled sizes (sig_ref 1.154%), 1x.

| 1x | median | geo | mean | max DD | t | trades/mo |
|---|---|---|---|---|---|---|
| DEV | +5.92 | +6.29 | +6.68 | 22.0 | 4.75 | 44.7 |
| TEST | +0.34 | +1.59 | +1.88 | 26.0 | 1.18 | 41.5 |
| full | +4.41 | +4.66 | +5.04 | 25.7 | 4.59 | 43.6 |

8/8 checks pass (2025 -6.3%; t 4.59, boot p5 +2.9; neighbours 91.7%; beats 100% of random; check 7 max position 8.5%).
2025-01..2026-10 at doubled costs: +20.8% (median month -0.11). 2026 YTD +50.4%. October 2026 -0.95%.
Worker 1 correlation: monthly -0.12 (2022-26), daily +0.03. **Current Worker 2 correlation: daily +0.47, monthly +0.68** -
it is largely the same afternoon-momentum trade entered two hours earlier. 500 USDT: smallest TEST order 6.2 USDT (tight but >= 5).

**Verdict: NO.** Fails bar (b): TEST median +0.34% < +2.22% and TEST geo +1.59% < +3.02%; it decayed like Worker 2 and
is a close relative of it, not a new, independent Worker 2.
