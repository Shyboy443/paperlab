# PaperLab

The **five-stock small-account trend controls** are at `/public/stock-trend`,
linked from the main dashboard as **Stock portfolio**; see
[the execution guide](docs/STOCK_TREND_LIVE.md). The user's $9.50 allocation
can target five $1.88 fractional positions selected monthly by prior 63-session
return. The original liquid-20 aggregate SMA200 still gates stock/cash exposure.
This is a new variant with a separate retrospective test. It starts paused;
a bounded paper execution check must pass before authenticated live activation.
Connecting API credentials alone does not start trading.

A Binance **testnet-only** strategy bake-off: twenty deliberately aggressive intraday strategies run
side by side, **each with its own isolated 100 USDT paper book**, and the exchange only ever sees the
**net** position per symbol. A dark single-page dashboard shows what every strategy is doing, the tape
of virtual and exchange fills, and the risk state.

> **Warning**
>
> * **Testnet is not live.** Testnet books are thin, volume is fake, funding is flat and balances
>   get reset. Anything that "works" here says almost nothing about live markets.
> * **These settings can 100 % a virtual book in hours.** 2 % risk per trade at 15x virtual
>   leverage across twenty 1m/5m strategies is a stress test of the plumbing, not a portfolio.
> * **Booting on a volume written by an older build flattens every open position and rebases every
>   wallet to 100 USDT in a NEW epoch.** See *[Rebase on first boot](#rebase-on-first-boot)* — do it
>   when you want a clean run, not in the middle of one you care about.
> * **Nothing here is financial advice.** The strategies are illustrative plumbing exercises.

## What it is

* **20 isolated 100 USDT books, all armed from boot.** Every strategy gets its own wallet of
  `STRATEGY_STARTING_BALANCE` (default **100 USDT**), so the lab starts with 20 × 100 = **2000 USDT**
  of paper capital and every strategy trades from the first candle. Wallets never share cash:
  arming, disarming or blowing up one book changes nothing in the other nineteen, and the
  leaderboard is a like-for-like comparison because every strategy started from exactly 100.
* **20 strategies** (`app/strategies/s01…s20`), every one with GUI-editable parameters and a
  documentation block (idea / entry / stop / targets / sizing / why it is aggressive).
* **Paper fills.** Every fill is a market fill at the last price ± 1–3 bps slippage paying the taker
  fee; margin is reserved at the strategy's *virtual* leverage from its own wallet. The virtual book
  is authoritative and event-sourced in SQLite.
* **Net-delta router.** When `DRY_RUN=false` the router mirrors the per-symbol net of all virtual
  positions onto the testnet account with MARKET orders (reduceOnly when shrinking, flips split in
  two) and keeps one exchange-side backstop STOP_MARKET per symbol.
* **Testnet only.** Three venues are supported: Binance Futures Testnet, Binance Demo Trading
  (futures) and the Spot Testnet. Live production hosts are refused at config time and again at the
  socket layer.
* **Per-strategy analytics.** Trades are reconstructed from the fill ledger, scored per book
  (equity, realized, win rate, profit factor, avg R, max drawdown), rolled up into a daily table
  and annotated in a journal. All of it exports to CSV.
* **Dark GUI** at `/`: strategy leaderboard with enable toggles, allocation / leverage / size
  sliders, a per-strategy drawer (params, state, trades, signals, notes), positions with PAPER / NETTED
  liquidation labels, the router panel (virtual net vs exchange net, DESYNC badge, backstop), the
  Tape (fills, signals, orders, events), equity curves and the kill switch.

## Safety model

* **Explicit `.env` only.** `load_settings()` reads `paperlab/.env` by absolute path
  (`app.config.ENV_PATH == PROJECT_ROOT / ".env"`) and never walks up to a parent directory.
* **Live hosts are refused.** `app/config.py` is the only module allowed to mention a live Binance
  hostname (a test greps for that). Any `REST_BASE_OVERRIDE` / `WS_BASE_OVERRIDE` pointing at
  `*.binance.com` without `testnet` / `demo` in the host raises `ConfigError`, unless
  `MODE=LIVE_OVERRIDE_I_UNDERSTAND` is set on purpose.
* **Host allowlist before any socket.** The ccxt client is a subclass whose `fetch()` refuses any
  host outside `settings.venue.allowed_hosts` *before* a connection is opened, and the websocket
  feed asserts the host the same way before connecting.
* **No `set_sandbox_mode()`.** ccxt's sandbox flag has routed some unified calls to live
  `api.binance.com` in the past. Instead the whole `urls['api']` map is replaced: venue endpoints
  point at the venue REST base, every other key points at `https://blocked.invalid`, and only
  venue-native implicit endpoints (`fapiPublicGetKlines`, `fapiPrivatePostOrder`, …) are used.
* **Secrets are never logged.** A `RedactFilter` on every log handler masks the API key, secret and
  listen keys; boot errors shown in `/api/health` pass through the same filter.
* **`.env` is git-ignored** (so are `data/` and every `*.db`). Never commit keys, even testnet ones.
* **First boot arms everything.** All twenty strategies start enabled at `size_mult 1.0`, each on
  its own 100 USDT book — that is the point of the bake-off. Switch any of them off by hand from
  the grid; a disabled strategy still computes signals (recorded as `shadow`) and keeps its wallet.

## Getting testnet keys

### FUTURES_TESTNET (Binance Futures Testnet)

1. Log in at <https://testnet.binancefuture.com> (its own login; the account, balance and keys are
   separate from binance.com).
2. Open the **API Key** tab at the bottom of the trading page and copy the key and secret.
3. `MODE=FUTURES_TESTNET`, REST `https://testnet.binancefuture.com`,
   WS `wss://fstream.binancefuture.com`.

