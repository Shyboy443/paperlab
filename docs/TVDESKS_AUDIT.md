# T3-CCI and CHI desks — reconciliation audit (2026-10-12)

Two TradingView strategies run as **paper-only** desks in PaperLab:

| Desk | Source script | Books | Rule version |
|---|---|---|---|
| T3-CCI | HPotter, "FX Sniper: T3-CCI Strategy" (tfkYcvBX) | ZEC, CAKE (4h, spot, long/cash) | T3CCI-R1 |
| CHI | juanchez, "CHI" (8F6WNLkO) | the package's 84 full-history assets (4h, spot, long/cash) | CHI-R1 |

All results on this page are **historical**. Nothing here is a forecast and nothing here enables live trading.
No parameters were optimised. No rule was changed to make a comparison match.

## How to reproduce

```bash
# 1. unpack the reference package (pristine copy) and keep a separate working copy for the package's own controls
python scripts/tvdesks_verify.py <TradingView_Research folder> --out docs/tvdesks     # app vs package -> audit.json
python scripts/tvdesks_build_history.py <TradingView_Research folder>                 # rebuilds app/tvdesks/history/*.json.gz
python -m pytest -q tests/test_tvdesks.py
```

The package's own controls run inside a working copy of the package (`python test_broker.py`, `python verify_execution.py`,
`python verify_math.py`, `python verify_benchmarks.py`, `npm ci && python verify_prefix.py`, `python screen.py`,
`PYTHONUTF8=1 python finish_report.py`). The verification uses only the recorded snapshot inside the package, never a fresh
download.

## Package integrity and package controls

| Control | Result |
|---|---|
| SHA-256 of every file in `package-manifest.json` | **571 / 571 match** |
| `test_broker.py` | 11 OK |
| `verify_execution.py` | 12 independent checks + all 161 MACD trades OK; MACD ZEC baseline +2,984.9027549618163 % |
| `verify_math.py` | 20 passed |
| `verify_benchmarks.py` | buy-and-hold controls match |
| `verify_prefix` (PineTS 0.11.0 via `npm ci`) | 36 / 36 passed |
| `screen.py` re-run | 19,008 individual evaluations, 216 cohort rows, 0 exclusions; equal to the shipped results within 2.3e-14 |
| `finish_report.py` | research-summary.json, RESEARCH_REPORT.md, trade CSVs and monthly CSVs regenerated identical |

## App vs package (`scripts/tvdesks_verify.py`, `docs/tvdesks/audit.json`)

**544 of 545 comparisons pass.** In total, 8,462 trade rows and 2,312,640 equity / high / low values were compared by
UTC timestamp.

| Group | Passed | What is compared |
|---|---|---|
| Package hashes | 1 / 1 | 571 SHA-256 values |
| 4h candles | 84 / 84 | the app's aggregation from 1h vs the package's aggregation, every candle and field |
| Raw requests | 85 / 86 | the app's indicators vs the saved PineTS buy/sell request indices, every index |
| Trades | 176 / 176 | every trade field vs the package broker (`screen.signal_path` + `screen.replay`) and the shipped CSVs, at base and doubled costs |
| Equity | 176 / 176 | every close / high / low equity value |
| Monthly | 2 / 2 | T3-CCI ZEC monthly and the CHI portfolio monthly vs the shipped CSVs |
| Summary rows | 3 / 3 | individual-results.csv (T3 ZEC/CAKE, CHI × 84, both costs) and cohort-results.csv |
| Checkpoints | 17 / 17 | the values below |

### The one mismatch: CHI raw requests, SHIB

> first mismatch: CHI requests SHIB, sell_indices position 152: expected index 3840, actual 3839
> (2026-06-02 20:00 UTC).

- **Cause:** PineTS 0.11.0 rounds indicator outputs (SMA / EMA / stdev) to 10 decimals. SHIB prices are around 1e-5, so
  this rounding moves the Bollinger-width change: full precision gives roc = 9.0199 at index 3839, while PineTS gives
  8.9416. The width "crosses 9 %" one candle earlier at full precision.
