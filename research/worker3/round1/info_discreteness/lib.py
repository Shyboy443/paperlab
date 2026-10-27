"""Momentum-QUALITY scores on the survivorship-free daily panel (research/monthly20/r2_rotation), run through the
unchanged agent-02 rotation engine (engine_r2.run: next-open fills, fee+slip per side, funding, 1% volume cap).

Every score at bar i uses closes up to c[i-1] only (the engine trades at the open of bar i).
Scores (L = formation window in days, r = daily log close-to-close return):
  mom    : sum r                                      (plain momentum, reference only; Worker 1 uses 14d/60d)
  fip    : Da-Gurun-Warachka information discreteness. ID = sign(PRET) * (%neg days - %pos days). Two-stage sort:
           among the top M=2k coins by PRET go long the k with the LOWEST ID (smooth winners); among the bottom M go
           short the k with the lowest ID (smooth losers).
  r2s    : Clenow trend quality = annualised slope of log-price regression x R^2.
  sharpe : mean r / std r over L.
  resid  : BTC-residual momentum, (sum r - beta*sum r_btc) / (resid std * sqrt(L)), beta over the same window.
"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r2 as E  # noqa
from data import build  # noqa
from common import DEV_START, DEV_END, TEST_START, TEST_END  # noqa

D = build()
P = E.Panel(D)
_UM = {}


def um(n, Pn=None):
    Pn = Pn or P
    key = (id(Pn), n)
    if key not in _UM:
        _UM[key] = E.universe_mask(Pn, n)
    return _UM[key]


def _roll(x, L):
    """rolling sum over rows t-L..t-1 placed at row t (i.e. known at the open of bar t if x[t-1] uses c[t-1])."""
    cs = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(x, 0)])
    out = np.full(x.shape, np.nan)
    out[L:] = cs[L:-1] - cs[:-1 - L]
    return out


def base_arrays(Pn):
    lc = np.log(Pn.c)
    r = np.full_like(lc, np.nan)
    r[1:] = lc[1:] - lc[:-1]          # r[t] uses c[t] and c[t-1]
    return lc, r


def score(Pn, kind, L):
    lc, r = base_arrays(Pn)
    ok = ~np.isnan(r)
    rz = np.where(ok, r, 0.0)
    n = _roll(ok.astype(float), L)
    full = n >= L - 0.5
    s1 = _roll(rz, L)
    if kind == "mom":
        out = s1
    elif kind in ("fip", "idraw"):
        pos = _roll((rz > 0).astype(float), L); neg = _roll((rz < 0).astype(float), L)
        idv = np.sign(s1) * (neg - pos) / L
        out = idv if kind == "idraw" else s1  # fip: PRET used for stage 1, ID kept separately
        if kind == "fip":
            return np.where(full, s1, np.nan), np.where(full, idv, np.nan)
    elif kind == "sharpe":
        s2 = _roll(rz * rz, L)
        m = s1 / L
        sd = np.sqrt(np.maximum(s2 / L - m * m, 1e-12))
        out = m / sd
    elif kind == "r2s":
        # regression of log price y on x = 0..L-1 over closes c[t-L..t-1]
        y = np.where(np.isnan(lc), 0.0, lc)
        okc = (~np.isnan(lc)).astype(float)
        tt = np.arange(lc.shape[0], dtype=float)[:, None] * np.ones((1, lc.shape[1]))
        Sy = _roll(y, L); Syy = _roll(y * y, L); Sty = _roll(tt * y, L); nc = _roll(okc, L)
        St = _roll(tt * okc, L); Stt = _roll(tt * tt * okc, L)
        with np.errstate(invalid="ignore", divide="ignore"):
            cov = Sty / L - (St / L) * (Sy / L)
            vt = Stt / L - (St / L) ** 2
            vy = Syy / L - (Sy / L) ** 2
            slope = cov / vt
            r2 = cov * cov / (vt * vy)
        out = (np.exp(slope * 365) - 1) * r2
        full = full & (nc >= L - 0.5)
    elif kind == "resid":
        b = Pn.syms.index("BTCUSDT")
        rb = rz[:, [b]]
        Sb = _roll(rb, L); Sbb = _roll(rb * rb, L); Sxb = _roll(rz * rb, L); Sxx = _roll(rz * rz, L)
        with np.errstate(invalid="ignore", divide="ignore"):
            cov = Sxb / L - (s1 / L) * (Sb / L)
            vb = Sbb / L - (Sb / L) ** 2
            beta = cov / vb
            vx = Sxx / L - (s1 / L) ** 2
            ve = np.maximum(vx - beta * beta * vb, 1e-12)
            out = (s1 - beta * Sb) / (np.sqrt(ve) * np.sqrt(L))
    else:
        raise ValueError(kind)
    return np.where(full, out, np.nan)


def fip_composite(Pn, L, k, univ, kS):
    """Turn the two-stage FIP sort into one score the engine can rank (top-k = longs, bottom-kS = shorts)."""
    pret, idv = score(Pn, "fip", L)
    U = um(univ, Pn)
    T, N = pret.shape
    out = np.full((T, N), np.nan)
    M_L, M_S = 2 * k, 2 * max(kS, 1)
    for i in range(T):
        idx = np.where(U[i] & ~np.isnan(pret[i]) & ~np.isnan(Pn.o[i]))[0]
        if len(idx) < M_L + M_S:
            continue
        order = idx[np.argsort(-pret[i, idx], kind="stable")]
        s = np.zeros(len(order))
        # middle: neutral rank in (-1, 1)
        s[:] = np.linspace(1, -1, len(order) + 2)[1:-1]
        w = order[:M_L]; lo = order[::-1][:M_S]
        out[i, order] = s
        rk_w = np.argsort(np.argsort(idv[i, w], kind="stable"), kind="stable")  # 0 = lowest ID (smoothest)
        out[i, w] = 3.0 - rk_w / M_L
        rk_l = np.argsort(np.argsort(idv[i, lo], kind="stable"), kind="stable")
        out[i, lo] = -3.0 + rk_l / M_S
    return out


def make_score(Pn, kind, L, k, univ, kS):
    if kind == "fip":
        return fip_composite(Pn, L, k, univ, kS)
    return score(Pn, kind, L)


_SC = {}


def run_cfg(kind, L, k, legs, rebal, univ=40, start=DEV_START, end=TEST_END, cost_mult=1.0, record=False, Pn=None, lev=1.0):
    Pn = Pn or P
    kS = k if legs == "LS" else 0
    key = (id(Pn), kind, L, k if kind == "fip" else 0, univ if kind == "fip" else 0, kS if kind == "fip" else 0)
    if key not in _SC:
        _SC[key] = make_score(Pn, kind, L, k, univ, kS)
    prm = dict(k_long=k, k_short=kS, rebal=rebal, lev=lev, mode="mom")
    return E.run(Pn, um(univ, Pn), _SC[key], prm, start, end, cost_mult=cost_mult, record=record)


def daily_returns(curve):
    eq = np.array([e for _, e in curve]); ts = np.array([t for t, _ in curve])
    return ts[:-1], eq[1:] / eq[:-1] - 1


def tstat(months):
    v = np.array(list(months.values()))
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 2 else float("nan")
