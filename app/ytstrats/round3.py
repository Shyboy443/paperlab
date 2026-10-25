"""Scribd round 3: NR7 breakouts, Internal Bar Strength, the SPY intraday-momentum paper and the ETH Bitcoin thesis,
written down as exact rules BEFORE any result (docs/SCRIBD_ROUND3_STUDY.md). Each docstring is the rule as tested.

Every simulator returns a list of trades {t, exit_t, dir, entry, ret_pct, result} with ``ret_pct`` after ``cost`` (a
share of the price paid on each fill). Inside a candle the price takes the neutral path (green O-L-H-C, red O-H-L-C).
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

from . import ind as I
from .swing import D, _stop_entry, path

DAY = 86_400_000


def _trade(t_in, t_out, d, px_in, px_out, cost, result):
    pnl = d * (px_out * (1 - d * cost) - px_in * (1 + d * cost))
    return {"t": int(t_in), "exit_t": int(t_out), "dir": int(d), "entry": float(px_in), "ret_pct": float(pnl / px_in * 100), "r": None, "result": result}


def nr7(h, l):
    rng = h - l
    return np.r_[np.zeros(6, bool), np.array([rng[i] <= rng[i - 6:i + 1].min() for i in range(6, len(rng))], bool)]


def _walk_daily(b: D, j: int, last: int, d: int, first_pts, stop, tp=None,
                trail: Callable[[int, float], float] | None = None, close_exit: Callable[[int], bool] | None = None):
    """Walk a position from candle j (entered, remaining path first_pts) to candle `last`. trail(k, stop) -> the stop
    for candle k (known before it opens); close_exit(k) -> exit at candle k's close. Returns (exit_px, k, result)."""
    fav = lambda x: x * d  # noqa: E731
    for k in range(j, last + 1):
        if k > j and trail is not None:
            stop = trail(k, stop)
        pts = first_pts if k == j else path(b.o[k], b.h[k], b.l[k], b.c[k])
        cur = pts[0]
        if k > j:
            if stop is not None and fav(cur) <= fav(stop):
                return cur, k, "stop"
            if tp is not None and fav(cur) >= fav(tp):
                return cur, k, "target"
        for p in pts[1:]:
            if fav(p) < fav(cur) and stop is not None and fav(p) <= fav(stop):
                return stop, k, "stop"
            if fav(p) > fav(cur) and tp is not None and fav(p) >= fav(tp):
                return tp, k, "target"
            cur = p
        if close_exit is not None and close_exit(k):
            return b.c[k], k, "close exit"
    return b.c[last], last, "end"


def _window(b: D, s0: int, s1: int) -> tuple[int, int]:
    return int(np.searchsorted(b.t, s0)), int(np.searchsorted(b.t, s1)) - 1


