"""Premium signals and target-weight builders. Every score row i uses bars <= i-1 only (trade at bar i's open)."""
import numpy as np


def roll_mean(x, w):
    """Trailing mean over w bars ending at the current bar, NaN-aware (needs >= w/2 finite values)."""
    v = np.nan_to_num(x); n = (~np.isnan(x)).astype(float)
    cs = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(v, 0)])
    cn = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(n, 0)])
    s = cs[w:] - cs[:-w]; k = cn[w:] - cn[:-w]
    out = np.full_like(x, np.nan)
    out[w - 1:] = np.where(k >= max(1, w / 2), s / np.maximum(k, 1), np.nan)
    return out


def roll_std(x, w):
    m = roll_mean(x, w); m2 = roll_mean(x * x, w)
    return np.sqrt(np.maximum(m2 - m * m, 0))


def shift1(x):
    out = np.full_like(x, np.nan); out[1:] = x[:-1]; return out


def score(P, fam, w, L=None):
    """Signal value known at the close of bar i-1 (row i). Higher = more 'overheated' (rich premium)."""
    pm = P.pm
    S = roll_mean(pm, w)
    if fam == "LEVEL":
        s = S
    elif fam == "Z":
        mu = roll_mean(pm, L); sd = roll_std(pm, L)
        s = (S - mu) / np.where(sd > 1e-6, sd, np.nan)
    elif fam == "MOM":
        prev = np.full_like(S, np.nan); prev[w:] = S[:-w]
        s = S - prev
    elif fam == "BASISRET":  # premium relative to own trailing level, minus price momentum sign-free proxy
        mu = roll_mean(pm, L); s = S - mu
    else:
        raise ValueError(fam)
    return shift1(s)


def xs_weights(sc, umask, k, mode, longs=True, shorts=True):
    """Cross-sectional: fade = long the k lowest-premium, short the k highest; follow = the opposite."""
    T, N = sc.shape
    W = np.zeros((T, N))
    sgn = -1.0 if mode == "fade" else 1.0
    nside = int(longs) + int(shorts)
    for i in range(T):
        idx = np.where(umask[i] & ~np.isnan(sc[i]))[0]
        if len(idx) < 2 * k:
            continue
        order = idx[np.argsort(sgn * sc[i, idx], kind="stable")]  # ascending in sgn*score
        lo, hi = order[:k], order[-k:]
        # fade: sgn=-1 -> ascending of -score -> lo = highest score (rich) -> short; hi = cheapest -> long
        if longs:
            W[i, hi] = 1.0 / nside / k
        if shorts:
            W[i, lo] = -1.0 / nside / k
    return W


def ts_weights(sc, umask, thr, mode, kmax=10):
    """Time-series: z > thr -> short (fade), z < -thr -> long; each active coin gets 1/max(n_active, kmax)."""
    sgn = -1.0 if mode == "fade" else 1.0
    raw = np.where(sc > thr, sgn, np.where(sc < -thr, -sgn, 0.0)) * umask
    raw = np.nan_to_num(raw)
    n = np.abs(raw).sum(1, keepdims=True)
    return raw / np.maximum(n, kmax)


def random_xs_weights(umask, valid, k, longs, shorts, rng):
    """Same structure as xs_weights (k long / k short among eligible coins) with random picks."""
    T, N = umask.shape
    W = np.zeros((T, N)); nside = int(longs) + int(shorts)
    for i in range(T):
        idx = np.where(umask[i] & valid[i])[0]
        if len(idx) < 2 * k:
            continue
        pick = rng.permutation(idx)[:2 * k]
        if longs:
            W[i, pick[:k]] = 1.0 / nside / k
        if shorts:
            W[i, pick[k:]] = -1.0 / nside / k
    return W
