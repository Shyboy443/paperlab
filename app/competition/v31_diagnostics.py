"""V3 ROOT-CAUSE DIAGNOSTIC (the first step of V3.1, DEVELOPMENT data only).

Reads the frozen V3 run's closed trades and the same 1m DEVELOPMENT tape they were replayed on, and
asks, per strategy family and timeframe:

    HOLDING   how long trades lasted, and gross PnL by holding bucket (<5m, 5-15m, 15-60m, 1-4h, >4h)
    EXITS     which exit closed them (stop, target, trail, time, ...), with gross and R per exit kind
    ENTRY     the signed price drift after each entry at +15m / +1h / +4h / +12h, in bps: do the
              entries point the right way at ANY horizon, before any exit logic?
    EXCURSION maximum favourable / adverse excursion while open (in R), how often a trade that was
              +1R ahead finished at or below zero, and how far price kept going after the exit

It never looks at TEST data (the TEST months are not even on disk) and never changes a strategy.
"""
from __future__ import annotations

import math
import statistics
from bisect import bisect_left
from collections import defaultdict
from typing import Any, Iterable, Sequence

HOLD_BUCKETS: tuple[tuple[str, float, float], ...] = (("<5m", 0, 300), ("5-15m", 300, 900), ("15-60m", 900, 3600),
                                                        ("1-4h", 3600, 14400), (">4h", 14400, float("inf")))
HORIZONS_MIN: tuple[int, ...] = (15, 60, 240, 720)
POST_EXIT_MIN = 240


class Tape:
    """1m closes/highs/lows of one symbol, addressable by minute."""

    def __init__(self, candles: Iterable[Any]):
        cs = sorted(candles, key=lambda c: c.open_time)
        self.t = [int(c.open_time) for c in cs]
        self.c = [float(c.close) for c in cs]
        self.h = [float(c.high) for c in cs]
        self.lo = [float(c.low) for c in cs]

    def index(self, ts: int) -> int:
        """Index of the first bar opening at or after ts."""
        return bisect_left(self.t, int(ts))

    def close_at(self, ts: int) -> float | None:
        i = self.index(ts)
        return self.c[i] if i < len(self.c) else None

    def extremes(self, a_ts: int, b_ts: int) -> tuple[float, float] | None:
        i, j = self.index(a_ts), self.index(b_ts)
        if i >= len(self.t) or j <= i:
            return None
        return max(self.h[i:j]), min(self.lo[i:j])


def _mean(xs: Sequence[float]) -> float | None:
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return sum(xs) / len(xs) if xs else None


def _r(x: Any, nd: int = 3) -> Any:
    return round(x, nd) if isinstance(x, (int, float)) and math.isfinite(x) else None


def trade_path(t: dict[str, Any], tape: Tape | None) -> dict[str, Any]:
    """Per-trade forward drift, excursions and post-exit continuation. Directional numbers are
    signed by the trade's side: positive = the trade's direction."""
    sgn = 1.0 if t["side"] == "long" else -1.0
    entry = float(t["entry"])
    stop_dist = (float(t["risk_usd"]) / float(t["qty"])) if (t.get("risk_usd") and t.get("qty")) else None
    out: dict[str, Any] = {"stop_dist_bps": (stop_dist / entry * 1e4) if stop_dist else None}
    if tape is None or entry <= 0:
        return out
    for h in HORIZONS_MIN:
        px = tape.close_at(int(t["entry_ts"]) + h * 60_000)
        out[f"drift_{h}m_bps"] = (sgn * (px - entry) / entry * 1e4) if px else None
    ex = tape.extremes(int(t["entry_ts"]), int(t["exit_ts"]) + 1)
    if ex and stop_dist:
        hi, lo = ex
        fav, adv = (hi - entry, entry - lo) if sgn > 0 else (entry - lo, hi - entry)
        out["mfe_r"], out["mae_r"] = max(0.0, fav) / stop_dist, max(0.0, adv) / stop_dist
    post = tape.extremes(int(t["exit_ts"]), int(t["exit_ts"]) + POST_EXIT_MIN * 60_000)
    if post and stop_dist:
        hi, lo = post
        ex_px = float(t["exit"])
        out["post_exit_mfe_r"] = max(0.0, (hi - ex_px) if sgn > 0 else (ex_px - lo)) / stop_dist
        px4 = tape.close_at(int(t["exit_ts"]) + POST_EXIT_MIN * 60_000)
        out["post_exit_drift_r"] = (sgn * (px4 - ex_px) / stop_dist) if px4 else None
    return out


