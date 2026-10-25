"""Worker 2 round 2 / attention: abnormal-volume shock rule. Re-uses r4_09_session's survivorship-free hourly panel.

Signal at the open of bar b = d*24 + hoff (all inputs known from bars < b):
  vr_j  = quote volume of the last 24 h / median of the coin's 24 h quote volume over the L previous 24 h blocks
          (blocks ending at b-24, b-48, ..., i.e. the shock day itself is NOT in the norm)
  z_j   = log(C[b-1] / O[b-24]) / (sigma_j * sqrt(24)), sigma_j = std of hourly log returns over the last W hours
Trigger: coin in that day's point-in-time top-40 (ex BTC/ETH), vr_j >= thr and |z_j| >= zmin.
Side: mode "mom" = sign(z) (continuation), "rev" = -sign(z) (reversal).
Hold X days: enter at the open of bar b, exit at the open of bar b + 24*X. 1x: each day's cohort gets equity/X,
split into max(n, K) slots, each slot scaled by m_j = min(1, sig_ref / sigma_j) (equal-risk, only ever scaled down).
Daily mark-to-market equity curve; costs (fee+slip) per side on notional, funding from the panel.
"""
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_09_session")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from base import *  # noqa  O, Hh, L, C, QV, U40, FCUM, td, th, syms, BTC, ETH, hvol, dayidx, FEE, SLIP, DAY
from common import summary, TEST_START, TEST_END, DEV_START, DEV_END

d0, dT, d1 = dayidx(DEV_START), dayidx(TEST_START), dayidx(TEST_END)
T, N = O.shape
ALT_ELIG = [np.array([j for j in np.where(U40[d])[0] if j not in (BTC, ETH)], dtype=int) for d in range(len(td))]

# forward-filled price for marking (a delisted coin freezes at its last close)
PX = O.copy()
_last = np.full(N, np.nan)
for t in range(T):
    row = PX[t]
    bad = ~np.isfinite(row)
    row[bad] = _last[bad]
    cl = C[t]
    _last = np.where(np.isfinite(cl), cl, np.where(np.isfinite(row), row, _last))
_QVc = np.vstack([np.zeros((1, N)), np.cumsum(np.nan_to_num(QV), 0)])
_QVn = np.vstack([np.zeros((1, N)), np.cumsum(np.isfinite(QV).astype(float), 0)])


def qv24(b):
    """quote volume over bars b-24..b-1 (nan if fewer than 22 bars present)."""
    s = _QVc[b] - _QVc[b - 24]; n = _QVn[b] - _QVn[b - 24]
    return np.where(n >= 22, s, np.nan)


_feat = {}


def features(hoff, Lb, W):
    """for each day d in [d0, d1): (vr, z, sigma) arrays over all coins."""
    key = (hoff, Lb, W)
    if key in _feat:
        return _feat[key]
    days = np.arange(d0, d1)
    bs = days * 24 + hoff
    sig = hvol(bs, W)
    VR = np.full((len(days), N), np.nan); Z = np.full((len(days), N), np.nan)
    for i, b in enumerate(bs):
        if b + 1 >= T:
            break
        cur = qv24(b)
        hist = np.vstack([qv24(b - 24 * k) for k in range(1, Lb + 1)])
        with np.errstate(invalid="ignore", divide="ignore"):
            nrm = np.nanmedian(hist, 0)
            ok = np.isfinite(hist).sum(0) >= 0.8 * Lb
            VR[i] = np.where(ok & (nrm > 0), cur / nrm, np.nan)
            Z[i] = np.log(C[b - 1] / O[b - 24]) / (sig[i] * np.sqrt(24))
    _feat[key] = (VR, Z, sig)
    return _feat[key]


def size_mult(sig_ref, s):
    if sig_ref is None:
        return 1.0
    return float(min(1.0, sig_ref / s)) if np.isfinite(s) and s > 0 else 1.0


def signals(p, a, b, exclude=()):
    """rows: (d, ie, ix, j, side, m)."""
    VR, Z, SG = features(p["hoff"], p["Lb"], p["W"])
    rows = []
    for d in range(a, b):
        ie = d * 24 + p["hoff"]
        ix = ie + int(round(24 * p["X"]))
        if ix >= T:
            break
        js = ALT_ELIG[d]
        if len(exclude):
            js = np.array([j for j in js if j not in exclude], dtype=int)
        if len(js) == 0:
            continue
        i = d - d0
        vr, z = VR[i, js], Z[i, js]
        ok = np.isfinite(vr) & np.isfinite(z) & (vr >= p["thr"]) & (np.abs(z) >= p["zmin"]) & np.isfinite(O[ie, js])
        if p.get("dir") == "up":
            ok &= z > 0
        elif p.get("dir") == "down":
            ok &= z < 0
        js2 = js[ok]
        if p.get("maxn") and len(js2) > p["maxn"]:  # keep the largest shocks
            js2 = js2[np.argsort(-VR[i, js2])[:p["maxn"]]]
        for j in js2:
            s = {"mom": np.sign(Z[i, j]), "rev": -np.sign(Z[i, j]), "long": 1.0, "short": -1.0}[p["mode"]]
            rows.append((d, ie, ix, int(j), float(s), size_mult(p["sig_ref"], SG[i, j])))
    return rows


def run(rows, a, b, p, cost_mult=1.0, eq0=10_000.0):
    """daily MTM. trades: (d, j, side, pnl$, pnl/eq_at_entry, notional/eq, ret)."""
    X, K, hoff = p["X"], p["K"], p["hoff"]
    Xd = int(np.ceil(X))
    cost = (FEE + SLIP) * cost_mult
    by_day = {}
    for r in rows:
        by_day.setdefault(r[0], []).append(r)
    eq = eq0
    curve = [(int(td[a]), eq)]
    open_ = []  # [notional, ie, ix, j, s, d, pnl_acc, eq_at_entry]
    trades = []
    for d in range(a, b + Xd + 1):
        t0 = d * 24 + hoff
        if t0 >= T:
            break
        # open new cohort at t0
        if d < b:
            rs = by_day.get(d, [])
            if rs:
                w = 1.0 / (X * max(len(rs), K))
                for r in rs:
                    nt = eq * w * r[5]
                    c = nt * cost
                    eq -= c
                    open_.append([nt, r[1], r[2], r[3], r[4], d, -c, eq + c])
        if d >= b and not open_:
            break
        t1 = min(t0 + 24, T - 1)
        pnl = 0.0
        still = []
        for q in open_:
            nt, ie, ix, j, s, dd, acc, e0 = q
            te = min(t1, ix)
            g = nt * s * (PX[te, j] - PX[t0, j]) / PX[ie, j]
            f = nt * s * (FCUM[te, j] - FCUM[t0, j])
            x = g - f
            if te >= ix:
                x -= nt * cost * PX[ix, j] / PX[ie, j]
                q[6] = acc + x
                trades.append((dd, j, s, q[6], q[6] / e0, nt / e0, q[6] / nt))
            else:
                q[6] = acc + x
                still.append(q)
            pnl += x
        open_ = still
        eq = max(eq + pnl, 0.0)
        if d < b:
            curve.append((int(td[d] + DAY - 1), eq))
        else:  # wind-down after the window: book into the last day
            curve[-1] = (curve[-1][0], eq)
        if eq <= 0:
            break
    return curve, trades


def sim(p, a, b, cost_mult=1.0, exclude=()):
    rows = signals(p, a, b, exclude)
    c, tr = run(rows, a, b, p, cost_mult)
    return c, tr, rows


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
