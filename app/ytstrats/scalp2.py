"""Scribd scalping round 2 (docs/SCRIBD_SCALPING_ROUND2_STUDY.md), written down before any result.

Signals are read on completed N-minute candles built from 1-minute candles; every trade is filled and managed on the
1-minute candles (neutral OHLC path: green O-L-H-C, red O-H-L-C). ``cost`` is a share of the price paid per side.
Longs are coded; shorts mirror them through ``d``.
"""
from __future__ import annotations

import datetime as dt
import functools
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import ind as I
from .swing import path, uk_dst

H = 3_600_000
DAY = 86_400_000


def us_dst(t_ms: np.ndarray) -> np.ndarray:
    """US daylight saving: 2nd Sunday of March 07:00 UTC -> 1st Sunday of November 06:00 UTC."""
    out = np.zeros(len(t_ms), bool)
    y0 = dt.datetime.fromtimestamp(t_ms[0] / 1000, dt.timezone.utc).year
    y1 = dt.datetime.fromtimestamp(t_ms[-1] / 1000, dt.timezone.utc).year
    for y in range(y0, y1 + 1):
        a = dt.datetime(y, 3, 8, 7, tzinfo=dt.timezone.utc)
        a += dt.timedelta(days=(6 - a.weekday()) % 7)
        z = dt.datetime(y, 11, 1, 6, tzinfo=dt.timezone.utc)
        z += dt.timedelta(days=(6 - z.weekday()) % 7)
        out |= (t_ms >= a.timestamp() * 1000) & (t_ms < z.timestamp() * 1000)
    return out


@dataclass
class M1:
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray

    def __post_init__(self):
        self.ny = self.t - np.where(us_dst(self.t), 4, 5) * H            # New York local ms
        self.ldn = self.t + np.where(uk_dst(self.t), 1, 0) * H           # London local ms


@dataclass
class Bars:
    """N-minute candles with the 1-minute index range [s, e] of each."""
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    s: np.ndarray
    e: np.ndarray
    n: int

    @functools.cached_property
    def end_t(self):
        return self.t + self.n * 60_000


