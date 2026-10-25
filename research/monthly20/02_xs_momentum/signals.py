"""Cross-sectional scores. score[i] uses bars <= i-1 only (known at the open of bar i)."""
import numpy as np


def shift(a, k):
    out = np.full_like(a, np.nan)
    if k < len(a):
        out[k:] = a[:len(a) - k]
    return out


def ret_lb(P, L):
    """return over the last L closed bars, known at open of bar i: c[i-1]/c[i-1-L]-1"""
    c = P.c
    return shift(c, 1) / shift(c, 1 + L) - 1


def resid_btc(P, L, beta_win, btc_j):
    """L-bar return minus beta * BTC's L-bar return (beta from beta_win bars of 1-bar returns)."""
    r1 = P.c / shift(P.c, 1) - 1
    b = r1[:, [btc_j]]
    # rolling beta via cumulative sums
    def roll(x):
        x = np.nan_to_num(x)
        cs = np.cumsum(x, 0)
        out = cs.copy(); out[beta_win:] = cs[beta_win:] - cs[:-beta_win]
        return out
    m = ~np.isnan(r1)
    n = roll(m.astype(float) * ~np.isnan(b))
    sx = roll(np.where(m, b, 0)); sy = roll(np.where(m, r1, 0) * ~np.isnan(b))
    sxy = roll(np.where(m, r1 * b, 0)); sxx = roll(np.where(m, b * b, 0))
    with np.errstate(invalid="ignore", divide="ignore"):
        beta = (sxy - sx * sy / n) / (sxx - sx * sx / n)
    beta = np.where(n >= beta_win * 0.8, beta, np.nan)
    rl = ret_lb(P, L)
    beta_k = shift(beta, 1)
    return rl - beta_k * rl[:, [btc_j]]


def vol_surge(P, L, base=30):
    """log(quote vol of last L bars / average L-bar quote vol over the last `base` bars), signed by the L-bar return."""
    qv = np.nan_to_num(P.qv)
    cs = np.vstack([np.zeros((1, qv.shape[1])), np.cumsum(qv, 0)])
    T = qv.shape[0]
    s = np.full(qv.shape, np.nan)
    for i in range(base + 1, T):
        last = cs[i] - cs[i - L]
        avg = (cs[i] - cs[i - base]) / base * L
        with np.errstate(invalid="ignore", divide="ignore"):
            s[i] = np.log(last / avg)
    return s * np.sign(ret_lb(P, L))
