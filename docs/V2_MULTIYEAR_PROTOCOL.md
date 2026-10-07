# v2 multi-year validation protocol (fixed 2026-09-23, before any multi-year result)

Written and committed to the repository BEFORE the first multi-year replay of the three candidates
ran. Every threshold below is recorded in the run's configuration (`protocol_fingerprint`). Changing
any of them after seeing results creates a new protocol version; it never re-labels an old result.

## Candidates (frozen, unchanged)

| bot | coin | signal timeframe | context | source |
|---|---|---|---|---|
| S26-XRPUSDT-30m@20x-v2 | XRP only | 30m | 4h trend | TEST `8227dfb422cb`: ADVANCE |
| S26-BNBUSDT-30m@20x-v2 | BNB only | 30m | 4h trend | TEST `8227dfb422cb`: ADVANCE |
| S12-XRPUSDT-15m@20x-v2 | XRP only | 15m | 1h trend | TEST `8227dfb422cb`: ADVANCE |

Each run verifies, before any replay, that the candidate's source fingerprint equals the v2 freeze
(`docs/V2_FREEZE.md`: base `bef9ced73cec`, S26 `7da4115014b8`, S12 `b6c81f3ee712`) and that its
configuration is the TEST run's configuration: strategy parameters (class defaults), timeframe,
symbol, AGGRESSIVE risk profile, cost gate (edge-to-cost >= 2.0), Binance USD-M fee schedule
(maker 0.020% / taker 0.050%), execution config, leverage ceiling 20x with needed-leverage sizing,
starting balance 20 USDT. A mismatch aborts the run: it would be a new version, not v2.

## Data

* Venue data: **Binance USD-M** 1m klines and funding from data.binance.vision.
* Evaluated window: **2021-01-01 00:00 UTC → 2025-10-31 23:59 UTC** (58 months).
* December 2020 is replayed first as indicator warmup only (`since_ms` = 2021-01-01: no entries).
* Exchange filters (tick, lot, minNotional) and leverage brackets are the ones the TEST run recorded
  (fetched 2026-09-23). Historical filters were not reconstructed; at these position sizes the
  binding minimum is minNotional (5 USDT for XRP and BNB), unchanged across the window.
* Mark price is not modelled historically; stops and liquidation are checked on the 1m last-price
  path (entries run at 0.4x–1.5x effective leverage in DEV/TEST, far from liquidation).

## Simulation

* ONE continuous master ledger per bot: 20 USDT at 2021-01-01, never reset, compounding through
  the whole window with the frozen sizing (1% ordinary risk, ATTACK/DEFENSIVE/HALTED machine).
* The frozen AGGRESSIVE profile halts a book permanently at a 30% drawdown from its peak. If the
  master ledger halts, or is liquidated, that validation path FAILS.
* 1m bars drive execution (latency, fills, stops, trailing, liquidation, funding); signals only on
  the bot's own timeframe.

## Reporting

* Windows: calendar quarters (the bots trade ~14–23 times a month in DEV/TEST, so a quarter holds
  ~40–70 trades). A window is ACTIVE with >= 5 closed trades. Window return is measured on the
  continuous ledger (equity at quarter end / equity at quarter start); nothing is re-based.
