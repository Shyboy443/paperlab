"""Indicators for the YouTube-ranking strategies (numpy, Pine conventions: EMA / RMA seeded with the simple average of the
first n values, Wilder RSI / ATR, NaN until enough data). Every function takes float arrays and returns float arrays."""
from __future__ import annotations

import numpy as np

NAN = np.nan


def ema(x: np.ndarray, n: int, alpha: float | None = None) -> np.ndarray:
    a = 2 / (n + 1) if alpha is None else alpha
    out = np.full(len(x), NAN)
    ok = np.flatnonzero(~np.isnan(x))
    if len(ok) < n:
        return out
    s = ok[0]
    if np.isnan(x[s:s + n]).any():                       # inputs are contiguous once they start
        return out
    prev = float(np.mean(x[s:s + n]))
    out[s + n - 1] = prev
    for i in range(s + n, len(x)):
        v = x[i]
        prev = prev if np.isnan(v) else a * v + (1 - a) * prev
        out[i] = prev
    return out


def rma(x: np.ndarray, n: int) -> np.ndarray:
    return ema(x, n, alpha=1 / n)


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), NAN)
    if len(x) < n:
        return out
    c = np.cumsum(np.insert(np.nan_to_num(x), 0, 0.0))
    out[n - 1:] = (c[n:] - c[:-n]) / n
    bad = np.convolve(np.isnan(x).astype(float), np.ones(n), "full")[:len(x)] > 0     # a NaN inside the window
    out[bad] = NAN
    return out


def wma(x: np.ndarray, n: int) -> np.ndarray:
    w = np.arange(1, n + 1, dtype=float)
    out = np.full(len(x), NAN)
    for i in range(n - 1, len(x)):
        seg = x[i - n + 1:i + 1]
        if not np.isnan(seg).any():
            out[i] = float(np.dot(seg, w) / w.sum())
    return out


def rolling(x: np.ndarray, n: int, fn) -> np.ndarray:
    out = np.full(len(x), NAN)
    if len(x) >= n:
        v = np.lib.stride_tricks.sliding_window_view(x, n)
        out[n - 1:] = fn(v, axis=1)
    return out


def highest(x, n):
    return rolling(x, n, np.max)


def lowest(x, n):
    return rolling(x, n, np.min)


def shift(x: np.ndarray, k: int) -> np.ndarray:
    """x[i - k] at i (k > 0: the past)."""
    out = np.full(len(x), NAN)
    if k < len(x):
        out[k:] = x[:len(x) - k]
    return out


def true_range(h, l, c):
    pc = shift(c, 1)
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    tr[0] = h[0] - l[0]
    return tr


def atr(h, l, c, n=14):
    return rma(true_range(h, l, c), n)


