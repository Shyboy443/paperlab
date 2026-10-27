"""Cross-sectional intraday periodicity (Heston, Korajczyk & Sadka 2010) on the survivorship-free hourly top-40 panel
(research/monthly20/r4_09_session/base.py). The UTC day is cut into NB = 24/B blocks of B hours (block 0 starts 00:00 UTC).

Signal for coin j at block b of day d: mean over the previous K days (d-K..d-1) of the coin's cross-sectionally
demeaned return in the SAME block b (open of first bar -> open of first bar of next block). Only finished blocks of
earlier days are used, so the signal is known at the block's open; fill at that open, exit at the next block's open.
Portfolio: long the top-k, short the bottom-k coins of the point-in-time top-40 (U40 of day d), weights +-0.5/k (1x gross).
Blocks not in the traded set are flat. Cost = |w_t - w_{t-1}| x (fee + slip) x cost_mult; funding stamps charged to
positions held across them (FCUM)."""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_09_session")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from base import O, C, U40, FCUM, td, th, syms, ND, dayidx, FEE, SLIP  # noqa
from common import summary, DEV_START, DEV_END, TEST_START, TEST_END  # noqa

N = O.shape[1]
# exit price: the bar's open, or the last known close if the coin stopped trading (delisting)
_cf = C.copy()
for t in range(1, len(_cf)):
    m = np.isnan(_cf[t]); _cf[t, m] = _cf[t - 1, m]
PX = np.where(np.isnan(O), np.vstack([np.full((1, N), np.nan), _cf[:-1]]), O)
_blk = {}


def blocks(B):
    """R[d,b,j] simple return of block (d,b), F funding, V valid & in U40, E demeaned return (nan outside U40)."""
    if B in _blk:
        return _blk[B]
    NB = 24 // B
    s = (np.arange(ND)[:, None] * 24 + np.arange(NB)[None, :] * B)  # start bar
    e = s + B
    ok = e < len(th)
    s2, e2 = np.where(ok, s, 0), np.where(ok, e, 0)
    o0 = O[s2]; p1 = PX[e2]
    R = p1 / o0 - 1
    R[~ok] = np.nan
    F = FCUM[e2] - FCUM[s2]
    V = np.isfinite(R) & U40[:, None, :]
    Rm = np.where(V, R, np.nan)
    E = Rm - np.nanmean(Rm, axis=2, keepdims=True)
    _blk[B] = (R, F, V, E, s, e)
    return _blk[B]


_sc = {}


def score(B, K, norm="raw"):
    key = (B, K, norm)
    if key in _sc:
        return _sc[key]
    R, F, V, E, s, e = blocks(B)
    x = np.nan_to_num(E); n = np.isfinite(E).astype(float)
    cx = np.concatenate([np.zeros((1,) + x.shape[1:]), np.cumsum(x, 0)]); cn = np.concatenate([np.zeros((1,) + x.shape[1:]), np.cumsum(n, 0)])
    d = np.arange(ND); a = np.maximum(d - K, 0)
    sx = cx[d] - cx[a]; sn = cn[d] - cn[a]
    with np.errstate(invalid="ignore", divide="ignore"):
        S = np.where(sn >= max(2, 0.8 * K), sx / sn, np.nan)
        if norm == "z":  # divide by the coin's dispersion of demeaned block returns over the same K days (all blocks)
            x2 = x * x
            c2 = np.concatenate([np.zeros((1,) + x.shape[1:]), np.cumsum(x2, 0)])
            s2 = (c2[d] - c2[a]).sum(1); s1 = sx.sum(1); nn = sn.sum(1)
            sd = np.sqrt(np.maximum(s2 / nn - (s1 / nn) ** 2, 0))
            S = S / sd[:, None, :]
    _sc[key] = S
    return S


def run(B=4, K=20, k=5, traded=None, norm="raw", start=DEV_START, end=TEST_END, cost_mult=1.0, record=False,
        exclude=(), rand_seed=None, mode="mom", buf=1.0):
    """traded: iterable of block indices traded each day (None = all). mode 'mom' (HKS continuation) or 'rev'."""
    R, F, V, E, s, e = blocks(B)
    NB = 24 // B
    S = score(B, K, norm).copy()
    if rand_seed is not None:
        rg = np.random.default_rng(rand_seed)
        S = np.where(np.isfinite(S), rg.random(S.shape), np.nan)
    elig = V & np.isfinite(S)
    if len(exclude):
        elig[:, :, list(exclude)] = False
    tr = np.zeros(NB, bool); tr[list(range(NB)) if traded is None else list(traded)] = True
    d0, d1 = dayidx(start), dayidx(end)
    cst = (FEE + SLIP) * cost_mult
    w_prev = np.zeros(N)
    eq = 1.0
    curve = [(int(th[s[d0, 0]]), 1.0)]
    pos = []  # (d, b, j, side, pnl in equity units)
    ntr = 0
    sgn = 1 if mode == "mom" else -1
    for d in range(d0, d1):
        for b in range(NB):
            if e[d, b] >= len(th):
                break
            w = np.zeros(N)
            if tr[b]:
                m = elig[d, b]
                idx = np.where(m)[0]
                if len(idx) >= 2 * k:
                    sc = S[d, b, idx] * sgn
                    o = np.argsort(sc)
                    if buf > 1.0:  # hysteresis: keep a held coin while it stays within the top/bottom round(buf*k)
                        kb = int(round(buf * k)); rk = np.empty(len(idx), int); rk[o] = np.arange(len(idx))
                        held_l = [i for i, j in enumerate(idx) if w_prev[j] > 0 and rk[i] >= len(idx) - kb]
                        held_s = [i for i, j in enumerate(idx) if w_prev[j] < 0 and rk[i] < kb]
                        held_l = sorted(held_l, key=lambda i: -rk[i])[:k]; held_s = sorted(held_s, key=lambda i: rk[i])[:k]
                        lg_i = held_l + [i for i in o[::-1] if i not in held_l and i not in held_s][:k - len(held_l)]
                        sh_i = held_s + [i for i in o if i not in held_s and i not in lg_i][:k - len(held_s)]
                        lg, sh = idx[lg_i], idx[sh_i]
                    else:
                        sh, lg = idx[o[:k]], idx[o[-k:]]
                    w[lg] = 0.5 / k; w[sh] = -0.5 / k
            # leaving a coin that became invalid: still costs (exit at PX)
            turn = np.abs(w - w_prev).sum()
            r = np.nan_to_num(R[d, b]); f = np.nan_to_num(F[d, b])
            contrib = w * (r - f)
            ret = contrib.sum() - turn * cst
            ntr += int((np.abs(w - w_prev) > 1e-12).sum())
            if record:
                for j in np.where(w != 0)[0]:
                    pos.append((d, b, int(j), int(np.sign(w[j])), float(contrib[j] * eq - abs(w[j] - w_prev[j]) * cst * eq)))
            eq *= (1 + ret)
            w_prev = w * (1 + np.nan_to_num(R[d, b])) / (1 + ret) if ret > -1 else w  # drift
            curve.append((int(th[e[d, b]]) if e[d, b] < len(th) else int(th[-1]), eq))
    res = summary(curve)
    res["trades"] = ntr
    if record:
        res["_curve"] = curve; res["_positions"] = pos
    return res


def tstat(months):
    v = np.array(list(months.values()))
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))
