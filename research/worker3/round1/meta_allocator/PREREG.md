# Worker 3 round 1: meta_allocator, pre-registration

Written 2026-10-26 BEFORE any TEST (2024-11-01..2026-10-21) number of any allocator was computed.
Part A (rule family, grid, selection criterion, checks) is written before the DEV grid is run. Part B (the ONE selected
config + its neighbour grid) is appended after the DEV run and before TEST. Neither part is edited afterwards.

## Idea
A smarter portfolio of the two running bots (Worker 1 = r4_07 ensemble daily book at 1x; Worker 2 = rule P session
momentum at 1x, small-account / no volume cap) plus cash, and optionally a third sleeve, a BTCUSDT-perp long/flat
trend at 1x (close > SMA_N). Weights are walk-forward: the weight for day t uses sleeve returns through day t-2 only
(Worker 2's day t-1 trade closes at 02:00 on day t, so t-1 is not safely known at 00:00). Gross <= 1 (sum of sleeve
weights <= 1, rest cash at 0%), no leverage, no shorting. Rebalance at most daily; weights drift between rebalances.
Rebalancing cost 0.07% per unit of turnover (sum |target - drifted weight|). Sleeve fees/slippage/funding are already in
their returns. Doubled-cost run: W2 re-simulated at 2x costs; W1 daily book shifted by its own measured 2x-cost drag
(per-window log-drag from r4_07 results.json spread evenly per day); BTC sleeve 2x; rebalancing cost 2x.

### Evidence (internet)
- Moreira & Muir (2017, JF) "Volatility-Managed Portfolios": scaling by inverse past variance raised alphas/Sharpe.
- Cederburg, O'Doherty, Wang & Yan (2020, JFE) "On the performance of volatility-managed portfolios": over 103
  strategies the real-time versions usually did NOT beat the unmanaged ones out of sample (structural instability)
  https://academicnewsletter.sufe.edu.cn/info/355606 ; DeMiguel, Martin-Utrera & Uppal (2024, JF) multifactor version
  https://lbsresearch.london.edu/id/eprint/3716
- Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert (2018, JPM) "The Impact of Volatility Targeting": Sharpe
  gains only for risk assets (leverage effect), but lower tail risk everywhere https://quantpedia.com/?p=830 ,
  https://scholars.duke.edu/publication/1370354
- Gupta & Kelly (2019, JPM) "Factor Momentum Everywhere": strategies/factors can be timed by their own past returns
  (AR(1) > 0 for 59/65 factors) https://www.aqr.com/Insights/Research/Working-Paper/Factor-Momentum-Everywhere
- Risk parity / inverse-vol and shrunk minimum-variance (Ledoit-Wolf style shrink to diagonal) as standard baselines.

Honest prior: with gross <= 1, a mix can only beat Worker 1's TEST geometric month (+5.33%) if the timing is good
enough to sit in the better sleeve at the right time; volatility scaling with a 1x cap can only REDUCE exposure, so it
cannot raise the geometric month above the fully invested mix unless it dodges losses. Expect PORTFOLIO-ONLY or FAIL.

## Part A: DEV grid (DEV = 2021-01..2024-10, sleeves' own data start 2021-01-01; first ~L days fall back to equal weight)
Benchmark (not selectable): fixed 50/50, daily rebalance.
- invvol: L in {30, 60, 120}, freq in {1, 7}
- minvar (shrink 0.5 to diagonal, long-only, sum 1): L in {60, 120}, freq 7
- voltarget (equal base scaled by min(1, tgt / trailing ann. vol)): L in {30, 60}, tgt in {0.20, 0.30, 0.40}, freq 1
- relmom (tilt toward sleeve with higher trailing L-day return): L in {30, 60, 120}, tilt in {0.15, 0.25}, freq 7
- absmom (sleeve weight x cut if its trailing L-day return <= 0, rest cash): L in {30, 60, 120}, cut in {0, 0.5}, freq 1
- ddcut (sleeve weight x cut while its drawdown from peak > X): X in {0.10, 0.15}, cut in {0, 0.5}, freq 1
- ivmom (inverse-vol L=60 x absmom filter M): M in {30, 60}, cut in {0, 0.5}, freq 1
- 3 sleeves with BTC trend SMA N in {50, 100, 200}: fixed 1/3 each (freq 1) or invvol L=60 (freq 7)
Total 40 configs + benchmark; all reported.

**Selection criterion (fixed now):** among configs with DEV months-up >= the 50/50 benchmark's DEV months-up AND DEV max
drawdown <= benchmark DEV max DD + 5 pp, pick the highest DEV geometric month; ties (within 0.02 pp) -> fewer parameters.
If none qualifies, the selection is the 50/50 benchmark itself.

## Checks (ROBUSTNESS.md 8 checks, adapted where a check is about coins)
1-4, 8 exactly as written, on the allocator's daily book (months 2021-01..2026-10).
5. Neighbours: each main parameter of the selected config at -50%, -25%, +25%, +50% (rounded to valid values) and rebalance
   frequency in {1, 3, 7, 14} days; >= 70% must be net positive on DEV and on TEST.
6. Beats chance: 200 random allocators with the same sleeves, same rebalance frequency and the same daily gross
   exposure series as the selected config, but the split between sleeves drawn uniformly at random and held for blocks of
   L days (L = selected lookback, 21 if none). The selected full-period net must beat >= 95% of them.
7. Not a few coins/trades: (i) both W1 and W2 pass their own check 7 (on file: both PASS); (ii) the allocator is still net
   positive over 2021-2026 after deleting its 5 best calendar weeks, and no single day contributes > 10% of total log
   growth. Optional BTC sleeve: one coin by construction; if selected it must not exceed 10% of total log growth.

## Part B: selection from DEV (appended after the DEV grid, before any TEST number)
DEV grid output: `dev_grid.json` (grid.py). One bug fixed before selection: inverse-vol weights for the BTC sleeve were
NaN when it sat flat for the whole lookback (zero vol); vol is now floored at 0.5%/day. All 40 configs reported.
DEV benchmark 50/50: median +5.15%, geo +4.88%, 33/46 months up, max DD 9.56%.
Qualifying configs (months up >= 33, DD <= 14.56%), by DEV geo: relmom_L30_t0.25 5.20, relmom_L60_t0.25 5.13,
relmom_L120_t0.25 5.13, relmom_L30_t0.15 5.08, relmom_L60_t0.15 5.04, relmom_L120_t0.15 5.04, voltgt_L30_t0.4 4.84 ...
(all 6 relative-momentum configs beat the benchmark's DEV geo; no vol-target / inverse-vol / min-var / drawdown / BTC
config did).

**SELECTED (the ONE config): relmom, L = 30 days, tilt = 0.25, weekly rebalance (freq 7).**
Every 7th day: compare W1 and W2 compounded returns over the 30 days ending 2 days before; target 75% in the better sleeve,
25% in the other (gross 1.0, no cash). DEV: median +4.73%, geo +5.20%, mean +5.39%, 35/46 up, max DD 10.76%.

Check-5 neighbour grid (11): L in {15, 22, 38, 45}; tilt in {0.125, 0.1875, 0.3125, 0.375}; freq in {1, 3, 14}.
Check-6 null: 200 random allocators, split w1 ~ U(0,1) redrawn every 30 days, gross 1.0, weekly rebalance, same costs.
