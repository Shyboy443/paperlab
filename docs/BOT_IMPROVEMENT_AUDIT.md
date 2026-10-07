# Bot audit and execution corrections

Completed 2026-10-05 (Asia/Colombo). Scope: the local PaperLab project, shared replay/forward-paper engine, V6–V14 manifests, cached paper trades and saved strategy studies. No running remote service or real-money account was changed.

## Findings

The performance problem has two parts: weak strategy expectancy and avoidable execution/risk defects. Fixing the defects reduced losses in the comparison below, but did not establish a profitable strategy.

The cached research database contains 3,156 paper trades through 2026-10-02. Its V8.3 CONTROL trades total **−3.20 USDT gross, 19.32 in fees and −27.51 net** over 575 trades. The V8.1/V8.2 controls also lose before fees. These are historical cached records across experiments, not current bot balances. Full attribution, timestamps and the reproducible SQL query are in [PAPER_TRADE_AUDIT.json](PAPER_TRADE_AUDIT.json).

The saved studies also contradict some tempting changes:

- V13's selected limit-entry strategy earned +0.0215 R per trade in development but **−0.0886 R on 421 test trades**. Its saved verdict remains FAIL.
- V14's higher-timeframe rule worsened the V8.3 copy and breakout scanner. The pullback scanner showed no overall improvement. The limit-entry snapback copy was positive on just **36 trades**; that is insufficient evidence to promote it.
- The V11 pullback study had a 65.5% win rate but −0.0399 R per trade. A high win rate alone does not cover larger losses and trading costs.

Sources: [V13 study](V13_SNAPBACK_STUDY.json), [V14 study](V14_HTF_STUDY.json). The existing standard Bybit fee schedule is consistent with its published non-VIP futures rates, 0.055% taker and 0.020% maker; no fee discount was invented to improve results. See [Bybit's fee schedule](https://www.bybit.com/en/help-center/article/Trading-Fee-Structure).

## Implemented corrections

| Defect | Corrected behavior |
|---|---|
| Risk approval happened at the signal, then delayed fills reused that approval. Multiple queued signals could exceed position slots or consume the same margin. | Market and resting-limit entries recheck slots, same-symbol positions, available margin including entry fees, gross/net exposure, minimum order size and hard risk at fill time. Orders can shrink or be rejected; never enlarged. |
| Price drift increased stop risk or invalidated the setup after approval. | Preserve the approved dollar-risk budget at the achievable fill price. Reject fills beyond the stop/first target, insufficient reward/risk, overly tight stops and fee-dominated stops. |
| Orders could execute after a prolonged feed gap. | Market orders expire after their permitted signal window. Resting limits retain their explicit expiry. |
| Daily loss limits were configured but never checked by the forward/replay engine. | Check mark-to-market loss at bar opens, each walked price, after management and before new entries. A breach flattens the book and cancels queued entries; entry trading resumes on the next UTC day without rebasing capital. The permanent strategy floor also flattens/cancels. |
| Entry execution could read ATR containing the executing candle's future prices. | Process pending orders before adding the executing candle to indicator history. |
| Multi-coin resets, final closes and explicit exits could construct an altcoin's market state from the last BTC bar. | Use the position's own symbol's current/last bar. Final equity includes the final exit's costs. |
| Halted books could still show as qualified, and daily halts were invisible in status. | Report DAILY_HALT, include it in watchdog state monitoring, and exclude it from V8–V14 qualification. |
| Rejected-candidate shadows would keep pre-fix fill sizes while controls used corrected sizes. | Apply the same price, risk and legal-size checks to counterfactual fills and record shadow rejections separately. Counterfactual trades remain independent candidate diagnostics, not a tradable alternative portfolio. |

Primary code: `app/backtest/replay.py`, `app/live/scan_engine.py`, `app/live/v13_engine.py`, `app/live/v6_runner.py` and the program qualification configurations. Existing live-mirror exchange order logic was not changed.

## Measured comparison

`scripts/execution_guard_study.py` compares the preserved pre-change engine with the corrected engine on the same cached V8.3 bars and parameters, six coins (ETH, SOL, XRP, DOGE, ARB, ENA), standard fees, 60-second decision latency and existing maker targets. There are five warm-up days followed by 55 measurement days. Daily book resets match the previous research protocol. No parameter search was performed. Daily/strategy loss halts are disabled in this comparison to isolate execution corrections; their behavior is separately tested.

| Metric | Before | After |
|---|---:|---:|
| Closed trades, excluding reset/final forced exits | 2,285 | 2,155 |
| Net PnL, USDT | −54.4902 | −46.5593 |
| Fees, USDT | 38.6864 | 34.6667 |
| Profit factor | 0.7325 | 0.7471 |
| Mean net R per trade | −0.113019 | −0.113611 |
| First-half net PnL | −33.3917 | −27.4279 |
| Second-half net PnL | −21.0984 | −19.1314 |

Total losses fell **14.6%**, fees **10.4%** and trades **5.7%**. Net losses decreased in both halves. Average R per trade worsened slightly overall and in the second half: this is an improvement in exposure/execution discipline, not evidence that the entry signal gained an edge. The remaining strategy still loses money.

Full coin-level results and rejection counts: [EXECUTION_GUARD_STUDY.json](EXECUTION_GUARD_STUDY.json). The data has been used in earlier studies; the halves are reporting splits, not fresh independent holdouts. Maker fills still use an OHLCV trade-through approximation, not a real queue simulation. Funding is not supplied in this particular execution-only comparison; it is not a complete deployment return estimate. Six daily-reset paper books cannot be interpreted as a compounded investment return.

## Experiment integrity and rollout

Original engine sources, configuration sources and freeze manifests were preserved in `snapshots/2026-10-04-execution-audit/`. All eight V6–V14 forward manifests were refreshed from the changed code, retaining instrument rules, universe, parameters and prior study verdicts. Each manifest verifies. On a later deployment the changed identities start new paper experiments; old records must remain separate from the corrected run. The instrument filters were retained from the existing manifests, not refreshed from an exchange.

The changes are local and require deployment/restart to affect a remote dashboard. Do not interpret the comparison as permission to promote any bot to real capital. The next strategy improvement needs positive performance on new data after all costs, enough trades, and stability across periods/coins; these findings do not justify increased leverage or a blanket trend filter.

## Verification

**Final full suite: 1,431 passed, 4 skipped** (212.26 seconds). All eight forward manifests verify.

The 25 new regression cases include simultaneous delayed entries, long/short gap rejection, stop-risk preservation, intrabar daily losses followed by recovery, UTC rollover, cancellation of resting limits on halt, margin exhaustion, own-symbol final closes, entry ATR look-ahead, stale orders, fee-dominated stops and halted-bot qualification. Existing Jev tests verify shadow/control fill parity.

```powershell
.venv/Scripts/python.exe -m pytest -q --disable-warnings
.venv/Scripts/python.exe scripts/execution_guard_study.py --workers 3
```

The initial environment lacked declared `tzdata`/Pillow dependencies; these were installed into the local virtual environment. DuckDB and NumPy were installed there for the research comparison. A pre-existing test selected a retired V8.1 bot by list index; it now selects the active ETH control by identity. Bizzy's sizing assertion now checks its approved risk after the delayed gap, rather than expecting an unsafe unchanged full-size fill.
