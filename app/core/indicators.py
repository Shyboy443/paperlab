"""Pure-Python indicator library + per-(symbol, tf, name, params) cache.

Every series function takes plain lists and returns a list aligned to the input,
with None where the value is undefined (warm-up). Nothing here touches I/O, numpy
or pandas: 600-bar windows recompute in well under a millisecond per indicator.
"""
from __future__ import annotations

import math
from statistics import median as _median
from typing import Any, Callable, Sequence

from app.core.types import Candle

Series = list  # list[float | None]


def _nan_to_none(v: float | None) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def sma(vals: Sequence[float | None], n: int) -> Series:
    out: Series = [None] * len(vals)
    if n <= 0:
        return out
    window: list[float] = []
    total = 0.0
    for i, v in enumerate(vals):
        if v is None:
            window.clear()
            total = 0.0
            continue
        window.append(v)
        total += v
        if len(window) > n:
            total -= window.pop(0)
        if len(window) == n:
            out[i] = total / n
    return out


def ema(vals: Sequence[float | None], n: int) -> Series:
    out: Series = [None] * len(vals)
    if n <= 0:
        return out
    k = 2.0 / (n + 1)
    seed: list[float] = []
    prev: float | None = None
    for i, v in enumerate(vals):
        if v is None:
            continue
        if prev is None:
            seed.append(v)
            if len(seed) == n:
                prev = sum(seed) / n
                out[i] = prev
            continue
        prev = v * k + prev * (1 - k)
        out[i] = prev
    return out


def wilder(vals: Sequence[float | None], n: int) -> Series:
    """Wilder's smoothing (RMA): SMA seed, then prev + (v - prev)/n."""
    out: Series = [None] * len(vals)
    if n <= 0:
        return out
    seed: list[float] = []
    prev: float | None = None
    for i, v in enumerate(vals):
        if v is None:
            continue
        if prev is None:
            seed.append(v)
            if len(seed) == n:
                prev = sum(seed) / n
                out[i] = prev
            continue
        prev = prev + (v - prev) / n
        out[i] = prev
    return out


def true_range(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    out: list[float] = []
    for i in range(len(highs)):
        if i == 0:
            out.append(highs[i] - lows[i])
        else:
            pc = closes[i - 1]
            out.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    return out


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> Series:
    return wilder(true_range(highs, lows, closes), n)


def rsi(closes: Sequence[float], n: int = 14) -> Series:
    out: Series = [None] * len(closes)
    if n <= 0 or len(closes) < 2:
        return out
    gains: Series = [None]
    losses: Series = [None]
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = wilder(gains, n)
    al = wilder(losses, n)
    for i in range(len(closes)):
        g, l = ag[i], al[i]
        if g is None or l is None:
            continue
        out[i] = 100.0 if l == 0 else 100.0 - 100.0 / (1.0 + g / l)
    return out


def stdev(vals: Sequence[float | None], n: int) -> Series:
    out: Series = [None] * len(vals)
    if n <= 1:
        return out
    for i in range(n - 1, len(vals)):
        window = vals[i - n + 1:i + 1]
        if any(v is None for v in window):
            continue
        m = sum(window) / n
        out[i] = math.sqrt(sum((v - m) ** 2 for v in window) / n)
    return out


def bollinger(closes: Sequence[float], n: int = 20, k: float = 2.0) -> tuple[Series, Series, Series, Series]:
    basis = sma(closes, n)
    sd = stdev(closes, n)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    width: Series = [None] * len(closes)
    for i in range(len(closes)):
        if basis[i] is None or sd[i] is None:
            continue
        upper[i] = basis[i] + k * sd[i]
        lower[i] = basis[i] - k * sd[i]
        width[i] = (upper[i] - lower[i]) / basis[i] if basis[i] else None
    return basis, upper, lower, width


def rolling_max(vals: Sequence[float], n: int) -> Series:
    out: Series = [None] * len(vals)
    for i in range(n - 1, len(vals)):
        out[i] = max(vals[i - n + 1:i + 1])
    return out


def rolling_min(vals: Sequence[float], n: int) -> Series:
    out: Series = [None] * len(vals)
    for i in range(n - 1, len(vals)):
        out[i] = min(vals[i - n + 1:i + 1])
    return out


def donchian(highs: Sequence[float], lows: Sequence[float], n: int) -> tuple[Series, Series]:
    """Channel of the PRIOR n bars (excludes the current bar so a breakout can be detected)."""
    upper: Series = [None] * len(highs)
    lower: Series = [None] * len(highs)
    for i in range(n, len(highs)):
        upper[i] = max(highs[i - n:i])
        lower[i] = min(lows[i - n:i])
    return upper, lower


def supertrend(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
               n: int = 10, mult: float = 3.0) -> tuple[Series, Series]:
    """Returns (line, direction); direction +1 = bullish (line below price), -1 = bearish."""
    a = atr(highs, lows, closes, n)
    size = len(closes)
    line: Series = [None] * size
    direction: Series = [None] * size
    upper_prev = lower_prev = None
    dir_prev = 1
    for i in range(size):
        if a[i] is None:
            continue
        hl2 = (highs[i] + lows[i]) / 2.0
        upper = hl2 + mult * a[i]
        lower = hl2 - mult * a[i]
        if upper_prev is not None and closes[i - 1] <= upper_prev:
            upper = min(upper, upper_prev)
        if lower_prev is not None and closes[i - 1] >= lower_prev:
            lower = max(lower, lower_prev)
        if upper_prev is None:
            d = 1 if closes[i] > upper else -1
        elif dir_prev == 1:
            d = -1 if closes[i] < lower else 1
        else:
            d = 1 if closes[i] > upper else -1
        direction[i] = d
        line[i] = lower if d == 1 else upper
        upper_prev, lower_prev, dir_prev = upper, lower, d
    return line, direction


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Series, Series, Series]:
    ef = ema(closes, fast)
    es = ema(closes, slow)
    m: Series = [None if (ef[i] is None or es[i] is None) else ef[i] - es[i] for i in range(len(closes))]
    s = ema(m, signal)
    h: Series = [None if (m[i] is None or s[i] is None) else m[i] - s[i] for i in range(len(closes))]
    return m, s, h


