"""Common metrics for the in-repo loop.

These intentionally don't reach for cleverness. The honest unit
"avg_daily net" is the only one that the strict-stop rule trusts.
"""
from __future__ import annotations
import math

def avg_daily_from_equity(equity_curve: list[tuple[int, float]], initial: float) -> float:
    """equity_curve: list of (ts_ms, equity). Returns the mean log return per
    calendar day across the curve, in *decimal* form (0.001 = 0.1%)."""
    if len(equity_curve) < 2: return 0.0
    rets = []
    for i in range(1, len(equity_curve)):
        prev, cur = equity_curve[i-1][1], equity_curve[i][1]
        if prev <= 0 or cur <= 0: continue
        rets.append(math.log(cur / prev))
    if not rets: return 0.0
    # Convert per-bar to per-day assuming hourly bars (24 bars/day). Rounds that
    # use a different bar size must scale `rets_per_bar` themselves.
    bars_per_day = 24
    rets_per_day = []
    for i in range(0, len(rets), bars_per_day):
        chunk = rets[i:i+bars_per_day]
        rets_per_day.append(sum(chunk))
    return sum(rets_per_day) / len(rets_per_day) if rets_per_day else 0.0

def sharpe_from_equity(equity_curve: list[tuple[int, float]], bars_per_day: int = 24) -> float:
    if len(equity_curve) < 2: return 0.0
    rets = []
    for i in range(1, len(equity_curve)):
        prev, cur = equity_curve[i-1][1], equity_curve[i][1]
        if prev <= 0 or cur <= 0: continue
        rets.append(math.log(cur / prev))
    if len(rets) < 2: return 0.0
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    if sd == 0: return 0.0
    return (mu / sd) * math.sqrt(bars_per_day * 365)

def max_drawdown(equity_curve: list[tuple[int, float]]) -> float:
    if not equity_curve: return 0.0
    peak = equity_curve[0][1]
    mdd = 0.0
    for _, e in equity_curve:
        if e > peak: peak = e
        dd = (peak - e) / peak if peak > 0 else 0.0
        if dd > mdd: mdd = dd
    return mdd
