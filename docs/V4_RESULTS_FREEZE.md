# V4 results freeze

A finished V4 run is evidence. `scripts/run_v4_arena.py` refuses every phase except `status` for a run listed here,
so a later analyzer or model change can never rewrite its stored answers. A change to V4 is V5.

| run | status | result |
|---|---|---|
| `v4-7bdf031bfc` | frozen | DEVELOPMENT 2025-11-01 → 2026-04-30 · 140 controls, 12 Jev pairs · no bot passed every gate · RAW edge negative on every timeframe (3m -0.079 R, 5m -0.085 R, 15m -0.070 R, 30m -0.037 R per setup before costs) · expected-edge gate uncalibrated (passed -0.210 R vs refused -0.187 R) · Jev V4 AUC 0.477 [0.404, 0.551], selection alpha -1.16 USDT (4/12 pairs), ATTACK added -1.63 USDT · capacity BAD 12/12 |
| `v4-0ef34a759d` | frozen | TEST (pre-registered holdout 2025-04-01 → 2025-09-30) · **ADVANCED SET = NONE** · RAW edge negative on every timeframe (3m -0.077 R, 5m -0.090 R, 15m -0.100 R, 30m -0.071 R) and every family (V4.4 best at -0.037 R) · edge gate passed -0.207 R vs refused -0.198 R · Jev V4 AUC 0.542 [0.414, 0.670], selection alpha -0.85 USDT (2/5 pairs, 0 above the random 90th percentile), ATTACK added -0.47 USDT · capacity BAD 5/5 |

The two least-bad DEVELOPMENT cells did not replicate: V4.4 MOMENTUM_CONTINUATION 30m +0.034 R (t 0.85) → -0.018 R,
V4.7 FLOW_BREAKOUT 30m +0.030 R (t 0.47) → the V4.7 family -0.137 R on the holdout. Nothing is advanced; nothing goes
to the forward shadow. The next step is a V5 hypothesis judged on data no V1-V4 design has seen, never a V4 patch.
