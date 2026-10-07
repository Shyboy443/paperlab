"""Shared machinery for V3 intraday strategies (docs/V3_PROTOCOL.md).

A V3 family is bound to ONE signal timeframe per bot by `for_timeframe(tf)`, with two context
timeframes and the 1m execution tape:

    signal   3m / 5m / 15m (primary), 30m (benchmark), 1m (experimental)
    context  3m, 5m -> 15m + 1h;  15m, 30m -> 1h + 4h;  1m -> 5m + 15m
    tape     1m, always subscribed (execution and Jev's fast-market features)

Rules every V3 entry follows:

* it is decided on a CLOSED signal bar and fills later (the engine's latency lands it on the next
  1m bar's open) -- nothing is read from a bar that has not closed;
* the stop is structural, then clamped to at least MIN_STOP_PCT (the frozen fee gate refuses any
  trade whose round trip exceeds 25% of R: 0.44% at Bybit's taker fee) and refused beyond
  MAX_STOP_PCT (a wider structure is not an intraday trade);
* `expected_move_pct` is the move the entry's first objective needs, bounded by the family's own
  volatility structure -- never a constant -- and `signal_quality` is the mean of named, bounded
  factors, both kept on the signal's meta with everything Jev V2 reads about the setup.
"""
from __future__ import annotations

from typing import Any, ClassVar, Sequence

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec, tf_ms
from app.strategies.base import MarketContext, Strategy

CONTEXT: dict[str, tuple[str, str]] = {"1m": ("5m", "15m"), "3m": ("15m", "1h"), "5m": ("15m", "1h"),
                                       "15m": ("1h", "4h"), "30m": ("1h", "4h")}
V3_TIMEFRAMES: tuple[str, ...] = ("1m", "3m", "5m", "15m", "30m")
PRIMARY_TIMEFRAMES: tuple[str, ...] = ("3m", "5m", "15m")
BENCHMARK_TIMEFRAMES: tuple[str, ...] = ("30m",)
EXPERIMENTAL_TIMEFRAMES: tuple[str, ...] = ("1m",)
MIN_STOP_PCT = 0.005
MAX_STOP_PCT = 0.020


def clamp01(x: float | None) -> float:
    if x is None:
        return 0.0
    return max(0.0, min(1.0, float(x)))


def scale(x: float | None, lo: float, hi: float) -> float:
    """Linear map of x from [lo, hi] onto [0, 1], clamped."""
    if x is None or hi == lo:
        return 0.0
    return clamp01((x - lo) / (hi - lo))


def close_location(c: Candle) -> float:
    """Where the bar closed inside its range: 0 = at the low, 1 = at the high."""
    return (c.close - c.low) / (c.high - c.low) if c.high > c.low else 0.5


