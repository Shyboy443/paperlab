"""lit_scout2 (Worker 3 round 5): two literature ideas on the cached daily panels.

A = TSD  : Zarattini, Pagani & Barbon (2025) "Catching Crypto Trends" - long-only time-series Donchian ensemble
           (9 lookbacks, entry on close > prior L-day max close, trailing stop = max(prev stop, L-day mid-channel)),
           sized to a volatility target, rotating point-in-time liquid universe, cash otherwise.
B = CTR  : Fieberg, Liedtke, Poddig, Walker & Zaremba (JFQA 2025) "A Trend Factor for the Cross-Section of
           Cryptocurrency Returns" (CTREND) - RECONSTRUCTION: price- and volume-moving-average + oscillator signals,
           cross-sectionally ranked, aggregated by rolling weekly cross-sectional ridge regressions (Han-Zhou-Zhu style
           averaged coefficients), long top-k / short bottom-k of the point-in-time top-40.
Engines: TSD uses the weight engine below (close-to-close, signals through close i-1 executed at close i-1);
CTR uses round-4 high52_v2 lib.run (engine_r2 copy) unchanged.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round4/high52_v2")
import lib as L4  # noqa
from lib import DEV_START, DEV_END, TEST_START, TEST_END, HO_START, HO_END, FEE, SLIP, DAY, summary  # noqa

OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r5_lit_scout2")
OUT.mkdir(parents=True, exist_ok=True)
_C = {}


def panels():
    D = L4.build(); H = L4.holdout_panel()
    return D, L4.make_panel(D), H, L4.make_panel(H)


def _shift(a, k=1):
    out = np.full(a.shape, np.nan); out[k:] = a[:-k]; return out


# ---------------------------------------------------------------- A: TSD
LOOKS = (5, 10, 20, 30, 60, 90, 150, 250, 360)


def tsd_state(Pn, Ls):
    """mean over lookbacks of the 0/1 Donchian state, known at the CLOSE of day i (row i)."""
    key = (id(Pn), "tsd", tuple(Ls))
    if key in _C:
        return _C[key]
    c = Pn.c; T, N = c.shape
    cdf = pd.DataFrame(c)
    acc = np.zeros((T, N))
    for L in Ls:
        mx = cdf.rolling(L, min_periods=L).max().values
        mn = cdf.rolling(L, min_periods=L).min().values
        pmx = _shift(mx)
        mid = (mx + mn) / 2
        on = np.zeros(N, bool); stop = np.full(N, -np.inf)
        st = np.zeros((T, N))
        for i in range(T):
            ci = c[i]
            valid = ~np.isnan(ci)
            # exits first (stop known from previous day), then update stop, then entries
            with np.errstate(invalid="ignore"):
                ex = on & valid & (ci < stop)
            on &= ~ex; on &= valid | on  # keep state through NaN days (engine forces exit on NaN)
            m = mid[i]
            upd = on & ~np.isnan(m)
            stop[upd] = np.maximum(stop[upd], m[upd])
            with np.errstate(invalid="ignore"):
                ent = ~on & valid & ~np.isnan(pmx[i]) & (ci > pmx[i])
            on |= ent
            stop[ent] = np.where(np.isnan(m[ent]), -np.inf, m[ent])
            stop[~on] = -np.inf
            st[i] = on
        acc += st
    s = acc / len(Ls)
    _C[key] = s
    return s


def rvol(Pn, W):
    key = (id(Pn), "rv", W)
    if key not in _C:
        lr = np.log(pd.DataFrame(Pn.c)).diff()
        _C[key] = (lr.rolling(W, min_periods=int(W * 0.8)).std() * np.sqrt(365)).values
    return _C[key]


TSD_DEFAULT = dict(univ=20, tv=1.0, cap=2.0, vw=90, scale=1.0, band=0.0, rebal=1, age=0)


def tsd_weights(Pn, cfg):
    """W[i] = target weight held over day i (close i-1 -> close i), from data through close i-1."""
    c = dict(TSD_DEFAULT); c.update(cfg)
    Ls = tuple(max(2, int(round(L * c["scale"]))) for L in LOOKS)
    s = tsd_state(Pn, Ls); v = rvol(Pn, c["vw"])
    with np.errstate(invalid="ignore", divide="ignore"):
        size = np.minimum(c["cap"], c["tv"] / v)
    raw = np.nan_to_num(s * size) / c["univ"]
    raw = _shift(raw); raw = np.nan_to_num(raw)
    U = L4.um(Pn, c["univ"], c["age"])
    raw = np.where(U, raw, 0.0)
    g = raw.sum(1, keepdims=True)
    raw = np.where(g > 1, raw / np.maximum(g, 1e-12), raw)
    return raw


def run_w(Pn, W, start, end, cost_mult=1.0, band=0.0, rebal=1, record=False, eq0=10_000.0):
    """Daily weight engine. Costs (fee+slip) x |traded notional|, funding = -w * fund[i], 1% prior-day volume cap,
    coin with no close on day i -> return 0 that day and forced out (weight 0 from day i+1, traded at last price).
    band: skip re-sizing a held coin if |target - current| <= band * |target| (entries/exits always trade).
    rebal: re-size only every `rebal` days (entries/exits on any day)."""
    cost = (FEE + SLIP) * cost_mult
    i0 = int(np.searchsorted(Pn.t, start)); i1 = int(np.searchsorted(Pn.t, end))
    T, N = Pn.c.shape
    cprev = _shift(Pn.c)
    with np.errstate(invalid="ignore", divide="ignore"):
        R = Pn.c / cprev - 1
    eq = eq0; w = np.zeros(N)
    curve = [(int(Pn.t[i0]), eq)]
    pnl_day = np.zeros((i1 - i0, N)) if record else None
    turn = 0.0; gross = []
    for i in range(i0, i1):
        tgt = W[i].copy()
        okp = ~np.isnan(cprev[i])
        tgt[~okp] = 0.0
        qcap = 0.01 * np.nan_to_num(Pn.qv[i - 1]) / max(eq, 1e-9)
        tgt = np.minimum(tgt, qcap) if (tgt >= 0).all() else np.clip(tgt, -qcap, qcap)
        held = (w != 0) & (tgt != 0) & (np.sign(w) == np.sign(tgt))
        keep = held & ((np.abs(tgt - w) <= band * np.abs(tgt)) | ((i - i0) % rebal != 0))
        tgt[keep] = w[keep]
        tr = np.abs(tgt - w)
        cst = tr * cost * eq
        turn += tr.sum()
        r = np.nan_to_num(R[i])
        f = np.nan_to_num(Pn.fund[i])
        contrib = tgt * (r - f)
        if record:
            pnl_day[i - i0] = contrib * eq - cst
        port = contrib.sum()
        eq = eq * (1 + port) - cst.sum()
        gross.append(np.abs(tgt).sum())
        # drift
        if 1 + port != 0:
            w = tgt * (1 + r) / (1 + port)
        w[np.isnan(Pn.c[i])] = 0.0  # forced out at last price (already marked)
        curve.append((int(Pn.t[i] + DAY - 1), eq))
    out = summary(curve, None)
    nm = max(out["n_months"], 1)
    out["turnover_per_month"] = round(turn / nm, 2); out["avg_gross"] = round(float(np.mean(gross)), 3)
    out["_curve"] = curve
    if record:
        # positions = contiguous spells with nonzero target weight
        Wn = W[i0:i1] != 0
        pos = []
        for j in np.where(Wn.any(0))[0]:
            col = Wn[:, j]; d = np.diff(np.r_[0, col.astype(int), 0])
            for a, b in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
                pos.append((j, 1, a, b, float(pnl_day[a:b + 1, j].sum()), 0.0))
        out["_positions"] = pos
        out["_coin_pnl"] = pnl_day.sum(0)
        out["trades"] = len(pos)
    return out


def run_tsd(Pn, cfg, start, end, cost_mult=1.0, record=False, cols_drop=None):
    c = dict(TSD_DEFAULT); c.update(cfg)
    W = tsd_weights(Pn, c)
    if cols_drop is not None:
        W = W.copy(); W[:, cols_drop] = 0.0
    r = run_w(Pn, W, start, end, cost_mult, band=c["band"], rebal=c["rebal"], record=record)
    if "trades" not in r:
        r["trades"] = int(((np.diff(np.r_[np.zeros((1, W.shape[1])), W[int(np.searchsorted(Pn.t, start)):int(np.searchsorted(Pn.t, end))] != 0], axis=0) > 0)).sum())
    return r


# ---------------------------------------------------------------- B: CTREND reconstruction
def _rank(x, mask):
    """cross-sectional rank in [-0.5, 0.5] among mask & finite; NaN elsewhere."""
    out = np.full(x.shape, np.nan)
    for i in range(x.shape[0]):
        m = mask[i] & np.isfinite(x[i])
        n = m.sum()
        out[i, mask[i]] = 0.0  # universe member with the indicator not yet computable -> neutral rank
        if n >= 5:
            rk = pd.Series(x[i, m]).rank().values
            out[i, m] = (rk - 1) / (n - 1) - 0.5
    return out


def ctr_features(Pn, fs="pv"):
    """feature stack F[f, i, j] known at close of day i (row i). Universe = top-40."""
    key = (id(Pn), "feat", fs)
    if key in _C:
        return _C[key]
    c = pd.DataFrame(Pn.c); q = pd.DataFrame(np.where(np.isnan(Pn.qv), np.nan, Pn.qv))
    h = pd.DataFrame(Pn.h); l = pd.DataFrame(Pn.l)
    feats = []
    for Lm in (3, 5, 10, 20, 50, 100, 200):
        feats.append((c / c.rolling(Lm, min_periods=Lm).mean() - 1).values)
    if "v" in fs:
        for Lm in (3, 5, 10, 20, 50, 100, 200):
            feats.append((q.rolling(Lm, min_periods=Lm).mean() / q.rolling(200, min_periods=100).mean()).values)
    if "o" in fs or "v" in fs:
        d = c.diff(); up = d.clip(lower=0).rolling(14).mean(); dn = (-d.clip(upper=0)).rolling(14).mean()
        feats.append((up / (up + dn)).values)                                   # RSI14
        hh = h.rolling(14).max(); ll = l.rolling(14).min()
        feats.append(((c - ll) / (hh - ll)).values)                              # stochastic %K14
        e12 = c.ewm(span=12, adjust=False).mean(); e26 = c.ewm(span=26, adjust=False).mean()
        macd = (e12 - e26) / c; feats.append(macd.values)
        feats.append((macd - macd.ewm(span=9, adjust=False).mean()).values)     # MACD histogram
        m20 = c.rolling(20).mean(); s20 = c.rolling(20).std()
        feats.append(((c - m20) / (2 * s20)).values)                             # Bollinger %b (centered)
        tp = (h + l + c) / 3; mtp = tp.rolling(20).mean()
        mad = 0.8 * tp.rolling(20).std()                                         # ~mean abs deviation (normal)
        feats.append(((tp - mtp) / (0.015 * mad)).values)                        # CCI20 (std-based MAD)
    U = L4.um(Pn, 40)
    # rank at close of day i among the universe that will trade on day i+1 (um row i+1 uses qv through day i)
    Unext = np.vstack([U[1:], U[-1:]])
    F = np.stack([_rank(np.asarray(f, float), Unext) for f in feats])
    _C[key] = F
    return F


def ctr_score(Pn, fs="pv", Wk=52, lam=1.0, H=7):
    """score[i] for trading at open of day i: uses features at close i-1 and coefficient estimates from cross-sections
    s (features at close s) whose H-day forward return (open s+1 -> open s+1+H) is complete by open i  (s+1+H <= i)."""
    key = (id(Pn), "ctr", fs, Wk, lam, H)
    if key in _C:
        return _C[key]
    F = ctr_features(Pn, fs); nf, T, N = F.shape
    o = Pn.o
    R20 = np.where(np.isfinite(pd.DataFrame(Pn.c).rolling(20, min_periods=20).mean().values), 1.0, np.nan)
    fwd = np.full((T, N), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        fwd[:T - 1 - H] = o[1 + H:T] / o[1:T - H] - 1
    beta = np.full((T, nf), np.nan)
    for s in range(T):
        X = F[:, s, :].T; y = fwd[s]
        m = np.isfinite(X).all(1) & np.isfinite(y) & np.isfinite(R20[s])
        if m.sum() < 15:
            continue
        yy = y[m]; yy = np.clip(yy, *np.percentile(yy, [2.5, 97.5])); yy = yy - yy.mean()
        Xm = X[m]
        beta[s] = np.linalg.solve(Xm.T @ Xm + lam * np.eye(nf), Xm.T @ yy)
    score = np.full((T, N), np.nan)
    for i in range(T):
        last = i - 1 - H
        if last < 0:
            continue
        idx = np.arange(last, max(last - Wk * 7, -1), -7)  # weekly-spaced cross-sections
        b = beta[idx]; b = b[np.isfinite(b).all(1)]
        if len(b) < max(8, Wk // 3):
            continue
        X = F[:, i - 1, :].T
        sc = X @ b.mean(0) * R20[i - 1]
        score[i] = sc
    _C[key] = score
    return score


CTR_DEFAULT = dict(fs="pv", Wk=52, lam=1.0, H=7, k=10, univ=40, rebal=7)


def run_ctr(Pn, cfg, start, end, cost_mult=1.0, record=False):
    c = dict(CTR_DEFAULT); c.update(cfg)
    sc = ctr_score(Pn, c["fs"], c["Wk"], c["lam"], c["H"])
    prm = dict(k_long=c["k"], k_short=c["k"], rebal=c["rebal"], lev=1.0)
    return L4.run(Pn, L4.um(Pn, c["univ"]), sc, prm, start, end, cost_mult=cost_mult, record=record)


# ---------------------------------------------------------------- stats
def tstat(months):
    v = np.array(list(months.values()))
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 2 and v.std() > 0 else float("nan")


def stats(r):
    m = r["months"]; nm = len(m)
    d = dict(median=r["median_month"], geo=r["geo_month"], mean=r["mean_month"], up=f'{r["months_up"]}/{nm}',
             max_dd=r["max_dd_pct"], net=r["net_pct"], t=round(tstat(m), 2),
             trades_per_month=round(r.get("trades", 0) / max(nm, 1), 1), worst=r["worst_month"], best=r["best_month"],
             turnover_x_per_month=r.get("turnover_per_month"))
    if "avg_gross" in r:
        d["avg_gross"] = r["avg_gross"]
    return d


def mstats(vals):
    v = np.array(vals, float)
    eq = np.cumprod(1 + v / 100); pk = np.maximum.accumulate(np.r_[1, eq])[1:]
    t = float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if v.std() > 0 else float("nan")
    return dict(n=len(v), median=round(float(np.median(v)), 2), geo=round(float((eq[-1] ** (1 / len(v)) - 1) * 100), 2),
                mean=round(float(v.mean()), 2), up=f"{int((v > 0).sum())}/{len(v)}",
                max_dd_monthly=round(float((1 - eq / pk).max() * 100), 2), t=round(t, 2),
                net=round(float((eq[-1] - 1) * 100), 2))
