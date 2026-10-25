# PREREG - agent 04, liquidation cascades and capitulation reversals

Written 2026-10-23 after DEV work (dev_grid.json: 160 variants; dev_robust.json), BEFORE any TEST run. Not edited afterwards.

## Data and universe (all attempts)
- Binance USD-M perps, 1h bars, from the public data.binance.vision monthly archive (includes delisted coins, so no
  survivorship bias in the universe). Funding from the same archive's fundingRate files; October 2026 (not yet archived)
  uses each coin's trailing-30-day mean funding.
- Universe: at each UTC month start, the 60 USDT perps with the highest quote volume in the prior 30 days (>= 80% of bars
  present), stablecoins and gold tokens excluded. Signals only on coins in that month's universe.

## Attempt A1 - BREADTH_LIMIT at 1x  (the core idea)
- k = 4 h. Crash bar i (closed) = at least 30% of the universe has a 4h log return <= ln(0.95) (down >= 5% in 4 h).
  Only the first crash bar of an episode (no crash bar in the previous 24 h).
- On that bar, rank the universe coins by 4h return; take the 10 worst.
- For each: buy limit at close_i x 0.98, live for bars i+1..i+6; filled only if a bar's LOW < the limit (trade-through),
  at the limit (or the open if it gaps below). Maker on entry (fee, no slippage).
- Exit: market at the open 24 bars after the fill bar (taker: fee + slippage). No stop, no target.
- Sizing: margin = 10% of current equity per position, isolated, max 10 open positions; notional = margin x leverage;
  leverage 1 (so at most 1x equity notional). Notional capped at 1% of the entry bar's quote volume.
- Costs 0.05% fee + 0.02% slippage per side, funding at real stamps, liquidation when the intrabar adverse excursion
  reaches 1/lev - 0.5% (margin lost).

## Attempt A2 - BREADTH_LIMIT at 2.5x  (the 20%/month attempt)
- Identical to A1 with leverage 2.5 per position (full deployment = 2.5x equity). 2.5x is the DEV leverage that gives a
  DEV mean month near 20% (19.6%) while maximising DEV geometric growth; 3x+ raised DEV drawdown to 87-100%.
- **This is the attempt the PASS/FAIL verdict is judged on.**

## Attempt A3 - FLUSH_LIMIT at 1x  (single-coin capitulation, secondary)
- k = 4 h. Coin signal on closed bar i: 4h log return z-score <= -3 (z = r4 / (14-day std of 1h log returns known
  4 h earlier x sqrt(4))), 4h quote volume >= 3x the prior 7-day median hourly volume x 4, taker-buy share of the 4h
  quote volume <= 0.45. 24 h cooldown per coin.
- Entry: buy limit at close_i x 0.99, live 3 bars, trade-through fill. Exit market at the open 24 bars after fill.
- Same sizing and costs as A1 (10% margin, 1x, max 10 positions).

## Reported for every attempt
TEST 2024-11-01..2026-10-21 at baseline and doubled costs; summary() monthly table; liquidations; largest trade's
share of profit. A descriptive TEST leverage sweep (1..5x) for BREADTH_LIMIT is reported as information only, not as an
attempt. PASS needs A2 TEST mean month >= +20%, mean > 0 at doubled costs, not wiped out, no trade > 50% of profit.

## What DEV said (honest prior)
A1 DEV: mean 7.5%/month, geo 6.7%, max DD 28%, 1337 trades; but 2021 alone was 18%/month and 2022-2023 only 1-2%/month.
A2 DEV: mean 19.6%, geo 13.4%, max DD 66%, 4 liquidations. 160 variants were tried on DEV, so selection bias is large.
