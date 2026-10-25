# Divergence Desk

Separate PaperLab desk at `/public/divergence`; read-only API `/api/public/divergence`.
The original AI Trading Desk and autonomous research service keep their own books.

## Operation

Runs continuously on Railway in a background scanner. No account keys, order
routes, live switch or exchange order methods exist in the new desk. Each
completed 15-minute interval is scanned across BTC, ETH, SOL, BNB, XRP, DOGE,
ADA, AVAX, LINK and SUI USDT perpetuals. Markets must pass the existing public
liquidity/spread screen. Actual Binance minute candles and quotes reconcile
paper positions between scans; funding is fetched before reconciliation.

The precise signal (DIV-SR-9: regular and hidden RSI divergences on closing-price swings, major 4h support/resistance, swing-gap, trend and no-chase rules) is frozen in `app/divergence/model.py` and explained in
`docs/DIVERGENCE_RULES.md`. A 4h divergence waits for 1h structure confirmation;
a 1h divergence waits for 15m confirmation. Confirmed 2/2 pivots cause a genuine
delay: a 4h pivot is only known after two further 4h candles close. The chart
pivot date is not the date at which a trade could have been placed.

## Two prospective books

Both start at $1,000 of simulated capital, risk at most 0.5% per planned stop
including execution costs, allow up to three simultaneous positions and cap
gross notional at 1x equity. The baseline follows qualifying mechanical signals.
The council book requires all four valid model votes: Structure advocate, Risk
reviewer, News analyst and Skeptic. The first three inspect evidence independently;
the Skeptic challenges their arguments. Any veto rejects. Missing/invalid model
output leaves the council book out of the trade. Unknown news is disclosed.

The council shares the existing desk's provider, keys and persisted spending
limit. It uses its fast model with no tools or continuation calls, at most four
calls per setup and twelve setup reviews per UTC day. Existing desk budgets and
model settings are not increased. The baseline continues if AI is unavailable.
Each new signal and decision is persisted once; interrupted reviews are marked
unavailable after restart rather than secretly replayed or approved.

Entry is a fresh observed quote after signal/review, with 0.02% adverse slippage,
not a backdated next-open fill. Reject entries that drift more than 0.25 of the
reference stop distance or arrive over 15 minutes after confirmation. Target is
2x actual entry-to-stop distance; realized net R is lower after costs. Taker
commission is 0.04% each side. Partial entry-minute stop ambiguity is counted
conservatively, with no unobserved entry-minute target credits. Missing minute
history defers reconciliation and is exposed as degraded data. A 20% equity
drawdown suspends new positions; hard stops remain active.

SQLite `/data/divergence-desk.db` stores bars, funding, frozen rules, actual
reviews and portfolios. Baseline execution decisions and book mutations commit
atomically. A saved-rule mismatch disables the new scanner rather than changing
an existing book. `DIVERGENCE_ENABLED=false` disables the worker.

## Reproduce the first study

Run `python -m scripts.run_divergence_research --days 730 --end 2026-10-10` from
the repository root. The pipeline caches public data under
`data/divergence-research`, verifies continuous 15m candles and funding coverage,
and writes `app/divergence/results/report.json` (per-book curves and trades go to the cache folder). No API key.

DIV-SR-4 study (2026-10-11): October 10, 2024 – October 10, 2026 UTC; first 511 days development, final 219 days
holdout with a 15-minute boundary embargo. All ten symbols, both timeframe mappings, and each of the four divergence
types as its own book (`by_type`).

Each book holds one position at a time. DIV-SR-4 trades:

| Type | Development | Holdout |
|---|---|---|
| 1h regular bullish | 12 trades, PF 1.84 | 1 trade |
| 1h regular bearish | 12 trades, PF 1.30 | 9 trades, PF 2.26 |
| 1h hidden bearish | 10 trades, PF 1.21 | 1 trade |
| 4h hidden bearish | 6 trades, PF 1.85 | none |
| Hidden bullish (both timeframes) | 5 trades, all lost | 2 trades, both lost |

Replaying every signal on its own over the two years:

