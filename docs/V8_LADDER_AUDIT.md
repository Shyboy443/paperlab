# V8.3 +LADDER audit, 2026-10-12 (revision 2)

This audit answers an operator review of "Thresh Ladder" (V8.3 ARB +LADDER) and the other V8.3 bots. It is
research only. The frozen V8 experiment `v8x-972151dbad84` (live since 2026-10-05 04:33 UTC) was not changed.

- **Frozen configuration:** `docs/V8_FREEZE.json`, plus the frozen code in `app/strategies/v8/` and
  `app/competition/v8_config.py`. Both have been unchanged since commit `55f60ac`.
- **Revision 2** answers the operator's follow-up review (§§1–2, 5–6 and the new §7).

Tools:
- `scripts/v8_live_dump.sh` takes a read-only snapshot of `/data/v8-forward.db`.
- `scripts/v8_live_parity.py` runs the paper-vs-replay comparison.
- `scripts/v8_ladder_audit_study.py` runs the pre-registered variant study; results are in
  `docs/V8_LADDER_AUDIT_STUDY.json`.

## 1. Paper trading and the backtest engine agree (at 1-minute resolution)

Every bot was rebuilt exactly as the live worker builds it:
- the same engine, `LevelMakerEngineV8`, with 60 s fills, maker targets and taker stops;
- the frozen rules and `SizingV6`;
- `ladder_class` for +LADDER.

They were then replayed over the experiment's own recorded 1m bars, live half-spreads and charged funding from T0.
This covered the 6 coins, LADDER and CONTROL, Oct 5 → Oct 12.

| | Count |
|---|---|
| Live trades (real, not shadow) | 483 |
| … with a replay trade at the same entry time and side | 483 (100%) |
| … identical (exit time, exit type, prices, R within 0.01) | 407 |
| … same exit type and time; fill price differs by ≤ 0.01R (live spread snapshot, lot rounding); 2 exits 1 s apart | 76 |
| Replay-only entries | 0 explained by anything other than the live gate's `MISSED_DOWNTIME` skips |

The engine walks every 1m bar adverse-first, so a bar touching both a stop and a target fills the stop.

Two 1-minute checks:
- Of the 32,785 one-minute bars inside the 244 live ladder trades, **0** touched both the next target and the stop;
  that would need a 1.65–2.1% range inside a single minute.
- Of the 54 ladder trades that filled a rung and later stopped out, **0** had the rung fill and the stop in the same
  minute.

**What this does NOT prove.** It shows agreement at 1-minute resolution and removes the stop-versus-target ambiguity
for these trades. It does **not** establish tick-level equivalence:
- whether a maker target would really have filled (queue position, trade-through);
- how the spread evolved inside a minute;
- the exact order of a target fill and a newly moved stop within one minute.

Paper and replay share the same execution model, so they agree with each other. Neither is a measurement of real
exchange fills. Finer data could change simulated execution, and only live fills or tick-level replays can settle it.

## 2. The October 8 "cluster" is an outage plus shadow rows

- **Outage:** the V8, V11, V13 and V14 workers all went silent at **07:54:51 UTC on Oct 8**. V6, V7, V9, V12, V16
  and V17 kept running, so the app itself was alive. No error was logged; the sessions still said LIVE. They resumed
  at **21:32**, 13.6 h later.
- **Shadow trades:** the gate recorded every missed signal as `MISSED_DOWNTIME`; for ARB, 17 signals between 09:31
  and 19:31. Each one became a **counterfactual** shadow trade with no tier.
- **Missing metadata:** all 17 rows without risk or tier metadata are those counterfactual rows.
- **"5 open at once":** this appears only when shadow rows are mixed in. The real bot never held more than one
  position (`max_positions = 1` per bot; the gate re-checks at fill).

**The position that was open when the worker stopped.** A real (non-counterfactual) short opened at **07:46** and was
still open when the worker went silent at 07:54. Its stop was hit at **09:26:16**, during the outage. At the restart
the worker:
- rebuilt the book from the backfilled 1m bars and the recorded decisions;
- booked that stop exit, −0.2208 USDT (−1.096R), flagged `rederived`;
- passed its continuity checks before taking new entries (18/18 bots).

An independent replay of the same entry gives the identical exit, time and P&L. It is a real position with a
reconstructed exit, so it belongs in the account report.

| ARB +LADDER, Oct 5–12 | Trades | Net (USDT) |
|---|---|---|
| All exported rows (incl. 17 counterfactual) | 70 | +0.60 |
| Real trades incl. the reconstructed pre-outage exit | 53 | +1.83 |
| Real trades with live-recorded exits only | 52 | +2.05 |

