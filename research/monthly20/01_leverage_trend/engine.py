"""Leveraged, volatility-targeted trend following on Binance USDT perps.

Event simulation over a common bar timeline:
- signals from CLOSED bars, filled at the next bar's open;
- each position (and each pyramid add) is an isolated-margin lot with its own liquidation price
  (wiped when the adverse move reaches 1/lev - 0.5%, checked on bar highs/lows, gap opens included);
- fee + slippage per side on notional, funding paid/received at every funding stamp;
- new lot notional capped at 1% of the previous bar's quote volume;
- universe re-picked monthly from trailing 30-day quote volume (point in time, from 1d bars).
"""
from __future__ import annotations
import math, sys
from bisect import bisect_left
from datetime import datetime, timezone

sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *  # noqa

BIG10 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT"]
BARS_PER_YEAR = {"1d": 365, "4h": 365 * 6, "1h": 365 * 24}

# ---------------------------------------------------------------- indicators

def ema(x, n):
    k, out, e = 2 / (n + 1), [], None
    for v in x:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def adx(h, l, c, n=14):
    N = len(c)
    out = [0.0] * N
    if N < 2 * n + 2:
        return out
    tr, pdm, mdm = [0.0] * N, [0.0] * N, [0.0] * N
    for i in range(1, N):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if up > dn and up > 0 else 0.0
        mdm[i] = dn if dn > up and dn > 0 else 0.0
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr, ap, am = sum(tr[1:n + 1]), sum(pdm[1:n + 1]), sum(mdm[1:n + 1])
    dx = []
    a = None
    for i in range(n + 1, N):
        atr = atr - atr / n + tr[i]
        ap = ap - ap / n + pdm[i]
        am = am - am / n + mdm[i]
        pdi = 100 * ap / atr if atr else 0
        mdi = 100 * am / atr if atr else 0
        d = 100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0
        dx.append(d)
        if len(dx) == n:
            a = sum(dx) / n
        elif len(dx) > n:
            a = (a * (n - 1) + d) / n
        out[i] = a if a is not None else 0.0
    return out