### FUTURES_DEMO (Binance Demo Trading)

1. On binance.com go to **More → Demo Trading (Futures)** and click **Start Demo Trading**.
2. Create a **Demo API key** at <https://www.binance.com/en/my/settings/api-management> under the
   *Demo* section (demo keys only work against the demo hosts).
3. Balances reset from **Assets → Reset** inside Demo Trading.
4. `MODE=FUTURES_DEMO`, REST `https://demo-fapi.binance.com`, WS `wss://demo-fstream.binance.com`.

### SPOT_TESTNET (Binance Spot Testnet)

1. Go to <https://testnet.binance.vision> and log in with GitHub.
2. Generate an HMAC key pair on that page.
3. `MODE=SPOT_TESTNET`, REST `https://testnet.binance.vision`,
   WS `wss://stream.testnet.binance.vision`.
4. Spot is **long-only**: no shorts, no leverage (virtual leverage is clamped to 1x), no funding,
   no exchange backstop. S16 (funding) is marked *venue unsupported*.

## Configuration (`.env`)

Copy `.env.example` to `.env`. All values are read once at start-up.

| Variable | Default | Meaning |
|---|---|---|
| `MODE` | `FUTURES_TESTNET` | `FUTURES_TESTNET`, `FUTURES_DEMO`, `SPOT_TESTNET` (aliases `TESTNET`, `DEMO`, `SPOT`); `LIVE_OVERRIDE_I_UNDERSTAND` is the only way to a live host |
| `BINANCE_API_KEY` | empty | venue key; not needed for `DRY_RUN=true` (public market data only) |
| `BINANCE_API_SECRET` | empty | venue secret |
| `DASHBOARD_PASSWORD` | required | Basic-auth password for user `admin` on every `/api/*` route except `/api/health` |
| `SYMBOLS` | `BTCUSDT,ETHUSDT,SOLUSDT` | comma-separated Binance symbols; changeable from the GUI |
| `ENGINE_ENABLED` | `true` | keep it `true` for a bake-off run: the engine boots straight into *running*. `false` boots into *paused* |
| `DRY_RUN` | `true` | `true` (recommended for a bake-off): virtual fills only, no exchange orders. `false`: same virtual book **plus** real testnet orders through the router (needs keys) |
| `STRATEGY_STARTING_BALANCE` | `100` | the isolated book **each** strategy starts with, in USDT (10 … 10 000 000). Paper total = this × 20, so the default run is 2000 USDT. Changing it rebases every wallet on the next boot |
| `DEFAULT_LEVERAGE` | `15` | **exchange** leverage set per symbol at boot (isolated margin); also the fallback virtual leverage |
| `DATA_DIR` | `<project>/data` | SQLite location; Railway's `RAILWAY_VOLUME_MOUNT_PATH` is used automatically |
| `PORT` | `8540` | listen port (Railway injects its own) |
| `BIND_HOST` | `127.0.0.1` | the Dockerfile sets `0.0.0.0` |
| `ALLOW_NO_AUTH` | `false` | skip Basic auth; refused on Railway or on a non-loopback bind with `PORT` set |
| `AUTO_RESUME_AFTER_HALT` | `false` | clear a daily halt automatically at the UTC rollover |
| `LOG_LEVEL` | `INFO` | Python log level |
| `TAKER_FEE` / `MAKER_FEE` | `0.0004` / `0.0002` | paper fee rates |
| `SLIPPAGE_BPS_MIN` / `SLIPPAGE_BPS_MAX` | `1` / `3` | paper slippage band, uniform per fill |
| `DAILY_HALT_PCT` | `0.12` | daily halt at −12 % of start-of-day equity (the whole lab) |
| `STRATEGY_HALT_PCT` | `0.25` | strategy halt at −25 % of **its own** book: 75 USDT on a 100 book |
| `MAX_TOTAL_NOTIONAL_MULT` | `8` | gross virtual notional cap, × paper equity (rejects, never clamps) |
| `MAX_NET_NOTIONAL_MULT` | `4` | per-symbol net notional cap, × paper equity (rejects, never clamps) |
| `RISK_PER_TRADE_PCT` | `0.02` | risk per trade as a fraction of that strategy's own wallet equity |
| `REST_BASE_OVERRIDE` / `WS_BASE_OVERRIDE` | unset | advanced; live hosts are refused |

## Run locally (Windows)

```bat
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env            :: then set DASHBOARD_PASSWORD (and keys if DRY_RUN=false)
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts\seed_candles.py
.venv\Scripts\python -m uvicorn app.main:app --port 8540
```

Open <http://localhost:8540> and log in as `admin` with `DASHBOARD_PASSWORD`.

`scripts/seed_candles.py` backfills 500 closed candles per symbol × timeframe (1m, 5m, 15m) from the
configured venue so the charts are not empty on first boot. `--synthetic` writes seeded random walks
tagged `source=synthetic` for offline GUI work; the engine deletes those for a series as soon as a
real backfill of that series succeeds. `--bars`, `--symbols`, `--tfs` and `--db` override the
defaults. Do not run it against a DB the server is currently using.

## Deploy on Railway

1. Push this folder to a GitHub repository (this folder is the repo root; `.env` stays out).
2. Railway → **New Project → Deploy from GitHub repo** → pick the repo. Railway builds the
   `Dockerfile` as declared in `railway.json` (healthcheck `/api/health`, restart on failure).
3. **Variables** tab: `MODE`, `BINANCE_API_KEY`, `BINANCE_API_SECRET`, `DASHBOARD_PASSWORD`,
   `SYMBOLS`, `DRY_RUN=true`, `ENGINE_ENABLED=true`, `STRATEGY_STARTING_BALANCE=100`,
   `DEFAULT_LEVERAGE`, `DATA_DIR=/data`, `AUTO_RESUME_AFTER_HALT`, `LOG_LEVEL`. Leave
   `ALLOW_NO_AUTH` unset (it is refused on Railway).
