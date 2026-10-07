# Higher-timeframe logic audit

Completed 2026-10-05. V14 only; local paper implementation.

Correctness defects are repaired. The historical replay does **not** show a consistent performance improvement across this bot set. The revised aggregate result is worse than the archived filter.

## Findings and changes

- Hourly EMA(84/240) proxies were labelled 4h/daily. The revised filter builds exact UTC OHLC candles from 4/24 completed hours and evaluates EMA21/10 on their actual closes.
- Entry prices determined both HTF trends, causing pullbacks to change the bigger-picture classification. Only each HTF's completed close now determines that timeframe's state.
- A candle count alone admitted stale or discontinuous history. Explicit decision cutoffs exclude future and forming candles. Invalid, duplicate, unordered, missing or stale history blocks new entries.
- One generic bias rule treated all setups alike. Breakouts require 4h agreement; pullbacks allow a neutral 4h inside an agreeing daily trend. Reversion requires at least one agreeing HTF and no opposing HTF. ATR-scaled deadbands reduce tiny trend flips.
- Queued orders relied on an earlier filter decision. Dedicated V14 engines recheck the regime before market/limit execution. A stale feed or regime veto cancels an entry. Signal and fill contexts are stored in fill metadata; original exits and risk checks still operate.
- Warm-up is 25 days; the existing 600-hour buffer supports 20 complete daily bars. V14 status now includes entry/fill rejection counts and the latest HTF context. Views are cached per coin/hour.

[Rule specification](V14_PROTOCOL.md) and [Bybit's closed-kline semantics](https://bybit-exchange.github.io/docs/v5/market/kline).

## Controlled replay

27 runs: the nine bots in BASE (copy alone), OLD (archived HTF proxy), and NEW (revised HTF plus fill checks). Shared execution engines, 20 USDT daily-rebased books, sizing, fees, recorded funding, 60.001-second latency and 25-day warm-up. Reset/final closes and their costs are included. Net R includes position-attributed funding. These sums are replay P/L across daily resets, not returns on a compounded live account.

| Family | BASE net USDT | OLD net USDT | NEW net USDT | OLD / NEW trades | OLD / NEW mean net R |
|---|---:|---:|---:|---:|---:|
| V14.1 VWAP (six coins) | -50.55 | -27.10 | -30.58 | 1299 / 1410 | -0.1124 / -0.1136 |
| V14.2 RS breakout | -3.27 | -4.27 | -3.64 | 410 / 299 | -0.0621 / -0.0733 |
| V14.3 RS pullback | -4.28 | -2.41 | -1.92 | 675 / 606 | -0.0218 / -0.0202 |
| V14.4 Limit snapback | -6.71 | +0.46 | +0.88 | 38 / 146 | +0.0775 / +0.0404 |

| Family | OLD halves (mean net R) | NEW halves (mean net R) | Consistent improvement? |
|---|---:|---:|---|
| V14.1 | -0.1076 / -0.1173 | -0.1308 / -0.0982 | No |
| V14.2 | -0.0361 / -0.1117 | -0.0504 / -0.1225 | No |
| V14.3 | -0.0570 / +0.0185 | -0.0623 / +0.0291 | No |
| V14.4 | -0.0207 / +0.1756 | -0.0659 / +0.1804 | No |

OLD aggregate net: -33.3216 USDT; 2422 closed trades.

NEW aggregate net: -35.2563 USDT; 2461 closed trades.

Measurement: 2026-07-22 through 2026-10-02 UTC (end exclusive), 72 days.

Pullback improves overall net and mean R, but its first half worsens. Breakout loses less mostly because it trades less; its mean R worsens in both halves. Snapback's total profit grows with a larger sample, but mean R declines and its first half loses money. VWAP worsens overall. No family meets the criterion of improved mean net R in both halves. The copied VWAP/breakout strategies are also negative without either filter; these HTF repairs do not establish an edge in the underlying setups.

The first prototype permitted both-neutral reversion entries. Its VWAP discovery-half result worsened, so that behavior was removed. This was an exploratory revision, not an untouched pre-registration. The halves use previously studied data; neither is an unseen holdout. No EMA/ATR parameter grid was optimized. Forward evidence is still required.

No NEW runs encountered missing/stale HTF history or a fill-time regime rejection in this complete cached dataset. Gap/rollover/order-cancellation behavior is covered by targeted regression tests; the measured P/L differences here come from the signal filter.

Raw trades, funding coverage, source and dataset SHA256 hashes: `V14_HTF_REVISION_STUDY.json`. Original V1 sources, freeze and study: `../snapshots/2026-10-05-htf-audit/`. BASE/OLD arms were reused from the initial comparison with unchanged common execution inputs; all nine NEW arms were rerun after the final production rule and engines were complete.

## Validation and deployment

Full suite: 1,466 passed, 4 skipped (`data/htf-revision-tests.log`). Latest focused tests: 46 passed, including one additional snapback ranking regression. Coverage includes UTC OHLC construction, hour/day boundaries, future/forming bars, stale/malformed/gapped history, true HTF EMA inputs, symmetric policies, preserved exits, pre-ranking filters, fill metadata, queued-order cancellation and retained risk checks.

All eight arena freezes verify. Only V14's protocol/identity changes for this HTF task. V2 verdicts do not inherit V1's positive snapback label. Revised bots remain paper-only and are not qualified by this replay. No running service or remote deployment was restarted.

V14 manifest `4e7678149e0d3d7e`; experiment `v14x-d9a3940c4814`.
