# V6 FORWARD ARENA — protocol (V6_FORWARD_PROTOCOL_V1)

V6 ends the research loop. V1–V5 are frozen research history: stopped, never restarted at deploy, under
VALIDATION → research history. V6 is built once, **frozen**, deployed, and then judged **only on live forward data**:
real Bybit market data (prices, observed spreads, funding, open interest, basis, long/short positioning), simulated
fills, real trading costs. `DRY_RUN=true`. There is no order path in the V6 worker, no real money, and no automatic
live promotion.

Nothing in V6 was fitted to V1–V5 results. V6.1 comes from V5's one surviving hypothesis (trend pullback with
positioning confirmation, which replicated only on V5's pseudo-holdout) and was written fresh, not tuned to that window.

## 1. What is measured

Whether frozen hourly and 4-hour swing strategies, with and without Jev, make money **forward** after every real cost.
No historical edge is claimed for any family.

Evidence maturity (per bot, per pair, per family and for the whole experiment). There is never a "WINNER":

| label | needs |
|---|---|
| TOO EARLY | anything less than COLLECTING |
| COLLECTING | ≥ 1 day and ≥ 3 closed trades |
| EARLY SIGNAL | ≥ 7 days and ≥ 15 closed trades |
| MATURE SAMPLE | ≥ 30 days and ≥ 30 closed trades |

A serious evaluation starts at MATURE SAMPLE. A bad first day is not a reason to change anything (§11).

## 2. The field: one bot = one coin

- Traded coins: the top 4 of the frozen universe rule (`docs/V6_UNIVERSE.json`). That is the V5 liquidity / cost /
  legality rule applied to standard crypto USDT perpetuals only; Bybit `symbolType` empty, so no tokenized stocks,
  ETFs or commodities. At the freeze: **ARB, ENA, XRP, DOGE**.
- HOURLY (the emphasis): V6.1–V6.6 on each coin, 1h signal, 4h + 1D context, time stop 24 h. That is 24 controls.
- SWING: V6.1 on each coin, 4h signal, 1D context, time stop 72 h. That is 4 controls.
- Every CONTROL has a matched **+JEV twin**, for 28 + 28 = 56 bots. Names: `V6.1-ENA-1H`, `V6.1-ENA-1H+JEV`,
  `V6.1-ENA-4H`.
- A bot trades only its own coin. Every bot observes the market context without trading it: BTC and ETH trends, and
  breadth, aggregate funding and aggregate OI over the frozen BREADTH SET (BTC, ETH and 14 liquid eligible coins).
- 20 USDT per bot, isolated books.

## 3. Families

| id | family | thesis (frozen) |
|---|---|---|
| V6.1 | POSITIONING_PULLBACK | context trends agree; pullback to the context EMA20; resumption bar; funding uncrowded; OI building |
| V6.2 | MOMENTUM_OI | context trend; top-15% multi-hour impulse; OI up ≥ 2% during it; 2–6 bar consolidation ≤ 50%; break |
| V6.3 | FUNDING_CROWDING_REVERSAL | funding ≥ 90th pct (≤ 10th) and basis ≥ 80th pct (≤ 20th); price ≥ 2 ATR past the EMA50; reversal bar |
| V6.4 | OI_BREAKOUT | Bollinger width in its bottom 20%; OI up ≥ 2% over 12 bars; close beyond the 20-bar range; not against 1D |
| V6.5 | DELEVERAGING_REVERSAL | OI down ≥ 5% within 4 h (swing 12 h) with a ≥ 2.5 ATR move; the bar closes back against the flush (OI is the liquidation proxy: Bybit publishes no liquidation history) |
| V6.6 | MARKET_ALIGNED_ALT_TREND | BTC 4h and 1D agree, ETH 1D not against, breadth ≥ 60%, aggregate OI not unwinding; the alt's 4h trend agrees, it outperforms BTC over 24 h and closes at a new 24-bar extreme |

Common exits: a structural stop (setup extreme + 0.25 ATR, clamped to 1–6%; a setup needing > 6% is refused), a 3R
target and the time stop. Parameters are in `docs/V6_FREEZE.json`.

**Pre-freeze smoke check (counts only, no PnL looked at).** The stored 28 days before the freeze gave these
candidate counts across the 4 coins: V6.1-1H 6, V6.2 12, V6.3 23, V6.4 12, V6.5 0, V6.6 2 (aggregate OI was only
stored for the last 3 days), V6.1-4H 0 (fewer than 40 closed daily candles in the stored window). The check found
that bug (fixed: the warm-up is now 45 days) and a continuity bug (§8). The families' own frequency notes are
estimates written before the check and look optimistic. Expect roughly 1–3 candidates a day across the whole field.