4. **Volumes**: add a volume and mount it at `/data` so the SQLite book survives redeploys.
5. **Settings → Networking → Generate Domain**.
6. Check `https://<domain>/api/health` — `engine_state` goes `booting → running` within a minute;
   `boot_error` is non-null when the boot failed (config, hedge mode, venue).
7. Open the domain and log in as `admin`.

## How the engine works

```
websocket feed ──► closed candle ──► every strategy (shadow when off) ──► RiskManager.approve()
     │                                                                          │ approved
     │  ticks / mark / forming 1m / 1 s timer                                    ▼
     └──────────────► ExitEngine (liq → stop → time → TP → BE → trail)     virtual fill (paper)
                                                                                │
                                                                    net-delta router ──► testnet MARKET order
                                                                                └──► backstop STOP_MARKET
```

* **Feed.** One combined websocket per venue (`kline_1m/5m/15m`, `markPrice@1s`, `depth10@100ms`,
  `aggTrade`) plus the user-data stream when `DRY_RUN=false`. It reconnects with jittered backoff,
  proactively before Binance's 24 h connection limit, and after 30 s of silence; the engine
  gap-fills missed closed bars after every reconnect. Funding is polled every 60 s.
* **Strategies always run.** A disabled strategy still computes signals; they are recorded with
  status `shadow` so the drawer shows what it *would* have done. Boot warm-up signals are `warmup`,
  signals on backfilled bars are `stale`; neither is traded.
* **RiskManager.approve()** sizes and gates every entry (see *Risk rules*). Rejections are recorded
  with their reason (`rr_below_min`, `max_positions`, `gross_notional_cap`, …).
* **Virtual fill.** MARKET at the last trade price ± slippage, taker fee charged, margin =
  notional / virtual leverage reserved from the strategy wallet.
* **Router.** After each batch of fills the router computes `target_net − exchange_net` per symbol
  (plus a sub-lot residual), rounds to the lot step and sends MARKET orders: reduceOnly when the
  order shrinks the exchange position, a flip as `[reduceOnly to zero, open remainder]`. Deltas
  below the exchange minimum notional wait unless they close the position. Virtual fills are
  **never rolled back**: an exchange rejection becomes a **DESYNC** badge and is retried every 10 s.
* **Exits are engine-managed** on every tick, mark-price update, forming 1m bar and a 1 s timer,
  at the current last price (paper liquidation uses the mark price). Order: paper liquidation →
  stop → time → take-profits (fractions of the *original* qty) → break-even → trailing. Fills
  happen at the current price, so a gap through the stop fills through it, like reality.
* **Backstop.** One exchange STOP_MARKET `closePosition` per symbol at
  `max(0.4 %, (1/lev_exchange − mmr) × 0.7)` from the exchange entry, re-placed whenever the net
  changes or the level moves by more than 0.1 %. If it fires, the virtual positions on that symbol
  are closed at the last price (estimated) and the symbol gets a 5 min cooldown.

### Virtual isolated ≠ exchange isolated

Each virtual position has its own leverage, margin and paper liquidation price (labelled **PAPER**
in the GUI). The exchange only holds the net: when S01 is long 0.01 BTC and S02 is short 0.01 BTC
the exchange holds **nothing**, so those legs get **no exchange liquidation, no funding and no real
fill** (labelled **NETTED**). The exchange-side backstop protects the *net* at `DEFAULT_LEVERAGE`:

| lev_exchange | mmr 0.4 % | mmr 2.5 % |
|---|---|---|
| 15x | 4.39 % | 2.92 % |
| 20x | 3.22 % | 1.75 % |
| 25x | 2.52 % | 1.05 % |

(distance from the exchange entry; floor 0.4 %). The exchange wallet is **not** the paper equity —
it only has to cover the margin of the net position at `DEFAULT_LEVERAGE`.

## Risk rules

* **Isolated books, no shared pool.** Each strategy owns one wallet of
  `STRATEGY_STARTING_BALANCE` (100 USDT by default) and nothing ever moves capital between them —
  not arming, not disarming, not a halt, not a blow-up. Only `POST /api/strategies/{sid}/allocation`
  changes one book, and it is clamped to **10 … 10 000 USDT** and refused (409) while that strategy
  holds a position.
