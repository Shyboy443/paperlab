"""Worker 2 round 2 / lottery: weekly market-neutral MAX / skewness book on the survivorship-free hourly top-40 panel.

Data: research/monthly20/r4_09_session/base.py (hpanel cache, incl. delisted coins, hourly funding). Read only.

At rebalance day d (00:00 UTC open, bar b = 24 d) everything uses bars < b only:
  daily closes  DCL[d] = close of hour 24d-1 (day d-1's close); daily returns over the last L days
  hourly log returns of the hours 24(d-L) .. 24d-1
Score (higher = MORE lottery-like = SHORT; lowest = LONG):
  max1d : max daily return over L days
  max5d : mean of the 5 largest daily returns over L days (Bali et al. MAX(5))
  maxh  : mean of the 5 largest hourly log returns over L days
  skewh : skewness of hourly log returns over L days
  skewd : skewness of daily returns over L days
  smax  : max5d / std(daily returns, L)       (vol-scaled lottery: tail shape, not volatility)
  smaxh : maxh / std(hourly log returns, L)
Book: universe U40[d] (top-40 by trailing 30-day quote volume, known at the open). Long the k lowest scores, short the
k highest. Equal-risk sizing: inside a leg weights ~ 1/sigma_j (sigma = 480 h hourly vol known at the open, hvol);
legs scaled so long-leg risk = short-leg risk (risk proxy = sum w_j sigma_j), total gross = 1x equity.
Hold R days, fill at the open of bar b; exit at the open of the next rebalance bar. Missing prices are carried
forward (a delisted coin is closed at its last known close). Costs (FEE+SLIP)*cost_mult per side on traded notional
(drifted old weights vs new targets), hourly funding on entry notional (a long pays, a short receives).
"""
import sys
import numpy as np
from datetime import datetime, timezone
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_09_session")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from base import *  # noqa  O, C, U40, FCUM, td, th, syms, hvol, dayidx, FEE, SLIP, ND
from common import summary, DEV_START, TEST_START, TEST_END

d0, dT, d1 = dayidx(DEV_START), dayidx(TEST_START), dayidx(TEST_END)
NB = O.shape[0]
# forward-filled close (for marking and delisting exits)
CF = C.copy()
for t in range(1, NB):
    m = np.isnan(CF[t]); CF[t, m] = CF[t - 1, m]
# daily close known at the open of day d (= raw close of hour 24d-1)
DCL = np.full((ND, O.shape[1]), np.nan)
for d in range(1, ND):
    if 24 * d - 1 < NB:
        DCL[d] = C[24 * d - 1]
DR = np.full_like(DCL, np.nan); DR[1:] = DCL[1:] / DCL[:-1] - 1  # DR[d] = return of day d-1, known at open of d
HLR = np.full_like(C, np.nan); HLR[1:] = np.log(C[1:] / C[:-1])  # HLR[t] = log return of hour t (known at t+1)
SIG = {}


def sigma(d):
    if d not in SIG:
        SIG[d] = hvol(np.array([24 * d]), 480)[0]
    return SIG[d]


_FC = {}


def _skew(x):
    n = np.sum(~np.isnan(x), 0).astype(float)
    mu = np.nanmean(x, 0)
    s = np.nanstd(x, 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.nanmean((x - mu) ** 3, 0) / s ** 3, n, s


def _topk_mean(x, k):
    y = np.where(np.isnan(x), -np.inf, x)
    p = -np.sort(-y, 0)[:k]
    out = p.mean(0)
    out[~np.isfinite(out)] = np.nan
    return out


def feature(name, L, d):
    key = (name, L, d)
    if key in _FC:
        return _FC[key]
    if name.startswith("neg_"):
        _FC[key] = -feature(name[4:], L, d)
        return _FC[key]
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if name in ("max1d", "max5d", "skewd", "smax"):
            x = DR[d - L + 1:d + 1]
            n = np.sum(~np.isnan(x), 0)
            ok = n >= 0.8 * L
            if name == "max1d":
                v = np.nanmax(np.where(np.isnan(x), -np.inf, x), 0)
            elif name == "max5d":
                v = _topk_mean(x, 5)
            elif name == "skewd":
                v = _skew(x)[0]
            else:
                v = _topk_mean(x, 5) / np.nanstd(x, 0)
        else:
            x = HLR[24 * (d - L):24 * d]
            n = np.sum(~np.isnan(x), 0)
            ok = n >= 0.8 * 24 * L
            if name == "maxh":
                v = _topk_mean(x, 5)
            elif name == "skewh":
                v = _skew(x)[0]
            elif name == "smaxh":
                v = _topk_mean(x, 5) / np.nanstd(x, 0)
            elif name == "vol":  # plain hourly vol over L days (for the low-vol comparison)
                v = np.nanstd(x, 0)
            else:
                raise ValueError(name)
    v = np.where(ok & np.isfinite(v), v, np.nan)
    _FC[key] = v
    return v


def select(p, d, exclude=(), rng=None):
    """return (longs, shorts, wL, wS) signed weights per coin index (sum |w| = 1)."""
    s = feature(p["feat"], p["L"], d)
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
    il, is_ = 1 / sg[longs], 1 / sg[shorts]
    if p.get("sizing", "erisk") == "equal":
        il, is_ = np.ones(k), np.ones(k)
    wl, ws = il / il.sum(), is_ / is_.sum()
    rl, rs = (wl * sg[longs]).sum(), (ws * sg[shorts]).sum()
    if p.get("sizing", "erisk") == "erisk":
        gL, gS = rs / (rl + rs), rl / (rl + rs)
    else:
        gL = gS = 0.5
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
