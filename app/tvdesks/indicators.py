"""Raw market-order requests of the two admitted TradingView sources, recomputed from completed candles.

Each function returns (buy, sell): one boolean per candle, meaning "the source requested this order at that candle's
close". The cash broker (broker.py) decides what fills. The formulas mirror the package's independent checks
(verify_execution.py), which it proved identical to its PineTS 0.11.0 runs of the original sources.

T3-CCI ("FX Sniper: T3-CCI Strategy", HPotter, tfkYcvBX) -- defaults CCI 14 on CLOSE, T3 period 5, b 0.618, reverse off:
    CCI = (close - SMA14(close)) / (0.015 * mean absolute deviation of the last 14 closes)
    nr = 1 + 0.5*(5-1) = 3, w1 = 2/(nr+1) = 0.5, w2 = 0.5
    e1 = w1*CCI + w2*nz(e1[1]); e2..e6 apply the same recurrence to the previous stage (nz -> an unavailable previous
    value counts as 0; a stage is unavailable while its input is)
    T3 = -b^3*e6 + 3(b^2+b^3)*e5 - 3(2b^2+b+b^3)*e4 + (1+3b+b^3+3b^2)*e3
    pos = 1 if T3 > 0, -1 if T3 < 0, else the previous pos (0 before any) -> buy request while pos == 1 (every bar),
    sell request while pos == -1 (the source's short entry, mapped to a cash exit).

CHI ("CHI", juanchez, 8F6WNLkO) -- defaults EMA 20 / 50 on close, BB 40 x 2 (population stdev), widen 9 %, Chandelier 22 / 4:
    the source reads close[barstate.islast ? 1 : 0] for its EMAs and Bollinger bands: on the LAST candle of the data the
    previous close is used. `islast=True` preserves that substitution (historical parity: only the final candle of a
    run is affected, and a final-candle request never fills). See service.py for its forward behaviour.
    width = (upper - lower) / basis = 4*stdev0(c,40)/SMA40(c);  roc = 100*(width - width[1]) / width[1]
    cross = roc crosses above 9 (roc > 9 and roc[1] <= 9) -- a 9 % CHANGE in relative width, not width >= 9 %
    long stop = highest(high, 22) - 4*ATR22 (Wilder RMA of the true range, raw candles, no substitution)
    buy  = EMA20 > EMA50 and cross
    sell = close crosses below the long stop (crossunder), or EMA20 < EMA50 and cross (the source's short entry ->
           cash exit). The crossunder exit is a close-confirmed market exit, not an intrabar stop.

`window` restricts requests to candles whose open time is in [start_ms, end_ms) -- the package's date gating. In the
original instrumentation the CHI Chandelier exit is NOT date-gated (its other requests are); the broker only acts
inside the window, so this only matters for audits, and gate_chi_exit=False reproduces the saved request arrays.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

NAN = float("nan")


def _window(times: Sequence[int], start_ms: int | None, end_ms: int | None) -> np.ndarray:
    t = np.asarray(times, dtype=np.int64)
    m = np.ones(len(t), dtype=bool)
    if start_ms is not None:
        m &= t >= start_ms
    if end_ms is not None:
        m &= t < end_ms
    return m


def _rolling(values: np.ndarray, n: int, fn) -> np.ndarray:
    out = np.full(len(values), NAN)
    for i in range(n - 1, len(values)):
        w = values[i - n + 1:i + 1]
        if np.all(np.isfinite(w)):
            out[i] = fn(w)
    return out


def sma(values: np.ndarray, n: int) -> np.ndarray:
    return _rolling(values, n, lambda w: float(np.mean(w)))


def ema_sma_seed(values: np.ndarray, n: int) -> np.ndarray:
    """Pine ta.ema: seeded with the simple average of the first n available values."""
    out, seed, prev, a = np.full(len(values), NAN), [], NAN, 2.0 / (n + 1)
    for i, v in enumerate(values):
        if not math.isfinite(v):
            continue
        if not math.isfinite(prev):
            seed.append(v)
            if len(seed) < n:
                continue
            prev = float(np.mean(seed[-n:]))
        else:
            prev = v * a + prev * (1 - a)
        out[i] = prev
    return out


def rma(values: np.ndarray, n: int) -> np.ndarray:
    """Pine ta.rma (Wilder), seeded with the simple average of the first n available values."""
    out, seed, prev = np.full(len(values), NAN), [], NAN
    for i, v in enumerate(values):
        if not math.isfinite(v):
            continue
        if not math.isfinite(prev):
            seed.append(v)
            if len(seed) < n:
                continue
            prev = float(np.mean(seed[-n:]))
        else:
            prev = (v + (n - 1) * prev) / n
        out[i] = prev
    return out


# -- T3-CCI -----------------------------------------------------------------------------------------------------------
T3_CCI_PARAMS = {"cci_length": 14, "cci_source": "close", "t3_period": 5, "b": 0.618, "reverse": False}


def t3cci_series(closes: Sequence[float], cci_length: int = 14, t3_period: int = 5, b: float = 0.618) -> dict[str, np.ndarray]:
    c = np.asarray(closes, dtype=float)
    mean = sma(c, cci_length)
    mad = _rolling(c, cci_length, lambda w: float(np.mean(np.abs(w - w.mean()))))
    with np.errstate(divide="ignore", invalid="ignore"):
        cci = (c - mean) / (0.015 * mad)
    nn = 1 if t3_period < 1 else t3_period
    nr = 1 + 0.5 * (nn - 1)
    w1 = 2 / (nr + 1)
    w2 = 1 - w1
    stages, values = [], cci
    for _ in range(6):
        stage = np.full(len(values), NAN)
        for i, x in enumerate(values):
            prev = stage[i - 1] if i and math.isfinite(stage[i - 1]) else 0.0          # nz(e[1])
            stage[i] = w1 * x + w2 * prev
        stages.append(stage)
        values = stage
    b2, b3 = b * b, b * b * b
    t3 = -b3 * stages[5] + 3 * (b2 + b3) * stages[4] - 3 * (2 * b2 + b + b3) * stages[3] + (1 + 3 * b + b3 + 3 * b2) * stages[2]
    pos = np.zeros(len(c))
    prev = 0.0
    for i, v in enumerate(t3):
        if v > 0:
            prev = 1.0
        elif v < 0:
            prev = -1.0
        pos[i] = prev                                                       # exactly 0 / unavailable keeps the previous
    return {"cci": cci, "t3": t3, "pos": pos}


def t3cci_requests(bars: Sequence[dict], start_ms: int | None = None, end_ms: int | None = None):
    s = t3cci_series([b["c"] for b in bars])
    win = _window([b["t"] for b in bars], start_ms, end_ms)
    return (s["pos"] == 1) & win, (s["pos"] == -1) & win, s


# -- CHI ----------------------------------------------------------------------------------------------------------------
CHI_PARAMS = {"ema_fast": 20, "ema_slow": 50, "ema_type": "ema", "bb_length": 40, "bb_mult": 2.0, "bb_stdev": "population",
              "widen_pct": 9, "chandelier_length": 22, "atr_length": 22, "atr_mult": 4, "enable_long": True, "enable_short": True}


def chi_series(bars: Sequence[dict], islast: bool = True) -> dict[str, np.ndarray]:
    close = np.asarray([b["c"] for b in bars], dtype=float)
    high = np.asarray([b["h"] for b in bars], dtype=float)
    low = np.asarray([b["l"] for b in bars], dtype=float)
    c = close.copy()
    if islast and len(c) >= 2:
        c[-1] = c[-2]                                       # close[barstate.islast ? 1 : 0] on the last candle
    fast, slow = ema_sma_seed(c, 20), ema_sma_seed(c, 50)
    basis = sma(c, 40)
    sd = _rolling(c, 40, lambda w: float(np.std(w)))         # population standard deviation (ddof = 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        width = 4 * sd / basis                               # (upper - lower) / basis with 2 stdev bands
        prev_w = np.r_[NAN, width[:-1]]
        roc = (width - prev_w) / prev_w * 100
    prev_roc = np.r_[NAN, roc[:-1]]
    cross = (roc > 9) & (prev_roc <= 9)
    prev_close = np.r_[NAN, close[:-1]]
    tr = np.where(np.isfinite(prev_close),
                  np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)]), high - low)
    hh = _rolling(high, 22, lambda w: float(np.max(w)))
    longstop = hh - 4 * rma(tr, 22)
    prev_stop = np.r_[NAN, longstop[:-1]]
    out = (close < longstop) & (prev_close >= prev_stop)
    return {"fast": fast, "slow": slow, "width": width, "roc": roc, "cross": cross, "longstop": longstop, "out": out,
            "long_cond": (fast > slow) & cross, "short_cond": (fast < slow) & cross}


def chi_requests(bars: Sequence[dict], start_ms: int | None = None, end_ms: int | None = None, islast: bool = True,
                 gate_chi_exit: bool = True):
    s = chi_series(bars, islast=islast)
    win = _window([b["t"] for b in bars], start_ms, end_ms)
    buy = s["long_cond"] & win
    sell = (s["out"] & (win if gate_chi_exit else True)) | (s["short_cond"] & win)
    return buy, sell, s
