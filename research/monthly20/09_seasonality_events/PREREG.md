# PREREG — agent 09, time and event effects (written 2026-10-23, before any TEST run)

All design work used DEV only (2021-01-01..2024-10-31): `explore.py` -> `explore_dev.txt`,
`dev_scan.py` -> `dev_scan.txt` (36 variants), `lev_scan.py` -> `lev_scan.txt`. TEST (2024-11-01..2026-10-21) has not been run.

## What DEV showed (summary)
- Hour-of-day, day-of-week, weekend, turn-of-month, funding-stamp hours, US open/close and options-expiry effects are
  1-6 bp per hour and change sign between DEV-A (2021-22) and DEV-B (2023-24). Best candidate (long 21-22 UTC daily)
  loses ~2%/month after costs (14 bp round trip). Dropped.
- Three event effects had the same sign in both DEV halves and survive costs at 1x:
  1. CME weekend gap MOMENTUM (fading the gap loses): BTC price at the Sunday 18:00 New York CME re-open vs the Friday
     17:00 NY CME close; |gap| > 1% -> trade in the gap's direction for 24 h.
  2. FOMC: long the basket from 4 h before the 14:00 NY statement bar to the end of that bar.
  3. CPI: long the basket from 24 h before the 08:30 NY release bar, exit at the release bar's open.

## Universe (fixed list, decided before TEST)
12 USDT perps that were large and listed before 2021: BTC ETH BNB SOL XRP DOGE ADA LINK AVAX LTC DOT TRX.
Survivorship bias: all 12 still trade today; the list is today's knowledge of which 2020 majors survived.

## Event calendars (point-in-time, scheduled in advance)
`events.py`: FOMC statement days from federalreserve.gov (scheduled meetings only), CPI release days from bls.gov.

## Execution, costs, risk (common.py rules)
Hourly bars, fills at bar open, 0.05% fee + 0.02% slippage per side, funding at 00/08/16 UTC, isolated margin per
position, liquidation when the adverse move from entry reaches 1/L - 0.5% on bar highs/lows (whole margin lost; no
re-entry until the next scheduled signal), entry notional capped at 1% of the bar's quote volume. Compounding.
Basket legs are split equally across the 12 coins; when legs overlap the gross is scaled down to 1 (`stack()`).

## Attempts (all run once on TEST, baseline and doubled costs)
| name | rule | leverage |
|---|---|---|
| STACK_1X | gap(BTC only, thr 1%, hold 24h) + FOMC(basket, pre 4h, post 1 bar) + CPI(basket, pre 24h, exit at release open) | 1 |
| STACK_3X | same | 3 (highest DEV leverage with max DD < 90%) |
| FOMC_1X | FOMC leg alone | 1 |
| FOMC_12X | FOMC leg alone | 12 (highest DEV mean before liquidations dominate; DEV mean 11.3%/month) |
| GAP_1X | gap leg alone | 1 |

DEV expectation: no variant reaches +20%/month on DEV at any leverage (stack peaks at 7.3%/month at 5x with a 98% DD,
and is wiped at 8x; FOMC peaks at 13.4% at 20x with 29 liquidations). The prior verdict is therefore FAIL.
PASS rule = PROTOCOL.md. After TEST, a leverage sweep is run as a labelled post-TEST diagnostic (not an attempt).