def realized_vol(c, n, bpy):
    out = [None] * len(c)
    r = [0.0] + [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    s = s2 = 0.0
    for i in range(1, len(c)):
        s += r[i]; s2 += r[i] * r[i]
        if i > n:
            s -= r[i - n]; s2 -= r[i - n] ** 2
        if i >= n:
            m = s / n
            out[i] = math.sqrt(max(s2 / n - m * m, 1e-12) * bpy)
    return out


def atr_series(h, l, c, n=20):
    out, a = [None] * len(c), None
    for i in range(1, len(c)):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        a = tr if a is None else a + (tr - a) / n
        if i >= n:
            out[i] = a
    return out


def signal(kind, h, l, c, sides="ls"):
    """Desired side at the close of each bar (+1/-1/0)."""
    N = len(c)
    sig = [0] * N
    if kind.startswith("ema"):
        f, s = map(int, kind[3:].split("_"))
        ef, es = ema(c, f), ema(c, s)
        for i in range(s, N):
            sig[i] = 1 if ef[i] > es[i] else -1
    elif kind.startswith("mom"):
        n = int(kind[3:])
        for i in range(n, N):
            sig[i] = 1 if c[i] > c[i - n] else -1
    elif kind.startswith("don"):  # Donchian breakout n, exit on n/2 opposite channel
        n = int(kind[3:]); m = max(2, n // 2)
        st = 0
        for i in range(n, N):
            hi, lo = max(h[i - n:i]), min(l[i - n:i])
            xhi, xlo = max(h[i - m:i]), min(l[i - m:i])
            if st == 1 and c[i] < xlo: st = 0
            if st == -1 and c[i] > xhi: st = 0
            if c[i] > hi: st = 1
            elif c[i] < lo: st = -1
            sig[i] = st
    else:
        raise ValueError(kind)
    if sides == "l":
        sig = [max(0, x) for x in sig]
    return sig


# ---------------------------------------------------------------- data

class Sym:
    def __init__(self, symbol, interval):
        rows = klines(symbol, interval)
        self.symbol = symbol
        self.t = [r[0] for r in rows]; self.o = [r[1] for r in rows]; self.h = [r[2] for r in rows]
        self.l = [r[3] for r in rows]; self.c = [r[4] for r in rows]; self.qv = [r[6] for r in rows]
        self.idx = {t: i for i, t in enumerate(self.t)}
        self.fund = funding(symbol)
        self.ft = [f[0] for f in self.fund]
        self.cache = {}

    def prep(self, kind, filt, bpy, vol_n, sides):
        key = (kind, filt, sides)
        if key not in self.cache:
            s = signal(kind, self.h, self.l, self.c, sides)
            if filt == "adx25":
                a = adx(self.h, self.l, self.c)
                s = [x if a[i] >= 25 else 0 for i, x in enumerate(s)]
            self.cache[key] = s
        if ("vol", vol_n) not in self.cache:
            self.cache[("vol", vol_n)] = realized_vol(self.c, vol_n, bpy)
            self.cache["atr"] = atr_series(self.h, self.l, self.c)
        return self.cache[key], self.cache[("vol", vol_n)], self.cache["atr"]


def month_key(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m")


_UNI = {}
_D1 = {}


def _d1(sym):
    if sym not in _D1:
        _D1[sym] = klines(sym, "1d")
    return _D1[sym]


def universe_at(n, at, pool=None):
    """Point-in-time top-n by trailing 30d quote volume (same rule as common.liquid_universe, cached in memory)."""
    key = (n, at, tuple(pool) if pool else None)
    if key in _UNI:
        return _UNI[key]
    ranked = []
    cands = pool if pool else [s["symbol"] for s in perp_symbols() if not (s["onboard"] and s["onboard"] > at - 30 * DAY)]
    for sym in cands:
        bars = _d1(sym)
        i0 = bisect_left([b[0] for b in bars], at - 30 * DAY) if bars else 0
        sel = [b for b in bars[i0:i0 + 31] if at - 30 * DAY <= b[0] < at]
        if len(sel) >= 24:
            ranked.append((sum(b[6] for b in sel), sym))
    out = [s for _, s in sorted(ranked, reverse=True)[:n]]
    _UNI[key] = out
    return out


# ---------------------------------------------------------------- simulation

def simulate(syms, cfg, start, end, cost_mult=1.0, eq0=10_000.0):
    """cfg: kind, filt, interval, tv (annual vol target per position), slots (margin buckets),
    max_lev, pyr (max pyramid adds), pyr_step (ATR multiple), uni (None=fixed list or int top-n), sides."""
    iv = cfg["interval"]; step = INTERVAL_MS[iv]; bpy = BARS_PER_YEAR[iv]
    cside = (FEE + SLIP) * cost_mult
    tv, slots, max_lev = cfg["tv"], cfg["slots"], cfg.get("max_lev", 20)
    pyr, pyr_step = cfg.get("pyr", 0), cfg.get("pyr_step", 1.0)
    vol_n = cfg.get("vol_n", 30 if iv == "1d" else 180)
    prepared = {s: syms[s].prep(cfg["kind"], cfg["filt"], bpy, vol_n, cfg.get("sides", "ls")) for s in syms}
    times = sorted({t for s in syms.values() for t in s.t if start <= t < end})
    cash = eq0
    pos = {}   # sym -> {"side", "lots": [ [qty, entry, margin, liq_px] ], "last_add": px}
    curve, trades = [], []
    month, uni = None, None
    nliq = 0
    gross = []
    wiped = False
    fptr = {s: bisect_left(syms[s].ft, start) for s in syms}

    def close_pos(sym, px, why):
        nonlocal cash
        p = pos.pop(sym)
        pnl = 0.0
        for q, e, m, lq in p["lots"]:
            g = q * (px - e)
            cost = abs(q) * px * cside
            pnl += g - cost
        cash += pnl
        trades.append(pnl + p["fees_in"] + p["fund"])
        return pnl

    for t in times:
        # ---- universe (monthly, point in time)
        mk = month_key(t)
        if mk != month:
            month = mk
            ms = utc(int(mk[:4]), int(mk[5:]), 1)
            uni = set(universe_at(cfg["uni"], ms, cfg.get("pool")) if isinstance(cfg.get("uni"), int) else syms.keys())
        # ---- equity at this open (mark at open) for sizing
        eq = cash
        for s, p in pos.items():
            S = syms[s]; i = S.idx.get(t)
            px = S.o[i] if i is not None else p["mark"]
            eq += sum(q * (px - e) for q, e, m, lq in p["lots"])
        # ---- act on signals of the previous closed bar, fill at this open
        for s, S in syms.items():
            i = S.idx.get(t)
            if i is None or i < 1:
                continue
            sig, vol, atr = prepared[s]
            want = sig[i - 1] if s in uni else 0
            o = S.o[i]
            p = pos.get(s)
            if p and p["side"] != want:
                close_pos(s, o, "signal")
                p = None
            if eq <= 0:
                continue
            v = vol[i - 1]
            if want != 0 and v and v > 0:
                lev = min(max_lev, tv / v)
                cap = 0.01 * S.qv[i - 1]
                used = sum(m for pp in pos.values() for _, _, m, _ in pp["lots"])
                avail = cash - used  # isolated margin must come out of the free wallet balance
                if p is None:
                    margin = min(eq / slots / (1 + pyr), avail)
                    if margin <= 0:
                        continue
                    notional = min(margin * lev, cap)
                    if notional * 1 < 10:
                        continue
                    q = want * notional / o
                    eff_lev = notional / margin
                    liq = o * (1 - want * (1 / eff_lev - MAINT_MARGIN))
                    fee = notional * cside
                    cash -= fee
                    pos[s] = {"side": want, "lots": [[q, o, margin, liq]], "last_add": o, "fees_in": -fee,
                              "fund": 0.0, "mark": o}
                elif pyr and len(p["lots"]) < 1 + pyr and atr[i - 1]:
                    if want * (o - p["last_add"]) >= pyr_step * atr[i - 1] and avail > 0:
                        margin = min(eq / slots / (1 + pyr), avail)
                        notional = min(margin * lev, cap)
                        q = want * notional / o
                        eff_lev = notional / margin
                        liq = o * (1 - want * (1 / eff_lev - MAINT_MARGIN))
                        fee = notional * cside
                        cash -= fee; p["fees_in"] -= fee
                        p["lots"].append([q, o, margin, liq]); p["last_add"] = o
        # ---- walk the bar: funding, liquidation, mark
        for s in list(pos):
            S = syms[s]; i = S.idx.get(t)
            p = pos[s]
            if i is None:
                continue
            # funding stamps inside [t, t+step)
            k = fptr[s]
            while k < len(S.ft) and S.ft[k] < t:
                k += 1
            while k < len(S.ft) and S.ft[k] < t + step:
                rate = S.fund[k][1]
                f = -sum(q for q, *_ in p["lots"]) * S.c[i] * rate
                cash += f; p["fund"] += f
                k += 1
            fptr[s] = k
            # liquidation per lot on adverse extreme
            adverse = S.l[i] if p["side"] == 1 else S.h[i]
            keep = []
            for lot in p["lots"]:
                q, e, m, lq = lot
                if lq is not None and (p["side"] == 1 and adverse <= lq or p["side"] == -1 and adverse >= lq):
                    cash -= m  # whole isolated margin lost
                    p["fund"] -= m  # booked into the trade result
                    nliq += 1
                else:
                    keep.append(lot)
            if not keep:
                trades.append(p["fees_in"] + p["fund"])
                del pos[s]
                continue
            p["lots"] = keep
            p["mark"] = S.c[i]
        eqc = cash + sum(sum(q * (p["mark"] - e) for q, e, m, lq in p["lots"]) for p in pos.values())
        curve.append((t + step - 1, eqc))
        if eqc > 0:
            gross.append(sum(abs(q) * p["mark"] for p in pos.values() for q, *_ in p["lots"]) / eqc)
        if eqc <= eq0 * 1e-6:
            curve[-1] = (curve[-1][0], 1e-9)
            wiped = True
            break
    # close what is open at the end
    for s in list(pos):
        close_pos(s, pos[s]["mark"], "end")
    if curve and not wiped:
        curve[-1] = (curve[-1][0], max(cash, 1e-9))
    if wiped:  # flat at zero for the rest of the window, so the monthly table shows the dead months
        t = curve[-1][0]
        while t + DAY < end:
            t += DAY
            curve.append((t, 1e-9))
    curve = [(start, eq0)] + curve  # (first run used start-1, which added a phantom 0% month; fixed, disclosed)
    simulate.avg_gross = sum(gross) / len(gross) if gross else 0.0
    simulate.max_gross = max(gross) if gross else 0.0
    return curve, trades, nliq