def bars(m: M1, n: int) -> Bars:
    if n == 1:
        idx = np.arange(len(m.t))
        return Bars(m.t, m.o, m.h, m.l, m.c, m.v, idx, idx, 1)
    key = (m.t // (n * 60_000)).astype(np.int64)
    s = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    e = np.r_[s[1:], len(m.t)] - 1
    return Bars(key[s] * n * 60_000.0, m.o[s], np.maximum.reduceat(m.h, s), np.minimum.reduceat(m.l, s), m.c[e],
                np.add.reduceat(m.v, s), s, e, n)


def htf_on(ltf: Bars, htf: Bars, values: np.ndarray) -> np.ndarray:
    """The value of the last HTF candle completed by each LTF candle's close."""
    pos = np.searchsorted(htf.end_t, ltf.end_t, side="right") - 1
    out = np.full(len(ltf.t), np.nan)
    ok = pos >= 0
    out[ok] = values[pos[ok]]
    return out


def vwap_bands(b: Bars, anchor_key: np.ndarray):
    """Volume-weighted price and standard deviation since each anchor (anchor_key changes = new anchor)."""
    tp = (b.h + b.l + b.c) / 3
    v = np.maximum(b.v, 1.0)
    starts = np.flatnonzero(np.r_[True, anchor_key[1:] != anchor_key[:-1]])
    grp = np.repeat(np.arange(len(starts)), np.diff(np.r_[starts, len(tp)]))

    def seg_cumsum(x):
        cs = np.cumsum(x)
        base = np.r_[0.0, cs][starts]                 # running total just before each anchor
        return cs - base[grp]
    a, b2, c2 = seg_cumsum(v), seg_cumsum(v * tp), seg_cumsum(v * tp * tp)
    vw = b2 / a
    sd = np.sqrt(np.maximum(c2 / a - vw ** 2, 0.0))
    return vw, sd


def walk(m: M1, j: int, end: int, d: int, entry: float, stop: float, legs: list[tuple[float, float | None]], cost: float,
         be: tuple[float, float] | None = None, be_after_first: bool = False, exit_at: set[int] | None = None,
         first_pts=None) -> tuple[float, int, str]:
    """Manage a position on 1-minute candles from index j (entry already at `entry`) to `end`.
    legs: [(fraction, target or None)]; be: (profit distance that triggers, new stop offset from entry);
    be_after_first: move the stop to entry once the first target is hit; exit_at: 1-minute indices whose OPEN closes
    everything. Returns (pnl in price per unit, last index, result)."""
    fav = lambda x: x * d  # noqa: E731
    open_legs = [list(x) for x in legs]
    pnl, result = 0.0, "end"

    def close(px, frac):
        return frac * d * (px * (1 - d * cost) - entry * (1 + d * cost))

    for k in range(j, end + 1):
        if exit_at and k in exit_at and k > j:
            pnl += close(m.o[k], sum(f for f, _ in open_legs))
            return pnl, k, "rule exit"
        pts = first_pts if (k == j and first_pts is not None) else path(m.o[k], m.h[k], m.l[k], m.c[k])
        cur = pts[0]
        if k > j and fav(cur) <= fav(stop):
            pnl += close(cur, sum(f for f, _ in open_legs))
            return pnl, k, "stop"
        if k > j:                                                   # a gap through targets fills them at the open
            for leg in list(open_legs):
                if leg[1] is not None and fav(cur) >= fav(leg[1]):
                    pnl += close(cur, leg[0])
                    open_legs.remove(leg)
            if not open_legs:
                return pnl, k, "target"
        for p in pts[1:]:
            if fav(p) < fav(cur):
                if fav(p) <= fav(stop):
                    pnl += close(stop, sum(f for f, _ in open_legs))
                    return pnl, k, "stop" if fav(stop) < fav(entry) else "breakeven/locked"
            elif fav(p) > fav(cur):
                for leg in list(open_legs):
                    if leg[1] is not None and fav(p) >= fav(leg[1]):
                        pnl += close(leg[1], leg[0])
                        open_legs.remove(leg)
                        if be_after_first:
                            stop = max(stop, entry) if d > 0 else min(stop, entry)
                if not open_legs:
                    return pnl, k, "target"
                if be is not None and fav(p) - fav(entry) >= be[0]:
                    ns = entry + d * be[1]
                    stop = max(stop, ns) if d > 0 else min(stop, ns)
            cur = p
    pnl += close(m.c[end], sum(f for f, _ in open_legs))
    return pnl, end, result


def _trade(m, j, k, d, entry, pnl, risk, res):
    return {"t": int(m.t[j]), "exit_t": int(m.t[k]), "dir": int(d), "entry": float(entry), "pnl_px": float(pnl),
            "ret_pct": float(pnl / entry * 100), "r": float(pnl / risk) if risk else None,
            "risk_pct": float(risk / entry * 100) if risk else None, "result": res}


def _bull(b: Bars, i: int, frac: float) -> bool:
    rng = b.h[i] - b.l[i]
    return rng > 0 and b.c[i] > b.o[i] and (b.c[i] - b.o[i]) / rng > frac


def _bear(b: Bars, i: int, frac: float) -> bool:
    rng = b.h[i] - b.l[i]
    return rng > 0 and b.c[i] < b.o[i] and (b.o[i] - b.c[i]) / rng > frac


def _rsi_turn(r: np.ndarray, i: int, d: int, lvl: float) -> bool:
    if i < 3 or np.isnan(r[i]) or np.isnan(r[i - 1]):
        return False
    w = r[i - 2:i + 1]
    return bool((np.nanmin(w) < lvl and r[i] > r[i - 1]) if d > 0 else (np.nanmax(w) > 100 - lvl and r[i] < r[i - 1]))


def _in_window(m: M1, s0: int, s1: int) -> tuple[int, int]:
    return int(np.searchsorted(m.t, s0)), int(np.searchsorted(m.t, s1)) - 1


# ------------------------------------------------------------------------------------------------------------ rules
def xau_vwap_band(m: M1, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """XAUUSD High-Frequency Scalping, Setup 1 (977479117), 3m with a 15m bias. VWAP anchored at 03:00 New York (to
    08:00) and 08:00 New York (to 17:00); trades 03:00-17:00 NY. Long: last 15m close > VWAP and EMA9 > EMA21 (15m);
    a 3m candle reaching VWAP - 1 sigma, bullish with body > 50 % of range, volume above the 3 candles before, RSI(7)
    rising after < 30 in the last 3 candles -> buy the next open. Stop 10 pips under the signal low; 70 % at VWAP,
    30 % at VWAP + 1 sigma; at +8 pips the stop goes to entry + 2 pips; closed at 17:00 NY. Shorts mirror."""
    b3, b15 = bars(m, 3), bars(m, 15)
    ny3 = b3.t - np.where(us_dst(b3.t), 4, 5) * H
    mins3 = ((ny3 % DAY) // 60_000).astype(np.int64)
    nyday = (ny3 // DAY).astype(np.int64)
    anchor = np.where(mins3 >= 480, nyday * 2 + 1, np.where(mins3 >= 180, nyday * 2, -1))
    vw, sd = vwap_bands(b3, anchor)
    ny15 = b15.t - np.where(us_dst(b15.t), 4, 5) * H
    m15 = ((ny15 % DAY) // 60_000).astype(np.int64)
    a15 = np.where(m15 >= 480, (ny15 // DAY) * 2 + 1, np.where(m15 >= 180, (ny15 // DAY) * 2, -1))
    vw15, _ = vwap_bands(b15, a15)
    bias15 = np.where((b15.c > vw15) & (I.ema(b15.c, 9) > I.ema(b15.c, 21)) & (a15 >= 0), 1,
                      np.where((b15.c < vw15) & (I.ema(b15.c, 9) < I.ema(b15.c, 21)) & (a15 >= 0), -1, 0)).astype(float)
    bias = htf_on(b3, b15, bias15)
    rsi = I.rsi(b3.c, 7)
    end_ny = m.ny
    first, last = _in_window(m, s0, s1)
    out, busy = [], -1
    for i in range(4, len(b3.t)):
        j = b3.e[i] + 1
        if j <= busy or j < first or j > last or anchor[i] < 0 or mins3[i] >= 17 * 60 - 3:
            continue
        for d in (1, -1):
            if bias[i] != d:
                continue
            ext = b3.l[i] <= vw[i] - sd[i] if d > 0 else b3.h[i] >= vw[i] + sd[i]
            if not ext or sd[i] <= 0:
                continue
            if not (_bull(b3, i, 0.5) if d > 0 else _bear(b3, i, 0.5)):
                continue
            if not b3.v[i] > b3.v[i - 3:i].mean() or not _rsi_turn(rsi, i, d, 30):
                continue
            entry = m.o[j]
            stop = (b3.l[i] - 10 * pip) if d > 0 else (b3.h[i] + 10 * pip)
            tp1, tp2 = vw[i], vw[i] + d * sd[i]
            if (tp1 - entry) * d <= 0 or (entry - stop) * d <= 0:
                continue
            day_end = int(np.searchsorted(end_ny, (end_ny[j] // DAY) * DAY + 17 * H)) - 1
            pnl, k, res = walk(m, j, min(day_end, last), d, entry, stop, [(0.7, tp1), (0.3, tp2)], cost, be=(8 * pip, 2 * pip))
            out.append(_trade(m, j, k, d, entry, pnl, abs(entry - stop), res))
            busy = k
            break
    return out


def vwap4h_early(m: M1, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """4H Anchored VWAP Scalping, Strategy A (977479114), 3m. VWAP anchored at each 4-hour UTC boundary; signals only
    in its first 90 minutes. Long: a 3m candle reaching VWAP - 1.5 sigma while the period's low is still above the
    previous 4-hour low; bullish with body > 50 %, volume >= 1.5x the previous 20 candles' average, RSI(7) rising
    after < 25 in the last 3 candles -> buy the next open. Stop 10 pips under the signal low; 70 % at VWAP, 30 % at
    VWAP + 1 sigma; closed at the end of the 4-hour period. Shorts mirror."""
    b = bars(m, 3)
    key = (b.t // (4 * H)).astype(np.int64)
    vw, sd = vwap_bands(b, key)
    rsi = I.rsi(b.c, 7)
    b4 = bars(m, 240)
    k4 = (b4.t // (4 * H)).astype(np.int64)
    pos4 = {int(k): i for i, k in enumerate(k4)}
    first, last = _in_window(m, s0, s1)
    out, busy = [], -1
    run_lo = run_hi = None
    prev_key = None
    for i in range(21, len(b.t)):
        if key[i] != prev_key:
            run_lo, run_hi, prev_key = b.l[i], b.h[i], key[i]
        else:
            run_lo, run_hi = min(run_lo, b.l[i]), max(run_hi, b.h[i])
        j = b.e[i] + 1
        if j <= busy or j < first or j > last:
            continue
        if (b.end_t[i] - key[i] * 4 * H) > 90 * 60_000:
            continue
        p = pos4.get(int(key[i]) - 1)
        if p is None or sd[i] <= 0:
            continue
        for d in (1, -1):
            ext = b.l[i] <= vw[i] - 1.5 * sd[i] if d > 0 else b.h[i] >= vw[i] + 1.5 * sd[i]
            held = run_lo > b4.l[p] if d > 0 else run_hi < b4.h[p]
            if not (ext and held and (_bull(b, i, 0.5) if d > 0 else _bear(b, i, 0.5))):
                continue
            if not b.v[i] >= 1.5 * b.v[i - 20:i].mean() or not _rsi_turn(rsi, i, d, 25):
                continue
            entry = m.o[j]
            stop = (b.l[i] - 10 * pip) if d > 0 else (b.h[i] + 10 * pip)
            tp1, tp2 = vw[i], vw[i] + d * sd[i]
            if (tp1 - entry) * d <= 0 or (entry - stop) * d <= 0:
                continue
            period_end = int(np.searchsorted(m.t, (key[i] + 1) * 4 * H)) - 1
            pnl, k, res = walk(m, j, min(period_end, last), d, entry, stop, [(0.7, tp1), (0.3, tp2)], cost)
            out.append(_trade(m, j, k, d, entry, pnl, abs(entry - stop), res))
            busy = k
            break
    return out


def gbpjpy_beast(m: M1, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """GBP/JPY 10 Pips "Beast Mode" (848267234). Hours: 07:00-10:00 London and 08:00-11:00 New York. Long: the last
    completed 5m EMA50 above its value 3 candles earlier; on 1m a candle in the last 3 touched EMA20, RSI(14) between
    40 and 60 and rising, and a bullish engulfing candle -> buy the next open. Stop 11 pips, target 10 pips. Two
    losses in a row end the day. Shorts mirror."""
    b1, b5 = bars(m, 1), bars(m, 5)
    e50 = I.ema(b5.c, 50)
    slope = htf_on(b1, b5, np.sign(e50 - I.shift(e50, 3)))
    e20 = I.ema(m.c, 20)
    rsi = I.rsi(m.c, 14)
    touch = (m.l <= e20) & (m.h >= e20)
    lmin = ((m.ldn % DAY) // 60_000).astype(np.int64)
    nmin = ((m.ny % DAY) // 60_000).astype(np.int64)
    sess = ((lmin >= 420) & (lmin < 600)) | ((nmin >= 480) & (nmin < 660))
    day = (m.ldn // DAY).astype(np.int64)
    first, last = _in_window(m, s0, s1)
    out, busy, losses, lday = [], -1, 0, None
    for i in range(max(first, 60), last):
        if day[i] != lday:
            lday, losses = day[i], 0
        j = i + 1
        if j <= busy or not sess[i] or losses >= 2 or np.isnan(slope[i]) or np.isnan(rsi[i]):
            continue
        for d in (1, -1):
            if slope[i] != d or not touch[i - 2:i + 1].any() or not (40 <= rsi[i] <= 60):
                continue
            if not (rsi[i] > rsi[i - 1] if d > 0 else rsi[i] < rsi[i - 1]):
                continue
            prev_bear, cur_bull = m.c[i - 1] < m.o[i - 1], m.c[i] > m.o[i]
            eng = (prev_bear and cur_bull and m.c[i] > m.o[i - 1] and m.o[i] <= m.c[i - 1]) if d > 0 else \
                  (not prev_bear and m.c[i - 1] > m.o[i - 1] and m.c[i] < m.o[i] and m.c[i] < m.o[i - 1] and m.o[i] >= m.c[i - 1])
            if not eng:
                continue
            entry = m.o[j]
            stop, tp = entry - d * 11 * pip, entry + d * 10 * pip
            day_end = int(np.searchsorted(m.ldn, (m.ldn[j] // DAY + 1) * DAY)) - 1
            pnl, k, res = walk(m, j, min(day_end, last), d, entry, stop, [(1.0, tp)], cost)
            out.append(_trade(m, j, k, d, entry, pnl, 11 * pip, res))
            losses = losses + 1 if pnl < 0 else 0
            busy = k
            break
    return out


def asr_vwap_macd(m: M1, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """ASR VWAP Scalping (760301637), 15m. Daily VWAP anchored at the broker day start (17:00 New York). Long: close >
    EMA200, and on the same candle the close crosses above VWAP and MACD(12,26,9) crosses above its signal -> buy the
    next open. Stop at the signal candle's low; half at 1R, half at 2R. Shorts mirror."""
    b = bars(m, 15)
    ny = b.t - np.where(us_dst(b.t), 4, 5) * H
    anchor = ((ny + 7 * H) // DAY).astype(np.int64)                 # the day starts at 17:00 New York
    vw, _ = vwap_bands(b, anchor)
    e200 = I.ema(b.c, 200)
    mac, sig = I.macd(b.c, 12, 26, 9)
    first, last = _in_window(m, s0, s1)
    out, busy = [], -1
    for i in range(201, len(b.t)):
        j = b.e[i] + 1
        if j <= busy or j < first or j > last:
            continue
        for d in (1, -1):
            trend = (b.c[i] > e200[i]) if d > 0 else (b.c[i] < e200[i])
            vx = (b.c[i - 1] <= vw[i - 1] and b.c[i] > vw[i]) if d > 0 else (b.c[i - 1] >= vw[i - 1] and b.c[i] < vw[i])
            mx = (mac[i - 1] <= sig[i - 1] and mac[i] > sig[i]) if d > 0 else (mac[i - 1] >= sig[i - 1] and mac[i] < sig[i])
            if not (trend and vx and mx):
                continue
            entry = m.o[j]
            stop = b.l[i] if d > 0 else b.h[i]
            risk = (entry - stop) * d
            if risk <= 0:
                continue
            day_end = min(last, j + 3 * 1440)
            pnl, k, res = walk(m, j, day_end, d, entry, stop, [(0.5, entry + d * risk), (0.5, entry + d * 2 * risk)], cost)
            out.append(_trade(m, j, k, d, entry, pnl, risk, res))
            busy = k
            break
    return out


def codetrading_vwap_bb(m: M1, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """VWAP_Scalping_01 notebook (932043376), EURUSD 5m. Daily VWAP anchored at 00:00 UTC. Long: the bodies of the
    signal candle and the 15 before it all above VWAP, close <= lower Bollinger(14, 2), RSI(16) < 45 -> buy the next
    open; stop close - 1.2 ATR(7), target close + 1.8 ATR(7); exit at the next open when RSI(16) >= 90. Shorts
    mirror."""
    b = bars(m, 5)
    vw, _ = vwap_bands(b, (b.t // DAY).astype(np.int64))
    mid = I.sma(b.c, 14)
    sdv = I.rolling(b.c, 14, np.std)
    lo, hi = mid - 2 * sdv, mid + 2 * sdv
    rsi = I.rsi(b.c, 16)
    atr = I.atr(b.h, b.l, b.c, 7)
    above = np.minimum(b.o, b.c) > vw
    below = np.maximum(b.o, b.c) < vw
    first, last = _in_window(m, s0, s1)
    out, busy = [], -1
    for i in range(30, len(b.t)):
        j = b.e[i] + 1
        if j <= busy or j < first or j > last or np.isnan(atr[i]):
            continue
        up, dn = above[i - 15:i + 1].all(), below[i - 15:i + 1].all()
        d = 1 if (up and b.c[i] <= lo[i] and rsi[i] < 45) else -1 if (dn and b.c[i] >= hi[i] and rsi[i] > 55) else 0
        if not d:
            continue
        entry = m.o[j]
        stop, tp = b.c[i] - d * 1.2 * atr[i], b.c[i] + d * 1.8 * atr[i]
        if (entry - stop) * d <= 0 or (tp - entry) * d <= 0:
            continue
        ex = {int(b.e[q] + 1) for q in range(i + 1, min(i + 600, len(b.t))) if (rsi[q] >= 90 if d > 0 else rsi[q] <= 10)}
        pnl, k, res = walk(m, j, min(last, j + 5 * 1440), d, entry, stop, [(1.0, tp)], cost, exit_at=ex)
        out.append(_trade(m, j, k, d, entry, pnl, abs(entry - stop), res))
        busy = k
    return out


def smb_vwap_flip(m: M1, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """SMB Capital Offsides scalping, trigger #2 "VWAP flip" (890912538), Dow (YM) -> US30, 5m. VWAP anchored at 18:00
    New York. Signals 05:00-12:00 NY except 09:30-09:45. Long: the close crosses above VWAP (previous close below) on
    a bullish candle with body >= 60 % of its range -> buy the next open; stop at the lowest low of the 6 candles
    before the signal candle; target 1R. Shorts mirror."""
    b = bars(m, 5)
    ny = b.t - np.where(us_dst(b.t), 4, 5) * H
    mins = ((ny % DAY) // 60_000).astype(np.int64)
    anchor = ((ny + 6 * H) // DAY).astype(np.int64)                 # 18:00 New York
    vw, _ = vwap_bands(b, anchor)
    first, last = _in_window(m, s0, s1)
    out, busy = [], -1
    for i in range(7, len(b.t)):
        j = b.e[i] + 1
        endm = mins[i] + 5
        if j <= busy or j < first or j > last or not (300 < endm <= 720) or (570 < endm <= 585):
            continue
        for d in (1, -1):
            flip = (b.c[i - 1] < vw[i - 1] and b.c[i] > vw[i]) if d > 0 else (b.c[i - 1] > vw[i - 1] and b.c[i] < vw[i])
            body = (b.c[i] - b.o[i]) * d
            rng = b.h[i] - b.l[i]
            if not (flip and rng > 0 and body / rng >= 0.6):
                continue
            entry = m.o[j]
            stop = b.l[i - 6:i].min() if d > 0 else b.h[i - 6:i].max()
            risk = (entry - stop) * d
            if risk <= 0:
                continue
            day_end = min(last, int(np.searchsorted(m.ny, (m.ny[j] // DAY) * DAY + 16 * H)) - 1)
            pnl, k, res = walk(m, j, max(day_end, j), d, entry, stop, [(1.0, entry + d * risk)], cost)
            out.append(_trade(m, j, k, d, entry, pnl, risk, res))
            busy = k
            break
    return out


def five_step_ema(m: M1, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """A 5 Step Scalping Strategy (754883344), 5m with an H1 anchor. Long: last completed H1 EMA8 > EMA21, both
    rising, close above both; on 5m EMA8 > EMA13 > EMA21 and a candle touching EMA8 (the trigger) -> buy stop 3 pips
    above the highest high of the 5 candles before it, live for 6 candles; stop 3 pips under the trigger low; half at
    1R then the stop to breakeven, half at 2R. Shorts mirror."""
    b, h1 = bars(m, 5), bars(m, 60)
    e8h, e21h = I.ema(h1.c, 8), I.ema(h1.c, 21)
    up_h = (e8h > e21h) & (e8h > I.shift(e8h, 1)) & (e21h > I.shift(e21h, 1)) & (h1.c > e8h) & (h1.c > e21h)
    dn_h = (e8h < e21h) & (e8h < I.shift(e8h, 1)) & (e21h < I.shift(e21h, 1)) & (h1.c < e8h) & (h1.c < e21h)
    anchor = htf_on(b, h1, np.where(up_h, 1, np.where(dn_h, -1, 0)).astype(float))
    e8, e13, e21 = I.ema(b.c, 8), I.ema(b.c, 13), I.ema(b.c, 21)
    first, last = _in_window(m, s0, s1)
    out, busy = [], -1
    for i in range(30, len(b.t) - 1):
        if b.e[i] + 1 <= busy or b.e[i] + 1 < first or b.e[i] + 1 > last:
            continue
        for d in (1, -1):
            if anchor[i] != d:
                continue
            stack = (e8[i] > e13[i] > e21[i]) if d > 0 else (e8[i] < e13[i] < e21[i])
            trig = (b.l[i] <= e8[i]) if d > 0 else (b.h[i] >= e8[i])
            if not (stack and trig):
                continue
            lvl = b.h[i - 5:i].max() + 3 * pip if d > 0 else b.l[i - 5:i].min() - 3 * pip
            stop = b.l[i] - 3 * pip if d > 0 else b.h[i] + 3 * pip
            j0, j1 = b.e[i] + 1, min(b.e[min(i + 6, len(b.t) - 1)], last)
            fill = None
            for q in range(j0, j1 + 1):
                pts = path(m.o[q], m.h[q], m.l[q], m.c[q])
                for z, p in enumerate(pts):
                    if (p >= lvl) if d > 0 else (p <= lvl):
                        fill = (q, p if z == 0 else lvl, z, pts)
                        break
                if fill:
                    break
            if not fill:
                continue
            q, entry, z, pts = fill
            risk = (entry - stop) * d
            if risk <= 0:
                continue
            fp = (entry, m.c[q]) if z else pts        # after a stop-entry fill: straight to the close (random-walk check)
            pnl, k, res = walk(m, q, min(last, q + 2 * 1440), d, entry, stop, [(0.5, entry + d * risk), (0.5, entry + d * 2 * risk)],
                               cost, be_after_first=True, first_pts=fp)
            out.append(_trade(m, q, k, d, entry, pnl, risk, res))
            busy = k
            break
    return out


RULES = [
    ("xau_vwap_band", "XAUUSD High-Frequency Scalping, Setup 1 VWAP band rejection (977479117)", ("XAUUSD",), xau_vwap_band, "3m", True),
    ("vwap4h_early", "4H Anchored VWAP Scalping, Strategy A (977479114)", ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"), vwap4h_early, "3m", False),
    ("gbpjpy_beast", "GBP/JPY 10 Pips Scalping, Beast Mode (848267234)", ("GBPJPY",), gbpjpy_beast, "1m", True),
    ("asr_vwap_macd", "ASR VWAP Scalping Trading Strategy (760301637)", ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"), asr_vwap_macd, "15m", False),
    ("codetrading_vwap_bb", "VWAP_Scalping_01 notebook, CodeTrading (932043376)", ("EURUSD",), codetrading_vwap_bb, "5m", True),
    ("smb_vwap_flip", "SMB Capital Offsides scalping: VWAP flip (890912538)", ("US30",), smb_vwap_flip, "5m", True),
    ("five_step_ema", "A 5 Step Scalping Strategy (754883344)", ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"), five_step_ema, "5m", False),
]
