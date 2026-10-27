"""Candidate 2: turnover-volatility factor (Mercik, Zaremba & Demir 2026, "Crypto Factor Zoo").

Binance has no market cap, so turnover volatility is proxied scale-free by the coefficient of variation of daily quote
volume over the last L days (days d-L..d-1, known at day d's open). Sign fixed a priori from the literature
(Chordia et al. 2001 in equities; liquidity-risk interpretation): LONG the k lowest-CV coins, SHORT the k highest-CV coins
of the point-in-time top-40 universe, dollar neutral, 1x gross, rebalanced every `rb` days.
"""
import numpy as np
from lib import *  # noqa


def cv_score(L):
    qv = P.qv
    out = np.full((T, N), np.nan)
    for i in range(L + 1, T):
        win = qv[i - L:i]
        ok = (~np.isnan(win)).sum(0) >= int(L * 0.8)
        m = np.nanmean(win, 0); s = np.nanstd(win, 0)
        out[i] = np.where(ok & (m > 0), s / np.where(m > 0, m, 1), np.nan)
    return out


_CV = {}


def make(L, k, rb=7, n_uni=40):
    def make_W(exclude=None):
        if L not in _CV:
            _CV[L] = cv_score(L)
        sc = _CV[L]
        um = umask(n_uni).copy()
        if exclude is not None:
            um &= ~exclude[None, :]
        W = np.full((T, N), np.nan)
        i0 = idx(utc(2020, 3, 1))
        for i in range(i0, T):
            if (i - i0) % rb:
                continue
            s = sc[i]
            el = np.where(um[i] & ~np.isnan(s) & ~np.isnan(P.o[i]))[0]
            w = np.zeros(N)
            if len(el) >= 2 * k:
                o = el[np.argsort(s[el], kind="stable")]
                w[o[:k]] = 0.5 / k
                w[o[-k:]] = -0.5 / k
            W[i] = w
        return W
    return make_W


def random_Ws(L, k, rb, n=200, seed=11, n_uni=40):
    """Same structure (k longs, k shorts, same dates, same universe), random coin picks."""
    rng = np.random.default_rng(seed)
    um = umask(n_uni)
    i0 = idx(utc(2020, 3, 1))
    dates = [i for i in range(i0, T) if (i - i0) % rb == 0]
    out = []
    for _ in range(n):
        W = np.full((T, N), np.nan)
        for i in dates:
            el = np.where(um[i] & ~np.isnan(P.o[i]))[0]
            w = np.zeros(N)
            if len(el) >= 2 * k:
                p = rng.permutation(el)
                w[p[:k]] = 0.5 / k; w[p[k:2 * k]] = -0.5 / k
            W[i] = w
        out.append(W)
    return out
