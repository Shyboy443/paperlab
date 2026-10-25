"""The six TradingView strategies behind the ZEC bots, ported line by line from their published Pine sources
(fmzquant/strategies @ 7853bb2b; docs/ZEC_BOTS_STUDY.md quotes each rule).

Every function takes completed candles (t, o, h, l, c) and returns (buy, sell): one boolean per candle meaning "the
source called strategy.entry long / short at that candle's close". All six sources only ever call strategy.entry, so a
position is held until the opposite signal reverses it (no take-profit, no stop in the sources themselves).

Pine semantics kept: ta.ema / ta.rma are seeded with the simple average of their first `length` values; RSI is Wilder's
(rma of gains / losses, 100 when there are no losses); CCI divides by 0.015 x the mean absolute deviation; comparisons
with na are false; crossover(a, b) = a > b now and a <= b on the previous candle.
"""
from __future__ import annotations

from typing import Callable, Sequence

from app.macd.model import ema

Series = list  # list[float | None]


def sma(x: Sequence[float | None], n: int) -> Series:
    out: Series = [None] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        if None not in w:
            out[i] = sum(w) / n
    return out


def rma(x: Sequence[float | None], n: int) -> Series:
    """Pine ta.rma: alpha 1/n, seeded with the simple average of the first n available values."""
    out: Series = []
    prev, seen = None, []
    for v in x:
        if v is None:
            out.append(None)
            continue
        if prev is None:
            seen.append(v)
            if len(seen) == n:
                prev = sum(seen) / n
            out.append(prev)
            continue
        prev = (v + (n - 1) * prev) / n
        out.append(prev)
    return out


def rsi(x: Sequence[float], n: int) -> Series:
    up = [None] + [max(x[i] - x[i - 1], 0.0) for i in range(1, len(x))]
    dn = [None] + [max(x[i - 1] - x[i], 0.0) for i in range(1, len(x))]
    u, d = rma(up, n), rma(dn, n)
    return [None if a is None or b is None else 100.0 if b == 0 else 0.0 if a == 0 else 100 - 100 / (1 + a / b)
            for a, b in zip(u, d)]


def cci(x: Sequence[float], n: int) -> Series:
    m = sma(x, n)
    out: Series = [None] * len(x)
    for i in range(n - 1, len(x)):
        if m[i] is None:
            continue
        dev = sum(abs(v - m[i]) for v in x[i - n + 1:i + 1]) / n
        out[i] = (x[i] - m[i]) / (0.015 * dev) if dev > 0 else None
    return out


def highest(x: Sequence[float], n: int) -> Series:
    return [max(x[i - n + 1:i + 1]) if i >= n - 1 else None for i in range(len(x))]


def lowest(x: Sequence[float], n: int) -> Series:
    return [min(x[i - n + 1:i + 1]) if i >= n - 1 else None for i in range(len(x))]


def true_range(bars: Sequence[dict]) -> Series:
    """Pine v4 `tr` (= tr(false)): na on the first candle."""
    return [None] + [max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])) for p, b in zip(bars, bars[1:])]


def _gt(a, b) -> bool:
    return a is not None and b is not None and a > b


def _lt(a, b) -> bool:
    return a is not None and b is not None and a < b


def crossover(a: Series, b: Series, i: int) -> bool:
    return i > 0 and _gt(a[i], b[i]) and a[i - 1] is not None and b[i - 1] is not None and a[i - 1] <= b[i - 1]


def crossunder(a: Series, b: Series, i: int) -> bool:
    return i > 0 and _lt(a[i], b[i]) and a[i - 1] is not None and b[i - 1] is not None and a[i - 1] >= b[i - 1]


# -- the six sources ---------------------------------------------------------------------------------------------------------
def ema_rsi(bars):
    """EMA & RSI Strategy with 200 EMA (fitradn): EMA4 crosses EMA12, RSI14 below 70 / above 30, close vs EMA48."""
    c = [b["c"] for b in bars]
    f, s, t, r = ema(c, 4), ema(c, 12), ema(c, 48), rsi(c, 14)
    buy = [crossover(f, s, i) and _lt(r[i], 70) and _gt(c[i], t[i]) for i in range(len(c))]
    sell = [crossunder(f, s, i) and _gt(r[i], 30) and _lt(c[i], t[i]) for i in range(len(c))]
    return buy, sell


def momentum_2cci(bars):
    """Momentum Explosion 2CCI RSI (capam): EMA8 crosses SMA26, mom(SMA26, 5), CCI34 and CCI55 vs 0, RSI26 vs 48."""
    c = [b["c"] for b in bars]
    e8, s26, c34, c55, r26 = ema(c, 8), sma(c, 26), cci(c, 34), cci(c, 55), rsi(c, 26)
    mom = [None if i < 5 or s26[i] is None or s26[i - 5] is None else s26[i] - s26[i - 5] for i in range(len(c))]
    buy = [crossover(e8, s26, i) and _gt(mom[i], 0) and _gt(c34[i], 0) and _gt(c55[i], 0) and _gt(r26[i], 48)
           for i in range(len(c))]
    sell = [crossunder(e8, s26, i) and _lt(mom[i], 0) and _lt(c34[i], 0) and _lt(c55[i], 0) and _lt(r26[i], 48)
            for i in range(len(c))]
    return buy, sell


