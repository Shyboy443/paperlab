# MACD (12/26/9) long-only ZEC audit — 2026-10-12

Independent reproduction of the operator's MACD backtest spec (Binance SPOT ZECUSDT 4h, 2024-10-11 → 2026-10-11).
Paper research only; one asset, one retrospective window, not evidence of future performance.

## What was run

- **Package files:** `reproduce.py`, `Binance-ZECUSDT-1h.csv.gz`, `manifest.json`, `macd-native-signals.json` and
  `reference-trades.csv` were **not found** on this machine. So:
  - the SHA-256 manifest check was not done;
  - the PineTS signal replay was not done;
  - there was no per-trade comparison against `reference-trades.csv`.
- **Data:** fetched independently from the Binance spot public API by `scripts/macd/zec_fetch_spot.py`:
  - 18,480 hourly candles, 2024-09-01 → 2026-10-11, with no gaps;
  - SHA-256 of our gz file: `803bd7ffb0d3cb55eecce357c50ae95a59a9f3f950fb01df6faca14c93d0705b`. This is our file's hash
    and will not equal the manifest's, because the CSV formatting differs.
- **Audit script:** `scripts/macd/zec_audit.py` aggregates exactly four hourly candles into each UTC-aligned 4h candle
  and computes the EMAs two ways, Pine-style (SMA seed) and pandas `ewm(adjust=False)`. It then simulates the spec:
  - flat start with 10,000 USDT, warm-up signals ignored;
  - a signal at the close of candle t fills at the open of t+1;
  - `units = cash / (open·(1+s)·(1+f))` and `cash = units·open·(1−s)·(1−f)`;
  - a final-candle signal is ignored, and an open position is sold at the final close;
  - drawdown is measured on candle-close equity, with 10,000 included in the running peak.

## Results against the checkpoints

| Check | Reference | Ours | |
|---|---|---|---|
| 4h candles / tradable | 4,620 / 4,380 | 4,620 / 4,380 | ✓ |
| First tradable open / final close | 33.83 / 1,221.98 | 33.83 / 1,221.98 | ✓ |
| Crossovers in the window, Pine vs pandas EMA | match | 322 = 322, identical | ✓ |
| Closed trades | 161 | 161 | ✓ |
| Ending cash | 308,490.275496 | 308,490.275496 | ✓ |
| Net return | +2,984.902755% | +2,984.902755% | ✓ |
| Max drawdown | 37.310030% | 37.310030% | ✓ |
| First trade | buy 2024-10-16 04:00 @ 38.427684, qty 259.969088692; sell 16:00 @ 37.302538; cash 9,687.809303 | identical | ✓ |
| 0.20% fee + 0.04% slip: return | +1,996.185737% | +1,996.185737% | ✓ |
| 0.20% fee + 0.04% slip: drawdown | 42.427359% | 42.427359% | ✓ |

The first draft of our audit script reported 42.551415% for the stressed drawdown. That was our own timing error: it
marked a fill at the signal candle's close instead of the next candle's open. Once fixed, every checkpoint matches to
six decimals. The full trade list is in `docs/MACD_ZEC_AUDIT_TRADES.csv`.

## Differences from the PaperLab MACD Desk (MACD-1)

1. **Bug found and fixed in the desk engine (`app/donchian/model.run`, shared with the Donchian desk).**
   - The engine traded during the warm-up candles, so profit from before the test window compounded into the reported
     return.
   - The same bug affected the forward paper books: a Donchian book showed about 2,000 USDT minutes after the desk
     started.
   - On the spec data and costs, the engine reported +3,751% before the fix and +2,988% after it.
   - Now warm-up candles only feed the indicators, and every book starts flat.
2. **Final open position.** The desk marks it to the last close without exit costs; the spec sells it. That accounts
   for the remaining +2,988% vs +2,985%, and for 160 closed + 1 open vs 161 closed.
3. **The desk's market and costs differ from the spec.** It trades Binance USDT perpetuals rather than spot, pays
   taker 0.05% + 0.02% slippage per side rather than 0.10% + 0.02%, and applies real funding. Its window is the last
   730 days rather than fixed dates. Same engine, ZEC:

| Setting | Return | Max drawdown |
|---|---|---|
| Spot, 0.10% + 0.02% (the spec) | +2,988% | 37.3% |
| Spot, 0.05% + 0.02% | +3,526% | 36.6% |
| Perp, 0.10% + 0.02%, no funding | +3,420% | 36.6% |
| Perp, 0.05% + 0.02%, no funding | +4,021% | 36.0% |
| Perp, 0.05% + 0.02%, funding (the desk) | +4,451% | 35.8% |