* **2 % of that strategy's own wallet at risk per trade** (`RISK_PER_TRADE_PCT × ITS wallet equity ×
  strategy size_mult × signal size_mult`), converted to quantity by the stop distance, capped by
  the wallet's virtual leverage and rounded down to the lot step. A 100 USDT book and a 200 USDT
  book therefore size the same signal differently, and a book that has lost money sizes down on its
  own.
* **Strict minimum notional: 2 × the symbol's `minNotional`.** A 50 % partial exit must still clear
  the exchange minimum, so an entry needs 100 USDT of notional on BTCUSDT, 40 on ETHUSDT and 10 on
  SOLUSDT — always, not only when the signal carries a partial. On a 100 USDT book this is the
  binding constraint and rejections are expected; they show up in the Tape and in
  `rejects_today` as `below_min_notional:<got>&lt;<needed>`.
* **The caps reject, they do not clamp.** An entry that would push the gross virtual notional over
  8 × paper equity, or a symbol's net over 4 × paper equity, is refused outright
  (`gross_notional_cap`, `net_notional_cap`) instead of being shrunk to fit.
* **Per-strategy virtual leverage**, 15x by default and adjustable in the drawer (1–25x; the
  presets sit in the 5–25x band). Margin = notional / virtual leverage must fit that wallet's own
  available balance — leverage is per strategy, never shared.
* **Min R:R per strategy** (2.5 for most; 1.5 for S04 and S08; 0.75 for S03 and 0.5 for S15, whose
  targets are geometric — the opposite band / the cascade retrace — rather than R multiples; 0 for the grid).
* **Max 1 position per strategy** (S11 grid: 6 legs), never two on the same symbol/leg.
* **Gross cap 8× paper equity**, **per-symbol net cap 4× paper equity**, and with `DRY_RUN=false`
  the extra exchange margin of a delta may use at most **80 % of the exchange available balance**.
* **Daily halt −12 %** from start-of-day equity: flatten everything, pause the engine. Manual resume
  (`POST /api/engine/resume {"confirm": true}`) **re-bases** start-of-day equity to the current
  equity; `AUTO_RESUME_AFTER_HALT=true` clears it at the UTC rollover instead.
* **Strategy halt −25 % of its own book**: the floor is **75 USDT on a 100 USDT book**, not a share
  of the 2000 USDT paper total (it is re-based whenever that book's allocation changes). The halted
  strategy is disabled and flattened, the other nineteen carry on; clear it from the drawer
  (`POST /api/strategies/{sid}/halt/clear`).
* **Exchange margin halt**: if the exchange net position's liquidation distance falls to
  `max(2 × mmr, 0.5 %)` the symbol is flattened, its virtual positions closed and new entries on it
  rejected (`margin_halted`) until cleared.
* **Kill switch**: flatten every virtual position, close every exchange net position, cancel the
  backstops, disable all strategies. `resume` (with confirm) un-kills; you re-arm strategies by hand.
* **Fees 0.04 % taker / 0.02 % maker** (maker only when a strategy tags a signal as maker),
  **slippage 1–3 bps** per paper fill, paper funding applied at every funding timestamp.

## How to read the leaderboard

Every strategy starts from **exactly 100 USDT**, so the columns are directly comparable — no
allocation weighting, no shared pool, nothing to normalise.

| Column | What it means here |
|---|---|
| **equity** | that book's wallet: `100 + realized − fees + funding + unrealised`. Above 100 the strategy is up on the run, below 100 it is down. Nothing else can move it |
| **realized** | closed PnL of that book only, **before** fees |
| **fees** / **funding** | paper taker/maker fees paid and paper funding received or paid by that book |
| **upnl** | mark-to-market of that book's open positions |
| **WR** (win rate) | wins ÷ closed trades of that book. A trade is one `position_id`: the entry fill plus all of its exit fills, so a scale-out is **one** trade, not three. Break-even counts as a loss |
| **PF** (profit factor) | gross profit ÷ gross loss over closed trades, **net of fees**. Capped at 999 when there are no losses yet, `—` when there are no closed trades |
| **avg R** | mean R multiple, where R = net PnL ÷ (entry qty × distance to the original stop). Signals without a stop have no R and are left out |
| **max DD** | the worst peak-to-trough on that book's own equity curve, in % |
| **trades / open / today** | closed trades in this epoch, positions open now, trades closed since 00:00 UTC |
| **rejects** | entries the RiskManager refused today, grouped by reason. **This is data, not noise**: `below_min_notional` on most signals means the strategy's edge cannot be expressed on a 100 USDT book, and `rr_below_min` or `max_positions` tells you the signal was there but the rules said no. A strategy with 0 trades and 400 rejects has failed in a different way from one with 0 signals |

Read the run, not the day: 20 books × a few trades each on a testnet is a plumbing test. Use
`profit_factor` and `avg R` together with the trade count, and treat anything under ~30 closed
trades as a sample of one.

### The journal (`analysis_notes`)

The lab keeps a plain-text journal next to the numbers. The engine writes a line for every notable
event (kill, halt cleared, rebase, reset, params changed, arm/disarm, symbols changed), for every
open and close (with R, PnL and the resulting wallet equity), and for rejects. You can add your own
with `POST /api/notes {"strategy_id": "S01", "text": "…"}` (author `user`); the drawer shows the
last 20 lines for that strategy and `GET /api/notes` returns the rest.

**Reject notes are throttled to one line per (strategy, reason) per hour**, otherwise a strategy
that is below the minimum notional on every bar would drown the journal. The throttle only affects
the journal: **every** reject is still persisted in the `signals` table with its full reason, and
that table is what `rejects_today`, `GET /api/analysis` and the daily rollup count.

## How to export a run

Everything the dashboard shows is reconstructable from the CSV exports —
`GET /api/export.csv?table=<name>`:

| Table | What is in it |
|---|---|
| `fills` | the ledger: every entry/exit/funding fill with fee, slippage, realized PnL and the wallet equity and upnl right after it |
| `signals` | every signal with its status (`approved`, `rejected`, `shadow`, `warmup`, `stale`) and reject reason |
| `orders` | exchange orders (only with `DRY_RUN=false`) with their drift in bps |
| `equity` | equity curve points per series (`TOTAL` and one per strategy) |
| `events` | the engine's event log (boot, halts, kill, rebase, reset, …) |
| `candles` / `funding` | the market data the run saw |
| `strategy_daily` | one row per UTC day × strategy: start/end equity, realized, fees, funding, trades, wins, losses, win rate, profit factor, avg R, max DD, signal counts and the reject-reason breakdown |
| `analysis_notes` | the journal |

Filenames carry the epoch and a UTC stamp — `paperlab_fills_3_20260920T104501Z.csv` — so exports
from before and after a reset or a rebase never overwrite each other and are easy to tell apart.

Two JSON routes cover the same ground without SQL:

* `GET /api/analysis?strategy=S01&from=2026-09-01&to=2026-09-20` — the `strategy_daily` rows for
  the current epoch plus totals, the reject-reason counts and the journal.
* `GET /api/strategies/S01/trades?skip=0&limit=100` — the reconstructed trades (entry, exits, net,
  R, hold time) newest first, with a summary.

The daily rollup is recomputed idempotently (from the fill ledger, not incrementally), so a
re-import or a mid-day refresh cannot double-count a day.

### Rebase on first boot

The `wallet_model` marker in `meta` records the capital model a database was written with
(`isolated:100`). When the first boot on an existing volume finds a different marker — an older
build, or a changed `STRATEGY_STARTING_BALANCE` — the engine **rebases**:

1. every open position is closed at the last price, flagged *estimated*, as `wallet_rebase` fills
   recorded in the **old** epoch, so the previous run stays auditable;
2. the epoch is bumped and every wallet is reset to `STRATEGY_STARTING_BALANCE`;
3. all twenty strategies are re-armed at `size_mult 1.0`, halts and cooldowns are cleared;
4. a `wallet_rebased` event and journal line are written, and start-of-day equity is re-based so
   the new book does not trip the daily halt on a phantom drawdown.

Old fills are never rewritten — the earlier epoch's rows stay exactly as they were, and
`?table=fills` still exports them. **Do this on purpose**: export what you care about first, then
boot, and treat the new epoch as a clean run.

## Turning strategies on one by one

The default is the opposite — all twenty armed from boot — but you may want a quieter run:

1. Boot with `DRY_RUN=true` first. Watch the strategy grid: every card shows its last signal and
   status (`shadow`, `approved`, `rejected: …`) even while disabled, so you can see what a strategy
   *would* do before you arm it.
2. Disarm the ones you are not watching yet from the grid (their wallets stay at 100 and they keep
   producing `shadow` signals). Confirm the fills of the rest in the **Tape**.
3. Switch to `DRY_RUN=false` (see below). Confirm the same fills appear as exchange orders in the
   Tape (`orders`) and as a position on the testnet UI, and that the router panel shows
   `exchange_net == virtual_net` with a backstop order.
4. Re-arm the rest one at a time, checking the Tape after each.
5. Lower `size_mult` on anything that DESYNCs, and raise it back to 1.0 only after a clean day.

## Switching `DRY_RUN=false`

* Needs `BINANCE_API_KEY` / `BINANCE_API_SECRET` for the venue in `.env` (`ConfigError` otherwise).
* At boot the client forces **one-way position mode** (hedge mode with open positions aborts the
  boot), **isolated margin** and `DEFAULT_LEVERAGE` on every symbol, cancels orphan orders, then
  syncs the exchange net to the virtual net and places the backstops.
* The exchange wallet only needs to cover the net margin. Fills from the exchange are shown in the
  Tape next to the virtual ones with their drift in bps.
* Flip back to `DRY_RUN=true` at any time; the virtual book carries on, the exchange position is
  left as it is (flatten first if you want it closed).

## The 20 strategies

| ID | Name | TF | Idea |
|---|---|---|---|
| S01 | EMA Cross Momentum | 1m (+15m filter) | trade every EMA8/EMA21 cross; stop 1.2×ATR beyond the cross candle; TP1 2.5R half, ATR trail |
| S02 | RSI Extreme Sniper | 5m | RSI(7) < 18 then reclaims 22 → long (mirror 82/78); stop beyond the 10-bar swing − 0.8×ATR; 3R full |
| S03 | Bollinger Squeeze Break | 5m | prior bar's BB(20,2) bandwidth is a 20-bar low, close outside the band on 1.5× volume; stop opposite band; TP 1.6× height half, ATR trail |
| S04 | Donchian Breakout | 15m | break of the prior channel |
| S05 | VWAP Reclaim Scalp | 1m | reclaim of the session VWAP |
| S06 | Supertrend Flip | 5m / 15m | trade every supertrend direction flip, trail on the line |
| S07 | MACD Acceleration | 5m | histogram acceleration in trend direction |
| S08 | Opening Range Break | 1m | break of the UTC opening range |
| S09 | ATR Expansion Break | 5m | range expansion vs the ATR median |
| S10 | Dual-TF Momentum Pullback | 1m / 15m | 15m momentum, 1m pullback entry |
| S11 | Volatility Grid | 5m / 15m | 6-leg grid sized by margin % (badge GRID) |
| S12 | EMA Pullback Continuation | 5m | pullback to the fast EMA in an established trend |
| S13 | Volume Spike Range Break | 5m | range break on a volume spike |
| S14 | StochRSI + Trend Gate | 5m / 15m | StochRSI turns gated by the 15m trend |
| S15 | Liquidation Cascade Fade | 1m (+15m gate) | fade a ≥ 0.7 % flush on ≥ 3× volume while the 15m close is inside the prior 4 h range (badge PROXY) |
| S16 | Funding Extreme Fade | funding poll + 5m | fade extreme funding with a 5m RSI trigger (futures only, badge FUNDING) |
| S17 | Order Book Imbalance Scalp | book | top-of-book imbalance scalp (badge BOOK) |
| S18 | Relative Strength Rotation | 15m | rotate into the strongest configured symbol |
| S19 | Volatility Burst News Proxy | 1m | volatility burst as a news proxy (badge PROXY) |
| S20 | Ensemble Vote | 5m (+15m EMA50) | meta: |net votes| ≥ 3 of S01/S03/S06/S10/S12/S13/S19 agreeing with the 15m EMA50; needs ≥ 3 live contributors; size 1.4 at ≥ 5 votes (badge META) |

Every card's drawer shows the full documentation block, the parameter sliders and the live `state()`
of the strategy (last cross, RSI, squeeze flag, cascade, votes, …).

## Bot Trading Competition

A tournament that answers a different question from the leaderboard above. The leaderboard asks who
made the most; the competition asks **whose edge survives realistic costs, several market regimes,
out-of-sample testing and bad luck**. A bot can rank #1 and still fail every qualification gate.

```bash
python scripts/competition.py --months 2026-07,2026-08 --balance 20 --leverage 20
python scripts/competition.py --months 2026-08 --detail S04 --sort return
```

Every competitor starts the season with the same balance, is handed the same immutable bar sequence
in the same order, and keeps its own wallet. Isolation is structural: each competitor runs in its
own `ReplayEngine`, so it has its own `Portfolio` and `RiskManager` and there is no object through
which one bot could move another's balance. (This also keeps `RiskManager`'s portfolio-wide notional
caps *per book* — in the live lab those caps are shared on purpose, because twenty books share one
real account, but in a competition they would let a busy bot crowd out a quiet one.)

### One execution model

`app/backtest/replay.py` drives the **live** `Portfolio`, `RiskManager` and `ExitEngine`. Backtests,
competition seasons and paper trading therefore price, size, gate and exit through the same code.
The only thing replay adds is an intrabar price path, since klines have no ticks:

```text
open -> adverse extreme -> favourable extreme -> close
```

Adverse is resolved per position, so ambiguous bars resolve against the bot, and exits fill at the
path price rather than at the stop level — a gap through a stop costs more than 1R, as it does live.

### Gross vs net

The leaderboard's PnL column is always net. `gross` is PnL at **decision** prices, before any cost:

```text
gross trading PnL         +1.53
commission                -1.05
slippage (decision->fill) -0.48
funding                   -0.01
------------------------------
NET                       +0.50
```

Funding is real: historical rates come from the same `data.binance.vision` archive as the klines
(`app/backtest/funding.py`), so price history and funding history are always the same dataset.

### Competition in the dashboard

The bake-off GUI gained a **Competition** tab (`/#competition`) — same shell, same auth, same
polling, same card/table/pill components as every other tab. Sub-views:

