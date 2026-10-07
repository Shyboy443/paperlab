# Fast scalp feasibility check — 2026-09-27

The user requested more frequent, aggressive paper scalping with the existing
2% maximum planned risk per bot per trade. The live V6 (1h/4h signals) and V7
(15m signals) experiments are frozen and were not changed.

An isolated research prototype tested a 3m range-break continuation with a
30-minute maximum hold and a 5m trend pullback with a 45-minute maximum hold,
on each of ARB, ENA, XRP and DOGE. Each simulated book began with 20 USDT.
The replay reused the exchange rules, 1% base / 2% maximum risk sizing,
Bybit taker fees, funding, slippage model and risk guards. Entries used a
one-minute decision-to-fill window; this is still a 1m-bar execution proxy,
not a tick-accurate scalp fill.

The fixed September 18–25 Bybit replay generated **576 closed trades** across
eight bots, about 82 per day. **Every bot lost money.** Combined net was
**−16.2542 USDT on 160 USDT** of paper starting capital (−10.16%), with
14.8454 USDT in modeled trading fees. Individual trade counts ranged from
41 to 99 and per-bot net ranged from −0.8860 to −2.9320 USDT. The machine
readable breakdown is in `V8_REPLAY_CHECK.json`; reproduce it with
`python scripts/v8_replay_check.py` from the project root.

This was a retrospective feasibility check on one week, not an independent
holdout. Historical bid/ask timestamps and REST arrival times are unavailable,
and the live spread gate was not reconstructed. Its performance cannot establish
future returns. The result is sufficient to reject **this specific prototype**:
it would trade much more often while losing materially after costs. It was not
connected to the Railway service or enabled as a live paper experiment.

Prior V3/V3.1 research also found negative gross expectancy for the tested
1m/3m/5m chart-pattern entries (`V31_PROTOCOL.md`). A distinct scalping
hypothesis needs a new source of information, such as timestamped book and
trade-flow data, plus a fill model that includes spread, latency and missed
maker orders. That data must be collected forward before a credible test can
determine whether a fast strategy has an after-cost edge.