## 4. Data and features (Bybit linear, public)

- **Live stream**: WebSocket `kline.1.<coin>` (closed bars only, `confirm=true`) and `tickers.<coin>` for best
  bid/ask, mark, index, predicted funding and open interest. BTC and ETH tickers are observed too.
- **REST**: 1m klines (warm-up and gap repair), open interest (1h), premium index klines (basis), account long/short
  ratio (1h), funding history, market-context 1h klines and OI.
- **Warm-up** only: 45 days of 1m bars, 95 days of funding, 35 days of OI / basis / ratio, 45 days of context klines,
  3 days of context OI and funding. Warm-up bars never trade. No history is replayed at boot beyond this. No V1–V5 run,
  no validation or Monte Carlo.
- **The hour barrier.** At each hour H the market holds the hour-closing 1m bar until that hour's positioning points
  have been fetched, for at most 45 s, then records a **watermark**: the newest stamp of every series. A decision at H
  reads each series only up to its watermark, so a point that arrives later can never change a decision that was
  already taken. That makes the re-derivation after a restart deterministic. The barrier result is recorded per hour
  (complete / missing / waited).
- Everything a decision reads is stored in `fwd6_*` tables: bars with their observed spread, positioning points,
  watermarks and the funding charged.

## 5. Risk: AGGRESSIVE_V6 dynamic legal risk

Base risk is 1% of the bot's equity. When the exchange minimum order needs more:

| legal minimum needs | tier |
|---|---|
| ≤ 1.0% | TAKE at 1% |
| 1.0–1.5% | LEGAL_STRONG at exactly the legal minimum, only for a strong setup (quality ≥ 0.60) |
| 1.5–2.0% | LEGAL_CONVICTION at exactly the minimum for very high conviction (quality ≥ 0.85); on a +JEV bot otherwise ATTACK_ONLY (traded only if Jev says ATTACK) |
| > 2.0% | SKIP (`min_notional_above_max_risk`) |

Risk never exceeds 2%. Risk is never raised just to reach the minimum beyond these tiers. Every refusal carries its
reason (`min_notional_needs_strong_setup`, `min_notional_needs_attack`, `min_notional_above_max_risk`, `bot_halted`).
Engine guards are pinned: halt at 30% below peak (sizing), a 12% daily loss stops new entries for the day, a permanent
halt at 75% of the start, notional caps, leverage up to 20× only as needed.

## 6. Jev V6

A gate on the CONTROL's candidate. Jev never creates a trade. One question per candidate: do current positioning,
trend, market, volatility and funding conditions CONTRADICT, SUPPORT or STRONGLY SUPPORT the setup?

- CONTRADICT → **SKIP**.
- SUPPORT → **TAKE** at the strategy's own legal size.
- STRONGLY SUPPORT → **ATTACK**: 2% risk on a healthy bot (< 18% below peak, recent expectancy ≥ −0.25R), if the
  RiskManager accepts the order. Otherwise TAKE, recorded. An ATTACK_ONLY candidate is SKIP unless the answer is ATTACK.
- There is no DEFENSIVE level. A failed, late or malformed answer is SKIP.

The state (JEV_STATE_V6) holds only what was closed at the decision instant: the bot and setup, its own trend
ladder, the market context, the coin's positioning (with price/OI divergence), volatility, round-trip cost and
expected funding, the sizing tier and the bot's health. It never holds a future return, a leaderboard rank or a
claimed edge. Prompt, state builder and policy fingerprints are frozen. The model is part of the experiment identity.

Matched pairs: CONTROL and +JEV get identical market data, candidates, starting equity, execution assumptions and risk
constraints.

## 7. Execution, costs and the live gates

- **Decision instant** H = the signal candle's close. Hourly bots evaluate every closed 1h candle. Between closes they
  only manage what they hold: stops, targets, time stops and liquidation, checked on every closed 1m bar. There is no
  extra signal before the next close.
- **Decision window**: 2 minutes. Jev must answer by H + 115 s (25 s timeout, 1 retry). The order fills at the
  **open of the first 1m bar starting at or after H + 2 min**. That is always after the decision, for CONTROL and +JEV
  alike, and never on the signal candle.
- **Costs**: Bybit taker fee (0.055%) on every fill (every order is a market order, so maker fees are 0); the
  **observed** half spread recorded with each live bar, plus modelled slippage (level 2); funding at each real
  settlement at the rate frozen when the settlement passes: Bybit's predicted rate, recorded, else the settled rate.
  There is never a fee-free paper result.