| view | what it answers |
|---|---|
| Overview | season header, qualification-state tiles, competition-wide cost totals |
| Leaderboard | ranked rows with **rank and qualification as separate columns** |
| Bots | one competitor's isolated account, gates, score, ledger |
| Seasons | every stored season; click one to switch the whole tab to it |
| Execution | legacy vs realistic execution, side by side |
| Validation | which stages have run — unrun ones show `NOT RUN`, never a pass |
| Settings | start a new season |

A banner states the product concept directly: **ranking is not qualification**. A bot at rank #1
with +29.9% still shows `INSUFFICIENT_SAMPLE`, and a profitable bot that was liquidated shows
`DISQUALIFIED` — green is reserved for states that actually passed the gates.

Each bot page shows the 20 USDT account top to bottom: starting balance → gross PnL at decision
prices → commission, spread, latency, impact, funding → current equity, with a note that the three
slippage terms are a *decomposition*, not extra charges.

**Running a season.** `POST /api/competition/run` returns immediately with a run id and replays on a
worker thread (`asyncio.to_thread`), because a month of 1m bars takes minutes and would exceed the
platform request timeout. The dashboard polls `/api/competition` for progress and shows a
provisional leaderboard while it runs. Only one season runs at a time.

**Where the bars come from.** Seasons replay candles already in the `candles` table — the same rows
the live feed backfills — so a run never downloads an archive at request time. To load a full month
for a season, seed it first:

