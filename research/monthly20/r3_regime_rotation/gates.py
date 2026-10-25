"""The 6 pre-registered regime gates (PREREG.md). Each returns s[i] in {0,1} for every daily bar i, using only closes up to i-1.
Defaults (ON) apply until a gate has enough history."""
import json, sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r3_regime_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r3 as E
from common import PROJECT, DEV_START, TEST_END

BTC = np.array(json.loads((PROJECT / "data" / "monthly20_cache" / "perp" / "1d" / "BTCUSDT.json").read_text()), float)


def btc_close(P):
    assert len(BTC) == len(P.t) and (BTC[:, 0] == P.t).all()
    return BTC[:, 4]


def elig(P, U, score):
    return U & ~np.isnan(score) & ~np.isnan(P.o)


def trailing_median_gate(x, win, mult, min_n=60):
    T = len(x); s = np.ones(T)
    for i in range(T):
        if np.isnan(x[i]):
            continue
        h = x[max(0, i - win + 1):i + 1]; h = h[~np.isnan(h)]
        if len(h) >= min_n:
            s[i] = 1.0 if x[i] >= mult * np.median(h) else 0.0
    return s


def g_disp(P, U, base, med_win=365, mult=1.0):
    sc = E.ret_lb(P, base.get("lb", 14)); el = elig(P, U, sc)
    x = np.full(len(P.t), np.nan)
    for i in range(len(P.t)):
        v = sc[i][el[i]]
        if len(v) >= 10:
            q75, q25 = np.percentile(v, [75, 25]); x[i] = q75 - q25
    return trailing_median_gate(x, med_win, mult)


def always_on_open_equity(P, U, base):
    """Equity of the always-on base book at the open of each bar (= after bar i-1), run continuously from DEV_START."""
    r = E.run(P, U, E.ret_lb(P, base.get("lb", 14)), dict(k_long=base.get("k", 10), k_short=base.get("k", 10),
              rebal=base.get("rebal", 7), lev=1.0, mode="mom"), DEV_START, TEST_END, record=True)
    A = np.full(len(P.t), np.nan)
    for t, e in r["_curve"]:
        k = int(np.searchsorted(P.t, t))
        if k < len(P.t) and P.t[k] == t:
            A[k] = e
    return A


def g_mom(P, U, base, lb=56, thr=0.0, A=None):
    A = always_on_open_equity(P, U, base) if A is None else A
    s = np.ones(len(P.t))
    for i in range(lb, len(P.t)):
        if not np.isnan(A[i]) and not np.isnan(A[i - lb]):
            s[i] = 1.0 if A[i] / A[i - lb] - 1 > thr else 0.0
    return s


def g_btc_trend(P, U, base, sma=100, band=0.0):
    c = btc_close(P); s = np.ones(len(P.t))
    for i in range(sma, len(P.t)):
        s[i] = 1.0 if c[i - 1] / c[i - sma:i].mean() - 1 > band else 0.0
    return s


def g_btc_vol(P, U, base, win=30, med_win=365, mult=1.0):
    c = btc_close(P); lr = np.r_[np.nan, np.diff(np.log(c))]
    v = np.full(len(P.t), np.nan)
    for i in range(win + 1, len(P.t)):
        v[i] = np.std(lr[i - win:i], ddof=1)  # returns of days i-win..i-1, known at the open of i
    return trailing_median_gate(v, med_win, mult)


def g_breadth(P, U, base, sma=50, hw=0.10):
    sc = E.ret_lb(P, base.get("lb", 14)); el = elig(P, U, sc)
    c = P.c; T = len(P.t); s = np.ones(T)
    cs = np.vstack([np.zeros((1, c.shape[1])), np.nancumsum(c, 0)])
    cn = np.vstack([np.zeros((1, c.shape[1])), np.cumsum(~np.isnan(c), 0)])
    for i in range(sma + 1, T):
        m = (cs[i] - cs[i - sma]) / np.maximum(cn[i] - cn[i - sma], 1)  # mean close over days i-sma..i-1
        ok = el[i] & (cn[i] - cn[i - sma] == sma) & ~np.isnan(c[i - 1])
        if ok.sum() >= 10:
            b = float((c[i - 1][ok] > m[ok]).mean())
            s[i] = 1.0 if (b >= 0.5 + hw or b <= 0.5 - hw) else 0.0
    return s


def _rank(a):
    r = np.empty(len(a)); r[np.argsort(a, kind="stable")] = np.arange(len(a)); return r


def g_rankcorr(P, U, base, nobs=4, thr=0.0):
    L = base.get("lb", 14)
    sc = E.ret_lb(P, L); el = elig(P, U, sc); T = len(P.t)
    rho = np.full(T, np.nan)
    for i in range(L, T):
        a, b = sc[i], sc[i - L]
        ok = el[i] & ~np.isnan(b)
        if ok.sum() >= 10:
            rho[i] = np.corrcoef(_rank(a[ok]), _rank(b[ok]))[0, 1]
    s = np.ones(T)
    for i in range(T):
        idx = [i - 7 * k for k in range(nobs)]
        if min(idx) >= 0:
            v = rho[idx]
            if not np.isnan(v).any():
                s[i] = 1.0 if v.mean() > thr else 0.0
    return s


GATES = {"DISP": g_disp, "MOM8": g_mom, "BTC100": g_btc_trend, "BTCVOL": g_btc_vol, "BREADTH": g_breadth, "RANKCORR": g_rankcorr}

# check-5 neighbours, exactly as PREREG.md
NEIGHBOURS = {
    "DISP": [("mult", v) for v in (0.5, 0.75, 1.25, 1.5)] + [("med_win", v) for v in (183, 274, 456, 548)],
    "MOM8": [("thr", v) for v in (-0.05, -0.025, 0.025, 0.05)] + [("lb", v) for v in (28, 42, 70, 84)],
    "BTC100": [("band", v) for v in (-0.05, -0.025, 0.025, 0.05)] + [("sma", v) for v in (50, 75, 125, 150)],
    "BTCVOL": [("mult", v) for v in (0.5, 0.75, 1.25, 1.5)] + [("win", v) for v in (15, 22, 38, 45)],
    "BREADTH": [("hw", v) for v in (0.05, 0.075, 0.125, 0.15)] + [("sma", v) for v in (25, 38, 63, 75)],
    "RANKCORR": [("thr", v) for v in (-0.10, -0.05, 0.05, 0.10)] + [("nobs", v) for v in (2, 3, 5, 6)],
}
