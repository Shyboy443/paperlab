# V3.1 freeze (2026-09-24, before the V3.1 DEVELOPMENT arena ran)

These are the exact sources the V3.1 DEVELOPMENT arena and the pre-registered TEST evaluate.
`scripts/run_v31_arena.py` refuses to run when any of them differs. After DEVELOPMENT, V3.1 is never
edited: an analyzer hypothesis becomes V3.2, developed on DEVELOPMENT data and judged on unseen data.

| source | fingerprint |
|---|---|
| v3_base | `33d10d257921` |
| base | `0de866f0754c` |
| S33.1 | `05629760d058` |
| S35.1 | `21ad7d9b13c9` |
| S37 | `4a357437251a` |
| jev_prompt | `6dc3edafd6250578` |
| jev_policy | `99fc4089f1e22c97` |

* strategies: sha256 (12 hex) of each module under `app/strategies/v31/` and of the V3 base it inherits
  (`registry.v31_fingerprints()`); the V3 base is itself frozen in docs/V3_FREEZE.md
* jev_prompt: JEV_PROMPT_V3 question set; jev_policy: JEV_POLICY_V3 (`app/ai/jev/v3.py`, `v3_fingerprints()`)
* state: JEV_STATE_V3; risk profile: AGGRESSIVE_V31; protocol: V31_AGGRESSIVE_EDGE_PROTOCOL_V1
  (docs/V31_PROTOCOL.md); each run records its own config fingerprint
* What was looked at before freezing is listed in docs/V31_PROTOCOL.md section 2.