```bash
python scripts/seed_archive.py --months 2026-08
```

That writes both candles and real funding settlements; without the funding rows a season reports a
funding cost of exactly zero and understates every bot's net result.

**Persistence.** Seasons are stored in `competition_runs` / `competition_competitors` (schema 5,
additive). They live in the same SQLite file as everything else, at `DATA_DIR/paperlab.db`. On
Railway `DATA_DIR=/data`: **if `/data` is not a mounted volume, competition history is lost on every
redeploy and restart.** Nothing else changes — `railway.json`, the `Dockerfile`, the healthcheck and
the `$PORT` binding are untouched.

**Live trading is unaffected.** No competition route can promote or arm anything; `LIVE_CANDIDATE`
is a label. Real money still requires the Controls tab, strategy selection, the pre-arm checks and
the `GO LIVE` phrase. There is a test that parses both competition modules and fails if either one
*calls* a promotion or order-placing function.

### Execution model

One `ExecutionModel` prices every fill for backtests, competitions and paper trading
(`app/execution/`). It picks the best level the data supports and records which one it used on
every fill — it never degrades silently.

```text
LEVEL 1  order book   walk the far side, VWAP, partial fills on thin depth
LEVEL 2  bid/ask      BUY at/above ask, SELL at/below bid, plus modelled impact
LEVEL 3  OHLCV        half-spread (tick-derived) + volatility widening + size impact
```

Historical tournaments run at **level 3**, because the Binance archive publishes no historical
order books — `bookDepth` is aggregated ±1%…±5% notional, not price levels — and best bid/ask
(`bookTicker`) exists only for 2023-05 → 2024-04. Level 1 is reachable live, from the websocket
`BookSnapshot`, which is where the shadow league will use it.

**Spread calibration.** On liquid USD-M majors the top of book is exactly one tick wide, measured
against production quotes:

| symbol | spread | half | tick / price |
|---|---|---|---|
| BTCUSDT | 0.012 bps | 0.006 | 0.1 / 85966 = 0.0116 |
| ETHUSDT | 0.036 bps | 0.018 | 0.01 / 2750 = 0.036 |
| SOLUSDT | 0.852 bps | 0.426 | 0.01 / 117.3 = 0.852 |

