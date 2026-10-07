# Feature audit — KEEP / HIDE / REMOVE (2026-09-24)

Goal: PaperLab should be simpler, faster and easier to understand (for a person and for ChatGPT) without losing
any safety signal or any stored experiment. Nothing historical was deleted; no gate, fee or result changed.

## KEEP (visible, unchanged in behaviour)

risk manager · kill switch · broker abstraction · execution simulator (Bybit / Binance fees, spread + impact
slippage, funding, paper liquidation, exchange filters) · competition engine · bot analyzer · historical validation ·
Monte Carlo · stress testing · live forward shadow · Jev experimentation · WebSocket realtime with SSE fallback ·
public inspection · reproducibility and experiment fingerprints.

**Safety stays in front of every viewer.** Every page header now carries a safety strip: `PAPER ● SIMULATED` (or
`LIVE ● REAL ORDERS` in red), `KILL ● off/ENGAGED`, `ENGINE ● running/HALT` (risk halt), `WS ● LIVE`,
`DATA ● LIVE/STALE/DOWN` (exchange / market-data disconnect) and `JEV ● OK` (Jev outage). The public health push
now carries these facts (the same ones `/public/inspection.json` already published). The private dashboard keeps
its dedicated mode badge, dry-run pill, feed light, DESYNC badge, engine chip and KILL ALL button, plus the
WS / DATA / JEV part of the strip.

## HIDE (moved to SYSTEM → Developer details)

raw fingerprints (strategy, Jev, universe, config, dataset) · internal run ids · schema and API versions · asset
version · raw event-bus counters · data hashes · verbose build diagnostics (formerly a footer on every public page).
They are one click away in SYSTEM, never removed, and still in `/public/inspection.json`.

## MERGED / MOVED

| before | after |
|---|---|
| 7 top-level areas (ARENA, TRADERS, MARKET, VALIDATION, ACTIVITY, ABOUT; SYSTEM hidden behind a button) | 6: **ARENA / TRADERS / MARKET / VALIDATION / ACTIVITY / SYSTEM** (About folded into SYSTEM) |
| ARENA opened on the V3.1 arena; five sub-screens incl. V3, V1 discovery, V1 control-vs-Jev | ARENA = **Current arena** (new front door), **V4 specialists** (new), Live shadow |
| V3.1 arena, V3 (frozen), V2 candidates, V1/V2 discovery arena, V1 control vs Jev, V1 multi-year, V1 seasons / season leaderboard / season bots / season list, execution comparison — spread over ARENA, TRADERS and VALIDATION | all under **VALIDATION → research history**; every old URL / hash still resolves there |
| TRADERS = V1 season leaderboard + season bot detail (paper bake-off and books privately) | **All traders**: one table across every program from the unified results index; a trader opens with **Overview / Trades / Analyzer / Jev / Validation** (`/public/competition/trader/<bot_id>`); bake-off and books stay in the private TRADERS |
| footer diagnostics on every public page | SYSTEM (execution & safety, stream, Jev API, forward shadow & live data, research jobs, database & environment, developer details) |

The Current arena answers in five seconds: how many bots (running / evaluated), who is best (and that it is NOT
qualified, with the reason), is anyone profitable (after costs, with >= 30 trades), is Jev helping (judged only
against the matched random action), is anyone advancing (qualified, advanced set, stage passes), what is failing
(top root causes) — plus the research programs, top 10 bots, top 10 Jev bots and the forward shadow.

## REMOVED

* the footer diagnostics block and its **two REST fetches on every stream reconnect** (now fetched only while
  SYSTEM is open);
* the public `STREAM ● status` pill (replaced by the WS pill of the safety strip).

**Dead-code audit (nothing else qualified):** 187 top-level frontend definitions scanned — none is unreferenced.
Every API route has a caller (frontend, test or script); the four with no frontend caller (`/api/equity`,
`/api/candles`, `/api/tape`, `/api/analysis`) are documented operator endpoints in the README, so they stay. No code
needed a `legacy/` or `compatibility/` move: every historical runner, frozen strategy set and view is still reached
by its frozen-run guard, its research-history screen or the results index.

## Performance

* one snapshot (`/public/inspection.json`: server cache 30 s, client cache 25 s, ~41-60 KB) feeds the Current
  arena, All traders and the safety strip instead of each view fetching its own;
* the Current arena refreshes every 60 s and only while it is visible; no view polls while hidden; realtime state
  stays push-only (WebSocket, SSE fallback);
* trader trades load on demand (newest 300, allow-listed fields);
* assets: +31 KB (`home.js`) for the new front door; 464 KB total, no new dependency.