## Limitations

- **Concentration:** the result is one coin in one window. ZEC's own buy & hold over the desk's two years was
  about +3,400%, so most of the gain is the coin's rally, not the rule.
- **Other coins:** on the desk's other coins the same rule is mixed to negative.
- **Drawdown measure:** close-to-close; the intrabar drawdown is larger.
- **Leverage:** fully funded spot at 1x. This says nothing about liquidation risk with leverage.

## Update: the desk now runs the audited settings (MACD-2, 2026-10-12)

At the operator's request the MACD Desk switched to the audit's settings:
- Binance **spot** ZEC/USDT, 4h, long or cash, 10,000 USDT;
- 0.10% commission + 0.02% slippage per side, with the exact entry and exit formulas;
- the backtest on the fixed 2024-10-11 → 2026-10-11 window, an open position sold at the final close.

`app/macd/service.py`, running against live Binance spot data, reproduces the audit exactly: 161 trades,
308,490.275496 USDT, +2,984.902755%, max drawdown 37.310030%, and every trade matches `MACD_ZEC_AUDIT_TRADES.csv`.

- **Buy & hold:** ZEC over the same window returned +3,512%, so the rule made less than simply holding ZEC.
- **Forward book:** a fresh 10,000 USDT book starts at deploy time (`macd-spot-desk.db`).
- **Removed:** the 10 perpetual coins of MACD-1.

Also fixed: spot candle requests are capped at 1,000 per request by Binance; the paging stopped early when it expected
1,500.

## Follow-up review (2026-10-12): futures figures, funding, liquidation, ranking

### The two futures figures, reconciled (MACD-1, retired)

Both figures come from one snapshot: the local Binance USDⓈ-M perpetual database (`macd-local.db`), built 2026-10-11
20:36 UTC.
- **Candles:** 4,560 ZEC 4h candles, 2024-09-11 20:00 → 2026-10-11 16:00 (open times); sha256 of the canonical
  `[t,o,h,l,c]` list `b2412e1805b78d94…`.
- **Funding:** 2,280 settlements, 2024-09-12 00:00 → 2026-10-11 16:00; sha256 `524a141f7befa085…`.

Both runs used the same configuration: long only, the whole book at 1x, taker 0.05% + slippage 0.02% per side, real
funding, 1,000 USDT.

| Figure | Window (UTC, end exclusive) | Closed + open | Last close | Return | Without funding |
|---|---|---|---|---|---|
| +4,451% (audit table) | 2024-10-11 00:00 → 2026-10-11 00:00 | 157 + 1 | 1,221.36 | +4,450.64% | +4,020.67% |
| +4,642% (summary) | 2024-10-11 20:00 → 2026-10-11 20:00 (rolling 730 days) | 157 + 1 | 1,273.06 | +4,642.41% | +4,195.10% |

The trades are identical; the first entry is 2024-10-16 04:00 @ 38.4377 in both. The whole difference is the open
position marked at the later close: 45.5064 × 1,273.06 / 1,221.36 = 47.43, which is +4,642%.

### Funding ledger

`docs/MACD1_ZEC_PERP_FUNDING_LEDGER.csv` lists every settlement in window A while the position was long. Each row has the
time, rate, Binance mark price, quantity, notional at mark, the payment at mark, and what the engine booked.

| | Settlements | USDT |
|---|---|---|
| Charged to the long (positive rate) | 759 | −564.11 |
| Paid to the long (negative rate) | 332 | +1,069.50 |
| Net | 1,091 | +505.39 |

- **Where the credits came from:** mostly April 2025 (+103.6), November 2025 (+256.1) and June 2026 (+260.2). In those
  months ZEC funding was deeply negative while the book was long and large.
- **Settlement intervals:** all 2,279 are 8 h to within 7 ms. The "7 h gap" reported earlier was an artifact of floor-dividing an interval of 8 h − 1 ms; Binance stamps some settlements a millisecond late.
- **Engine price corrected:** the engine had priced each settlement at the 4h close (+504.04). Since 2026-10-12 it uses
  the recorded mark price (`app/donchian/model.run`).

### Liquidation (Donchian, the remaining perpetual desk)

