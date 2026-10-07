# V4 freeze

V4 is frozen after one DEVELOPMENT pass and its analysis. `scripts/run_v4_arena.py --phase fingerprints` must
print exactly these values; the TEST pre-registration (`docs/V4_TEST_PREREGISTRATION.json`) records them and the
TEST run refuses to start if any source differs.

| source | fingerprint |
|---|---|
| strategy: V3 base (inherited) | `33d10d257921` |
| strategy: V4 base | `fa7ce5a960e3` |
| V4.1 TREND_PULLBACK | `9699fc9a1487` |
| V4.2 SQUEEZE_EXPANSION | `a547c5820bf2` |
| V4.3 BREAKOUT_RETEST | `e8cbdc999c4d` |
| V4.4 MOMENTUM_CONTINUATION | `f20838069379` |
| V4.5 FAILED_BREAKOUT_REVERSAL | `7588ef4d6105` |
| V4.6 TREND_RANGE_REJECTION | `b1e48f9c0b7d` |
| V4.7 FLOW_BREAKOUT | `1dbbfcdddc1a` |
| Jev V4 prompt (JEV_PROMPT_V4) | `d61b2171b8d620d3` |
| Jev V4 policy (JEV_POLICY_V4) | `aae92ebe2c8bf0e6` |
| expected-edge model config | `b3ad781537bb` |
| expected-edge model source (`v4_edge.py`) | `efd551b45614` |
| tradeability rule | `983b0eee3243` |

## The DEVELOPMENT evidence of the frozen version

Run `v4-7bdf031bfc` (2025-11-01..2026-04-30, warm-up 2025-10) was replayed with exactly these sources (its stored
configuration carries the same fingerprints), so it is the DEVELOPMENT run of frozen V4; its Jev / always-take /
random / capacity phases run on it after this freeze.

## Why nothing was changed after the DEVELOPMENT analysis

The protocol allows one round of DEVELOPMENT changes. The analyzer gave nothing that evidence supports changing:

* **No raw edge anywhere.** 21,089 sequenced RAW setups (every legal setup at TAKE size, ungated): gross expectancy
  per family -0.015 R (V4.4) to -0.132 R (V4.6); per timeframe 3m -0.079, 5m -0.085, 15m -0.069, 30m -0.037 R;
  the best family x timeframe cells (V4.4 30m +0.034 R, V4.7 30m +0.030 R) have t < 1 — the round trip costs
  0.10-0.19 R. Keeping only the least-bad cells would be fitting noise across 28 cells.
* **The exits are not the problem.** On the executed trades, winners' price moved -0.10 to -0.55 R in the 4 hours
  after the exit (nothing left on the table); stopped trades did not reach +2R more often than random entries with
  the same stop except in samples of 10-106 trades.
* **The expected-edge gate is uncalibrated** (passed -0.210 R realised vs refused -0.187 R) because there is no
  persistent family edge to find; it still did its job of refusing ~98% of setups of families that were losing.

## Amendment 1 (activity only, recorded before any Jev decision existed)

The Jev field floor is lowered from 30 to 10 executed entries per bot (`FIELD_MIN_ENTRIES`, `v4_arena.py`). With
the edge gate refusing ~98% of setups only 1 pair reached 30 entries, which cannot test Jev at all. The field is
still chosen by activity alone (top 2 coins per family x timeframe by executed entries, ties alphabetical), never by
PnL; no strategy, gate, threshold or exit changed.
