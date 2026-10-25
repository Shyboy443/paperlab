"""Scribd round 6: the index mean-reversion family out of sample (docs/SCRIBD_ROUND6_STUDY.md), written down before any
result. Daily candles (swing.D). Trades: {t, exit_t, dir, entry, ret_pct, r, result} with ret_pct after ``cost`` per side.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from . import ind as I
from .round3 import _ibs, _trade, _window
from .swing import D

PAGONIDIS_ETFS = ("ECH", "EEM", "EFA", "EPHE", "EPI", "EPP", "EWA", "EWC", "EWD", "EWG", "EWH", "EWI", "EWJ", "EWL", "EWM",
                  "EWO", "EWP", "EWQ", "EWS", "EWT", "EWU", "EWY", "EWZ", "EZA", "FXI", "GXG", "IDX", "ILF", "IWM", "QQQ",
                  "RSX", "SPY", "THD")


def cutler_rsi(c: np.ndarray, n: int) -> np.ndarray:
    """Cutler's RSI: simple n-day averages of up and down closes; 100 when there were no down closes."""
    d = np.diff(c, prepend=np.nan)
    up = I.sma(np.where(d > 0, d, 0.0), n)
    dn = I.sma(np.where(d < 0, -d, 0.0), n)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = 100 - 100 / (1 + up / dn)
    r = np.where(dn == 0, 100.0, r)
    r[:n] = np.nan
    return r


def pagonidis_ibs_1day(b: D, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """The IBS Effect (168296451), Table 6 strategy: IBS <= 0.2 -> long at the close, IBS >= 0.8 -> short at the close;
    closed at the next close."""
    out, x = [], _ibs(b)
    first, last = _window(b, s0, s1)
    for i in range(first, last):
        d = 1 if x[i] <= 0.2 else -1 if x[i] >= 0.8 else 0
        if d:
            out.append(_trade(b.t[i], b.t[i + 1], d, b.c[i], b.c[i + 1], cost, "next close"))
    return out


def pagonidis_rsi3_ibs(b: D, pip: float, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """The IBS Effect (168296451), Table 16: long only. Buy at the close when Cutler's RSI(3) < 10 and IBS <= 0.5; hold
    while RSI(3) <= 40 and IBS <= 0.5 (checked at each close), otherwise sell at that close."""
    out, x, r = [], _ibs(b), cutler_rsi(b.c, 3)
    first, last = _window(b, s0, s1)
    i = first
    while i < last:
        if not (r[i] < 10 and x[i] <= 0.5):
            i += 1
            continue
        k = i + 1
        while k < last and (r[k] <= 40 and x[k] <= 0.5):
            k += 1
        out.append(_trade(b.t[i], b.t[k], 1, b.c[i], b.c[k], cost, "rule exit" if k < last else "end"))
        i = k
    return out


def stiffness(c: np.ndarray, ma: int = 100, period: int = 60) -> np.ndarray:
    ma2 = I.sma(c, ma) - 0.2 * I.rolling(c, ma, np.std)
    above = np.nan_to_num(c > ma2).astype(float)
    above[np.isnan(ma2)] = np.nan
    return I.sma(above, period) * 100


def katsanos_stiffness(b: D, spy: D, cost: float, s0: int, s1: int) -> list[dict[str, Any]]:
    """The Stiffness Indicator (422943971): buy at the next open when stiffness(100, 60) crosses above 90 and SPY's
    EMA(100) is above yesterday's; sell at the next open when stiffness crosses below 50 or after 84 bars."""
    st = stiffness(b.c)
    e = I.ema(spy.c, 100)
    rising = np.full(len(spy.c), False)
    rising[1:] = e[1:] > e[:-1]
    pos = np.searchsorted(spy.t, b.t, side="right") - 1                 # SPY's day for each stock day
    up = np.where(pos >= 0, rising[np.clip(pos, 0, None)], False)
    first, last = _window(b, s0, s1)
    out, i = [], max(first, 161)
    while i < last:
        if not (st[i - 1] <= 90 < st[i] and up[i]):
            i += 1
            continue
        j = i + 1
        k = j
        while k < last and not (st[k - 1] >= 50 > st[k]) and k - j < 84:
            k += 1
        xk = min(k + 1, last)
        out.append(_trade(b.t[j], b.t[xk], 1, b.o[j], b.o[xk], cost, "rule exit" if k < last else "end"))
        i = xk
    return out