- **Margin model:** whole-book 1x, with the position notional equal to the wallet.
- **Liquidation prices:** Binance's isolated-margin formula, with no tier maintenance amount, gives 0 for a long and
  2·entry/(1+MMR) for a short, about +95% at a conservative 2.5% MMR.
- **Trades checked:** all 1,254 trades of the 2-year, 10-coin backtest, against 4h *trade-price* candle highs and lows
  (`scripts/macd/donchian_liq.py`).
- **Closest short:** LINK, 2025-03-04, +26.0% intrabar, which is 27% of the distance to its liquidation price.
- **Worst long:** −20.9% intrabar. This simplified formula gives 0 as a 1x long's liquidation price. That holds only while collateral equals the entry notional; fees and funding reduce collateral. See the strict check below.

**Limits of this check:**
- It uses last-price candles, not mark-price candles.
- Fees and funding paid shave the wallet slightly.
- Any leverage above 1x changes it completely.

MACD-2 is fully funded spot: no borrowing, so no margin liquidation, although the capital can still lose heavily. A 1x perpetual gets no such blanket assurance.

### Package audit still open

`reproduce.py`, `Binance-ZECUSDT-1h.csv.gz`, `manifest.json`, `macd-native-signals.json` and `reference-trades.csv` have
not been provided.
- **Done:** matching the totals and the first trade, plus our own 161-trade list, supports the reproduction.
- **Not yet proven:** that every candle, signal and trade matches the package. That needs a per-timestamp comparison
  once the ZIP is supplied.


## Second review (2026-10-11 22:30 UTC): package comparison, corrections, strict liquidation check

All times are UTC. The clock reads 2026-10-11 22:28 UTC when this was written; earlier sections dated "2026-10-12" use
Sri Lanka local time.

### Package `MACD_ZEC_Exact_Reproduction.zip`: every candle, signal and trade compared

Unpacked into an isolated folder. `reproduce.py` and `backtest.py` were read before running: they do file I/O only in
their own folder and make no network calls.

- **SHA-256:** `c009d023…bf55190` matches `manifest.json`.
- **`reproduce.py`:** passes all its assertions, including "All 161 reference trades match". The regenerated
  `reproduced-*` files equal the shipped ones; the only difference is Windows line endings.
- **Independent comparison** (`scripts/macd/package_compare.py`), by timestamp:
  1. **Hourly candles:** 18,480 package candles against our own Binance spot API download. None missing, none extra,
     0 field mismatches across OHLCV.
  2. **Native PineTS signals** (`macd-native-signals.json`) against the desk's MACD-2 crossovers: 161 buys and 161
     sells in the window, identical. The 13 warm-up crossovers also match, because the desk seeds its EMAs as Pine does.
     The package's pandas check differs on four early warm-up events.
  3. **Trades:** all 161 reference trades against the desk's MACD-2 code on the package data. Entry, exit and signal
     timestamps are identical; the largest relative difference in price, units or cash is 2×10⁻¹⁶.
  4. **Equity:** all 4,380 candle values match the package's reproduced equity within 10⁻⁶ relative.
- **Buy & hold:** the package's +3,503.46% charges fees and slippage on entry and exit. Our +3,512.1% is the raw price
  ratio, 1,221.98 / 33.83.

### Correction: the common window

The ranking page's window was reported as ending "2026-10-12 08:08 UTC". That was wrong: the stored end was
2026-10-11 22:08 UTC, mislabelled by about 10 h. No-Wick's window end is the close of its last completed 1-minute
candle; the live check showed end 22:19:00 against computed 22:19:26. The 4h desks store completed candles only. The
common window is now snapped inward to 4h boundaries, so every desk covers the same completed candles.

### Futures figures recomputed after the mark-price funding fix (MACD-1, retired)

| Window (UTC, end exclusive) | Before | After (mark-price funding) | Without funding |
|---|---|---|---|
| A: 2024-10-11 00:00 → 2026-10-11 00:00 | +4,450.64% | **+4,455.56%** (45,555.639268) | +4,020.67% |
| B: 2024-10-11 20:00 → 2026-10-11 20:00 | +4,642.41% | **+4,647.55%** (47,475.513236) | +4,195.10% |

The equity bridge from A to B is exact:

> 45,555.639268 + 37.300912146 ZEC × (1,273.06 − 1,221.36) [= +1,928.457158 price P&L] − 8.583190 extra funding
> [2026-10-11 00:00 +0.004107%, 08:00 +0.008200%, 16:00 +0.006323%] − 0 costs (no fills) = 47,475.513236.

