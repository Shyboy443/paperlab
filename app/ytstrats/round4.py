"""Scribd round 4: monthly momentum / rotation systems (docs/SCRIBD_ROUND4_STUDY.md), written down before any result.

Signals use adjusted closes on each month's last common trading day; the trade is made at the next trading day's open
and held to the next month's first open. One "trade" = one month's holding-period return. ``cost`` is charged per side
on the traded amount (|weight change|).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from .swing import D


def _calendar(data: dict[str, D]):
    """Common trading days of all assets -> (day keys, {sym: index array into its own D})."""
    keys = None
    for b in data.values():
        k = (b.t // 86_400_000).astype(np.int64)
        keys = set(k) if keys is None else keys & set(k)
    days = np.array(sorted(keys), dtype=np.int64)
    idx = {s: np.searchsorted((b.t // 86_400_000).astype(np.int64), days) for s, b in data.items()}
    return days, idx


def monthly(data: dict[str, D], weights_fn: Callable[[int, dict[str, np.ndarray], list[int]], dict[str, float]],
            cost: float, s0: int, s1: int, scale: float = 1.0) -> tuple[list[dict[str, Any]], list[float], list[int]]:
    """Run a monthly rule. weights_fn(m, closes_at_month_ends, month_end_positions) -> {sym: weight} for month m+1.
    scale: a trade's return is reported per its own book (e.g. 12 for one ETF of an equal 1/12 portfolio).
    Returns (trades, portfolio month returns in %, month start times)."""
    days, idx = _calendar(data)
    ym = np.array([(lambda d: d.year * 12 + d.month)(datetime.fromtimestamp(int(x) * 86400, timezone.utc)) for x in days])
    ends = np.flatnonzero(np.r_[ym[1:] != ym[:-1], True])        # last common day of each month
    me_close = {s: data[s].c[idx[s][ends]] for s in data}      # month-end closes
    trades, port, starts = [], [], []
    w_old: dict[str, float] = {}
    last_trade_of: dict[str, dict] = {}
    for m in range(len(ends) - 1):
        e = ends[m]
        if e + 1 >= len(days):
            break
        n0 = e + 1                                              # first day of the next month
        n1 = ends[m + 1] + 1                                    # first day of the month after
        if n1 >= len(days):
            break
        t0 = int(days[n0]) * 86_400_000
        if not (s0 <= t0 < s1):
            continue
        w = weights_fn(m, me_close, list(ends[:m + 1]))
        if w is None:
            continue
        month = 0.0
        for s in set(w) | set(w_old):
            wn, wo = w.get(s, 0.0), w_old.get(s, 0.0)
            c = cost * abs(wn - wo)
            month -= c
            if wn > 0:
                b = data[s]
                r = b.o[idx[s][n1]] / b.o[idx[s][n0]] - 1
                month += wn * r
                rec = {"t": t0, "exit_t": int(days[n1]) * 86_400_000, "asset": s, "weight": wn,
                       "ret_pct": (wn * r - c) * 100 * scale, "r": None, "result": "month"}
                trades.append(rec)
                last_trade_of[s] = rec
            elif c and s in last_trade_of:                      # an exit: its cost goes to the last month held
                last_trade_of[s]["ret_pct"] -= c * 100 * scale
        port.append(month * 100)
        starts.append(t0)
        w_old = w
    return trades, port, starts


def _ret(me: np.ndarray, pos: int, k: int) -> float:
    return me[pos] / me[pos - k] - 1 if pos - k >= 0 else np.nan


def gmr_weights(syms: tuple[str, ...], lookback: int = 3):
    """Global Market Rotation, do-it-yourself version (399509251): each month hold the one ETF of MDY, IEV, EEM, ILF,
    EPP, EDV with the best 3-month return."""
    def fn(m, me, ends):
        perf = {s: _ret(me[s], m, lookback) for s in syms}
        if any(np.isnan(v) for v in perf.values()):
            return None
        return {max(perf, key=perf.get): 1.0}
    return fn


def gem_weights(m, me, ends):
    """Global Equities Momentum, Antonacci (1071527952): the stronger of SPY and VEU by 12-month return; hold it if it
    beat T-bills (BIL) over 12 months, otherwise hold AGG."""
    r = {s: _ret(me[s], m, 12) for s in ("SPY", "VEU", "BIL")}
    if any(np.isnan(v) for v in r.values()):
        return None
    win = "SPY" if r["SPY"] >= r["VEU"] else "VEU"
    return {win: 1.0} if r[win] > r["BIL"] else {"AGG": 1.0}


def alvarez_weights(syms: tuple[str, ...]):
    """Trend-Following Plus Momentum in ETFs, Alvarez Quant Trading (439780519): per ETF, trend = month-end close above
    its 10-month average, momentum = 10-month return > 0; both -> 100 %, one -> 50 %, none -> 0 %. Each ETF holds 1/12
    of the capital in the portfolio return."""
    def fn(m, me, ends):
        if m < 10:
            return None
        w = {}
        for s in syms:
            seg = me[s][m - 9:m + 1]
            trend = me[s][m] > seg.mean()
            mom = _ret(me[s], m, 10) > 0
            k = int(trend) + int(mom)
            if k:
                w[s] = (0.5 if k == 1 else 1.0) / len(syms)
        return w
    return fn
