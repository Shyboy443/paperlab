# Bitcoin 4h breakout from the supplied video

Source: [Lewis Jackson Investing — GPT-6 Astra Is INSANE for Trading](https://www.youtube.com/watch?v=vVT6vzpRB3o).
Extracted from the complete timestamped English captions. The video stream could not be retrieved,
so additional settings briefly displayed on screen remain unverified.

## Rules frozen before evaluation

At **5:05–5:20**, the presenter describes a completed 4h candle closing strictly above
the highest price in the **previous 7 days**, buying at the **next open**, and selling
at the next open after a close strictly below the lowest price in the **previous 3 days**.
At 4h resolution these are 42 prior candle highs and 18 prior candle lows. The signal
candle is excluded. Buy means one long position; sell returns to cash. No shorting,
pyramiding, take-profit, ATR stop, moving-average filter or leverage is added.

At **5:24–5:43**, costs/slippage and a **>40% maximum drawdown rejection gate** are
described. This is a validation gate, not an instruction to close a position or halt
the simulation at 40%. At **10:36–10:50**, fewer than **50 completed trades** makes
a test inconclusive. Failure takes priority over insufficient sample size.
`KNOWN_GATES_PASS` means only these two disclosed checks passed; full validation
cannot be claimed because the presenter mentions other unspecified conditions.

The strategy itself was rejected in the video's original backtest at roughly 45%
drawdown despite its quoted 384% net return (**6:23–6:56**). Those are the author's
claims, not verified results of this implementation.

## Explicit implementation assumptions

- Spot BTCUSDT, long/cash, 1x. The video's exact venue/instrument is unavailable.
- Initial cash: 1,000 USDT; 100% of available cash per entry, reserving the entry fee.
  Position sizing was not disclosed. `--balance` and `--allocation` are configurable.
- Per side: 10 bps fee and 2 bps adverse proportional slippage. Numeric costs were
  not disclosed. Override with `--fee-bps` and `--slippage-bps` for your venue.
- Research dates: 2022-01-01 through 2025-12-31. These are selected complete years,
  not a claimed match to the author's four-year dataset. A preceding 7-day warmup
  supplies history without generating trades or entering the measured results.
- Equity/drawdown is sampled at 4h closes and traded opens, not intrabar ticks.
- The last position stays marked to market; the last signal remains pending if
  there is no next bar. No invented end-of-test liquidation.

## Run from `paperlab`

Python 3.11+. The implementation uses the standard library and the existing Candle
type. No exchange credentials or extra packages are needed for the runner.

```powershell
python scripts/video_breakout.py backtest --start 2022-01-01 --end 2026-01-01
python scripts/video_breakout.py paper --once
python scripts/video_breakout.py paper
```

Backtests fetch only public [Binance spot archives](https://github.com/binance/binance-public-data),
verify published SHA-256 checksums, handle millisecond/microsecond timestamp formats,
and reject incomplete, missing, duplicate or out-of-order bars. Results live in
`research/video_breakout/output/<run-id>/`: report, manifest, summary, and signal,
fill, trade and equity CSVs. Manifest/source/data identities are frozen before the run.
Run Ethereum or alternative windows using `--symbol ETHUSDT`, `--start` and `--end`.
Rules stay fixed; no parameter search or selection is performed.

## Persistent paper bot

### Trade from PaperLab

Open **Bitcoin breakout** on the Competition dashboard or in the **Bots** table.
Its authenticated bot page is `/?p=video&bot=BTC-4H-BREAKOUT#bots/bots`;
the old `/public/video-breakout` link redirects there. Public Competition also
shows its card, charts and trade history, with a link to sign in for controls.
Unlock with the existing dashboard password, set the paper balance, allocation
and execution costs, then click **Start bot**. The service is registered
in the app lifecycle and saves its own book on the Railway data volume at
`DATA_DIR/video-breakout.json`. It starts paused on the first deployment; an explicitly
started book resumes after a normal restart if its market history remains contiguous.

**Pause entries** retains the position and keeps its automatic three-day exit.
**Close position** sells at a fresh observed quote, deducts costs and pauses.
**Reset wallet** requires a paused, flat book and archives its previous history
under `DATA_DIR/video-breakout-history/`. Settings cannot rebase a wallet with trade
history; reset first. The lab's **KILL ALL** also pauses this book and attempts to close
its paper position. A quote failure preserves inventory and reports a blocked state.

The page shows current price, breakout levels, wallet equity, inventory, net return,
signals, fills, fees, closed trades and activity. Controls require Basic auth and the
existing CSRF header. No real-order broker integration is enabled by these controls.
The standalone CLI below remains available with a separate state file.

The bot reads Binance's public market-data-only endpoint. It has no API keys, order
endpoints or live-order mode. It is separate from the lab's leveraged strategy registry,
whose stops/sizing/targets would change the requested rules. No existing strategy is changed.

First start warms up completed bars and waits for the next close. It does not manufacture
historical paper fills. Polling defaults to 10 seconds. A new close queues an action for
the following open; paper execution uses the observed market price near that open,
plus configured slippage. The reported candle open and observation timestamp are also
recorded. Actions first observed more than 30 seconds after the opening expire instead
of filling retrospectively; `--max-lateness-seconds` controls this 1..60 second window.
This latency guard is an operational addition, not a rule disclosed by the presenter.

Paper state is checkpointed atomically in `data/video_breakout/paper.json`, with fills,
cash, inventory, signals, history and equity. Restart with the same config to resume.
A process lock prevents duplicate runners sharing a state file. Missed closed candles
or data/network errors stop the process for review. For a separate book use `--state`
with another path. State/config mismatches are refused. Stop with Ctrl+C.

An expired sell leaves the paper position open until a subsequent valid exit signal;
there is no automatic liquidation at an invented historical opening price.

```powershell
python -m pytest tests/test_video_breakout.py -q
```
