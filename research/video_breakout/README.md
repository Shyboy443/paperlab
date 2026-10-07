# Video Bitcoin breakout

The Python implementation and persistent paper bot are ready. Read
[the full rules and run commands](../../docs/VIDEO_BREAKOUT.md).

- Engine: `app/video_breakout/engine.py`
- Public spot data: `app/video_breakout/data.py`
- CLI: `scripts/video_breakout.py`
- Tests: `tests/test_video_breakout.py`

From `paperlab`:

```powershell
python scripts/video_breakout.py backtest --start 2022-01-01 --end 2026-01-01
python scripts/video_breakout.py paper --once
python scripts/video_breakout.py paper
```

BTCUSDT, 2022–2025, 1,000 USDT, 100% cash allocation, 10 bps fee and 2 bps
slippage per side: **174.92% net return, 32.28% sampled maximum drawdown,
61 completed trades**. These satisfy the two disclosed gates. Other video
acceptance conditions are unavailable; the original video result is not verified.

See `output/856a287337c6c4ca/REPORT.md` and its frozen manifest, summary and
CSV ledgers. This is a spot long/cash model with explicit assumptions where the
video omits numeric settings. Rules remain 4h / prior 7-day high / prior 3-day low.

Validation: 42 strategy/integration tests and 84 existing regression tests passed; both a fresh public-feed paper initialization and a
restart were checked. An independent ledger replay reconciled 122 fills and all
61 completed trades to 2,749.247690 USDT final equity. The earlier run
`29a1fe615628aa50` is preserved; the current manifest also pins the data loader,
runner and shared Candle contract, with identical numerical results.

PaperLab now includes a **Bitcoin breakout** control page at `/public/video-breakout`,
using the dashboard password. Start, pause, close and reset are available; the lab's
kill switch includes this book. The newest research identity includes shared runtime
code and the manual-close accounting extension; backtest metrics remain identical.
