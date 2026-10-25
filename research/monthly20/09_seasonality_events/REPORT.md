# Agent 09: time and event effects. Verdict: FAIL

**The idea.** Crypto trades 24/7, but the people trading it work on human calendars. I tested every effect on DEV
(2021-01 to 2024-10): hour of day, day of week, weekends, turn of the month, the hours around funding (00/08/16 UTC),
the US cash open and close, Deribit options-expiry Fridays, CME BTC weekend gaps, and scheduled CPI and FOMC releases.
The CPI and FOMC dates come from bls.gov and federalreserve.gov. Both calendars are published in advance.
The basket was 12 perps listed before 2021 (survivorship bias: all 12 still trade today).

**What DEV showed.**
- Hour, day, weekend, month-turn, funding-hour, US-session and expiry effects are only 1–6 bp per hour. Most of them
  flip sign between 2021–22 and 2023–24, and a 14 bp round trip eats them. The best one, long 21–22 UTC every day,
  lost about 2% a month after costs.
- Three event effects kept the same sign in both DEV halves:
  1. Follow the CME weekend gap for 24 h. Fading it loses.
  2. Be long from 4 h before the FOMC statement until the statement bar closes.
  3. Be long for the 24 h before CPI.
- At 1x these made +1% to +3% a month on DEV. No leverage reached +20%/month on DEV:
  - the stack peaked at +7% a month at 5x, with a 98% drawdown;
  - FOMC alone peaked at +13% a month at 20x, with 29 liquidations.

**TEST (2024-11 to 2026-10), five pre-registered attempts plus one bug-fix attempt. All edges vanished:**

| attempt | lev | TEST mean | median | worst | max DD | 2x-cost mean | trades |
|---|---|---|---|---|---|---|---|
| STACK_1X (prereg) | 1 | +0.03% | −0.03% | −4.85% | 18.4% | −0.26% | 85 |
| STACK_3X (prereg) | 3 | −0.05% | −0.08% | −15.8% | 49.5% | −0.91% | 85 |
| FOMC_1X (prereg) | 1 | −0.11% | 0.00% | −2.16% | 8.4% | −0.20% | 192 |
| FOMC_12X (prereg) | 12 | −1.28% | 0.00% | −25.8% | 68.6% | −2.39% | 192 |
| GAP_1X (prereg) | 1 | +0.04% | −0.10% | −4.82% | 18.4% | −0.23% | 46 |
| STACK_FIXED_1X (post-TEST) | 1 | +0.04% | +0.10% | −7.28% | 23.0% | −0.47% | 538 |
| STACK_FIXED_3X (post-TEST) | 3 | −0.01% | +0.29% | −21.4% | 59.4% | −1.52% | 538 |

**About the bug.** The pre-registered STACK had a column-alignment bug: it only ever traded BTC, so it was really the
gap leg alone. I found this after TEST and fixed it as a new attempt, labelled post-TEST. The fixed version earned
+3.1%/month on DEV and +0.04%/month on TEST, so the bug does not change the verdict.

STACK_FIXED_1X by TEST month (%):
- **2024-11 to 2025-06:** 11.3, 5.6, 3.0, −7.0, −2.5, 7.7, −2.9, −2.2
- **2025-07 to 2026-02:** −6.9, 1.6, 4.4, 2.8, 0.6, 1.2, 2.1, −4.2
- **2026-03 to 2026-10:** −7.3, −3.1, 0.3, −1.1, 0.0, 0.2, −1.9, −1.0

The good start came from November 2024, the election month. After that the stack was flat to negative.

**Leverage for +20%/month.** None exists. The best 1x TEST mean is +0.04% a month, so straight scaling would need
about 500x, which is meaningless. In the post-TEST leverage sweep, every leg's drawdown was over 90% by 8x, and the
legs were wiped out or suffered repeated liquidations at 12x to 20x. Even on DEV, the fixed stack would have needed
about 6.4x. On DEV that leverage meant drawdowns above 80% and liquidations.

**Verdict: FAIL.** Calendar and event effects in crypto are real but tiny (a few bp per hour). The three that
survived costs on DEV went to zero in the last two years. Stacking them does not get anywhere near +20% a month,
and leverage only adds risk of ruin.

Files: `run.py` (reproduces everything; uses `strategies.py`, `engine.py`, `base.py`, `events.py`), `PREREG.md`,
`results.json`, `run_output.txt`; DEV work in `explore.py`/`explore_dev.txt`, `dev_scan.py`/`dev_scan.txt`,
`lev_scan.py`/`lev_scan.txt`.
