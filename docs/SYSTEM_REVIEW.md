# PaperLab system review and roadmap (2026-10-03)

This review checks PaperLab against the 25-point "professional trading system" brief. It was inspected first, then
measured, then changed only where the change is safe.

No change here is claimed to make the bots profitable. Everything below says what was measured, and how a change will
be kept or removed.

## 0. Bottom line

1. **The data does not offer an edge after market-order (taker) costs** at 5 minutes to 4 hours. The test covered 30
   Bybit perpetuals over 99 days (853,530 decisions), all 22 candidate features, and 4 horizons
   (`docs/ALTDATA_STUDY.json`). Under the pre-registered rules, nothing is TRADEABLE.
2. **The one robust effect is short-term mean reversion.** Coins that just rose underperform, and vice versa.
   - The effect shows at every horizon, in both halves, with |t| of 5 to 13.
   - It is worth 0–5 bp gross per trade. A taker round trip costs about 11 bp.
   - At maker costs, one variant (4h cross-sectional MA-slope reversal) is marginally positive: +1.1 / +0.6 bp.
3. **The alternative data adds almost nothing beyond price.**
   - Open interest, funding, the long/short ratio and taker imbalance were all REMOVED: no robust new information.
   - The premium index adds new information at 5–15 minutes, but it is worth under 1 bp.
4. **Your squeeze set-up does not survive proper statistics** (negative funding + OI jump + fast drop,
   `docs/SQUEEZE_STUDY.json`).
   - Its raw +38 bp per hour came from overlapping events and a few crash days.
   - Market-adjusted, counted per day, and without its best 3 days, it is −1.6 bp at 1h and +9.5 bp at 4h (t 0.3).
5. **ML is not justified** (`docs/ML_VS_SIMPLE_STUDY.json`).
   - Ridge and gradient boosting rank coins slightly better than the 1h-reversal rule (out-of-sample IC 0.043 vs
     0.036).
   - Their traded long/short spread is still about +0.1–0.9 bp, against about 11 bp of cost.
6. **News, structured into de-duplicated typed events, shows no effect** (`docs/NEWS_EVENT_STUDY.json`). No event type
   gives a stable volatility signal or a tradable direction. This agrees with the earlier V10 study.
7. **The live paper bots lose before fees too.**
   - V8 families: −2.6 to −4.3 USDT gross each; fees add about 0.19 R per trade.
   - No market regime rescues any family (`research/queries/paper_by_regime.sql`).
   - Spreads are not the problem: the real entry half-spread is 0.3–0.8 bp.

**What this means.** The binding constraint is cost and the absence of edge, not missing data. More data feeds and
more models will not fix that, and adding them would only add risk. The highest-value work is therefore:
- (a) safety of the real-money path and monitoring — done today;
- (b) a cost-aware research loop that can recognise an edge if one appears, and reject it when it doesn't — built
  today;
- (c) the few experiments that attack the cost side: maker entries on the reversal effect, as a new frozen paper
  experiment, only if you want one.

## 1. Current architecture

```
Bybit WS/REST (V6-V8), Bybit REST tape (V11/V12), Alpaca IEX (V9), Binance (V1 shadow)
        |
  worker processes (one per program, respawned with backoff)  -- DATA_STALE / LATE_DECISION / downtime gates
        |
  frozen strategies (fingerprinted; a change = a new experiment)  -- Jev LLM gate on the +JEV twins
        |
  ReplayEngine (paper fills: observed spread, maker/taker fees, funding, 60-120 s latency, level fills)
        |
  per-bot books in SQLite (/data/v*-forward.db)  -- elimination / qualification rules
        |                                   \
  dashboard + /api/public (read-only)        live mirror (operator-armed, testnet-first) -> Binance/Bybit
        |                                   /
  Telegram (trades + system alerts)  <--  watchdog (health -> alerts, SAFE MODE, /public/health/deep)

Research (local): DuckDB store -> feature engine -> pre-registered studies / SQL library / paper-trade attribution
Validation (existing): walk-forward -> Monte Carlo -> stress -> qualification gates
```

