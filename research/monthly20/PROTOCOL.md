# Monthly 20% research round — shared protocol

**Goal (from the user):** find trading strategies whose returns over the **last 2 years** average **+20% or more per month**.

**Target as the user means it:** a simple average of +20% per month, i.e. 20% × 12 = +240% a year without compounding.
That is exactly the PASS rule below: the arithmetic `mean_month` ≥ 20.
**Honest prior:** even +240% a year is far above anything found here. Nothing in this project has come close; the best
honest result so far is about +15% a YEAR. The only realistic ways to get there are heavy leverage, tiny illiquid
coins, or overfitting. So every agent must report what it really finds, including "no", plus the leverage and the risk
of ruin that a 20%/month target would need.

## Windows (fixed in `common.py`)
- **DEV** 2021-01-01 → 2024-10-31: design, explore and tune here only.
- **TEST** 2024-11-01 → 2026-10-21 (the "last 2 years", 24 calendar months, the last one partial): touched **once**.
- Before the first TEST run, write `PREREG.md`: exact rules, parameters, universe rule, sizing and leverage. Do not edit it after.
- If you change anything after seeing TEST, that is a NEW attempt. Report every attempt and its TEST result; never only the best.

## Realism
- **Signals** come from closed bars only; fill at the next bar's open. No look-ahead in the universe either: use `liquid_universe(at=...)`.
- **Costs:** 0.05% fee + 0.02% slippage per side (`FEE`, `SLIP`). Funding on perps (`funding()`). Rerun everything with costs doubled.
- **Limit / maker fills** may only be assumed when the price trades *through* the limit, not just touches it.
- **Leverage** is allowed. Model liquidation: a position is wiped when its loss reaches 1/leverage − 0.5%, checked on bar highs and lows. Compounding position sizes must stay within what the coin's volume could absorb (≤1% of the bar's quote volume).
- **Survivorship bias:** delisted coins are missing from Binance's list. Say so when it matters.

## Data
- `common.py` gives you a shared, cached loader for Binance public data: `klines(symbol, interval)`, `funding(symbol)`, `perp_symbols()`, `liquid_universe()`.
- Use it. Do not hammer the API: ten agents share one IP. Prefer 1h or slower bars; 15m only for a handful of coins.
- Any other **free, public** data (web pages, CSV datasets, fear & greed index, etc.) is fine for ideas or signals if it is point-in-time. No sign-ups, no API keys, nothing paid.

## Hard limits
- Research only: **no orders**, no exchange accounts, no keys. Never read `.env`, the key vault or secrets.
- Do not edit anything outside your own folder `research/monthly20/<your_folder>/` (reading is fine). Do not touch `app/`, live bots, the IBS files, git or Railway.
- Big caches go in `data/monthly20_cache/` (via `common.py`) or `data/monthly20_<your_folder>/`, not in the research folder.

## Deliverables in your folder
- `run.py`: reproduces every number.
- `PREREG.md`: written before the TEST run.
- `results.json`:
  - `{"agent", "idea", "attempts": [{"name", "prereg": true/false, "dev": summary(), "test": summary(), "test_double_cost": summary()}], "best_honest": name, "verdict": "PASS"|"FAIL", "leverage_needed_for_20pct": number|null}`
  - `summary()` from `common.py` gives the monthly table and the stats.
- `REPORT.md`, in plain English, at most about one page:
  - the idea;
  - the TEST monthly table;
  - mean, median and worst month, max drawdown, trades;
  - what leverage 20%/month would need and how often that blows up;
  - the verdict.
- **PASS** only if the pre-registered TEST mean month is ≥ +20% at baseline costs, the mean stays > 0 at doubled costs, the strategy is not wiped out, and no single trade makes more than half the profit.
