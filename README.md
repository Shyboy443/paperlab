# PaperLab

PaperLab is a research lab for algorithmic trading bots. Each bot is a pre-registered hypothesis: it is frozen, then run
forward on live market data with simulated fills and realistic costs, so that evidence, not intuition, decides whether
it is worth anything.

Most of the evidence here is negative, and that is the point. Every study keeps its pre-registered rules, its
results and its failures. The few bots that have survived a two-year backtest can be mirrored to a real exchange
account, but only by the operator, with explicit loss limits.

> **Not financial advice.** Everything here is research software. Paper results, backtests and qualifications are
> evidence, not promises. Real money is never traded unless the operator arms a live mirror by hand.

---

## Contents

- [What runs](#what-runs)
- [Results so far](#results-so-far)
- [Architecture](#architecture)
- [Safety model](#safety-model)
- [Running locally](#running-locally)
- [Deploying (Railway)](#deploying-railway)
- [Configuration](#configuration)
- [Research workflow](#research-workflow)
- [Repository layout](#repository-layout)
- [Documentation index](#documentation-index)

---

## What runs

Every program has its own database, its own freeze file (`docs/V*_FREEZE.json`) and its own worker process. Changing
any code a freeze pins starts a new experiment. Old results are never silently mixed with new ones.

| Program | What it is | Market | Docs |
|---|---|---|---|
| **V6 forward arena** | Hourly strategies that read Bybit positioning (funding, open interest, premium, account ratio), each with a Jev AI twin. **In the arena since 2026-10-08: only V6.6 and V6.2**, the two families that made money over the two-year backtest | Bybit perps: ARB, ENA, XRP, DOGE | [V6_PROTOCOL](docs/V6_PROTOCOL.md) |
| ~~V7 active challenger~~ | 15-minute trend and positioning strategies. *Switched off 2026-10-08* | Bybit perps: same coins | [V7_PROTOCOL](docs/V7_PROTOCOL.md) |
| ~~V8.3 scalpers~~ | 5-minute VWAP snap-back with Jev and ladder twins. *Switched off 2026-10-08* | Bybit perps: ETH, SOL, XRP, DOGE, ARB, ENA | [V8_PROTOCOL](docs/V8_PROTOCOL.md) |
| ~~V9 stocks~~ | The V8 scalp families on US stocks and ETFs. *Switched off 2026-10-08* | Alpaca IEX data (paper) | [V9_PROTOCOL](docs/V9_PROTOCOL.md) |
| **V10 scout** | News and Reddit attention research. It never trades | Data only | [V10_PROTOCOL](docs/V10_PROTOCOL.md) |
| ~~V11 scanners~~ | Four scanners over 30 coins with a 25/50/25 take-profit ladder. *Switched off 2026-10-08* | Bybit perps: 30 coins | [V11_PROTOCOL](docs/V11_PROTOCOL.md) |
| ~~V12 Bizzy~~ | Daily breakout, ported from beebots' "Bizzy Bee". *Switched off 2026-10-08* | ETH, SOL, HYPE | [V12_PROTOCOL](docs/V12_PROTOCOL.md) |
| ~~V13 Bounce~~ | Maker snap-back to the 24-hour VWAP. *Switched off 2026-10-08* | 29 coins | [V13_PROTOCOL](docs/V13_PROTOCOL.md) |
| ~~V14 HTF~~ | Copies of leading bots that only trade with the 4h / daily trend. *Switched off 2026-10-08* | V8 / V11 / V13 universes | [V14_PROTOCOL](docs/V14_PROTOCOL.md) |
| V15 day traders (research) | Five stock day-trading families (opening range, stocks in play, breakout, noise band, last half hour). **None passed**: the two-year study failed all five and V15.1's holdout year lost. Not running | Alpaca IEX (study only) | [V15_DAYTRADE_STUDY](docs/V15_DAYTRADE_STUDY.json), [V15_HOLDOUT](docs/V15_HOLDOUT.json) |
| **Stock trend** | Five-stock small-account monthly trend portfolio with an SMA200 market filter | Alpaca | [STOCK_TREND_LIVE](docs/STOCK_TREND_LIVE.md) |
| **Autonomous research** | Continuous template search over every Binance USDT spot pair and perpetual, with paper probation books | Binance (public data) | [AUTONOMOUS_RESEARCH](docs/AUTONOMOUS_RESEARCH.md) |
| **Video breakout** | A Bitcoin 4-hour breakout taken from a YouTube video, rules frozen before evaluation | BTC | [VIDEO_BREAKOUT](docs/VIDEO_BREAKOUT.md) |
| V1-V5 | The original Binance testnet bake-off and four research arenas. Now frozen history | | [LEGACY_V1_BAKEOFF](docs/LEGACY_V1_BAKEOFF.md), `docs/V2..V5_*` |

Switched-off programs keep their databases on the server as history; `app/core/programs.py` is the one list. V6's
other families (V6.1, V6.3, V6.4, V6.5) still compute inside V6's frozen experiment but are out of every list, feed and
Telegram message, because removing them from the field would restart the winners' experiment.

Each bot is named after a League of Legends champion and has a persona (`app/core/bot_names.py`). Twins share the
name ("Lux AI", "Lux Ladder"), and V14 copies carry their original's ("Lulu HTF"). The home screen shows the six best
bots in profit plus pinned groups; every other bot keeps running out of view.

## Results so far

A two-year backtest (October 2024 to October 2026) replayed all 93 running bots through their own live code, with
fees, funding and their sizing. The script is `scripts/backtest_2y.py`; the results are in
[`docs/BACKTEST_2Y_SUMMARY.json`](docs/BACKTEST_2Y_SUMMARY.json).

- **Only V6 made money.**
  - V6.6 (trend follower) was positive on all four coins: XRP +24.5%, ARB +19.8%, ENA +13.5%, DOGE +2.1%.
  - V6.2 (momentum rider) was positive on three: ARB +18.8%, DOGE +3.6%, ENA +2.3%.
- **Every fast bot lost.** That covers V7, V8.3, V9, V11, V13 and V14. Before fees they are roughly break-even; fees
  and spread turn that into a loss.
- **Follow-up studies tried to rescue them; none found a profitable fix.**
  - [`V9_SIGNAL_STUDY.json`](docs/V9_SIGNAL_STUDY.json): 104,000 setups x 480 exit configurations x 12 filters.
  - [`V9_STOCK_STUDY.json`](docs/V9_STOCK_STUDY.json) and [`V11_CRASH_STUDY.json`](docs/V11_CRASH_STUDY.json).
  - Wider stops and maker entries shrink the losses, but the entries themselves have no edge.

Earlier findings are summarized in [SYSTEM_REVIEW](docs/SYSTEM_REVIEW.md) and the `*_STUDY.json` files.

## Architecture

```
            Bybit / Binance / Alpaca public market data
                              |
   +--------------------------+---------------------------+
   |  one worker process per program (app/live/*_worker)  |
   |  ReplayEngine-based engines, run on live 1m bars:     |
   |  fills, fees, spread, funding, stops, targets, risk   |
   +--------------------------+---------------------------+
                              | events
   +--------------------------+---------------------------+
   | FastAPI app (app/main.py)                             |
   |  - SQLite per program on the /data volume             |
   |  - dashboards: / (private), /public/competition (r/o) |
   |  - realtime WebSocket / SSE stream                    |
   |  - Telegram notifier, watchdog, /public/health/deep   |
   |  - live mirror (operator-armed real orders)           |
   +-------------------------------------------------------+
```

Key modules:

| Path | Role |
|---|---|
| `app/backtest/replay.py` | The replay engine. The same code is used for history and for live paper trading |
| `app/live/scan_engine.py`, `v8_engine.py`, `v13_engine.py`, `v14_engine.py` | Program-specific engines: level fills, maker take-profits, limit entries, higher-timeframe checks |
| `app/strategies/` | Every strategy family, by program |
| `app/competition/v*_config.py` | Each program's bot list, execution, sizing, qualification rules and freeze |
| `app/live/*_worker.py`, `v6_service.py` | Worker processes. They resume their experiment after every restart |
| `app/live/mirror.py`, `providers.py`, `key_vault.py` | Live mirror, exchange providers, encrypted key vault |
| `app/live/telegram.py`, `trade_chart.py`, `trade_stats.py` | Telegram trade signals, with a chart and a confidence line |
| `app/live/watchdog.py` | Health checks, SAFE MODE for live mirrors, deep health endpoint |
| `app/live/v6_golive.py` | Which V6 bots may go live (a profitable two-year backtest) |
| `app/core/` | API routes, views, storage, auth, realtime stream |
| `app/dashboard/` | Front end: the operator console and public arena (plain JavaScript, no build step), in the Lovable dashboard's design (sidebar, `styles.css` "LAB THEME") |
| `app/dashboard/lab.js` | The Programs and Cost analyzer views, ported from the Lovable dashboard |
| `app/core/api_lab.py`, `app/ai/analyzer.py` | The private AI cost analyzer (OpenRouter; the key stays server-side) |

## Safety model

- **Paper by default.** Every program runs simulated fills (`DRY_RUN=true`). Paper workers have no order path.
- **Real orders only through the live mirror** (`app/live/mirror.py`). The operator arms it per bot on the private
  dashboard:
  - typed confirmation (`GO LIVE <bot>`);
  - an amount, risk per trade (2% at most), a daily loss limit and a total loss limit.
- **Eligibility is re-checked before every live entry.** A V8 bot must be QUALIFIED; a V6.2 / V6.6 bot must have a
  profitable two-year backtest and an unhalted paper book.
- **Mainnet is off unless switched on.** It needs `LIVE_MIRROR_MAINNET_ENABLED=true` and a recorded testnet round trip
  on that exchange.
- **Protection on the exchange.** Every live entry gets a stop-loss on the exchange itself, so it holds even if the
  server is down. Reconciliation runs every 30 seconds.
- **Off switches.** SAFE MODE pauses new entries while data is unhealthy. A per-bot STOP and a global KILL ALL flatten
  everything.
- **Secrets never live in code.** API keys and the Telegram token sit in an encrypted vault on the server (or in
  Railway variables). `.env` is git-ignored. Public endpoints are GET-only and sanitized.
- **Freezes keep evidence honest.** `docs/V*_FREEZE.json` fingerprints every module a program depends on. A mismatch
  stops that program (FROZEN_MISMATCH) instead of trading with code nobody validated.

## Running locally

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows; on Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # set DASHBOARD_PASSWORD at least
uvicorn app.main:app --port 8540
```

- Private dashboard: http://localhost:8540/ (user `admin`, password from `.env`).
- Public read-only arena: http://localhost:8540/public/competition.
- Both have Dashboard, Bots, Markets, Programs, Cost analyzer, Scout and System. The cost analyzer asks for the
  dashboard password on the public page.

The forward programs start only when their switches are on (see [Configuration](#configuration)).

Tests:

```bash
python -m pytest -q
```

That is about 1,500 tests, including freeze checks, mirror guards against a fake exchange, and engine timing.

## Deploying (Railway)

The service runs the Dockerfile (`uvicorn app.main:app`). State lives on a Railway volume mounted at `/data`.

```bash
railway up --detach --service paperlab      # from this folder; uploads the working tree
```

Every deploy restarts all workers; each one resumes its experiment from the database. Before deploying, check that
the freezes still match, so that a deploy does not silently start new experiments:

```bash
python -c "import importlib; [print(v, importlib.import_module(f'app.competition.{v}_config').verify_freeze(importlib.import_module(f'app.competition.{v}_config').load_freeze()) or 'OK') for v in ('v6','v7','v8','v9','v11','v12','v13','v14')]"
```

Long research jobs run detached on the same container (for example `scripts/backtest_2y.py` with `setsid nohup ...`).
They write to `/data`, so a deploy only interrupts the run in progress. Most of these scripts skip finished work when
re-run.

## Configuration

`.env.example` documents the base settings. The important switches:

| Variable | Effect |
|---|---|
| `DASHBOARD_PASSWORD` | Private dashboard and API login (Basic auth) |
| `V6_FORWARD_ENABLED`, `V8_FORWARD_ENABLED`, ... `V14_FORWARD_ENABLED` | Start each forward paper program at boot |
| `JEV_ENABLED`, `OPENROUTER_API_KEY` | The Jev AI twins (decision model via OpenRouter) |
| `ANALYZER_MODEL`, `ANALYZER_MAX_TOKENS` | The cost analyzer's OpenRouter model (default `anthropic/claude-opus-5.5`) and answer length (16000) |
| `SCOUT_ENABLED`, `AUTORESEARCH_ENABLED` | The research-only programs |
| `TELEGRAM_FILTER` | Which programs send Telegram trade signals (for example `v11,v12,v13,v14,v8:CONTROL`) |
| `LIVE_MIRROR_MAINNET_ENABLED` | Allows real-money live mirrors (off by default) |
| `LIVE_MIRROR_MAX_AMOUNT_USDT` | Cap per live mirror (default 200) |

Exchange and Telegram keys are entered on the private dashboard (System, then Providers & Live). They are checked
against the provider and stored encrypted; they are never shown again.

## Research workflow

1. **Pre-register.** Write the rules, the data window and the pass/fail criterion in the study script's docstring
   before running it.
2. **Split the data.** Discover on one half (DEV) and confirm on the other (TEST). A change is adopted only if it is
   better in both halves, or in both years for two-year studies.
3. **Freeze.** `scripts/v*_freeze.py` writes the program's freeze file. The live worker refuses to run if the code
   drifts from it.
4. **Run forward.** Let live paper data judge. QUALIFIED is a gate (minimum days, trades, profit factor and drawdown),
   not a profit claim.

Data sources:
- Bybit's public REST archive: `scripts/v5_bybit.py fetch` (1m klines, funding, open interest, premium, account ratio);
- a local DuckDB research store: `research/ingest.py`;
- Alpaca IEX bars for stocks.

## Repository layout

```
app/            the application (see Architecture)
scripts/        studies, freezes, backtests, data fetchers
docs/           protocols, freezes, study results, audits
tests/          pytest suite
research/       standalone research notebooks and scripts (DuckDB store, ML / alt-data studies)
seeds/          small seed datasets
snapshots/      archived evidence from earlier audits
```

## Documentation index

| Topic | Documents |
|---|---|
| System overview and roadmap | [SYSTEM_REVIEW](docs/SYSTEM_REVIEW.md), [BOT_IMPROVEMENT_AUDIT](docs/BOT_IMPROVEMENT_AUDIT.md) |
| Forward programs | [V6](docs/V6_PROTOCOL.md), [V7](docs/V7_PROTOCOL.md), [V8](docs/V8_PROTOCOL.md), [V9](docs/V9_PROTOCOL.md), [V10](docs/V10_PROTOCOL.md), [V11](docs/V11_PROTOCOL.md), [V12](docs/V12_PROTOCOL.md), [V13](docs/V13_PROTOCOL.md), [V14](docs/V14_PROTOCOL.md) |
| Live trading pieces | [TELEGRAM](docs/TELEGRAM.md), [LIVE_PRICE_UPDATER](docs/LIVE_PRICE_UPDATER.md), [STOCK_TREND_LIVE](docs/STOCK_TREND_LIVE.md), [MIN_NOTIONAL_AUDIT](docs/MIN_NOTIONAL_AUDIT.md), [VENUE_AUDIT](docs/VENUE_AUDIT.md) |
| Audits | [HTF_LOGIC_AUDIT](docs/HTF_LOGIC_AUDIT.md), [HTF_FOLLOWUP_AUDIT](docs/HTF_FOLLOWUP_AUDIT.md), [FEATURE_AUDIT](docs/FEATURE_AUDIT.md), [V9_STOCK_REPAIR_AUDIT](docs/V9_STOCK_REPAIR_AUDIT.md) |
| Research programs | [AUTONOMOUS_RESEARCH](docs/AUTONOMOUS_RESEARCH.md), [VIDEO_BREAKOUT](docs/VIDEO_BREAKOUT.md), `docs/*_STUDY.json` |
| History (V1-V5) | [LEGACY_V1_BAKEOFF](docs/LEGACY_V1_BAKEOFF.md), `docs/V2_*` to `docs/V5_*` |

A separate project, [drl-stocks](https://github.com/Shyboy443/drl-stocks), holds a deep-reinforcement-learning stock study validated against
the same standards. Its verdict was FAIL.
