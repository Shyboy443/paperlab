# V5 dataset map — what every earlier program consumed, and what V5 may call untouched

Read from the run tables of the production database (read-only, 2026-09-24).

| program | run(s) | window(s) | coins | venue / data |
|---|---|---|---|---|
| V1 seasons | `776805fe47ed`, `4d6862b73874` | 2026-08 | BTC, ETH, SOL | Binance USD-M 1m |
| V1 multi-year validation | `a7965533de2a`, `bdddc67f6315` | 2024-01..2024-10; **2021-01..2026-08** (61 walk-forward windows) | BTC, ETH, SOL | Binance USD-M 1m |
| V1 discovery arena / V1 Jev | `9bbe42e53fd8`, `527331f5d0c7` | 2026-07..2026-08 | V1 / V2 coins | Binance USD-M 1m |
| V2 DEVELOPMENT / TEST arenas | `78b995a21d07`, `8227dfb422cb` | 2025-11..2026-04; 2026-05..2026-06 | ADA AVAX BNB DOGE ETH LINK LTC SOL XRP | Binance USD-M 1m |
| V2 multi-year candidates | `9688867c33bc` | **2021-01..2025-10** | the 3 V2 TEST survivors' coins | Binance USD-M 1m |
| V3 DEVELOPMENT | `v3-e39e94890b` | 2025-10..2026-04 | ZEC UNI HYPE SUI ARB TAO PENGU ENA AAVE ZRO | Binance 1m tape, Bybit costs |
| V3.1 DEVELOPMENT / TEST | `v31-46d2e9af7d`, `v31-04f1291069` | 2025-10..2026-04; 2026-04..2026-08 | same as V3 | Binance 1m tape, Bybit costs |
| V4 DEVELOPMENT / TEST | `v4-7bdf031bfc`, `v4-0ef34a759d` | 2025-10..2026-04; 2025-03..2025-09 | HYPE ENA FARTCOIN TAO APT DOGE XPL; ALCH ARC DOGE ENA FARTCOIN PNUT POPCAT | Binance 1m tape, Bybit costs |
| Forward shadow (V2 bots) | live session | 2026-09-23 → today | V2 field | live data, paper fills |

## What this means

* **No historical window is untouched.** 2021-2026 prices were replayed by V1 (BTC/ETH/SOL) or V2 (large caps), and
  2025-03..2026-08 by V3, V3.1 and V4 (alts).
* **What IS untouched:** no PaperLab program ever loaded Bybit funding, open interest, basis or account-ratio data, and
  no program ever tested an hourly / daily positioning hypothesis. The only truly untouched data is the **future**.

## V5 windows (declared honestly)

| role | trade window | warm-up | status |
|---|---|---|---|
| DEVELOPMENT | 2025-03-01 → 2026-08-31 (18 months) | 2024-12-01 → 2025-02-28 | prices seen by V3-V4 (fine for development) |
| **PSEUDO-HOLDOUT** | 2023-09-01 → 2025-02-28 (18 months) | 2023-06-01 → 2023-08-31 | **not untouched**: its prices were replayed by V1 / V2 multi-year validation of unrelated 1m-30m chart strategies on BTC/ETH/SOL and V2's large caps. Never used by V3-V5, never with positioning data, never looked at for V5 design. Pre-registered and downloaded only after the V5 freeze. |
| **FORWARD VALIDATION** | from the V5 freeze onward, live Bybit data | — | the only untouched test; required before any V5 bot can be QUALIFIED |

A V5 bot can be ADVANCED on DEVELOPMENT + PSEUDO-HOLDOUT; it cannot be QUALIFIED without forward validation.
