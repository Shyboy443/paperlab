# ZEC perpetual MACD, 2026 by month: funding data and an independent reproduction

Data: Binance USDⓈ-M public API, fetched 2026-10-11 23:07:41 UTC (`scripts/macd/zec_perp_2026_fetch.py`).
- **4h candles:** from 2025-09-01, for indicator warm-up.
- **1h candles:** for hourly equity marks.
- **Funding:** every settlement with Binance's recorded **settlement mark price**.

Only completed candles are used.

## Files

| File | Contents |
|---|---|
| `ZECUSDT_funding_2026.csv` | All 852 ZECUSDT funding settlements, 2026-01-01 00:00 → 2026-10-11 16:00 UTC: time (UTC and ms), rate, settlement mark price. Every interval is 8 h; the ms offsets are Binance's own stamps. October so far: 33 settlements. sha256 `3e47efed09278a8dfd88627c4b27a2ac9238eb9d347fdb82d39ee83be2397cb1`. |
| `funding_paid_while_long_2026.csv` | The settlements the account was exposed to (Jan → Oct 11): units, notional at mark, payment. |
| `trades_jan_sep_2026.csv` | The 54 trades, January–September. |

## Method (`scripts/macd/zec_perp_2026_monthly.py`)

- **Rule:** MACD 12/26/9 on 4h closes, with Pine-style SMA-seeded EMAs from 2025-09-01.
- **Positions:** long or cash, 1x. One account starts flat with $10 at 2026-01-01 00:00 UTC; earlier signals are
  ignored.
- **Fills:** at the next 4h open, with commission 0.05% and adverse slippage 0.02% per side.
  - Buy: `units = cash / (open·1.0002·1.0005)`.
  - Sell: `cash = units·open·0.9998·0.9995 + funding`.
- **Funding:** paid or received on `units × settlement mark × rate` at each settlement while long. A fill at T is
  exposed to the settlement stamped at T (settlements land a few ms after T); an exit at T is not.
- **Equity and drawdown:** equity is `units × hourly close + accumulated funding`. Monthly drawdown is measured on
  hourly closes within the month, from the month's opening equity.

## Comparison with the operator's table

| Month | Ours | Operator | Ours: max DD | Operator: max DD | Ours: balance | Operator: balance |
|---|---|---|---|---|---|---|
| January | −5.40% | −5.39% | 13.34% | 13.31% | $9.46 | $9.46 |
| February | −19.73% | −19.72% | 29.02% | 29.04% | $7.59 | $7.59 |
| March | +30.59% | +30.62% | 13.42% | 13.47% | $9.92 | $9.92 |
| April | +46.98% | +46.99% | 12.32% | 12.35% | $14.58 | $14.58 |
| May | +84.24% | +84.26% | 16.11% | 16.19% | $26.85 | $26.87 |
| June | +13.34% | +13.37% | 18.15% | 18.09% | $30.44 | $30.46 |
| July | +5.35% | +5.34% | 11.91% | 11.93% | $32.07 | $32.09 |
| August | +54.10% | +54.14% | 10.62% | 10.61% | $49.41 | $49.47 |
| September | +22.57% | +22.57% | 20.92% | 20.90% | $60.56 | $60.63 |
| **Jan–Sep** | **+505.64%** | +506.31% | **36.00%** | 36.00% | | |

Both runs have 54 trades and no position open at the end of September. Monthly gaps are at most 0.08 points. They are
consistent with the funding price: we use Binance's recorded settlement mark price, the operator used minute-open
mark-price proxies. The settlement-boundary convention and the EMA warm-up start may also differ slightly. Net
funding over January–September: +$0.24 on the $10 account.

## October (month to date, incomplete)

The account carries on from September without a forced sale. It was marked at the last completed hour,
2026-10-11 23:00 UTC, with a position open:

| | Value |
|---|---|
| October to date | −10.08% |
| Monthly max DD | 13.08% |
| Balance | $54.46 (September close: $60.56) |
| January–October to date | +444.60% |
| Whole-period max DD | 36.00% |

October ends 2026-10-31 24:00 UTC; rerun both scripts after that for the full month.

## Limits

- **One coin, chosen with hindsight.** The operator's own 84-coin run of this rule returned +11.5%.
- **Small account:** minimum order sizes and step sizes are not modelled, so the $10 balances are illustrations.
- **Drawdown basis:** hourly closes only, so intrabar extremes are not counted.
- **Liquidation:** not modelled here. At 1x, fees and funding still reduce collateral; the strict mark-price check in
  `docs/MACD_ZEC_AUDIT.md` covers the Donchian desk, not this account.
- **Paper research only.**
