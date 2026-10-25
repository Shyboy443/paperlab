"""Reversal scores, all known at the open of hour i (built from closes up to hour i-1). Higher score = rose more.
The engine longs the LOWEST scores and shorts the HIGHEST.

raw    : L-hour simple return
resid  : L-hour log return minus beta x BTC's L-hour log return (beta = OLS on the trailing 720 hourly log returns)
z      : resid / (sigma_resid x sqrt(L)), sigma_resid = std of the trailing 720 hourly residual returns
zv     : z / sqrt(relvol), relvol = quote volume of the last L hours / (L x mean hourly quote volume of the last 720 h)
         (moves on light volume get a bigger score: the textbook "low-volume moves reverse" refinement)
"""
import numpy as np

WIN = 720


def _roll(a, w):
    cs = np.cumsum(np.vstack([np.zeros((1, a.shape[1])), a]), axis=0)
    out = np.full(a.shape, np.nan)
    out[w - 1:] = cs[w:] - cs[:-w]
    return out


class Scores:
    def __init__(self, P):
        self.P = P
        cf = P.cf
        T, N = cf.shape
        self.beta = np.full((T, N), np.nan, np.float32)
        self.sig = np.full((T, N), np.nan, np.float32)
        lrb = np.zeros(T); lrb[1:] = np.nan_to_num(np.log(cf[1:, P.btc].astype(np.float64) / cf[:-1, P.btc]))
        for c0 in range(0, N, 48):  # column chunks keep memory low
            cc = slice(c0, min(N, c0 + 48))
            lr = np.full((T, cc.stop - c0), np.nan)
            lr[1:] = np.log(cf[1:, cc].astype(np.float64) / cf[:-1, cc])
            valid = np.isfinite(lr)
            x = np.where(valid, lrb[:, None], 0.0)
            y = np.where(valid, lr, 0.0)
            n = _roll(valid.astype(float), WIN)
            sx, sy, sxx, sxy, syy = (_roll(v, WIN) for v in (x, y, x * x, x * y, y * y))
            with np.errstate(invalid="ignore", divide="ignore"):
                vx = sxx / n - (sx / n) ** 2
                cov = sxy / n - (sx / n) * (sy / n)
                vy = syy / n - (sy / n) ** 2
                beta = cov / vx
                vres = np.maximum(vy - cov ** 2 / vx, 1e-12)
            beta[~(n >= WIN / 2)] = np.nan
            # shift by one hour: the value used at hour i is the one computed through hour i-1
            self.beta[1:, cc] = beta[:-1]
            self.sig[1:, cc] = np.sqrt(vres)[:-1]
        self.logc = np.log(cf).astype(np.float32)
        self.cache = {}

    def _ret(self, L):
        lc = self.logc
        out = np.full(lc.shape, np.nan, np.float32)
        out[L + 1:] = lc[L:-1] - lc[:-1 - L]  # close[i-1] vs close[i-1-L]
        return out

    def get(self, kind, L):
        key = (kind, L)
        if key in self.cache:
            return self.cache[key]
        if len(self.cache) >= 2:
            self.cache.pop(next(iter(self.cache)))
        r = self._ret(L).astype(np.float32)
        if kind == "raw":
            s = np.expm1(r)
        else:
            rb = r[:, [self.P.btc]]
            s = r - self.beta * rb
            if kind in ("z", "zv"):
                s = s / (self.sig * np.sqrt(L))
            if kind == "zv":
                qv = np.nan_to_num(self.P.qv.astype(np.float64))
                recent = _roll(qv, L) / L       # row m = hours m-L+1..m
                base = _roll(qv, WIN) / WIN
                del qv
                with np.errstate(invalid="ignore", divide="ignore"):
                    rel = (recent / base).astype(np.float32)
                del recent, base
                rel = np.vstack([np.full((1, rel.shape[1]), np.nan, np.float32), rel[:-1]])  # known at hour i: through i-1
                s = s / np.sqrt(np.clip(rel, 0.1, 10))
        s = s.astype(np.float32)
        self.cache[key] = s
        return s
