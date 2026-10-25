"""Worker 2 / session: shared code. Re-uses r4_09_session's data (base.py, hpanel cache) unchanged.

signals() and run() are copies of r4_09_session/run.py:signals and engine.py:run with ONE addition:
each row carries a size multiplier `m` (0 < m <= 1). With m = 1 for every row the result is r4_09 exactly.
The pre-registered concentration fix (PREREG.md) sets m = min(1, sig_ref / sigma_j), sigma_j = the coin's trailing
480 h hourly vol known at the day's 00:00 UTC open (the same number the z-score already uses)."""
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_09_session")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from base import *  # noqa  (O, Hh, L, C, QV, U40, FCUM, td, th, syms, BTC, ETH, hvol, dayidx, FEE, SLIP, DAY)
from common import summary, TEST_START, TEST_END, DEV_START, DEV_END, MAINT_MARGIN as MAINT

SEL = dict(H=16, k=2.0, X=26, K=5, W=480, uni="ALT", mode="mom", sig_ref=None)
d0, dT, d1 = dayidx(DEV_START), dayidx(TEST_START), dayidx(TEST_END)
ALT_ELIG = [np.array([j for j in np.where(U40[d])[0] if j not in (BTC, ETH)], dtype=int) for d in range(len(td))]
_hv = {}


def hv(W):
    if W not in _hv:
        _hv[W] = hvol(np.arange(d0 * 24, min(d1 * 24, len(th)), 24), W)
    return _hv[W]


def size_mult(p, sig):
    if p.get("sig_ref") is None:
        return 1.0
    return float(min(1.0, p["sig_ref"] / sig)) if np.isfinite(sig) and sig > 0 else 1.0


def signals(p, d_from, d_to, exclude=()):
    H_, k, X, W = p["H"], p["k"], p["X"], p["W"]
    V = hv(W)
    rows = []
    for d in range(d_from, d_to):
        b = d * 24
        if b + X >= len(th):
            break
        js = np.array([BTC, ETH]) if p["uni"] == "BE" else ALT_ELIG[d]
        if len(exclude):
            js = np.array([j for j in js if j not in exclude], dtype=int)
        if len(js) == 0:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            z = np.log(C[b + H_ - 1, js] / O[b, js]) / (V[d - d0, js] * np.sqrt(H_))
        ok = np.isfinite(z) & (np.abs(z) >= k) & np.isfinite(O[b + H_, js])
        for j, zz in zip(js[ok], z[ok]):
            rows.append((d, b + H_, b + X, int(j), np.sign(zz) * (1 if p["mode"] == "mom" else -1),
                         size_mult(p, V[d - d0, j])))
    return rows


def run(rows, d_from, d_to, K, lev=1.0, cost_mult=1.0, eq0=10_000.0, liq_cap=True, record_notional=False):
    """Copy of r4_09 engine.run + size multiplier r[5]. trades: (day, j, side, pnl$, pnl/eq, notional/eq, ret)."""
    by_day = {}
    for r in rows:
        by_day.setdefault(r[0], []).append(r)
    eq = eq0
    curve = [(int(td[d_from]), eq)]
    trades, liqs = [], 0
    cost = (FEE + SLIP) * cost_mult
    for d in range(d_from, d_to):
        rs = by_day.get(d, [])
        if rs:
            n = len(rs)
            w = 1.0 / max(n, K)
            pnl = 0.0
            for r in rs:
                _, ie, ix, j, s = r[:5]
                m = r[5] if len(r) > 5 else 1.0
                notional = eq * w * m * lev
                if liq_cap:
                    notional = min(notional, 0.01 * QV[ie, j]) if np.isfinite(QV[ie, j]) else notional
                margin = notional / lev
                px = O[ix, j]
                if not np.isfinite(px):
                    seg = C[ie:ix, j]; fin = seg[np.isfinite(seg)]
                    px = fin[-1] if len(fin) else O[ie, j]
                g = s * (px / O[ie, j] - 1)
                f = s * (FCUM[ix, j] - FCUM[ie, j])
                if lev > 1:
                    a = 1 - np.nanmin(L[ie:ix, j]) / O[ie, j] if s > 0 else np.nanmax(Hh[ie:ix, j]) / O[ie, j] - 1
                    if a >= 1.0 / lev - MAINT:
                        liqs += 1
                        p_ = -margin - notional * cost
                        pnl += p_; trades.append((d, j, s, p_, p_ / eq, notional / eq, -1.0 / lev)); continue
                ret = g - f - 2 * cost
                p_ = notional * ret
                pnl += p_
                trades.append((d, j, s, p_, p_ / eq, notional / eq, ret))
            eq = max(eq + pnl, 0.0)
        curve.append((int(td[d] + DAY - 1), eq))
        if eq <= 0:
            break
    return curve, trades, liqs


def sim(p, a, b, lev=1.0, cost_mult=1.0, exclude=()):
    rows = signals(p, a, b, exclude)
    c, tr, liq = run(rows, a, b, p["K"], lev=lev, cost_mult=cost_mult)
    return c, tr, liq, rows


def stats(c, tr=None):
    s = summary(c, [t[3] for t in tr] if tr is not None else None)
    v = np.array(list(s["months"].values()))
    s["t_stat"] = round(float(v.mean() / v.std(ddof=1) * np.sqrt(len(v))), 2) if len(v) > 2 else None
    return s


def yearly(months):
    out = {}
    for k_, v in months.items():
        out.setdefault(k_[:4], 1.0)
        out[k_[:4]] *= 1 + v / 100
    return {y: round((g - 1) * 100, 2) for y, g in out.items()}


def neighbours(base):
    g = []
    for name, vals in (("H", (8, 12, 20, 24)), ("k", (1.0, 1.5, 2.5, 3.0)), ("hold", (5, 8, 12, 15)),
                       ("K", (3, 4, 6, 8)), ("W", (240, 360, 600, 720))):
        for v in vals:
            p = dict(base)
            if name == "hold":
                p["X"] = p["H"] + v
            elif name == "H":
                p["H"] = v; p["X"] = v + (base["X"] - base["H"])
            else:
                p[name] = v
            g.append((f"{name}={v}", p))
    return g
