# Worker 2 research (2026-10-25)

Goal: a second live crypto strategy that behaves unlike Worker 1 (the Ensemble: rotation + ML + 60-day momentum).
Two round-4 candidates had a low correlation with Worker 1 and failed only luck check 7 (single-position concentration).
Each got one concentration fix, written in its PREREG.md before any variant ran, and was re-run through all 8 checks
(research/monthly20/ROBUSTNESS.md) at 1x.

Caveat for both: the r4 round had already used TEST (2024-11-01..2026-10-21) once, and the fixes were chosen knowing
check 7 had failed. A pass here is weaker evidence than a first-time holdout pass.

## Result

| | Afternoon momentum (session/) | Retail contrarian (positioning/) |
|---|---|---|
| Fix | size x min(1, 1.154% / coin's 480 h hourly vol) | 1/vol sizes, 10% cap, daily trim |
| Luck checks | **8 / 8 PASS** | 7 / 8 FAIL (biggest position 16.2% of profit, limit 10%) |
| TEST median / geo month | +2.22 / +3.02 % | +3.01 / +3.60 % |
| Full median / geo month | +4.44 / +5.35 % | +0.78 / +2.97 % |
| Worst month / max DD (full) | -17.4 % / 20.3 % | -13.2 % / 34.2 % |
| Correlation with Worker 1 (monthly / daily) | -0.06 / +0.04 (2022-26) | -0.15 / +0.09 (2022-26) |
| 50/50 with Worker 1: geo / max DD (full) | 4.71 % / 11.4 % | 3.54 % / 16.9 % |
| 500 USDT size | OK (3 of 1,240 orders skipped, delisting coins) | OK (2.9% skipped, mostly BTC) |

## Choice: afternoon momentum = Worker 2

- Rule: every day, among the point-in-time top-40 USDT perps excluding BTC/ETH, take alts that moved >= 2 of their own
  vol units between 00:00 and 16:00 UTC. Ride the move from 16:00 to 02:00 UTC, at most 5 positions, 1x, sizes
  scaled down for wild coins.
- **Weakness:** the edge is decaying. Geo month by year: 2021 9.96, 2022 6.24, 2023 5.57, 2024 4.67, 2025 2.28,
  2026 2.94. 2025-26 alone is still positive at doubled costs: +45%, geo 1.70.
- Cost check: the model uses 0.05% fee + 0.02% slippage per side. A 100 USDT market order showed a median of 0.55 bps
  of book cost in one order-book snapshot. The doubled-cost run still passes.

Numbers: session/results.json, session/run_output.txt, positioning/results.json, positioning/extra.json.
