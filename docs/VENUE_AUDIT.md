# Venue audit (2026-09-23)

## What PaperLab runs today

| component | venue | what it uses |
|---|---|---|
| Paper engine (27 v1 bake-off books, Railway) | **BYBIT LINEAR** | `EXCHANGE=bybit`, `MODE=LIVE_OVERRIDE_I_UNDERSTAND`, real Bybit API keys, `DRY_RUN=true` (simulated fills on live Bybit data), fee schedule `bybit_linear` maker 0.020% / taker 0.055% |
| Specialist arena (v1 `9bbe42e53fd8`, v2 DEV `78b995a21d07`, v2 TEST `8227dfb422cb`) | **BINANCE USD-M** | Binance archive klines + funding, `binance_usdm` fees maker 0.020% / taker 0.050%, Binance exchangeInfo filters and leverage brackets |
| Multi-year validation of the three v2 candidates | **BINANCE USD-M** | same as the arena, 2021-01 → 2025-10 |
| Live shadow (frozen v2 bots + Jev V1 twins, Railway) | **BINANCE USD-M** | Binance public market data (klines `/market`, bookTicker `/public`, premiumIndex), Binance fees and filters |

The only path to real money configured anywhere in PaperLab is the paper engine's Bybit live
override: Bybit keys exist on Railway and nowhere else. No Binance trading key is configured.

## Target live venue

**BYBIT_LINEAR** — as configured. This is a configuration fact, not a strategy result; if the
intended real-money venue is Binance, switch `EXCHANGE`, and Binance becomes the explicit specialist
target (the Binance evidence below then counts as venue evidence too).

Because the target is Bybit and the research is Binance:

* **STRATEGY ROBUSTNESS** — tested historically on Binance USD-M data (DEV, TEST, multi-year). It
  says whether the signal has an edge on the coin; it is not venue evidence.
* **BYBIT EXECUTION VALIDATION** — required before any live promotion. A candidate that survives the
  historical stages must also survive Bybit-specific shadow execution: Bybit market data, Bybit
  fees, Bybit instrument filters, Bybit funding, Bybit spread, Bybit risk limits and mark price.

No result, card or label presents Binance numbers as Bybit numbers: every validation card carries
its venue (BINANCE USD-M for historical and live shadow, BYBIT LINEAR as the target).

## Venue differences for the three candidates (read 2026-09-23 from the public endpoints)

| | XRPUSDT Binance | XRPUSDT Bybit | BNBUSDT Binance | BNBUSDT Bybit |
|---|---|---|---|---|
| taker / maker fee | 0.050% / 0.020% | **0.055%** / 0.020% | 0.050% / 0.020% | **0.055%** / 0.020% |
| tick size | 0.0001 | 0.0001 | 0.01 | **0.10** |
| lot step / min qty | 0.1 / 0.1 | 0.1 / 0.1 | 0.01 / 0.01 | 0.01 / 0.01 |
| min notional | 5 USDT | 5 USDT | 5 USDT | 5 USDT |
| funding interval | 8h | 8h | 8h | 8h |
| funding rate (that hour) | 0.0100% | 0.0100% | 0.0000% | −0.0022% |
| quoted spread | 0.66 bps | 0.66 bps | 0.13 bps | **1.31 bps** |
| tier-1 maintenance margin | bracket table | 0.50% (≤ 50k) | bracket table | 0.67% (≤ 10k) |

* **XRP**: filters, funding interval and spread match; Bybit costs 10% more in taker fees.
* **BNB**: Bybit's tick is ten times Binance's, so every round trip crosses roughly one extra full
  tick (≈1.2 bps at 760 USDT, ≈2–3 bps at 2021–2022 prices) on top of the higher taker fee. The
  multi-year cost-headroom figures include this for the Bybit comparison.
* Liquidation: both venues liquidate on mark price with tiered maintenance margin; the candidates run
  at roughly 0.4x–1.5x effective leverage, far from either venue's liquidation.

## What still has to be modelled before a Bybit live decision

Bybit historical klines and funding for the candidate coins, Bybit risk-limit tiers in the
liquidation model, Bybit mark price, and a forward Bybit shadow of any candidate that passes the
multi-year stages. None of it is needed to answer whether the signals have an edge; all of it is
needed before a Bybit bot trades real money.