## 3. Stops and break-even accounting

- **Real stop exits:** 111, from **−1.084R to −1.149R**, median −1.095R. The mean of −1.097R breaks down as:
  - −1R for the stop itself;
  - **−0.095R in fees**: a taker fee on the entry and on the stop, about 0.11% of notional against a ~1.2% stop;
  - −0.003R of slippage past the trigger.
- **The −1.38R to −1.43R stops** in the review, and the worst at −3.80R, are all counterfactual shadow rows.
- **R definition:** R is measured from the fill. The stop is 1.2% from the signal close, and the order fills 60 s
  later, so the stop's distance from the fill ranges from 0.78% to 1.36% (mean 1.175%).
- **The ATR buffer never moves the stop.** V8.3's minimum and maximum stop are both 1.2%. The structural stop plus the
  0.2 ATR buffer only decides whether a setup is skipped, when it would need more than 1.2%.
- **"be" exits are not flat trades.** The engine labels any stop after a stop move as `be`, and the trade row's net
  includes the partial profits already banked.

Live LADDER trades by exit type and rungs filled:

| Exit | Rungs filled | Trades | Avg R | Net (USDT) |
|---|---|---|---|---|
| be | 2 | 19 | +0.95 | +3.34 |
| be | 1 | 29 | +0.25 | +1.32 |
| be | 0 | 9 | −0.05 | −0.08 |

The 9 trades with no rung filled had the break-even move trigger on fill-based R before the first target, which is
priced from the signal, was reached.

- **Ladder shape:** **thirds (0.75R / 1.5R / 2.5R)**, not 25/50/25. On these 20 USDT books, 11 live trades could not
  be split and closed whole at the first rung.
- **Full-target exits:** only 3 live trades reached TP3, average +1.36R. Historically 2.9% of trades (W1) and 3.7%
  (W2) reach TP3.

## 4. Time exit

The live 3-hour exits that had banked at least one rung earned +4.47 USDT (30 trades); the 96 with no rung averaged
−0.10R. The 3 h hold came from a pre-registered study (`docs/V8_SNAP_HOLD_STUDY.json`), and this audit found no reason
to shorten it.

## 5. Pre-registered variant study (rule and variants fixed before running)

Windows:
- **W1:** 2026-06-26 → 10-02, 98 days, split into halves.
- **W2:** 2025-01-01 → 2026-06-25, 18 months, independent.

Both use 6 coins and the live engine, with each coin book restarting at 20 USDT every UTC day. The registered rule is
reported as **"relative improvement"** (it beats E0 on R per trade, total R and max DD in R, in both W1 halves and in
W2). **It never means profitable: every variant loses in every window.**

### 5a. Complete-strategy comparison

Each variant here is its own full strategy, with its own trades: filters and cooldowns remove entries, and different
exits change which later signals are free to fill. Trade counts therefore differ. This answers "which complete
strategy loses least".

| Variant | R/trade W1 h1 | W1 h2 | W2 | Trades W1 / W2 | Max DD (R) W1 / W2 | Registered rule |
|---|---|---|---|---|---|---|
| **E0 live ladder (thirds)** | −0.089 | −0.120 | −0.129 | 3,827 / 22,022 | 412 / 2,850 | baseline |
| E1 single 1.5R target (= CONTROL) | −0.092 | −0.102 | −0.127 | 3,652 / 21,277 | 383 / 2,704 | no relative improvement |
| E2 two-stage (½ at 1R, ½ at 2R) | −0.084 | −0.110 | −0.129 | 3,664 / 21,415 | 372 / 2,775 | no relative improvement |
| F1 60-min cooldown after a stop | −0.080 | −0.120 | −0.128 | 3,413 / 19,650 | 355 / 2,528 | relative improvement (still loses) |
| F2 flat VWAP slope (\|ΔVWAP\|/ATR ≤ 0.5) | −0.062 | −0.087 | −0.113 | 1,723 / 9,355 | 139 / 1,061 | relative improvement (still loses) |
| F3 1h trend flat | −0.084 | −0.091 | −0.113 | 955 / 5,306 | 92 / 607 | relative improvement (still loses) |
| P2 one book, max 2 open trades | −0.099 | −0.142 | −0.134 | 1,874 / 11,472 | 235 / 1,542 | no relative improvement |

- **F2 and F3** lose less mainly by trading 55–75% less.
- **F1 and F3** were worse than E0 in W2's first half.

### 5b. Matched-entry exit comparison

This uses only the entries that E0, E1 and E2 **all** took (same coin, time and side), so only the exit rule differs.
It answers "do the exits matter".

