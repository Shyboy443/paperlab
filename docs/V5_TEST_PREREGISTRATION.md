# V5 PSEUDO-HOLDOUT pre-registration (2026-09-24T12:27:05Z)

Window **2023-09-01 .. 2025-02-28** (warm-up from 2023-06); DEVELOPMENT run `v5-1d32b2c190` (advanced set NONE).

This is a PSEUDO-holdout: V1-V4 used parts of this period (docs/V5_DATASET_MAP.md), so it is not untouched data. No month of it was downloaded for V5 before this file existed.

Config fingerprint `69d328e7e7fc7d61` (dataset and rules snapshot excluded), universe fingerprint `1c7963695f75`.

Universe (frozen V5 rule, scored on {"from": "2023-08-02", "to": "2023-08-31"}): YGGUSDT, XRPUSDT, LPTUSDT, SHIB1000USDT, OPUSDT, RUNEUSDT, DOGEUSDT, APTUSDT, APEUSDT, ARBUSDT

**Stages on the pseudo-holdout.** Stage 1 for every family x class (baseline exits; replication of the raw-edge test). Stages 2-3 only for the DEVELOPMENT economic survivors listed here, with their frozen DEVELOPMENT exits; nothing is selected on TEST. There are none, so only Stage 1 runs.

**Expected.** ADVANCED SET = NONE with certainty: no V5 family x class passed the DEVELOPMENT raw-edge gate.

**Pre-registered diagnostic near-misses (DEVELOPMENT, failed the raw-edge gate):**

| family x class | DEV gross R | P(mean <= 0) | trades | sub-periods | failed checks |
|---|---|---|---|---|---|
| V5.2 FUNDING_OI_CROWDING_REVERSAL SWING | 0.0572 | 0.245 | 241 | [0.0913, 0.238, -0.1776] | significant, without_top_5pct |
| V5.4 TREND_PULLBACK_POSITIONING HOURLY | 0.0657 | 0.066 | 1172 | [-0.033, 0.1972, -0.01] | significant, subperiods, without_top_5pct |
| V5.4 TREND_PULLBACK_POSITIONING SWING | 0.0731 | 0.202 | 250 | [0.2339, 0.2647, -0.0855] | significant, without_top_5pct |
| V5.8 MOMENTUM_CONTINUATION_OI HOURLY | 0.129 | 0.1295 | 207 | [0.0525, 0.286, 0.0909] | significant, without_top_5pct |

Each DEVELOPMENT near-miss is DIRECTIONALLY REPLICATED if its TEST pooled gross R (100 USDT twins) is > 0 on >= the class trade minimum, and REPLICATED only if it passes the whole TEST raw-edge gate. Neither can promote a V5 bot: a replicated near-miss is at most a V6 hypothesis that must then be tested on FORWARD data only. Pre-registered expectation for V5.4 HOURLY: its DEVELOPMENT gross came from shorts (a falling altcoin market); if it is market beta rather than selection, its shorts lose in the mostly rising 2023-09 .. 2025-02 market.

| source | fingerprint |
|---|---|
| strategy:v3_base | `33d10d257921` |
| strategy:base | `7be736c53f9f` |
| strategy:V5.1 | `f4f72abc0a7d` |
| strategy:V5.2 | `3b421c29aec3` |
| strategy:V5.3 | `01e8ea7e986d` |
| strategy:V5.4 | `2fd17a549cf2` |
| strategy:V5.5 | `dfc5fbb80b56` |
| strategy:V5.6 | `0d541d70e47a` |
| strategy:V5.7 | `f54c7f2faec3` |
| strategy:V5.8 | `9decbe7ce090` |
| jev:prompt | `85b5d6019280fff2` |
| jev:policy | `d57140c8d63d45e6` |
| jev:state_version | `JEV_STATE_V5` |
| jev:prompt_version | `JEV_PROMPT_V5` |
| jev:state_builder | `cb256942dc82` |
| universe_rule | `c01f7c75cc3b` |
| source:v5_features | `476efb11f523` |
| source:v5_arena | `2b12fa3a2434` |
| source:v5_analyzer | `3acb63746779` |
| source:v5_run | `cfc2431290ee` |

Gates: see the JSON. a bot is ADVANCED only if it passes every gate in DEVELOPMENT (run dev_run_id) AND in this TEST. V5 is not patched: a failed pseudo-holdout is answered by a V6 hypothesis.
