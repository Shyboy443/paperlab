# PREREG: info_discreteness (Worker 3, round 1). Written 2026-10-26, BEFORE any TEST run.

## Family and sources
Momentum *quality* on the crypto cross-section: does the *path* of the formation-period return (smooth vs jumpy)
predict continuation better than the raw return?
- Da, Gurun & Warachka (2014), "Frog in the Pan: Continuous Information and Momentum", RFS 27(7) 2171-2218
  (https://www3.nd.edu/~zda/Frog.pdf). Equities: momentum falls monotonically from +5.94% (continuous information)
  to -2.07% (discrete) for similar formation returns. Information discreteness ID = sign(PRET) x (%neg - %pos days).
- Alpha Architect summary (https://alphaarchitect.com/2015/11/frog-in-the-pan-identifying-the-highest-quality-momentum-stocks/);
  Gray & Vogel, *Quantitative Momentum* (FIP screen).
- Clenow, *Stocks on the Move*: exponential-regression slope x R^2 as trend-quality momentum.
- Blitz, Huij & Martens (2011), residual momentum; Sharpe-ratio (risk-adjusted) momentum.
- Crypto: Han, Kang & Ryu (https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)
  find crypto XS momentum weak and concentrated in LARGE winners (i.e. jumpy paths), which is the opposite prior to FIP;
  "Momentum and liquidity in cryptocurrencies" (arXiv 1904.00890). No crypto replication of FIP was found.

## Data, engine, costs
Survivorship-free daily panel (`research/monthly20/r2_rotation/data.py`, 683 crypto perps incl. delisted), point-in-time
top-N by 30-day quote volume; agent-02 engine `engine_r2.run` unchanged (signal from closes up to day d-1, fill at day d
open, fee 0.05% + slip 0.02% per side, funding, 1% bar-volume cap), 1x gross, equal weight per leg.

## DEV grid (2021-01..2024-10 only), `dev_grid.py` -> `dev_grid.json`
kind {mom (reference), fip, r2s, sharpe, resid} x L {14,30,60,90} x k {5,10} x legs {LS, LO} x rebal {1,7}, univ 40.
Result: FIP (smooth-winner) does NOT improve crypto momentum on DEV (best fip t = 1.78 vs plain mom14 LS t = 2.97).

## Selection rule (DEV only)
1. Highest DEV monthly t-stat among quality kinds with DEV geo > 0 and DEV net > 0 at doubled cost
   -> `sharpe L14 k10 LS weekly` (t 2.91). REJECTED as not materially different from Worker 1: DEV daily
   correlation 0.88 with plain 14d LS rotation (W1's leg) (`dev_corr.py` -> `dev_corr.json`).
2. Added constraint (brief: "must differ materially"), decided on DEV numbers only: DEV daily correlation with plain
   14d LS10 weekly rotation < 0.70 AND with the Worker 1 book < 0.50, and long/short (all long-only configs had
   DEV max DD 85-93%, impossible vs the 23% DD bar). Highest DEV t meeting it:

## THE ONE selected config
**r2s, L = 30, k = 10, LS, rebalance every 7 days, universe top-40.** Each week, for every coin in the top-40 with 30
complete daily closes, fit OLS of ln(close) on time over the last 30 closes; score = (exp(365 x slope) - 1) x R^2.
Long the 10 highest, short the 10 lowest, 50% gross per side (1x total), market orders at the daily open.
DEV: median +0.28%, geo +3.22%, mean +3.69%, 24/47 up, DD 30.4%, t 2.44, net 2x cost +294%; DEV corr W1 0.365, corr mom14 0.669.

## Check 5 neighbour grid (pre-declared)
L {15, 22, 38, 45}; k {5, 8, 12, 15}; rebal {1, 3, 5, 10, 14}; univ {20, 30, 50, 60}  (17 neighbours, each one changed alone).
Check 6: 500 random-pick books, same 10/10 structure, weekly, top-40, same costs. Check 7: re-run with top-5 profit coins removed.
Others exactly as `research/monthly20/ROBUSTNESS.md`. TEST is run ONCE by `run.py`.
