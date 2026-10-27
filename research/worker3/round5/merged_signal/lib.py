"""merged_signal (Worker 3 round 5): merge Worker 1 (r4_07 ensemble R+M+T) with the high52 (Donchian-30 range) signal
inside ONE book, on Worker 1's own engine (r4_07_ensemble/ens.py, unchanged).

Panels:
  perp  = data/monthly20_r2_trend/panel.npz (W1's DEV/TEST panel, funding)      ML preds data/monthly20_r2_ml
  spot  = data/w1_backfill/panel.npz        (W1's 2018-2020 backfill, funding 0, shorts assumed possible on spot)
                                                                                 ML preds data/w1_backfill/ml
Research only: no orders, no keys.
"""
from __future__ import annotations
import sys, math
from pathlib import Path
import numpy as np
import pandas as pd

M20 = Path(r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
sys.path.insert(0, str(M20)); sys.path.insert(0, str(M20 / "r4_07_ensemble"))
import ens  # noqa: E402  (frozen Worker 1 code)
from common import utc, DAY, DEV_START, DEV_END, TEST_START, TEST_END  # noqa: E402

PROJ = Path(r"D:/Projects/BinanceBOt/paperlab")
HO_START, HO_END = utc(2018, 1, 1), utc(2021, 1, 1)
FULL_START = DEV_START
PATHS = dict(perp=(PROJ / "data/monthly20_r2_trend/panel.npz", PROJ / "data/monthly20_r2_ml", 1577836800000),
             spot=(PROJ / "data/w1_backfill/panel.npz", PROJ / "data/w1_backfill/ml", utc(2017, 8, 1)))


def _point(kind):
    ens.PANEL, ens.MLD, ens.DATA_FROM_ML = PATHS[kind]


def load(kind):
    _point(kind)
    D = ens.Data()
    D.kind = kind
    D.cache = {}
    return D


# ------------------------------------------------------------------ features (all use data up to d-1 only)
def _shift(a):
    out = np.full(a.shape, np.nan); out[1:] = a[:-1]; return out


def rng(D, L=30):
    """high52 score: (close - L-day low) / (L-day high - L-day low) on bars d-L..d-1, as in round4/high52_v2/lib.rng."""
    k = ("rng", L)
    if k not in D.cache:
        mp = int(round(L * 0.9))
        hx = pd.DataFrame(D.H).rolling(L, min_periods=mp).max().values
        lx = pd.DataFrame(D.L).rolling(L, min_periods=mp).min().values
        with np.errstate(invalid="ignore", divide="ignore"):
            s = (D.C - lx) / (hx - lx)
        D.cache[k] = _shift(s)
    return D.cache[k]


def recency(D, L=30):
    """Bhootra-Hur style: minus the number of days since the L-day high (recent high = higher score), data to d-1."""
    k = ("rec", L)
    if k not in D.cache:
        Hh = np.where(np.isnan(D.H), -np.inf, D.H)
        out = np.full(D.H.shape, np.nan)
        from numpy.lib.stride_tricks import sliding_window_view
        win = sliding_window_view(Hh, L, axis=0)            # (T-L+1, N, L): rows t..t+L-1
        am = win.argmax(axis=2)                              # index of the high inside the window
        out[L - 1:] = -(L - 1 - am).astype(float)
        out[np.isnan(rng(D, L) if False else D.C)] = np.nan
        D.cache[k] = _shift(out)
    return D.cache[k]


def ret_lb(D, lb=14):
    s = np.full((D.T, D.N), np.nan)
    s[1 + lb:] = D.C[lb:-1] / D.C[:-1 - lb] - 1
    return s


def univ(D, n=40, drop=()):
    """leg_rotation's universe rule (top-n 30-day quote volume, >= 24 bars, listed >= 30 days)."""
    k = ("U", n, tuple(sorted(drop)))
    if k not in D.cache:
        dropj = np.array([D.col[s] for s in drop if s in D.col], int)
        U = np.zeros((D.T, D.N), bool)
        for d in range(30, D.T):
            vol = D.qcs[d] - D.qcs[d - 30]
            ok = (D.hcs[d] - D.hcs[d - 30] >= 24) & (D.first <= d - 30)
            if len(dropj):
                ok[dropj] = False
            v = np.where(ok, vol, -1)
            top = np.argsort(-v)[:n]
            U[d, top[v[top] > 0]] = True
        D.cache[k] = U
    return D.cache[k]


def _pr(x):
    """percentile rank in [0,1] of a 1-d array (no NaN)."""
    r = np.argsort(np.argsort(x, kind="stable"), kind="stable").astype(float)
    return r / max(len(x) - 1, 1)


# ------------------------------------------------------------------ legs (F = signed sleeve fraction per coin)
def leg_H(D, L=30, k=10, n=40, drop=(), rng_=None, long_only=False):
    """high52 alone: long top-k / short bottom-k of rng among the top-n universe, daily."""
    U = univ(D, n, drop); s = rng(D, L)
    F = np.zeros((D.T, D.N))
    for d in range(31, D.T):
        el = np.where(U[d] & ~np.isnan(s[d]) & D.has[d])[0]
        if len(el) < 2 * k:
            continue
        order = rng_.permutation(el) if rng_ is not None else el[np.argsort(-s[d, el], kind="stable")]
        if long_only:
            F[d, order[:k]] = 1.0 / k
        else:
            F[d, order[:k]] = 0.5 / k; F[d, order[::-1][:k]] = -0.5 / k
    return F


def leg_rank(D, w_mom=0.5, lb=14, L=30, k=10, n=40, use_rec=False, drop=(), rng_=None):
    """Rank-average of W1's rotation score (lb-day return) and the high52 range score (+ recency rank if use_rec)."""
    U = univ(D, n, drop); a = ret_lb(D, lb); b = rng(D, L); c = recency(D, L) if use_rec else None
    F = np.zeros((D.T, D.N))
    for d in range(31, D.T):
        ok = U[d] & ~np.isnan(a[d]) & ~np.isnan(b[d]) & D.has[d]
        if use_rec:
            ok &= ~np.isnan(c[d])
        el = np.where(ok)[0]
        if len(el) < 2 * k:
            continue
        if rng_ is not None:
            order = rng_.permutation(el)
        else:
            if use_rec:
                sc = (_pr(a[d, el]) + _pr(b[d, el]) + _pr(c[d, el])) / 3
            else:
                sc = w_mom * _pr(a[d, el]) + (1 - w_mom) * _pr(b[d, el])
            order = el[np.argsort(-sc, kind="stable")]
        F[d, order[:k]] = 0.5 / k; F[d, order[::-1][:k]] = -0.5 / k
    return F


def leg_R_refill(D, X, lb=14, L=30, k=10, n=40, drop=(), rng_=None):
    """W1 rotation but longs only from coins with rng >= X, shorts only from rng <= 1-X (top/bottom by lb return)."""
    U = univ(D, n, drop); a = ret_lb(D, lb); b = rng(D, L)
    F = np.zeros((D.T, D.N))
    for d in range(31, D.T):
        el = np.where(U[d] & ~np.isnan(a[d]) & ~np.isnan(b[d]) & D.has[d])[0]
        if len(el) < 2 * k:
            continue
        lc = el[b[d, el] >= X]; sc = el[b[d, el] <= 1 - X]
        if rng_ is not None:
            lo = rng_.permutation(lc)[:k]; sh = rng_.permutation(sc)[:k]
        else:
            lo = lc[np.argsort(-a[d, lc], kind="stable")][:k]; sh = sc[np.argsort(a[d, sc], kind="stable")][:k]
        F[d, lo] = 0.5 / k; F[d, sh] = -0.5 / k
    return F


def leg_R_hold(D, X, lb=14, L=30, k=10, n=40, drop=(), rng_=None):
    """high52-style holding for W1's rotation: a long stays while rng >= X (short while rng <= 1-X) and it is in the
    universe; empty slots are refilled DAILY from the top (bottom) lb-day return ranks. Daily resize."""
    U = univ(D, n, drop); a = ret_lb(D, lb); b = rng(D, L)
    F = np.zeros((D.T, D.N))
    longs, shorts = set(), set()
    for d in range(31, D.T):
        el = np.where(U[d] & ~np.isnan(a[d]) & ~np.isnan(b[d]) & D.has[d])[0]
        if len(el) < 2 * k:
            longs, shorts = set(), set(); continue
        es = set(el.tolist())
        longs = {j for j in longs if j in es and b[d, j] >= X}
        shorts = {j for j in shorts if j in es and b[d, j] <= 1 - X}
        order = rng_.permutation(el) if rng_ is not None else el[np.argsort(-a[d, el], kind="stable")]
        for j in order:
            if len(longs) >= k:
                break
            if j not in shorts:
                longs.add(int(j))
        for j in order[::-1]:
            if len(shorts) >= k:
                break
            if j not in longs:
                shorts.add(int(j))
        F[d, list(longs)] = 0.5 / k; F[d, list(shorts)] = -0.5 / k
    return F


def filt(D, F, X, L=30, longs_only=False):
    """high52 filter on an existing leg's targets: drop longs with rng < X and shorts with rng > 1-X (-> cash)."""
    b = rng(D, L)
    G = F.copy()
    bad_l = (F > 0) & ~(b >= X)
    G[bad_l] = 0
    if not longs_only:
        G[(F < 0) & ~(b <= 1 - X)] = 0
    return G


# ------------------------------------------------------------------ W1 legs (exactly r4_07 run.LEGP)
def w1_leg(D, kind, drop=(), rng_=None, p=None):
    _point(D.kind)
    P = dict(R=dict(lb=14, k=10, univ=40), M=dict(k=3, seed=0), T=dict(look=60, univ=50))[kind]
    P.update(p or {})
    if kind == "R":
        return ens.leg_rotation(D, P["lb"], P["k"], P["univ"], drop=drop, rng=rng_)
    if kind == "M":
        return ens.leg_ml(D, P["k"], P["seed"], drop=drop, rng=rng_)
    return ens.leg_mom(D, P["look"], P["univ"], drop=drop, rng=rng_)


def first_active(F):
    nz = np.where((F != 0).any(1))[0]
    return int(nz[0]) if len(nz) else 10 ** 9


def weight_path_avail(Rm, firsts, start_i, scheme="IV", lookback=60, every=7, min_obs=20):
    """worker1_backfill.report.weight_path_avail (IV) generalised to EW / ERC; legs without a signal yet get 0.
    Identical to ens.weight_path when every leg is available."""
    T, n = Rm.shape
    W = np.full((T, n), 1 / n); cur = np.full(n, 1 / n)
    for d in range(T):
        if (d - start_i) % every == 0:
            av = np.array([f <= d for f in firsts])
            cur = np.zeros(n)
            if not av.any():
                cur = np.full(n, 1 / n)
            else:
                X = Rm[max(0, d - lookback):d][:, av]
                X = X[~np.isnan(X).any(1)]
                if scheme == "EW" or len(X) < min_obs:
                    cur[av] = 1 / av.sum()
                elif scheme == "IV":
                    sd = X.std(0, ddof=1) + 1e-9
                    cur[av] = (1 / sd) / (1 / sd).sum()
                else:  # ERC
                    S = np.cov(X.T) + np.eye(av.sum()) * 1e-10
                    w = np.full(av.sum(), 1 / av.sum())
                    for _ in range(200):
                        rc = w * (S @ w)
                        w = w * np.sqrt(rc.mean() / np.maximum(rc, 1e-18)); w = w / w.sum()
                    cur[av] = w
        W[d] = cur
    return W


def standalone(D, leg, start, end):
    r = ens.simulate(D, [leg], start, end)
    out = np.full(D.T, np.nan)
    out[r["i0"]:r["i0"] + len(r["ret"])] = r["ret"]
    return out


def weights_for(D, legs, scheme="IV", wlook=60, wevery=7):
    """Weight path exactly as each window's W1 run would build it:
    perp: standalone legs over 2021-01..2026-10 (r4_07 run_test); spot: standalone over 2018-01..2020-12 (backfill)."""
    if len(legs) == 1:
        return None
    S, E = (FULL_START, TEST_END) if D.kind == "perp" else (HO_START, HO_END)
    i0 = D.idx(S)
    cols, firsts = [], []
    for l in legs:
        r = standalone(D, l, S, E)
        f = max(first_active(l[1]), i0) if D.kind == "spot" else i0
        r[:f] = np.nan
        cols.append(r); firsts.append(f)
    return weight_path_avail(np.column_stack(cols), firsts, i0, scheme, wlook, wevery)


def mk(kind_engine, F):
    return (kind_engine, F, ens.trade_mask(ens_dummy(F), F, kind_engine) if kind_engine == "T" else
            (None if kind_engine == "R" else np.ones_like(F, bool)))


class _Dm:
    pass


def ens_dummy(F):
    """trade_mask('T') only needs D.N and D.dom; set by build() before use."""
    return _DOM


_DOM = _Dm()


def set_dom(D):
    _DOM.N = D.N; _DOM.dom = D.dom


def run(D, legs, start, end, wpath=None, cost_mult=1.0, record=False):
    r = ens.simulate(D, legs, start, end, wpath=wpath, cost_mult=cost_mult, record=record)
    r["stats"] = ens.stats_of(r["curve"])
    return r


def brief(r):
    s = r["stats"]
    v = np.array(list(s["months"].values()))
    return dict(median=s["median_month"], geo=s["geo_month"], mean=s["mean_month"], up=f'{s["months_up"]}/{s["n_months"]}',
                max_dd=s["max_dd_pct"], t=s["t_stat"], net=s["net_pct"], worst=s["worst_month"], liq=r["liquidations"],
                turnover_per_year=round(r["turnover_per_year"], 1))
