# V5 RESULTS FREEZE

These V5 runs are frozen research history. `scripts/run_v5_arena.py` refuses every phase but `status` on a run listed
here; their database rows, trades, analyses and summaries are kept as they are.

| run | role | window | result |
|---|---|---|---|
| `v5-1d32b2c190` | DEVELOPMENT | 2025-03-01 .. 2026-08-31 | 0 of 16 family x class with a raw edge; ADVANCED SET = NONE |
| `v5-10ba77cb14` | PSEUDO-HOLDOUT (TEST) | 2023-09-01 .. 2025-02-28 | Stage 1 replication only (no DEVELOPMENT survivor); ADVANCED SET = NONE |

## DEVELOPMENT (`v5-1d32b2c190`, config `3ae0ac8c90549002`, archive `71af0ed7551e66bf`)

Stage 1 raw edge (100 USDT CAPACITY twins, every setup legal; gate: trades >= 100 HOURLY / 40 SWING, gross R > 0 with
bootstrap P(mean <= 0) <= 5%, >= 2 of 3 sub-periods positive, >= 50% of coins positive, >= 0 without the best 5%):

| family | class | trades | gross R | P(mean <= 0) | sub-periods | net R @20 USDT (trades) | verdict |
|---|---|---|---|---|---|---|---|
| V5.1 POSITIONING_TREND | HOURLY | 272 | -0.096 | 0.949 | -0.25 / -0.02 / -0.06 | +0.011 (83) | NO RAW EDGE |
| V5.1 POSITIONING_TREND | SWING | 150 | -0.159 | 0.961 | -0.41 / +0.14 / -0.33 | -0.579 (21) | NO RAW EDGE |
| V5.2 FUNDING_OI_CROWDING_REVERSAL | HOURLY | 1184 | -0.021 | 0.730 | -0.02 / +0.08 / -0.13 | -0.081 (770) | NO RAW EDGE |
| V5.2 FUNDING_OI_CROWDING_REVERSAL | SWING | 241 | +0.057 | 0.245 | +0.09 / +0.24 / -0.18 | -0.091 (55) | NO RAW EDGE |
| V5.3 COMPRESSION_BREAKOUT_OI | HOURLY | 402 | -0.056 | 0.839 | -0.00 / -0.09 / -0.08 | -0.140 (203) | NO RAW EDGE |
| V5.3 COMPRESSION_BREAKOUT_OI | SWING | 87 | -0.224 | 0.991 | -0.10 / +0.04 / -0.47 | -0.614 (12) | NO RAW EDGE |
| V5.4 TREND_PULLBACK_POSITIONING | HOURLY | 1172 | +0.066 | 0.066 | -0.03 / +0.20 / -0.01 | +0.025 (929) | NO RAW EDGE |
| V5.4 TREND_PULLBACK_POSITIONING | SWING | 250 | +0.073 | 0.202 | +0.23 / +0.26 / -0.09 | -0.043 (59) | NO RAW EDGE |
| V5.5 DELEVERAGING_REVERSAL | HOURLY | 67 | +0.084 | 0.288 | -0.06 / +0.30 / -0.27 | -0.540 (22) | NO RAW EDGE |
| V5.5 DELEVERAGING_REVERSAL | SWING | 10 | -0.631 | 0.928 | -0.19 / -1.07 / – | – (0) | NO RAW EDGE |
| V5.6 CARRY_AWARE_TREND | HOURLY | 682 | -0.043 | 0.885 | -0.04 / +0.03 / -0.14 | -0.061 (195) | NO RAW EDGE |
| V5.6 CARRY_AWARE_TREND | SWING | 128 | -0.069 | 0.792 | -0.14 / +0.20 / -0.26 | -0.639 (9) | NO RAW EDGE |
| V5.7 STRUCTURAL_RANGE_REVERSAL | HOURLY | 567 | -0.087 | 0.893 | -0.05 / -0.06 / -0.16 | -0.167 (472) | NO RAW EDGE |
| V5.7 STRUCTURAL_RANGE_REVERSAL | SWING | 58 | -0.021 | 0.558 | +0.19 / +0.14 / -0.27 | -0.550 (32) | NO RAW EDGE |
| V5.8 MOMENTUM_CONTINUATION_OI | HOURLY | 207 | +0.129 | 0.130 | +0.05 / +0.29 / +0.09 | +0.071 (115) | NO RAW EDGE |
| V5.8 MOMENTUM_CONTINUATION_OI | SWING | 33 | -0.055 | 0.629 | -0.44 / +0.33 / -0.01 | -1.086 (5) | NO RAW EDGE |

