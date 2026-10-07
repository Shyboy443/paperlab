# V3.0 freeze (2026-09-23, before the V3 AGGRESSIVE DISCOVERY ran)

These are the exact sources the discovery evaluates. `scripts/run_v3_arena.py` refuses to run when
any of them differs. After the discovery, V3.0 is never edited: an analyzer hypothesis becomes V3.1,
developed on DEVELOPMENT data and judged only on data it has never seen (docs/DATASET_SPLIT_V3.md).

| source | fingerprint |
|---|---|
| base | `33d10d257921` |
| S31 | `248382c2d387` |
| S32 | `b6352fcac101` |
| S33 | `c0f8ea89803b` |
| S34 | `64c7da9702c2` |
| S35 | `76dbfc1f8515` |
| S36 | `8a20a5fcf3a4` |
| prompt | `50626baa34cf7457` |
| policy | `8b296e6efd69a1fd` |

* strategies: sha256 (12 hex) of each module under `app/strategies/v3/` (`registry.v3_fingerprints()`)
* prompt: JEV_PROMPT_V2 question set; policy: JEV_POLICY_V2 (`app/ai/jev/v2.py`, `v2_fingerprints()`)
* state: JEV_STATE_V2; risk profile: AGGRESSIVE_V3; protocol: V3_AGGRESSIVE_PROTOCOL_V1 (docs/V3_PROTOCOL.md)

What was looked at before freezing, all on DEVELOPMENT data only: signal and entry counts, exchange
minimum rejections and cost-gate behaviour on single months (ZEC Nov 2025, SUI and AAVE Feb 2026),
and a pipeline smoke test on SUI 5m (Nov 2025 → Apr 2026) that also showed those six controls'
results. No strategy parameter was changed after that; one early-return in S35 was reordered with
identical behaviour before these fingerprints were taken.
