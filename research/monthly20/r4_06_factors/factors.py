"""Point-in-time factor scores on the daily panel. Row i of every array = what is known at the OPEN of UTC day i
(statistics over daily close-to-close returns of days i-L..i-1). Higher score = long, lower = short.

r[d] = c[d]/c[d-1] - 1 (NaN if either close is missing). Market = BTCUSDT perp daily return.
min_periods = 80% of the window.

Factors (the sign is the textbook one; liquidity factors are run with both signs):
  lowvol   : -std(r, L)                                   long low volatility, short high
  beta     : -beta to BTC over L                          betting-against-beta (run beta-neutral = BAB)
  ivol     : -std of residual of r on BTC over L          long low idiosyncratic vol
  max      : -max(r, L)                                   short the lottery coins (biggest single-day jump)
  skew     : -skewness(r, L)                              short positive skew
  volg+/-  : +/- log(mean quote vol last L days / mean quote vol last 30 days)  (L in 3,7,14)
  size+/-  : +/- log(mean quote vol over L)  ('size-' = long the SMALL (low volume) coins of the universe)
  combo    : equal-weight mean of cross-sectional percentile ranks of lowvol, beta, ivol, max, skew (same L)
"""
import numpy as np
import pandas as pd


def daily_ret(c):
    r = np.full_like(c, np.nan)
    r[1:] = c[1:] / c[:-1] - 1
    return r


def shift1(a):
    out = np.full_like(a, np.nan)
    out[1:] = a[:-1]
    return out


class Factors:
    def __init__(self, P, btc_close):
        self.P = P
        self.r = pd.DataFrame(daily_ret(P.c))
        m = daily_ret(btc_close.reshape(-1, 1))[:, 0]
        self.m = pd.Series(m)
        self.qv = pd.DataFrame(P.qv)
        self.cache = {}

    def _beta_parts(self, L):
        key = ("bp", L)
        if key not in self.cache:
            mp = int(0.8 * L)
            r = self.r
            m = pd.DataFrame(np.repeat(self.m.values[:, None], r.shape[1], 1)).where(r.notna())
            rm = (r * m).rolling(L, min_periods=mp).mean()
            mr = r.rolling(L, min_periods=mp).mean()
            mm = m.rolling(L, min_periods=mp).mean()
            vm = (m * m).rolling(L, min_periods=mp).mean() - mm ** 2
            vr = (r * r).rolling(L, min_periods=mp).mean() - mr ** 2
            cov = rm - mr * mm
            b = cov / vm.where(vm > 0)
            resid_var = (vr - b * cov).clip(lower=0)
            self.cache[key] = (b.values, np.sqrt(resid_var.values))
        return self.cache[key]

    def beta_raw(self, L):
        """beta to BTC known at the open of day i (used for beta-neutral sizing)."""
        return shift1(self._beta_parts(L)[0])

    def score(self, name, L):
        key = (name, L)
        if key in self.cache:
            return self.cache[key]
        mp = int(0.8 * L)
        if name == "lowvol":
            a = -self.r.rolling(L, min_periods=mp).std().values
        elif name == "beta":
            a = -self._beta_parts(L)[0]
        elif name == "ivol":
            a = -self._beta_parts(L)[1]
        elif name == "max":
            a = -self.r.rolling(L, min_periods=mp).max().values
        elif name == "skew":
            a = -self.r.rolling(L, min_periods=mp).skew().values
        elif name in ("volg+", "volg-"):
            q = self.qv.where(self.qv > 0)
            a = np.log(q.rolling(L, min_periods=mp).mean() / q.rolling(30, min_periods=24).mean()).values
            a = a if name == "volg+" else -a
        elif name in ("size+", "size-"):
            q = self.qv.where(self.qv > 0)
            a = np.log(q.rolling(L, min_periods=mp).mean()).values
            a = a if name == "size+" else -a
        elif name == "combo":
            parts = []
            for f in ("lowvol", "beta", "ivol", "max", "skew"):
                s = pd.DataFrame(self.score(f, L))  # already shifted
                parts.append(s.rank(axis=1, pct=True).values)
            a = np.nanmean(np.stack(parts), 0)  # mean of ranks; NaN only if all parts NaN
            bad = np.any(np.isnan(np.stack(parts)), 0)
            a[bad] = np.nan
            self.cache[key] = a
            return a
        else:
            raise ValueError(name)
        a = shift1(np.asarray(a, float))
        self.cache[key] = a
        return a
