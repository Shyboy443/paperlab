# V3.1 results freeze

A finished V3.1 run is evidence. `scripts/run_v31_arena.py` refuses every phase except `status` for a run
listed here, so a later analyzer or edge-model change can never rewrite its stored answers.

| run | status | result |
|---|---|---|
| `v31-46d2e9af7d` | frozen | DEVELOPMENT · no bot passed every gate · edge gate uncalibrated (passed −0.250R vs refused −0.237R realized) · Jev V3 AUC 0.500 [0.463, 0.536], selection alpha −2.52 USDT |
| `v31-04f1291069` | frozen | TEST (pre-registered, 2026-05 → 2026-08) · no bot passed every gate · **ADVANCED SET = NONE** · edge gate passed −0.193R vs refused −0.253R realized · Jev V3 AUC 0.546 [0.499, 0.593], selection alpha −0.32 USDT |

The DEVELOPMENT run's RAW observations are the frozen evidence of the TEST edge gate
(docs/V31_TEST_PREREGISTRATION.json, fingerprint `99eb832170728587`).

Closest to a survivor, on record and NOT advanced: S33.1-ZEC-15m (CONTROL) — DEVELOPMENT +21.3% (PF 1.37) and
TEST +8.5% (PF 1.21), but it fails the pre-registered concentration gate in both windows (net without its best 3
trades −0.06 and −1.46 USDT) and DEVELOPMENT participation (0.48 < 0.5 trades/day). The only family x timeframe cell
net-positive after costs in both windows, pooled over the 10 coins, is S33.1 15m (+2.38 USDT on 181 trades; +3.70
on 149) — a V3.2 hypothesis, to be judged only on data V3.1 never saw.
