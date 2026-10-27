"""Tug of war (Lou, Polk & Skouras 2019) in the crypto cross-section: split each coin's past return into trader-geography
sessions from the survivorship-free hourly panel and sort on one session's cumulative return (or a session difference).

Sessions (UTC, fixed a priori, cover the whole day):  A = Asia 00:00-08:00, E = Europe 08:00-14:00, U = US 14:00-24:00.
Session log return of day d = log(O[hour end] / O[hour start]) from hourly opens. Score known at the open of day i uses
days i-L .. i-1 only (the last US session ends at day i 00:00 = the fill time, so it is known).
Signals: 'A','E','U' = sum of that session's log returns over L days; 'X-Y' = z_X - z_Y (cross-sectional z over coins that
have a value, i.e. U40 members of the daily panel are the only coins traded anyway); 'T' = total (reference).
Run through the unchanged agent-02 rotation engine (engine_r2.run): fill at day open, fee+slip per side, funding, 1x.
"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_09_session")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r2 as E  # noqa
from data import build  # noqa
from common import DEV_START, DEV_END, TEST_START, TEST_END, DATA_FROM, HOUR  # noqa
from hpanel import make  # noqa

D = build()
P = E.Panel(D)
H = make()
HS = [str(s) for s in H["syms"]]
HO = H["o"].astype(np.float64)
assert int(H["th"][0]) == DATA_FROM and np.array_equal(H["td"], P.t)
W1D = np.load(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/_rbook_full_1x.npy")
SESS = {"A": (0, 8), "E": (8, 14), "U": (14, 24), "T": (0, 24)}
ND = len(P.t)

# daily session log returns, day x hourly-coin
def _sess_daily():
    out = {}
    nh = HO.shape[0]
    for k, (h0, h1) in SESS.items():
        i0 = np.arange(ND) * 24 + h0; i1 = np.arange(ND) * 24 + h1
        ok = i1 < nh
        r = np.full((ND, HO.shape[1]), np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            r[ok] = np.log(HO[i1[ok]] / HO[i0[ok]])
        out[k] = r
    return out


SD = _sess_daily()
_CS = {}


def cum(k, L):
    """sum of session-k log returns over days i-L..i-1, at row i; NaN if < 80% present."""
    key = (k, L)
    if key not in _CS:
        r = SD[k]; x = np.nan_to_num(r); n = (~np.isnan(r)).astype(float)
        cx = np.vstack([np.zeros((1, r.shape[1])), np.cumsum(x, 0)]); cn = np.vstack([np.zeros((1, r.shape[1])), np.cumsum(n, 0)])
        out = np.full(r.shape, np.nan)
        i = np.arange(L, ND)
        s = cx[i] - cx[i - L]; c = cn[i] - cn[i - L]
        out[i] = np.where(c >= 0.8 * L, s, np.nan)
        _CS[key] = out
    return _CS[key]


def _z(a, mask):
    a = np.where(mask, a, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = np.nanmean(a, 1, keepdims=True); s = np.nanstd(a, 1, keepdims=True)
        return (a - m) / s


_U40H = H["U40"]


def hscore(sig, L):
    if "-" in sig:
        x, y = sig.split("-")
        cx, cy = cum(x, L), cum(y, L)
        m = _U40H & ~np.isnan(cx) & ~np.isnan(cy)
        return _z(cx, m) - _z(cy, m)
    return cum(sig, L)


def score(Pn, sig, L):
    """map the hourly-coin score onto panel Pn's columns (coins never in the top-40 stay NaN)."""
    hs = hscore(sig, L)
    out = np.full((ND, len(Pn.syms)), np.nan)
    idx = {s: j for j, s in enumerate(HS)}
    for j, s in enumerate(Pn.syms):
        if s in idx:
            out[:, j] = hs[:, idx[s]]
    return out


_UM = {}


def um(n, Pn=None):
    Pn = Pn or P
    key = (id(Pn), n)
    if key not in _UM:
        _UM[key] = E.universe_mask(Pn, n)
    return _UM[key]


_SC = {}


def run_cfg(sig, L, k, sign, rebal, univ=40, start=DEV_START, end=TEST_END, cost_mult=1.0, record=False, Pn=None):
    """sign +1 = long the highest score (momentum), -1 = long the lowest (reversal). k long / k short, 1x."""
    Pn = Pn or P
    key = (id(Pn), sig, L)
    if key not in _SC:
        _SC[key] = score(Pn, sig, L)
    prm = dict(k_long=k, k_short=k, rebal=rebal, lev=1.0, mode="mom" if sign > 0 else "rev")
    return E.run(Pn, um(univ, Pn), _SC[key], prm, start, end, cost_mult=cost_mult, record=record)


def daily_returns(curve):
    eq = np.array([e for _, e in curve]); ts = np.array([t for t, _ in curve])
    return ts[:-1], eq[1:] / eq[:-1] - 1


def corr_w1(curve, Pn=None):
    Pn = Pn or P
    ts, dr = daily_returns(curve)
    i = ((ts - Pn.t[0]) // 86400000).astype(int)
    w = W1D[i]; m = ~np.isnan(w) & ~np.isnan(dr)
    return round(float(np.corrcoef(dr[m], w[m])[0, 1]), 3)


def tstat(months):
    v = np.array(list(months.values()))
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 2 else float("nan")
