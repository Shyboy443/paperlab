"""Worker 3 round 1 / residual_reversal: market-neutral cross-sectional book on BTC-beta-adjusted (residual) daily returns.

Data: research/monthly20/r4_09_session/base.py (survivorship-free hourly top-40 panel incl. delisted coins, hourly funding).
At rebalance day d (00:00 UTC open, bar b = 24 d) everything uses bars < b only:
  daily log returns DLR[d] = log(close of day d-1 / close of day d-2)   (known at the open of d)
  beta_j = OLS slope of DLR_j on DLR_BTC over the last B days (days d-B+1..d), resid_j = DLR_j - beta_j * DLR_BTC
  sres_j = std of resid_j over those B days
Scores (book is LONG the k LOWEST scores, SHORT the k HIGHEST):
  rrev : sum of resid over the last L days / sres          (residual reversal: long recent residual losers)
  rmom : -(sum of resid over days d-L-s+1 .. d-s) / sres   (idiosyncratic momentum, skip s days: long residual winners)
  raw_rev / raw_mom: same with raw returns (info / comparison)
Book: universe U40[d]; dollar-neutral, equal weight 0.5x per leg (sizing="equal") or equal-risk (sizing="erisk"); gross 1x.
Hold R days, fill at the open of bar b, exit at the next rebalance open. Costs (FEE+SLIP)*cost_mult per side on traded
notional (drifted weights vs targets), hourly funding. Delisted coins closed at last known close.
The sim() engine is copied verbatim from research/worker2/round2/lottery/core.py.
"""
import sys, warnings
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_09_session")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from base import *  # noqa
from common import summary, DEV_START, TEST_START, TEST_END

d0, dT, d1 = dayidx(DEV_START), dayidx(TEST_START), dayidx(TEST_END)
NB = O.shape[0]
CF = C.copy()
for t in range(1, NB):
    m = np.isnan(CF[t]); CF[t, m] = CF[t - 1, m]
DCL = np.full((ND, O.shape[1]), np.nan)
for d in range(1, ND):
    if 24 * d - 1 < NB:
        DCL[d] = C[24 * d - 1]
DLR = np.full_like(DCL, np.nan); DLR[1:] = np.log(DCL[1:] / DCL[:-1])
SIG = {}


def sigma(d):
    if d not in SIG:
        SIG[d] = hvol(np.array([24 * d]), 480)[0]
    return SIG[d]


_RC = {}


def resid_block(d, B):
    """(resid matrix for days d-B+1..d, sres, raw matrix) with beta estimated on the same past window."""
    key = (d, B)
    if key in _RC:
        return _RC[key]
    x = DLR[d - B + 1:d + 1]
    m = x[:, BTC]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ok = np.isfinite(x) & np.isfinite(m)[:, None]
        n = ok.sum(0)
        mm = np.where(ok, m[:, None], np.nan)
        mx, mb = np.nanmean(np.where(ok, x, np.nan), 0), np.nanmean(mm, 0)
        cov = np.nanmean((np.where(ok, x, np.nan) - mx) * (mm - mb), 0)
        var = np.nanmean((mm - mb) ** 2, 0)
        beta = cov / var
        res = x - beta * m[:, None]
        sres = np.nanstd(res, 0)
    good = (n >= 0.8 * B) & np.isfinite(beta) & (sres > 0)
    sres = np.where(good, sres, np.nan)
    _RC[key] = (res, sres, x, beta)
    if len(_RC) > 6000:
        _RC.clear()
    return _RC[key]


_FC = {}


def feature(p, d):
    name, L, B, s = p["feat"], p["L"], p.get("B", 60), p.get("skip", 0)
    key = (name, L, B, s, d)
    if key in _FC:
        return _FC[key]
    W = max(B, L + s)
    res, sres, raw, beta = resid_block(d, W) if W != B else resid_block(d, B)
    if W != B:  # beta window must stay B: recompute residuals of the long window with the B-day beta
        _, sres, _, beta = resid_block(d, B)
        x = DLR[d - W + 1:d + 1]
        res = x - beta * x[:, BTC][:, None]
        raw = x
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if name in ("rrev", "raw_rev"):
            seg = (res if name == "rrev" else raw)[-L:]
        else:
            seg = (res if name == "rmom" else raw)[len(res) - L - s:len(res) - s]
        nn = np.isfinite(seg).sum(0)
        v = np.nansum(seg, 0)
        if name in ("rrev", "rmom"):
            v = v / sres
        else:
            v = v / np.nanstd(DLR[d - B + 1:d + 1], 0)
        if name in ("rmom", "raw_mom"):
            v = -v
    v = np.where((nn >= 0.8 * L) & np.isfinite(v), v, np.nan)
    _FC[key] = v
    if len(_FC) > 20000:
        _FC.clear()
    return v


