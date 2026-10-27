# tug_of_war (Worker 3 round 3): FAIL, 1/8 checks

## Idea
The analogue of Lou, Polk & Skouras (2019, JFE) "tug of war" in the crypto cross-section. Each coin's past return is split by
trader-geography session (UTC): Asia 00-08, Europe 08-14, US 14-24. Coins are then sorted on one session's cumulative return,
or on a session minus another session or minus the total.

No direct crypto replication of the cross-sectional split exists. The indirect evidence is in PREREG.md:
- Wen et al. (2022, NAJEF) on BTC intraday momentum and reversal;
- MDPI JRFM (2026) on day/overnight sessions in BTC/ETH;
- RQFA "tea time" on session patterns;
- a Galaxy note on BTC gains coming in Asia hours.

Data: survivorship-free hourly panel, top-40, unchanged agent-02 engine, 1x, fee 0.05% + slip 0.02% per side, funding included.

## DEV search
304 configs on DEV only (`dev_grid.log`):
- 9 signals x momentum or reversal x L {7,14,30,60} x k {5,10} x rebalance {1,7};
- plus 16 total-momentum references.

What DEV showed:
- No plain session-reversal leg: 0/16 for A, E and U reversal.
- "E-T reversal" was positive in 16/16 configs: long the coins whose momentum came outside European hours, short those
  whose momentum came in European hours.

Selected by the fixed rule: **E-T, reversal, L = 14 days, 5 long / 5 short, weekly rebalance, top-40.**

## Results (1x, baseline costs)
| | median month | geo month | mean | months up | max DD | t | trades/mo |
|---|---|---|---|---|---|---|---|
| DEV 2021-01..2024-10 | +4.53% | +3.65% | +4.23% | 30/46 | 29.2% | 2.60 | 27.7 |
| **TEST 2024-11..2026-10** | **−3.87%** | **−4.34%** | −3.23% | 10/24 | 78.7% | −1.07 | 29.2 |
| Full 2021-01..2026-10 | +2.37% | +0.89% | +1.73% | 39/70 | 78.7% | 1.11 | 28.6 |

- TEST net: −65.5%, and −68.8% at doubled costs.
- 2025-01..2026-10 at doubled costs: negative.
- 2026 YTD: −74.2%. October 2026: −13.5%.
- Calendar years: 2021 +195%, 2022 +21%, 2023 +45%, 2024 +12%, 2025 +24%, 2026 −74%. The DEV edge was mostly 2021, and it
  broke hard in 2026.

## Checks: 1/8
| Check | Result | Numbers |
|---|---|---|
| 1. Both halves | FAIL | TEST net −65.5% (−68.8% at 2x costs) |
| 2. Most years | FAIL | 2022-01..2026-10 net −36.9% |
| 3. Statistically real | FAIL | t 1.11, bootstrap 5th percentile −0.73 |
| 4. Not a few months | FAIL | −16.4% without the 3 best months |
| 5. Not a knife edge | FAIL | 3/16 neighbours = 18.8% (only rebalance 1, 3, 5 days) |
| 6. Beats chance | FAIL | beats 93.0% of daily random draws and 88.2% of 30-day-block draws |
| 7. Not a few coins | FAIL | −38.0% without PIPPIN, SIREN, TRB, NEIRO, DOGE; largest position = 241% of net profit |
| 8. Compounds | PASS | geo +0.89% ≥ half of mean +1.73% |

Check 5 note: the top-50 and top-60 neighbours equal the top-40 run, because the score only exists for hourly-panel top-40
coins (stated in PREREG).

## Correlation and portfolio
| Correlation | Worker 1 | Worker 2 |
|---|---|---|
| Monthly, TEST | 0.22 | −0.17 |
| Monthly, full | 0.18 | −0.02 |
| Daily, full | 0.28 | not computed |

Daily correlation with 14-day total momentum (same k and rebalance): 0.48.

TEST portfolio, W1 + W2 + X in thirds:
- median +2.15%, geo +1.82%, 13/24 months up, monthly DD 25.5%;
- against W1 + W2 at 50/50: +3.54% / +4.48%, 20/24, 5.9%;
- so it is **not PORTFOLIO-ONLY**.

## 500 USDT
10 legs of 50 USDT each, well above the 5 USDT minimum for alts. BTC legs (1.3% of positions) are below BTC's 100 USDT
minimum. The size is workable, but moot.

## Verdict
**FAIL** on (a) 1/8, (b), (c) and (d).

The session-split sort had a strong DEV look (16/16 positive) and collapsed out of sample. This is the same 2021-24 → 2025-26
fade seen in rounds 1-2, here worse than random. No post-hoc variants were run on TEST.

## Files
- `lib.py`: session scores.
- `dev_grid.py`, `dev_grid.json`, `dev_grid.log`: DEV grid.
- `PREREG.md`.
- `run.py`, `run.log`, `results.json`: single TEST run and checks.
