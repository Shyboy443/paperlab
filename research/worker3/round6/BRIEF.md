# Worker 3, round 6: improve the built rule (2026-10-27)

The operator wants Worker 3 to have **better results**. The standing rule still applies: no curve-fitting and no lottery
bots (`research/monthly20/ROBUSTNESS.md`).

## The rule to improve (built as app/worker3)
- **Score:** the position of the previous close in the 30-day high/low channel.
- **Universe:** top 20 perps by 30-day quote volume.
- **Book:** long the top 5 and short the bottom 5, each 0.1 of equity, re-sized daily at the open, 1x.
- **Mechanics:** a 1% of the previous day's volume cap per position, funding charged, and the 1x short liquidation
  rule.
- **Code:** `research/worker3/round5/high52_plus/lib5.py` `run5(Pn, cfg, ...)`, with the config in
  `round5/high52_plus/dev.json` → `selected.cfg`.
- **Evaluation:** `round5/high52_plus/final.py` is the full harness: panels, the 8 checks, randoms, neighbours, W1/W2
  correlations and portfolios. COPY it into your folder and adapt it. Do not edit round5.
- **Panels:** `L.L4.build()`/`make_panel` gives the perp panel (DEV+TEST). `L.L4.holdout_panel()` gives the 2018–2020
  spot panel (HOLDOUT).

### Its numbers (1x, all costs)
| Window | Median month | Geo month | Months up | Max DD | Checks |
|---|---|---|---|---|---|
| DEV 2021-01..2024-10 | +2.78 | +5.13 | 27/46 | 26.3% | – |
| TEST 2024-11..2026-10 | +10.23 | +10.33 | 16/24 | 39.9% | 7/8 (fails check 7, RAVE 15.3%) |
| HOLDOUT 2018–2020 | +3.25 | +5.18 | 24/36 | 28.5% | 8/8 |

### Its weak spots
- a 40% drawdown;
- only 16/24 months up on TEST;
- TEST profit concentrated in 2025–26 new meme perps;
- 9 short liquidations on TEST, from coins doubling in a day;
- many tiny daily re-size trades, which cost fees and hurt at 500 USDT.

## Protocol (strict, because TEST and HOLDOUT have both been seen for this rule)
1. **Choose on DEV only.**
   - Every variant, parameter and threshold is picked on DEV (2021-01..2024-10).
   - Prefer changes with a strong prior from the literature or from mechanics (risk, costs) over fitted thresholds.
   - Keep the grid small, at most about 30 configs, and log every one.
2. **PREREG.md** must be written BEFORE TEST and HOLDOUT. It contains:
   - every config tried;
   - the selection rule;
   - the ONE selected variant;
   - its neighbour grid.

   Then run TEST once and HOLDOUT once.
3. **Costs and leverage:** fee 0.05% + slippage 0.02% per side, plus a doubled-cost run. Gross exposure never above
   1x, so no leverage gains.
4. **Scope:**
   - Write only in `research/worker3/round6/<your_name>/`, plus `data/w3r6_<your_name>/`.
   - Research only: no orders, no keys, never read .env or secrets, never touch `app/`, never kill processes you did not
     start.
   - Runtime: about 90 min at most.
   - A clean FAIL is valid. Never hide losing variants.
5. **REPORT.md:** write it in your folder. If the file tool is refused, put the full report in your final message.

## The bar: IMPROVED
The selected variant must meet all of these:
- **On DEV:** better than the current rule on geo month, or the same geo with a clearly lower DD.
- **On TEST AND HOLDOUT, each:**
  - geo month ≥ 90% of the current rule's (TEST ≥ +9.3, HOLDOUT ≥ +4.66);
  - AND at least one of: max DD at least 5 points lower; months up at least 2 more; geo month higher.
- **Checks:** 8/8 on HOLDOUT, and on TEST no worse than the current rule (7/8 or better).
- **Doubled costs:** positive on both windows.

**BETTER-RISK** means it lowers the drawdown by 10 points or more, at a geo month ≥ 75% of the current rule's on both
windows. Report this too, because the operator may prefer it.

Anything else is FAIL.

## Final message (short)
- the selected variant;
- a DEV / TEST / HOLDOUT table next to the current rule;
- checks n/8;
- doubled costs;
- the verdict: IMPROVED / BETTER-RISK / FAIL.
