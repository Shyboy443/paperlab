# "Not luck" checks: round 2 (fixed before any check is run)

The user does not want lottery bots. A strategy only counts as **robust** if it passes ALL of these, run at **1x leverage**.
Leverage only scales luck up or down, so it is judged separately at the end. Read PROTOCOL.md and common.py first; the same data rules apply.

**Universe must include delisted coins.** Use the public archive data.binance.vision (monthly 1h/1d kline zips and funding),
as agents 04, 05 and 07 already did. Their caches are in `data/monthly20_04_crash_reversal/`, `data/monthly20_05_new_listings/` and
`data/monthly20_07_pump_scanner/`; reuse them before downloading anything. Pick the universe point-in-time by trailing quote volume.

## The checks (each one PASS/FAIL)
1. **Both halves.** Net positive on DEV (2021-01..2024-10) AND on TEST (2024-11..2026-10), both at baseline and at doubled costs.
2. **Most years.** Positive in at least 4 of the 6 calendar years 2021–2026, and positive over 2022-01..2026-10 (no 2021).
3. **Statistically real.** The t-stat of the monthly mean over all 70 months is ≥ 2.0, and the block-bootstrap (3-month blocks, 5,000 draws) 5th percentile of the mean month is > 0.
4. **Not a few months.** Still net positive over 2021–2026 after deleting its 3 best months.
5. **Not a knife edge.** In a pre-declared grid of neighbouring parameters (each main parameter at about −50%, −25%, +25% and +50%, and other rebalance frequencies), at least 70% of neighbours are net positive on both DEV and TEST at baseline costs.
6. **Beats chance.** Build at least 200 random versions with the same structure (same number of longs/shorts, same rebalance times and costs, random coin picks). The strategy's full-period result must beat at least 95% of them.
7. **Not a few coins or trades.** Still net positive when the 5 coins that contributed most profit are removed from the universe, and no single position (one coin, one holding period) contributes more than 10% of total profit.
8. **Compounds.** Over 2021–2026 the geometric mean month is > 0 and at least half of the arithmetic mean month. That rules out "high average, account goes nowhere".

Report each check's numbers and PASS/FAIL. Overall ROBUST only if all 8 pass. Then also show the 1x / 1.5x / 2x monthly stats and drawdowns of the ROBUST version.

## Output
In your folder `research/monthly20/r2_<name>/`, write:
- `run.py`;
- `results.json`, with keys `checks: {1..8: {pass, numbers}}`, `robust`, `monthly_1x` {YYYY-MM: %} for 2021-01..2026-10, and `leverage_table`;
- `REPORT.md`, in plain English, about one page.

If the harness blocks writing REPORT.md, put its full text in your final message instead. Do NOT ask anyone to write it for you.

## Safety
- Research only: no orders, no keys, never read .env or secrets.
- Write only inside your own folder and `data/monthly20_r2_<name>/`.
- Never kill processes you did not start yourself: track your own PIDs.
- Be gentle with the Binance API, because other agents share the IP. Prefer data.binance.vision.
