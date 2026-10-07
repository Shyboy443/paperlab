# Stock bot performance audit

Generated 2026-10-07T00:57:59.868103+05:30. All displayed timestamps use Asia/Colombo.

The three stock bots have short recent winning records, but each lost in the continuous historical replay from 2024-10-01 to 2026-10-01. Fresh replays exactly matched every saved trade and ending balance.

| Bot | Highest marked equity | Peak date | Highest settled balance | Ending balance | Return | Trades | Profit factor | Stop date |
|---|---:|---|---:|---:|---:|---:|---:|---|
| Halo / TSLA | $1,132.59 | 2025-02-28 22:35:59 | $1,131.45 | $790.65 | -20.93% | 444 | 0.874 | 2025-08-15 23:03:00 |
| Iris / AAPL | $1,015.71 | 2024-10-02 20:19:59 | $1,013.96 | $708.17 | -29.18% | 283 | 0.608 | 2025-01-08 00:11:19 |
| Oscar / AMZN | $1,028.79 | 2024-10-10 23:01:59 | $1,024.25 | $716.87 | -28.31% | 369 | 0.726 | 2025-05-27 23:52:19 |

Peak marked equity includes open-position gains. The continuous book stopped taking trades after its peak-drawdown risk limit; the remaining archive does not create later trades.

**When the decline started:** Halo reached its high in February 2025, then lost $64.22 in March and $106.23 in June, before stopping in August. Iris peaked on October 2, 2024 and lost $203.21 that October. Oscar peaked on October 10, 2024; every trading month from October through May finished negative.

**What the trades show:** Long and short trades both lost overall in each bot. Stop exits averaged approximately -1.17R, so the realized loss regularly exceeded intended risk. These observations support an entry/exit and execution problem; they do not isolate one proven causal fix.

Across the existing replay, all 30 CONTROL stock bots ended below $1,000; 26 triggered the peak risk stop. Selecting only the current leaders hides this wider result.

## Recent forward paper history

Forward experiment began 2026-10-05 10:03:00. Snapshot near 2026-10-07 00:57:52.

| Bot | Marked equity now | Recorded marked peak | Settled balance | Closed trades / wins |
|---|---:|---:|---:|---|
| Halo | $1,022.20 | $1,034.63 | $1,019.70 | 8 / 4 |
| Iris | $1,021.79 | $1,026.47 | $1,022.44 | 4 / 4 |
| Oscar | $1,023.09 | $1,026.14 | $1,021.56 | 9 / 7 |

Iris has no closed losing forward trades yet; its displayed infinite profit factor results from that tiny sample, not evidence of unlimited or reliable performance.

## Recent losses

### Halo

- 2026-10-06 20:26:00: long, time exit, $-1.73.
- 2026-10-06 21:46:00: long, time exit, $-2.28.
- 2026-10-06 22:51:00: long, time exit, $-1.13.
- 2026-10-06 23:56:00: long, time exit, $-6.49.

Historical monthly net: 2024-10: $+9.55; 2024-11: $-34.10; 2024-12: $+86.00; 2025-01: $+25.89; 2025-02: $+32.08; 2025-03: $-64.22; 2025-04: $-21.33; 2025-05: $-0.92; 2025-06: $-106.23; 2025-07: $-60.35; 2025-08: $-75.73

### Iris

No closed losing trades in this snapshot.


Historical monthly net: 2024-10: $-203.21; 2024-11: $-26.51; 2024-12: $-40.44; 2025-01: $-21.67

### Oscar

- 2026-10-05 21:47:00: long, stop exit, $-8.42.
- 2026-10-06 22:53:19: long, stop exit, $-7.99.

Historical monthly net: 2024-10: $-41.49; 2024-11: $-72.39; 2024-12: $-13.71; 2025-01: $-33.95; 2025-02: $-31.82; 2025-03: $-11.64; 2025-04: $-20.96; 2025-05: $-57.17

## Existing replay with the peak risk stop removed

This separate saved experiment keeps each bot trading to the end of September 2026. Removing the stop made all three ending balances worse; it does not repair the strategy.

| Bot | Ending balance without peak stop | Return | Closed trades |
|---|---:|---:|---:|
| Halo | $526.94 | -47.31% | 1056 |
| Iris | $452.12 | -54.79% | 2065 |
| Oscar | $614.85 | -38.51% | 1178 |
## Limits and definitions

- Simulated USD books, not broker account balances.
- Historical marked peak includes unrealized PnL and is sampled at each processed 1m close; not tick-level.
- Settled peak is reconstructed after completed trades, including their modeled fees and slippage.
- The replay preserves the 30% peak-drawdown sizing stop; daily and strategy engine halts are disabled. The forward arena has additional eligibility gates.
- Alpaca IEX regular-session archive; missing session minutes are filled flat by the existing session tape.
- Current instrument rules and strategy settings; parts of this historical period informed development. This is not an independent unseen validation.
- The daily-reset variant study in V9_STOCK_STUDY.json is a different experiment and is not mixed with these continuous wallet balances.
- Forward prices continue changing; recent amounts are a saved snapshot.

Alpaca documents the feed distinction: [Historical Stock Data](https://docs.alpaca.markets/us/v1.1/docs/historical-stock-data-1). IEX is not the consolidated SIP feed.

No trading configuration, live orders, or Railway book data were changed by this audit.