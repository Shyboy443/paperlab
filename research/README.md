# Research loop — strict-stop hunt for +0.5%/day net

This workspace is a **loop driver** that runs research rounds until a strict
stop rule is met, a fatal integrity issue is caught, or the user stops it.

It is **not** a port of the prior round-1..8 research that produced the
historical results in `RESEARCH_HISTORY.md`. Those results came from a
different workspace and are not re-derived here. The numbers this loop
produces start at **round 9** and are reproducible end-to-end inside this
repo (`python -m research.runner --round 9 --mode walkforward`).

## Stop rule (must hit ALL of these to declare success)

Walk-forward, chained windows, **net of all modeled costs**:

  - avg_daily >= +0.25%
  - sharpe    >= 0.5
  - floor_intact  (the per-window minimum equity never crosses the
                   configured floor — default 45% of initial)
  - n_trades >= 30  (we will not declare a hit on a 5-trade noise result)

If any condition fails, the round verdict is **NO HIT — keep going** and
the runner picks the next round per the input-class rotation below.

## Integrity tripwires (any one stops the loop with NO-CONFIDENCE)

- Lookahead detected (signal references data after the bar close it
  claims to act on)
- Data snooping (parameter picked on test data, or on the full sample
  before splitting)
- Multiple-comparison: declaring a hit on a small-N best-of-K selection
  without an explicit correction
- Overlapping-window statistic (e.g. "33 prints" that are 3 distinct
  clusters inflated to 33 by autocorrelation)
- Cost-free fantasy: any reported "gross" number that does not match
  the same trade's "net" via the documented cost schedule

The runner is paranoid about these because every prior round in
`RESEARCH_HISTORY.md` ended in "not achievable" — the loop's job is
to be honest about that, not to find a way to call a loss a win.

## Round 9 — result (2026-10-08, in-repo)

Cross-venue perpetual basis (OKX vs Binance mark, 1m, BTC-USDT-SWAP).

- **Data:** OKX public `history-mark-price-candles` returns a 24h rolling
  window regardless of pagination cursor (verified via 50-page probe at
  `research/scripts/probe_okx_depth.py`). Binance `markPriceKlines` returns
  the 1000 most recent bars. Aligned window: ~17h.
- **Structural finding:** basis p50 = 0.66 bps, p90 = 1.50 bps, p99 = 3.04 bps,
  max = 5.05 bps. Round-trip cost (2 legs, std_taker_alt) = 80 bps. The
  signal never fires (entry threshold 25 bps). Same finding as prior
  round 4.2 (cross-venue spot basis) — basis << cost, structurally
  infeasible at retail.
- **Walk-forward:** not evaluable (data too short to form even one 455-bar
  chained test window). 0 trades, 0% return, 0% drawdown. Honest verdict:
  **INSUFFICIENT DATA + STRUCTURAL FAILURE**, NOT a hit.
- **Why the loop pauses:** the same data-availability wall your prior
  workspace hit (Coinbase ~300d, OKX funding ~100d, OKX mark ~24h,
  Bybit 1000 bars). Continuing to run the loop here without authenticated
  bulk history will only produce more "INSUFFICIENT DATA" verdicts. The
  loop's job is to be honest, so it pauses and asks.
- Full structured result: `research/logs/round_09_summary.json`.
- Loop synthesis: `research/logs/synthesis.json`.

## Input-class rotation (loop policy)

The cost-breakeven analysis in the prior research proved the same thing
the rounds did: re-tuning parameters on the same data is a dead end —
two families had negative gross edge, meaning no fee tier fixes them.
Each new round therefore must change the **input class**, not the
parameters. The rotation in this scaffold, in order, is:

  - 9  : cross-venue perpetual basis (OKX vs Binance vs Bybit perps)
  - 10 : 1-minute crypto microstructure (maker fill probability, queue
         position, adverse selection) — needs fresh data
  - 11 : options skew / IV term-structure mispricing (Deribit public)
  - 12 : cross-asset lead-lag (BTC lead → alts lag, or vice versa)
         using tick-level alt data
  - 13 : stablecoin depeg / basis (USDT vs USDC vs DAI)
  - 14 : prediction-market mispricing (Polymarket / Kalshi, if reachable
         from this network)
  - 15 : equity intraday vol surface (SPY/QQQ options, public OPRA/OPRA-
         delayed)
  - 16+: further input classes discovered during the loop, not parameters

Skipping a round requires an explicit "round N: skipped, reason …"
entry in the log.

## Running

    python -m research.runner --round 9 --mode walkforward
    python -m research.runner --round 9 --mode split
    python -m research.runner --round 9 --mode both

A per-round log is written to `research/logs/round_NN_*.log`. A summary
JSON is written to `research/logs/round_NN_summary.json`.

## Honesty

If a round hits, the loop stops and reports. If 5 consecutive rounds
all return NO HIT with the same structural finding (e.g. "fee tier
insufficient at this venue class"), the loop emits a SYNTHESIS log
summarizing why the input class is exhausted and proposes a NEW
direction rather than continuing to grind.
