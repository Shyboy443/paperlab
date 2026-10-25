# Worker 2 research round 2 (2026-10-25): no replacement found

Bar to replace Worker 2 (BRIEF.md): (a) 8/8 checks at 1x, (b) TEST median >= +2.22% and geo >= +3.02%,
(c) |monthly corr with Worker 1| <= 0.3, (d) works with 500 USDT at 1x.

| Agent | Idea | Checks | TEST median / geo month | W1 corr | 500 USDT | Verdict |
|---|---|---|---|---|---|---|
| session_map | other intraday windows (864 scanned, Bonferroni) | 8/8 | +0.34 / +1.59 | -0.12 | tight | NO (b); also 0.68 monthly corr with current Worker 2 |
| lottery | short MAX/spiky coins, long calm | 0/8 | +2.08 / -0.29 | 0.16 | no | NO |
| attention | ride 3x-volume + big-move shocks 3 days | 7/8 (RAVE = 21% of profit) | -0.45 / +2.92 | -0.12 | yes | NO (a, b) |
| funding_clock | drift around 8h funding stamps | 5/8 (TEST -56%) | -4.24 / -3.33 | -0.26 | yes | NO |
| oi_flow | OI-confirmed 7d momentum, long/short | 7/8 (ENJ = 31% of profit) | +11.34 / +7.44 | 0.18 | conditional | NO (a); DEV weak (t 1.38), profit from a few new-listing moves |

Post-hoc leads (not eligible, would need a fresh forward test): reversed lottery sort (TEST geo +3.6%, 7/8, corr W1 0.43),
oi_flow with a per-coin cap. Decision: current Worker 2 (rule P) stays. Details in each folder (some agents could not
write REPORT.md; their final messages are summarised here).