Every family x class is negative without its best 5% of trades. Stages 2-3 did not run; no Jev call was spent.

160 official 20 USDT controls: gross +1.58, maker fees 0.00, taker fees 27.22, slippage 17.04, funding paid 2.38 /
received 3.39, **net -41.64 USDT** over 2,982 trades (HOURLY 2,789 trades, 0.064 / bot-day, 16.0 h average hold, gross
+0.016 R -> net -0.058 R; SWING 193 trades, 0.004 / bot-day, 41.6 h, gross -0.226 R -> net -0.290 R). 4,785 of 12,343
setups (39%) were refused by the exchange minimum at 20 USDT (MIN_NOTIONAL_LIMITED; never enlarged). 37 controls are
net-positive, 11 of them with >= 30 trades and 4 of those with P(mean <= 0) <= 10% — about what chance gives across 31
bots with >= 30 trades; none is active enough (>= 0.25 trades / day HOURLY) and no family passed, so none advances.

## PSEUDO-HOLDOUT (`v5-10ba77cb14`, config `182bcb98091d61b8` / pre-registered `69d328e7e7fc7d61`, archive `5d80adb7a01aa93e`)

Pre-registered before download (docs/V5_TEST_PREREGISTRATION.json); the run passed every fingerprint, universe and
configuration check. Universe (frozen rule, scored 2023-08-02 .. 2023-08-31): YGG, XRP, LPT, SHIB1000, OP, RUNE, DOGE,
APT, APE, ARB. Bybit-native archive: 9,987,864 rows, 0 missing minutes. Stage 1 for every family x class; Stages 2-3
had nothing to run. **ADVANCED SET = NONE** (certain before the run: no family passed DEVELOPMENT).