Strong points that already exist:
- **Versioning:** a freeze per experiment (code fingerprints, experiment ids).
- **Pre-registered studies** with discovery and confirmation halves.
- **Validation pipeline:** walk-forward (180/30/30 days), Monte Carlo (10,000 block bootstraps), and 8 stress
  scenarios.
- **Paper fills** use observed spreads.
- **Isolation:** each program runs in its own worker process, so a crash in one never stops the others.
- **Mainnet lock:** mainnet stays locked behind a testnet round trip plus an explicit switch.

## 2. Weaknesses

- The strategies have no measurable edge after costs. Every study since V3 says so, and today's study at five-minute
  resolution confirms it.
- Research logic was scattered across about 40 scripts, with no reusable feature store and no SQL research layer.
  This is now fixed (`research/`).
- The forward paper books never enforce the 12% daily halt.
  - It is configured in `app/competition/v6_config.py:129`.
  - But `ReplayEngine` never calls `check_daily_halt` (only the V1 engine does: `app/core/engine_dispatch.py:375`).
- Paper "qualification" (≥ 40 trades, PF ≥ 1.2 over ≥ 2 days) is a low bar with large sampling noise.
  - A QUALIFIED badge is not evidence of edge.
  - The live mirror is gated by more than the badge: testnet first, mainnet switch, loss limits.
- `/api/health` always answers ok. It did not reflect worker, feed or mirror state. This is fixed with
  `/public/health/deep`.

## 3. Dangerous components (real-money path)

Each item is either FIXED today (in `app/live/mirror.py`, with tests in `tests/test_mirror.py`) or OPEN.

| # | Danger | Status |
|---|---|---|
| D1 | An entry the exchange did not confirm (timeout / "UNCONFIRMED") was dropped. If it filled later, it was an untracked live position with no stop. | **FIXED.** The account is read back: a fill is adopted and protected, otherwise the order is cancelled. Both cases are alerted. |
| D2 | A stop order rejected by the exchange (status not checked) left the position unprotected. | **FIXED.** A rejected stop closes the position and is alerted. |
| D3 | A close that failed after the stop was cancelled left the position unprotected until the next check. | **FIXED.** The stop is put back at once, the close is retried every 30 s, and it is alerted. If even that fails, a 🚨 alert says the position may be unprotected. |
| D4 | Loss limits counted realized PnL only. | **FIXED.** Mark-to-market every 30 s, including the fees to close. |
| D5 | QUALIFIED was checked only when arming. | **FIXED.** It is checked at every new entry; a bot that drops out stops opening live trades. |
| D6 | Nothing was pushed to the operator: mirror errors, loss-limit stops, kill switch, outages. | **FIXED.** Telegram system alerts and the watchdog. |
| D7 | One mirror's reconciliation error aborted the whole cycle. | **FIXED.** Mirrors are reconciled independently; 3 failures in a row on one exchange turn on SAFE MODE there. |
| D8 | No limit across mirrors: committed amounts, same-direction stacking. | **FIXED.** Armed amounts are capped at the account's equity, and at most 2 same-direction live positions run per account. |
| D9 | Exits follow the paper bot with market orders. If the paper worker is down, a live position relies on its exchange stop alone, and no take-profit rests on the exchange. | OPEN — HIGH (R1 below). |
| D10 | Mirror fees are estimated (0.06%); funding is not tracked; exit slippage is not logged. | OPEN — MEDIUM. Entry slippage and order latency are logged from today. |
| D11 | The V8/V11/V12 worker processes do not install the log RedactFilter. They hold the OpenRouter key for Jev. | OPEN — MEDIUM. Those files are pinned by the freezes, so the fix belongs in the next re-freeze or in a spawn-time wrapper (R6). |

## 4. Data sources: what helps and what doesn't

Tested today, 30 coins, 99 days, at 5m / 15m / 1h / 4h horizons (`docs/ALTDATA_STUDY.json`):

