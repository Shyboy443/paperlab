"""Scribd round 2: swing, trend and session-breakout strategies, written down as exact rules BEFORE any result
(docs/SCRIBD_SWING_STUDY.md). Each strategy's docstring is the rule as tested.

Three kinds of rule:
- daily signal rules returning a scalp.Plan (RSI-2 family, Holy Grail, the two stock MA rules);
- the Turtle systems, with their own simulator (pyramiding units, per-unit stops, the System 1 skip rule);
- London-session rules on 5-minute candles, with their own day-by-day simulator (OCO stop orders, trailing stops,
  time stops).
Every simulator walks a candle along the neutral OHLC path (green O-L-H-C, red O-H-L-C) and pays ``cost`` (a share
of the price) on every fill, like app/ytstrats/scalp.py.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from . import ind as I
from .scalp import Plan

MAJORS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD")
GBP_PAIRS = ("GBPUSD", "GBPJPY", "GBPAUD", "GBPCAD", "GBPNZD", "GBPCHF", "EURGBP")
PIP = {**{s: 0.0001 for s in MAJORS + GBP_PAIRS}, "USDJPY": 0.01, "GBPJPY": 0.01}
MT5_DAILY = MAJORS + ("AUDJPY", "GBPJPY", "XAUUSD", "XAGUSD", "US30", "US500", "NAS100", "UK100", "GER40")
DOW30 = ("AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS", "GS", "HD", "HON", "IBM", "JNJ",
         "JPM", "KO", "MCD", "MMM", "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT")
DAY = 86_400_000


def path(o, h, l, c):
    return (o, l, h, c) if c >= o else (o, h, l, c)


def dmi(h, l, c, n=14):
    """(ADX, +DI, -DI), Wilder."""
    up, dn = np.diff(h, prepend=np.nan), -np.diff(l, prepend=np.nan)
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr = I.rma(I.true_range(h, l, c), n)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi, ndi = 100 * I.rma(pdm, n) / atr, 100 * I.rma(ndm, n) / atr
        dx = 100 * np.abs(pdi - ndi) / (pdi + ndi)
    return I.rma(np.nan_to_num(dx), n), pdi, ndi


# ----------------------------------------------------------------------------------------------- daily signal rules
@dataclass
class D:
    """Daily candles of one market."""
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray


def rsi2_sp500(b: D, pip: float) -> Plan:
    """S&P 500 2-period RSI (439781936): long only. Close > SMA200 and RSI(2) < 10 -> buy at the close. Exit when the
    close is above SMA10. No stop."""
    r = I.rsi(b.c, 2)
    sig = (b.c > I.sma(b.c, 200)) & (r < 10)
    return Plan(sig=np.where(np.nan_to_num(sig).astype(bool), 1, 0), exit_long=np.nan_to_num(b.c > I.sma(b.c, 10)).astype(bool))


def r2_improved(b: D, pip: float) -> Plan:
    """Connors' Improved R2 (100141147): long only. Close > SMA200; RSI(2) < 65 on day 1, lower on day 2, lower again
    on day 3 -> buy at the close of day 3. Exit when RSI(2) closes above 75. No stop."""
    r = I.rsi(b.c, 2)
    r1, r2 = I.shift(r, 1), I.shift(r, 2)
    sig = (b.c > I.sma(b.c, 200)) & (r2 < 65) & (r1 < r2) & (r < r1)
    return Plan(sig=np.where(np.nan_to_num(sig).astype(bool), 1, 0), exit_long=np.nan_to_num(r > 75).astype(bool))


def rsi2_15_85(b: D, pip: float) -> Plan:
    """QuantifiedStrategies RSI (896242411), first rule: RSI(2) crosses below 15 -> buy at the close; exit when RSI(2)
    is above 85. No stop."""
    r = I.rsi(b.c, 2)
    sig = (r < 15) & (I.shift(r, 1) >= 15)
    return Plan(sig=np.where(np.nan_to_num(sig).astype(bool), 1, 0), exit_long=np.nan_to_num(r > 85).astype(bool))


def rsi2_rsi30(b: D, pip: float) -> Plan:
    """QuantifiedStrategies RSI (896242411), AFL rule: RSI(30) > 50 and RSI(2) < 15 -> buy at the close; exit when
    RSI(2) > 85. No stop."""
    r = I.rsi(b.c, 2)
    sig = (I.rsi(b.c, 30) > 50) & (r < 15)
    return Plan(sig=np.where(np.nan_to_num(sig).astype(bool), 1, 0), exit_long=np.nan_to_num(r > 85).astype(bool))


def holy_grail(b: D, pip: float) -> Plan:
    """LBR Holy Grail (596991201). Setup: ADX(14) > 30 and below yesterday's ADX, trend = the larger DI, the bar
    touches the 20 EMA. Long: buy stop 3 pips above the 2-event high (this bar and the one before), stop 3 pips below
    the 2-event low, target the highest high of the 20 bars before (the previous trend high), closed in full there.
    Re-armed every bar while the setup holds. Shorts mirror."""
    adx, pdi, ndi = dmi(b.h, b.l, b.c, 14)
    e = I.ema(b.c, 20)
    touch = (b.l <= e) & (b.h >= e)
    setup = (adx > 30) & (adx < I.shift(adx, 1)) & touch
    up, dn = setup & (pdi > ndi), setup & (ndi > pdi)
    h2, l2 = np.maximum(b.h, I.shift(b.h, 1)), np.minimum(b.l, I.shift(b.l, 1))
    tgt_up, tgt_dn = I.highest(I.shift(b.h, 1), 20), I.lowest(I.shift(b.l, 1), 20)
    ent = np.where(up, h2 + 3 * pip, np.where(dn, l2 - 3 * pip, np.nan))
    stop = np.where(up, l2 - 3 * pip, np.where(dn, h2 + 3 * pip, np.nan))
    tgt = np.where(up, tgt_up, np.where(dn, tgt_dn, np.nan))
    room = np.where(up, tgt - ent, np.where(dn, ent - tgt, np.nan))
    ok = np.nan_to_num(room > 0).astype(bool)
    sig = np.where(ok & np.nan_to_num(up).astype(bool), 1, np.where(ok & np.nan_to_num(dn).astype(bool), -1, 0))
    return Plan(sig=sig, entry_px=np.where(ok, ent, np.nan), stop=np.where(ok, stop, np.nan), tp_dist=np.where(ok, room, np.nan))


def ma_breakout(b: D, pip: float) -> Plan:
    """Moving Average Breakout (683919166), pip = 0.10 USD for stocks. Long: after the close crosses above SMA20, the
    first two bars with lows above SMA20 -> buy stop 0.10 USD above the higher high of those two bars, armed while the
    close stays above SMA20. Short mirrors (two highs below, sell stop 0.10 below the lower low). No exit given: the
    opposite signal closes and reverses."""
    s = I.sma(b.c, 20)
    n = len(b.c)
    sig, ent = np.zeros(n, int), np.full(n, np.nan)
    state, cnt, ext = 0, 0, []
    for i in range(1, n):
        if np.isnan(s[i]) or np.isnan(s[i - 1]):
            continue
        if b.c[i] > s[i] and b.c[i - 1] <= s[i - 1]:
            state, cnt, ext = 1, 0, []
        elif b.c[i] < s[i] and b.c[i - 1] >= s[i - 1]:
            state, cnt, ext = -1, 0, []
        elif state == 1 and b.c[i] < s[i] or state == -1 and b.c[i] > s[i]:
            state = 0
        if state == 1 and cnt < 2 and b.l[i] > s[i]:
            cnt += 1
            ext.append(b.h[i])
        elif state == -1 and cnt < 2 and b.h[i] < s[i]:
            cnt += 1
            ext.append(b.l[i])
        if state and cnt == 2:
            sig[i] = state
            ent[i] = max(ext) + pip if state > 0 else min(ext) - pip
    return Plan(sig=sig, entry_px=ent, reverse=True)


def triple_ma(b: D, pip: float) -> Plan:
    """Triple Moving Average System (410843959), long only: buy on a bar where SMA5 > SMA10 > SMA20 and SMA50 is not
    declining; exit when SMA5 < SMA10 < SMA20."""
    s5, s10, s20, s50 = (I.sma(b.c, n) for n in (5, 10, 20, 50))
    sig = (s5 > s10) & (s10 > s20) & (s50 >= I.shift(s50, 1))
    return Plan(sig=np.where(np.nan_to_num(sig).astype(bool), 1, 0), exit_long=np.nan_to_num((s5 < s10) & (s10 < s20)).astype(bool))


# -------------------------------------------------------------------------------------------------------- Turtles
def turtle(b: D, system: int, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """Original Turtle Trading Rules (466279786). System 1: 20-day breakout entry (skipped when the last 20-day breakout
    was a winner, then the 55-day failsafe), 10-day exit. System 2: 55-day breakout, 20-day exit. N = 20-day Wilder
    ATR; stop 2 N; add a unit every 1/2 N up to 4, raising earlier units' stops by 1/2 N (new unit: 2 N from its fill).
    Intraday levels, gap-through fills at the open. One trade = the whole position; return = sum over units of the
    move, as a % of the first fill."""
    t, o, h, l, c = b.t, b.o, b.h, b.l, b.c
    n = len(c)
    N = I.atr(h, l, c, 20)
    ent_n, ex_n = (20, 10) if system == 1 else (55, 20)
    hi_e, lo_e = I.shift(I.highest(h, ent_n), 1), I.shift(I.lowest(l, ent_n), 1)
    hi55, lo55 = I.shift(I.highest(h, 55), 1), I.shift(I.lowest(l, 55), 1)
    hi_x, lo_x = I.shift(I.highest(h, ex_n), 1), I.shift(I.lowest(l, ex_n), 1)
    hi10, lo10 = I.shift(I.highest(h, 10), 1), I.shift(I.lowest(l, 10), 1)

    # System 1's skip rule needs the outcome of every 20-day breakout, taken or not: a 1-unit shadow trade.
    shadow_open, last_winner = False, None
    winner_known_at = np.full(n, np.nan)          # last closed shadow outcome known before day i (1 win, 0 loss)
    shadow_busy = np.zeros(n, bool)
    if system == 1:
        sd = se = sstop = 0.0
        for i in range(1, n):
            winner_known_at[i] = np.nan if last_winner is None else float(last_winner)
            shadow_busy[i] = shadow_open
            if np.isnan(hi_e[i]) or np.isnan(N[i - 1]) or np.isnan(lo10[i]):
                continue
            pts = path(o[i], h[i], l[i], c[i])
            cur = pts[0]
            k0 = 0
            if not shadow_open:
                for q, p in enumerate(pts):
                    if p > hi_e[i] or p < lo_e[i]:                 # "exceeded by a single tick"
                        sd = 1.0 if p > hi_e[i] else -1.0
                        se = p if q == 0 else (hi_e[i] if sd > 0 else lo_e[i])
                        sstop = se - sd * 2 * N[i - 1]
                        shadow_open, k0, cur = True, q, se
                        break
                if not shadow_open:
                    continue
            ex_lvl = lo10[i] if sd > 0 else hi10[i]
            lvl = max(sstop, ex_lvl) if sd > 0 else min(sstop, ex_lvl)
            for p in pts[k0:]:
                if (p <= lvl) if sd > 0 else (p >= lvl):
                    px = p if (cur <= lvl if sd > 0 else cur >= lvl) else lvl
                    last_winner, shadow_open = (px - se) * sd > 0, False
                    break
                cur = p

    trades: list[dict[str, Any]] = []
    pos = None                                     # dict(d, N0, units=[[fill, stop]], next_add, t0, i0)
    first = int(np.searchsorted(t, s0))
    last = int(np.searchsorted(t, s1)) - 1
    for i in range(max(first, 56), last + 1):
        if np.isnan(N[i - 1]) or np.isnan(hi55[i]) or np.isnan(hi_e[i]):
            continue
        pts = path(o[i], h[i], l[i], c[i])
        k0 = 0
        if pos is None:
            if system == 1:
                use55 = shadow_busy[i] or winner_known_at[i] == 1.0
                up_l, dn_l = (hi55[i], lo55[i]) if use55 else (hi_e[i], lo_e[i])
            else:
                up_l, dn_l = hi_e[i], lo_e[i]
            for q, p in enumerate(pts):
                if p > up_l or p < dn_l:                           # "exceeded by a single tick"
                    d = 1.0 if p > up_l else -1.0
                    fill = p if q == 0 else (up_l if d > 0 else dn_l)
                    N0 = float(N[i - 1])
                    pos = {"d": d, "N0": N0, "units": [[fill, fill - d * 2 * N0]], "next_add": fill + d * N0 / 2, "t0": int(t[i]),
                           "first": fill, "entry_day": True}
                    k0 = q
                    break
            if pos is None:
                continue
        d, N0, units = pos["d"], pos["N0"], pos["units"]
        ex_lvl = lo_x[i] if d > 0 else hi_x[i]
        closed: list[tuple[float, float]] = []
        cur = pos["first"] if pos.pop("entry_day", False) else None    # entry day: walk on from the fill
        for q in range(k0, 4):
            p = pts[q]
            if cur is None:                                 # the open: gaps through levels fill at the open
                cur = p
                for u in list(units):
                    if (p <= max(u[1], ex_lvl)) if d > 0 else (p >= min(u[1], ex_lvl)):
                        closed.append((u[0], p))
                        units.remove(u)
                while units and len(units) < 4 and ((p >= pos["next_add"]) if d > 0 else (p <= pos["next_add"])):
                    for u in units:
                        u[1] += d * N0 / 2
                    units.append([p, p - d * 2 * N0])
                    pos["next_add"] = p + d * N0 / 2
                continue
            if (p - cur) * d < 0:                           # moving against the trade: stops and the exit breakout
                for u in sorted(units, key=lambda u: -d * max(u[1] * d, ex_lvl * d)):
                    lvl = max(u[1], ex_lvl) if d > 0 else min(u[1], ex_lvl)
                    if (p <= lvl) if d > 0 else (p >= lvl):
                        closed.append((u[0], lvl))
                        units.remove(u)
            elif (p - cur) * d > 0:                         # moving with it: add units at each 1/2 N
                while units and len(units) < 4 and ((p >= pos["next_add"]) if d > 0 else (p <= pos["next_add"])):
                    lvl = pos["next_add"]
                    for u in units:
                        u[1] += d * N0 / 2
                    units.append([lvl, lvl - d * 2 * N0])
                    pos["next_add"] = lvl + d * N0 / 2
            cur = p
            if not units:
                break
        pos.setdefault("closed", []).extend(closed)
        if not units or i == last:
            for u in units:
                pos["closed"].append((u[0], c[i]))
            first_fill = pos["first"]
            pnl = sum(d * (ex * (1 - d * cost) - en * (1 + d * cost)) for en, ex in pos["closed"])
            trades.append({"t": pos["t0"], "exit_t": int(t[i]), "dir": int(d), "entry": float(first_fill), "units": len(pos["closed"]),
                           "pnl_px": float(pnl), "ret_pct": float(pnl / first_fill * 100), "r": float(pnl / (2 * N0)),
                           "risk_pct": float(2 * N0 / first_fill * 100), "result": "end" if units else "exit"})
            pos = None
    return trades


# ------------------------------------------------------------------------------------------- London session rules
def uk_dst(t_ms: np.ndarray) -> np.ndarray:
    """True where British Summer Time is in effect (last Sunday of March 01:00 UTC -> last Sunday of October 01:00 UTC)."""
    out = np.zeros(len(t_ms), bool)
    y0 = dt.datetime.fromtimestamp(t_ms[0] / 1000, dt.timezone.utc).year
    y1 = dt.datetime.fromtimestamp(t_ms[-1] / 1000, dt.timezone.utc).year
    for y in range(y0, y1 + 1):
        a = dt.datetime(y, 3, 31, 1, tzinfo=dt.timezone.utc)
        a -= dt.timedelta(days=(a.weekday() + 1) % 7)
        z = dt.datetime(y, 10, 31, 1, tzinfo=dt.timezone.utc)
        z -= dt.timedelta(days=(z.weekday() + 1) % 7)
        out |= (t_ms >= a.timestamp() * 1000) & (t_ms < z.timestamp() * 1000)
    return out


@dataclass
class M:
    """Intraday candles of one market (UTC open times) with London local date / minute of each candle's open."""
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    tf: int

    def __post_init__(self):
        loc = self.t + np.where(uk_dst(self.t), 3_600_000, 0)
        self.lday = (loc // DAY).astype(np.int64)
        self.lmin = ((loc % DAY) // 60_000).astype(np.int64)
        self.umin = ((self.t % DAY) // 60_000).astype(np.int64)


def _days(m: M):
    starts = np.flatnonzero(np.r_[True, m.lday[1:] != m.lday[:-1]])
    return zip(starts, np.r_[starts[1:], len(m.t)])


def _walk(m: M, j: int, end: int, d: int, entry_raw: float, first_pts, stop, tp, trail=None, early=None, time_stop=None):
    """Manage one position from candle j (already at entry_raw, remaining path first_pts) to candle end-1 (closed at
    its close if still open). early(k) -> True: exit at the next open. time_stop(k, best_adverse) -> True: exit at the
    next open. Returns (exit_raw, k, result)."""
    best = entry_raw
    fav = lambda x: x * d  # noqa: E731
    for k in range(j, end):
        pts = first_pts if k == j else path(m.o[k], m.h[k], m.l[k], m.c[k])
        cur = pts[0]
        if k > j:
            if stop is not None and fav(cur) <= fav(stop):
                return cur, k, "stop"
            if tp is not None and fav(cur) >= fav(tp):
                return cur, k, "target"
        for p in pts[1:]:
            if fav(p) < fav(cur):
                if stop is not None and fav(p) <= fav(stop):
                    return stop, k, "stop"
            elif fav(p) > fav(cur):
                if tp is not None and fav(p) >= fav(tp):
                    return tp, k, "target"
                if fav(p) > fav(best):
                    best = p
                    if trail is not None:
                        ns = best - d * trail
                        stop = ns if stop is None else (max(stop, ns) if d > 0 else min(stop, ns))
            cur = p
        if k + 1 < end:
            if early is not None and early(k):
                return m.o[k + 1], k + 1, "rule exit"
            if time_stop is not None and time_stop(k):
                return m.o[k + 1], k + 1, "time stop"
    return m.c[end - 1], end - 1, "day end"


def _trade(m: M, j: int, k: int, d: int, raw_in: float, raw_out: float, cost: float, risk: float | None, result: str):
    pnl = d * (raw_out * (1 - d * cost) - raw_in * (1 + d * cost))
    return {"t": int(m.t[j]), "exit_t": int(m.t[min(k, len(m.t) - 1)]), "dir": int(d), "entry": float(raw_in), "pnl_px": float(pnl),
            "ret_pct": float(pnl / raw_in * 100), "r": float(pnl / risk) if risk else None,
            "risk_pct": float(risk / raw_in * 100) if risk else None, "result": result}


def _cutoff(m: M, s: int, e: int, utc_min: int = 21 * 60) -> int:
    """First candle of the day [s, e) opening at/after utc_min (UTC) -> the trade's last candle bound."""
    idx = np.flatnonzero(m.umin[s:e] >= utc_min)
    return s + int(idx[0]) if len(idx) else e


def _stop_entry(pts, buy, sell):
    """First stop-entry level reached on a candle path: (d, fill, segment index) or None. buy/sell may be None."""
    p0 = pts[0]
    if buy is not None and p0 >= buy:
        return 1, p0, 0
    if sell is not None and p0 <= sell:
        return -1, p0, 0
    for q in range(1, 4):
        if buy is not None and pts[q] >= buy:
            return 1, buy, q
        if sell is not None and pts[q] <= sell:
            return -1, sell, q
    return None


def london_bo(m: M, pip: float, cost: float, sym: str, s0: int, s1: int) -> list[dict[str, Any]]:
    """London Breakout (427438395), GBP pairs, 5m. Asian range 00:00-08:00 London; at 08:00 buy stop range high + 5
    pips / sell stop range low - 5 pips, first fill cancels the other, live until 16:00 London. Stop 20 pips trailing
    20 pips behind the best price, target 40 pips. Exit at the next open after two consecutive H1 candles against the
    trade close. Skip days with an Asian range > 100 pips except GBPUSD/GBPJPY. Closed at 21:00 UTC if still open."""
    out = []
    h1_close = (m.umin % 60) == (60 - m.tf)
    for s, e in _days(m):
        if not (s0 <= m.t[s] < s1):
            continue
        asia = np.flatnonzero(m.lmin[s:e] < 8 * 60) + s
        live = np.flatnonzero((m.lmin[s:e] >= 8 * 60) & (m.lmin[s:e] < 16 * 60)) + s
        if len(asia) < 60 or not len(live):
            continue
        hi, lo = m.h[asia].max(), m.l[asia].min()
        if (hi - lo) > 100 * pip and sym not in ("GBPUSD", "GBPJPY"):
            continue
        end = _cutoff(m, s, e)
        for j in live:
            if j >= end:
                break
            pts = path(m.o[j], m.h[j], m.l[j], m.c[j])
            hit = _stop_entry(pts, hi + 5 * pip, lo - 5 * pip)
            if not hit:
                continue
            d, fill, q = hit
            first_pts = (fill,) + tuple(pts[q:]) if q else pts
            # H1 candles closing after the entry; two in a row against the trade -> exit
            state = {"run": 0}

            def early(k, d=d, j=j, state=state):
                hk = int(m.t[k] // 3_600_000)
                if not h1_close[k]:
                    return False
                ks = int(np.searchsorted(m.t, hk * 3_600_000))
                col = np.sign(m.c[k] - m.o[ks])
                state["run"] = state["run"] + 1 if col == -d else 0
                return state["run"] >= 2
            raw_out, k, res = _walk(m, j, end, d, fill, first_pts, fill - d * 20 * pip, fill + d * 40 * pip, trail=20 * pip, early=early)
            out.append(_trade(m, j, k, d, fill, raw_out, cost, 20 * pip, res))
            break
    return out


def london_breakfast(m: M, pip: float, cost: float, sym: str, s0: int, s1: int, tp_pips: float = 40) -> list[dict[str, Any]]:
    """London Free Breakfast (423041768), GBPUSD, 15m. Asian box 00:00-08:00 London; from 08:00 to 16:00 London the
    FIRST 15m close beyond the box -> enter that way at the next open, stop at the breakout candle's other extreme,
    target 40 pips (30 and 50 as sensitivity). One trade a day, closed at 21:00 UTC if still open."""
    out = []
    for s, e in _days(m):
        if not (s0 <= m.t[s] < s1):
            continue
        asia = np.flatnonzero(m.lmin[s:e] < 8 * 60) + s
        live = np.flatnonzero((m.lmin[s:e] >= 8 * 60) & (m.lmin[s:e] < 16 * 60)) + s
        if len(asia) < 20 or not len(live):
            continue
        hi, lo = m.h[asia].max(), m.l[asia].min()
        end = _cutoff(m, s, e)
        for i in live:
            if i + 1 >= end:
                break
            d = 1 if m.c[i] > hi else -1 if m.c[i] < lo else 0
            if not d:
                continue
            j = i + 1
            fill = m.o[j]
            stop = m.l[i] if d > 0 else m.h[i]
            if (fill - stop) * d <= 0:
                break
            pts = path(m.o[j], m.h[j], m.l[j], m.c[j])
            raw_out, k, res = _walk(m, j, end, d, fill, pts, stop, fill + d * tp_pips * pip)
            out.append(_trade(m, j, k, d, fill, raw_out, cost, abs(fill - stop), res))
            break
    return out


def big_ben_fade(m: M, pip: float, cost: float, sym: str, s0: int, s1: int) -> list[dict[str, Any]]:
    """Big Ben Breakout (450251566), 5m. Body range (candle opens/closes) 00:00-07:00 London. The first 5m close
    beyond it between 07:00 and 08:00 London is the pre-open breakout. Between 08:00 and 09:00 London, the first 5m
    close back inside the range -> enter against the breakout at the next open. Target: the range size projected from
    the far side. Time stop: at 09:00 London exit unless price has traded back to the 07:00 London price. No price
    stop; closed at 21:00 UTC if still open."""
    out = []
    for s, e in _days(m):
        if not (s0 <= m.t[s] < s1):
            continue
        lm = m.lmin[s:e]
        asia = np.flatnonzero(lm < 7 * 60) + s
        pre = np.flatnonzero((lm >= 7 * 60) & (lm < 8 * 60)) + s
        opn = np.flatnonzero((lm >= 8 * 60) & (lm < 9 * 60)) + s
        if len(asia) < 50 or not len(pre) or len(opn) < 2:
            continue
        bh = max(m.o[asia].max(), m.c[asia].max())
        bl = min(m.o[asia].min(), m.c[asia].min())
        brk = 0
        for i in pre:
            brk = 1 if m.c[i] > bh else -1 if m.c[i] < bl else 0
            if brk:
                break
        if not brk:
            continue
        p0 = m.o[pre[0]]
        end = _cutoff(m, s, e)
        for i in opn:
            if i + 1 >= end or m.lmin[i + 1] >= 9 * 60:
                break
            if not ((m.c[i] < bh) if brk > 0 else (m.c[i] > bl)):
                continue
            d, j = -brk, i + 1
            fill = m.o[j]
            tp = bl - (bh - bl) if d < 0 else bh + (bh - bl)
            last_open = int(opn[-1])
            seen = {"ok": (fill <= p0) if d < 0 else (fill >= p0)}

            def tstop(k, d=d, seen=seen, last_open=last_open):
                seen["ok"] = seen["ok"] or ((m.l[k] <= p0) if d < 0 else (m.h[k] >= p0))
                return k == last_open and not seen["ok"]
            pts = path(m.o[j], m.h[j], m.l[j], m.c[j])
            raw_out, k, res = _walk(m, j, end, d, fill, pts, None, tp, time_stop=tstop)
            out.append(_trade(m, j, k, d, fill, raw_out, cost, None, res))
            break
    return out


def pips20_daily(m: M, pip: float, cost: float, sym: str, s0: int, s1: int, daily: D) -> list[dict[str, Any]]:
    """20 Pips Daily Forex Breakout (442430072, decoded), any pair. Broker day (New York close). Buy stop 3 pips above
    the previous day's high, sell stop 3 pips below its low; stop 20 pips, target 20 pips. An order touched before
    07:00 London (the Asian session) is not taken that day. Each order fills at most once a day, one position at a
    time; a trade still open at the end of the broker day is closed at its last close."""
    out = []
    for di in range(1, len(daily.t)):
        d0 = int(daily.t[di])
        if not (s0 <= d0 < s1):
            continue
        s, e = int(np.searchsorted(m.t, d0)), int(np.searchsorted(m.t, d0 + DAY))
        if e - s < 100:
            continue
        lv = {1: daily.h[di - 1] + 3 * pip, -1: daily.l[di - 1] - 3 * pip}
        used = {1: False, -1: False}
        j = s
        while j < e:
            pts = path(m.o[j], m.h[j], m.l[j], m.c[j])
            hit = _stop_entry(pts, None if used[1] else lv[1], None if used[-1] else lv[-1])
            if not hit:
                j += 1
                continue
            d, fill, q = hit
            used[d] = True
            asian = m.lmin[j] < 7 * 60 or m.lmin[j] >= 21 * 60     # the broker day starts in the London evening
            if asian:
                continue                                           # the other order may still be reached on this candle
            first_pts = (fill,) + tuple(pts[q:]) if q else pts
            raw_out, k, res = _walk(m, j, e, d, fill, first_pts, fill - d * 20 * pip, fill + d * 20 * pip)
            out.append(_trade(m, j, k, d, fill, raw_out, cost, 20 * pip, res))
            j = k + 1
    return out


@dataclass
class Rule:
    key: str
    doc: str
    assets: tuple[str, ...]
    kind: str                     # "plan" (daily Plan), "turtle", "session"
    fn: Callable
    note: str = ""
    single_market: bool = False


RULES: list[Rule] = [
    Rule("turtle_s1", "Original Turtle Trading Rules (466279786), System 1", MT5_DAILY, "turtle", lambda b, cost, s0, s1: turtle(b, 1, cost, s0, s1),
         "diversified futures in the document -> every MT5 market with long daily history"),
    Rule("turtle_s2", "Original Turtle Trading Rules (466279786), System 2", MT5_DAILY, "turtle", lambda b, cost, s0, s1: turtle(b, 2, cost, s0, s1)),
    Rule("rsi2_sp500", "S&P 500 Trading with 2-Period RSI (439781936)", ("US500",), "plan", rsi2_sp500, single_market=True),
    Rule("r2_improved", "The Improved R2 Strategy, Larry Connors (100141147)", ("US500",), "plan", r2_improved, single_market=True),
    Rule("rsi2_15_85", "RSI Trading Strategy 91% Win Rate, QuantifiedStrategies (896242411)", ("US500",), "plan", rsi2_15_85, single_market=True),
    Rule("rsi2_rsi30", "RSI Trading Strategy 91% Win Rate, AFL rule (896242411)", ("US500",), "plan", rsi2_rsi30, single_market=True),
    Rule("holy_grail", "Holy Grail Trade, LBR (596991201)", MAJORS, "plan", holy_grail, "timeframe not given -> daily (Raschke's original)"),
    Rule("ma_breakout", "Moving Average Breakout (683919166)", DOW30, "plan", ma_breakout, "stocks; survivorship-biased list"),
    Rule("triple_ma", "The Triple Moving Average System (410843959)", DOW30, "plan", triple_ma, "stocks; long only; survivorship-biased list"),
    Rule("london_bo", "London Breakout Strategy (427438395)", GBP_PAIRS, "session", london_bo),
    Rule("london_breakfast", "London Free Breakfast Forex Strategy (423041768)", ("GBPUSD",), "session", london_breakfast, "15m", single_market=True),
    Rule("big_ben_fade", "Big Ben Breakout Strategy (450251566)", ("GBPUSD", "GBPJPY", "EURGBP", "EURUSD", "USDJPY", "AUDUSD"), "session", big_ben_fade),
    Rule("pips20_daily", "20 Pips Daily Forex Breakout Strategy (442430072)", MAJORS, "session", pips20_daily),
]
