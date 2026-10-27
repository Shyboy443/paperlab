"""Speculation intensity = Binance USD-M perp quote volume / Binance SPOT quote volume (USD-stable quotes), per coin.
Perp volume: survivorship-free daily panel (research/monthly20/r2_rotation, sum of 1h quote volume, incl. delisted).
Spot volume: data/w3r3_spec_ratio/spot/{PAIR}.json from data.binance.vision (fetch_spot.py), summed over USDT/BUSD/FDUSD/USDC/TUSD.
Engine: agent-02 engine_r2.run unchanged (score known at day d open from bars <= d-1, fill at day d open, fee 0.05% +
slip 0.02% per side, funding, 1% volume cap, 1x). Coins with no spot market (or < 80% spot days in the window) get NaN
score and are not traded, so the tradable universe = point-in-time top-N perps that also have a spot market.

Scores (value at row i uses days <= i-1):
  lvl : log( sum_L perpQV / sum_L spotQV )
  chg : lvl(L) - lvl over the preceding 4L days (days i-5L..i-L-1): change in speculation intensity
sign +1 = long high ratio / short low ratio ("speculation momentum"); -1 = long low / short high ("overpricing").
"""
from __future__ import annotations
import json, sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r2 as E  # noqa
from data import build  # noqa
from common import DEV_START, DEV_END, TEST_START, TEST_END, DAY  # noqa
from pathlib import Path

SD = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r3_spec_ratio")
D = build()
P = E.Panel(D)
W1D = np.load(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/_rbook_full_1x.npy")


def load_spot():
    cache = SD / "spot_panel.npz"
    if cache.exists():
        z = np.load(cache); return z["sqv"], z["spx"]
    mp = json.loads((SD / "mapping.json").read_text())
    T, N = P.c.shape
    sqv = np.full((T, N), np.nan); spx = np.full((T, N), np.nan)
    for j, s in enumerate(P.syms):
        if s not in mp:
            continue
        mult = mp[s]["mult"]
        for pr in mp[s]["pairs"]:
            fp = SD / "spot" / f"{pr}.json"
            if not fp.exists():
                continue
            rows = json.loads(fp.read_text())
            if not rows:
                continue
            a = np.array(rows, float)
            d = ((a[:, 0].astype(np.int64) - int(P.t[0])) // DAY).astype(int)
            ok = (d >= 0) & (d < T)
            d, a = d[ok], a[ok]
            sqv[d, j] = np.nan_to_num(sqv[d, j]) + a[:, 5]
            if pr.endswith("USDT"):
                spx[d, j] = a[:, 4] * mult
    np.savez_compressed(cache, sqv=sqv, spx=spx)
    return sqv, spx


SQV, SPX = load_spot()


def sanity():
    """Reject coins whose spot close is not the same asset as the perp (median |log ratio| > 0.1 on common days)."""
    bad = []
    for j in range(len(P.syms)):
        m = ~np.isnan(SPX[:, j]) & ~np.isnan(P.c[:, j])
        if m.sum() < 10:
            continue
        r = np.nanmedian(np.abs(np.log(SPX[m, j] / P.c[m, j])))
        if r > 0.1:
            bad.append((P.syms[j], round(float(r), 3)))
    return bad


BAD = {s for s, _ in sanity()}
for s in BAD:
    SQV[:, P.syms.index(s)] = np.nan
_UM, _SC = {}, {}


def um(n, Pn=None):
    Pn = Pn or P
    key = (id(Pn), n)
    if key not in _UM:
        _UM[key] = E.universe_mask(Pn, n)
    return _UM[key]


def _shift(a):
    out = np.full(a.shape, np.nan); out[1:] = a[:-1]; return out


def _lvl(pq, sq, sn, L, lag=0):
    """log ratio over days [t-lag-L+1 .. t-lag] (at row t, before the 1-day shift)."""
    ps = pd.DataFrame(pq).rolling(L, min_periods=int(0.8 * L)).sum()
    ss = pd.DataFrame(sq).rolling(L, min_periods=int(0.8 * L)).sum()
    cnt = pd.DataFrame(sn).rolling(L, min_periods=1).sum()
    with np.errstate(invalid="ignore", divide="ignore"):
        v = np.log(ps.values / ss.values)
    v[(cnt.values < 0.8 * L) | ~np.isfinite(v)] = np.nan
    if lag:
        v = np.vstack([np.full((lag, v.shape[1]), np.nan), v[:-lag]])
    return v


def score_raw(kind, L, cols=None):
    cols = np.arange(len(P.syms)) if cols is None else cols
    pq = np.where(np.isnan(P.qv[:, cols]), 0, P.qv[:, cols])
    sq = np.nan_to_num(SQV[:, cols]); sn = (~np.isnan(SQV[:, cols])).astype(float)
    if kind == "lvl":
        v = _lvl(pq, sq, sn, L)
    elif kind == "chg":
        v = _lvl(pq, sq, sn, L) - _lvl(pq, sq, sn, 4 * L, lag=L)
    else:
        raise ValueError(kind)
    v[np.isnan(P.c[:, cols])] = np.nan
    return _shift(v)


def get_score(kind, L, sign, Pn=None, cols=None):
    Pn = Pn or P
    key = (id(Pn), kind, L, sign)
    if key not in _SC:
        _SC[key] = sign * score_raw(kind, L, cols)
    return _SC[key]


def run_cfg(kind, L, sign, k, rebal, univ=40, start=DEV_START, end=TEST_END, cost_mult=1.0, record=False, Pn=None, cols=None, legs="LS"):
    Pn = Pn or P
    sc = get_score(kind, L, sign, Pn, cols)
    prm = dict(k_long=k, k_short=k if legs == "LS" else 0, rebal=rebal, lev=1.0, mode="mom")
    return E.run(Pn, um(univ, Pn), sc, prm, start, end, cost_mult=cost_mult, record=record)


def daily_returns(curve):
    eq = np.array([e for _, e in curve]); ts = np.array([t for t, _ in curve])
    return ts[:-1], eq[1:] / eq[:-1] - 1


def corr_w1(curve):
    ts, dr = daily_returns(curve)
    i = ((ts - P.t[0]) // 86400000).astype(int)
    w = W1D[i]; m = ~np.isnan(w) & ~np.isnan(dr)
    return round(float(np.corrcoef(dr[m], w[m])[0, 1]), 3)


def tstat(months):
    v = np.array(list(months.values()))
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 2 else float("nan")
