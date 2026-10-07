"""EXIT ANALYZER: does a strategy lose its edge on the way OUT? (MFE / MAE on the 1m tape)

For every closed trade, from the 1m bars between its entry and exit:

    MFE  maximum favourable excursion before the exit, in R (stop distances)
    MAE  maximum adverse excursion before the exit, in R
    profit left on the table   MFE - realised R (what the exit gave back)
    stop hit before the eventual favourable move   a trade stopped out whose idea then worked: within
         12 hours of its ENTRY, price later reached +1R / +2R from the entry price

Aggregated per group (strategy family x timeframe), with duration by winner / loser, the result by
holding-time bucket (<5m, 5-15m, 15-30m, 30-60m, 1-2h, 2-4h, >4h), and EDGE QUALITY: edge per trade, per
unit of turnover, per fee dollar and per day. The flags say which exit component is broken:

    STOPS TOO TIGHT        many stopped trades later reach +2R, and winners already go deep against us
    TARGETS TOO CLOSE      winners' MFE is far beyond what they realise (large profit left on the table)
    WINNERS CUT EARLY      winners hold much shorter than the move they catch
    LOSERS HELD TOO LONG   losers stay open far longer than winners

Holding-time buckets are CONDITIONED on the outcome (a trade still open after 4h is one the stop did not
take), so "long holds win" is survivorship, not advice; the counterfactual exit grid is the test for that.

Read only: it never changes a trade or a strategy, and it only reads bars the trade could have seen
plus the 12h after entry that define whether the idea eventually worked (a diagnostic, never an input).
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from app.competition.v31_diagnostics import Tape

HOLD_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("<5m", 0, 300), ("5-15m", 300, 900), ("15-30m", 900, 1800), ("30-60m", 1800, 3600),
    ("1-2h", 3600, 7200), ("2-4h", 7200, 14400), (">4h", 14400, float("inf")))
EVENTUAL_MIN = 720
POST_EXIT_MIN = 240


def _r(x: Any, nd: int = 3) -> Any:
    return round(x, nd) if isinstance(x, (int, float)) and math.isfinite(x) else None


def _mean(xs: Iterable[float]) -> float | None:
    v = [x for x in xs if x is not None and math.isfinite(x)]
    return sum(v) / len(v) if v else None


def _pct(xs: Iterable[float], q: float) -> float | None:
    v = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def stop_distance(t: Mapping[str, Any]) -> float | None:
    """Per-unit stop distance. R = net / (qty x |fill - stop|), so net / R / qty recovers it exactly;
    the sizing budget (risk_usd / qty) is the fallback for a scratch trade (R = 0)."""
    try:
        qty, net, r = float(t["qty"]), float(t.get("net") or 0.0), float(t.get("r") or 0.0)
        if qty > 0 and abs(r) > 1e-9 and abs(net) > 1e-12:
            return abs(net / r) / qty
        return float(t["risk_usd"]) / qty if t.get("risk_usd") and qty > 0 else None
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def excursion(t: Mapping[str, Any], tape: Tape | None) -> dict[str, Any] | None:
    """MFE / MAE / profit left / stopped-then-favourable for one trade (None if it cannot be measured)."""
    stop_dist = stop_distance(t)
    if tape is None or not stop_dist:
        return None
    try:
        entry, exit_px = float(t["entry"]), float(t["exit"])
        a, b = int(t["entry_ts"]), int(t["exit_ts"])
    except (KeyError, TypeError, ValueError):
        return None
    ex = tape.extremes(a, b + 1)
    if entry <= 0 or ex is None:
        return None
    sgn = 1.0 if t.get("side") == "long" else -1.0
    hi, lo = ex
    fav, adv = (hi - entry, entry - lo) if sgn > 0 else (entry - lo, hi - entry)
    mfe, mae = max(0.0, fav) / stop_dist, max(0.0, adv) / stop_dist
    realised = sgn * (exit_px - entry) / stop_dist
    out = {"mfe_r": mfe, "mae_r": mae, "realised_r": realised, "profit_left_r": max(0.0, mfe - max(realised, 0.0)),
           "stop_bps": stop_dist / entry * 1e4}
    if str(t.get("exit_kind")) == "stop":
        later = tape.extremes(b + 1, a + EVENTUAL_MIN * 60_000)
        if later:
            out["eventual_r"] = ((later[0] - entry) if sgn > 0 else (entry - later[1])) / stop_dist
    px = tape.close_at(b + POST_EXIT_MIN * 60_000)
    if px:
        out["post_exit_r"] = sgn * (px - exit_px) / stop_dist
    return out


def stopped_then_2r_baseline(tape: Tape, stop_dist: float, entry_ts: int, rng: Any, draws: int = 3) -> list[bool]:
    """The same question for RANDOM entries (random time within a day of the trade, random side, same
    stop distance): of those stopped within 12h, how many then reached +2R from entry within 12h? A
    tight stop in a volatile market scores high on this by construction; only the excess over this
    baseline says the stop, not the noise, was the problem."""
    out = []
    n = len(tape.t)
    for _ in range(draws):
        i0 = tape.index(entry_ts + rng.randint(-1440, 1440) * 60_000)
        if i0 >= n - EVENTUAL_MIN:
            continue
        entry = tape.c[i0]
        sgn = rng.choice((1.0, -1.0))
        stop = entry - sgn * stop_dist
        hit = None
        for j in range(i0 + 1, i0 + EVENTUAL_MIN):
            if (sgn > 0 and tape.lo[j] <= stop) or (sgn < 0 and tape.h[j] >= stop):
                hit = j
                break
        if hit is None:
            continue
        ex = tape.extremes(tape.t[hit] + 1, tape.t[i0] + EVENTUAL_MIN * 60_000)
        out.append(bool(ex) and ((ex[0] - entry) if sgn > 0 else (entry - ex[1])) >= 2.0 * stop_dist)
    return out


def summarize(trades: Sequence[Mapping[str, Any]], exc: Sequence[dict[str, Any] | None],
              days: float | None = None, baseline: Sequence[bool] | None = None) -> dict[str, Any]:
    n = len(trades)
    if not n:
        return {"n": 0}
    pairs = [(t, e) for t, e in zip(trades, exc) if e is not None]
    win = [(t, e) for t, e in pairs if float(t["net"]) > 0]
    los = [(t, e) for t, e in pairs if float(t["net"]) <= 0]
    stopped = [(t, e) for t, e in pairs if str(t.get("exit_kind")) == "stop" and e.get("eventual_r") is not None]
    gross = sum(float(t.get("gross") or 0.0) for t in trades)
    fees = sum(float(t.get("fees") or 0.0) for t in trades)
    slip = sum(float(t.get("slippage") or 0.0) for t in trades)
    net = sum(float(t.get("net") or 0.0) for t in trades)
    notional = sum(float(t.get("notional") or 0.0) for t in trades)
    risk = [float(t["risk_usd"]) for t in trades if t.get("risk_usd")]
    buckets = {}
    for name, lo_s, hi_s in HOLD_BUCKETS:
        part = [t for t in trades if lo_s <= float(t.get("hold_s") or 0) < hi_s]
        buckets[name] = {"n": len(part), "gross": _r(sum(float(t.get("gross") or 0) for t in part), 4),
                         "net": _r(sum(float(t.get("net") or 0) for t in part), 4),
                         "mean_r": _r(_mean(float(t["r"]) for t in part), 3)}
    w_hold = _pct([float(t.get("hold_s") or 0) / 60 for t, _ in win], 0.5)
    l_hold = _pct([float(t.get("hold_s") or 0) / 60 for t, _ in los], 0.5)
    s = {
        "n": n, "measured": len(pairs), "winners": len(win), "losers": len(los),
        "stop_bps_p50": _r(_pct([e["stop_bps"] for _, e in pairs], 0.5), 1),
        "mfe_r_mean": _r(_mean(e["mfe_r"] for _, e in pairs)), "mae_r_mean": _r(_mean(e["mae_r"] for _, e in pairs)),
        "winners_mfe_r_p50": _r(_pct([e["mfe_r"] for _, e in win], 0.5)),
        "winners_mae_r_p75": _r(_pct([e["mae_r"] for _, e in win], 0.75)),
        "winners_realised_r_mean": _r(_mean(e["realised_r"] for _, e in win)),
        "losers_mfe_r_p50": _r(_pct([e["mfe_r"] for _, e in los], 0.5)),
        "losers_mae_r_p50": _r(_pct([e["mae_r"] for _, e in los], 0.5)),
        "profit_left_r_mean": _r(_mean(e["profit_left_r"] for _, e in pairs)),
        "winners_profit_left_r_mean": _r(_mean(e["profit_left_r"] for _, e in win)),
        "reached_1r_share": _r(sum(1 for _, e in pairs if e["mfe_r"] >= 1.0) / len(pairs)) if pairs else None,
        "stopped": len(stopped),
        "stopped_then_1r_share": _r(sum(1 for _, e in stopped if e["eventual_r"] >= 1.0) / len(stopped)) if stopped else None,
        "stopped_then_2r_share": _r(sum(1 for _, e in stopped if e["eventual_r"] >= 2.0) / len(stopped)) if stopped else None,
        "random_stopped_then_2r_share": _r(sum(baseline) / len(baseline)) if baseline else None,
        "winners_post_exit_4h_r_mean": _r(_mean(e.get("post_exit_r") for _, e in win)),
        "all_post_exit_4h_r_mean": _r(_mean(e.get("post_exit_r") for _, e in pairs)),
        "winner_hold_min_p50": _r(w_hold, 1), "loser_hold_min_p50": _r(l_hold, 1),
        "hold_buckets": buckets,
        "edge": {"gross": _r(gross, 4), "fees": _r(fees, 4), "slippage": _r(slip, 4), "net": _r(net, 4),
                 "gross_per_trade": _r(gross / n, 5), "net_per_trade": _r(net / n, 5),
                 "gross_r_per_trade": _r(gross / sum(risk), 4) if risk and sum(risk) > 0 else None,
                 "gross_bps_of_turnover": _r(gross / notional * 1e4, 2) if notional > 0 else None,
                 "net_bps_of_turnover": _r(net / notional * 1e4, 2) if notional > 0 else None,
                 "gross_per_fee_dollar": _r(gross / fees, 3) if fees > 0 else None,
                 "net_per_fee_dollar": _r(net / fees, 3) if fees > 0 else None,
                 "gross_per_day": _r(gross / days, 4) if days else None, "net_per_day": _r(net / days, 4) if days else None},
    }
    s["flags"] = flags(s)
    return s


def flags(s: Mapping[str, Any]) -> list[str]:
    """Which exit component is broken, judged against a baseline rather than in isolation:

    STOPS TOO TIGHT       stopped trades later reach +2R clearly MORE often than random entries with the
                          same stop distance do (+10 points), and winners already dip >= 0.6R first
    WINNERS CUT EARLY     after a winning exit, price keeps going our way: +0.3R mean over the next 4h
    TARGETS TOO CLOSE     winners' median MFE is >= 1.6x what they realise AND the move continues
                          after the exit (the post-exit test above); MFE alone is not evidence
    LOSERS HELD TOO LONG  losers' median hold is > 1.5x the winners'
    """
    out = []
    st, base = s.get("stopped_then_2r_share"), s.get("random_stopped_then_2r_share")
    if st is not None and base is not None and st >= base + 0.10 and (s.get("winners_mae_r_p75") or 0) >= 0.6:
        out.append("STOPS TOO TIGHT")
    post = s.get("winners_post_exit_4h_r_mean")
    if post is not None and post >= 0.3:
        mfe_w, real_w = s.get("winners_mfe_r_p50"), s.get("winners_realised_r_mean")
        close = mfe_w is not None and real_w and real_w > 0 and mfe_w >= 1.6 * real_w
        out.append("TARGETS TOO CLOSE / WINNERS CUT EARLY" if close else "WINNERS CUT EARLY")
    wh, lh = s.get("winner_hold_min_p50"), s.get("loser_hold_min_p50")
    if wh and lh and lh > 1.5 * wh:
        out.append("LOSERS HELD TOO LONG")
    if not out:
        out.append("NO EXIT FLAG" + ("" if ((s.get("edge") or {}).get("gross") or 0) > 0 else " (GROSS <= 0: LOOK AT ENTRIES)"))
    return out


def analyze(groups: Mapping[Any, Sequence[Mapping[str, Any]]], tapes: Mapping[str, Tape],
            days: float | None = None, symbol_of=lambda t: t.get("symbol"), seed: int = 7) -> dict[Any, dict[str, Any]]:
    import random
    rng = random.Random(seed)
    out = {}
    for g, trades in groups.items():
        exc, base = [], []
        for t in trades:
            tape = tapes.get(symbol_of(t))
            e = excursion(t, tape)
            exc.append(e)
            if e is not None and str(t.get("exit_kind")) == "stop":
                base.extend(stopped_then_2r_baseline(tape, stop_distance(t), int(t["entry_ts"]), rng))
        out[g] = summarize(trades, exc, days, base)
    return out
