# PREREG - Worker 3 round 1 / residual_reversal (written 2026-10-26 after the DEV grid, BEFORE any TEST run)

## Idea and sources
Market-neutral cross-sectional sorts on BTC-beta-adjusted (residual) daily returns, daily-to-weekly horizon:
- Short-term residual reversal: Blitz, Huij, Lansdorp & Verbeek (2013), "Short-term residual reversal", J. Financial Markets.
- Idiosyncratic (residual) momentum: Blitz, Huij & Martens (2011), "Residual momentum", J. Empirical Finance.
- Crypto evidence: Liu, Tsyvinski & Wu (2022), "Common Risk Factors in Cryptocurrency", J. Finance (market, size and
  1-4 week momentum factors); Dobrynskaya (2023), "Cryptocurrency Momentum and Reversal", J. Alternative Investments,
  SSRN 3913263 (momentum up to 2-4 weeks, reversal only after about 1 month); Drogen, Hoffstein & Otte (2023),
  "Cross-sectional Momentum in Cryptocurrency Markets", SSRN 4322637 (30d formation, 7d persistence);
  "Up or down? Short-term reversal, momentum, and liquidity effects in cryptocurrency markets", Finance Research Letters
  2021 (large liquid coins show daily momentum, not reversal); Fieberg, Liedtke, Metko & Zaremba (2023),
  "Cryptocurrency factor momentum", Quantitative Finance. No crypto paper on residual momentum was found (searched);
  in equities, ranking on residuals roughly halves momentum's crash risk.

## Engine (core.py)
Survivorship-free hourly top-40 panel (r4_09_session/base.py, incl. delisted coins, hourly funding). At day d's 00:00 UTC
open: daily log returns of past days; beta_j to BTC = OLS over the last B days; resid = r_j - beta_j r_BTC; sres = std(resid).
rrev = sum of resid over L days / sres (long lowest). rmom = sum of resid over days d-L-s+1..d-s / sres (long highest).
Universe U40 (top-40 by 30d quote volume, point in time). Long k, short k, dollar-neutral equal weight 0.5x per leg, 1x gross.
Rebalance every R days at the open; costs 0.05% + 0.02% per side on traded notional (drifted vs target), hourly funding.
sim() is copied verbatim from research/worker2/round2/lottery/core.py.

## DEV grid (dev_grid.py -> dev_grid.json / dev_grid.txt, all 94 rows kept)
rrev: L {1,2,3,5,7} x R {1,3,7} x k {5,8} (30 rows); rmom: L {14,21,30,60} x skip {0,3} x R {3,7} x k {5,8} (32 rows);
raw_mom: the same 32 rows, info only, not selectable. B = 60 fixed. Rule fixed in the script before it ran:
eligible = rrev/rmom with DEV net > 0 at baseline AND doubled costs AND DEV median > 0; take the highest DEV t.
(Before the grid, two DEV-only smoke tests were run: rrev L3 R3 k8 and rmom L30 R7 k8.)

### DEV findings
- Residual REVERSAL fails everywhere: 0/30 eligible, 29/30 net negative, t down to -4.8. At 1-7 day horizons crypto
  residuals CONTINUE (as r4_01 found for raw returns). The brief's primary hypothesis is rejected on DEV already.
- Residual MOMENTUM: 25/32 eligible; L 14-30 works, L 60 weak. It is NOT better than plain raw momentum on DEV
  (best raw_mom t 3.25 vs best rmom t 2.82): the residual adjustment adds no value here.

## The ONE selected config (frozen)
feat = rmom, L = 14 days, skip = 0, B = 60 days, k = 8 per leg, R = 7 days, equal weight, dollar-neutral, U40.
DEV 1x: median +3.93%, geo +3.53%, mean +3.93%, net +410%, doubled-cost net +330%, max DD 25.1%, t 2.82, 28/46 up.
Already visible on DEV: geo +3.53% is below the Worker 1 bar (+5.33%), so passing (b) would need TEST > DEV.

## TEST 2024-11-01..2026-10-21: run ONCE (baseline + doubled costs). No re-tuning.

## Checks (ROBUSTNESS.md, 1x, 2021-01..2026-10), implemented as in research/worker2/round2/lottery/run.py
1-4 and 8 as written. 5: one parameter at a time from (L14, k8, R7, B60): L {7,10,18,21}; k {4,6,10,12}; R {3,5,10,14};
B {30,45,75,90} -> 16 neighbours; PASS if >= 70% are net > 0 on both DEV and TEST at baseline.
6: 200 random books (seeds 0..199), same eligibility, k=8, equal weight, rebalance dates and costs; PASS if beats >= 95%.
7: remove the 5 coins with the largest whole-period P&L from the universe -> full net > 0; and no single position
(one coin, one continuous holding) > 10% of total profit.
Also reported: Worker 1 / Worker 2 monthly correlation, 2025-01..2026-10 at doubled costs, 2026 YTD, Oct 2026, trades/mo,
500 USDT smallest order, and the equal-weight W1+W2+X monthly mix on TEST vs W1+W2.