class V3Strategy(Strategy):
    version: ClassVar[str] = "v3"
    family: ClassVar[str] = ""
    hypothesis: ClassVar[str] = ""
    supported_timeframes: ClassVar[tuple[str, ...]] = V3_TIMEFRAMES
    signal_tf: ClassVar[str] = ""
    ctx_fast: ClassVar[str] = ""
    ctx_slow: ClassVar[str] = ""
    context_tf: ClassVar[str] = ""
    timeframes: ClassVar[tuple[str, ...]] = V3_TIMEFRAMES + ("1h", "4h")
    min_rr = 0.0
    default_leverage = 20
    max_positions = 1
    warmup_bars = 150
    max_hold_bars: ClassVar[int] = 48

    @classmethod
    def for_timeframe(cls, tf: str) -> type["V3Strategy"]:
        if tf not in V3_TIMEFRAMES:
            raise ValueError(f"{cls.__name__} does not support {tf}")
        fast, slow = CONTEXT[tf]
        subs = tuple(dict.fromkeys((tf, fast, slow, "1m")))
        return type(f"{cls.__name__}_{tf}", (cls,), {
            "signal_tf": tf, "ctx_fast": fast, "ctx_slow": slow, "context_tf": slow,
            "native_timeframe": tf, "supported_timeframes": (tf,), "timeframes": subs,
            "__module__": cls.__module__})

    def cooldown_ms(self) -> int:
        return int(getattr(self.params, "cooldown_bars", 0) or 0) * tf_ms(self.signal_tf or "1m")

    # -- helpers --------------------------------------------------------------------------------
    def bound(self, c: Candle) -> bool:
        return bool(self.signal_tf) and c.tf == self.signal_tf

    @staticmethod
    def series(ctx: MarketContext, symbol: str, tf: str) -> list[Candle]:
        return list(ctx.candles(symbol, tf))

    def trend(self, ctx: MarketContext, symbol: str, tf: str, fast: int = 20, slow: int = 50,
              slope_bars: int = 5) -> dict[str, Any] | None:
        """EMA trend of a context series: direction, spread and slope as fractions of price."""
        cs = ctx.candles(symbol, tf)
        if len(cs) < slow + slope_bars + 2:
            return None
        f_s = ctx.ind(symbol, tf, "ema", n=fast)
        s_s = ctx.ind(symbol, tf, "ema", n=slow)
        f, s, s_then = last(f_s), last(s_s), last(s_s, slope_bars)
        close = cs[-1].close
        if None in (f, s, s_then) or not s_then or not close:
            return None
        spread = (f - s) / close
        slope = (s - s_then) / s_then
        direction = ("up" if (f > s and slope > 0 and close > s) else
                     "down" if (f < s and slope < 0 and close < s) else "flat")
        return {"direction": direction, "spread": spread, "slope": slope, "close_vs_slow": close / s - 1.0}

    def atr(self, ctx: MarketContext, symbol: str, tf: str, n: int = 14, offset: int = 0) -> float | None:
        v = last(ctx.ind(symbol, tf, "atr", n=n), offset)
        return float(v) if v else None

    @staticmethod
    def volume_ratio(candles: Sequence[Candle], n: int = 20) -> float | None:
        prev = [c.volume for c in candles[-n - 1:-1]]
        if len(prev) < n or sum(prev) <= 0:
            return None
        return candles[-1].volume / (sum(prev) / len(prev))

    @staticmethod
    def trades_ratio(candles: Sequence[Candle], n: int = 20) -> float | None:
        prev = [c.trades for c in candles[-n - 1:-1]]
        if len(prev) < n or sum(prev) <= 0:
            return None
        return candles[-1].trades / (sum(prev) / len(prev))

    @staticmethod
    def stop_pct(price: float, raw_distance: float) -> float | None:
        """Structural stop distance as a fraction of price, clamped to the V3 band; None = too wide."""
        if price <= 0 or raw_distance <= 0:
            return None
        pct = raw_distance / price
        if pct > MAX_STOP_PCT:
            return None
        return max(pct, MIN_STOP_PCT)

    def entry(self, *, c: Candle, ctx: MarketContext, side: str, price: float, stop_pct: float,
              tps_r: Sequence[tuple[float, float]] = (), tps_price: Sequence[tuple[float, float]] = (),
              trail_atr: float | None = None, be_at_r: float | None = None, expected_move_pct: float,
              expected_move_source: str, factors: dict[str, float], reason: str,
              extra: dict[str, Any] | None = None) -> Signal:
        dist = price * stop_pct
        stop = price - dist if side == "long" else price + dist
        first = (tps_price[0][0] if tps_price else
                 price + (dist * tps_r[0][0] if side == "long" else -dist * tps_r[0][0]) if tps_r else None)
        target_pct = abs(first - price) / price if first else None
        quality = sum(factors.values()) / len(factors) if factors else 0.0
        trail = TrailSpec("atr", self.signal_tf, trail_atr, 14, True) if trail_atr else None
        meta = {"family": self.family, "signal_quality": round(quality, 4),
                "quality_factors": {k: round(v, 3) for k, v in factors.items()},
                "expected_move_pct": round(max(0.0, expected_move_pct), 6),
                "expected_move_source": expected_move_source,
                "stop_pct": round(stop_pct, 6), "target_pct": round(target_pct, 6) if target_pct else None,
                "reward_risk": round(target_pct / stop_pct, 3) if target_pct else None,
                "signal_tf": self.signal_tf, "context_tfs": [self.ctx_fast, self.ctx_slow],
                "execution_tf": "1m", **(extra or {})}
        return self.make_entry(symbol=c.symbol, side=side, ts=c.close_time, tf=self.signal_tf,
                               price=price, stop=stop, tps_r=tps_r, tps_price=tps_price, trail=trail,
                               be_at_r=be_at_r, max_hold_s=int(self.max_hold_bars * tf_ms(self.signal_tf) / 1000),
                               reason=reason[:120], meta=meta)
