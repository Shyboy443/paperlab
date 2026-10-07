"""S14 StochRSI + Trend Gate - "first cross out of washed-out, with the 15m trend"

Idea:       Enter the first 5m StochRSI K/D cross out of a washed-out reading, only in the direction of the 15m
            EMA20/EMA50 trend.
Timeframe:  5m signals, 15m trend gate.
Symbols:    all configured symbols.
Entry:      15m EMA20 > EMA50 and K crosses above D with the previous K < 20 -> long;
            15m EMA20 < EMA50 and K crosses below D with the previous K > 80 -> short.
Stop:       1.0 x ATR(14) from the entry price.
Targets:    3.5R full size.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: the 20/80 bands (was 15/85) fire on a tape that rarely reaches 15/85, and it takes the first
            cross out of washed-out, not the second.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import crossed_above, crossed_below, last
from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    rsi_period: int = P(14, min=5, max=50, step=1, label="RSI period")
    stoch_period: int = P(14, min=5, max=50, step=1, label="stochastic period")
    k: int = P(3, min=1, max=10, step=1, label="K smoothing")
    d: int = P(3, min=1, max=10, step=1, label="D smoothing")
    oversold: float = P(20.0, min=1.0, max=40.0, step=1.0, label="washed-out K (long)",
                        help="previous K must be below this for a long")
    overbought: float = P(80.0, min=60.0, max=99.0, step=1.0, label="washed-out K (short)",
                          help="previous K must be above this for a short")
    htf_ema_fast: int = P(20, min=5, max=100, step=1, label="15m fast EMA")
    htf_ema_slow: int = P(50, min=10, max=200, step=1, label="15m slow EMA")
    stop_atr_mult: float = P(1.0, min=0.3, max=4.0, step=0.1, label="stop ATR mult")
    tp_r: float = P(3.5, min=1.0, max=8.0, step=0.1, label="TP (R)")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")


class StochRsiTrendGate(Strategy):
    id = "S14"
    name = "StochRSI + Trend Gate"
    Params = Params
    doc = StrategyDoc(
        idea="First 5m StochRSI K/D cross out of a washed-out reading, gated by the 15m EMA20/EMA50 trend.",
        timeframe="5m signals, 15m gate",
        symbols="all configured",
        entry="15m EMA20 > EMA50 and K crosses above D from K < 20 -> long; 15m EMA20 < EMA50 and K crosses below D "
              "from K > 80 -> short",
        stop="1.0 x ATR(14) from entry",
        targets="3.5R full size",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="20/80 washed-out bands (was 15/85) so the cross actually fires on this tape, and it takes "
                       "the first cross, not the second",
    )
    timeframes = ("5m", "15m")
    contributes_votes = False
    warmup_bars = 60
    min_rr = 2.5

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, int] = {"long": 0, "short": 0, "gated": 0}

    def _htf_trend(self, symbol: str, ctx: MarketContext) -> str | None:
        p = self.params
        htf = ctx.candles(symbol, "15m")
        if len(htf) < max(p.htf_ema_fast, p.htf_ema_slow) + 2:
            return None
        fast = ctx.ind_last(symbol, "15m", "ema", n=p.htf_ema_fast)
        slow = ctx.ind_last(symbol, "15m", "ema", n=p.htf_ema_slow)
        if fast is None or slow is None:
            return None
        return "up" if fast > slow else "down" if fast < slow else None

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "5m")
        if len(candles) < p.rsi_period + p.stoch_period + p.k + p.d + 2:
            return []
        k_series, d_series = ctx.ind(c.symbol, "5m", "stoch_rsi", rsi_n=p.rsi_period, stoch_n=p.stoch_period,
                                     k=p.k, d=p.d)
        atr = ctx.ind_last(c.symbol, "5m", "atr", n=p.atr_period)
        k_prev = last(k_series, 1)
        if k_prev is None or last(d_series) is None or atr is None or atr <= 0:
            return []
        trend = self._htf_trend(c.symbol, ctx)
        cross: str | None = None
        if crossed_above(k_series, d_series) and k_prev < p.oversold:
            cross = "long"
        elif crossed_below(k_series, d_series) and k_prev > p.overbought:
            cross = "short"
        self._last[c.symbol] = {"ts": c.close_time, "k": last(k_series), "d": last(d_series), "k_prev": k_prev,
                                "htf_trend": trend, "cross": cross}
        if cross is None:
            return []
        if (cross == "long" and trend != "up") or (cross == "short" and trend != "down"):
            self._counters["gated"] += 1
            return []
        price = ctx.last_price(c.symbol) or c.close
        stop = price - p.stop_atr_mult * atr if cross == "long" else price + p.stop_atr_mult * atr
        self._counters[cross] += 1
        return [self.make_entry(
            symbol=c.symbol, side=cross, ts=c.close_time, tf="5m", price=price, stop=stop,
            tps_r=[(p.tp_r, 1.0)], valid_bars=2,
            reason=f"StochRSI K/D cross {cross} from K={k_prev:.1f}, 15m trend {trend}",
            meta={"k": last(k_series), "d": last(d_series), "k_prev": k_prev, "atr": atr, "htf_trend": trend})]

    def state(self) -> dict[str, Any]:
        return {"last": self._last, "counters": dict(self._counters)}

    def reset(self) -> None:
        self._last.clear()
        self._counters = {"long": 0, "short": 0, "gated": 0}