- **The app keeps full precision.** That is what TradingView itself computes, and the rule is not altered to match.
- **Effect:** none on trades or equity. SHIB was flat (no position) at that time, so a sell request does nothing.

### Checkpoints (all matched)

| Checkpoint | Value |
|---|---|
| T3-CCI ZEC trades | 174 |
| T3-CCI ZEC ending capital | 242,415.35909601 |
| T3-CCI ZEC net return | +2,324.153591 % |
| T3-CCI ZEC close-equity drawdown | 48.492574 % |
| T3-CCI ZEC OHLC drawdown bound | 51.465752 % |
| T3-CCI ZEC return at doubled costs | +1,496.608370 % |
| T3-CCI CAKE net return | +409.133158 % |
| T3-CCI CAKE close-equity drawdown | 33.506498 % |
| T3-CCI CAKE OHLC drawdown bound | 39.506146 % |
| CHI 84 trades | 1,860 |
| CHI 84 ending capital | 986,992.00751935 |
| CHI 84 net return | +17.499049 % |
| CHI 84 close-equity drawdown | 29.100771 % |
| CHI 84 OHLC drawdown bound | 30.210689 % |
| CHI 84 return at doubled costs | +11.385354 % |
| CHI 84 without ZEC | +13.530781 % |
| CHI 84 profitable coins | 39 |

### Other CHI figures

- First 18 months: +7.816 %. Last 6 months: +8.981 %.
- **45 of 84 coins lost money.** The median coin returned about −5.56 %.
- An equal-weight buy-and-hold of the same 84 coins returned +6.85 %.

## Required caveats

- **T3-CCI on ZEC (+2,324 %) underperformed** both the audited MACD ZEC spot strategy (+2,984.90 %) and simply holding
  ZEC (+3,503.46 %) over the same window.
- **CHI lost money on 45 of 84 coins.** Its portfolio result is a small positive number, and ZEC contributes a large
  part of it.
- These are research reproductions, not official TradingView Strategy Tester results. The package does not use
  TradingView's broker, and this app does not use PineTS's native strategy broker either.
- Results are scaled to 10,000 USDT per book. Scaling them down to a small account (for example 10 USDT) does **not**
  mean the orders would be executable. See the execution simulation below.

## Rules as implemented

