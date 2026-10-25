"""The frozen IBS rule (docs/IBS_PAPER_BOT.md; round 3's `ibs_qqq`, docs/SCRIBD_ROUND3_STUDY.md).

    IBS = (close - low) / (high - low) of QQQ's regular session.
    Flat:    IBS < 0.10                      -> buy at the close.
    Holding: close > previous session's high -> sell at the close.
Long only, no stop. Any change here is a new experiment: FREEZE changes with the file.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

IBS_ENTRY = 0.10
SIGNAL_SYMBOL = "QQQ"
BOOKS = {"IBS-QQQ": "QQQ", "IBS-TQQQ": "TQQQ"}


def freeze_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def ibs(close: float, high: float, low: float) -> float | None:
    if high is None or low is None or close is None or high <= low:
        return None
    return (close - low) / (high - low)


@dataclass(frozen=True)
class Session:
    """QQQ's regular session so far (or final): high, low, last/close, and the previous session's high."""
    high: float
    low: float
    last: float
    prev_high: float


def decide(holding: bool, s: Session) -> tuple[str, dict]:
    """'enter', 'exit' or 'hold' / 'wait', with the numbers behind it."""
    x = ibs(s.last, s.high, s.low)
    info = {"ibs": x, "high": s.high, "low": s.low, "last": s.last, "prev_high": s.prev_high}
    if holding:
        return ("exit" if s.last > s.prev_high else "hold"), info
    return ("enter" if x is not None and x < IBS_ENTRY else "wait"), info
