# PREREG: Worker 2 round 2 / session_map (written 2026-10-25, before any return was computed)

## Hypothesis (first principles)
Crypto trades 24/7 but liquidity and participants rotate: Asia (00:00 UTC), Europe (07:00-08:00), US cash open
(13:30-14:30), US close (20:00-21:00), weekend (thin, retail). A large idiosyncratic alt move made by one session may be
continued (slow diffusion to the next session's traders) or reversed (liquidity-provision / over-shoot) by the next one.
The current Worker 2 (00:00-16:00 move ridden 16:00 -> 02:00, mom) is one cell of this map and is EXCLUDED.

## Rule family (code: core2.py, a generalisation of research/worker2/session/core.py; engine `run` re-used unchanged)
For signal day d (UTC) and entry hour E: entry bar e = d*24 + E (open), look-back window = bars [e-H, e) (may cross
midnight), z_j = log(C[e-1]/O[e-H]) / (sigma_j * sqrt(H)), sigma_j = trailing 480 h hourly vol known at day d 00:00 UTC.
Universe = point-in-time top-40 perps excl. BTC/ETH (ALT_ELIG, survivorship-free). |z| >= k -> position at open of e,
side = sign(z) (mom) or -sign(z) (rev), exit at open of e + X. Max K slots: size = equity/max(n,K) x min(1, 0.01154/sigma_j)
(Worker 2's pre-registered sig_ref, not re-fitted), 1x, 1% of bar quote volume cap, fee 0.05% + slip 0.02% per side,
funding included. Day filter on the entry day: all / weekday (Mon-Fri) / weekend (Sat-Sun).
X <= 24 - (gap) is guaranteed because X <= 12 < 24, so one day's positions never overlap the next day's (gross <= 1x).

## DEV-only scan grid (DEV = 2021-01-01..2024-10-31)
- E (entry hour UTC) in {0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22}  (12)
- H (look-back hours) in {4, 8, 16, 24}                              (4)
- X (hold hours) in {4, 8, 12}                                       (3)
- mode in {mom, rev}                                                 (2)
- days in {all, weekday, weekend}                                    (3)
- fixed: k = 2.0, K = 5, W = 480.
Total N = 864 configs. **Excluded from selection** (Worker 2's own trade or a near copy): E = 16 with mode = mom
(any H/X/days) and E = 18 with mode = mom and H >= 16. They are still scanned and shown for the map.

## Multiple-testing haircut and selection (decided now)
Statistic per config = t-stat of DEV DAILY equity returns (all calendar days, zero on flat days), t = mean/sd*sqrt(n).
Bonferroni bar: one-sided alpha 0.05 / 864 = 5.8e-5 -> **t_DEV >= 3.85**. A config is ELIGIBLE only if
t_DEV >= 3.85 AND DEV monthly t-stat >= 2.0 AND DEV median month > 0 AND DEV doubled-cost net > 0 AND not excluded.
Among eligible configs select the ONE with the highest DEV geometric month; ties -> higher t_DEV.
If NO config is eligible the family is a FAIL on DEV, TEST is not run for selection purposes (the best-t config's TEST
is then shown as information only and labelled as such).

## Check 5 neighbour grid (fixed now, relative to the selected config)
H x {0.5, 0.75, 1.25, 1.5} (rounded, >= 2), X x {0.5, 0.75, 1.25, 1.5} (rounded, >= 2), k in {1.0, 1.5, 2.5, 3.0},
K in {3, 4, 6, 8}, W in {240, 360, 600, 720}, and other rebalance times: E +-1, +-2 h. 24 neighbours; >= 70% must be
net positive on both DEV and TEST at baseline costs.

## Judging
Exactly ROBUSTNESS.md checks 1-8 at 1x (same implementation as research/worker2/session/run.py: bootstrap seed 7,
200 random-coin books sized by the same rule, top-5 coin removal, 10% single-position share in dollar P&L).
Replacement bar (BRIEF.md): 8/8, TEST median >= +2.22%, TEST geo >= +3.02%, |monthly corr W1| <= 0.3 (2022-01..2026-10),
500 USDT tradable. Also reported: daily correlation with the current Worker 2 book (sim(dict(SEL, sig_ref=0.01154))),
2025-01..2026-10 at doubled costs, 2026 YTD, October 2026, trades/month, smallest order at 500 USDT.
TEST (2024-11-01..2026-10-21) is run ONCE for the selected config. Nothing is tuned on TEST.

## Selection (appended after the DEV scan, BEFORE any TEST run)
DEV scan (scan_dev.json): 864 configs, 4 eligible (t_DEV >= 3.85 etc.). Selected by the rule above (highest DEV geo):
**E = 14, H = 8, X = 12, mode = mom, days = all** (06:00-14:00 UTC move, enter 14:00, exit 02:00), k 2.0, K 5, W 480,
sig_ref 1.154%. DEV: t_daily 4.85, t_month 4.75, median +5.92%, geo +6.29%, max DD 22.0%, 2x-cost net +1071%.
Map honesty note: every cell with DEV t > 2 is momentum entered 12:00-22:00 UTC; Asia/Europe handovers (E 0-10) and all
reversal cells have no DEV edge (many significantly negative). The selected cell holds 14:00 -> 02:00, which CONTAINS the
current Worker 2 holding window (16:00 -> 02:00), so it is expected to be highly correlated with Worker 2. Run TEST once.
