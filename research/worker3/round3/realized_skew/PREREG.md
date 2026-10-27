# realized_skew: pre-registration (written after the DEV grid, before any TEST run), 2026-10-26

## Idea and sources
Weekly realized higher moments from intraday returns predict the cross-section of next-week returns.
- Amaya, Christoffersen, Jacobs & Vasquez (2015), "Does realized skewness predict the cross-section of equity returns?",
  JFE 118(1) 135-167 (https://ideas.repec.org/a/eee/jfinec/v118y2015i1p135-167.html, SSRN 1898735). Long low-skew / short
  high-skew deciles earn ~19 bp/week (t = 3.70); kurtosis is positive but weak; volatility is not significant.
- Jia, Liu & Yan (2021), "Higher moments, extreme returns, and cross-section of cryptocurrency returns", Finance Research
  Letters 39 (https://ideas.repec.org/a/eee/finlet/v39y2021ics1544612320303135.html). 84 coins, intraday data: skewness
  predicts negatively, kurtosis and volatility positively. The effect is driven by extreme POSITIVE returns (lottery
  preference), so it should overlap with MAX, which already failed in an earlier round.
- Zhang, Li, Xiong & Wang (2021), "Do higher-order realized moments matter for cryptocurrency returns?", International
  Review of Economics & Finance 72, 483-499 (https://ideas.repec.org/a/eee/reveco/v72y2021icp483-499.html). Abstract not
  retrieved (403); cited for completeness only.

## Rule (rs.py)
- **Moments:** hourly log returns over the W days that ended by day d's 00:00 UTC open, on the survivorship-free hourly
  panel (r4_09_session); a coin needs at least 80% of the hours.
  - RSkew = sqrt(N) * sum r^3 / (sum r^2)^1.5
  - RKurt = N * sum r^4 / (sum r^2)^2
- **Sign:** taken from the literature and fixed beforehand.
  - skew: long the k LOWEST, short the k HIGHEST;
  - kurt: long the k HIGHEST, short the k LOWEST.
- **Trading:** 1x gross, half long and half short, weekly rebalance, point-in-time top-N by 30-day quote volume. Uses the
  unchanged agent-02 engine: fee 0.05% + slippage 0.02% per side, funding charged, 1% volume cap.

## DEV grid (2021-01..2024-10 only; dev_grid.py, dev_grid.json, 48 rows)
- **Rows:** stat {skew, kurt} x W {7, 14, 28} x k {5, 10} x univ {20, 40}, in both the literature sign and the reverse
  sign. The reverse sign was reference only and not selectable.
- **Selection rule, fixed before running:** among literature-sign rows with DEV geo > 0, DEV max DD <= 40% and DEV net > 0
  at 2x costs, take the highest DEV t. If no row qualifies, the result is DEV FAIL, and the best-t literature-sign row
  still runs once on TEST, labelled DEV FAIL.

**DEV outcome: DEV FAIL.**
- **Skew:** all 12 literature-sign rows lose (geo month −0.15% to −4.17%, t down to −2.25). In crypto, high realized skew
  is followed by HIGHER returns, the opposite of ACJV. This matches the round-1 lesson that short horizons continue.
- **Kurt:** no row clears DD <= 40% with geo > 0.
- **Overlap with MAX (DEV, top-40, mean weekly cross-sectional Spearman):**
  - RSkew vs MAX hourly return: 0.65 to 0.67;
  - RSkew vs MAX daily return: 0.51 to 0.57;
  - RKurt vs MAX hourly return: 0.25 to 0.30;
  - RSkew vs RKurt: about 0.07.

## THE ONE config run on TEST (labelled DEV FAIL)
**stat = kurt, W = 14 days, k = 10/10, univ = top-40, weekly rebalance, 1x.**
DEV: median +0.27%, geo +0.65%, 27/47 months up, max DD 53.2%, t 0.85.

## Check-5 neighbour grid (fixed now)
- W in {7, 10, 18, 21}
- k in {5, 8, 12, 15}
- rebal in {3, 5, 10, 14} days
- univ in {20, 30, 50, 60}

16 neighbours; a neighbour passes if it is net positive on DEV and on TEST.

## Secondary diagnostic (NOT eligible, declared now)
Reverse-sign skew (long HIGH skew / short LOW skew), W = 28, k = 10, univ = 20. This is the best reverse row on DEV
(geo +1.86%, DD 28.8%, t 1.79). It runs on TEST once, only to show whether skew continuation persisted in 2025-26. The
sign was chosen after seeing DEV, so it cannot pass the bar whatever it shows.