def summarize(trades: Sequence[dict[str, Any]], paths: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    if not n:
        return {"n": 0}
    holds = [t["hold_s"] for t in trades]
    hold = {}
    for name, lo, hi in HOLD_BUCKETS:
        part = [t for t in trades if lo <= t["hold_s"] < hi]
        hold[name] = {"n": len(part), "gross": _r(sum(t["gross"] for t in part), 4), "net": _r(sum(t["net"] for t in part), 4),
                      "mean_r": _r(_mean([t["r"] for t in part]))}
    exits: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        exits[t.get("exit_kind") or "?"].append(t)
    exit_mix = {k: {"n": len(v), "share": _r(len(v) / n), "gross": _r(sum(t["gross"] for t in v), 4),
                    "mean_r": _r(_mean([t["r"] for t in v]))} for k, v in sorted(exits.items(), key=lambda kv: -len(kv[1]))}
    cost_bps = _mean([(t["fees"] + t["slippage"]) / t["notional"] * 1e4 for t in trades if t.get("notional")])
    drift = {f"{h}m": _r(_mean([p.get(f"drift_{h}m_bps") for p in paths]), 2) for h in HORIZONS_MIN}
    mfe = [p.get("mfe_r") for p in paths if p.get("mfe_r") is not None]
    given_back = [1 for t, p in zip(trades, paths) if (p.get("mfe_r") or 0) >= 1.0 and t["net"] <= 0]
    winners = [t for t in trades if t["net"] > 0]
    losers = [t for t in trades if t["net"] <= 0]
    return {
        "n": n, "median_hold_min": _r(statistics.median(holds) / 60.0, 1), "hold_buckets": hold, "exits": exit_mix,
        "gross_bps_per_trade": _r(_mean([t["gross"] / t["notional"] * 1e4 for t in trades if t.get("notional")]), 2),
        "cost_bps_per_trade": _r(cost_bps, 2), "drift_bps": drift,
        "best_drift_bps": max((v for v in drift.values() if v is not None), default=None),
        "mfe_r_mean": _r(_mean(mfe)), "mae_r_mean": _r(_mean([p.get("mae_r") for p in paths])),
        "reached_1r_share": _r(sum(1 for x in mfe if x >= 1.0) / len(mfe)) if mfe else None,
        "gave_back_after_1r": len(given_back), "gave_back_share": _r(len(given_back) / n),
        "post_exit_mfe_r": _r(_mean([p.get("post_exit_mfe_r") for p in paths])),
        "post_exit_drift_r_winners": _r(_mean([p.get("post_exit_drift_r") for t, p in zip(trades, paths) if t["net"] > 0])),
        "post_exit_drift_r_losers": _r(_mean([p.get("post_exit_drift_r") for t, p in zip(trades, paths) if t["net"] <= 0])),
        "avg_win_r": _r(_mean([t["r"] for t in winners])), "avg_loss_r": _r(_mean([t["r"] for t in losers])),
        "win_rate": _r(len(winners) / n), "stop_dist_bps": _r(_mean([p.get("stop_dist_bps") for p in paths]), 1),
    }


def verdict(s: dict[str, Any]) -> str:
    """Where the edge is lost, from the family's own numbers."""
    if not s.get("n"):
        return "NO TRADES"
    cost = s.get("cost_bps_per_trade") or 0.0
    best = s.get("best_drift_bps")
    gross = s.get("gross_bps_per_trade") or 0.0
    if best is None or best <= 0:
        return "ENTRY HAS NO EDGE"
    if best < cost:
        return "ENTRY EDGE BELOW COST"
    if gross < best * 0.5:
        return "EXIT DESTROYS EDGE" if (s.get("gave_back_share") or 0) > 0.1 else "HOLD TOO SHORT"
    return "COST DESTROYED" if gross < cost else "EDGE SURVIVES"


def diagnose(records: dict[str, dict[str, Any]], tapes: dict[str, Tape]) -> dict[str, Any]:
    """`records`: {bot key: {identity, trades}}; `tapes`: {symbol: Tape}. Returns family x timeframe
    and timeframe-pooled diagnostics plus a verdict per cell."""
    cells: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for rec in records.values():
        idn = rec["identity"]
        tape = tapes.get(f"{idn['coin']}USDT")
        for t in rec.get("trades") or []:
            cells[(idn["strategy_id"], idn["timeframe"])].append((t, trade_path(t, tape)))
    fam: dict[str, dict[str, Any]] = {}
    for (sid, tf), rows in sorted(cells.items()):
        s = summarize([r[0] for r in rows], [r[1] for r in rows])
        s["verdict"] = verdict(s)
        fam.setdefault(sid, {})[tf] = s
    by_tf: dict[str, dict[str, Any]] = {}
    for tf in sorted({k[1] for k in cells}, key=lambda x: ["1m", "3m", "5m", "15m", "30m"].index(x) if x in ("1m", "3m", "5m", "15m", "30m") else 9):
        rows = [r for (sid, t), rs in cells.items() if t == tf for r in rs]
        s = summarize([r[0] for r in rows], [r[1] for r in rows])
        s["verdict"] = verdict(s)
        by_tf[tf] = s
    return {"families": fam, "timeframes": by_tf, "trades": sum(len(v) for v in cells.values()),
            "horizons_min": list(HORIZONS_MIN), "hold_buckets": [b[0] for b in HOLD_BUCKETS]}
