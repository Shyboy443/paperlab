"""Shared paper execution and atomic persistence for CLI and PaperLab."""
from __future__ import annotations
import json
import os
from contextlib import contextmanager
from pathlib import Path
from app.video_breakout.engine import BreakoutBot, ENTRY_BARS, STEP, validate_bar

def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextmanager
def state_lock(path: Path):
    """Hold a process lock for the entire paper session, including each poll."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a+b") as f:
        f.seek(0, 2)
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def paper_step(bot: BreakoutBot, observed: int, bars, lateness: int, *, allow_entry: bool = True) -> dict:
    for i, c in enumerate(bars):
        validate_bar(c, bot.config.symbol)
        if c.close_time != c.open_time + STEP or (i and c.open_time != bars[i - 1].open_time + STEP):
            raise ValueError("Non-contiguous or incorrectly timestamped public feed")
    closed = [c for c in bars if c.closed]
    forming = [c for c in bars if not c.closed]
    if len(closed) < ENTRY_BARS or len(forming) != 1:
        raise ValueError("Need contiguous warmup and exactly one current forming bar")
    current = forming[0]
    if closed[-1].close_time != current.open_time:
        raise ValueError("Warmup does not reach the current interval")
    if not current.open_time <= observed < current.open_time + STEP:
        raise ValueError("Stale current-bar response")
    if not bot.history:
        bot.warmup(closed[-ENTRY_BARS:])
        bot.mark(observed, current.close)
        return {"status": "WARMED_UP", "detail": "Waiting for the next completed candle; no historical fills"}
    newer = [c for c in closed if c.open_time > bot.history[-1].open_time]
    # An offline bot cannot retrospectively trade bars it missed. Require operator review.
    if len(newer) > 1 or (newer and newer[0].open_time != bot.history[-1].open_time + STEP):
        raise ValueError("Missed candle(s) while offline; use a new state file after reviewing the open position")
    if newer:
        signal = bot.on_close(newer[0])
        if signal and signal["side"] == "BUY" and not allow_entry:
            bot.pending = None
            bot.signals[-1]["suppressed"] = "New entries paused"
    fill = bot.on_open(current.open_time, current.close, observed_ms=observed,
                       max_lateness_ms=lateness)
    if fill:
        fill["reported_candle_open"] = current.open
        fill["observed_ms"] = observed
        fill["execution_model"] = "Paper fill at observed price near next open, plus adverse slippage"
        if bot.entry is not None and fill["side"] == "BUY":
            bot.entry = fill.copy()
    bot.mark(observed, current.close)
    return {"status": "PAPER", "fill": fill, **bot.summary()}


