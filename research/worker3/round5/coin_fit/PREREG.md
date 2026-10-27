# coin_fit PREREG (written 2026-10-27, after the DEV run, BEFORE any TEST or HOLDOUT run)

Question: does choosing a rule per coin ("the coin that suits the strategy") on DEV have out-of-sample skill?

## Data and universe
- Perps (DEV + TEST): `data/monthly20_r2_rotation/panel_1d.npz`, daily, funding included.
- Spot 2017-08..2020-11 (`data/w3r4_high52_v2/holdout_panel.npz`) is spliced in front of each perp series. It is used as
  signal warm-up for DEV and as the traded series in the 2018-2020 HOLDOUT (no funding, shorts assumed possible at no
  borrow cost).
- Universe, frozen: the 20 largest perps by Dec-2020 quote volume that were live on 2020-12-01 and had spot data from
  before 2020-01-01: BTC ETH XRP LTC LINK BCH XLM ADA EOS BNB ZIL THETA TRX ETC WAVES VET XMR BAND XTZ ZEC.
- Costs: 0.05% fee + 0.02% slippage per side on every position change, plus a drift-rebalance charge between pair
  sub-accounts. A doubled-cost run is also made. 1x only.

## Rules (6, fixed literature parameters, daily only; no 4h rule was used)
| rule | definition | source |
|---|---|---|
| TSMOM60_LS | sign of 60-day return, long/short | Moskowitz, Ooi & Pedersen (2012); Liu & Tsyvinski (2021) |
| EMA20_100 | long while EMA20 > EMA100, else flat | Faber (2007); Hurst, Ooi & Pedersen (2017) |
| DON55_20 | long on close > prior 55-day high, exit close < prior 20-day low | Turtle System 2 (Faith 2007) |
| HIGH52 | long while close >= 90% of the 365-day high | George & Hwang (2004) |
| RSI2_CONNORS | long RSI(2) < 10 and close > SMA200, exit close > SMA5 | Connors & Alvarez (2008) |
| BOLL20_MR | long close < SMA20 - 2 sd, exit close >= SMA20 | Bollinger (2001) |

A position is set at the close of day d-1 from closes up to d-1. A book weights its pairs equally among the pairs that
are available that day (coin has data and full look-back), with daily rebalancing.

## Configs tried
- 120 pairs (20 coins x 6 rules), all evaluated on DEV.
- 1 selection rule. dev.py and `run.py dev` are the same computation run twice. No rule or parameter was changed after
  seeing any result.
- Insights from earlier TEST/HOLDOUT reports: none used. HIGH52 is a time-series version of the round-4 lead's idea;
  it is in the menu because the brief asked for it, not tuned.

## The ONE selected config (static)
- Per coin, pick the rule with the highest DEV (2021-01..2024-10) annualised Sharpe of its 1x net daily returns.
- Skip the coin if that best Sharpe is <= 0.5.
- Result (K = 14 pairs):
  - BTC, ETH, BNB: DON55_20;
  - XRP, XLM: RSI2_CONNORS;
  - BCH, ADA, TRX: EMA20_100;
  - ZIL: TSMOM60_LS;
  - THETA, ETC, WAVES, VET: HIGH52;
  - XMR: BOLL20_MR;
  - skipped: LTC, LINK, EOS, BAND, XTZ, ZEC.
- DEV in-sample, which is optimistic by construction: geo +3.37%, median +1.10%, 27/46 months up, max DD 19.3%,
  Sharpe 1.48.

## Comparisons (run identically on TEST and HOLDOUT)
- **ALL_PAIRS**: all 120 pairs.
- **The six RULE_x_ALL_COINS books.**
- **BEST_RULE_HIGH52**: HIGH52 had the best DEV all-coin Sharpe. It is run on all coins and on the 14 chosen coins.
- **RANDOM**: 2,000 draws of K = 14 random coins, each with a random rule. This is the key comparison and also check 6.
  Pass requires >= 95th percentile on net return.
- **PERM**: 2,000 draws that keep the 14 chosen coins and shuffle the 14 chosen rules among them. This isolates the
  coin-to-rule matching from the rule mix.
- **Walk-forward**: every calendar quarter, re-pick the rule per coin from the trailing 365 days with the same metric
  and the same 0.5 threshold, equal weight, cash if nothing is chosen.
  - It is reported for 2022-01..2024-10 (DEV), TEST, and 2019-01..2020-12 (HOLDOUT).
  - It is compared against ALL_PAIRS and 200 random walk-forward paths (same count each quarter, random coins and rules).
- **Personality persistence**: per coin, measured in DEV, TEST and HOLDOUT.
  - Measures: lag-1 autocorrelation; Lo-MacKinlay VR(10); an aggregated-variance Hurst exponent; and trend-minus-MR,
    which is the mean Sharpe of the 4 trend rules minus the mean of the 2 MR rules.
  - Reported as the Spearman rho DEV->TEST and DEV->HOLDOUT, plus the coin x rule interaction-residual rho and the
    later rank of the DEV-best rule (chance = 3.5 of 6).

## DEV-only evidence already seen
These numbers are known before the out-of-sample runs.
- **Split-half inside DEV**: picking on 2021-22 and trading 2023-01..2024-10 gave -2.43% net, the 48.6th percentile
  of random books and the 57th percentile of permutations.
  - Later rank of the earlier-best rule: 3.85, against 3.5 by chance.
  - Interaction rho: 0.09 (p 0.35).
- **Walk-forward 2022-01..2024-10**: -61.5% net, against -16.3% for ALL_PAIRS.
- **Prediction**: FAIL.

## Neighbour grid (check 5)
Each neighbour re-selects on DEV, then is run on TEST and HOLDOUT.
- Look-back scale x0.5, x0.75, x1.25 and x1.5 on all rules.
- Threshold 0.0, 0.25, 0.75 and 1.0.
- Metric: DEV net return (threshold 0) and DEV Calmar (threshold 1.0).
- Universe of 10, 15, 25 and 30 coins.

That makes 14 neighbours. The check passes if >= 70% are net positive in the window.

## Checks on TEST and HOLDOUT
These are the 8 checks of ROBUSTNESS.md, applied per window.
- **Check 2 adaptation**: the window's calendar years must be positive in a majority, and the window must be positive
  after dropping its first calendar year.
- **Check 6**: the RANDOM test above.
- **Check 7**: drop the top-5 profit coins, and the largest position must be <= 10% of profit.

## Portfolio
- **W1 and W2 monthly series**: `r4_07_ensemble` and `worker2/session` for DEV and TEST; the `worker1_backfill` and
  `worker2/backfill` series for HOLDOUT.
- **DEV-chosen mix**: the X weight in {0, 0.1, ..., 0.6}, with the rest split 50/50 between W1 and W2, chosen by the best
  DEV monthly mean/sd.
  - DEV picked **X = 0.0**: even the in-sample X lowers the DEV Sharpe of W1+W2.
  - So the DEV mix equals W1+W2 50/50, and the PORTFOLIO verdict is judged on the **thirds** mix.

## Verdict rule
STRONG, PORTFOLIO or FAIL, exactly as in BRIEF.md. The coin-matching question is answered by the RANDOM and PERM
percentiles on TEST and HOLDOUT, the walk-forward against ALL and random, and the persistence rhos.
