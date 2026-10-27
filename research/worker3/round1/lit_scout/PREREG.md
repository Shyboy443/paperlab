# lit_scout PREREG (written 2026-10-26 before any TEST run; not edited afterwards)

Ranked literature shortlist: SHORTLIST.md. Candidates #1 and #2 were designed on DEV (2021-01..2024-10) only.

## Candidate 1: stablecoin float growth timer (goes to TEST)
Sources: Griffin & Shams (2020, J. Finance) "Is Bitcoin Really Untethered?"; Ante, Fiedler & Strehle (2021, Finance
Research Letters) "The influence of stablecoin issuances on cryptocurrency markets"; CryptoQuant research notes 2024-25
(float growth led the 2021 and 2024-25 rallies; practitioner).
Data: DefiLlama `https://stablecoins.llama.fi/stablecoincharts/all` (keyless), field totalCirculatingUSD.peggedUSD, cached
in `data/w3r1_lit_scout/stable_all.json`. On-chain supply, not revised; used with a 2-day lag (value labelled d-2 for
trading day d) so it is point-in-time regardless of DefiLlama's stamp time.

Rule (`stable.py`): g_d = S(d-2)/S(d-2-L) - 1. If g_d > thr: hold the top-K USDT perps by trailing 30-day quote volume
(point-in-time top-40 universe incl. delisted coins), equal weight, 1x total; else flat (LF) or short the basket (LS).
Basket re-picked and re-weighted every `recon` = 7 days and whenever the signal flips. Fill at the day's open
(00:00 UTC), costs 0.05% + 0.02% per side on traded notional, funding paid/received daily.

DEV grid (72 configs, `dev_stable.py`, `dev_stable.json`): L in {7,14,30,60} x thr in {0, 0.5%, 1%} x K in {1,3,10} x
mode in {LF, LS}. Selection rule (declared before looking at TEST): highest DEV geometric month averaged over the
config and its immediate L/thr grid neighbours (same K, mode), to avoid picking a knife edge.
(The single best raw DEV geo was L7/thr0/K10/LS at +3.89%, but its neighbour L14/thr0/K10/LS had +0.05% -> rejected by the rule.)

**SELECTED: L = 30, thr = 1.0%, K = 3, mode = LF (long/flat), recon = 7.**
DEV at 1x: median 0.00%, geo +2.71%, mean +4.20%, 18/46 up, max DD 55.6%, t 1.56, 7.9 trades/month, net +243%
(double cost +236%). Always-long top-3 benchmark on DEV: geo +1.13%, max DD 85.8%.
Honest prior: DEV max DD alone already breaks bar (c) (<= 23%), so a bar PASS is not expected; TEST is run once to measure
OOS evidence and the portfolio contribution.

Check-5 neighbour grid (13): L in {15, 22, 38, 45}; thr in {0.5%, 0.75%, 1.25%, 1.5%}; K in {2, 4, 5}; recon in {3, 14}
(one parameter moved at a time, others at the selected values). Pass if >= 70% are net positive on both DEV and TEST.
Check-6 random versions (200): the selected config's ON/OFF series circularly shifted by a random offset of
60..(n-60) days over 2021-01..2026-10 (same exposure share, same number and length of ON spells, same basket rule and costs).
Check 7: top-5 profit coins removed from the universe (the basket then takes the next most liquid coins); position =
one coin from entry to exit.

## Candidate 2: turnover-volatility factor (DEV FAIL, not taken to TEST)
Source: Mercik, Zaremba & Demir (2026, IRFA) "Crypto Factor Zoo". Proxy: coefficient of variation of daily quote volume
over L days; long the k lowest-CV, short the k highest-CV coins of the top-40, dollar neutral, sign fixed a priori.
DEV grid (18, `dev_tvol.py`, `dev_tvol.json`): L {14,30,60} x k {3,5,10} x rebalance {7,14}. 17/18 lost money on DEV;
best L14/k5/rb7 geo +0.90%, t 0.82, DD 52.7%. No config selected; TEST not run. (Post-hoc observation, not tested: the
reversed sign would have made money on DEV; that is "buy erratic-volume coins", close to the tried attention/lottery families.)
