# Corrected QQQ published-source evaluation — FAIL

The first approximation was not source-equivalent. v2 corrects the mismatches and separately archives v1.
The full published Pine source and Python signal engine matched on 311,520 comparisons over the same 2024–2025 QQQ candles.

| Experiment | Monthly average 2024–25 | Total net | Max DD | Arrows | Trades | Doubled-cost total |
|---|---:|---:|---:|---:|---:|---:|
| QQQ-15m-previous-5bars | -0.1310% | -3.1073% | 3.3194% | 25 | 22 | -4.0737% |
| QQQ-15m-developing-5bars | -0.1151% | -2.7495% | 3.0560% | 43 | 40 | -4.8650% |
| QQQ-30m-previous-5bars | -0.1063% | -2.5281% | 2.7658% | 16 | 14 | -2.9717% |
| QQQ-30m-developing-5bars | -0.0621% | -1.4844% | 1.8276% | 23 | 18 | -2.4426% |
| QQQ-15m-previous-10bars | -0.1414% | -3.3502% | 4.0267% | 31 | 27 | -4.4212% |
| QQQ-15m-developing-10bars | -0.1180% | -2.8228% | 3.1291% | 50 | 47 | -5.2723% |
| QQQ-30m-previous-10bars | -0.1054% | -2.5079% | 3.3476% | 21 | 18 | -3.1233% |
| QQQ-30m-developing-10bars | -0.0920% | -2.1905% | 2.3243% | 27 | 21 | -3.2442% |

Default worst month: -0.8707%; annualized: -1.5686%; Sharpe: -1.6506.

All eight configurations lost after costs in this comparison. Default reasons: Nonpositive net comparison return; Loses with doubled cost allowance; Fewer than 30 completed comparison trades.
Good chart examples are compatible with these results; they neither establish an all-history edge nor show that the signals are fake.

These are observed historical results, not expected future income. QQQ regular sessions and volume differ from the video’s 18:00 Nasdaq futures profile.
The video-linked file is an indicator; our next-open entries, opposite-boundary target, sizing and session flatten are separately stated assumptions.
v2 keeps every indicator arrow even if a trade is rejected or a position is already open. Candle replay lets the reader inspect them.

The source’s radial profile expansion can undershoot its configured 70% coverage; actual coverage is disclosed rather than silently changing its math.
New forward books use fresh start times and delayed SIP observations; no broker orders. Existing live stock execution is unchanged.

[Methods and corrections](QQQ_VALUE_REVERSAL_V2.md). Full JSON, curves, monthly table, source files and replay are available on the hosted page.