**Shared execution (the package's independent cash broker):**

- A request on candle *i* fills at the open of *i+1*.
- Long or cash only. No pyramiding.
- A buy fills only while flat; a sell fills only while held.
- Requests on the final candle never fill.
- Costs are 0.10 % commission plus 0.02 % slippage, per side:
  - entry units = cash / (open·(1+s)·(1+f));
  - exit cash = units·open·(1−s)·(1−f).
- The historical test ends with a forced sale at the final close.
- Drawdown is measured on close equity (the initial capital included). The OHLC bound takes each candle's high before
  its low.

**T3-CCI:**

- CCI(14) on close, using an SMA and the mean absolute deviation.
- Six stages of e = 0.5·x + 0.5·nz(e[1]), then
  T3 = −b³·e6 + 3(b²+b³)·e5 − 3(2b²+b+b³)·e4 + (1+3b+b³+3b²)·e3, with b = 0.618.
- pos = sign(T3). When T3 is 0, the previous pos is kept.
- A buy request is made on every candle while pos = 1, and a sell request while pos = −1. Requests are date-gated.

**CHI:**

- EMA20 and EMA50 (SMA-seeded) on c, where the last candle uses the previous close (the source's `islast`
  substitution).
- width = 4·stdev(40)/SMA(40); roc = 100·Δwidth/width[1]. A "cross" means roc > 9 after roc[1] ≤ 9.
- Long stop = highest(high, 22) − 4·RMA(TR, 22).
- Buy = fast > slow AND cross.
- Sell = crossunder(close, long stop) OR (fast < slow AND cross). The stop exit is not date-gated, as in the saved
  instrumentation.

## Forward paper books (what runs on the server)

- **Start:** each desk starts flat at the first 4h boundary after it was created. Nothing from the historical test is
  carried over.
- **Data:** completed 1h spot candles, aggregated to 4h only when all four hours exist.
  - Binance spot for 78 coins.
  - Gate for KAS, ONDO, AKT, CRO and GT.
  - Bitget for BGB.
  - These are public GET endpoints, with no keys and no orders.
- **Decision and fill:** the decision is made at each 4h close, on the data known at that moment. The order fills at
  the next 4h open as soon as that candle's first hour is known.
- **CHI `islast`:** each forward decision uses the decision-time substitution, because the candle being decided is the
  last candle at that moment. When a later re-computation (where that candle is no longer last) would have decided
  differently, the difference is recorded as an *islast divergence*. It is never applied retroactively.
- **Two modes per book:**
  - **execution:** units are rounded down to the exchange's step size. Orders below the exchange's minimum quantity or
    minimum notional are *skipped*, and each skip is recorded with its reason. Rounding leftovers are tracked as dust.
  - **research:** fractional units, the same as the package.
- **Restart safety:**
  - State lives in SQLite on the volume (`tv-t3cci.db`, `tv-chi.db`).
  - A fill is idempotent by its fill time, so a restart never fills or books anything twice.
  - The forward start time is persisted.
- **Monitoring:** a heartbeat is shown on the desk. A watchdog sends one Telegram alert after a 10-minute heartbeat gap,
  and one when the worker recovers.
- **Switches:** `T3CCI_ENABLED` and `CHI_ENABLED`, both on by default. Disabled in tests.

### Execution simulation of the historical window

The history files also replay the historical window in execution mode, using today's order filters at 10,000 USDT per
book.

- **Skips:** 0 on every book.
- **Differences from the research mode:** at most 0.013 percentage points for T3-CCI and 0.005 for CHI, from step
  rounding.
- **Caveat:** filters change over time, and smaller books would hit minimum-notional skips. The forward execution books
  record any such skip.

## CHI decided in real time vs the backtest (`scripts/tvdesks_chi_realtime_study.py`)

The CHI source uses `close[barstate.islast ? 1 : 0]`: on the last candle, the previous close stands in for the real
one. In a historical run only the dataset's final candle is "last", so every past signal used its real close. In real
time, the candle being decided is always the last one, so the substitution applies to every decision. That is how the
forward paper desk decides, and how any live trading of CHI would have to decide.

- **What the substitution suppresses:** CHI enters when the Bollinger width jumps by more than 9 % in one candle. That
  jump almost always comes from the newest candle's own move, and that move is exactly what the substitution removes.
  The exit (close below the chandelier stop) uses the real close and is unaffected.
- **The study:** it replays the package's recorded snapshot (84 books, same window, same broker, same costs). Each
  candle is decided at its own close. Decision-time values are computed in one pass and checked against the exact
  prefix computation on 839 sampled decisions: 0 mismatches.

| CHI, 2024-10-11 → 2026-10-11 | Backtest (reproduced) | Decided in real time |
|---|---|---|
| Entry signals | 3,205 | 88 (45 of them also in the backtest) |
| Trades | 1,860 | 87 |
| Portfolio return | +17.50 % | +0.83 % |
| Close-equity drawdown | 29.10 % | 2.84 % |
| Coins with no trade at all | 0 | 28 of 84 |
| Coins: up / down | 39 / 45 | 20 / 36 |
| ZEC | 31 trades, +346.87 % | 2 trades, +19.50 % |

**Conclusion:** the historical CHI result is not reachable in real time. Expect the forward paper books to trade rarely. For this
reason CHI has no live switch. The result file is `docs/tvdesks/chi_realtime_study.json`; the desk serves a compact
copy, `app/tvdesks/history/chi_realtime.json`. The study and the rule are unchanged: nothing was tuned.

## Limitations

- CHI's backtest is not reachable in real time (see above). T3-CCI has no such substitution.
- This is not official TradingView parity: PineTS and full-precision Python can differ near thresholds (see SHIB
  above).
- Spot venue data may differ slightly from TradingView's own data feed.
- The forward books are paper only: no exchange orders, no keys, no real money.
