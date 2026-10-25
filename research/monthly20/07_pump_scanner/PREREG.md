# PREREG - agent 07, top-gainer / top-loser scanner (written before any TEST run, 2026-10-23)

Designed on DEV only (2021-01..2024-10). TEST = 2024-11-01..2026-10-21, run once, plus doubled costs.

## Universe (same for every attempt)
- Every Binance USDT perpetual with 1h data on data.binance.vision: the 524 trading today **plus every delisted
  USDT perp the portal still holds** (377 listed; survivorship fix; 878 symbols in total load with enough history).
- A coin is eligible at bar i only if its mean 1h quote volume over the previous 30 days (720 bars, bar i excluded)
  is >= $200,000 and it has >= 720 bars of history.
- Signals on the close of 1h bar i; fill at the open of bar i+1 (which must exist and be the next hour).

## Portfolio rules (same for every attempt)
- Start 1,000 USDT. At most k = 5 open positions, one per coin. Each new position gets margin = equity/5 (capped by
  free cash) and notional = margin x leverage.
- Capacity: notional <= 1% of min(signal bar quote volume, fill bar quote volume); larger orders are cut down.
- When more coins signal than free slots: the strongest signal first (largest r4 for MOM, largest r24 for SHORT_EXH,
  most negative r24 for BOUNCE).
- Costs 0.05% fee + 0.02% slippage per side on notional (x2 in the stress run). Funding from the portal's
  fundingRate files, charged on each funding stamp while open (October 2026 funding files are not published yet ->
  no funding charged in the last 3 weeks).
- Stops on bar extremes, worst case first (adverse extreme before the bar can raise a trailing stop or hit a TP); a
  gap through the stop fills at the open. Liquidation when the loss reaches 1/lev - 0.5%: whole margin lost.
- TPs only when price trades through the level.

## Attempts (exact parameters)
| # | name | side | entry signal at close of bar i | stop / exit | lev |
|---|------|------|-------------------------------|-------------|-----|
| A1 | LONG_MOM lev1 | long | 4h return (c[i]/c[i-4]-1) >= 15% AND c[i] > highest high of previous 720 bars | initial stop 15% below entry, trailing 15% below highest high since entry, max hold 72 bars | 1 |
| A2 | LONG_MOM lev3 | long | same as A1 | same as A1 | 3 |
| A3 | LONG_BOUNCE lev1 | long | 24h return (c[i]/c[i-24]-1) <= -20% | stop 20% below entry, exit at the close of the 24th bar | 1 |
| A4 | SHORT_EXH lev1 | short | 24h return >= 50% AND c[i] < 0.9 x highest high of the last 24 bars | stop 20% above entry, TP 20% below entry, max hold 72 bars | 1 |

Why these: A1, A3 and A4 are the best DEV geometric month of their families (DEV grid in dev_results.json, every
row reported). A2 is the leverage at which A1's DEV *arithmetic* mean month first reaches +20% (3x: mean +20.05%,
but geo -0.2%/month and max DD 98.7% on DEV) - it is the honest answer to "what leverage would 20%/month need".
Plain shorting of pumps (SHORT_FADE) lost ~-9%/month on DEV in every variant and is not carried forward.

## Pass rule (from PROTOCOL.md)
PASS only if the TEST mean month >= +20% at baseline costs, the mean stays > 0 at doubled costs, the run is not
wiped out, and no single trade makes more than half the profit. Expected verdict from DEV: FAIL.
