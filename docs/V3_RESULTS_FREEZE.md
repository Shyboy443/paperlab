# V3 results freeze (2026-09-24)

The V3 AGGRESSIVE DISCOVERY is finished and is evidence. Nothing may change it: no re-analysis, no
new phase, no re-run. `scripts/run_v3_arena.py` refuses every phase except `status` for a run listed
here, so a later analyzer change can never rewrite its stored answers.

| run | status | result |
|---|---|---|
| `v3-e39e94890b` | frozen | ADVANCED SET = NONE · 0 of 240 controls and 0 of 22 +JEV2 bots passed · Jev V2 AUC 0.511 [0.496, 0.527], n 5,921 |

Frozen with it: V3.0 strategy sources (docs/V3_FREEZE.md), JEV_PROMPT_V2 / JEV_STATE_V2 /
JEV_POLICY_V2, AGGRESSIVE_V3, the V3 protocol and amendment 1 (docs/V3_PROTOCOL.md), dataset
`4f4f48fcab0d1c0e`, config `612d69f8ab2fc72f`.

V3.1 (docs/V31_PROTOCOL.md) is a separate program: new strategy versions, an expected-net-edge gate,
JEV_POLICY_V3, its own tables (`v31_runs`, `v31_bots`) and runs. It reads V3's trades for its
root-cause analysis and never writes to V3's rows.
