# PREREG: w1_upgrade (Worker 3 round 2). Part 1 was written 2026-10-26 before any run of a variant.

## Goal
Improve Worker 1 itself (r4_07 ensemble: legs R = weekly 14d rotation, M = ML walk-forward, T = 60d momentum;
inverse-vol leg weights over 60 days, re-set every 7 days; 1x) with a few literature-backed changes, without fishing
for parameters. Code: `ens.py` is a copy of `research/monthly20/r4_07_ensemble/ens.py`. The only addition is
`simulate(pos_out=...)`, which records the net positions. `up.py` builds the legs exactly as r4_07's run.py does.
The baseline must reproduce Worker 1's TEST result (median +3.84%, geo +5.33%) before anything else runs.

All changes keep the account at 1x gross or less: leg weights sum to 1 or less, so a change can only cut exposure,
re-weight it, or fill idle capacity. Costs: 0.05% fee + 0.02% slippage per side, funding included, plus a doubled-cost
run. A changed weight re-sizes the sleeves only on W1's own weekly re-set days (no extra re-balance schedule).

## The four pre-registered changes (each tested alone on DEV = 2021-01..2024-10)
**V1. Volatility-scaled legs** (Barroso & Santa-Clara 2015, "Momentum has its moments", JFE 116(1); crypto replication:
Grobys et al. 2025, "Cryptocurrency momentum has (not) its moments", FMPM, https://link.springer.com/article/10.1007/s11408-025-00474-9
which finds vol management softens crypto momentum crashes).
On each weekly re-set day d, for each leg i, take sigma_i = the sd of the leg's stand-alone 1x daily returns over the
trailing 126 days (rows < d, at least 60 rows). BSC used 6 months. ref_i = the expanding median of sigma_i over earlier
re-set days (at least 8 values). Then s_i = min(1, ref_i / sigma_i), and the leg's weight is w_IV,i x s_i. A leg is cut
when its own volatility is above its normal level. It is never levered above its W1 weight.
Neighbours: vol window {63, 94, 158, 189}; re-set every {3, 14} days; ref = expanding mean; ref = expanding 25th and
75th percentile; IV lookback {30, 90}. That makes 11.

**V2. Momentum crash protection** (Daniel & Moskowitz 2016, "Momentum crashes", JFE 122(2): momentum crashes cluster
in market rebounds that follow bear states, when the loser/short side rallies).
Market = BTC. On each re-set day, bear = BTC close(d-1) / close(d-1-365) < 1, and rebound = BTC close(d-1) / close(d-31) > 1.
If bear AND rebound, the two momentum legs (R and T) get weight x 0.5. M is unchanged and the cash is left idle.
Neighbours: bear look {180, 270, 540, 730}; rebound look {14, 21, 45, 60}; cut factor {0, 0.25, 0.75}; bear only
(no rebound condition). That makes 12.

**V3. Leg weights by trailing variance** (Moreira & Muir 2017, "Volatility-managed portfolios", JF 72(4): weights
proportional to 1/variance). W1 uses 1/sd. V3 uses w_i proportional to 1/var over the same 60 days, re-set every 7 days.
Neighbours: lookback {30, 45, 75, 90}; re-set every {1, 3, 14, 30}. That makes 8.

**V4. Worker 2 as a 4th leg in the same account, under a joint 1x gross cap.** W1 keeps its full book. Worker 2's
primary rule P (`research/worker2/session/core.py`, sig_ref 1.154%, 16:00 to 02:00 UTC alts) is added only into the
gross headroom that is left. On W2 signal day d, x_j = W2's own notional/equity per trade. W1's net notional/equity per
coin n_j is taken after day d's 00:00 re-size, and G1 = sum |n_j|. The extra gross is dG = sum |n_j + s_j x_j| - |n_j|.
Scale f = 1 if dG <= 1 - G1, else max(0, 1 - G1) / dG. The book's daily return is r_W1(d) + sum_j f x_j ret_j.
ret_j is W2's net trade return after its own costs, and no netting saving is credited (conservative). The return is
booked on the entry day. This is a daily-return approximation of one account: W2's P&L does not re-size W1 within the week.
Neighbours: W2 K {3, 4, 6, 8}; W2 k {1.5, 2.5}; W2 entry hour H {12, 20}; W2 hold {8, 12}; W1 weights x 0.75, which
frees at least 0.25 of gross, with W2 at its own size and capped by the headroom (clarified before any run); W2 without vol scaling (sig_ref None). That makes 12.

## DEV selection rule (fixed now)
W1 on DEV: median +1.87%, geo +2.96%, max DD 20.21%. A change QUALIFIES if all of these hold on DEV:
DEV geo month > W1's; DEV median month >= W1's; DEV max DD <= W1's + 2 pp; at least 70% of its DEV neighbours have a
positive DEV net; and DEV geo >= 0.5 x DEV mean.
Qualifiers are ranked by DEV geo-month uplift. TEST (run once):
- PRIMARY = the top qualifier alone;
- SECONDARY = the top two combined, but only if two qualify (V1/V2 multiply the weights, V3 replaces the IV base,
  V4 adds on top).
No qualifier means no TEST run of any variant, and the verdict is FAIL.
The 8 checks (ROBUSTNESS.md, done exactly as r4_07's run_test.py) are run on PRIMARY, and on SECONDARY if it exists.
Check 5 uses the neighbour grid above (net > 0 on DEV and TEST). Check 6 uses 200 random versions with every leg's
coins drawn at random (r4_07's procedure; for V4, W2 also gets random coins from the same day's eligible alts, with the
same sides and timing) and the variant's own weight path. Check 7 drops the top 5 coins from every leg (V4: from W2 too).
Head-to-head with W1 on the same DEV/TEST/full windows.

## Part 2: DEV result and TEST selection (written 2026-10-26 after `python up.py dev`, before any TEST run of a variant)
A bug was fixed before selection: an empty parameter dict made V1 and V2 silently equal W1 (`if v.get("v1")`).
After the fix the DEV numbers below came out; the selection is unchanged. Full numbers are in dev.json.

| DEV 2021-01..2024-10, 1x | median | geo | mean | max DD | t | neighbours net>0 | qualifies |
|---|---|---|---|---|---|---|---|
| W1 (baseline) | 1.87 | 2.96 | 3.16 | 20.2 | 3.32 | - | - |
| V1 vol-scaled legs | 1.83 | 2.93 | 3.12 | 20.7 | 3.29 | 11/11 | no (geo, median) |
| V2 crash protection | 1.92 | 2.92 | 3.10 | 19.7 | 3.34 | 12/12 | no (geo) |
| V3 inverse-variance | 2.39 | 3.37 | 3.56 | 19.5 | 3.75 | 8/8 | yes (+0.41) |
| V4 W2 in headroom | 5.69 | 5.56 | 5.87 | 16.8 | 4.82 | 12/12 | yes (+2.60) |

On DEV, W1's net gross after the 00:00 re-size averages 0.84 (p90 0.94). W2 asks for 0.36 of gross on a signal day,
so W2 runs at roughly 45% of its own size inside the headroom.

**PRIMARY = V4** (W1 unchanged, plus W2 rule P in the gross headroom). **SECONDARY = V4 + V3** (the same, with
inverse-variance leg weights).
SECONDARY check-5 grid: the 12 V4 neighbours on the V3 base plus the 8 V3 neighbours with W2 added, 20 in total.

Honest caveat: TEST is not unseen for either part. W1's and W2's TEST results were already published, and only the
way they are combined in one account is new. A PASS here is therefore weaker evidence than a fresh signal and needs
a forward paper test.