def ema_crossover(bars):
    """EMA Crossover Strategy (SDTA): long while the close is above EMA50, EMA144 and EMA200; short while below all."""
    c = [b["c"] for b in bars]
    a, b_, d = ema(c, 50), ema(c, 144), ema(c, 200)
    buy = [_gt(c[i], a[i]) and _gt(c[i], b_[i]) and _gt(c[i], d[i]) for i in range(len(c))]
    sell = [_lt(c[i], d[i]) and _lt(c[i], b_[i]) and _lt(c[i], a[i]) for i in range(len(c))]
    return buy, sell


def ma_crossover(bars):
    """MA crossover strategy (ccrockatt21700), defaults: SMA20 crosses SMA200, LongShort."""
    c = [b["c"] for b in bars]
    fast, slow = sma(c, 20), sma(c, 200)
    return [crossover(fast, slow, i) for i in range(len(c))], [crossunder(fast, slow, i) for i in range(len(c))]


def scalp_slayer(bars):
    """Scalp Slayer (I) (Creativ3mindz), defaults: three growing same-colour candles, range above 1.5 x EMA50 of the
    range, close vs EMA50. The source's TP/SL lines only draw labels; its orders are the two strategy.entry calls."""
    o, h, l, c = ([b[k] for b in bars] for k in "ohlc")
    rng = [hi - lo for hi, lo in zip(h, l)]
    er = ema(rng, 50)
    trend = ema(c, 50)
    buy, sell = [False] * len(c), [False] * len(c)
    for i in range(2, len(c)):
        if er[i] is None or trend[i] is None:
            continue
        body = [abs(c[i - k] - o[i - k]) for k in (2, 1, 0)]
        grows = body[0] > body[1] and body[2] > body[1]
        wide = rng[i] > 1.5 * er[i]
        b = (c[i] > trend[i] and all(c[i - k] > o[i - k] for k in (2, 1, 0)) and grows and c[i] > c[i - 1] > c[i - 2] and wide)
        s = (c[i] < trend[i] and all(c[i - k] < o[i - k] for k in (2, 1, 0)) and grows and c[i] < c[i - 1] < c[i - 2] and wide)
        buy[i], sell[i] = b, (s and not b)
    return buy, sell


def wilder_trailing(bars):
    """Wilder's Volatility Trailing Stop (SparkyFlary), defaults: ATR = RMA7 of the true range, multiplier 3, trail on
    closes. TS = upperTrail[1] when the close is below TS[1], lowerTrail[1] when above (0 otherwise, as the source's
    non-`var` declaration); long on close crossing above TS, short on crossing below."""
    c = [b["c"] for b in bars]
    atr = rma(true_range(bars), 7)
    lo7, hi7 = lowest(c, 7), highest(c, 7)
    up = [None if a is None or x is None else x + 3 * a for x, a in zip(lo7, atr)]
    dn = [None if a is None or x is None else x - 3 * a for x, a in zip(hi7, atr)]
    ts: Series = []
    for i in range(len(c)):
        prev = ts[i - 1] if i else None
        if _lt(c[i], prev):
            ts.append(up[i - 1])
        elif _gt(c[i], prev):
            ts.append(dn[i - 1])
        else:
            ts.append(0.0)
    return [crossover(c, ts, i) for i in range(len(c))], [crossunder(c, ts, i) for i in range(len(c))]


H1, H4 = 3_600_000, 14_400_000

# key -> (display name, function, candle length, interval, source author)
RULES: dict[str, tuple[str, Callable, int, str, str]] = {
    "emarsi": ("EMA & RSI", ema_rsi, H4, "4h", "fitradn"),
    "momentum": ("Momentum Explosion 2CCI RSI", momentum_2cci, H4, "4h", "capam"),
    "emacross": ("EMA Crossover", ema_crossover, H1, "1h", "SDTA"),
    "macross": ("MA Crossover", ma_crossover, H1, "1h", "ccrockatt21700"),
    "scalpslayer": ("Scalp Slayer", scalp_slayer, H4, "4h", "Creativ3mindz"),
    "wilder": ("Wilder Volatility Trailing Stop", wilder_trailing, H4, "4h", "SparkyFlary"),
}


def to_sig(buy: Sequence[bool], sell: Sequence[bool]) -> list[int]:
    """+1 long / -1 short / 0 nothing per candle (a candle with both requests keeps the source's order: long first)."""
    return [1 if b else -1 if s else 0 for b, s in zip(buy, sell)]