# ------------------------------------------------------------------------------------------------------------ NR7
def nr7_forex(b: D, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """NR7 Forex Trading Strategy (428753972), daily, any pair. After an NR7 bar (narrowest range of the last 7 bars,
    itself included): buy stop 2 pips above its high / sell stop 2 pips below its low on the next bar only (the first
    reached fills). Stop 2 pips beyond the other side, target 3 R."""
    out, flag = [], nr7(b.h, b.l)
    first, last = _window(b, s0, s1)
    i = max(first - 1, 6)
    while i < last:
        if not flag[i]:
            i += 1
            continue
        j = i + 1
        pts = path(b.o[j], b.h[j], b.l[j], b.c[j])
        hit = _stop_entry(pts, b.h[i] + 2 * pip, b.l[i] - 2 * pip)
        if not hit:
            i += 1
            continue
        d, fill, q = hit
        stop = b.l[i] - 2 * pip if d > 0 else b.h[i] + 2 * pip
        risk = (fill - stop) * d
        if risk <= 0:
            i += 1
            continue
        px, k, res = _walk_daily(b, j, last, d, (fill,) + tuple(pts[q:]) if q else pts, stop, fill + d * 3 * risk)
        out.append(_trade(b.t[j], b.t[k], d, fill, px, cost, res))
        i = k
    return out


def nr7_etf(b: D, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """StockCharts NR4/NR7 (418172887), daily ETFs. On the day after an NR7 day: buy above its high / sell below its
    low (stop entries, that day only, the first reached). Stop: the other side of the NR7 day. Exit at the first
    profitable close (the entry day included), Crabel's exit."""
    out, flag = [], nr7(b.h, b.l)
    first, last = _window(b, s0, s1)
    i = max(first - 1, 6)
    while i < last:
        if not flag[i]:
            i += 1
            continue
        j = i + 1
        pts = path(b.o[j], b.h[j], b.l[j], b.c[j])
        hit = _stop_entry(pts, b.h[i], b.l[i])
        if not hit:
            i += 1
            continue
        d, fill, q = hit
        stop = b.l[i] if d > 0 else b.h[i]
        if (fill - stop) * d <= 0:
            i += 1
            continue
        px, k, res = _walk_daily(b, j, last, d, (fill,) + tuple(pts[q:]) if q else pts, stop,
                                 close_exit=lambda k, d=d, fill=fill: (b.c[k] - fill) * d > 0)
        out.append(_trade(b.t[j], b.t[k], d, fill, px, cost, res))
        i = k
    return out


def nr7_nifty(b: D, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """AW10's NR7 draft system (382606844), NIFTY daily. Day 1 after an NR7 day: buy above the Day 0 high / sell
    below its low (stop entries, the first reached). Initial stop: the other side of Day 0. Trailing: on day k the
    stop is the low (long) / high (short) of day k-2, moved only in the trade's favour. Gap through -> exit at the
    open. No target."""
    out, flag = [], nr7(b.h, b.l)
    first, last = _window(b, s0, s1)
    i = max(first - 1, 6)
    while i < last:
        if not flag[i]:
            i += 1
            continue
        j = i + 1
        pts = path(b.o[j], b.h[j], b.l[j], b.c[j])
        hit = _stop_entry(pts, b.h[i], b.l[i])
        if not hit:
            i += 1
            continue
        d, fill, q = hit
        stop = b.l[i] if d > 0 else b.h[i]
        if (fill - stop) * d <= 0:
            i += 1
            continue

        def trail(k, s, d=d):
            lvl = b.l[k - 2] if d > 0 else b.h[k - 2]
            return max(s, lvl) if d > 0 else min(s, lvl)
        px, k, res = _walk_daily(b, j, last, d, (fill,) + tuple(pts[q:]) if q else pts, stop, trail=trail)
        out.append(_trade(b.t[j], b.t[k], d, fill, px, cost, res))
        i = k
    return out


# ------------------------------------------------------------------------------------------------------------ IBS
def _ibs(b: D) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(b.h > b.l, (b.c - b.l) / (b.h - b.l), np.nan)


def ibs_spy_ews(b: D, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """IBS (343659347, Kinlay after Pagonidis), SPY and EWS: IBS < 0.2 -> buy at the close, IBS > 0.8 -> sell short at
    the close; closed at the next close. Each signal day is its own one-day trade."""
    out, x = [], _ibs(b)
    first, last = _window(b, s0, s1)
    for i in range(first, last):
        d = 1 if x[i] < 0.2 else -1 if x[i] > 0.8 else 0
        if d:
            out.append(_trade(b.t[i], b.t[i + 1], d, b.c[i], b.c[i + 1], cost, "next close"))
    return out


def ibs_qqq(b: D, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """IBS (343659347, Llewelyn James' rule), QQQ: IBS < 0.10 -> buy at the close; exit at the first close above the
    previous day's high. Long only."""
    out, x = [], _ibs(b)
    first, last = _window(b, s0, s1)
    i = first
    while i < last:
        if not x[i] < 0.10:
            i += 1
            continue
        k = i + 1
        while k < last and not b.c[k] > b.h[k - 1]:
            k += 1
        out.append(_trade(b.t[i], b.t[k], 1, b.c[i], b.c[k], cost, "close exit" if b.c[k] > b.h[k - 1] else "end"))
        i = k
    return out


# ------------------------------------------------------------------------------------- SPY intraday momentum paper
def intraday_momentum(t, o, h, l, c, v, cost: float, s0: int, s1: int, ny_offset_ms: np.ndarray) -> list[dict[str, Any]]:
    """Beat the Market (861126103), "Current band + VWAP" model, on 1-minute candles. US session 09:30-16:00 New York.
    sigma(HH:MM) = mean over the previous 14 days of |close at HH:MM / 09:30 open - 1|. Upper band = max(open, previous
    16:00 close) * (1 + sigma), lower = min(...) * (1 - sigma). At HH:00 / HH:30 from 10:00 to 15:30: long above the
    upper band, short below the lower; a long exits below max(upper band, VWAP), a short above min(lower band, VWAP),
    and the opposite side may then open at the same check. Everything closes at 16:00. Fills at the next minute's
    open; VWAP from tick volume."""
    ny = t + ny_offset_ms
    day = (ny // DAY).astype(np.int64)
    mins = ((ny % DAY) // 60_000).astype(np.int64)
    checks = list(range(600, 960, 30))                       # 10:00 ... 15:30 (time = the end of minute T-1)
    rows = []                                                # per session: (t_day, open, close16, {T: (idx_of_bar_T-1)}, start idx, end idx)
    starts = np.flatnonzero(np.r_[True, day[1:] != day[:-1]])
    ends = np.r_[starts[1:], len(t)]
    for s, e in zip(starts, ends):
        m = mins[s:e]
        rth = np.flatnonzero((m >= 570) & (m < 960)) + s
        if len(rth) < 300 or mins[rth[0]] > 575 or mins[rth[-1]] < 955:
            continue
        pos = {int(mins[k]): k for k in rth}
        at = {T: pos.get(T - 1) for T in checks}
        if any(x is None for x in at.values()):
            continue
        rows.append((int(day[s]), float(o[rth[0]]), float(c[rth[-1]]), at, int(rth[0]), int(rth[-1])))
    moves = np.array([[abs(c[r[3][T]] / r[1] - 1) for T in checks] for r in rows]) if rows else np.zeros((0, len(checks)))
    out = []
    for n in range(15, len(rows)):
        dkey, op, close16, at, a, z = rows[n]
        if not (s0 <= t[a] < s1):
            continue
        sigma = moves[n - 14:n].mean(axis=0)
        prev = rows[n - 1][2]
        ub = max(op, prev) * (1 + sigma)
        lb = min(op, prev) * (1 - sigma)
        tp = (h[a:z + 1] + l[a:z + 1] + c[a:z + 1]) / 3
        vol = np.maximum(v[a:z + 1], 1.0)
        vwap = np.cumsum(tp * vol) / np.cumsum(vol)
        side, entry, t_in = 0, 0.0, 0
        for ci, T in enumerate(checks):
            k = at[T]
            p = c[k]
            vw = vwap[k - a]
            fill = o[k + 1] if k + 1 <= z else c[k]
            if side > 0 and p < max(ub[ci], vw):
                out.append(_trade(t_in, t[min(k + 1, z)], 1, entry, fill, cost, "trailing stop"))
                side = 0
            elif side < 0 and p > min(lb[ci], vw):
                out.append(_trade(t_in, t[min(k + 1, z)], -1, entry, fill, cost, "trailing stop"))
                side = 0
            if side == 0:
                if p > ub[ci]:
                    side, entry, t_in = 1, fill, t[min(k + 1, z)]
                elif p < lb[ci]:
                    side, entry, t_in = -1, fill, t[min(k + 1, z)]
        if side:
            out.append(_trade(t_in, t[z], side, entry, close16, cost, "16:00"))
    return out


# ---------------------------------------------------------------------------------------------- the Bitcoin thesis
def _btc_indicators(c, v):
    mid = I.sma(c, 20)
    sd = I.rolling(c, 20, np.std)
    up, lo = mid + 2 * sd, mid - 2 * sd
    with np.errstate(invalid="ignore", divide="ignore"):
        bbw = (up - lo) / mid
    bbw0 = np.nan_to_num(bbw)
    vf, vs = I.sma(bbw0, 200), I.sma(bbw0, 1000)
    vtop = vs + 2 * I.rolling(bbw0, 1000, np.std)
    volc = I.sma(v, 10) > I.sma(v, 50)
    s = {n: I.sma(c, n) for n in (20, 50, 100, 200)}
    return up, lo, bbw, vf, vs, vtop, volc, s


def btc_thesis(t, o, h, l, c, v, cost: float, s0: int, s1: int, shorts: bool) -> list[dict[str, Any]]:
    """Backtesting of Trading Strategies for Bitcoin (435132310, ETH Zurich thesis), 1-hour candles.
    New Long: the close crosses DOWN through the upper Bollinger(20,2) band with SMA10(volume) > SMA50(volume) and
    close > SMA20; if BBW < VLI top: in a low volatility level (SMA200(BBW) < SMA1000(BBW)) long if SMA50 > SMA200,
    in a high level long unless SMA200 > SMA100 > SMA50; if BBW >= VLI top: long if SMA100 > SMA200, stop at the
    signal candle's low and +1 % locked once the profit is over 3 %. Exit: the close crosses down through the lower
    band with the volume condition; stop 5 % below the entry candle's low; stop-wins +15/20/25/30/35 % after
    20/25/30/35/40 % profit.
    New Short 2 (shorts=True): a close crossing UP through the lower band with BBW < VLI slow arms a short, fired when
    SMA100 crosses below SMA50 and BBW < VLI top (an upper-band cross-down disarms it). Stop at the highest high of
    the last 20 candles (10 once the profit is over 10 %, 5 over 15 %), stop-wins +20/25/30/35 % after 25/30/35/40 %.
    One position; an opposite entry closes and reverses. Signals at a candle's close fill at the next open."""
    up, lo, bbw, vf, vs, vtop, volc, s = _btc_indicators(c, v)
    n = len(c)
    cdt = np.r_[False, (c[:-1] > up[:-1]) & (c[1:] < up[1:])]
    cdb = np.r_[False, (c[:-1] > lo[:-1]) & (c[1:] < lo[1:])]
    cub = np.r_[False, (c[:-1] < lo[:-1]) & (c[1:] > lo[1:])]
    x_mid = np.r_[False, (s[100][:-1] > s[50][:-1]) & (s[100][1:] < s[50][1:])]
    hh = {k: I.highest(h, k) for k in (5, 10, 20)}
    ladder_long = [(0.40, 0.35), (0.35, 0.30), (0.30, 0.25), (0.25, 0.20), (0.20, 0.15)]
    ladder_short = [(0.40, 0.35), (0.35, 0.30), (0.30, 0.25), (0.25, 0.20)]
    first, last = int(np.searchsorted(t, s0)), int(np.searchsorted(t, s1)) - 1
    out = []
    pos = None                       # dict(d, entry, t, stop, extreme)
    armed = False

    def long_signal(i):
        if not (cdt[i] and volc[i] and c[i] > s[20][i]) or np.isnan(vtop[i]) or np.isnan(s[200][i]):
            return None
        if bbw[i] < vtop[i]:
            if vf[i] < vs[i]:
                return "normal" if s[50][i] > s[200][i] else None
            return None if (s[200][i] > s[100][i] > s[50][i]) else "normal"
        return "extreme" if s[100][i] > s[200][i] else None

    def close_pos(k, px, res):
        nonlocal pos
        out.append(_trade(pos["t"], t[k], pos["d"], pos["entry"], px, cost, res))
        pos = None

    for i in range(max(first, 1001), last):
        # 1) manage an open position on candle i (stops known before it opened)
        if pos is not None:
            d = pos["d"]
            pts = path(o[i], h[i], l[i], c[i])
            stop = pos["stop"]
            hit = False
            for q, p in enumerate(pts):
                if (p <= stop) if d > 0 else (p >= stop):
                    close_pos(i, p if q == 0 else stop, "stop")
                    hit = True
                    break
            if not hit:
                prof = (c[i] / pos["entry"] - 1) * d
                if d > 0:
                    for lim, lock in ladder_long:
                        if prof > lim:
                            pos["stop"] = max(pos["stop"], pos["entry"] * (1 + lock))
                            break
                    if pos["extreme"] and prof > 0.03:
                        pos["stop"] = max(pos["stop"], pos["entry"] * 1.01)
                else:
                    per = 5 if prof > 0.15 else 10 if prof > 0.10 else 20
                    pos["stop"] = min(pos["stop"], hh[per][i])
                    for lim, lock in ladder_short:
                        if prof > lim:
                            pos["stop"] = min(pos["stop"], pos["entry"] * (1 - lock))
                            break
        # 2) signals at candle i's close, filled at i+1's open
        if cdt[i]:
            armed = False
        if shorts and cub[i] and not np.isnan(vs[i]) and bbw[i] < vs[i]:
            armed = True
        sig_long = long_signal(i)
        sig_short = shorts and armed and x_mid[i] and not np.isnan(vtop[i]) and bbw[i] < vtop[i]
        if pos is not None and pos["d"] > 0 and cdb[i] and volc[i]:
            close_pos(i + 1, o[i + 1], "rule exit")
        if sig_long and (pos is None or pos["d"] < 0):
            if pos is not None:
                close_pos(i + 1, o[i + 1], "reverse")
            stop = l[i] if sig_long == "extreme" else l[i] * 0.95
            pos = {"d": 1, "entry": o[i + 1], "t": t[i + 1], "stop": stop, "extreme": sig_long == "extreme"}
        elif sig_short and (pos is None or pos["d"] > 0):
            if pos is not None:
                close_pos(i + 1, o[i + 1], "reverse")
            pos = {"d": -1, "entry": o[i + 1], "t": t[i + 1], "stop": hh[20][i], "extreme": False}
            armed = False
    if pos is not None:
        close_pos(last, c[last], "end")
    return out
