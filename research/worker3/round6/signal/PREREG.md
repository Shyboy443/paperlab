# Worker 3 round 6 — `signal` family: PREREG (written before any DEV run of the variants)

Base = the built rule (round5 high52_plus `selected.cfg`: LS, daily, 30d channel, top-20 universe, 5/5, 1x).
Engine: `lib6.py` = lib5 + two options (cfg `age`, per-leg `long_score`/`short_score`); default path verified
bit-identical: DEV geo +5.13 / TEST +10.33 / HOLDOUT +5.18 (repro.py).
Scores: `sig.py`. All signals use data up to the previous close.

## Stage 1 grid (15 configs, DEV 2021-01..2024-10 only)
| id | spec | idea / prior |
|---|---|---|
| 0 | base | the current rule |
| A1 | ratio | channel position of coin/BTC close ratio (residual vs market) |
| A2 | avgratio | mean of raw and A1 positions (combined rank) |
| A3 | blitz | channel position of the cumulative residual-return path, beta to BTC from a 90d window (Blitz-Huij-Martens residual momentum) |
| B1 | volsc | (position - 0.5) / 30d vol (risk-adjusted channel distance) |
| B2a/b/c | jump=0.4/0.5/0.6 | exclude coins (both legs) whose largest 1-day |log move| in 30d > X of log(30d high/low) — anti-lottery |
| B3 | jumpup=0.5 | long leg only: exclude coins whose largest up-day > 50% of the range |
| C1 | s7neg | short only coins with a negative 7-day return |
| C2/C3 | schan=60/90 | short leg ranked on the 60/90-day channel (longer lookback avoids short-term-reversal bounces) |
| D1/D2/D3 | age=60/90/180 | universe = top-20 by volume among perps listed >= N days |

## Selection rule (fixed now)
- Eligible: DEV net at 2x cost > 0, DEV max DD <= 30%, DEV months up >= 25/46, largest single position <= 15% of profit.
- "Improves on base" on DEV: geo >= base geo + 0.25, OR (geo >= base geo - 0.15 AND max DD <= base DD - 4).
- Stage 2: if >= 2 families (A/B/C/D) have an improving member, test the combinations of each family's best improving
  member (pairs + all-together; at most 8 configs). A combo replaces a single variant only if it improves on the best
  single by geo >= +0.25 (or same geo within 0.15 with DD 3 points lower) — otherwise keep the simpler one.
- Pick the improving config with the highest DEV geo (ties: lower DD). If nothing improves → selected = base → FAIL,
  and TEST/HOLDOUT are not run for a variant.
- Neighbour grid for check 5 = the round-5 grid (lscale, k, every, univ) applied on top of the variant, plus the
  variant's own parameter neighbours (listed in the addendum).

## Addendum (after DEV, before TEST and HOLDOUT)
- DEV ran 15 stage-1 configs (devtable.md). Only **C1 (s7neg)** improves on base under the rule (geo +5.75 vs +5.13,
  DD 27.3% vs 26.3%). One family improving → no stage 2. **Total configs: 15.**
- **SELECTED: C1** = the built rule, but the short leg only takes coins from the bottom of the 30d channel that also have
  a negative 7-day return (close i-1 vs close i-8). If fewer than 5 qualify, the short leg holds fewer names at 0.1
  each (gross < 1x, never more).
- Known side effect (diagnosed on DEV, not tuned): the bottom-5 hold on average 3.4 names with ret7<0, and on 28% of
  DEV days fewer than 5 shortable names exist → C1 carries a partial net-long tilt.
- Neighbour grid (check 5, 20 points): lscale 0.5/0.75/1.25/1.5, k 3/4/6/8, every 2/3/5/7, univ 10/15/25/30 (all with
  the C1 filter), plus the return lookback 3/5/10/14 days.
- Random books (check 6): random scores with the same ret7<0 filter on the short leg.
- TEST once, HOLDOUT once, plus 2x costs, with final.py. The verdict uses the brief's IMPROVED / BETTER-RISK / FAIL bar.
