"""Panel + positioning metrics aligned day x coin, and the weight builders for every family (r4_03)."""
import json, sys
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r2 as E
from data import build
from common import PROJECT, DEV_START

MET = PROJECT / "data" / "monthly20_r4_positioning"
FIELDS = ["oi", "oiv", "top_acc", "top_pos", "glob_acc", "taker"]


def load():
    P = E.Panel(build())
    UM = E.universe_mask(P, 40)
    T, N = P.c.shape
    M = {f: np.full((T, N), np.nan) for f in FIELDS}
    day_idx = {datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d"): i for i, t in enumerate(P.t)}
    for j, s in enumerate(P.syms):
        p = MET / "daily" / f"{s}.json"
        if not p.exists():
            continue
        for d, rec in json.loads(p.read_text()).items():
            if rec is None or d not in day_idx:
                continue
            i = day_idx[d]
            for k, f in enumerate(FIELDS):
                v = rec[k]
                if v is not None and v > 0:
                    M[f][i, j] = v
    return P, UM, M


def lag(x, k):
    out = np.full_like(x, np.nan); out[k:] = x[:-k]; return out


def logchg(x, k):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(x / lag(x, k))


def rollmean(x, k):
    """mean over the last k days, NaN if fewer than k//2+1 values."""
    v = np.nan_to_num(x); n = (~np.isnan(x)).astype(float)
    cs = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(v, 0)]); cn = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(n, 0)])
    s = cs[k:] - cs[:-k]; c = cn[k:] - cn[:-k]
    out = np.full_like(x, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[k - 1:] = np.where(c >= k // 2 + 1, s / np.maximum(c, 1), np.nan)
    return out


def xrank(x):
    """cross-sectional percentile rank per row (NaN kept)."""
    out = np.full_like(x, np.nan)
    for i in range(x.shape[0]):
        m = ~np.isnan(x[i])
        if m.sum() > 1:
            r = np.argsort(np.argsort(x[i, m]))
            out[i, m] = r / (m.sum() - 1)
    return out


def scores(P, M, fam, k):
    """score[i] known at close of day i. Higher = long."""
    c = P.c
    ret = np.log(c / lag(c, k))
    if fam == "squeeze":          # OI up while price down -> long; OI up while price up -> short
        return xrank(logchg(M["oi"], k)) - xrank(ret)
    if fam == "retail_contra":    # retail account long/short ratio low (retail short) -> long
        return -np.log(rollmean(M["glob_acc"], k))
    if fam == "retail_chg":       # retail turning more short over k days -> long
        return -logchg(rollmean(M["glob_acc"], 3), k)
    if fam == "smart_vs_retail":  # top traders (position ratio) more long than retail accounts -> long
        return rollmean(np.log(M["top_pos"]) - np.log(M["glob_acc"]), k)
    if fam == "taker_contra":     # aggressive selling dominant (taker buy/sell low) -> long
        return -rollmean(np.log(M["taker"]), k)
    raise ValueError(fam)


def xs_weights(P, UM, score, kL, kS, rb, start, rand=None):
    """Long top-kL / short bottom-kS of score[i-1] among universe coins, rebalanced every rb days from start.
    rand: np.random.Generator -> random picks among the same eligible coins (check 6)."""
    T, N = P.c.shape
    W = np.zeros((T, N)); reb = np.zeros(T, bool)
    i0 = int(np.searchsorted(P.t, start))
    nside = (kL > 0) + (kS > 0)
    cur = np.zeros(N)
    for i in range(i0, T):
        if (i - i0) % rb == 0:
            reb[i] = True
            s = score[i - 1]
            idx = np.where(UM[i] & ~np.isnan(s) & ~np.isnan(P.o[i]))[0]
            cur = np.zeros(N)
            if len(idx) >= max(2 * (kL + kS), 10):
                if rand is not None:
                    order = rand.permutation(idx)
                else:
                    order = idx[np.argsort(-s[idx], kind="stable")]
                if kL:
                    cur[order[:kL]] = 1.0 / nside / kL
                if kS:
                    cur[order[::-1][:kS]] = -1.0 / nside / kS
        W[i] = cur
    return W, reb


def flush_weights(P, UM, M, k, drop, hold, slots, start, rand=None):
    """OI flush: OI (contracts) fell >= drop over k days AND price fell over k days, known at close i-1 ->
    long from open i for `hold` days, weight 1/slots each, at most `slots` at once (largest OI drop first).
    rand -> the same number of entries each day go to random eligible coins instead."""
    T, N = P.c.shape
    doi = logchg(M["oi"], k); ret = np.log(P.c / lag(P.c, k))
    W = np.zeros((T, N)); until = np.full(N, -1)
    i0 = int(np.searchsorted(P.t, start))
    for i in range(i0, T):
        held = until >= i
        free = slots - held.sum()
        elig = UM[i] & ~np.isnan(P.o[i]) & ~held
        sig = elig & (doi[i - 1] <= np.log(1 - drop)) & (ret[i - 1] < 0)
        idx = np.where(sig)[0]
        if free > 0 and len(idx):
            idx = idx[np.argsort(doi[i - 1, idx])][:free]
            if rand is not None:
                pool = np.where(elig & ~np.isnan(doi[i - 1]))[0]
                idx = rand.choice(pool, size=min(len(idx), len(pool)), replace=False)
            until[idx] = i + hold - 1
        held = until >= i
        W[i, held] = 1.0 / slots
    return W, None
