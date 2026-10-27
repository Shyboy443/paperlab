# PREREG: lit_scout2 (Worker 3, round 5). Written 2026-10-27 AFTER the DEV grid (dev.py / dev.json / dev.log,
dev_ctr_v2.log) and BEFORE any TEST (2024-11..2026-10) or HOLDOUT (2018-01..2020-12) run of either idea.

No TEST or HOLDOUT insight from earlier reports was used (nothing re-used except the shared engine/data code).

## Literature shortlist (5; why each could persist in 2025-26)
1. **TSD - "Catching Crypto Trends"** (Zarattini, Pagani & Barbon, SSRN 2025, survivorship-free, 2015..2025-03, net
   Sharpe > 1.5 on a rotating top-20 liquid basket). Time-series (absolute) trend, long-only, cash otherwise. Persistence:
   crypto has no cash-flow anchor, retail/flow-driven under-reaction and slow diffusion; the payoff is crash avoidance
   (convex), which is not arbitraged away because it costs carry in chop. NOT the round-2 cross-sectional Donchian L/S.
   IMPLEMENTED.
2. **CTREND** (Fieberg, Liedtke, Poddig, Walker & Zaremba, JFQA 60(7) 2025): ML aggregation of price/volume
   technical signals across horizons; survives costs and holds in big, liquid coins; robust across subperiods.
   Persistence: aggregates many weak, slowly-decaying trend signals; less crowded than a single lookback. IMPLEMENTED
   as a reconstruction (indicator list and estimator are my approximation; the full paper text was not reachable).
3. Short-term MAX reversal x trend (Ozdamar, Akdeniz & Sensoy 2021 MAX premium; Dobrynskaya 2023 momentum/reversal):
   NOT implemented - MAX/lottery and daily/weekly reversal were already tried (rounds 3 R4 and W3 round 1).
4. Weekend vs weekday trend (day-of-week literature is mixed: Caporale & Plastun; Kurihara & Fukushima): NOT
   implemented - weak evidence, and "seasonality/events" + session windows already tried.
5. Volume-weighted / dual momentum (Korchmar 2022 thesis volume momentum; Antonacci dual momentum, practitioner only):
   NOT implemented - no peer-reviewed crypto OOS evidence; dual momentum's absolute filter is a sub-case of idea 1,
   and 60d momentum + rotation are inside Worker 1 already.

## Idea A: TSD (lib5.tsd_weights / run_w)
Per coin, per lookback L in {5,10,20,30,60,90,150,250,360} days (close prices, full L bars required): enter when
close > max(previous L closes); trailing stop = max(previous stop, (L-day max close + L-day min close)/2); exit when
close < stop. Coin signal s = mean of the 9 states (0..1). Weight = s x min(2, tv / vol90) / univ, vol90 = annualised
90-day close-to-close st.dev.; only coins in the point-in-time top-`univ` by 30d quote volume; gross capped at 1 (1x).
Signals at close i-1, traded at close i-1 (= next open), held over day i. No-trade band: a held coin is only re-sized if
|target - current| > band x |target| (entries/exits always trade). Costs 0.07%/side on traded notional, funding paid.
DEV grid (12 configs): univ {20,40} x tv {0.5,1.0,2.0} x band {0,0.25}.
Selection rule: among configs with DEV doubled-cost net > 0 and DEV max DD <= 30%, the highest DEV monthly t-stat.
**SELECTED: univ 20, tv 0.5, band 0.25** (DEV geo +0.40%, median -0.52%, t 0.85, DD 19.9%).

## Idea B: CTR (lib5.ctr_score / run_ctr)
Features at close (each cross-sectionally ranked to [-0.5,0.5] in the next day's top-40; missing = 0):
price/MA_L - 1 for L in {3,5,10,20,50,100,200}; fs "pv" adds volume MA_L / MA_200 for the same L and RSI14, %K14,
MACD/price, MACD histogram, Bollinger %b, CCI20. Every day s: ridge (lambda 1) cross-sectional regression of the
winsorised, demeaned 7-day forward open-to-open return on the features. Score on day i = features(close i-1) x the
mean coefficient over the last Wk weekly-spaced cross-sections whose forward return is complete before open i
(needs >= Wk/3 of them). Long top 10 / short bottom 10 of the point-in-time top-40, weekly rebalance (7 days),
engine = round-4 high52_v2 lib.run (engine_r2 copy, 1x, 0.07%/side, funding, 1% volume cap).
DEV grid (6 configs): fs {p, pv} x Wk {26,52,104}.
Selection rule: among configs with DEV doubled-cost net > 0 and DEV max DD <= 35%, the highest DEV monthly t-stat.
**SELECTED: fs "p", Wk 104** (DEV geo +1.15%, median 0.00%, t 1.27, DD 30.9%; inactive until 2021-10 warm-up).
Note: a first DEV pass required every feature to be finite (no coin had 200 days of perp history until late 2021);
I fixed that to "missing = neutral rank" before selection. Both passes are in dev.log / dev_ctr_v2.log.

Total configs looked at: 12 (A) + 6 (B) + 6 (B first pass) = 24.

## Check-5 neighbour grids (16 each)
- A: lookback scale x{0.5,0.75,1.25,1.5}; tv {0.25,0.375,0.625,0.75}; univ {10,15,25,30}; rebal (re-size every)
  {2,3,5,7} days.
- B: Wk {52,78,130,156}; k {5,8,12,15}; univ {20,30,50,60}; rebal {3,5,10,14}.

## Runs (each ONCE, final.py)
TEST 2024-11-01..2026-10-21 (perps) and 2021-01..2026-10 checks per ROBUSTNESS.md (random books: 200 daily-redraw
+ 200 30-day-block; for A the random book keeps the strategy's daily sorted weight vector and assigns it to random
eligible coins). HOLDOUT 2018-01..2020-12 on Binance spot daily (funding 0; shorts on spot ASSUMED possible at the same
costs for B), 36-month adapted checks as round 4 (300+300 random books there replaced by 200+200 for runtime).
Doubled-cost runs for both windows. Portfolio: W1+W2+X thirds; and the DEV-chosen mix (1-x)/2 W1 + (1-x)/2 W2 + x X,
x in {0,0.1,...,0.5}, x chosen by highest DEV (2021-01..2024-10) geo month, then applied to TEST and HOLDOUT.
Verdict per brief: STRONG / PORTFOLIO / FAIL.
