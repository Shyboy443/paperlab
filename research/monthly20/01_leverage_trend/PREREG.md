# PREREG - agent 01, leveraged volatility-targeted trend following

Written 2026-10-23 after the DEV sweeps (2021-01..2024-10) and BEFORE any TEST run. Not to be edited after TEST.

## Common rules (engine.py, unchanged for TEST)
- Binance USDT perps, signals on closed bars, fill at the next bar's open.
- Every position = isolated-margin lot. Margin per lot = equity / slots (capped by the free wallet balance).
  Notional = margin x min(50, tv / realized_vol), realized_vol = annualized stdev of log returns
  (30 bars on 1d, 180 bars on 4h). No resizing while a trade is open.
- Liquidation: lot wiped (whole margin lost) when the adverse move from entry reaches 1/lev - 0.5%, checked on
  every bar's high/low. Funding paid/received at each stamp. Costs 0.05% fee + 0.02% slippage per side,
  stress run at 2x. New lot notional <= 1% of the previous bar's quote volume.
- Long and short. ADX(14) Wilder >= 25 filter: flat while ADX < 25.
- Start equity 10,000 USDT on 2024-11-01; TEST = 2024-11-01..2026-10-21.

## Attempts (all reported; A is the primary because it is the one that targets the user's 20%/month)
| name | universe | bars | signal | filter | tv | DEV mean / median / geo month, max DD |
|---|---|---|---|---|---|---|
| A_target20 | BIG10 fixed* | 4h | Donchian 120 breakout, exit on 60-bar opposite channel | adx25 | 4.0 | 20.35 / -3.57 / 5.36 %, 97.2% |
| B_growth_optimal | BIG10 fixed* | 4h | same | adx25 | 3.0 | 16.07 / ? / 7.29 %, 87.3% |
| C_moderate | BIG10 fixed* | 4h | same | adx25 | 1.0 | ~5.5 / ? / 4.31 %, 46% |
| D_ema_reference | BIG10 fixed* | 1d | EMA5 > EMA100 long, else short | adx25 | 3.0 | 15.55 / ? / 6.93 %, 86.3% |
| E_broad_top50 | top-50 by 30d quote volume, re-picked monthly (point in time), slots 50 | 1d | 60-day momentum sign | none | 2.0 | ~8 / ? / ~4.6 %, ~73% (DEV figure without funding; rerun with funding) |

*BIG10 = BTC ETH SOL XRP BNB DOGE ADA LINK AVAX LTC (the earlier trend_strength study's list; chosen with
today's knowledge, a mild survivorship bias).

`leverage_needed_for_20pct` = tv 4.0 on A, i.e. ~5-6x per open position, avg gross 1.9x, peaks ~10x.

## Pass rule (from PROTOCOL.md)
PASS only if A's TEST mean month >= +20% at baseline costs, the mean stays > 0 at doubled costs, no wipe-out, and no
single trade makes more than half the profit. Otherwise FAIL. B..E are reported but cannot rescue a FAIL of A unless
they meet the same rule (they are pre-registered here, so they count as honest attempts too).
