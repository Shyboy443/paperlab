"""Trading Desk indicators: plain functions over candle lists, no third-party dependency.

A candle here is a dict with t (open time, ms), o, h, l, c, v (base volume). Every function reads closed candles only;
the caller decides whether the last one is still forming.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

Candle = dict[str, float]


def ema(values: Sequence[float], n: int) -> list[float]:
    """Exponential moving average seeded with the first value (the same convention as the swing strategies)."""
    out: list[float] = []
    k = 2 / (n + 1)
    for v in values:
        out.append(v if not out else out[-1] + k * (v - out[-1]))
    return out


def rsi(closes: Sequence[float], n: int = 14) -> float | None:
    """Wilder's RSI of the last close; None without n + 1 closes."""
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[:n], closes[1:n + 1]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / n, losses / n
    for a, b in zip(closes[n:-1], closes[n + 1:]):
        d = b - a
        avg_g = (avg_g * (n - 1) + max(d, 0.0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0.0)) / n
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_g / avg_l)


def atr(candles: Sequence[Candle], n: int = 14) -> float | None:
    """Wilder's average true range of the last candle; None without n + 1 candles."""
    if len(candles) < n + 1:
        return None
    trs = [max(b["h"], a["c"]) - min(b["l"], a["c"]) for a, b in zip(candles[:-1], candles[1:])]
    val = sum(trs[:n]) / n
    for tr in trs[n:]:
        val = (val * (n - 1) + tr) / n
    return val


def relative_volume(candles: Sequence[Candle], n: int = 20) -> float | None:
    """The last candle's volume over the average of the n candles before it."""
    if len(candles) < n + 1:
        return None
    base = sum(c["v"] for c in candles[-n - 1:-1]) / n
    return candles[-1]["v"] / base if base > 0 else None


def swings(candles: Sequence[Candle], k: int = 2) -> tuple[list[float], list[float]]:
    """Fractal swing highs and lows: a high above the k candles on each side (lows mirrored). The last k candles can
    not be confirmed yet and are skipped."""
    highs, lows = [], []
    for i in range(k, len(candles) - k):
        win = candles[i - k:i + k + 1]
        if candles[i]["h"] == max(c["h"] for c in win):
            highs.append(candles[i]["h"])
        if candles[i]["l"] == min(c["l"] for c in win):
            lows.append(candles[i]["l"])
    return highs, lows


def nearest_levels(price: float, highs: Sequence[float], lows: Sequence[float], count: int = 2) -> dict[str, list[float]]:
    """The closest swing levels above (resistance) and below (support) the price."""
    levels = sorted(set(highs) | set(lows))
    above = [x for x in levels if x > price][:count]
    below = [x for x in levels if x < price][-count:][::-1]
    return {"resistance": above, "support": below}


def round_step(price: float) -> float:
    """The 'round number' spacing traders watch at this price: one digit below the leading one
    (80,954 -> 1,000; 2,419 -> 100; 152 -> 10; 2.41 -> 0.1)."""
    if price <= 0:
        return 0.0
    return 10 ** (math.floor(math.log10(price)) - 1)


def round_numbers(price: float, count: int = 2) -> dict[str, list[float]]:
    step = round_step(price)
    if not step:
        return {"above": [], "below": []}
    base = math.floor(price / step) * step
    below = [round(base - i * step, 10) for i in range(count) if base - i * step < price]
    above = [round(base + (i + 1) * step, 10) for i in range(count)]
    return {"above": above, "below": below}


def near_round(level: float, price: float, tol: float = 0.001) -> bool:
    """Is `level` within `tol` (fraction of price) of a round number?"""
    step = round_step(price)
    if not step:
        return False
    nearest = round(level / step) * step
    return abs(level - nearest) <= tol * price


def frame_summary(candles: Sequence[Candle]) -> dict[str, Any]:
    """Indicators of one timeframe (closed candles only)."""
    closes = [c["c"] for c in candles]
    if len(closes) < 51:
        return {"ok": False, "reason": f"only {len(closes)} candles"}
    e20, e50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
    trend = "up" if closes[-1] > e20 > e50 else "down" if closes[-1] < e20 < e50 else "mixed"
    hi, lo = swings(candles[-120:])
    r = rsi(closes)
    return {"ok": True, "close": closes[-1], "rsi14": r, "ema20": e20, "ema50": e50, "trend": trend,
            "atr14": atr(candles), "rel_volume": relative_volume(candles), "levels": nearest_levels(closes[-1], hi, lo, 4),
            "swing_highs": hi, "swing_lows": lo,
            "change_pct": 100 * (closes[-1] / closes[-2] - 1)}
