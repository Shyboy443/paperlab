# PREREG - agent 02, cross-sectional rotation (written 2026-10-23, before any TEST run)

All attempts below are run ONCE on TEST (2024-11-01 .. 2026-10-22) by `run.py`, at baseline costs and at doubled costs.
Nothing is edited after TEST is seen; any change would be a new, separately reported attempt.

## Common rules (all attempts)
- Venue/data: Binance USDT perps, daily bars from `common.klines(sym, "1d")`, funding from `common.funding()`
  (coins with no funding history: assume +0.01%/8h, longs pay).
- Universe: re-picked at EVERY daily bar with the `liquid_universe(n=40, at=day_open)` rule (top-40 by 30-day quote volume,
  listed >= 30 days, >= 80% bars present). Implemented vectorised in `engine.universe_mask`; checked identical to
  `liquid_universe` on 2023-05-01 and 2025-06-01.
- Signals from closed bars only (score at bar i uses closes up to i-1); fills at the open of bar i (00:00 UTC).
- Equal weight within each leg; gross exposure = lev x equity (split 50/50 between legs for long-short).
- Costs: 0.05% fee + 0.02% slippage per side on traded notional; doubled in the stress run. Funding charged per bar on notional.
- Liquidation: isolated margin per position (margin = notional / lev); a position is wiped (loses its whole margin) when its
  adverse move from the entry/rebalance open reaches 1/lev - 0.5%, checked on daily highs/lows. Equity <= 0 = wiped out.
- Liquidity cap: each position <= 1% of the coin's previous-day quote volume. Starting equity 10,000 USDT.
- BTC regime gate (where used): hold nothing on a day unless BTCUSDT's previous daily close > its 50-day SMA.

## Attempts
| name | score | legs | rebalance | gate | lev |
|---|---|---|---|---|---|
| P1_mom7_top3_btc50_2x | 7-day return | long top 3 | daily | BTC>SMA50 | 2x |
| P2_mom7_top3_btc50_1x | 7-day return | long top 3 | daily | BTC>SMA50 | 1x |
| P3_mom7_top1_btc50_1x | 7-day return | long top 1 | daily | BTC>SMA50 | 1x |
| P4_mom14_LS10_weekly_2x | 14-day return | long top 10 / short bottom 10 | every 7 days | none | 2x |
| P5_resid28_LS10_weekly_1x | 28-day return minus 60-day beta x BTC 28-day return | long top 10 / short bottom 10 | every 7 days | none | 1x |
| P6_mom7_top3_nogate_1x | 7-day return | long top 3 | daily | none | 1x |

P1 is the main candidate for the +20%/month target (DEV: mean +46.8%/mo, geo +20.6%/mo, max DD 66.5%, but 2021 alone
supplies most of it; 2024-Jan..Oct geo ~0%). P4/P5 are the robust beta-neutral-ish family (DEV geo ~2-2.5%/mo, DD 20-26%).
P6 is the ungated control.

Diagnostic (also pre-registered): leverage ladder 1, 1.5, 2, 3, 4, 5x on P2's rules, DEV and TEST, to show what leverage the
target would need and whether it blows up.

## PASS rule (protocol)
TEST mean month >= +20% at baseline costs, TEST mean > 0 at doubled costs, not wiped out, and no single trade > half of the profit.

## Not pre-registered (DEV evidence only)
- 4h bars (lookbacks 4h..3d, rebalance 4h or daily; partial run, log dev_4h_partialdata.log): long-short and 4h-lookback
  versions lose on DEV after costs. The one big DEV number, 12h-return top-1 rebalanced every 4h (DEV mean +77%/mo, geo
  +17.9%/mo), is 2021 only (2021 geo +92.5%/mo, 2022 -3.8, 2023 +3.4, 2024 -0.7) - the same profile as P3, so it is NOT
  taken to TEST. Caveat: built from agent 04's 1h vision cache, which still lacked 156 of the 373 universe coins
  (XRP, LTC, NEAR, OP ...), so these 4h numbers are incomplete.
- Reversal at every horizon 1-28 days: negative on DEV in every year.
