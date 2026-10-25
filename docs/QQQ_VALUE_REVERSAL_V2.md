# Corrected published-source QQQ reclaim experiment

The original v1 approximation was not signal-equivalent to the video's linked indicator.
It is superseded, and its report, rules and forward state remain archived separately.

Source: [LuxAlgo Value Area Reversion Signals](https://www.luxalgo.com/library/indicator/value-area-reversion-signals/),
linked directly in the [video description](https://www.youtube.com/watch?v=dUczefIYKIU).
The downloaded original Pine file is retained in `app/value_reversal/sources/` with its MPL-2.0
notice. The translated `indicator.py` is also MPL-2.0 and publicly downloadable on the result page.

## Actual mismatches corrected

| v1 approximation | Published source / v2 |
|---|---|
| Prior close had to be inside | Any outside close can arm a breakout |
| Two consecutive same-direction outside candles | Compare remembered directional volume across intervening opposite/doji candles |
| Reclaim volume compared with last candle | Compare with last bearish breakout volume for a long; last bullish breakout volume for a short |
| 64 rows; overlap-proportional allocation | 60 rows; equal allocation to inclusive touched bins |
| Greedy contiguous value-area expansion | Published radial expansion from POC, translated literally |
| Developing profile excluded current candle | Includes current completed candle, without future data |
| Five-bar timer counted the reclaim | Source counts outside bars; reclaim is not added to that count |
| Signals suppressed while trading or halted | Keep all arrows; execution eligibility is separate |

The video shows a five-bar input, while the published source defaults to ten. Both settings
are displayed, across 15/30-minute candles and previous/developing profiles: eight independent
experiments. The default remains 15-minute previous profile with the video's five-bar setting.
No return-based selection or tuning was performed.

## Independent source equivalence

The **full original Pine source** ran in PineTS 0.11.0 against exactly the same QQQ RTH bars as
the Python translation for all 2024–2025. Inputs were explicitly changed to five/ten bars and
QQQ's one-cent tick; diagnostic plots were appended. Signal logic and profile formulas were
not replaced. All **311,520 level and signal comparisons matched**, covering 38,940 candle
updates across the four timeframe/window runs, with both profile modes per run.

`sources/source-parity.json` records runtime, counts, scope and immutable source hashes.
Reproduce with a test-only `npm install --prefix data/qqq-source-validation pinets@0.11.0`,
then the `scripts/verify_qqq_source_parity.py` preparation and Node checker. PineTS is not a
production dependency. Equality on identical candles does not establish equality across
different feeds, QQQ versus NQ, or different sessions.

**Published-source limitation:** its radial expansion skips the unchosen bin at each distance.
Consequently the returned band can contain less than the requested 70%. v2 exposes actual
band coverage rather than silently changing the source algorithm or promising 70% coverage.
The retained fixture with 650 volume units captures 360 units (55.38%) using the published rule.

## What is still an explicit adaptation

The user selected **QQQ on Alpaca**. QQQ regular-hours profiles reset at 09:30 New York; NQ in
the video anchors at 18:00 and has an overnight futures session. QQQ volumes and price behavior
are different. This run tests the published signal formula on the approved QQQ session; it
does not reproduce the video's NQ arrows or Fabio's actual account performance.

The published file is an **indicator**, not an order-management strategy or verified backtest.
Our execution assumptions remain separately stated: close-confirmed signal, next-bar open,
excursion stop with one-cent buffer, opposite-boundary target (the initial strategy explanation),
whole-share 0.5% stop risk, <=100% gross exposure, session-close flatten and 20% drawdown halt.
Stops win intrabar stop/target ties; adverse gaps and rejected entry gaps are modeled. The
presenter's later 2R/3R/POC suggestions are discretionary alternatives, not silently optimized targets.
Base costs are 1 bp fees plus 2 bps slippage per side, with doubled-cost stress. Intraday borrow
fee is assumed zero and no locate is guaranteed. SIP data arrives at least 16 minutes late.

## Evaluation, deployment and preservation

Fixed periods remain 2022–2023 development and 2024–2025 retrospective comparison. Prior
research has seen these years; this is not an untouched holdout. New forward books have a new
start time, no startup fills, and independent persisted state. They submit no broker orders.
Daily/monthly results, benchmark, complete arrows and trade ledger are included in the JSON.
The replay endpoint reconstructs every closed-candle arrow for a selected historical session.

v1 `/data/qqq-value-reversal.json` and `qqq-value-reversal-report.json` are retained untouched.
v2 uses `/data/qqq-value-reversal-v2.json` and `qqq-value-reversal-v2-report.json`. No wallets,
signals or evidence are combined across rule versions. Existing live stock execution and
continuous market research are unchanged. Historical input cache is reused read-only.

Reproduce download and returns: `python scripts/qqq_value_reversal.py` (Alpaca paper credentials
through environment variables; GET calendar and consolidated market-data calls only).
