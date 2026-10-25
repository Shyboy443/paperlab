"""General bar-by-bar trade replay for the Scribd scalping strategies (docs/SCRIBD_SCALPING_STUDY.md).

A strategy returns a Plan: per candle, a signal (+1 / -1 / 0, read at the close, filled at the next open) and how the
trade is managed. Every price distance is in PRICE units (the strategy converts pips with the asset's pip size).

Per trade (one position at a time per asset):
- entry at the next open, paying half the spread + slippage + commission (``cost_side``: a share of the price);
- ``stop``: an absolute stop price fixed at the signal, or ``stop_dist``: a distance from the actual fill;
- ``tp_dist`` (price distance) or ``tp_r`` (multiple of the initial risk); ``half_at_r``: close half at that R and
  move the stop to breakeven;
- ``trail_long`` / ``trail_short``: a stop level per candle (taken from the previous close) that only moves in the
  trade's favour;
- ``exit_long`` / ``exit_short``: rule exits read at a close, filled at the next open; ``reverse``: an opposite signal
  closes and opens the other side;
- ``max_hold``: candles after the entry candle; ``max_per_day``: entries per UTC day.
Inside a candle the price is assumed to travel open -> low -> high -> close for a green candle and open -> high -> low ->
close for a red one (the standard neutral OHLC path); the first level reached on that path wins, including a stop-entry
level inside the entry candle. A gap through a level fills at the open.
Results are in R (the initial risk) when a stop exists, and always in price and as a % of the entry price.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class Plan:
    sig: np.ndarray
    stop: np.ndarray | None = None          # absolute stop price set at the signal candle
    stop_dist: Any = None                   # float or array: distance from the fill
    tp_dist: Any = None                     # float or array: target distance from the fill
    tp_r: float | None = None               # target as a multiple of the initial risk
    half_at_r: float | None = None          # close half at this R, then the stop goes to breakeven
    trail_long: np.ndarray | None = None
    trail_short: np.ndarray | None = None
    exit_long: np.ndarray | None = None
    exit_short: np.ndarray | None = None
    reverse: bool = False
    max_hold: int | None = None
    max_per_day: int | None = None
    entry_px: np.ndarray | None = None      # a stop-entry level (entered only if the next candle reaches it)
    extra: dict = field(default_factory=dict)


def _val(x, i):
    if x is None:
        return None
    if np.isscalar(x):
        return float(x)
    v = x[i]
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


def replay(t, o, h, l, c, plan: Plan, start_ms: int, end_ms: int, cost_side: float) -> list[dict[str, Any]]:
    """cost_side: per-side cost as a share of the price (half spread + slippage + commission)."""
    n = len(c)
    idx = np.flatnonzero((t >= start_ms) & (t < end_ms))
    trades: list[dict[str, Any]] = []
    if not len(idx):
        return trades
    first, last = int(idx[0]), int(idx[-1])
    sig = plan.sig
    per_day: dict[int, int] = {}
    i = first
    pending_reverse = 0
    while i < last:
        d = int(sig[i]) if not pending_reverse else pending_reverse
        pending_reverse = 0
        if d == 0:
            i += 1
            continue
        day = int(t[i] // 86_400_000)
        if plan.max_per_day and per_day.get(day, 0) >= plan.max_per_day:
            i += 1
            continue
        j = i + 1
        if t[j] - t[i] > 4 * max(1.0, float(t[1] - t[0])):     # a market gap (weekend): no stale fill
            i += 1
            continue
        path = lambda q: (o[q], l[q], h[q], c[q]) if c[q] >= o[q] else (o[q], h[q], l[q], c[q])  # noqa: E731
        pts0 = path(j)
        if plan.entry_px is not None and not np.isnan(plan.entry_px[i]):
            lvl = plan.entry_px[i]
            seg = None                                           # the first point / segment reaching the stop-entry level
            if (pts0[0] >= lvl) if d > 0 else (pts0[0] <= lvl):
                seg, raw_entry = 0, pts0[0]
            else:
                for q in range(1, 4):
                    if (pts0[q] >= lvl) if d > 0 else (pts0[q] <= lvl):
                        seg, raw_entry = q, lvl
                        break
            if seg is None:
                i += 1
                continue
            first_pts = (raw_entry,) + tuple(pts0[seg:]) if seg else pts0
        else:
            raw_entry = o[j]
            first_pts = pts0
        per_day[day] = per_day.get(day, 0) + 1
        entry = raw_entry * (1 + d * cost_side)
        stop = _val(plan.stop, i)
        sd = _val(plan.stop_dist, i)
        if stop is None and sd is not None:
            stop = raw_entry - d * sd
        if stop is not None and (stop - raw_entry) * d >= 0:                 # stop on the wrong side: skip
            per_day[day] -= 1
            i += 1
            continue
        risk = abs(raw_entry - stop) if stop is not None else None
        tp = None
        td = _val(plan.tp_dist, i)
        if td is not None:
            tp = raw_entry + d * td
        elif plan.tp_r and risk:
            tp = raw_entry + d * plan.tp_r * risk
        half_px = raw_entry + d * plan.half_at_r * risk if plan.half_at_r and risk else None
        share, pnl, k, result, half_done = 1.0, 0.0, j, None, False

        def close_at(px_raw: float, sh: float) -> float:
            px = px_raw * (1 - d * cost_side)
            return sh * d * (px - entry)

        while k <= last:
            # the trailing level known at the previous close
            if k > j:
                tr = plan.trail_long if d > 0 else plan.trail_short
                if tr is not None and not np.isnan(tr[k - 1]):
                    stop = tr[k - 1] if stop is None else (max(stop, tr[k - 1]) if d > 0 else min(stop, tr[k - 1]))
            pts = first_pts if k == j else path(k)
            # walk the candle's path (green: open-low-high-close, red: open-high-low-close); the first level reached wins
            cur, done = pts[0], False
            fav = lambda x: x * d  # noqa: E731     (larger = more in the trade's favour)
            if stop is not None and fav(cur) <= fav(stop):
                pnl += close_at(cur, share)
                share, result, done = 0.0, "stop", True
            elif tp is not None and fav(cur) >= fav(tp):
                pnl += close_at(cur, share)
                share, result, done = 0.0, "target", True
            if not done:
                for nxt in pts[1:]:
                    if fav(nxt) < fav(cur):                                  # moving against the trade
                        if stop is not None and fav(nxt) <= fav(stop):
                            pnl += close_at(stop, share)
                            share, result, done = 0.0, "stop", True
                            break
                    elif fav(nxt) > fav(cur):                                # moving with the trade
                        if half_px is not None and not half_done and fav(nxt) >= fav(half_px):
                            pnl += close_at(half_px, share / 2)
                            share, half_done = share / 2, True
                            if not plan.extra.get("no_breakeven"):
                                stop = raw_entry
                        if tp is not None and fav(nxt) >= fav(tp):
                            pnl += close_at(tp, share)
                            share, result, done = 0.0, "target", True
                            break
                    cur = nxt
            if done:
                break
            ex = plan.exit_long if d > 0 else plan.exit_short
            rule_exit = ex is not None and bool(ex[k])
            opp = plan.reverse and int(sig[k]) == -d
            timed = plan.max_hold is not None and k - j + 1 >= plan.max_hold
            if (rule_exit or opp or timed) and k + 1 <= last:
                pnl += close_at(o[k + 1], share)
                share = 0.0
                result = "rule exit" if rule_exit else "opposite signal" if opp else "time"
                if opp:
                    pending_reverse = -d
                k += 1
                break
            k += 1
        if share > 0:
            k = min(k, last)
            pnl += close_at(c[k], share)
            result = "end"
        trades.append({"t": int(t[j]), "exit_t": int(t[min(k, n - 1)]), "dir": d, "entry": float(raw_entry),
                       "pnl_px": float(pnl), "ret_pct": float(pnl / raw_entry * 100),
                       "r": float(pnl / risk) if risk else None, "risk_pct": float(risk / raw_entry * 100) if risk else None,
                       "result": result})
        if pending_reverse:
            i = k - 1                     # the reversal fills at the same open the close used
        else:
            i = k + 1 if k > i else i + 1
    return trades


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    ret = np.array([x["ret_pct"] for x in trades])
    wins, losses = ret[ret > 0], ret[ret <= 0]
    rs = np.array([x["r"] for x in trades if x["r"] is not None])
    eq = np.cumsum(ret)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    out = {"trades": int(len(ret)), "win_rate_pct": float(len(wins) / len(ret) * 100),
           "mean_ret_pct": float(ret.mean()), "total_ret_pct": float(ret.sum()),
           "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
           "max_dd_pct_sum": float((peak - eq).max())}
    if len(rs):
        out.update(mean_r=float(rs.mean()), r_per_100=float(rs.mean() * 100))
    return out
