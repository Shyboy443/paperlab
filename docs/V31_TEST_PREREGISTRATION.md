# V3.1 TEST pre-registration (written 2026-09-24 01:02 UTC, before any TEST month was downloaded)

The machine-readable record is `docs/V31_TEST_PREREGISTRATION.json`; `scripts/run_v31_arena.py --role TEST`
refuses to run unless the sources, the configuration, the edge-model evidence and the field match it.
At the moment of writing, `/data/v31test` (the TEST archive) did not exist.

* **Data**: TEST 2026-05-01 → 2026-08-31 (2026-04 as indicator warmup only), Binance USD-M 1m klines and
  funding from data.binance.vision (SHA-256 verified), 10 coins (ZEC, UNI, HYPE, SUI, ARB, TAO, PENGU, ENA, AAVE, ZRO),
  into `/data/v31test` — the V3 archive is not touched. Costs and filters: Bybit linear (bybit v5 instruments-info (linear)).
* **Sources** (docs/V31_FREEZE.md): v3_base `33d10d257921`, base `0de866f0754c`, S33.1 `05629760d058`, S35.1 `21ad7d9b13c9`, S37 `4a357437251a`, jev_prompt `6dc3edafd6250578`, jev_policy `99fc4089f1e22c97`.
* **Configuration**: TEST config fingerprint `e55c6ed46328c78b` (without the dataset fingerprint).
* **Edge gate**: frozen DEVELOPMENT evidence of run `v31-46d2e9af7d` — 24,994 sequenced RAW
  observations with an exit ≤ 2026-04-30 23:59:59 UTC, fingerprint `99eb832170728587`; no TEST outcome is ever added.
  Thresholds: TAKE if predicted net ≥ 0.05R; ATTACK if net − se ≥ 0.1R, gross ≥ 2.0× cost
  and quality ≥ 0.6; EXCEPTIONAL if net − se ≥ 0.2R and quality ≥ 0.85;
  k = 30.0, family minimum 30 observations.
* **Jev V3**: JEV_PROMPT_V3 / JEV_STATE_V3 / JEV_POLICY_V3 (SKIP 0 · TAKE 1.0 · ATTACK 1.5 · STRONG_ATTACK 2.0), model
  `typesafe/jev-1.13`, a failed request is SKIP.
* **Risk**: AGGRESSIVE_V31 — TAKE 1.0% · ATTACK 1.5% · STRONG 2.0% · 20x ceiling · no ATTACK ≥ 18% below the peak · halt at 30%.
* **Maker / taker**: every bot is replayed taker-only; the maker-first experiment waits [5, 15] minutes, converts only
  within 0.25 × stop, conservative fills need a trade-through of max(1 tick,
  1.0 bp).
* **Qualification** (unchanged from DEVELOPMENT): ≥ 30 trades · participation 3m ≥ 2 / 5m ≥ 1.5 / 15m ≥ 0.5 / 30m ≥ 0.25 trades
  per day · net > 0 · ExpR > 0 · PF ≥ 1.1 · DD ≤ 30%, no halt · no liquidation ·
  positive without the best 3 and top 3 ≤ 60% of profit; +JEV3 also skip ≤ 80%, above the
  random action's 90th percentile with positive selection alpha, ≥ CONTROL + 0.5 USDT,
  P(support) AUC CI above 0.5, errors ≤ 2%, p95 latency ≤ 2000 ms.
* **Bots**: all 120 CONTROL bots (every family × coin × timeframe; no selection happens on TEST); for the
  30 field pairs below: +JEV3, ALWAYS TAKE, 20 RANDOM matched-action seeds each, CAPACITY at 50 and 100 USDT.
* **ADVANCED SET** = bots that pass every gate on BOTH DEVELOPMENT and TEST. DEVELOPMENT survivors: **none** —
  so the ADVANCED SET is NONE whatever TEST shows; TEST still runs, as the out-of-sample evidence on the V3.1 design.

## Field (30 pairs; pre-registered rule 15 pairs + amendment 1, activity only)

| family | coin | tf | DEV executed entries | origin |
|---|---|---|---|---|
| S33.1 | ZEC | 3m | 119 | pre-registered rule |
| S33.1 | SUI | 3m | 42 | pre-registered rule |
| S33.1 | PENGU | 3m | 38 | pre-registered rule |
| S35.1 | ARB | 3m | 47 | pre-registered rule |
| S35.1 | ZEC | 3m | 31 | pre-registered rule |
| S35.1 | AAVE | 3m | 13 | EXTENDED |
| S37 | UNI | 3m | 33 | pre-registered rule |
| S37 | AAVE | 3m | 14 | EXTENDED |
| S37 | PENGU | 3m | 13 | EXTENDED |
| S33.1 | ZEC | 5m | 130 | pre-registered rule |
| S33.1 | PENGU | 5m | 27 | EXTENDED |
| S33.1 | ARB | 5m | 20 | EXTENDED |
| S35.1 | UNI | 5m | 60 | pre-registered rule |
| S35.1 | ZEC | 5m | 59 | pre-registered rule |
| S35.1 | SUI | 5m | 24 | EXTENDED |
| S37 | UNI | 5m | 27 | EXTENDED |
| S37 | ENA | 5m | 13 | EXTENDED |
| S33.1 | ZEC | 15m | 87 | pre-registered rule |
| S33.1 | UNI | 15m | 38 | pre-registered rule |
| S33.1 | ENA | 15m | 32 | pre-registered rule |
| S35.1 | ZEC | 15m | 28 | EXTENDED |
| S35.1 | UNI | 15m | 13 | EXTENDED |
| S35.1 | ARB | 15m | 10 | EXTENDED |
| S37 | TAO | 15m | 71 | pre-registered rule |
| S37 | UNI | 15m | 35 | pre-registered rule |
| S37 | ZEC | 15m | 16 | EXTENDED |
| S35.1 | UNI | 30m | 10 | EXTENDED |
| S37 | ZEC | 30m | 38 | pre-registered rule |
| S37 | UNI | 30m | 19 | EXTENDED |
| S37 | TAO | 30m | 17 | EXTENDED |
