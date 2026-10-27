"""Round-6 'signal' family: score matrices for lib6.run5. Every matrix is known at the day-i open (uses data to close i-1)."""
import numpy as np
import pandas as pd
import lib6 as L

_C = {}


def _sh(a):
    return L.L4._shift(np.asarray(a, float))


def raw(Pn):
    return L.score(Pn, "30")


def chan_close(Pn, X, n=30):
    """Channel position of a close-only series X (T x N), shifted to the open."""
    d = pd.DataFrame(X); mp = int(round(n * 0.9))
    hx = d.rolling(n, min_periods=mp).max().values; lx = d.rolling(n, min_periods=mp).min().values
    with np.errstate(invalid="ignore", divide="ignore"):
        s = (X - lx) / (hx - lx)
    return _sh(s)


def ratio(Pn):
    """A1: channel position of coin/BTC close ratio (BTC itself = 0.5)."""
    k = (id(Pn), "ratio")
    if k not in _C:
        b = Pn.syms.index("BTCUSDT")
        cb = pd.Series(Pn.c[:, b]).ffill().values
        s = chan_close(Pn, Pn.c / cb[:, None])
        s[:, b] = np.where(np.isnan(raw(Pn)[:, b]), np.nan, 0.5)
        _C[k] = s
    return _C[k]


def blitz(Pn, W=90):
    """A3: channel position of the cumulative BTC-beta residual return path (beta from a 90d window ending the day before)."""
    k = (id(Pn), "blitz", W)
    if k not in _C:
        b = Pn.syms.index("BTCUSDT")
        lr = np.log(pd.DataFrame(Pn.c)).diff()
        rb = lr.iloc[:, b]
        cov = lr.rolling(W, min_periods=int(W * 2 / 3)).cov(rb)
        var = rb.rolling(W, min_periods=int(W * 2 / 3)).var()
        beta = cov.div(var, axis=0).shift(1)
        res = lr.sub(beta.mul(rb, axis=0))
        pp = res.fillna(0).cumsum().values
        pp[np.isnan(Pn.c) | np.isnan(beta.values)] = np.nan
        s = chan_close(Pn, pp)
        s[:, b] = np.where(np.isnan(raw(Pn)[:, b]), np.nan, 0.5)
        _C[k] = s
    return _C[k]


def volscaled(Pn):
    """B1: (position - 0.5) / trailing 30d vol."""
    return (raw(Pn) - 0.5) * L.L4.ivol(Pn, 30)


def jump(Pn, kind="abs"):
    """Largest daily close-to-close log move in 30 days / log(30d high/low). kind abs|up|down. Shifted."""
    k = (id(Pn), "jump", kind)
    if k not in _C:
        lr = np.log(pd.DataFrame(Pn.c)).diff()
        x = lr.abs() if kind == "abs" else (lr.clip(lower=0) if kind == "up" else (-lr).clip(lower=0))
        mx = x.rolling(30, min_periods=27).max().values
        hx = pd.DataFrame(Pn.h).rolling(30, min_periods=27).max().values
        lx = pd.DataFrame(Pn.l).rolling(30, min_periods=27).min().values
        with np.errstate(invalid="ignore", divide="ignore"):
            _C[k] = _sh(mx / np.log(hx / lx))
    return _C[k]


def ret(Pn, n=7):
    k = (id(Pn), "ret", n)
    if k not in _C:
        c = pd.DataFrame(Pn.c).ffill()
        _C[k] = _sh((c / c.shift(n) - 1).values)
    return _C[k]


def mask(S, keep):
    out = S.copy(); out[~(keep == True)] = np.nan  # noqa
    return out


def build(Pn, spec):
    """spec -> dict(cfg_over, score_override, long_score, short_score)."""
    s = raw(Pn); kw = dict(cfg={}, score_override=None, long_score=None, short_score=None)
    for part in spec.split("+"):
        f, _, v = part.partition("=")
        if f == "base":
            pass
        elif f == "ratio":
            kw["score_override"] = ratio(Pn)
        elif f == "avgratio":
            kw["score_override"] = (s + ratio(Pn)) / 2
        elif f == "blitz":
            kw["score_override"] = blitz(Pn)
        elif f == "volsc":
            kw["score_override"] = volscaled(Pn)
        elif f == "jump":  # symmetric exclusion
            S0 = kw["score_override"] if kw["score_override"] is not None else s
            kw["score_override"] = mask(S0, ~(jump(Pn, "abs") > float(v)))
        elif f == "jumpup":  # long leg only: exclude coins whose biggest up-day > v of the range
            S0 = kw["score_override"] if kw["score_override"] is not None else s
            kw["long_score"] = mask(S0, ~(jump(Pn, "up") > float(v)))
        elif f == "s7neg":
            S0 = kw["short_score"] if kw["short_score"] is not None else (kw["score_override"] if kw["score_override"] is not None else s)
            kw["short_score"] = mask(S0, ret(Pn, int(v) if v else 7) < 0)
        elif f == "schan":
            kw["short_score"] = L.L4.rng(Pn, int(v))
        elif f == "age":
            kw["cfg"]["age"] = int(v)
        else:
            raise ValueError(part)
    return kw