def stoch_rsi(closes: Sequence[float], rsi_n: int = 14, stoch_n: int = 14, k: int = 3, d: int = 3) -> tuple[Series, Series]:
    r = rsi(closes, rsi_n)
    raw: Series = [None] * len(closes)
    for i in range(len(closes)):
        window = r[i - stoch_n + 1:i + 1] if i - stoch_n + 1 >= 0 else []
        if not window or any(v is None for v in window):
            continue
        lo, hi = min(window), max(window)
        raw[i] = 50.0 if hi == lo else (r[i] - lo) / (hi - lo) * 100.0
    kk = sma(raw, k)
    dd = sma(kk, d)
    return kk, dd


def vwap_session(candles: Sequence[Candle], session_ms: int = 86_400_000) -> Series:
    """Volume-weighted average price, reset at every UTC session boundary."""
    out: Series = [None] * len(candles)
    pv = vol = 0.0
    session = None
    for i, c in enumerate(candles):
        s = c.open_time // session_ms
        if s != session:
            session, pv, vol = s, 0.0, 0.0
        typical = (c.high + c.low + c.close) / 3.0
        pv += typical * c.volume
        vol += c.volume
        out[i] = pv / vol if vol > 0 else typical
    return out


def median(vals: Sequence[float | None]) -> float | None:
    clean = [v for v in vals if v is not None]
    return _median(clean) if clean else None


def rolling_median(vals: Sequence[float | None], n: int) -> Series:
    out: Series = [None] * len(vals)
    for i in range(n - 1, len(vals)):
        window = [v for v in vals[i - n + 1:i + 1] if v is not None]
        if len(window) == n:
            out[i] = _median(window)
    return out


def pct_rank(vals: Sequence[float | None], window: int) -> Series:
    """Percentile rank (0..100) of each value within its trailing window (inclusive)."""
    out: Series = [None] * len(vals)
    for i in range(window - 1, len(vals)):
        w = vals[i - window + 1:i + 1]
        if any(v is None for v in w):
            continue
        cur = w[-1]
        out[i] = sum(1 for v in w if v <= cur) / window * 100.0
    return out


def log_returns(closes: Sequence[float], n: int) -> Series:
    out: Series = [None] * len(closes)
    for i in range(n, len(closes)):
        if closes[i - n] > 0 and closes[i] > 0:
            out[i] = math.log(closes[i] / closes[i - n])
    return out


def zscore(vals: Sequence[float | None], n: int) -> Series:
    out: Series = [None] * len(vals)
    for i in range(n - 1, len(vals)):
        w = [v for v in vals[i - n + 1:i + 1] if v is not None]
        if len(w) < n:
            continue
        m = sum(w) / n
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / n)
        out[i] = 0.0 if sd == 0 else (w[-1] - m) / sd
    return out


