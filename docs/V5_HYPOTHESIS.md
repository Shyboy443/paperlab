# V5 hypothesis (written after the V4 TEST failed; V4 is not patched)

## What V1-V4 established

Four programs, 987 evaluated bots, two pre-registered holdouts: **no intraday price-pattern entry on 1m-30m alt
perpetuals has a raw edge.** Gross expectancy before any cost was negative for every program, for 27 of 30 V3 cells,
for 10 of 12 V3.1 cells and for every V4 timeframe and family in both windows (V4 TEST: -0.04 to -0.15 R per setup,
t down to -7.7). The costs (~0.10-0.19 R per round trip at a structural stop) only set the speed of the loss. Exits
were not the cause (tested against random entries), account size was not the cause (the same bots lose at 50 / 100
USDT), and Jev never discriminated winners from losers (every AUC interval contains 0.5).

## Hypothesis

If PaperLab can find an edge at all, it is **not in the shape of the last few 15m bars**. Two directions remain
untested and are the only ones worth a V5:

1. **Slower horizons, where costs are small against the move.** Continuation over 4h-3d (trend / time-series
   momentum) with stops of 3-8%, so the round trip is <= 3-5% of R instead of 10-19%. V3.1's only replicated signal
   (S33.1 15m pooled over coins, DEV +2.38 / TEST +3.70 USDT) and the V3.1 exit study (drift +80 bps at 12h vs +3 bps
   at 15m) both point to longer holds. Sizing must be checked, not assumed: at 1% risk (0.20 USDT) a 5% stop is a
   4 USDT order, below Bybit's 5 USDT minimum, so a 20 USDT book needs 2% risk (8 USDT) or stops <= 4%.
2. **Information the price tape does not carry.** Funding-rate extremes, open-interest build-ups and liquidation
   cascades (positioning), judged by the same RAW-evidence machinery: a signal family only qualifies if its RAW
   gross edge is positive before any gate.

## Rules carried over (non-negotiable)

Design on V4's DEVELOPMENT window only (2025-10..2026-04); pre-register a holdout no V1-V4 design touched —
2024-09..2025-02, or the forward shadow from 2026-10 on — before looking at it; raw-edge gate (gross > 0) first;
fees, spread, slippage, funding and exchange minimums as in V4; coins by the tradeability rule, never by PnL; Jev only
after a raw edge exists and only judged against the matched random action. If V5 fails its holdout, report
ADVANCED SET = NONE again.