def select(p, d, exclude=(), rng=None):
    s = feature(p, d)
    b = 24 * d
    sg = sigma(d)
    elig = U40[d] & np.isfinite(s) & np.isfinite(O[b]) & np.isfinite(sg) & (sg > 0)
    if len(exclude):
        elig[list(exclude)] = False
    idx = np.where(elig)[0]
    k = p["k"]
    if len(idx) < 2 * k:
        return None
    if rng is None:
        order = idx[np.argsort(s[idx], kind="stable")]
        longs, shorts = order[:k], order[::-1][:k]
    else:
        pk = rng.choice(idx, 2 * k, replace=False)
        longs, shorts = pk[:k], pk[k:]
    w = np.zeros(O.shape[1])
    if p.get("sizing", "equal") == "erisk":
        il, is_ = 1 / sg[longs], 1 / sg[shorts]
        wl, ws = il / il.sum(), is_ / is_.sum()
        rl, rs = (wl * sg[longs]).sum(), (ws * sg[shorts]).sum()
        gL, gS = rs / (rl + rs), rl / (rl + rs)
    else:
        wl, ws, gL, gS = np.ones(k) / k, np.ones(k) / k, 0.5, 0.5
    w[longs] = gL * wl
    w[shorts] = -gS * ws
    return w


def sim(p, da, db, cost_mult=1.0, exclude=(), rng=None, record=False):
    """returns (curve [(ms, eq)], positions [(j, side, d_open, d_close, pnl$)], n_trades)."""
    cost = (FEE + SLIP) * cost_mult
    R = p["R"]
    eq = 10_000.0
    curve = [(int(td[da]), eq)]
    N = O.shape[1]
    w_old = np.zeros(N)  # drifted weights (fraction of equity) at the moment before rebalancing
    pos_pnl = np.zeros(N); pos_open = np.zeros(N, int); pos_side = np.zeros(N)
    positions = []; ntr = 0
    d = da
    while d < db:
        e = min(d + R, db)
        b, be = 24 * d, min(24 * e, NB - 1)
        wn = select(p, d, exclude, rng)
        if wn is None:
            wn = np.zeros(N)
        traded = np.abs(wn - w_old)
        ntr += int(np.sum(traded > 1e-9))
        c0 = traded.sum() * cost * eq
        eq -= c0
        # position bookkeeping
        for j in np.where((w_old != 0) & ((wn == 0) | (np.sign(wn) != np.sign(w_old))))[0]:
            positions.append((j, pos_side[j], pos_open[j], d, pos_pnl[j])); pos_pnl[j] = 0
        new = (wn != 0) & ((w_old == 0) | (np.sign(wn) != np.sign(w_old)))
        pos_open[new] = d; pos_side[new] = np.sign(wn[new])
        pos_pnl -= traded * cost * eq
        live = np.where(wn != 0)[0]
        px0 = O[b, live]
        eq0 = eq
        for dd in range(d + 1, e + 1):
            bb = min(24 * dd, NB - 1)
            px = O[bb, live] if dd == e else CF[bb - 1, live]
            px = np.where(np.isfinite(px), px, CF[bb - 1, live])
            px = np.where(np.isfinite(px), px, px0)
            g = wn[live] * (px / px0 - 1) - wn[live] * (FCUM[bb, live] - FCUM[b, live])
            eq = eq0 * (1 + g.sum())
            curve.append((int(td[dd]) if dd < ND else int(td[-1]) + 86400000, eq))
        pos_pnl[live] += eq0 * g
        # drift weights
        val = wn[live] * (px / px0)
        w_old = np.zeros(N)
        w_old[live] = eq0 * val / eq if eq > 0 else 0
        d = e
    # close all at end
    eq -= np.abs(w_old).sum() * cost * eq
    for j in np.where(w_old != 0)[0]:
        positions.append((j, pos_side[j], pos_open[j], db, pos_pnl[j]))
    curve[-1] = (curve[-1][0], eq)
    return curve, positions, ntr


def stats(curve, ntr=None):
    s = summary(curve)
    v = np.array(list(s["months"].values()))
    s["t_stat"] = round(float(v.mean() / v.std(ddof=1) * np.sqrt(len(v))), 2) if len(v) > 2 else None
    if ntr is not None:
        s["trades_per_month"] = round(ntr / len(v), 1)
    return s


def yearly(months):
    y = {}
    for k, v in months.items():
        y[k[:4]] = y.get(k[:4], 1.0) * (1 + v / 100)
    return {k: round((v - 1) * 100, 2) for k, v in y.items()}
