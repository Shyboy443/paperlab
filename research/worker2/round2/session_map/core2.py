"""session_map: generalised session signal (entry hour E, look-back H, hold X, mom/rev, day filter).
Re-uses research/worker2/session/core.py (data, ALT_ELIG, hv, size_mult, run, stats) unchanged."""
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker2/session")
from core import *  # noqa
from core import SEL as W2SEL

SIG = 0.01154
W2 = dict(W2SEL, sig_ref=SIG)
BASE = dict(E=0, H=8, X=8, k=2.0, K=5, W=480, mode="mom", days="all", sig_ref=SIG)


def day_ok(d, days):
    if days == "all":
        return True
    wk = DOW[d] >= 5
    return wk if days == "weekend" else not wk


def signals2(p, d_from, d_to, exclude=()):
    E, H_, X, k, W = p["E"], p["H"], p["X"], p["k"], p["W"]
    V = hv(W)
    rows = []
    for d in range(d_from, d_to):
        if not day_ok(d, p["days"]):
            continue
        e = d * 24 + E
        if e + X >= len(th) or e - H_ < 0:
            break
        js = ALT_ELIG[d]
        if len(exclude):
            js = np.array([j for j in js if j not in exclude], dtype=int)
        if len(js) == 0:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            z = np.log(C[e - 1, js] / O[e - H_, js]) / (V[d - d0, js] * np.sqrt(H_))
        ok = np.isfinite(z) & (np.abs(z) >= k) & np.isfinite(O[e, js])
        for j, zz in zip(js[ok], z[ok]):
            rows.append((d, e, e + X, int(j), np.sign(zz) * (1 if p["mode"] == "mom" else -1), size_mult(p, V[d - d0, j])))
    return rows


def sim2(p, a, b, cost_mult=1.0, exclude=(), liq_cap=True):
    rows = signals2(p, a, b, exclude)
    c, tr, liq = run(rows, a, b, p["K"], cost_mult=cost_mult, liq_cap=liq_cap)
    return c, tr, liq, rows


def daily_t(c):
    eq = np.array([e for _, e in c])
    r = eq[1:] / eq[:-1] - 1
    return float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))) if r.std() > 0 else 0.0


def excluded(p):
    return p["mode"] == "mom" and (p["E"] == 16 or (p["E"] == 18 and p["H"] >= 16))


def neighbours2(b):
    g = []
    for f in (0.5, 0.75, 1.25, 1.5):
        g.append((f"H*{f}", dict(b, H=max(2, int(round(b["H"] * f))))))
    for f in (0.5, 0.75, 1.25, 1.5):
        g.append((f"X*{f}", dict(b, X=max(2, int(round(b["X"] * f))))))
    for v in (1.0, 1.5, 2.5, 3.0):
        g.append((f"k={v}", dict(b, k=v)))
    for v in (3, 4, 6, 8):
        g.append((f"K={v}", dict(b, K=v)))
    for v in (240, 360, 600, 720):
        g.append((f"W={v}", dict(b, W=v)))
    for dv in (-2, -1, 1, 2):
        g.append((f"E{dv:+d}", dict(b, E=(b["E"] + dv) % 24)))
    return g