- **Live gates**, applied to both bots of a pair before anything else:
  - `MISSED_DOWNTIME`: no session was running at the decision instant (only found during re-derivation). Never traded
    late.
  - `LATE_DECISION`: the decision would land after the window.
  - `DATA_STALE:<why>`: WebSocket down, no kline or ticker message for 90 s, OI older than 3 h, or funding older than
    one interval + 3 h. One verdict per coin and instant, shared by both bots of a pair. Stale data pauses decisions;
    V6 never trades on stale data.
- Every decision is recorded. The CONTROL's TAKE and every blocked candidate are recorded too.

## 8. Automatic start, forward start, continuity

- `V6_FORWARD_ENABLED=true` (Railway variable) makes the service start the worker process at every boot. There is no
  manual step (no SSH, no script). `V6_FORWARD_JEV` (default true) runs the +JEV twins. `DRY_RUN=true` stays.
- Boot sequence: migrations → verify the running code against `docs/V6_FREEZE.json` (a mismatch is FROZEN_MISMATCH
  and nothing trades) → experiment identity → resume or create → warm-up → market healthy → bots start → LIVE.
- **V6_FORWARD_START** is set once, when a new experiment's market is warm and healthy, and never changes. Only
  decisions after it count. Nothing is back-filled before it.
- **Redeploy continuity.** A resumed experiment re-derives every book from its forward start on the stored inputs.
  Every recorded decision is replayed, and Jev is never asked about the past. Equity, open positions, entry prices,
  stops, targets, trailing state, peak, drawdown, funding and halts carry over. A restart never closes a position:
  the engine's end-of-run flatten is never recorded. Nothing is recorded twice. A per-bot continuity check compares
  the re-derived book with the last saved snapshot and is reported.
- Every session loads stored history from the same anchor, the experiment's forward start. It never loads relative
  to "now", so a re-derivation weeks later sees the same 90-day, 30-day and 45-day windows the live decision saw.
  This was found and fixed before the freeze.

## 9. Freeze and experiment identity

`python scripts/v6_freeze.py --as-of <date> [--force]` writes `docs/V6_FREEZE.json`, containing:

- source fingerprints of every V6 strategy module, the V6 and V3 bases, the feature definitions, the V6 configuration,
  Jev V6, the gates, the live market's data semantics and the shared engine (replay, execution model, risk,
  portfolio, positions);
- all parameters and the configuration (execution, fees, risk profile, engine guards, windows);
- the universe and breadth set, instrument rules, funding intervals and Jev fingerprints.

The worker verifies all of this at every boot. The **experiment id** is `v6x-` plus a hash of (protocol, manifest
fingerprint, Jev model, venue). Same identity: resume. Anything different: a NEW experiment. Evidence from
incompatible experiments is never merged. Reporting code (runner, worker, service, views) is outside the identity. A
change there that would alter trading must bump the protocol version.

## 10. Outputs

- **Home** (public `/public/competition` and the private dashboard): `V6 FORWARD ARENA ● LIVE`, forward age,
  maturity, next 1h decision countdown, active bots, open positions, trades / 24 h, total virtual equity, net PnL,
  Jev edge, health (WS, BYBIT, DATA, JEV, hour barrier), leaderboard, activity feed (candidate → Jev → open → close),
  open positions (bot, coin, side, entry, mark, unrealized, risk, hold time), CONTROL vs +JEV pairs, gross → net,
  Jev live analytics (actions, latency p50/p95/p99, move during latency, selection alpha, ATTACK increment), families
  and risk distribution. The home has no backtest control. Research controls live in SYSTEM → RESEARCH (private).
- `GET /api/public/competition/v6`, `/v6/activity`, `/v6/bot/{key}`: read-only, allow-listed. The Jev state never
  leaves the server.
- `/public/inspection.json` → `v6_forward`: generated_at, forward_start, forward_age, bots, open positions,
  candidates / trades 24 h, gross, fees, slippage, funding, net, top bots, Jev pairs, risk distribution, min-notional
  skips, stream health.
- `/public/competition/report`: "V6 FORWARD LIVE" at the top.

## 11. Rules during the test

- **Do not change V6 while it runs.** No threshold, entry, exit, Jev prompt or sizing change based on live results.
- A **correctness bug** gets a fix, a re-freeze and a NEW experiment (a patched experiment with its own
  fingerprint). The old evidence stays separate.
- Ideas for improvement are written down as **V7 HYPOTHESES** and not applied.
- Do not build V7 because the first day or week looks bad. Let the experiment collect evidence.

### V7 hypotheses (recorded, not applied)

- None yet.

### User-requested challenger, 2026-09-25

The user subsequently requested more frequent paper trades with the 2% risk cap.
That request authorizes a separate V7 experiment described in `V7_PROTOCOL.md`.
V6's frozen trading rules, open positions and experiment identity remain intact.