| Data | Already had | Adds information beyond price? | Pays after fees? | Verdict |
|---|---|---|---|---|
| OHLCV, volume (RSI, ATR, VWAP distance, MA slope, range position, BTC beta/corr) | yes | — (the baseline) | no (0–5 bp vs 11 bp) | keep: the reversal effect is real but small |
| Open interest (5m, new) | 1h only | no | no | removed |
| Funding | yes | no | no | removed |
| Long/short account ratio (5m, new) | 1h only | no | no | removed |
| Premium index / basis (5m, new) | 1h only | yes at 5–15 min (t −6.8 / −2.7) | no (< 1 bp) | informative, not tradable |
| Binance taker buy/sell imbalance (new) | V3 only | no (it is the price-reversal effect again) | no | removed |
| News (Benzinga) → typed, de-duplicated events (new) | tone only | — | no | no effect |
| Bybit official announcements (new) | no | listings and delistings rarely touch our 30 coins | — | kept as an event feed |
| Liquidations | no history anywhere | untestable until collected | — | collect forward (R8) |
| Order-book depth / imbalance | no history | untestable until collected | — | collect forward (R8) |
| On-chain, whale flows, exchange in/outflows | no | needs a paid provider (Glassnode, CryptoQuant, Arkham) | — | OPTIONAL: only after R8 shows that flow data helps |
| Macro calendar | no | macro headlines showed no stable effect | — | OPTIONAL: as a risk filter (size down around FOMC / CPI), not a signal |

Redundant features (|ρ| > 0.7): r_1h ≈ RSI-14; r_4h ≈ MA slope; VWAP distance ≈ 1-day range position. When two are
redundant, keep one.

## 5. Missing risk protections

| Protection | State |
|---|---|
| Max risk per trade | Exists. Paper 1–2% (`SizingV6`, `_cap_risk`); live ≤ 2%. |
| Max daily loss | Live mirror: exists, now mark-to-market. **Paper forward books: configured but NOT enforced** (R3). |
| Max drawdown / disable strategy | Exists: floor halt at −25%, elimination, 30%-from-peak HALTED. |
| Max total / per-asset exposure | Paper: per book (8× gross, 4× net). Live: amounts ≤ equity (new). |
| Correlated exposure | Live: ≤ 2 same-direction positions per account (new). Paper books are independent by design. |
| Leverage limits | Exist: paper ceilings 20× / 3× / 2×; live ≤ 10×, isolated margin. |
| Volatility-adjusted sizing | Implicit: qty = risk / stop distance, and stops are ATR-based. |
| Stop-loss validation | Exists: minimum stop (8 bp engine, 1.0–1.5% per family), fee-vs-R gate. Live: the exchange stop is now verified. |
| Liquidity / spread / slippage checks at trade time | Spread and turnover are filters at universe selection only. Measured entry spreads are 0.3–0.8 bp, so a trade-time spread gate would rarely fire. LOW. |
| Stale data → stop new trades | Exists (`DATA_STALE`). Live now also has SAFE MODE from the watchdog. |
| Exchange API abnormal → stop new trades | Live: SAFE MODE after 3 failed reconciliations (new). |
| Latency rises → disable latency-sensitive strategies | Paper: `LATE_DECISION` gate. Live: order latency is logged from today; no automatic rule yet (part of R2). |

## 6. Backtesting

The pipeline is already realistic:
- maker/taker fees by achieved role;
- observed spreads;
- funding;
- latency;
- next-bar fills;
- a pessimistic intrabar path, level fills and gap fills.

Remaining problems:
- **Partial fills** are modelled only with an order book, which replay never has. This matters little at 20–200 USDT
  sizes.
- **Market impact** is zero (`size_component` = 0). That is fine at these sizes; it must be revisited before sizes
  grow.
- **Maker fills are optimistic.** They happen on a 0.5 bp trade-through, with no queue position and no adverse
  selection. Any maker-based edge must therefore be confirmed in forward paper trading.
- **Sharpe/Sortino are reported daily and not annualised** (`app/competition/metrics.py:233`). This is cosmetic.
- **The V8/V11 qualification thresholds** (≥ 40 trades in ≥ 2 days) are far below the validation pipeline's own
  gates (≥ 100 trades, profitable OOS ratio ≥ 0.55, ruin ≤ 5%). See R5.

## 7. Execution

- **Paper:**
  - market entries;
  - TPs as resting limits (maker) for V8 and V11;
  - stops filled at their level.
- **Live mirror:**
  - market entry and an exchange stop (Binance Algo API, verified on testnet);
  - market exit when the paper bot exits;
  - no exchange-side TP (D9).