| Rule | Trades | Win rate | Total |
|---|---|---|---|
| DIV-SR-9 (15m trend and opposite divergence are warnings) | 95 | 41% | +16.2R (+0.17R per trade) |
| DIV-SR-8 (no active opposite divergence; hidden follow the higher-timeframe trend) | 29 | 45% | +8.8R (+0.30R per trade) |
| DIV-SR-7 (15m trend check; no-chase 2 ATR) | 52 | 40% | +8.7R |
| DIV-SR-6 (stop beyond the previous swing extreme) | 83 | 40% | +10.7R |
| DIV-SR-5 (buying vs selling volume since swing 2) | 99 | 36% | +1.8R |
| DIV-SR-4 | 62 | 40% | +8.9R |
| DIV-SR-3 | 11 | | +3.4R |
| DIV-SR-2 | 266 | | −1.6R |

The volume fix adds 37 trades that net about −7R. Most of that comes from 1h hidden bullish divergences: 14 trades,
1 win, −12.3R. It is kept because it is the correct reading of volume, not a tuned choice.

The samples are still far too small to establish an edge.

The 30-day replays are in `docs/DIVERGENCE_SR3_COMPARISON.md` (DIV-SR-2 vs DIV-SR-3) and
`docs/DIVERGENCE_SR4_COMPARISON.md` (DIV-SR-3 vs DIV-SR-4) and `docs/DIVERGENCE_SR5_COMPARISON.md` (DIV-SR-4 vs DIV-SR-5) and `docs/DIVERGENCE_SR6_COMPARISON.md` (DIV-SR-5 vs DIV-SR-6) and `docs/DIVERGENCE_SR7_COMPARISON.md` (DIV-SR-6 vs DIV-SR-7) and `docs/DIVERGENCE_SR8_COMPARISON.md` (DIV-SR-7 vs DIV-SR-8).

Under DIV-SR-7, hidden divergences carry the result: 37 trades, 43% wins, +9.2R. Regular ones are flat: 15 trades,
−0.5R.

The UI displays actual Vela candles and Wilder RSI, pivot/zone/trigger/risk
overlays, setup history, source-linked council arguments, both forward books,
and all historical rows. Historical scan signals are explicitly distinguished
from prospective signals; opening the chart never opens a paper position.


## Dashboard integration (2026-10-11)

Divergence Desk now shares the main PaperLab sidebar and header. Group chat shows the actual saved four-reviewer output and explicit review failures. Ledger, Decisions, Charts and Historical results stay within the same panel. No chat input is shown because the frozen council is event-driven and exposes no user-message endpoint.

Public route: `/public/competition/divergence`. Authenticated route: `/#divergence/divergence`. `/public/divergence` redirects to the public panel. Existing frozen rules, evidence and paper books are preserved. This UI release does not rerun an old council review or charge additional AI calls.

At the pre-deploy read on 2026-10-11, the baseline had one open ADA short and zero closed trades; the council had zero positions after its first review failed validation. The old error stores only ValueError, so the exact validation cause cannot be reconstructed from that record. Historical average net holdout returns were -0.266% (1h, 37 trades) and -0.307% (4h, 12 trades), across ten independent symbol books; these are not monthly portfolio returns.


Integration verification: browser fixtures cover desktop/mobile conversation rendering, safe text and links, same-document navigation, inactive polling cleanup, ledger/decision/history panes, native Vela overlays and synchronized viewport, and failed-request recovery. Production checks cover both public and authenticated shells using GET-only requests. The same check exposed an existing activity-feed initialization bug for newer program streams; buffers are now initialized when the first event arrives, with duplicate events still suppressed.


## Price / RSI divergence annotations (2026-10-11)

Charts now use one native Vela instance with a lower RSI study pane, replacing the volume bars. RSI is the existing server-computed Wilder RSI(14), plotted on the exact candle timestamps with 30/70 guides and 0/100 bounds. No strategy calculations change.

Matching numbered pivots and price/RSI values are drawn in both panes, with cyan bullish and pink bearish connectors. The legend describes lower-low/higher-low or higher-high/lower-high and retains the setup's status and date. Opening a market selects its latest recorded setup (prefer the current divergence timeframe); changing to a different timeframe explains where to find the divergence instead of drawing higher-timeframe RSI values over lower-timeframe RSI. Historical/expired setups remain labeled as such.

Verified one chart, two panes, no volume indicator, two visible price markers and two RSI markers, synchronized zoom, and responsive layouts. Regression checked the original bot charts and their volume/trade overlays separately.
