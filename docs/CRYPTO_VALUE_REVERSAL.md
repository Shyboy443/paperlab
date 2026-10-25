# Value-area reversal across QQQ, BTC, ETH and SOL

This extends the corrected QQQ experiment with **historical paper research**, without changing QQQ's frozen rules, saved history, forward books, stock execution, or the continuous scanner.

The video is https://www.youtube.com/watch?v=dUczefIYKIU. At 13:02–13:09 the presenter discusses **30 minutes**; at 16:15–16:18 he identifies **15 minutes**; at 17:43 he names both. The futures session starts **18:00 America/New_York**, rather than the QQQ adaptation's 09:30 stock session. Crypto candles run around the clock, with that same 18:00 anchor and 23/25-hour daylight-saving sessions.

The source is LuxAlgo's [published Value Area Reversion Signals](https://www.luxalgo.com/library/indicator/value-area-reversion-signals/), included with its MPL-2.0 attribution. The signal retains 60-row candle-volume allocation, the source's radial value-area expansion, fading same-direction volume, an opposing engulfing body, expanding reclaim volume, and 5/10 outside bars. The source's radial expansion can include less than the requested 70%; it is retained for fidelity. It does not measure actual order-flow absorption.

There are 24 settings: BTCUSDT / ETHUSDT / SOLUSDT perpetuals × 15m / 30m × previous / developing profile × video 5 / published 10 outside bars. No configuration is selected or promoted based on its comparison return. The default is BTCUSDT, 15m, previous, 5 bars.

Each run starts with an independent $10,000 wallet, risks at most 0.5% to its excursion stop, uses fractional quantities, caps notional at 100%, and halts new entries at 20% drawdown. An arrow confirmed on a closed candle can enter at the next opening price only if that price remains between stop and target. Stop and opposite value-area target freeze at the signal. Stop wins ambiguous stop/target ties; stop gaps fill adversely; remaining inventory exits at 18:00. One position at a time. These order rules are assumptions added to an indicator which provides no executable backtest.

Costs are 0.04% taker fee plus 0.02% modeled slippage **per side**, with a doubled-cost run. Actual historical funding rates and settlement intervals are applied only while a position is held; longs pay positive funding and shorts receive it. Marks use the historical mark-price hourly open at the nominal settlement, retaining the archive's recorded timestamp offset. This is an estimate of settlement notional. BTC/ETH/SOL fixed tick/lot assumptions, rather than an invented reconstruction of historical exchange filters, are disclosed in the JSON. Gross, no-funding, net, and stress runs are independent wallets, so their return differences include resulting position-size and halt-timing differences.

The history is 2022–2023 development and 2024–2025 retrospective comparison. These years were already known and are **not newly unseen holdout evidence**. Month tables include cash periods after a halt; all indicator arrows are retained after a halt and while a position is already open. Sharpe/Sortino use 365.25 days for crypto. The full grid, ledgers, equity, drawdown, cost/funding breakdown and candle replay are on the existing `/public/value-reversal` page.

## Data and independent verification

The pipeline verifies 441 [official Binance monthly archive](https://github.com/binance/binance-public-data) ZIP checksums: 49 months, three markets, trade klines / historical marks / funding. December 2021 warms the profile. Each market has 143,232 raw 15m candles. There are 23 actual-exchange API gap repairs, retained in the provenance manifest; missing data is never replaced with zero funding or fabricated candles. Funding uses the historical settlement schedule, including SOL interval changes.

The original Pine source was independently executed with PineTS 0.11.0 on identical BTC, ETH and SOL candles spanning **both 2024 DST changes**, for both timeframes and 5/10-bar inputs. All **152,064** level/signal checks matched. Asset tick and max-bar input overrides are the only source modifications. This is a selected source-parity sample, not a claim that every candle in the full backtest was independently checked. The fractional-volume comparison tolerance exposed by this test was corrected only in the new crypto adapter; QQQ's frozen translation stays unchanged.

## Why QQQ had 22 trades

The default QQQ 15m/previous/5-bar setting generated **25 arrows** in 2024–2025, of which **22** became trades. The untraded signals were:

| New York candle opening time | Reason |
|---|---|
| 2024-08-12 12:45 | A position was already open |
| 2025-07-30 15:45 | Closing candle; no next-open entry within the session |
| 2025-12-26 11:45 | A position was already open |

No entry in that setting was rejected for whole-share size. Counts were verified by replaying the original cached data and are bound to its data SHA-256 in `sources/qqq-arrow-audit.json`. They describe one configuration, not the total over every instrument and timeframe.

## Reproduce

From the PaperLab repository, using Python 3.12 and tzdata:

```powershell
.venv/Scripts/python.exe scripts/crypto_value_reversal.py
.venv/Scripts/python.exe -m pytest tests/test_crypto_value_reversal.py tests/test_crypto_value_reversal_api.py -q
```

For independent Pine validation, install the pinned test-only runtime (Node is not a production dependency), then run:

```powershell
npm install --prefix data/qqq-source-validation pinets@0.11.0
.venv/Scripts/python.exe scripts/verify_crypto_source_parity.py
.venv/Scripts/python.exe scripts/package_crypto_value_reversal.py
```

The downloader uses public endpoints and needs no API key. `--data` and `--output` are supported by the study runner; the parity/package scripts use the documented default paths. The deployment contains derived, read-only gzip research artifacts and selected replay sessions. It does not activate cryptocurrency orders or add historical fills to any forward book.
