"""Realized higher moments from hourly returns (Amaya, Christoffersen, Jacobs & Vasquez 2015, JFE) on the
survivorship-free panel, traded through the unchanged agent-02 rotation engine (engine_r2.run: score known at day d
00:00 UTC open, fill at that open, fee 0.05% + slip 0.02% per side, funding, 1% volume cap, 1x).

Hourly log returns r_t = ln(O[t+1]/O[t]) from research/monthly20/r4_09_session hourly panel (all coins ever in the
point-in-time top-40, incl. delisted). For day d the window is the W*24 hourly returns that ended by day d's open
(hours d*24-W*24 .. d*24-1), so nothing after the fill is used.
  RVar  = sum r^2
  RSkew = sqrt(N) * sum r^3 / RVar^1.5        (ACJV eq. 2, no demeaning)
  RKurt = N * sum r^4 / RVar^2
  MAXh  = max hourly r in the window, MAXd = max daily (24h, aligned to 00:00) return in the window (for the MAX overlap)
Coins need >= 80% of the window's hourly returns.
Score sign follows the literature (fixed before the grid): skew -> long LOW skew / short HIGH skew;
kurt -> long HIGH kurt / short LOW kurt (Jia, Liu & Yan 2021 FRL, crypto: kurtosis positive, skewness negative).
"""
from __future__ import annotations
import sys
import numpy as np
import importlib.util
_sp = importlib.util.spec_from_file_location("h52lib", r"D:/Projects/BinanceBOt/paperlab/research/worker3/round2/high52/lib.py")
L2 = importlib.util.module_from_spec(_sp); _sp.loader.exec_module(L2)  # daily panel P, engine E, universe masks, helpers
P, D, E, um, daily_returns, corr_w1, tstat = L2.P, L2.D, L2.E, L2.um, L2.daily_returns, L2.corr_w1, L2.tstat
DEV_START, DEV_END, TEST_START, TEST_END = L2.DEV_START, L2.DEV_END, L2.TEST_START, L2.TEST_END

Z = np.load(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r4_session/hpanel.npz")
HS = [str(s) for s in Z["syms"]]
O = Z["o"].astype(np.float64)
assert int(Z["th"][0]) == int(P.t[0])
LR = np.full_like(O, np.nan); LR[:-1] = np.log(O[1:] / O[:-1])
NH, NC = LR.shape
COL = np.array([P.syms.index(s) for s in HS])  # hourly column -> daily panel column
x = np.nan_to_num(LR); n = (~np.isnan(LR)).astype(float)


def _cs(a):
    return np.vstack([np.zeros((1, NC)), np.cumsum(a, 0)])


C1, C2, C3, C4, CN = _cs(x), _cs(x * x), _cs(x ** 3), _cs(x ** 4), _cs(n)
ND = P.c.shape[0]
_CACHE = {}


def moments(W):
    """dict of day x daily-panel-col arrays (NaN where unknown) for window W days."""
    if W in _CACHE:
        return _CACHE[W]
    b = np.minimum(np.arange(ND) * 24, NH)  # sum over hours a..b-1
    a = np.maximum(b - W * 24, 0)
    cnt = CN[b] - CN[a]
    s2 = C2[b] - C2[a]; s3 = C3[b] - C3[a]; s4 = C4[b] - C4[a]
    ok = (cnt >= 0.8 * W * 24) & (s2 > 0) & (b[:, None] - a[:, None] == W * 24)
    with np.errstate(invalid="ignore", divide="ignore"):
        sk = np.sqrt(cnt) * s3 / s2 ** 1.5
        ku = cnt * s4 / s2 ** 2
        rv = s2
    out = {}
    for k, v in (("skew", sk), ("kurt", ku), ("rvar", rv)):
        full = np.full((ND, len(P.syms)), np.nan)
        full[:, COL] = np.where(ok, v, np.nan)
        out[k] = full
    _CACHE[W] = out
    return out


def max_scores(W):
    """MAXh (max hourly log return) and MAXd (max 24h log return of the W days) known at day d open."""
    lrd = np.full((ND, NC), np.nan)
    for d in range(ND):
        h = d * 24
        if h + 24 < NH:
            lrd[d] = np.log(O[h + 24] / O[h])
    mh = np.full((ND, NC), np.nan); md = np.full((ND, NC), np.nan)
    for d in range(W, ND):
        blk = LR[(d - W) * 24:d * 24]
        with np.errstate(all="ignore"):
            mh[d] = np.nanmax(blk, 0) if blk.size else np.nan
            md[d] = np.nanmax(lrd[d - W:d], 0)
    f1 = np.full((ND, len(P.syms)), np.nan); f2 = f1.copy()
    f1[:, COL] = mh; f2[:, COL] = md
    return f1, f2


def score(stat, W, sign="lit"):
    m = moments(W)[stat]
    lit = -1.0 if stat == "skew" else 1.0  # higher score = long
    s = lit if sign == "lit" else -lit
    return s * m


def run_cfg(stat, W, k, rebal=7, univ=40, sign="lit", start=DEV_START, end=TEST_END, cost_mult=1.0, record=False, Pn=None,
            cols=None):
    Pn = Pn or P
    sc = score(stat, W, sign)
    if cols is not None:
        sc = sc[:, cols]
    prm = dict(k_long=k, k_short=k, rebal=rebal, lev=1.0, mode="mom")
    return E.run(Pn, um(univ, Pn), sc, prm, start, end, cost_mult=cost_mult, record=record)