- **Measured cost:** fees are about 0.13–0.20 R per trade for the scalpers. Fees are the largest controllable drag
  (`research/queries/paper_costs.sql`).

## 8. Monitoring

| Need | State |
|---|---|
| Trades opened / closed, TP hits | Telegram (filtered: V11, Bizzy, V8 controls). Replies thread to the opening message. |
| Large loss | NEW: a closed trade worse than −1.5 R is flagged in its Telegram message. |
| Daily loss limit, risk limit, strategy disabled | NEW: Telegram alerts for mirror loss limits, ELIMINATED, FLOOR_HALT and QUALIFIED changes. |
| Exchange API failure, WebSocket disconnect, stale data | NEW: watchdog alerts. Debounced (60 s) and capped at one alert per 30 min per program. |
| Bot restart | NEW: "PaperLab restarted" and per-worker restart alerts. |
| Unusual slippage | Entry slippage is logged per live order (NEW). There is no alert threshold yet (part of R2). |
| Daily report | NEW: Telegram daily summary after 00:05 UTC (net, trades, best/worst bot, mirror state). |
| External uptime monitor | NEW: `/public/health/deep` returns 200 or 503 and is public-safe. Point Uptime Kuma, UptimeRobot or BetterStack at it. |
| Error tracking (Sentry) | NOT FOUND. MEDIUM (R7): needs a free Sentry DSN from you. |
| Grafana | NOT FOUND. OPTIONAL: the dashboard, Telegram and deep health cover what matters now. |

## 9. Highest-value improvements

1. **The real-money path cannot leave a position unprotected or untracked**, and every incident reaches you. DONE
   today: D1–D8.
2. **Know when the system is unhealthy** before it costs money. DONE today: the watchdog, SAFE MODE, deep health and
   the daily summary.
3. **A research loop that tells the truth quickly.** DONE today: the DuckDB store, the feature engine, the studies,
   the SQL library and paper-trade attribution.
4. **Attack the cost side** with a single, pre-registered experiment (R4) instead of adding features. Mean reversion is
   the only robust effect, and it only has a chance with maker entries.
5. **Stop treating QUALIFIED as an edge** (R5) before any mainnet money.

## 10. Prioritized roadmap

For each item: the problem, the change, complexity, the downside, and how we will know.

### CRITICAL

**C1. Live-mirror order safety (DONE today).**
- **Problem:** D1–D4, D7 above.
- **Change:** read-back of unconfirmed entries, verified stops, stop restore on a failed close, mark-to-market loss
  limits, independent reconciliation.
- **Complexity:** medium.
- **Downside:** in a rare failure, an extra read-back call (about 1.5 s) on an unconfirmed entry.
- **Test:** 9 new failure-path tests against a misbehaving fake exchange; a testnet round trip before any mainnet use.
- **Keep:** always. This is not a performance change.

**C2. Watchdog, SAFE MODE and operator alerts (DONE today).**
- **Problem:** outages, loss-limit stops and the kill switch were silent; `/api/health` was always ok.
- **Change:** `app/live/watchdog.py`, Telegram system alerts, `/public/health/deep`.
- **Complexity:** medium.
- **Downside:** alert noise. This is mitigated by debouncing, the 30-minute cooldown, and the Telegram filter for
  bot-level alerts.
- **Test:** 10 tests; live check — all 6 programs LIVE and the endpoint returns 200.
- **Keep:** if alerts stay under about 10 a day outside incidents. Adjust the thresholds if not.

**C3. Portfolio limits across live mirrors (DONE today).**
- **Problem:** D5, D8.
- **Change:** QUALIFIED required at every entry; armed amounts ≤ equity; ≤ 2 same-direction positions per account.
- **Downside:** some live entries are skipped. Gamma's testnet mirror pauses until Gamma requalifies; it is ACTIVE
  in the new V8 experiment.
- **Keep:** always.

**C4. Before ANY mainnet money: an exchange-side take-profit and exits that do not depend on the paper worker (R1),
plus the R5 evidence bar.**
- **Problem:** D9. A dead worker means the live position waits for its stop; and a QUALIFIED badge is a weak bar.
- **Change:**
  - place the TP as a reduce-only limit on the exchange;
  - if the followed program is down for more than N minutes while a live position is open, close it.
