# Worker 2 improvement round (2026-10-25) — result: keep rule P

Trigger: paper book -12.7% in October 2026. Diagnosis: the server matches the research (Jul-Sep within a few points,
research October -7.7% to 21 Oct); all 21 October trades lost (moves reversed, choppy market). Inside the normal range
(worst month -17.4%, May 2026 -11.7%).

Two variants were pre-registered in PREREG.md and run through the 8 checks (results.json, run_output.txt):

| 1x | P (live now) | R residual momentum | L no shorts vs BTC uptrend |
|---|---|---|---|
| Luck checks | 8/8 | 8/8 | 8/8 |
| DEV median / geo month | +6.88 / +6.52 | +3.21 / +3.95 | +4.47 / +6.16 |
| TEST median / geo month | +2.22 / +3.02 | +1.61 / +2.11 | +0.72 / +2.92 |
| TEST max DD | 17.3% | 18.9% | 16.4% |
| Full max DD | 20.3% | 25.4% | 20.9% |
| 2025-26 at doubled costs, median | +1.02 | +0.46 | -0.45 |
| Oct 2026 (to 21 Oct) | -7.7 | -8.0 | -4.3 |

Adoption rule (median >= P on DEV and TEST, geo >= P on TEST) fails for both. P stays; nothing redeployed.
L softens October but loses more elsewhere (Jul 2026 -6.9%); choosing it for October would be curve-fitting.
