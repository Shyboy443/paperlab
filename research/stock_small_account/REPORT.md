# Five-stock small-account variant — retrospective verdict

Precommitted implementation: five of the monthly liquid 20 stocks, ranked by prior 63-session return. Keep the aggregate 20-stock SMA200 gate. No full-history winner selection. $9.50 initial capital and exposure cap, 1% cash reserve, $1 minimum new trade, skip small adjustments, no cash interest. The default was not changed after seeing the grid.

| Case | Monthly net mean | Annualized | Sharpe over T-bill | Max DD | Worst month | End USD |
|---|---:|---:|---:|---:|---|---:|
| IS_L63_seedNone | 0.47% | 4.75% | 0.29 | 33.96% | 2012-05 (-12.61%) | $24.01 |
| OOS_L63_seedNone | 1.54% | 18.95% | 0.90 | 17.56% | 2025-03 (-6.31%) | $22.51 |
| OOS_sell_fee_0.0 | 1.63% | 20.44% | 1.00 | 15.96% | 2025-03 (-5.67%) | $23.95 |
| OOS_sell_fee_0.03 | 1.32% | 15.72% | 0.70 | 21.21% | 2025-03 (-7.79%) | $19.63 |

The main 2021–2025 book grew from $9.50 to $22.51, with $1.74 in modeled execution costs. All five default execution-cost stress seeds remained positive. The full 3-lookback × 5-seed grid is in metrics.csv, together with unseeded defaults and fee sensitivity. These seeds perturb costs; they are not independent market histories.

The worst month was March 2025 (−6.31%). Concentration and trend whipsaws are the main risk: five winners can reverse together, and the 200-day aggregate filter reacts with delay. No new stop has been added. Selecting fewer shares solves initial order size, not this market risk.

**Verdict: executable sizing at $9.50; retrospective positive result; NOT a fresh OOS or prospective validation pass.** The original 20-stock study used a much larger compounding book and T-bill cash. Its 23.38% CAGR cannot be transferred to this capped five-stock book. Missing delisted prices and already examined history limit the evidence. This does not establish an expected future monthly return.

Actual broker fractional precision, splits, per-dividend rounding and regulatory charges can differ from the historical adjusted-unit approximation. Costs here are a stress proxy: 6bps per side, square-root impact and $0.01 per sell. Fee sensitivity uses $0 and $0.03 per sell. Holdings are marked at the final observed open, without assumed liquidation. Missing executable opening prices block the basket and retain prior marks; no such held-price gaps occurred in the default OOS book.

![Equity and drawdown](output/equity_drawdown.png)

Outputs: physical valuation-date daily ledgers, executed orders, monthly table, complete sensitivity metrics, SHA256 source/data pin and machine-readable verdict. IS covers 2001–2020 because the frozen input first has 20 eligible stocks in January 2001; OOS is an independently funded 2021–2025 book.
Execution parity: on the first preflight failure the research book pauses and retains holdings, just as production does; it assumes no operator rearm. The 42-session IS cases depleted capital below the five-stock minimum and spent many sessions paused. Their negative results remain in the full grid rather than being omitted. Default and all OOS cases had no blocked or paused days.