So level 3 derives the half-spread from tick size and adds a small volatility term. Deriving it
from ATR instead produced ~23 bps a side, enough on its own to invert the leaderboard.

**Latency.** `signal_latency_ms + order_latency_ms` (400 ms by default) is applied: an order
signalled on bar N's close executes against bar N+1's open. The market state an order is priced
against uses the *previous* closed bar's range and volume, because the executing bar's high, low
and volume have not happened yet.

**Maker / taker.** Market orders and crossing limit orders are TAKER. A resting limit order is
MAKER only once it actually receives quantity, and only when the market traded *through* its price
— touching it is not a fill. Fees come from `FeeSchedule` keyed on the role achieved, not requested.

**Cost decomposition.** Slippage is reported as three terms that sum to it exactly, never in
addition to it:

```text
latency in flight   how far the market moved while the order was in transit
spread crossed      the cost of reaching the far side
market impact       the cost of being large relative to available liquidity
```

### Maintenance margin

Liquidation uses Binance's **notional-tiered leverage brackets** (`app/backtest/brackets.py`),
fetched from production and cached with source and retrieval timestamp. `exchangeInfo`'s
`maintMarginPercent` is **not** the bracket rate: it reports 2.5% for every symbol because that is
the generic 20x-tier default. Bracket 1 — the tier a 20 USDT book actually trades in — is 0.4% for
BTCUSDT and ETHUSDT, 0.5% for SOLUSDT. Using 2.5% liquidates roughly six times too early.

### Exchange metadata

`MarketRules` come from the venue's real `exchangeInfo`, cached with venue, environment and
retrieval timestamp (`app/backtest/rules.py`). The default environment is **production**, because a
competition that models real trading must size against real constraints even though every order is
simulated — testnet disagrees (BTCUSDT `minQty` 0.0001 vs production 0.001, `liquidationFee` 0.02
vs 0.0125). `app/config.py` is still the only module that names a live host; the metadata endpoints
live there as read-only constants and never carry an order.

### Qualification

Ranking and qualification are separate columns. Hard gates live in `QualificationConfig`; states are
`COMPETING → INSUFFICIENT_SAMPLE | FAILED | WATCHLIST | QUALIFIED → SHADOW_LIVE → LIVE_CANDIDATE`,
plus `DISQUALIFIED` on liquidation. A stage that has not been run does not pass — a bot with no
walk-forward, Monte Carlo or stress evidence lands on `WATCHLIST`, never `QUALIFIED`.

`LIVE_CANDIDATE` sends nothing. Real capital still requires the existing operator path: `promote`,
the `GO LIVE` arm phrase, the live whitelist and `max_live_strategies`.

### Strategy versioning

A competitor is `strategy + parameters`, hashed (`S21:a1b2c3d4`). Moving a GUI slider after seeing
out-of-sample results produces a **new** competitor whose evaluation restarts, so the leaderboard
cannot be farmed by retuning against the test window.

### Reproducibility

`Season.fingerprint()` hashes symbols, window, fees, execution model, risk limits, gates, weights
and seed. Results are stored against it; if the fingerprint changes, it is a different season.

## API summary

Basic auth `admin:<DASHBOARD_PASSWORD>` on everything except `/api/health`; every `POST /api/*`
also needs the header `X-PaperLab: 1` (CSRF guard). JSON bodies where noted.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | mode, venue, dry_run, engine_state, feed_connected, boot_error (no auth) |
| GET | `/api/state?curves=1` | the whole dashboard payload (equity, risk, strategies, positions, router, tape) |
| GET | `/api/strategies` · `/api/strategies/{sid}` | leaderboard rows / drawer detail (doc, params, state, scoreboard, trades, open positions, signals, rejects_today, daily, notes) |
| POST | `/api/strategies/{sid}/enable` `{"enabled": true}` | arm / disarm |
| POST | `/api/strategies/{sid}/params` `{"params": {…}}` | set parameters (clamped, unknown names ignored with a warning) |
| POST | `/api/strategies/{sid}/allocation` `{"allocation", "leverage", "size_mult"}` | that book's allocation (10–10 000), virtual leverage (1–25), size (0.05–3); 409 while it holds a position |
| POST | `/api/strategies/{sid}/flatten` · `/halt/clear` | close its positions / clear a −25 % halt |
| POST | `/api/kill` | kill switch |
| POST | `/api/engine/start` · `/stop` · `/resume` `{"confirm": true}` | pause / run / clear halt + un-kill |
| POST | `/api/flatten` | close every virtual position |
| POST | `/api/reset` `{"confirm": true}` | new epoch: every wallet back to `STRATEGY_STARTING_BALANCE`, all twenty re-armed at size 1.0, halts cleared (no open positions allowed) |
| POST | `/api/symbols` `{"symbols": [...]}` | change the symbol set (restarts the feed) |
| GET | `/api/equity?series=TOTAL|S01&since_ts=&points=` | equity curve points |
| GET | `/api/candles?symbol=&tf=&limit=` | in-memory candles (with source) |
| GET | `/api/tape?limit=` | fills, signals, orders, events |
| GET | `/api/strategies/{sid}/trades?skip=&limit=` | reconstructed trades (newest first) + summary |
| GET | `/api/analysis?strategy=&from=&to=` | `strategy_daily` rows, totals, reject counts and the journal |
| GET | `/api/notes?strategy=&limit=` · POST `/api/notes` `{"strategy_id", "symbol", "text"}` | read / append journal lines |
| GET | `/api/export.csv?table=fills\|signals\|orders\|equity\|events\|candles\|funding\|strategy_daily\|analysis_notes` | CSV export; the filename carries the epoch (`paperlab_<table>_<epoch>_<utc>.csv`) |