def last(series: Sequence[Any], offset: int = 0) -> Any:
    """Value `offset` bars back from the end, or None."""
    idx = len(series) - 1 - offset
    return None if idx < 0 else series[idx]


def crossed_above(a: Sequence[float | None], b: Sequence[float | None]) -> bool:
    """True when a crossed above b on the newest bar."""
    if len(a) < 2 or len(b) < 2:
        return False
    a0, a1, b0, b1 = a[-2], a[-1], b[-2], b[-1]
    if None in (a0, a1, b0, b1):
        return False
    return a0 <= b0 and a1 > b1


def crossed_below(a: Sequence[float | None], b: Sequence[float | None]) -> bool:
    if len(a) < 2 or len(b) < 2:
        return False
    a0, a1, b0, b1 = a[-2], a[-1], b[-2], b[-1]
    if None in (a0, a1, b0, b1):
        return False
    return a0 >= b0 and a1 < b1


# ---- registry used by IndicatorCache ------------------------------------------

def _closes(c: Sequence[Candle]) -> list[float]:
    return [x.close for x in c]


def _highs(c: Sequence[Candle]) -> list[float]:
    return [x.high for x in c]


def _lows(c: Sequence[Candle]) -> list[float]:
    return [x.low for x in c]


def _vols(c: Sequence[Candle]) -> list[float]:
    return [x.volume for x in c]


INDICATORS: dict[str, Callable[..., Any]] = {
    "ema": lambda c, n=21: ema(_closes(c), n),
    "sma": lambda c, n=20: sma(_closes(c), n),
    "rsi": lambda c, n=14: rsi(_closes(c), n),
    "atr": lambda c, n=14: atr(_highs(c), _lows(c), _closes(c), n),
    "atr_pct": lambda c, n=14: [None if a is None or not x.close else a / x.close * 100.0
                                for a, x in zip(atr(_highs(c), _lows(c), _closes(c), n), c)],
    "true_range": lambda c: true_range(_highs(c), _lows(c), _closes(c)),
    "bollinger": lambda c, n=20, k=2.0: bollinger(_closes(c), n, k),
    "donchian": lambda c, n=20: donchian(_highs(c), _lows(c), n),
    "supertrend": lambda c, n=10, mult=3.0: supertrend(_highs(c), _lows(c), _closes(c), n, mult),
    "macd": lambda c, fast=12, slow=26, signal=9: macd(_closes(c), fast, slow, signal),
    "stoch_rsi": lambda c, rsi_n=14, stoch_n=14, k=3, d=3: stoch_rsi(_closes(c), rsi_n, stoch_n, k, d),
    "vwap": lambda c, session_ms=86_400_000: vwap_session(c, session_ms),
    "vol_sma": lambda c, n=20: sma(_vols(c), n),
    "highest": lambda c, n=20: rolling_max(_highs(c), n),
    "lowest": lambda c, n=20: rolling_min(_lows(c), n),
    "close_highest": lambda c, n=20: rolling_max(_closes(c), n),
    "close_lowest": lambda c, n=20: rolling_min(_closes(c), n),
    "log_returns": lambda c, n=1: log_returns(_closes(c), n),
    "zscore_close": lambda c, n=20: zscore(_closes(c), n),
    "atr_median": lambda c, n=14, window=120: rolling_median(atr(_highs(c), _lows(c), _closes(c), n), window),
    "bb_width_rank": lambda c, n=20, k=2.0, window=100: pct_rank(bollinger(_closes(c), n, k)[3], window),
}


class IndicatorCache:
    """Memoises indicator series per (symbol, tf, name, params) until the candle set changes."""

    def __init__(self) -> None:
        self._store: dict[tuple, tuple[tuple, Any]] = {}

    def get(self, symbol: str, tf: str, name: str, candles: Sequence[Candle], **params: Any) -> Any:
        if name not in INDICATORS:
            raise KeyError(f"unknown indicator {name!r}; known: {sorted(INDICATORS)}")
        key = (symbol, tf, name, tuple(sorted(params.items())))
        stamp = (candles[-1].open_time if candles else None, len(candles),
                 candles[-1].close if candles else None)
        hit = self._store.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        result = INDICATORS[name](candles, **params)
        self._store[key] = (stamp, result)
        return result

    def invalidate(self, symbol: str | None = None, tf: str | None = None) -> None:
        if symbol is None:
            self._store.clear()
            return
        for key in [k for k in self._store if k[0] == symbol and (tf is None or k[1] == tf)]:
            del self._store[key]

    def __len__(self) -> int:
        return len(self._store)
