"""Walk-forward and split-test scaffold used by every round.

The user's prior rounds (1..8) used a 12-month-train / 3-month-test
walk-forward with chained test windows and a 70/30 split-test. This
module reproduces that structure so any round in this repo can plug in.
"""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class WalkForwardSpec:
    train_days: int = 365
    test_days: int = 90

def walkforward_windows(n_bars: int, spec: WalkForwardSpec | None = None) -> list[tuple[int, int, int, int]]:
    """Return [(train_start, train_end, test_start, test_end), ...] for the input
    bar count, expressed in hourly bars (1 bar = 1 hour).

    The windowing is the same chained-test convention the prior rounds
    used: 12m train → 3m test, slide by 3m, chain the test windows.
    """
    spec = spec or WalkForwardSpec()
    train, test = spec.train_days, spec.test_days
    step = test
    windows = []
    start = 0
    while start + train + test <= n_bars:
        tr_s, tr_e = start, start + train
        te_s, te_e = tr_e, tr_e + test
        windows.append((tr_s, tr_e, te_s, te_e))
        start += step
    return windows

def split70_30(n_bars: int) -> tuple[int, int, int, int]:
    """70/30 train/test split on a single window. Returns (tr_s, tr_e, te_s, te_e)."""
    cut = int(n_bars * 0.7)
    return 0, cut, cut, n_bars
