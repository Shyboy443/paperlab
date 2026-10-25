"""The Scribd scalping strategies (operator, 2026-10-20: "test everything and find the best returning winners that
consistently work"), written down as exact rules BEFORE any result (docs/SCRIBD_SCALPING_STUDY.md). Each entry names
its source document, the market the document names (only that market is tested), its timeframe and its rule.

A strategy function takes a Bars object (completed candles of one asset at its timeframe, UTC) and the asset's pip
size, and returns a scalp.Plan. Gold: 1 pip = 0.10 USD (confirmed by the documents' own examples, e.g. 4 USD = 40 pips).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import numpy as np

from . import ind as I
from .scalp import Plan

MAJORS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD")
PIP = {**{s: 0.0001 for s in MAJORS}, "USDJPY": 0.01, "AUDJPY": 0.01, "GBPJPY": 0.01, "GBPAUD": 0.0001, "XAUUSD": 0.10}


class Bars:
    def __init__(self, a: np.ndarray, tf_min: int):
        self.t, self.o, self.h, self.l, self.c, self.v = (a[:, k] for k in range(6))
        self.tf = tf_min
        self.n = len(self.c)

    def minute_of_day(self, close: bool = True) -> np.ndarray:
        """UTC minute of the day at which each candle closes (close=True) or opens."""
        m = (self.t // 60_000).astype(np.int64) + (self.tf if close else 0)
        return m % 1440

    def htf(self, minutes: int, fn: Callable[[np.ndarray, np.ndarray, np.ndarray, np.ndarray], np.ndarray]) -> np.ndarray:
        """A higher-timeframe series (computed on COMPLETED higher-timeframe candles) aligned to these candles: at each
        candle, the value from the last higher-timeframe candle that had closed by this candle's close."""
        step = minutes * 60_000
        key = (self.t // step).astype(np.int64)
        starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
        ends = np.r_[starts[1:], self.n]
        ho = self.o[starts]
        hh = np.array([self.h[s:e].max() for s, e in zip(starts, ends)])
        hl = np.array([self.l[s:e].min() for s, e in zip(starts, ends)])
        hc = self.c[ends - 1]
        vals = fn(ho, hh, hl, hc)
        out = np.full(self.n, np.nan)
        closes_at = (key[starts] + 1) * step                      # when each higher candle closes
        my_close = self.t + self.tf * 60_000
        pos = np.searchsorted(closes_at, my_close, side="right") - 1
        ok = pos >= 0
        out[ok] = vals[pos[ok]]
        return out


def _s(long, short):
    long, short = np.nan_to_num(long).astype(bool), np.nan_to_num(short).astype(bool)
    return np.where(long, 1, np.where(short, -1, 0))


def first(x: np.ndarray) -> np.ndarray:
    """True only on the first candle of each run of True."""
    x = np.nan_to_num(x).astype(bool)
    return x & ~np.r_[False, x[:-1]]


def recent(x: np.ndarray, n: int) -> np.ndarray:
    return I.rolling(np.nan_to_num(x).astype(float), n, np.max) > 0


def fractals(h, l):
    """Bill Williams 5-candle fractals, CONFIRMED two candles later: (up_level, down_level) at the confirming candle."""
    n = len(h)
    up, dn = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(4, n):
        m = i - 2
        if h[m] > max(h[m - 2], h[m - 1], h[m + 1], h[m + 2]):
            up[i] = h[m]
        if l[m] < min(l[m - 2], l[m - 1], l[m + 1], l[m + 2]):
            dn[i] = l[m]
    return up, dn


def ffill(x):
    out = x.copy()
    for i in range(1, len(out)):
        if np.isnan(out[i]):
            out[i] = out[i - 1]
    return out


def williams_r(h, l, c, n):
    hh, ll = I.highest(h, n), I.lowest(l, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(hh > ll, -100 * (hh - c) / (hh - ll), -50.0)


def adx(h, l, c, n):
    up, dn = np.diff(h, prepend=np.nan), -np.diff(l, prepend=np.nan)
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = I.true_range(h, l, c)
    atr = I.rma(tr, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi, ndi = 100 * I.rma(pdm, n) / atr, 100 * I.rma(ndm, n) / atr
        dx = 100 * np.abs(pdi - ndi) / (pdi + ndi)
    return I.rma(np.nan_to_num(dx), n)


def boll(c, n, k):
    m = I.sma(c, n)
    sd = I.rolling(c, n, np.std)
    return m, m + k * sd, m - k * sd


def session(b: Bars, *windows) -> np.ndarray:
    m = b.minute_of_day()
    out = np.zeros(b.n, bool)
    for a, z in windows:
        out |= (m > a) & (m <= z)
    return out


def swing_stop(b: Bars, d_sig: np.ndarray, n: int, pad: float) -> np.ndarray:
    lo, hi = I.lowest(b.l, n), I.highest(b.h, n)
    return np.where(d_sig > 0, lo - pad, np.where(d_sig < 0, hi + pad, np.nan))


# ---------------------------------------------------------------------------------------------------------------------------
def s01_sma_clear(b: Bars, pip: float) -> Plan:
    """#1 / #20a 5 Minute Forex Scalping: SMA50 side; a candle closing fully clear of SMA10 and SMA15; stop at SMA50;
    exit on the opposite signal (no target given)."""
    s50, s10, s15 = I.sma(b.c, 50), I.sma(b.c, 10), I.sma(b.c, 15)
    with np.errstate(invalid="ignore"):
        above = b.l > np.fmax(s10, s15)
        below = b.h < np.fmin(s10, s15)
        sig = _s(first(above) & (b.c > s50), first(below) & (b.c < s50))
    return Plan(sig, stop=np.where(sig != 0, s50, np.nan), reverse=True)


def s04_trend_shift(b: Bars, pip: float) -> Plan:
    """#4 144 Trend Shift: SMMA5 crosses LWMA144 with price within 10 pips of the LWMA; stop at the nearest confirmed
    fractal beyond the entry; target 2R."""
    sm, lw = I.smma(b.c, 5), I.wma(b.c, 144)
    with np.errstate(invalid="ignore"):
        near = np.abs(b.c - lw) <= 10 * pip
    sig = _s(I.cross_up(sm, lw) & near, I.cross_down(sm, lw) & near)
    up, dn = fractals(b.h, b.l)
    lo, hi = ffill(dn), ffill(up)
    return Plan(sig, stop=np.where(sig > 0, lo, np.where(sig < 0, hi, np.nan)), tp_r=2.0)


def s06_sma70_stoch(b: Bars, pip: float) -> Plan:
    """#6 Easy 1-Minute: SMA70 trend; Stochastic(14,3,3) %K crosses up through 20 (down through 80); no exit given ->
    stop-and-reverse on the opposite signal."""
    s70 = I.sma(b.c, 70)
    k, _ = I.stoch(b.h, b.l, b.c, 14, 3, 3)
    with np.errstate(invalid="ignore"):
        sig = _s((b.c > s70) & I.cross_up(k, 20), (b.c < s70) & I.cross_down(k, 80))
    return Plan(sig, reverse=True)


def s07_smma_fractal(b: Bars, pip: float) -> Plan:
    """#7 5-Minute Scalping (decoded): SMMA 21 > 50 > 200 and price above them; enter after a bullish (down) fractal
    is confirmed; stop beyond the fractal; target 2R."""
    a, m, z = I.smma(b.c, 21), I.smma(b.c, 50), I.smma(b.c, 200)
    up, dn = fractals(b.h, b.l)
    with np.errstate(invalid="ignore"):
        bull = (a > m) & (m > z) & (b.c > a)
        bear = (a < m) & (m < z) & (b.c < a)
        sig = _s(bull & ~np.isnan(dn), bear & ~np.isnan(up))
    return Plan(sig, stop=np.where(sig > 0, dn, np.where(sig < 0, up, np.nan)), tp_r=2.0)


def s10b_ema_stoch(b: Bars, pip: float) -> Plan:
    """#10b 1-Minute EMA 50/200 + Stochastic: price beyond both EMAs; %K crosses 20 / 80; stop 1.5 pips beyond the
    10-candle swing; target 1.5R."""
    e50, e200 = I.ema(b.c, 50), I.ema(b.c, 200)
    k, _ = I.stoch(b.h, b.l, b.c)
    with np.errstate(invalid="ignore"):
        sig = _s((b.c > e50) & (b.c > e200) & I.cross_up(k, 20), (b.c < e50) & (b.c < e200) & I.cross_down(k, 80))
    return Plan(sig, stop=swing_stop(b, sig, 10, 1.5 * pip), tp_r=1.5)


def s11_ema_retest_stoch(b: Bars, pip: float) -> Plan:
    """#11 1-Minute Forex Scalping Guide: London + New York (07:00-16:00 UTC); EMA50 vs EMA100; a retest of either EMA in
    the last 5 candles; Stochastic(5,3,3) breaks 20 / 80; stop 2.5 pips beyond the 10-candle swing; target 12.5 pips."""
    e50, e100 = I.ema(b.c, 50), I.ema(b.c, 100)
    k, _ = I.stoch(b.h, b.l, b.c, 5, 3, 3)
    with np.errstate(invalid="ignore"):
        touch_up = recent((b.l <= np.fmax(e50, e100)) & (b.h >= np.fmin(e50, e100)), 5)
        sig = _s((e50 > e100) & touch_up & I.cross_up(k, 20), (e50 < e100) & touch_up & I.cross_down(k, 80))
    sig = np.where(session(b, (7 * 60, 16 * 60)), sig, 0)
    return Plan(sig, stop=swing_stop(b, sig, 10, 2.5 * pip), tp_dist=12.5 * pip)


def s12a_ema8_21(b: Bars, pip: float) -> Plan:
    """#12a Quick Guide 5m: EMA8 crosses EMA21 -> trade the trend; exit on a close back through the EMA21 (trailing)."""
    e8, e21 = I.ema(b.c, 8), I.ema(b.c, 21)
    sig = _s(I.cross_up(e8, e21), I.cross_down(e8, e21))
    with np.errstate(invalid="ignore"):
        return Plan(sig, exit_long=b.c < e21, exit_short=b.c > e21)


def s13_four_screen(b: Bars, pip: float) -> Plan:
    """#13 Price Action Scalper on 5-minute candles: at each full hour, the just-closed M5, M15, M30 and H1 candles are all
    bullish (bearish) -> buy-stop 3 pips above (sell-stop below) the close, valid for the first 5 minutes; stop 2 pips
    beyond the previous H1 candle; target 2.5x the stop."""
    green = lambda o, h, l, c: (c > o).astype(float)  # noqa: E731
    red = lambda o, h, l, c: (c < o).astype(float)  # noqa: E731
    hour_close = b.minute_of_day() % 60 == 0
    g = [b.htf(m, green) for m in (15, 30, 60)]
    r = [b.htf(m, red) for m in (15, 30, 60)]
    with np.errstate(invalid="ignore"):
        bull = hour_close & (b.c > b.o) & (g[0] == 1) & (g[1] == 1) & (g[2] == 1)
        bear = hour_close & (b.c < b.o) & (r[0] == 1) & (r[1] == 1) & (r[2] == 1)
    sig = _s(bull, bear)
    hlo = b.htf(60, lambda o, h, l, c: l)
    hhi = b.htf(60, lambda o, h, l, c: h)
    entry = np.where(sig > 0, b.c + 3 * pip, np.where(sig < 0, b.c - 3 * pip, np.nan))
    stop = np.where(sig > 0, hlo - 2 * pip, np.where(sig < 0, hhi + 2 * pip, np.nan))
    return Plan(sig, stop=stop, tp_r=2.5, entry_px=entry)


def s14_bb_stoch_eurusd(b: Bars, pip: float) -> Plan:
    """#14 Bollinger(20,2)+(20,1.5) + Stochastic(5,3,3), EURUSD M5: a close beyond the 2.0 band with Stochastic beyond
    80/20, then the next candle closes back inside both bands -> enter against the move; TP 3 pips, SL 2 pips."""
    _, u2, l2 = boll(b.c, 20, 2.0)
    _, u15, l15 = boll(b.c, 20, 1.5)
    k, _ = I.stoch(b.h, b.l, b.c, 5, 3, 3)
    pc, pk = I.shift(b.c, 1), I.shift(k, 1)
    pu2, pl2 = I.shift(u2, 1), I.shift(l2, 1)
    with np.errstate(invalid="ignore"):
        sell = (pc > pu2) & (pk > 80) & (b.c < u15)
        buy = (pc < pl2) & (pk < 20) & (b.c > l15)
    return Plan(_s(buy, sell), stop_dist=2 * pip, tp_dist=3 * pip)


def s16_macd_stoch(tp_pips: float):
    def fn(b: Bars, pip: float) -> Plan:
        """#16 M1/M5 Forex Scalping: MACD(12,26,9) line > 0 and Stochastic(8,3,3) crosses back above 20 (sell mirror);
        stop 1 pip beyond the 10-candle swing; target 15 pips (M1) / 25 pips (M5)."""
        m, _ = I.macd(b.c)
        k, _ = I.stoch(b.h, b.l, b.c, 8, 3, 3)
        with np.errstate(invalid="ignore"):
            sig = _s((m > 0) & I.cross_up(k, 20), (m < 0) & I.cross_down(k, 80))
        return Plan(sig, stop=swing_stop(b, sig, 10, pip), tp_dist=tp_pips * pip)
    return fn


def _et_offset_hours(t_ms: np.ndarray) -> np.ndarray:
    """UTC -> US Eastern offset (-4 during US daylight saving, else -5)."""
    out = np.full(len(t_ms), -5.0)
    y0 = datetime.fromtimestamp(t_ms[0] / 1000, timezone.utc).year
    y1 = datetime.fromtimestamp(t_ms[-1] / 1000, timezone.utc).year
    for y in range(y0, y1 + 1):
        mar = datetime(y, 3, 8, 7, tzinfo=timezone.utc)
        mar += timedelta(days=(6 - mar.weekday()) % 7)
        nov = datetime(y, 11, 1, 6, tzinfo=timezone.utc)
        nov += timedelta(days=(6 - nov.weekday()) % 7)
        out[(t_ms >= mar.timestamp() * 1000) & (t_ms < nov.timestamp() * 1000)] = -4.0
    return out


def s17a_dead_time(b: Bars, pip: float) -> Plan:
    """#17a Fielder dead-time range (H1): range of the 15:00 and 16:00 ET candles; at the 17:00 ET open go long below
    the midpoint, short above; target 12.5 pips, stop 12.5 pips (1:1)."""
    et_hour = ((b.t / 3_600_000 + _et_offset_hours(b.t)) % 24).astype(int)
    sig = np.zeros(b.n, int)
    for i in range(2, b.n - 1):
        if et_hour[i] == 16 and et_hour[i - 1] == 15:              # the 16:00 candle closes at 17:00
            mid = (max(b.h[i], b.h[i - 1]) + min(b.l[i], b.l[i - 1])) / 2
            sig[i] = 1 if b.c[i] < mid else -1
    return Plan(sig, stop_dist=12.5 * pip, tp_dist=12.5 * pip)


def s17c_session_breakout(prev_bar: bool):
    def fn(b: Bars, pip: float) -> Plan:
        """#17c / #17d Fielder session-open breakout on 15-minute candles: in the first hour after 19:00 / 02:00 / 03:00 /
        08:00 ET, a stop entry at the day's high/low so far (17c) or the previous H1 candle's (17d); stop 1 pip beyond
        the previous 15-minute candle or 10 pips (the smaller); target 12.5 pips; out at the end of that hour."""
        off = _et_offset_hours(b.t)
        et_min = (((b.t / 60_000) + off * 60) % 1440).astype(int)
        opens = (19 * 60, 2 * 60, 3 * 60, 8 * 60)
        in_first_hour = np.zeros(b.n, bool)
        for a in opens:
            in_first_hour |= ((et_min - a) % 1440) < 60
        # the FX day starts at 17:00 ET: running high / low since then, up to the previous candle
        day_key = ((b.t / 60_000 + off * 60 - 17 * 60) // 1440).astype(np.int64)
        hi_so_far, lo_so_far = np.full(b.n, np.nan), np.full(b.n, np.nan)
        cur_k, hi, lo = None, -np.inf, np.inf
        for i in range(b.n):
            if day_key[i] != cur_k:
                cur_k, hi, lo = day_key[i], -np.inf, np.inf
            hi_so_far[i], lo_so_far[i] = (hi if np.isfinite(hi) else np.nan), (lo if np.isfinite(lo) else np.nan)
            hi, lo = max(hi, b.h[i]), min(lo, b.l[i])
        if prev_bar:
            hi_lvl = b.htf(60, lambda o, h, l, c: h)
            lo_lvl = b.htf(60, lambda o, h, l, c: l)
        else:
            hi_lvl, lo_lvl = hi_so_far, lo_so_far
        nxt_in = np.r_[in_first_hour[1:], False]
        # signal at candle i -> stop entry on candle i+1 at the level (both sides armed: take the long if both)
        sig = np.zeros(b.n, int)
        entry = np.full(b.n, np.nan)
        stop = np.full(b.n, np.nan)
        for i in range(b.n - 1):
            if not nxt_in[i] or np.isnan(hi_lvl[i]) or np.isnan(lo_lvl[i]):
                continue
            up_hit = b.h[i + 1] >= hi_lvl[i] > b.c[i]
            dn_hit = b.l[i + 1] <= lo_lvl[i] < b.c[i]
            if up_hit and dn_hit:                       # an OCO pair: the side the candle's path reaches first
                up_hit = b.c[i + 1] < b.o[i + 1]        # a red candle goes up first (open-high-low-close)
                dn_hit = not up_hit
            if up_hit:
                sig[i], entry[i] = 1, hi_lvl[i]
                stop[i] = max(b.l[i] - pip, hi_lvl[i] - 10 * pip)
            elif dn_hit:
                sig[i], entry[i] = -1, lo_lvl[i]
                stop[i] = min(b.h[i] + pip, lo_lvl[i] + 10 * pip)
        return Plan(sig, stop=stop, tp_dist=12.5 * pip, entry_px=entry, max_hold=4)
    return fn


def s19_btc_mfi(b: Bars, pip: float) -> Plan:
    """#19 Crypto day trading (Bitcoin), 5m, long only: MFI(3) = 100 readings counted per UTC day; on the third or a
    later one, if the candle is bullish and closes in the top quarter of its range -> buy; stop at the day's low so
    far; exit after 30 minutes."""
    mfi = I.mfi(b.h, b.l, b.c, b.v, 3)
    day = (b.t // 86_400_000).astype(np.int64)
    sig = np.zeros(b.n, int)
    stop = np.full(b.n, np.nan)
    cnt, cur, lo = 0, None, np.inf
    for i in range(b.n):
        if day[i] != cur:
            cur, cnt, lo = day[i], 0, np.inf
        lo = min(lo, b.l[i])
        if mfi[i] >= 99.999:
            cnt += 1
            rng = b.h[i] - b.l[i]
            if cnt >= 3 and b.c[i] > b.o[i] and rng > 0 and (b.c[i] - b.l[i]) >= 0.75 * rng:
                sig[i], stop[i] = 1, lo
    return Plan(sig, stop=stop, max_hold=6)


def s23a_fractal_sma50(b: Bars, pip: float) -> Plan:
    """#23a Fractal scalping: above SMA50 take bullish (down) fractals only, below take bearish; enter when confirmed;
    stop beyond the fractal; exit on the next opposite fractal."""
    s50 = I.sma(b.c, 50)
    up, dn = fractals(b.h, b.l)
    with np.errstate(invalid="ignore"):
        sig = _s((b.c > s50) & ~np.isnan(dn), (b.c < s50) & ~np.isnan(up))
    return Plan(sig, stop=np.where(sig > 0, dn, np.where(sig < 0, up, np.nan)), exit_long=~np.isnan(up), exit_short=~np.isnan(dn))


def s25_sheriff(b: Bars, pip: float) -> Plan:
    """#25 Scalping Sheriff (crypto, M15): a bearish candle closes below the lower Bollinger(20,2) with RSI(7) < 20 and
    Stochastic(14,3,3) < 20 -> buy (sell mirror); no exit given -> stop-and-reverse on the opposite signal."""
    _, u, lo = boll(b.c, 20, 2.0)
    r = I.rsi(b.c, 7)
    k, _ = I.stoch(b.h, b.l, b.c)
    with np.errstate(invalid="ignore"):
        sig = _s((b.c < b.o) & (b.c < lo) & (r < 20) & (k < 20), (b.c > b.o) & (b.c > u) & (r > 80) & (k > 80))
    return Plan(sig, reverse=True)


def s28_adx_bb(b: Bars, pip: float) -> Plan:
    """#28 1 Min ADX + Bollinger: after ADX(20) dips below 15, the first close above the upper (below the lower)
    Bollinger(20,2) band -> enter; stop at the opposite band, trailed along it; exit when ADX crosses above 50."""
    a = adx(b.h, b.l, b.c, 20)
    _, u, lo = boll(b.c, 20, 2.0)
    sig = np.zeros(b.n, int)
    armed = False
    for i in range(b.n):
        if np.isnan(a[i]) or np.isnan(u[i]):
            continue
        if a[i] < 15:
            armed = True
        elif armed and b.c[i] > u[i]:
            sig[i], armed = 1, False
        elif armed and b.c[i] < lo[i]:
            sig[i], armed = -1, False
    with np.errstate(invalid="ignore"):
        ex = I.cross_up(a, 50)
    return Plan(sig, stop=np.where(sig > 0, lo, np.where(sig < 0, u, np.nan)), trail_long=lo, trail_short=u,
                exit_long=ex, exit_short=ex)


def s30a_bb_range(b: Bars, pip: float) -> Plan:
    """#30a Bollinger range scalp (5m): only while the band width is below its 100-candle median; a candle touching the
    lower band and closing back inside -> buy (upper -> sell); stop 10 pips; exit at the opposite band."""
    m, u, lo = boll(b.c, 20, 2.0)
    width = u - lo
    flat = width < I.rolling(width, 100, np.nanmedian)
    with np.errstate(invalid="ignore"):
        sig = _s(flat & (b.l <= lo) & (b.c > lo), flat & (b.h >= u) & (b.c < u))
    return Plan(sig, stop_dist=10 * pip, exit_long=b.h >= u, exit_short=b.l <= lo)


def s30b_momo(b: Bars, pip: float) -> Plan:
    """#30b / G13b 5-minute Momo: a close crossing the EMA20 with the MACD histogram turned to the same side within the
    last 5 candles; stop 2 pips (20 points) beyond the EMA; half off at +1R with the stop to breakeven; the rest
    trailed at EMA20 -/+ 1.5 pips."""
    e = I.ema(b.c, 20)
    m, s = I.macd(b.c)
    hist = m - s
    with np.errstate(invalid="ignore"):
        turned_up = recent(I.cross_up(hist, 0), 5) & (hist > 0)
        turned_dn = recent(I.cross_down(hist, 0), 5) & (hist < 0)
        sig = _s(I.cross_up(b.c, e) & turned_up, I.cross_down(b.c, e) & turned_dn)
    stop = np.where(sig > 0, e - 2 * pip, np.where(sig < 0, e + 2 * pip, np.nan))
    return Plan(sig, stop=stop, half_at_r=1.0, trail_long=e - 1.5 * pip, trail_short=e + 1.5 * pip)


def s31_psar_macd(tp_pips: float):
    def fn(b: Bars, pip: float) -> Plan:
        """#31 5 min Parabolic SAR + MACD: SAR(0.1, 0.11) below price, a close crossing above the SMA220, MACD(5,8,9)
        histogram above 0 -> buy (sell mirror); stop 11 pips; target per pair (GBPUSD 10, EURUSD 8, AUDUSD 6)."""
        sar, sd = I.psar(b.h, b.l, 0.1, 0.1, 0.11)
        s220 = I.sma(b.c, 220)
        m, s = I.macd(b.c, 5, 8, 9)
        with np.errstate(invalid="ignore"):
            sig = _s((sd > 0) & I.cross_up(b.c, s220) & (m - s > 0), (sd < 0) & I.cross_down(b.c, s220) & (m - s < 0))
        return Plan(sig, stop_dist=11 * pip, tp_dist=tp_pips * pip)
    return fn


def s35_lazy_river(b: Bars, pip: float) -> Plan:
    """#35 Lazy River (5m): EMA20 > EMA50, both rising, price pulled back to touch an EMA within the last 6 candles with
    at most 3 closes below the EMA20, then a close back above the EMA20 -> buy; stop 3.5 pips beyond the pullback low;
    half off at 1:1 (stop NOT moved); the rest trailed beyond each new confirmed 3-candle swing."""
    e20, e50 = I.ema(b.c, 20), I.ema(b.c, 50)
    with np.errstate(invalid="ignore"):
        up = (e20 > e50) & (e20 > I.shift(e20, 3)) & (e50 > I.shift(e50, 3))
        dn = (e20 < e50) & (e20 < I.shift(e20, 3)) & (e50 < I.shift(e50, 3))
        touch_l = recent(b.l <= e20, 6)
        touch_s = recent(b.h >= e20, 6)
        below = I.rolling((b.c < e20).astype(float), 4, np.sum) <= 3
        above = I.rolling((b.c > e20).astype(float), 4, np.sum) <= 3
        sig = _s(up & touch_l & below & I.cross_up(b.c, e20), dn & touch_s & above & I.cross_down(b.c, e20))
    lo6, hi6 = I.lowest(b.l, 6), I.highest(b.h, 6)
    stop = np.where(sig > 0, lo6 - 3.5 * pip, np.where(sig < 0, hi6 + 3.5 * pip, np.nan))
    # 3-candle swings confirmed one candle later
    swl = np.where((I.shift(b.l, 1) < I.shift(b.l, 2)) & (I.shift(b.l, 1) < b.l), I.shift(b.l, 1) - 3.5 * pip, np.nan)
    swh = np.where((I.shift(b.h, 1) > I.shift(b.h, 2)) & (I.shift(b.h, 1) > b.h), I.shift(b.h, 1) + 3.5 * pip, np.nan)
    p = Plan(sig, stop=stop, half_at_r=1.0, trail_long=ffill(swl), trail_short=ffill(swh))
    p.extra["no_breakeven"] = True
    return p


# -- gold (XAUUSD) ---------------------------------------------------------------------------------------------------------
def g05_ema_stoch5(b: Bars, pip: float) -> Plan:
    """G5/G15 Teknik Rahsia Scalping Gold (M1, 06:00-12:00 UTC): EMA50 vs EMA100; Stochastic(5,3,3) crosses 20 / 80;
    stop 2.5 pips; target 7.5 pips."""
    e50, e100 = I.ema(b.c, 50), I.ema(b.c, 100)
    k, _ = I.stoch(b.h, b.l, b.c, 5, 3, 3)
    with np.errstate(invalid="ignore"):
        sig = _s((e50 > e100) & I.cross_up(k, 20), (e50 < e100) & I.cross_down(k, 80))
    sig = np.where(session(b, (6 * 60, 12 * 60)), sig, 0)
    return Plan(sig, stop_dist=2.5 * pip, tp_dist=7.5 * pip)


def g06_ema14_50(b: Bars, pip: float) -> Plan:
    """G6 Waqar 14 & 50 EMA (15m, USDJPY / AUDJPY / GBPJPY): EMA14 crosses EMA50 after clear spacing (|EMA14-EMA50| >
    0.5 ATR in the 10 candles before); target 15 pips, stop 15 pips."""
    e14, e50 = I.ema(b.c, 14), I.ema(b.c, 50)
    a = I.atr(b.h, b.l, b.c)
    with np.errstate(invalid="ignore"):
        spaced = recent(np.abs(e14 - e50) > 0.5 * a, 10)
    sig = _s(I.cross_up(e14, e50) & spaced, I.cross_down(e14, e50) & spaced)
    return Plan(sig, stop_dist=15 * pip, tp_dist=15 * pip)


def g08_ema9_50_rsi_stoch(b: Bars, pip: float) -> Plan:
    """G8 High-Probability Gold Scalping (M1/M5, 07:00-16:00 UTC): price > EMA50, EMA9 > EMA50, RSI(14) crossed above 30
    and Stochastic(14,3,3) crossed above 20 within the last 5 candles, and a close back above the EMA9 after touching
    it; stop beyond the 10-candle swing; target 10 pips."""
    e9, e50 = I.ema(b.c, 9), I.ema(b.c, 50)
    r = I.rsi(b.c, 14)
    k, _ = I.stoch(b.h, b.l, b.c)
    with np.errstate(invalid="ignore"):
        up = (b.c > e50) & (e9 > e50) & recent(I.cross_up(r, 30), 5) & recent(I.cross_up(k, 20), 5) & recent(b.l <= e9, 3) & I.cross_up(b.c, e9)
        dn = (b.c < e50) & (e9 < e50) & recent(I.cross_down(r, 70), 5) & recent(I.cross_down(k, 80), 5) & recent(b.h >= e9, 3) & I.cross_down(b.c, e9)
    sig = np.where(session(b, (7 * 60, 16 * 60)), _s(up, dn), 0)
    return Plan(sig, stop=swing_stop(b, sig, 10, 0.0), tp_dist=10 * pip)


def _pine_supertrend(src, h, l, c, factor, atr_len):
    """The Pine function in G9 exactly (direction -1 = up, the line then is the lower band)."""
    a = I.atr(h, l, c, atr_len)
    n = len(c)
    sup = np.full(n, np.nan)
    dirn = np.full(n, np.nan)
    prev_up = prev_lo = prev_sup = 0.0
    for i in range(n):
        if np.isnan(a[i]):
            continue
        ub, lb = src[i] + factor * a[i], src[i] - factor * a[i]
        ub = ub if (ub < prev_up or (i and c[i - 1] > prev_up)) else prev_up
        lb = lb if (lb > prev_lo or (i and c[i - 1] < prev_lo)) else prev_lo
        if i == 0 or np.isnan(a[i - 1]):
            d = 1
        elif prev_sup == prev_up:
            d = -1 if c[i] > ub else 1
        else:
            d = 1 if c[i] < lb else -1
        s = lb if d == -1 else ub
        sup[i], dirn[i] = s, d
        prev_up, prev_lo, prev_sup = ub, lb, s
    return sup, dirn


def g09_pine_supertrend(b: Bars, pip: float) -> Plan:
    """G9 'Gold Scalping (Anti-Repainting)' Pine strategy, exact: crossover(close, supertrend(4, 11)) and close >= SMA13
    and close >= the 30-minute SMA13 -> long (short mirror); entries reverse; TP 100 points (1.00 USD), SL 50 points."""
    st, _ = _pine_supertrend(b.c, b.h, b.l, b.c, 4.0, 11)
    s13 = I.sma(b.c, 13)
    # request.security(..., "30", ta.sma(close, 13)) without lookahead: the forming 30m candle's SMA13 = current close
    step = 30 * 60_000
    key = (b.t // step).astype(np.int64)
    last30 = {}
    htf_sma = np.full(b.n, np.nan)
    closes: list[float] = []
    cur = None
    for i in range(b.n):
        if key[i] != cur:
            if cur is not None:
                closes.append(last30[cur])
            cur = key[i]
        last30[cur] = b.c[i]
        window = closes[-12:] + [b.c[i]]
        if len(window) == 13:
            htf_sma[i] = float(np.mean(window))
    with np.errstate(invalid="ignore"):
        long = I.cross_up(b.c, st) & (b.c >= s13) & (b.c >= htf_sma)
        short = I.cross_down(b.c, st) & (b.c <= s13) & (b.c <= htf_sma)
    return Plan(_s(long, short), stop_dist=0.50, tp_dist=1.00, reverse=True)


def g10_wpr(b: Bars, pip: float) -> Plan:
    """G10 Gold scalping Rules (M1): above EMA50 (not flat) Williams %R(9) crosses above -20 on a green candle -> buy
    (below, crosses -80 on a red candle -> sell); stop at the entry candle's open; target 2R."""
    e50 = I.ema(b.c, 50)
    wr = williams_r(b.h, b.l, b.c, 9)
    a = I.atr(b.h, b.l, b.c)
    with np.errstate(invalid="ignore"):
        sloped = np.abs(e50 - I.shift(e50, 10)) >= 0.1 * a
        sig = _s(sloped & (b.c > e50) & I.cross_up(wr, -20) & (b.c > b.o), sloped & (b.c < e50) & I.cross_down(wr, -80) & (b.c < b.o))
    return Plan(sig, stop=np.where(sig != 0, b.o, np.nan), tp_r=2.0)


def g12_pine_ema5_20(b: Bars, pip: float) -> Plan:
    """G12 'XAUUSD Scalping with 5 & 20 EMA' Pine strategy, exact: long on crossover(EMA5, EMA20), close it on the
    crossunder; long only."""
    e5, e20 = I.ema(b.c, 5), I.ema(b.c, 20)
    return Plan(_s(I.cross_up(e5, e20), np.zeros(b.n)), exit_long=I.cross_down(e5, e20))


def g13c_ema9_20_rsi_macd(b: Bars, pip: float) -> Plan:
    """G13c 5-Minute Gold: EMA9 crossed above EMA20 and MACD crossed above its signal within the last 3 candles, RSI(14)
    > 50 -> buy; exit when EMA9 < EMA20, RSI < 50 and MACD < signal (sell mirror)."""
    e9, e20 = I.ema(b.c, 9), I.ema(b.c, 20)
    r = I.rsi(b.c, 14)
    m, s = I.macd(b.c)
    with np.errstate(invalid="ignore"):
        buy = recent(I.cross_up(e9, e20), 3) & recent(I.cross_up(m, s), 3) & (r > 50) & (e9 > e20) & (m > s)
        sell = recent(I.cross_down(e9, e20), 3) & recent(I.cross_down(m, s), 3) & (r < 50) & (e9 < e20) & (m < s)
        ex_l = (e9 < e20) & (r < 50) & (m < s)
        ex_s = (e9 > e20) & (r > 50) & (m > s)
    return Plan(_s(first(buy), first(sell)), exit_long=ex_l, exit_short=ex_s)


@dataclass(frozen=True)
class Strat:
    key: str
    doc: str              # Scribd document id + title
    assets: tuple
    tf: int               # minutes
    fn: Callable
    note: str = ""


S = Strat
STRATEGIES: tuple[Strat, ...] = (
    S("s01_sma_clear", "440084498 5 Minute Forex Scalping (+340991688)", MAJORS, 5, s01_sma_clear, "no exit given: stop at SMA50, reverse"),
    S("s04_trend_shift", "408196018 144 Trend Shift Scalping", ("GBPUSD", "EURUSD"), 5, s04_trend_shift),
    S("s06_sma70_stoch", "443369739 Easy 1-Minute Forex Scalping", MAJORS, 1, s06_sma70_stoch, "no exit given: reverse"),
    S("s07_smma_fractal", "562656610 5-Minute Scalping Strategy (decoded)", MAJORS, 5, s07_smma_fractal),
    S("s10b_ema_stoch", "787362037 1-Minute Scalping (EMA 50/200 + Stochastic)", MAJORS, 1, s10b_ema_stoch),
    S("s11_ema_retest_stoch", "699198790 1-Minute Forex Scalping Strategy Guide", MAJORS, 1, s11_ema_retest_stoch),
    S("s12a_ema8_21", "513233085 Scalping Quick Guide 5m (decoded)", MAJORS, 5, s12a_ema8_21),
    S("s13_four_screen", "372397582 Price Action Scalper (4 screens)", MAJORS, 5, s13_four_screen),
    S("s14_bb_stoch", "470357499 BB-Stochastic 5 Minute Scalping (decoded)", ("EURUSD",), 5, s14_bb_stoch_eurusd),
    S("s16_macd_stoch_m1", "412362945 M1/M5 Forex Scalping (M1)", ("EURUSD", "GBPUSD"), 1, s16_macd_stoch(15)),
    S("s16_macd_stoch_m5", "412362945 M1/M5 Forex Scalping (M5)", ("EURUSD", "GBPUSD"), 5, s16_macd_stoch(25)),
    S("s17a_dead_time", "109121976 Fielder: dead-time range", MAJORS, 60, s17a_dead_time),
    S("s17c_session_break", "109121976 Fielder: session-open breakout #1", MAJORS, 15, s17c_session_breakout(False)),
    S("s17d_prevbar_break", "109121976 Fielder: session-open breakout #2", MAJORS, 15, s17c_session_breakout(True)),
    S("s19_btc_mfi", "463942515 How to Day Trade Cryptocurrency (decoded)", ("BTC",), 5, s19_btc_mfi, "long only"),
    S("s23a_fractal_sma50", "685094333 Fractal Trading Strategy (scalping)", MAJORS, 5, s23a_fractal_sma50),
    S("s25_sheriff", "381414258 Scalping Sheriff (crypto)", ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC",
                                                            "DOT", "TRX", "BCH", "NEAR", "ATOM", "ETC", "FIL", "APT", "ARB", "OP"), 15, s25_sheriff,
      "no exit given: reverse"),
    S("s28_adx_bb", "336146688 1 Min ADX + Bollinger", MAJORS, 1, s28_adx_bb),
    S("s30a_bb_range", "629199372 5 Minute Bollinger range scalp", MAJORS, 5, s30a_bb_range),
    S("s30b_momo", "629199372 5 Minute Momo", MAJORS, 5, s30b_momo),
    S("s31_psar_gbpusd", "417801648 5 min Parabolic SAR + MACD (GBPUSD)", ("GBPUSD",), 5, s31_psar_macd(10)),
    S("s31_psar_eurusd", "417801648 5 min Parabolic SAR + MACD (EURUSD)", ("EURUSD",), 5, s31_psar_macd(8)),
    S("s31_psar_audusd", "417801648 5 min Parabolic SAR + MACD (AUDUSD)", ("AUDUSD",), 5, s31_psar_macd(6)),
    S("s35_lazy_river", "432856832 Lazy River Scalping", MAJORS, 5, s35_lazy_river),
    S("g05_ema_stoch5", "566749943 / 818021976 Teknik Rahsia Scalping Gold", ("XAUUSD",), 1, g05_ema_stoch5),
    S("g06_ema14_50", "494426827 14 & 50 EMA Scalping (Waqar)", ("USDJPY", "AUDJPY", "GBPJPY"), 15, g06_ema14_50),
    S("g08_ema9_50_m1", "830550341 High-Probability Gold Scalping (M1)", ("XAUUSD",), 1, g08_ema9_50_rsi_stoch),
    S("g08_ema9_50_m5", "830550341 High-Probability Gold Scalping (M5)", ("XAUUSD",), 5, g08_ema9_50_rsi_stoch),
    S("g09_pine_st_m1", "893052746 Gold Scalping (Anti-Repainting) Pine (M1)", ("XAUUSD",), 1, g09_pine_supertrend),
    S("g09_pine_st_m5", "893052746 Gold Scalping (Anti-Repainting) Pine (M5)", ("XAUUSD",), 5, g09_pine_supertrend),
    S("g10_wpr", "665486654 Gold scalping Rules (Williams %R)", ("XAUUSD",), 1, g10_wpr),
    S("g12_pine_ema5_20_m1", "831663542 XAUUSD 5 & 20 EMA Pine (M1)", ("XAUUSD",), 1, g12_pine_ema5_20, "long only"),
    S("g12_pine_ema5_20_m5", "831663542 XAUUSD 5 & 20 EMA Pine (M5)", ("XAUUSD",), 5, g12_pine_ema5_20, "long only"),
    S("g13b_momo_gold", "901335198 5-Minute Gold Scalping: Momo", ("XAUUSD",), 5, s30b_momo),
    S("g13c_ema_rsi_macd", "901335198 5-Minute Gold Scalping: EMA9/20 + RSI + MACD", ("XAUUSD",), 5, g13c_ema9_20_rsi_macd),
)
BY_KEY = {s.key: s for s in STRATEGIES}