- **Complexity:** medium.
- **Downside:** more orders to keep in sync. The TP must be cancelled or replaced when the paper bot exits differently.
- **Test:** fake-exchange tests, then a testnet round trip including the TP.
- **Keep:** always.

### HIGH VALUE

**R2. Execution-quality monitor for live mirrors.**
- **Problem:** entry slippage and latency are now logged, but nothing alerts on them, and exit slippage is not
  recorded.
- **Change:** record exit slippage; alert when the median slippage over the last 10 trades exceeds the paper model by
  more than 3 bp, or when order latency exceeds 3 s; SAFE MODE when it persists.
- **Complexity:** low.
- **Downside:** false alarms on thin testnet books.
- **Test:** compare live vs paper per trade over 30 trades.
- **Keep:** if it flags real drift without more than 1 false alarm a week.

**R3. Enforce the daily loss halt in the forward paper books (next re-freeze only).**
- **Problem:** the 12% intraday halt is configured but not enforced in `ReplayEngine`.
- **Change:** call the existing `RiskManager.check_daily_halt` in replay.
- **Complexity:** low code, but HIGH process cost: `replay.py` is pinned by V6/V7, so it ends every running
  experiment.
- **Downside:** a restart of all experiments.
- **Test:** a replay test that hits the halt.
- **Keep:** always. Bundle it with the next planned re-freeze; do not restart experiments for it alone.

**R4. ONE pre-registered cost-side experiment: maker-entry mean reversion (V13 paper, only if you want it).**
- **Problem:**
  - The only robust effect, reversal, is worth 0–5 bp, which is less than taker costs.
  - Every taker strategy is negative.
- **Change:** a new frozen paper experiment.
  - Entries are post-only limits a fixed fraction of an ATR beyond the current price, in the reversal direction.
  - Exits are maker TPs.
  - Signals are the strongest TS reversal features (VWAP distance, 4h MA slope).
  - No new data feeds.
- **Complexity:** medium (a new engine path for resting entry orders).
- **Downside:** maker fills are adverse-selected. The paper model cannot fully capture this, so live behaviour can be
  worse than paper.
- **Test:** pre-registered DEV/TEST halves on the 99-day store first, then 2–3 weeks of forward paper. Compare
  expected vs simulated fills.
- **Keep:** only if net R per trade is > 0 in both halves AND forward paper trading confirms with ≥ 200 trades.
  Otherwise remove it.

**R5. Raise the bar from QUALIFIED to a statistical promotion test before mainnet.**
- **Problem:** ≥ 40 trades / PF ≥ 1.2 over 2 days is mostly noise. For example, the earlier Gamma qualification
  vanished after the restart.
- **Change:** require the existing validation gates on the bot's frozen version before mainnet is unlocked for it:
  - walk-forward OOS profitable ratio ≥ 0.55;
  - Monte Carlo ruin ≤ 5%;
  - stress ALL_TAKER / SLIP_150 ≥ 0;
  - ≥ 100 forward trades.
