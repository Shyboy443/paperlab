# v2 freeze (2026-09-23, before the TEST arena)

Rule 2 of [DATASET_SPLIT_V2.md](DATASET_SPLIT_V2.md): every v2 strategy is frozen before TEST. These
are the source fingerprints (sha256 of each module, first 12 hex, CRLF normalised) that the TEST
arena records in its configuration (`strategy_fingerprints`). A later run whose fingerprints differ
is not a v2 run.

| module | fingerprint |
|---|---|
| app/strategies/v2/base.py | `bef9ced73cec` |
| S03 v2 Squeeze Expansion | `05d0a8ee4379` |
| S04 v2 Trend Breakout | `aae1fb697d67` |
| S12 v2 Trend Pullback | `b6c81f3ee712` |
| S22 v2 Range Reversion | `7df4a8eedb68` |
| S26 v2 Trend Rider | `7da4115014b8` |

`python -c "from app.strategies.registry import v2_fingerprints; print(v2_fingerprints())"` recomputes them.

## What DEVELOPMENT showed (run `78b995a21d07`, 2025-11 → 2026-04, all 135 v2 bots)

Design data, not performance. 135 bots, 9,482 trades: gross −105.63, fees −129.98, slippage −33.36,
funding −1.28, **net −270.18 USDT**. 14 bots were net positive; 6 passed the discovery gates.

| timeframe | bots | net positive | gross edge summed | costs summed |
|---|---|---|---|---|
| 5m | 45 | 0 | −72.70 | 72.61 |
| 15m | 45 | 1 | −28.99 | 60.82 |
| 30m | 45 | 13 | −3.95 | 31.48 |

Cost classes: GROSS_NEGATIVE 104, FEE_DESTROYED 17, MARGINAL 7, HEALTHY 7.

The pattern is structural and matches the v2 hypothesis: the shorter the signal timeframe, the
smaller the move per trade relative to a fixed round-trip cost, and on 5m the trading logic itself
loses before any cost is charged.

## Changes made after DEVELOPMENT

**No strategy code or parameter was changed after DEVELOPMENT.** The fingerprints above are the
code that produced the DEVELOPMENT numbers.

The one decision taken from DEVELOPMENT is which bots enter TEST, by a rule fixed here before TEST
ran:

> **TEST field = every v2 bot with a positive gross edge (before fees, slippage and funding) on
> DEVELOPMENT.** A bot whose logic loses before costs has no edge for costs to be judged against.

That is 31 bots (listed in [v2_test_field.txt](v2_test_field.txt)): 17 on 30m, 11 on 15m, 3 on 5m,
all nine coins. The rule does not look at net profit or at the discovery gates, so FEE_DESTROYED
bots stay in and TEST can show whether their gross edge was real.

## TEST protocol

* Window 2026-05-01 → 2026-06-30, evaluated **once**.
* Same arena gates as every discovery run (no lowering): trades ≥ 40 / 20 / 12 (5m / 15m / 30m),
  net > 0, expectancy R > 0, PF ≥ 1.10, max DD ≤ 35%, no liquidation.
* Binance USD-M fee schedule (maker 0.02%, taker 0.05%), realistic execution, 20 USDT books,
  AGGRESSIVE profile, 20x ceiling with needed-leverage sizing, cost gate edge-to-cost ≥ 2.0,
  ATTACK v2 (signal quality ≥ 0.70 and edge-to-cost ≥ 3.0 and healthy book).
* After TEST, v2 is never tuned. Any change is v3 and needs FORWARD (live shadow) data.
* JEV-eligible controls are judged on TEST, not DEVELOPMENT.
