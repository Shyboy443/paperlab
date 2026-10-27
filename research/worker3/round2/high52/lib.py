"""Nearness-to-high (anchoring) scores on the survivorship-free daily panel (research/monthly20/r2_rotation), run
through the unchanged agent-02 rotation engine (engine_r2.run: signal known at day d open, fill at day d open, fee+slip
per side, funding, 1% volume cap, 1x).

Scores at bar i use bars up to i-1 only:
  nh  : George & Hwang (2004) nearness to the L-day high = c[i-1] / max(h[i-L..i-1])          (in (0, 1])
  rng : Donchian range position = (c[i-1] - min l) / (max h - min l) over the same L days       (in [0, 1])
  mom : plain L-day close-to-close return (reference only)
Gate 'g' (long-only only): hold cash on days where BTC c[i-1] <= SMA_G of BTC closes c[i-G..i-1].
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r2 as E  # noqa
from data import build  # noqa
from common import DEV_START, DEV_END, TEST_START, TEST_END  # noqa

D = build()
P = E.Panel(D)
W1D = np.load(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/_rbook_full_1x.npy")
_UM = {}
_SC = {}


def um(n, Pn=None):
    Pn = Pn or P
    key = (id(Pn), n)
    if key not in _UM:
        _UM[key] = E.universe_mask(Pn, n)
    return _UM[key]


def _shift(a):
    out = np.full(a.shape, np.nan); out[1:] = a[:-1]; return out


def score(Pn, kind, L):
    c = pd.DataFrame(Pn.c); h = pd.DataFrame(Pn.h); l = pd.DataFrame(Pn.l)
    mp = int(round(L * 0.9))
    hx = h.rolling(L, min_periods=mp).max().values
    if kind == "nh":
        s = Pn.c / hx
    elif kind == "rng":
        lx = l.rolling(L, min_periods=mp).min().values
        with np.errstate(invalid="ignore", divide="ignore"):
            s = (Pn.c - lx) / (hx - lx)
    elif kind == "mom":
        s = Pn.c / c.shift(L).values - 1
    else:
        raise ValueError(kind)
    return _shift(s)  # value at row i is known at the open of day i


def btc_gate(Pn, G):
    b = Pn.syms.index("BTCUSDT")
    cb = pd.Series(Pn.c[:, b])
    ok = (cb > cb.rolling(G, min_periods=G).mean()).values
    return _shift(ok.astype(float)) > 0.5


def get_score(Pn, kind, L, gate):
    key = (id(Pn), kind, L, gate)
    if key not in _SC:
        s = score(Pn, kind, L)
        if gate:
            s = np.where(btc_gate(Pn, gate)[:, None], s, np.nan)
        _SC[key] = s
    return _SC[key]


def run_cfg(kind, L, k, legs, rebal, univ=40, gate=0, start=DEV_START, end=TEST_END, cost_mult=1.0, record=False, Pn=None):
    """legs: 'LS' (k long / k short) or 'LO' (k long). gate>0 only meaningful for LO (cash when BTC below SMA)."""
    Pn = Pn or P
    kS = k if legs == "LS" else 0
    sc = get_score(Pn, kind, L, gate)
    prm = dict(k_long=k, k_short=kS, rebal=rebal, lev=1.0, mode="mom")
    return E.run(Pn, um(univ, Pn), sc, prm, start, end, cost_mult=cost_mult, record=record)


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
