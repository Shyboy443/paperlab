"""Shared machinery for v2 strategies: the timeframe binding, trend context, and honest quality.

`for_timeframe(tf)` returns a subclass bound to one signal timeframe. The arena calls it per bot, so
S04v2 on 15m and S04v2 on 30m are separate classes with their own native timeframe, and the replay
subscribes each to exactly the series it reads (signal timeframe + its context timeframe).
"""
from __future__ import annotations

from typing import Any, ClassVar, Sequence

from app.core.indicators import last
from app.core.types import Candle
from app.strategies.base import MarketContext, Strategy

# Trend context for each signal timeframe: roughly 4-12x slower, and always a series the replay can
# aggregate from 1m bars.
CONTEXT_TF: dict[str, str] = {"5m": "1h", "15m": "1h", "30m": "4h"}
V2_TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "30m")


def clamp01(x: float | None) -> float:
    if x is None:
        return 0.0
    return max(0.0, min(1.0, float(x)))


def scale(x: float | None, lo: float, hi: float) -> float:
    """Linear map of x from [lo, hi] onto [0, 1], clamped."""
    if x is None or hi == lo:
        return 0.0
    return clamp01((x - lo) / (hi - lo))


class V2Strategy(Strategy):
    version: ClassVar[str] = "v2"
    supported_timeframes: ClassVar[tuple[str, ...]] = V2_TIMEFRAMES
    signal_tf: ClassVar[str] = ""          # set by for_timeframe(); empty on the unbound family class
    context_tf: ClassVar[str] = ""
    timeframes: ClassVar[tuple[str, ...]] = V2_TIMEFRAMES + ("1h", "4h")
    min_rr = 0.0                           # exits are structural (partial + trail), not one fixed target
    default_leverage = 20                  # a ceiling; the arena fits the leverage each position needs

    @classmethod
    def for_timeframe(cls, tf: str) -> type["V2Strategy"]:
        if tf not in V2_TIMEFRAMES:
            raise ValueError(f"{cls.__name__} does not support {tf}")
        ctx_tf = CONTEXT_TF[tf]
        return type(f"{cls.__name__}_{tf}", (cls,), {
            "signal_tf": tf, "context_tf": ctx_tf, "native_timeframe": tf,
            "supported_timeframes": (tf,), "timeframes": (tf, ctx_tf), "__module__": cls.__module__})

    # -- helpers --------------------------------------------------------------------------------
    def bound(self, c: Candle) -> bool:
        """Only the bot's own signal timeframe triggers anything."""
        return bool(self.signal_tf) and c.tf == self.signal_tf

    def trend(self, ctx: MarketContext, symbol: str, fast: int = 20, slow: int = 50,
              slope_bars: int = 5) -> dict[str, Any] | None:
        """Context-timeframe trend: direction, EMA spread and slope, both as fractions of price."""
        htf = self.context_tf
        if len(ctx.candles(symbol, htf)) < slow + slope_bars + 2:
            return None
        f_s = ctx.ind(symbol, htf, "ema", n=fast)
        s_s = ctx.ind(symbol, htf, "ema", n=slow)
        f, s, s_then = last(f_s), last(s_s), last(s_s, slope_bars)
        close = ctx.candles(symbol, htf)[-1].close
        if None in (f, s, s_then) or not s_then or not close:
            return None
        spread = (f - s) / close
        slope = (s - s_then) / s_then
        direction = "up" if (f > s and slope > 0 and close > s) else "down" if (f < s and slope < 0 and close < s) else "flat"
        return {"direction": direction, "spread": spread, "slope": slope}

    @staticmethod
    def volume_ratio(candles: Sequence[Candle], n: int = 20) -> float | None:
        prev = [c.volume for c in candles[-n - 1:-1]]
        if len(prev) < n or sum(prev) <= 0:
            return None
        return candles[-1].volume / (sum(prev) / len(prev))

    @staticmethod
    def expected_move(price: float, stop: float, first_objective_r: float, atr: float,
                      atr_cap: float = 1.5) -> float:
        """The move this entry's first objective needs, capped at what the market typically makes:
        min(first objective distance, atr_cap x ATR), as a fraction of price. Conservative on
        purpose -- it is never larger than the target the strategy actually set."""
        target = abs(price - stop) * first_objective_r
        return min(target, atr_cap * atr) / price if price else 0.0
