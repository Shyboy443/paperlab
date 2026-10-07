# V5 runtime audit — what keeps running, what stops, and why (2026-09-24)

The runtime should spend its CPU, API calls and attention on the current experiment (V5). Two old workers were running
only because they existed. Nothing below deletes a record: every fill, trade, session, report and reproducibility path
stays in the database and in `/public/inspection.json` / the static report.

## Stopped

| worker | what it was | state at the stop (2026-09-24 11:32 UTC) | why it can stop | how |
|---|---|---|---|---|
| V1 paper bake-off (27 books) | the frozen V1 strategies S01-S27 on SOL/ETH, one isolated 10 USDT paper book each, respawned when a book blows its floor | current lives net -26.51 USDT over 336 trades; -134.61 USDT across all 69 lives (42 respawns); 5 books positive on tiny samples; 7 open paper positions | V1 failed its seasons and its 2021-2026 multi-year validation; no V1 strategy is a candidate; the books are not a control for anything current; respawning losing books produces no new evidence | `BAKEOFF_ENABLED=false`: books boot disarmed, never respawn, open PAPER positions closed on boot (dry run only) |
| Forward shadow (V2 field, 36 bots) | live-data paper books of the V2 TEST field since 2026-09-23 | 36 bots, 9 closed trades, net -0.89 USDT in 22 h | all 3 V2 TEST survivors failed multi-year validation (0 of 3); nothing in V2 awaits forward evidence | `LIVE_SHADOW_ENABLED=false` (its sessions, bots and trades stay stored) |

## Verified before stopping

* **Not safety-critical.** The kill switch, RiskManager, market feed, router (dry run) and venue checks belong to the
  engine, not to the books; the engine keeps running (`ENGINE ● running`, `DATA ● LIVE` from its own feed).
* **Not a control.** V5 compares every bot with its own CONTROL / ALWAYS-TAKE / RANDOM twins; no V1 book or V2 shadow
  bot is a baseline for V5.
* **Not required forward evidence.** No V1 or V2 bot is ADVANCED or QUALIFIED; nothing awaits their forward results.

## Kept running

The engine (paper execution against live Bybit data, DRY_RUN=true, 0 real orders ever), the realtime stream (WS + SSE),
the Jev API health checks, the public inspection endpoint and the static report.

## Reversible

`BAKEOFF_ENABLED=true` + `/api/strategies/arm-all` (operator) restores the bake-off; `LIVE_SHADOW_ENABLED=true` restores
the forward shadow. V5 forward validation will run its own shadow session only for V5 bots that earn it.
