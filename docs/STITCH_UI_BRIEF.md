# PaperLab UI — Stitch design brief

Input for generating the PaperLab dashboard screens in Google Stitch. The designs are then ported into
`app/dashboard/` (vanilla JS + one stylesheet). Only the look and layout change. The data, routes and read-only rules
stay as they are, and none of this touches the frozen V6 trading code.

## Product

PaperLab is a paper-trading bot arena. The current program is **V6 FORWARD ARENA**: 56 bots (28 CONTROL bots and
their 28 "+JEV" twins, which an AI model can SKIP / TAKE / ATTACK) trade live Bybit USDT-perpetual market data with
simulated fills and no real money. Each bot trades one coin (ARB, ENA, XRP, DOGE) with 20 USDT. There are two
entry points with the same screens:

- **Public page**: read-only inspection, no login, no buttons that change anything.
- **Private dashboard**: the same screens plus operator controls under SYSTEM.

## Visual direction

- A dark trading terminal: charcoal background, panel cards, a thin accent line under the header. It should be dense
  but calm.
- All numbers are tabular monospace. Green is profit / LIVE / connected, red is loss / down / halt, amber is warning /
  stale, blue is accent / info, purple is Jev.
- Status is shown as pills (small caps, bordered). The one hero status badge is a pill with a pulsing dot:
  "● LIVE".
- Desktop first (1440 px), fully usable at 375 px: tables scroll sideways, KPI tiles stack two per row, the nav
  becomes a scrollable tab row.

## Global frame (every screen)

- Header: logo "PaperLab", "BOT ARENA" badge, "READ-ONLY INSPECTION" pill, and "live since HH:MM" on the right.
- Safety strip of pills: `PAPER ● SIMULATED`, `KILL ● OFF`, `ENGINE ● RUNNING`, `WS ● LIVE`, `DATA ● LIVE`,
  `JEV ● READY`.
- Main nav: ARENA · TRADERS · MARKET · VALIDATION · ACTIVITY · SYSTEM, with a sub-nav row when a section has views.

## Screens

1. **ARENA / V6 forward arena (home)**
   - Hero: the title "V6 FORWARD ARENA" and the `● LIVE` badge; the subtitle "LIVE Bybit market data · simulated
     fills · DRY_RUN — no real orders".
   - Three large readouts: FORWARD AGE ("1d 8h", with "since 2026-09-24 15:04 UTC · experiment v6x-… · session 3"),
     EVIDENCE (a maturity pill: TOO EARLY / COLLECTING / EARLY SIGNAL / MATURE SAMPLE, never "winner"), and NEXT 1H
     DECISION (a countdown "47:50", with "candle closes 16:00 UTC; orders fill ~2 min later").
   - Six KPI tiles: ACTIVE BOTS "56 / 56", POSITIONS OPEN, TRADES / 24H, TOTAL VIRTUAL EQUITY "1120.00 USDT",
     NET PNL (signed and colored), JEV EDGE (signed).
   - Health pills: WS, BYBIT, DATA (age in seconds), JEV, and HOUR hh:00 INPUTS COMPLETE.
   - Leaderboard table. Columns: #, bot (e.g. "V6.1-ENA-1H", with "+JEV" rows tinted), equity, return %, net, open
     position (side pill + unrealized), trades, 24h, exp R, PF, max DD, fees, funding, risk state pill, evidence pill.
     Filter chips: All / Controls / +JEV / Hourly / Swing 4h / Open positions. It shows the top 12 with "show all 56".
   - Open positions: bot, coin, side, entry, mark, unrealized, risk % (USDT), stop, target, hold time, tier.
   - Activity feed: time, an icon (◆ candidate, ◇ Jev, ▲ open, ■ close, ● system) and a sentence. Filter chips: All /
     Candidates / Jev / Trades / System. Examples:
     - "V6.1-ENA-1H candidate LONG @ 0.2231 · stop 1.8% → ORDERED"
     - "V6.1-ENA-1H+JEV Jev TAKE 78% support · 301 ms"
     - "LONG OPENED V6.1-ENA-1H @ 0.2232 · risk 1.00%"
     - "CLOSED V6.1-ENA-1H +1.4R (net +0.28 USDT)"
   - CONTROL vs +JEV pairs table: pair, control net, +JEV net, Δ, trades C/J, Jev decisions, SKIP/TAKE/ATTACK,
     selection α, ATTACK +, p50 latency, evidence.
   - Two side-by-side cards:
     - "Gross → net": gross → fees → slippage → funding → net for CONTROL and +JEV.
     - "Jev V6 live": decisions, actions, latency p50/p95/p99, move during latency, selection alpha, errors, API cost.
   - Two side-by-side cards: Families (family, bots, trades, net, evidence) and Risk distribution (trades by tier,
     min-notional skips, risk states).
   - A protocol footnote card with small print.
2. **Bot detail** (opens under the leaderboard, or as a right-hand drawer)
   - A header with the bot name and family.
   - Six tiles: equity, trades, expectancy R, drawdown, costs, risk state.
   - A restart-continuity note.
   - A closed-trades table: entry time, side, entry → exit, gross, fees, slippage, funding, net, R, exit, risk, and a
     "SHADOW" tag on counterfactual trades.
   - A gate / Jev decisions table.
3. **MARKET**: one table of live Bybit prices. Columns: coin, mid, half spread (bps), mark, index, funding %,
   next funding countdown, open interest, last 1m bar. Rows: ARB, ENA, XRP, DOGE, BTC, ETH.
4. **SYSTEM**: key/value cards for Execution & safety, Stream, Jev API, Forward & live data, Research jobs (table), and
   Database & environment, plus a collapsed "Developer details" section. The private dashboard adds operator
   controls here.
5. **VALIDATION / research history**: summary tiles (bots, best bot, profitable after costs, Jev helping, advancing,
   failing) and a table of the frozen programs V1–V5. Frozen programs are marked "stopped".

## States to design

- Hero status: LIVE, WARMING UP, DEGRADED · signals pause, PAUSED · DATA STALE, RESTARTING, NOT FROZEN · NOT TRADING
  (red), OFF.
- Empty states: "no open paper positions" and "nothing yet: hourly bots decide at each 1h close (+2 min)".
- Loading skeletons for tables and tiles.

## Hard constraints

- There is no trade, backtest or "run" button anywhere on the public screens.
- The design must not suggest real money. PAPER / SIMULATED stays visible at all times.
- There is never a "winner" or trophy for early results. Evidence maturity is always shown next to PnL.