| family | class | trades | gross R | P(mean <= 0) | sub-periods | without best 5% | net R @20 USDT (trades) | TEST raw gate |
|---|---|---|---|---|---|---|---|---|
| V5.1 POSITIONING_TREND | HOURLY | 368 | +0.098 | 0.132 | +0.14 / +0.06 / +0.07 | -0.137 | +0.156 (107) | fail |
| V5.1 POSITIONING_TREND | SWING | 185 | +0.045 | 0.387 | +0.02 / +0.08 / +0.05 | -0.188 | -0.034 (31) | fail |
| V5.2 FUNDING_OI_CROWDING_REVERSAL | HOURLY | 690 | -0.047 | 0.840 | -0.27 / +0.24 / -0.06 | -0.221 | -0.109 (437) | fail |
| V5.2 FUNDING_OI_CROWDING_REVERSAL | SWING | 152 | +0.016 | 0.456 | -0.37 / +0.42 / +0.12 | -0.175 | -0.299 (49) | fail |
| V5.3 COMPRESSION_BREAKOUT_OI | HOURLY | 459 | +0.065 | 0.135 | +0.11 / -0.08 / +0.16 | -0.131 | +0.023 (257) | fail |
| V5.3 COMPRESSION_BREAKOUT_OI | SWING | 96 | +0.048 | 0.443 | +0.46 / -0.23 / -0.22 | -0.327 | -0.366 (21) | fail |
| V5.4 TREND_PULLBACK_POSITIONING | HOURLY | 833 | +0.109 | 0.019 | -0.11 / +0.32 / +0.04 | -0.136 | +0.044 (693) | fail (best 5%) |
| V5.4 TREND_PULLBACK_POSITIONING | SWING | 196 | +0.223 | 0.005 | -0.00 / +0.37 / +0.27 | +0.057 | +0.019 (73) | **PASS** |
| V5.5 DELEVERAGING_REVERSAL | HOURLY | 96 | +0.054 | 0.339 | -0.25 / -0.05 / +0.42 | -0.073 | -0.116 (36) | fail |
| V5.5 DELEVERAGING_REVERSAL | SWING | 9 | -0.172 | 0.614 | +1.64 / -1.09 / -1.05 | -0.731 | -1.066 (1) | fail |
| V5.6 CARRY_AWARE_TREND | HOURLY | 659 | +0.065 | 0.139 | +0.01 / +0.03 / +0.16 | -0.144 | +0.100 (195) | fail |
| V5.6 CARRY_AWARE_TREND | SWING | 158 | -0.058 | 0.715 | -0.05 / -0.01 / -0.18 | -0.267 | -0.120 (18) | fail |
| V5.7 STRUCTURAL_RANGE_REVERSAL | HOURLY | 389 | -0.129 | 0.951 | -0.05 / -0.01 / -0.27 | -0.345 | -0.227 (337) | fail |
| V5.7 STRUCTURAL_RANGE_REVERSAL | SWING | 26 | -0.150 | 0.681 | -0.62 / +0.44 / +0.84 | -0.351 | -0.238 (12) | fail |
| V5.8 MOMENTUM_CONTINUATION_OI | HOURLY | 254 | +0.102 | 0.186 | +0.01 / +0.20 / +0.12 | -0.209 | +0.102 (164) | fail |
| V5.8 MOMENTUM_CONTINUATION_OI | SWING | 35 | -0.307 | 0.915 | -0.25 / -1.02 / -0.17 | -0.543 | -0.191 (6) | fail |

160 official 20 USDT controls: gross +20.10, maker fees 0.00, taker fees 23.51, slippage 11.76, funding paid 2.43 /
received 3.84, **net -13.76 USDT** over 2,437 trades (HOURLY gross +0.053 R -> net -0.018 R; SWING -0.070 R -> -0.139 R);
4,044 of 9,909 setups refused by the exchange minimum. The 100 USDT twins: gross +187.85, taker fees 156.62, slippage
91.02, funding -27.12 / +28.68, net -58.17 USDT.

### The pre-registered diagnostic near-misses

| near-miss | DEVELOPMENT gross R (P) | PSEUDO-HOLDOUT gross R (P) | pre-registered verdict |
|---|---|---|---|
| V5.2 SWING | +0.057 (0.245) | +0.016 (0.456), n 152 | DIRECTIONALLY REPLICATED (barely) |
| V5.4 HOURLY | +0.066 (0.066) | +0.109 (0.019), n 833 | DIRECTIONALLY REPLICATED (fails the best-5% check) |
| V5.4 SWING | +0.073 (0.202) | +0.223 (0.005), n 196 | **REPLICATED** (passes the whole TEST raw-edge gate) |
| V5.8 HOURLY | +0.129 (0.130) | +0.102 (0.186), n 254 | DIRECTIONALLY REPLICATED |

The pre-registered expectation that V5.4 HOURLY's gross is short-side market beta was **not** borne out overall: its
shorts won on the pseudo-holdout too (+0.180 R, 668 trades; longs -0.177 R, 165), although the gross sits in the
falling 2024-03 .. 2024-08 sub-period (+0.32 R) and the rising first sub-period lost (-0.11 R). V5.4 SWING is positive on
both sides (shorts +0.253 R, longs +0.142 R) and at 100 USDT (net +0.179 R), but its official 20 USDT books net only
+0.019 R over 73 trades because 60% of its setups are below the exchange minimum.

Per the pre-registration none of this can promote a V5 bot. **V5.4 SWING is a V6 hypothesis** (trend pullback on
1D/1W with positioning, 12-72 h holds), to be tested on FORWARD data only — and at 20 USDT it is small-account
constrained, so its 20 USDT economics must be established there, not assumed from the 100 USDT twins.
