"""Strategy version 2: cost-aware, timeframe-configurable specialists.

v1 strategies are frozen -- stored experiments (9bbe42e53fd8, 527331f5d0c7, bdddc67f6315) must stay
reproducible, so nothing under app/strategies/sNN_*.py changes behaviour. A v2 class shares its v1
family's id (S04 v2 is a revised Donchian breakout) but is a different competitor: its bot key ends
in -v2 and its version fingerprint differs.

Design rules every v2 strategy follows (decided before any v2 result, docs/DATASET_SPLIT_V2.md):

* one signal timeframe per bot, chosen by the arena (5m / 15m / 30m) via `for_timeframe()`,
  with a higher-timeframe trend context;
* every entry states `expected_move_pct` (the move its first objective needs) so the cost gate can
  compare it with the round-trip cost, and a `signal_quality` in [0, 1] built from named, bounded
  factors -- never a constant;
* stops are ATR-based and wide enough that fees are a small fraction of R; a partial target at 2R
  and a trailing runner, so winners pay for the many small losers.
"""