def rsi(c, n=14):
    d = np.diff(c, prepend=NAN)
    up, dn = rma(np.where(d > 0, d, 0.0) * np.where(np.isnan(d), NAN, 1), n), rma(np.where(d < 0, -d, 0.0) * np.where(np.isnan(d), NAN, 1), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = 100 - 100 / (1 + up / dn)
    r = np.where(dn == 0, 100.0, r)
    return np.where(np.isnan(up) | np.isnan(dn), NAN, r)


def stoch(h, l, c, k=14, smooth_k=3, d=3):
    hh, ll = highest(h, k), lowest(l, k)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where(hh > ll, 100 * (c - ll) / (hh - ll), 50.0)
    raw = np.where(np.isnan(hh), NAN, raw)
    kk = sma(raw, smooth_k)
    return kk, sma(kk, d)


def stoch_rsi(c, rsi_len=14, stoch_len=14, k=3, d=3):
    r = rsi(c, rsi_len)
    hh, ll = highest(r, stoch_len), lowest(r, stoch_len)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where(hh > ll, 100 * (r - ll) / (hh - ll), 50.0)
    raw = np.where(np.isnan(hh), NAN, raw)
    kk = sma(raw, k)
    return kk, sma(kk, d)


def macd(c, fast=12, slow=26, sig=9):
    line = ema(c, fast) - ema(c, slow)
    return line, ema(line, sig)


def supertrend(h, l, c, n=10, mult=3.0):
    """(line, direction): direction +1 = bullish (line below price), -1 = bearish."""
    a = atr(h, l, c, n)
    hl2 = (h + l) / 2
    up, dn = hl2 - mult * a, hl2 + mult * a
    line, d = np.full(len(c), NAN), np.zeros(len(c))
    fu, fd = NAN, NAN
    for i in range(len(c)):
        if np.isnan(a[i]):
            continue
        pfu, pfd = fu, fd
        fu = up[i] if np.isnan(pfu) or c[i - 1] < pfu else max(up[i], pfu)
        fd = dn[i] if np.isnan(pfd) or c[i - 1] > pfd else min(dn[i], pfd)
        prev = d[i - 1] if i else 0
        if prev == 0:
            d[i] = 1 if c[i] > fd else -1
        elif prev < 0:
            d[i] = 1 if c[i] > pfd else -1
        else:
            d[i] = -1 if c[i] < pfu else 1
        line[i] = fu if d[i] > 0 else fd
    return line, d


def psar(h, l, start=0.02, inc=0.02, mx=0.2):
    """(sar, direction +1 long / -1 short)."""
    n = len(h)
    sar, d = np.full(n, NAN), np.zeros(n)
    if n < 2:
        return sar, d
    up = h[1] > h[0]
    ep, af, s = (h[1] if up else l[1]), start, (l[0] if up else h[0])
    for i in range(1, n):
        s = s + af * (ep - s)
        if up:
            s = min(s, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < s:
                up, s, ep, af = False, ep, l[i], start
            elif h[i] > ep:
                ep, af = h[i], min(af + inc, mx)
        else:
            s = max(s, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > s:
                up, s, ep, af = True, ep, h[i], start
            elif l[i] < ep:
                ep, af = l[i], min(af + inc, mx)
        sar[i], d[i] = s, 1 if up else -1
    return sar, d


def mfi(h, l, c, v, n=14):
    tp = (h + l + c) / 3
    flow = tp * v
    dtp = np.diff(tp, prepend=NAN)
    pos = rolling(np.where(dtp > 0, flow, 0.0), n, np.sum)
    neg = rolling(np.where(dtp < 0, flow, 0.0), n, np.sum)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = 100 - 100 / (1 + pos / neg)
    return np.where(neg == 0, 100.0, r)


def cmf(h, l, c, v, n=20):
    with np.errstate(divide="ignore", invalid="ignore"):
        mfm = np.where(h > l, ((c - l) - (h - c)) / (h - l), 0.0)
    return rolling(mfm * v, n, np.sum) / rolling(v, n, np.sum)


def wavetrend(h, l, c, n1=10, n2=21):
    ap = (h + l + c) / 3
    esa = ema(ap, n1)
    d = ema(np.abs(ap - esa), n1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ci = (ap - esa) / (0.015 * d)
    wt1 = ema(ci, n2)
    return wt1, sma(wt1, 4)


def stc(c, length=10, fast=23, slow=50, factor=0.5):
    m = ema(c, fast) - ema(c, slow)
    out = np.full(len(c), NAN)
    f1 = pf = f2 = st = NAN
    lo1, hi1 = lowest(m, length), highest(m, length)
    pfs = np.full(len(c), NAN)
    for i in range(len(c)):
        if np.isnan(lo1[i]):
            continue
        rng = hi1[i] - lo1[i]
        f1 = (m[i] - lo1[i]) / rng * 100 if rng > 0 else (f1 if not np.isnan(f1) else 0.0)
        pf = f1 if np.isnan(pf) else pf + factor * (f1 - pf)
        pfs[i] = pf
        w = pfs[max(0, i - length + 1):i + 1]
        if np.isnan(w).any() or len(w) < length:
            continue
        lo2, rng2 = w.min(), w.max() - w.min()
        f2 = (pf - lo2) / rng2 * 100 if rng2 > 0 else (f2 if not np.isnan(f2) else 0.0)
        st = f2 if np.isnan(st) else st + factor * (f2 - st)
        out[i] = st
    return out


def ichimoku(h, l, conv=9, base=26, span_b=52, disp=26):
    """tenkan, kijun, cloud A / B AT each candle (the spans drawn there, computed disp candles earlier), and the
    spans computed NOW (the cloud disp candles ahead)."""
    ten = (highest(h, conv) + lowest(l, conv)) / 2
    kij = (highest(h, base) + lowest(l, base)) / 2
    a_now = (ten + kij) / 2
    b_now = (highest(h, span_b) + lowest(l, span_b)) / 2
    return ten, kij, shift(a_now, disp - 1), shift(b_now, disp - 1), a_now, b_now


def smma(x, n):
    return rma(x, n)


def heikin_ashi(o, h, l, c):
    hc = (o + h + l + c) / 4
    ho = np.empty(len(c))
    ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(c)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    return ho, hc


def linreg(x, n):
    """Pine ta.linreg(x, n, 0): the least-squares line's value at the newest point."""
    out = np.full(len(x), NAN)
    t = np.arange(n, dtype=float)
    tm = t.mean()
    den = ((t - tm) ** 2).sum()
    for i in range(n - 1, len(x)):
        y = x[i - n + 1:i + 1]
        if np.isnan(y).any():
            continue
        b = ((t - tm) * (y - y.mean())).sum() / den
        out[i] = y.mean() + b * (n - 1 - tm)
    return out


def cross_up(a, b):
    """a crosses above b at i (a > b now, a <= b on the previous candle); b may be a number."""
    b = np.full(len(a), float(b)) if np.isscalar(b) else b
    pa, pb = shift(a, 1), shift(b, 1)
    with np.errstate(invalid="ignore"):
        return (a > b) & (pa <= pb)


def cross_down(a, b):
    b = np.full(len(a), float(b)) if np.isscalar(b) else b
    pa, pb = shift(a, 1), shift(b, 1)
    with np.errstate(invalid="ignore"):
        return (a < b) & (pa >= pb)