The residual is 0 (`scripts/macd/perp_bridge.py`).

### Strict liquidation check (Donchian DTR-1, 1x perps): `scripts/macd/donchian_liq_strict.py`

Inputs:
- Binance **mark-price** 4h klines for all 10 coins;
- Binance's published USDⓈ-M maintenance-margin **brackets** (MMR plus cumulative maintenance amount by notional),
  retrieved 2026-10-11 22:31 UTC;
- **collateral** = notional after the entry fee, updated by every funding settlement. Charges inside a 4h candle are
  assumed to land before its worst mark.

Condition tested: maintenance margin ≥ wallet + unrealized P&L at that worst mark.

- **Result:** 0 liquidations in 1,254 trades. The highest margin ratio was **1.50%** (Binance liquidates at 100%), on a
  SUI short entered 2025-03-04: mark +19.9% against it, balance 623.49 USDT, maintenance 9.33 USDT at 1.00% MMR.
- **Limits:**
  - brackets are today's; historical tiers may have differed;
  - 4h mark candles, not tick data;
  - collateral is isolated per position at 1x;
  - paper only, and any leverage invalidates the result.

### What the ranking still cannot claim

The common window equalises dates and capital per coin. It does not equalise the desks:
- MACD is one retrospectively selected coin on spot;
- the others are 2 to 10 coins on perpetuals, with different sizing.

The package's own README makes the same point. The same MACD rule across its 84-asset cohort returned +11.54% with a
57.24% drawdown, and −24.29% on average once ZEC is excluded. The ranking orders these books; it does not identify the
best strategy.

### Third review: drawdown basis and window start (ranking)

- **The stop-touch figure was never an upper bound.** Open gains can lift the equity peak before a trade gives them back,
  and stops can fill worse than their trigger. A unit test shows it: a long that runs to +3R and closes at +1R has 0%
  closed-trade drawdown and 0.5% under the stop scenario, but about 1% real drawdown from the open-profit peak. The
  figure is now labelled **"stop-loss stress scenario"**, shown for information only, and never used to rank.
- **Eligibility now uses actual equity curves, including open positions:**
  - Donchian and MACD: 4h closes;
  - No-Wick: every 1m close (0.5% of realised equity per trade, marked by side × (close − entry) / |entry − stop|);
  - Divergence: every 15m close, with overlapping independent signals marked together.

  Exit costs enter only through each trade's realised R. Books without a marked curve, currently the No-Wick live
  books, are tagged **provisional**.
- **Common-window start.** My earlier "July 14 06:09 UTC" was another mislabel: the data actually began
  2026-07-13 22:09 UTC, and rounding up to 4h gave 2026-07-14 00:00, which is later, not earlier. That start still
  ignored warm-up and open trades. The rule is now: the first 4h boundary at least 6 h after No-Wick's first stored
  minute (its 15m ATR14 needs 3.5 h, its 1m EMA50 50 min) at which no No-Wick book holds a trade. This matters because
  No-Wick's replay carries state across its start. Every book then starts flat: the 4h desks re-simulate from that
  boundary, and Divergence signals are independent. The endpoint reports the data start, the warm-up and the chosen
  boundary.
- **Bug found while checking the first mark-to-market run.** It showed Divergence at a 14.48% drawdown, against a 1.07%
  stop-loss stress figure. The per-trade worst marks on the server were all ≥ −1.27R, which ruled the trades out as the
  cause. The new code had marked each freshly opened trade with the coin's last known price, possibly months old.
  Fixed: a new trade is marked at its fill until the next candle close, and the price resets when a coin has no open
  trade. A regression test covers it.

Live result after the fix. Common window: 2026-07-16 04:00 → 2026-10-11 20:00 UTC; No-Wick data starts 2026-07-13 22:54.

| Book (common window) | Net return | Max DD (marked) | Stop-loss stress | Trades |
|---|---|---|---|---|
| MACD-2 (ZEC spot, 1 coin) | +57.31% | 31.11% | — | 21 |
| Divergence (BTC/ETH) | +0.97% | 0.25% | 0.25% | 2 |
| Donchian (10 perps) | −3.09% | 17.41% | — | 145 |
| No-Wick 15m (10 perps) | −5.26% | 6.29% | 6.29% | 374 |
| No-Wick 5m (10 perps) | −81.77% | 81.99% | 82.00% | 7,132 |

Full window, Divergence: marked drawdown 1.39% against 1.07% stress, the open-profit giveback the stress scenario
cannot see.
