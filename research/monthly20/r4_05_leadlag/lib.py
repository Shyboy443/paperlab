"""Shared lead-lag features (all causal: everything at row t uses bars that closed at or before t's close)."""
import numpy as np


def logret(c):
    r = np.full(c.shape, np.nan, np.float64)
    r[1:] = np.log(c[1:].astype(np.float64) / c[:-1].astype(np.float64))
    return r


def roll_beta(r, rb, win):
    """Rolling OLS beta of each column of r on rb over the last `win` rows ending at row t (inclusive). NaN-safe."""
    ok = ~np.isnan(r)
    x = np.where(ok, rb[:, None], 0.0)
    y = np.where(ok, r, 0.0)
    def cs(a):
        z = np.cumsum(np.vstack([np.zeros((1,) + a.shape[1:]), a]), 0)
        return z[win:] - z[:-win]
    n, sx, sy, sxy, sxx = cs(ok.astype(float)), cs(x), cs(y), cs(x * y), cs(x * x)
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = sxy / n - sx / n * sy / n
        var = sxx / n - (sx / n) ** 2
        b = cov / var
    b = np.where(n >= win * 0.7, b, np.nan)
    out = np.full(r.shape, np.nan)
    out[win - 1:] = b
    return out


def roll_std(x, win):
    ok = ~np.isnan(x)
    z = np.where(ok, x, 0.0)
    c1 = np.cumsum(np.r_[0, z]); c2 = np.cumsum(np.r_[0, z * z]); cn = np.cumsum(np.r_[0, ok.astype(float)])
    out = np.full(len(x), np.nan)
    n = cn[win:] - cn[:-win]
    m = (c1[win:] - c1[:-win]) / n
    v = (c2[win:] - c2[:-win]) / n - m * m
    out[win - 1:] = np.sqrt(np.maximum(v, 0))
    return out


def lret(c, L):
    """L-bar log return ending at row t."""
    c = c.astype(np.float64)
    out = np.full(c.shape, np.nan)
    out[L:] = np.log(c[L:] / c[:-L])
    return out
