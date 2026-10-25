"""OI-flow vs price (Worker 2 round 2, oi_flow).

Daily panel (survivorship-free top-40 USDT perps, r2_rotation) + Binance UM daily `metrics` archive (cached by r4_03 in
data/monthly20_r4_positioning, read-only). oi = sum_open_interest (contracts, last 5m snapshot of the UTC day).
Contracts are used for the OI leg so the OI change is not mechanically the price change (oiv = oi x price).

Regimes (all known at the close of day i-1, traded at the open of day i):
  x = cross-sectional rank of k-day log price return, y = cross-sectional rank of k-day log OI change (universe only).
  UU = price top-q, OI top-q   (new longs piling in)
  DU = price bottom-q, OI top-q (new shorts piling in)
  UD = price top-q, OI bottom-q (short covering)
  DD = price bottom-q, OI bottom-q (long liquidation)
Book: each regime is assigned +1 / -1 / 0; long coins in +1 regimes, short coins in -1 regimes, equal risk (1/vol20),
long book = short book = 0.5 of equity (market neutral, gross 1x). Hold h days via h overlapping daily tranches
(each tranche 1/h of equity, weights frozen at entry), so W[i] = mean of the last h tranche targets.
"""
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_03_positioning")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import signals as S  # noqa: E402  (read-only reuse: panel + metrics loader)
import engine as G   # noqa: E402  (r4_03 target-weight daily engine: fees, slippage, funding, delistings)
from common import DEV_START, DEV_END, TEST_START, TEST_END  # noqa: E402

P, UM, M = S.load()
T, N = P.c.shape
LC = np.log(P.c)
_r1 = np.full((T, N), np.nan); _r1[1:] = LC[1:] - LC[:-1]
# 20-day vol of daily close-to-close log returns, known at close of day i
VOL = np.full((T, N), np.nan)
for i in range(20, T):
    w = _r1[i - 19:i + 1]
    n = (~np.isnan(w)).sum(0)
    VOL[i] = np.where(n >= 15, np.nanstd(w, 0), np.nan)
REGIMES = ("UU", "DU", "UD", "DD")


def regime_masks(k, q):
    """dict regime -> bool (T,N), known at close of day i (row i)."""
    ret = S.logchg(P.c, k)
    doi = S.logchg(M["oi"], k)
    ok = UM & ~np.isnan(ret) & ~np.isnan(doi) & ~np.isnan(VOL)
    ret = np.where(ok, ret, np.nan); doi = np.where(ok, doi, np.nan)
    xr = S.xrank(ret); yr = S.xrank(doi)
    hi_p, lo_p = xr >= 1 - q, xr <= q
    hi_o, lo_o = yr >= 1 - q, yr <= q
    return {"UU": hi_p & hi_o, "DU": lo_p & hi_o, "UD": hi_p & lo_o, "DD": lo_p & lo_o}, ok


def fwd_xs(h):
    """forward h-day open-to-open SIMPLE return from open i+1 (entry after the close-i signal), minus the universe
    equal-weight mean, divided by vol20*sqrt(h) (equal-risk units, as the book is sized)."""
    f = np.full((T, N), np.nan)
    f[:T - 1 - h] = P.o[1 + h:T] / P.o[1:T - h] - 1
    f = np.where(UM & ~np.isnan(VOL), f, np.nan)
    f = f - np.nanmean(f, axis=1, keepdims=True)
    return f / (VOL * np.sqrt(h))


def weights(k, q, h, signs, start=DEV_START, rand=None):
    """signs: dict regime -> +1/-1/0. rand: Generator -> same count of longs/shorts per day, random eligible coins."""
    R, ok = regime_masks(k, q)
    W = np.zeros((T, N)); tr = []
    i0 = int(np.searchsorted(P.t, start))
    for i in range(i0, T):
        s = i - 1
        elig = ok[s] & ~np.isnan(P.o[i])
        L = np.zeros(N, bool); Sh = np.zeros(N, bool)
        for r in REGIMES:
            if signs.get(r, 0) > 0:
                L |= R[r][s]
            elif signs.get(r, 0) < 0:
                Sh |= R[r][s]
        L &= elig; Sh &= elig
        both = L & Sh; L &= ~both; Sh &= ~both
        if rand is not None:
            nl, ns = L.sum(), Sh.sum()
            pool = rand.permutation(np.where(elig)[0])
            L = np.zeros(N, bool); Sh = np.zeros(N, bool)
            L[pool[:nl]] = True; Sh[pool[nl:nl + ns]] = True
        w = np.zeros(N)
        if L.sum() >= 1 and Sh.sum() >= 1:
            iv = 1.0 / VOL[s]
            w[L] = 0.5 * iv[L] / iv[L].sum()
            w[Sh] = -0.5 * iv[Sh] / iv[Sh].sum()
        tr.append(w)
        a = max(0, len(tr) - h)
        W[i] = np.sum(tr[a:], axis=0) / h
    return W


def run(W, start, end, **kw):
    return G.sim(P, W, start, end, **kw)
