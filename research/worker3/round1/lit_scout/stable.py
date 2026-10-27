"""Candidate 1: stablecoin float growth as a crypto market timer.

g_d = S(d-2) / S(d-2-L) - 1, S = DefiLlama total circulating USD-pegged stablecoins (date label = UTC day). Using the
value labelled two days before the trading day keeps it strictly point-in-time whatever time of day DefiLlama stamps.
ON (g > thr): long the top-K coins by 30-day quote volume (point-in-time, survivorship-free), equal weight 1x total.
OFF: flat (mode LF) or short the same basket (mode LS).
Basket re-picked and re-weighted every `recon` days and whenever the signal flips.
"""
import json
import numpy as np
from lib import *  # noqa

_S = None


def stable_series():
    global _S
    if _S is None:
        rows = json.loads((DATA / "stable_all.json").read_text())
        m = {int(r["date"]) * 1000: float(r["totalCirculatingUSD"]["peggedUSD"]) for r in rows}
        s = np.array([m.get(int(t), np.nan) for t in P.t])
        # forward-fill gaps
        for i in range(1, len(s)):
            if np.isnan(s[i]):
                s[i] = s[i - 1]
        _S = s
    return _S


def signal(L):
    S = stable_series()
    g = np.full(T, np.nan)
    g[2 + L:] = S[L:T - 2] / S[:T - 2 - L] - 1  # g[d] = S[d-2] / S[d-2-L] - 1
    return g


def on_series(L, thr):
    g = signal(L)
    return np.where(np.isnan(g), False, g > thr)


def make(L, thr, K, mode="LF", recon=7, on_override=None):
    def make_W(exclude=None):
        on = on_series(L, thr) if on_override is None else on_override
        um = umask(max(K, 40)).copy()
        if exclude is not None:
            um &= ~exclude[None, :]
        qv = np.nan_to_num(P.qv)
        cs = np.vstack([np.zeros((1, N)), np.cumsum(qv, 0)])
        W = np.full((T, N), np.nan)
        last_on, cnt = None, 0
        for i in range(31, T):
            if on[i] != last_on or cnt % recon == 0:
                vol = np.where(um[i], cs[i] - cs[i - 30], -1)
                top = np.argsort(-vol)[:K]
                top = top[vol[top] > 0]
                w = np.zeros(N)
                if len(top):
                    sgn = 1.0 if on[i] else (-1.0 if mode == "LS" else 0.0)
                    w[top] = sgn / len(top)
                W[i] = w
                cnt = 0
            last_on = on[i]; cnt += 1
        return W
    return make_W


def on_fraction(L, thr, a, b):
    g = signal(L)[idx(a):idx(b)]
    return float(np.mean(np.nan_to_num(g, nan=-1) > thr))