## Known testnet limitations

* **Thin order books**: S17 skips most of the time, and exchange fills drift from virtual fills
  (the Tape shows the drift in bps per order).
* **Weak aggTrade volume**: S09 / S13 / S15 / S19 fire erratically; their "volume spike" gates are
  a proxy, not a liquidation feed.
* **Flat funding**: S16 rarely triggers on testnet.
* **Periodic testnet balance resets** close exchange positions behind our back; the engine detects
  an exchange-flat symbol and closes its virtual positions with `exchange_flat` fills labelled
  *estimated*.
* **24 h stream reconnects and the 60 min listenKey expiry** are handled (proactive reconnect,
  keepalive every 30 min, re-key on `listenKeyExpired`), with a gap fill after every reconnect.
* **`closePosition` backstops** may be rejected by some testnet symbols; the client falls back to a
  `reduceOnly` STOP_MARKET with an explicit quantity.
* **positionRisk lags fills by seconds**: the router keeps its own view after each fill and refreshes
  from the exchange every 10 s; a brief `exchange_net ≠ virtual_net` right after a fill is normal.
* **The exchange wallet is not the paper equity.** It only needs to cover the net margin at
  `DEFAULT_LEVERAGE`; paper equity lives in the virtual wallets.
* **SPOT_TESTNET is long-only**: shorts are rejected (`venue: no shorts`), leverage is 1x, no
  funding, no backstop.
* **PnL ignores BNB fee discounts** (flat 0.04 % / 0.02 %).
* **v1 paper funding** is applied to every virtual position at each funding timestamp, even when
  the exchange net is flat and nothing is really paid; those fills are tagged `PAPER`.

## Troubleshooting

* **`ConfigError` at start-up** (the process refuses to boot and logs `CONFIG ERROR: …`):
  * `Unknown MODE=…` — use `FUTURES_TESTNET`, `FUTURES_DEMO` or `SPOT_TESTNET`.
  * `… looks like LIVE production. Refusing: set MODE=LIVE_OVERRIDE_I_UNDERSTAND only if you really mean it.`
    — a `REST_BASE_OVERRIDE` / `WS_BASE_OVERRIDE` points at a live host.
  * `DASHBOARD_PASSWORD is empty …` — set it, or `ALLOW_NO_AUTH=true` for a local loopback run.
  * `ALLOW_NO_AUTH=true is only allowed for a local loopback run …` — you are on Railway or bound
    to a public interface; set a password.
  * `DRY_RUN=false needs BINANCE_API_KEY and BINANCE_API_SECRET (testnet keys).`
  * `SYMBOLS …` — empty, duplicated or not a Binance symbol; `X must be true/false`, `X outside allowed range`.
* **Hedge-mode abort**: `/api/health` shows `boot_error: AccountModeError: dual-side (hedge) mode
  not supported - switch the account to one-way mode`. The lab only switches the account to one-way
  itself when no position is open; otherwise close the positions on the testnet UI (or switch the
  position mode there) and restart.
* **DESYNC badge** on a symbol: an exchange order was rejected or unconfirmed (or no reference price
  was available), so `exchange_net ≠ virtual_net`. Virtual fills are never rolled back; the router
  retries every 10 s. Look at the Orders tape for the venue's error text (margin insufficient,
  quantity below the minimum, precision, reduceOnly rejected …). Fix the cause (top up the testnet
  wallet, lower sizes) or flatten the strategy; the badge clears when a sync succeeds.
* **409 from the API** means a state precondition: `killed: use resume` / `killed: resume first`,
  `daily halt active: use resume` / `resume first`, `strategy halted: clear the halt first`,
  `venue unsupported for this strategy`, `close the strategy's position before changing allocation
  or leverage`, `close all positions before resetting`, `killed: resume (unkill) before resetting`,
  `close positions on removed symbols first`.
* **Engine `paused`** with `paused_reason` = `daily_halt`, `kill`, `manual` or `engine_disabled`:
  resume (with confirm) for the first two, `engine/start` for a manual stop, `ENGINE_ENABLED=true`
  for the last.
* **Empty charts on first boot**: run `scripts/seed_candles.py` (or `--synthetic` offline).

## Layout

```
app/config.py            settings, venues, live-host refusal, RedactFilter
app/core/                engine (boot / dispatch), portfolio, risk, positions (exits), signals, storage, feed
app/core/analytics.py    trade grouping, scoreboards, the daily rollup and the journal
app/exchange/            guard (host allowlist), client (venue-native calls), paper_router (net delta + backstop)
app/strategies/          base.py, registry.py, s01 … s20
app/dashboard/           the single-page GUI
scripts/seed_candles.py  candle backfill / synthetic seed
tests/                   pytest suite (asyncio_mode=auto)
```

## Video Bitcoin breakout (4h, 7-day entry / 3-day exit)

The strategy from the supplied Lewis Jackson video has its own Python engine,
checksum-verified spot backtest and persistent paper runner. Open **Bitcoin breakout**
in the lab header to start, pause, close or reset its paper wallet using the existing
dashboard password. The bot appears in the Competition roster and Bots view; its
controls are at `/?p=video&bot=BTC-4H-BREAKOUT#bots/bots`. The old standalone URL
redirects to that bot page. See
[docs/VIDEO_BREAKOUT.md](docs/VIDEO_BREAKOUT.md) for the sourced rules, explicit
assumptions and execution details. Run from this directory:

```powershell
python scripts/video_breakout.py backtest --start 2022-01-01 --end 2026-01-01
python scripts/video_breakout.py paper
```

Paper mode uses public prices and places no exchange orders. This long/cash
strategy runs separately because the lab registry's leveraged sizing, stops
and targets would change the video's rules.