* Year by year: 2021, 2022, 2023, 2024, 2025 (Jan–Oct).
* Regimes (per coin, daily bars, information up to the previous day's close only):
  trend = EMA50 of the daily close and its 10-day slope: TREND_UP when close > EMA50 and slope >
  +2%, TREND_DOWN when close < EMA50 and slope < −2%, otherwise RANGE; volatility = 20-day realised
  volatility ranked against the trailing 365 days (>= 120 days of history): HIGH_VOL >= 67th
  percentile, LOW_VOL <= 33rd, otherwise NORMAL_VOL. A trade takes the regime of its entry day.
  Flag (not a gate): all net profit comes from one trend regime or one volatility regime.
* Robustness: largest winner and top-3 winners as a share of net profit; share of gross profit
  from the top 10% of winners; net result without the best trade and without the best 3.
* Cost headroom: break-even round-trip cost = (gross PnL at decision prices + funding) / entry
  notional, in bps; compared with the simulated Binance cost and with Bybit linear fees
  (taker 0.055% / maker 0.020%) applied to the same fills.

## Monte Carlo

10,000 paths per bot, block bootstrap (block of 5 consecutive trades, preserving loss clustering)
of per-trade returns relative to equity at entry, compounded from 20 USDT, fixed seed 20260923.

**Ruin (explicit):** a path is ruined when its drawdown from peak reaches **30%** — the frozen
AGGRESSIVE `halt_drawdown`, at which the real bot stops trading permanently. Ending-equity figures
apply that rule (a ruined path stays at its halt equity). The drawdown distribution (median, 95th,
99th, P(>20%), P(>30%), P(>50%)) is reported on the same paths with the halt NOT applied, so the
depth of drawdowns the edge produces is visible.

## Stress (each a full re-simulation of the whole window)

NORMAL · FEES +25% · FEES +50% · SLIPPAGE 1.5x · SLIPPAGE 2x · LATENCY 2x · ALL TAKER · ADVERSE
FUNDING (every settlement charged against the open position, at the historical |rate|) ·
combinations FEES +25% & SLIPPAGE 1.5x, FEES +50% & SLIPPAGE 2x & LATENCY 2x, and ALL (fees +50%,
slippage 2x, latency 2x, all taker, adverse funding) · venue check BYBIT LINEAR FEES.

## Multi-year gates (all must pass; none is relaxed after results)

| gate | threshold |
|---|---|
| sufficient trades | >= 200 closed trades |
| net PnL after all costs | > 0 |
| expectancy | > 0 R |
| profit factor | >= 1.15 |
| max drawdown | < 30% (and the ledger never HALTED) |
| liquidations | 0 |
| windows | profitable ACTIVE quarters > 50% (a majority) |
| Monte Carlo | P(ruin) <= 5% |
| stress | net > 0 AND expectancy > 0 R in every single-factor stress (FEES +25%, FEES +50%, SLIPPAGE 1.5x, SLIPPAGE 2x, LATENCY 2x, ALL TAKER, ADVERSE FUNDING) |
| concentration | net > 0 without the best 3 trades |
| persistence | net > 0 without the best calendar year |

Combination stresses and the Bybit fee check are reported, not gated.

## What a PASS means

A PASS earns FORWARD evidence collection and Bybit execution validation. It is not QUALIFIED and
never live: qualification needs forward shadow evidence on the venue the bot will actually trade,
and going live always needs an operator decision.

## Amendment 1 (2026-09-23, before any S26 result and before any Monte Carlo was computed)

The first run (`97d03df42cb4`, stopped after 8 of 36 replays, all of them S12-XRPUSDT-15m) showed the
master ledger HALTING at a drawdown below 30%. The frozen engine has a second permanent halt that the
text above missed: the RiskManager's strategy halt floor, **equity <= 75% of the starting 20 USDT**
(`strategy_halt_pct` 25%, the same setting the TEST run used). The protocol is corrected to describe
the frozen engine completely; no gate threshold changes, and the correction can only make ruin more
likely, never less:

* **Ruin (Monte Carlo) = the frozen engine's halt, whichever comes first:** equity <= 15 USDT (75% of
  the start) OR drawdown from peak >= 30%. A ruined path stays at its halt equity.
* "Never HALTED" in the drawdown gate covers both halts.
* Added, informational only (not gated): **LATENCY +1 BAR** — every fill one full 1m bar later.
  At 1m resolution, LATENCY 2x (0.4 s -> 0.8 s) still fills on the next bar's open, so it cannot move
  a fill; the gated LATENCY 2x scenario stays exactly as pre-registered and is reported as such.
* Every raw scenario replay is now saved, so the analysis can be re-assembled without re-running.

The S12-XRPUSDT-15m replays of the stopped run are superseded by the re-run under this amendment.
