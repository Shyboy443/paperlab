"""Fixed reward:risk trade replay for the YouTube-ranking strategies (docs/YT_STRATEGIES_STUDY.md).

Per coin, one position at a time:
- a signal at candle i's close fills at candle i+1's open; the risk is |fill - stop| (a stop on the wrong side of the
  fill skips the trade); each target is fill +/- R x risk;
- from the fill candle on, every candle is checked for the stop and the targets; a 2-stage plan closes its shares
  separately. A candle that touches both the stop and an open target is settled with the 15-minute candles inside it
  (`fine`): whichever is touched first; still both in one 15-minute candle (or no finer data) -> the stop (conservative);
- results are in R (1 R = the planned risk), so they compare directly with the videos' "1 % risk per trade" GOA;
- costs: per side FEE + SLIP on the notional, converted to R per trade: (FEE + SLIP) x (entry + exit) / risk;
- signals while a position is open are ignored; a position still open at the window's end is closed at the last close.
"""
from __future__ import annotations

from typing import Any

import numpy as np

FEE, SLIP = 0.0005, 0.0002          # Binance USD-M taker 0.05 % + 0.02 % adverse slippage per side


def _first_touch(fine, t0: int, t1: int, d: int, sl: float, tp: float) -> str:
    """'stop' or 'target': which level the 15-minute candles inside [t0, t1) touch first (ties -> 'stop')."""
    if fine is None:
        return "stop"
    ft, fh, fl = fine
    a, b = np.searchsorted(ft, t0), np.searchsorted(ft, t1)
    for m in range(a, b):
        s_hit = fl[m] <= sl if d > 0 else fh[m] >= sl
        if s_hit:
            return "stop"
        if (fh[m] >= tp) if d > 0 else (fl[m] <= tp):
            return "target"
    return "stop"


def replay(t, o, h, l, c, sig, stop, targets, start_ms: int, end_ms: int, fee: float = FEE, slip: float = SLIP,
           fine=None, tf_ms: int = 0) -> list[dict[str, Any]]:
    """fine: (t, h, l) arrays of 15-minute candles to settle a candle touching both levels (tf_ms = this candle length)."""
    trades: list[dict[str, Any]] = []
    n = len(c)
    i = 0
    idx = np.flatnonzero((t >= start_ms) & (t < end_ms))
    if not len(idx):
        return trades
    first, last = idx[0], idx[-1]
    i = first
    while i < last:
        d = int(sig[i])
        if d == 0 or np.isnan(stop[i]):
            i += 1
            continue
        j = i + 1
        entry = o[j]
        risk = (entry - stop[i]) * d
        if not risk > 0:
            i += 1
            continue
        sl = stop[i]
        legs = [(share, entry + d * r * risk) for share, r in targets]
        open_legs = list(range(len(legs)))
        gross_r, cost_r, k = 0.0, 0.0, j
        result = None
        def book(q: int, px: float) -> None:
            nonlocal gross_r, cost_r
            share = legs[q][0]
            gross_r += share * d * (px - entry) / risk
            cost_r += share * (fee + slip) * (entry + px) / risk

        while open_legs and k <= last:
            hit_stop = (l[k] <= sl) if d > 0 else (h[k] >= sl)
            near = legs[open_legs[0]][1]                              # the nearest open target
            hit_near = (h[k] >= near) if d > 0 else (l[k] <= near)
            target_first = hit_stop and hit_near and _first_touch(fine, int(t[k]), int(t[k]) + tf_ms, d, sl, near) == "target"
            if not hit_stop or target_first:                          # targets touched this candle (before any stop)
                for q in list(open_legs):
                    tp = legs[q][1]
                    if (h[k] >= tp) if d > 0 else (l[k] <= tp):
                        book(q, max(o[k], tp) if d > 0 else min(o[k], tp))
                        open_legs.remove(q)
            if hit_stop and open_legs:                                # the rest stops out (a gap fills at the open)
                px = min(o[k], sl) if d > 0 else max(o[k], sl)
                for q in open_legs:
                    book(q, px)
                open_legs, result = [], "stop"
                break
            if open_legs:
                k += 1
        if open_legs:                                              # the window ended: close at the last close
            for q in open_legs:
                share = legs[q][0]
                gross_r += share * d * (c[last] - entry) / risk
                cost_r += share * (fee + slip) * (entry + c[last]) / risk
            result, k = "end", last
        trades.append({"t": int(t[j]), "exit_t": int(t[min(k, n - 1)]), "dir": d, "entry": float(entry), "stop": float(sl),
                       "risk_pct": float(risk / entry * 100), "gross_r": float(gross_r), "cost_r": float(cost_r),
                       "net_r": float(gross_r - cost_r), "result": result or "target"})
        i = k + 1 if k > i else i + 1
    return trades


def summarize(trades: list[dict[str, Any]], key: str = "net_r") -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    r = np.array([x[key] for x in trades])
    wins, losses = r[r > 0], r[r <= 0]
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    return {"trades": int(len(r)), "win_rate_pct": float(len(wins) / len(r) * 100), "total_r": float(r.sum()),
            "mean_r": float(r.mean()), "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
            "max_dd_r": float((peak - eq).max()), "goa_per_100": float(r.mean() * 100),
            "median_risk_pct": float(np.median([x["risk_pct"] for x in trades])) if "risk_pct" in trades[0] else None,
            "mean_cost_r": float(np.mean([x["cost_r"] for x in trades]))}


def bootstrap_low(r: np.ndarray, n: int = 2000, q: float = 2.5, seed: int = 1) -> float:
    """The q-th percentile of the bootstrapped mean (the lower bound of a 95 % interval for q = 2.5)."""
    if len(r) < 2:
        return float("nan")
    rng = np.random.default_rng(seed)
    means = rng.choice(r, size=(n, len(r)), replace=True).mean(axis=1)
    return float(np.percentile(means, q))