- **Complexity:** medium (wire `app/competition/validation.py` into the mirror's mainnet check).
- **Downside:** probably no bot qualifies for mainnet. That is the honest outcome today.
- **Keep:** always.

**R6. Redact secrets in every worker process.**
- **Problem:** D11.
- **Change:** install `RedactFilter` before the worker module is imported. A small boot function in the unpinned
  `v6_service` spawn path, so no experiment restarts.
- **Complexity:** low.
- **Downside:** none.
- **Test:** a test that logs a fake key from a worker and finds it masked.
- **Keep:** always.

### MEDIUM VALUE

**R7. Sentry error tracking.**
- **Problem:** exceptions live only in Railway logs.
- **Change:** `sentry-sdk` with a `before_send` that removes headers, env and any configured key; active only when you
  set `SENTRY_DSN` (free tier).
- **Complexity:** low.
- **Downside:** an external service receives stack traces, scrubbed.
- **Keep:** if it surfaces at least one real issue a month that logs alone would have missed.

**R8. Forward collector for liquidations and order-book imbalance.**
- **Problem:** neither has any history, so neither can be tested.
- **Change:** a research-only collector on Railway:
  - the Bybit `allLiquidation` and `orderbook.50` streams for the 30 coins;
  - 1-minute aggregates: liquidation imbalance, depth within 10/25 bp, book imbalance;
  - stored in a separate SQLite file and pulled into DuckDB weekly.
- **Complexity:** medium.
- **Downside:** CPU and bandwidth on the shared Railway service, and 4–8 weeks before a study is possible.
- **Test:** the same pre-registered study as today's.
- **Keep:** the features only if they pass; the collector only while a study needs it.

**R9. Continuous research job.**
- **Problem:** studies are run by hand.
- **Change:** a weekly local job.
  - Steps: ingest → features → pull trades → re-run the studies and attribution.
  - It posts a summary to Telegram: what passed, what decayed, and proposals with their evidence.
  - It never deploys anything.
- **Complexity:** low–medium.
- **Downside:** proposals can tempt over-fitting. Every proposal must still pass a new pre-registered test.
- **Keep:** if it is read.

**R10. Per-trade feature snapshot (attribution at entry).**
- **Problem:** attribution is reconstructed afterwards from the store; live trades do not record their feature state.
- **Change:** a listener (like Telegram) that stores the `features_5m`-style state at each open.
- **Complexity:** low–medium.
- **Downside:** more storage.
- **Keep:** if attribution studies use it.

**R11. Mirror fee and funding truth.**
- **Problem:** D10.
- **Change:** read the actual fills, fees and funding from the exchange.
- **Complexity:** low.
- **Keep:** always.

### OPTIONAL

**O1. Docker microservices** (separate market-data, strategy, risk, execution and database services).
- Not recommended now.
- Each program already runs in its own process with restart and backoff, and the live path has its own guards.
- On Railway, separate services mean more cost and more failure points (network between services) for little
  measured benefit at this size.
- Revisit if live capital grows or a second server is needed.

**O2. Grafana.**
- The dashboard, Telegram and deep health cover what matters today.
- Worth it only with a Prometheus `/metrics` endpoint and a reason to watch long time series (for example, R2 drift).

**O3. n8n.**
- Scheduling inside the app already covers news ingestion, the daily summary and alerts.
- n8n would add another service to secure.
- It may be useful later for backups or reports; never for orders.

**O4. On-chain, whale and exchange-flow data.**
- These are paid feeds.
- Worth testing only if R8 shows that flow-type data carries information. The free proxies tested today (taker flow,
  OI) did not.

**O5. ML regime detection, anomaly detection, NLP news models.**
- Today's tests show that the simple versions carry no tradable information. Trailing z-scores already serve as an
  anomaly detector, and word-list or Jev tone already serves as news NLP.
- A heavier model is justified only after a simple version shows signal.

**O6. AI coding agents.**
- The workflow the brief asks for is already the house rule: propose → new frozen version → tests → studies (both
  halves) → forward paper → human review.
- Agents may write research code and tests. They never change a frozen production strategy.

## What changed in this pass

**Production code** (deployed; no frozen experiment touched; every freeze verified unchanged):
- `app/live/mirror.py`: C1, C3 and the alert hooks.
- `app/live/watchdog.py` (new): C2.
- `app/live/telegram.py`: system alerts, the large-loss flag, `wants()` and `kv()`.
- `app/main.py`: wiring, and `/public/health/deep`.
- Tests: `tests/test_mirror.py` (+12), `tests/test_watchdog.py` (new, 10), `tests/test_telegram.py`.

**Research** (local only; `research/`, excluded from the server image):
- `ingest.py`, `features.py`, `query.py` and `queries/*.sql`;
- `study_altdata.py`, `study_ml.py`, `study_squeeze.py`;
- `news_events.py`, `pull_trades.py`;
- results in `docs/ALTDATA_STUDY.json`, `ML_VS_SIMPLE_STUDY.json`, `SQUEEZE_STUDY.json`, `NEWS_EVENT_STUDY.json`.

**For you to do:**
- Point an uptime monitor at `https://paperlab-production-919c.up.railway.app/public/health/deep` (alert on non-200).
- Decide whether to run R4 (the maker-entry reversal experiment), and whether to add R7 (Sentry).
- Keep mainnet locked until C4 and R5 are in place.
