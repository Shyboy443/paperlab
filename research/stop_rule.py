"""Strict-stop check used by every round.

Returns a typed verdict so the runner can switch on it cleanly. The
historical rounds 1..8 in the user's RESEARCH_HISTORY.md converged on
"not achievable" — this module's job is to make that verdict a single
function call rather than a paragraph of prose, so the loop can't
accidentally declare a hit by hand-waving the criteria.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class StopCriteria:
    min_avg_daily: float = 0.0025   # +0.25%/day
    min_sharpe: float = 0.5
    require_floor_intact: bool = True
    min_trades: int = 30

@dataclass(frozen=True)
class RoundMetrics:
    avg_daily: float
    sharpe: float
    max_dd: float
    min_equity_pct: float          # worst per-window value, 1.0 = 100%
    trades: int
    n_windows: int

@dataclass(frozen=True)
class Verdict:
    hit: bool
    reason: str
    criteria: StopCriteria
    metrics: RoundMetrics

    def to_dict(self) -> dict:
        return {"hit": self.hit, "reason": self.reason,
                "criteria": asdict(self.criteria),
                "metrics": asdict(self.metrics)}

def evaluate(m: RoundMetrics, c: StopCriteria | None = None) -> Verdict:
    c = c or StopCriteria()
    if c.require_floor_intact and m.min_equity_pct < 0.45:
        return Verdict(False, f"floor breached: min_equity_pct={m.min_equity_pct:.3f} < 0.45", c, m)
    if m.trades < c.min_trades:
        return Verdict(False, f"too few trades: {m.trades} < {c.min_trades}", c, m)
    if m.avg_daily < c.min_avg_daily:
        return Verdict(False, f"avg_daily {m.avg_daily*100:.3f}% < +{c.min_avg_daily*100:.2f}%", c, m)
    if m.sharpe < c.min_sharpe:
        return Verdict(False, f"sharpe {m.sharpe:.3f} < {c.min_sharpe}", c, m)
    return Verdict(True, "all criteria met", c, m)