| Exit rule on the same entries | W1 (3,194 entries) R/trade | DD (R) | W2 (17,787 entries) R/trade | DD (R) |
|---|---|---|---|---|
| E0 ladder (thirds) | −0.106 | 346 | −0.135 | 2,393 |
| E1 single 1.5R | −0.098 | 334 | −0.134 | 2,381 |
| E2 two-stage | −0.099 | 323 | −0.135 | 2,408 |

The exits barely matter, and the ladder is never better. The losses come from the entries.

### 5c. Account drawdown at every 1-minute equity update (USDT and %)

The account equity is the wallet plus open positions marked at every 1m close, summed over the six 20 USDT coin books,
with each UTC day re-funded and the day's P&L banked. The running peak and the maximum drawdown are taken at **every**
minute update; the hourly thinning only shapes what is kept for charts. Percentages are of the 120 USDT starting
capital. Because every day is re-funded, they can pass 100%: the account would have been wiped out.

| Variant | W1 max DD | W1 end (from 120) | W2 max DD | W2 end (from 120) |
|---|---|---|---|---|
| E0 live ladder | 72.67 USDT (60.6%) | 49.30 | 535.55 USDT (446.3%) | −414.58 |
| E1 single 1.5R | 67.10 (55.9%) | 58.03 | 492.00 (410.0%) | −369.24 |
| E2 two-stage | 65.00 (54.2%) | 58.30 | 500.13 (416.8%) | −378.94 |
| F1 cooldown | 62.49 (52.1%) | 60.01 | 447.52 (372.9%) | −325.79 |
| F2 flat VWAP | 25.41 (21.2%) | 96.40 | 188.41 (157.0%) | −68.01 |
| F3 1h trend flat | 16.12 (13.4%) | 105.36 | 109.35 (91.1%) | 12.82 |

**Long vs short.** One week of short-side profit is not evidence. The 2026-10-04 study found that the side pattern
reversed on an independent window.

## 6. Conclusion

- **Paper and replay agree** at 1-minute resolution (§1, with its limits).
- **The live week's profit does not outweigh the history.** ARB +LADDER made +1.83 USDT over 53 trades (+2.05 over the
  52 with live exits). The same rule lost −0.106R per trade over 98 days and −0.129R over 18 months.
- **None of these tweaks is profitable,** and none was deployed. This rejects these particular changes. It does
  **not** show that every VWAP-reversion design is unprofitable.
- **Recommendation:** keep V8.3 as the research baseline. Any follow-up, for example entries gated on expected
  movement after costs, should be a separately named, pre-registered experiment, not an edit to the frozen V8.

## 7. Heartbeat monitoring (deployed 2026-10-12, tested on production)

**Why nothing caught the outage.** The forward-worker supervisor (`app/live/v6_service.py`) kept the last status a
worker sent and restarted a worker only when its process EXITED. A hung worker kept showing its last "LIVE" report,
with data 3 s old, indefinitely. On top of that, the "desk decisions only" Telegram scope (since 2026-10-11) mutes
ordinary alerts.

Changes (none in a frozen file; all freeze tests pass):
- **The supervisor** records every heartbeat, reports `heartbeat_age_s`, and terminates and restarts a worker that has
  been silent for 300 s (1,200 s while warming up).
- **The restart path** resumes the experiment, re-derives every book from stored bars and recorded decisions, and
  requires the continuity checks to pass before new entries.
- **The watchdog** treats more than 60 s without a heartbeat as DOWN, confirmed on two checks 30 s apart. It sends
  "🔴 DOWN", "🟢 recovered" and "♻️ restarted" through a new Telegram `ops_alert` that every scope delivers.
- **The external endpoint** `/public/health/deep` (200 when healthy, 503 when not) now carries the heartbeat ages,
  for an independent uptime monitor.

Deliberate test on the V16 paper worker (`SIGSTOP` on its process, on production):

| Phase | Result |
|---|---|
| A: frozen for 150 s, then resumed | Flagged "worker silent for 105 s" at 111 s; deep health returned 503; after resuming, healthy again within 48 s; 0 Telegram send errors |
| B: frozen past the limit | At 303 s the supervisor marked RESTARTING and killed the hung process; a new worker was LIVE at 354 s, resumed the same experiment, and passed 2/2 continuity checks before trading |

**Still to set up (operator):** an uptime monitor outside Railway (for example UptimeRobot) polling
`https://paperlab-production-919c.up.railway.app/public/health/deep` every 5 minutes. It covers the case the in-app
watchdog cannot see: the whole app going down.
