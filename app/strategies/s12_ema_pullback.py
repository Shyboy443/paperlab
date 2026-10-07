"""S12 EMA Pullback Continuation - "buy the EMA13 touch in a stacked trend"

Idea:       In a stacked 5m trend (EMA9 > EMA13 > EMA50) buy the bar that touches the EMA13 and closes bullish
            above it; the stop sits under the EMA50 so the trend must fully break to lose.
Timeframe:  5m.
Symbols:    all configured symbols.
Entry:      EMA9 > EMA13 > EMA50, bar low <= EMA13, bullish close above EMA13 -> long; mirror (EMA9 < EMA13 < EMA50,
            bar high >= EMA13, bearish close below EMA13) -> short.
Stop:       EMA50 - 0.2 x ATR(14) for longs (EMA50 + 0.2 x ATR for shorts).
Targets:    TP1 2R closes 40%; the remaining 60% rides a 1.2 x ATR(5m, 14) trail that arms after TP1 and is
            hard-capped at 6R. Break-even stop at 0.8R.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: the shallower EMA13 touch fires far more often than the EMA21 one, break-even arrives at 0.8R so
            scratches stop bleeding, and the runner is allowed all the way to 6R on 15x.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    ema_fast: int = P(9, min=3, max=50, step=1, label="fast EMA")
    ema_mid: int = P(13, min=5, max=100, step=1, label="pullback EMA")
    ema_slow: int = P(50, min=10, max=200, step=1, label="slow EMA", help="the stop sits beyond this EMA")
    stop_buffer_atr: float = P(0.2, min=0.0, max=1.0, step=0.05, label="stop buffer (ATR)")
    tp1_r: float = P(2.0, min=0.5, max=6.0, step=0.1, label="TP1 (R)")
    tp1_fraction: float = P(0.40, min=0.1, max=0.9, step=0.05, label="TP1 fraction")
    runner_r: float = P(6.0, min=1.0, max=12.0, step=0.1, label="runner cap (R)",
                        help="hard cap on the runner: closes the remainder if the trail has not already")
    trail_atr_mult: float = P(1.2, min=0.5, max=5.0, step=0.1, label="runner trail ATR mult")
    be_at_r: float = P(0.8, min=0.0, max=3.0, step=0.1, label="break-even at (R)",
                       help="move the stop to entry once the trade is this many R in profit")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")


class EmaPullbackContinuation(Strategy):
    id = "S12"
    name = "EMA Pullback Continuation"
    Params = Params
    doc = StrategyDoc(
        idea="Touch of the EMA13 with a bullish close in a stacked EMA9 > EMA13 > EMA50 5m trend.",
        timeframe="5m",
        symbols="all configured",
        entry="EMA9 > EMA13 > EMA50, low <= EMA13, bullish close above EMA13 -> long; mirror -> short",
        stop="EMA50 -/+ 0.2 x ATR(14)",
        targets="TP1 2R closes 40%, 1.2 x ATR trail on the rest after TP1, hard cap 6R; break-even at 0.8R",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="shallower EMA13 touch fires far more often, break-even at 0.8R, runner allowed to 6R",
    )
    timeframes = ("5m",)
    contributes_votes = True
    warmup_bars = 80
    min_rr = 2.5

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, int] = {"long": 0, "short": 0}

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "5m")
        if len(candles) < max(p.ema_slow, p.atr_period) + 3:
            return []
        fast = ctx.ind_last(c.symbol, "5m", "ema", n=p.ema_fast)
        mid = ctx.ind_last(c.symbol, "5m", "ema", n=p.ema_mid)
        slow = ctx.ind_last(c.symbol, "5m", "ema", n=p.ema_slow)
        atr = ctx.ind_last(c.symbol, "5m", "atr", n=p.atr_period)
        if fast is None or mid is None or slow is None or atr is None or atr <= 0:
            return []
        trend = "up" if fast > mid > slow else "down" if fast < mid < slow else None
        side: str | None = None
        if trend == "up" and c.low <= mid and c.is_bull and c.close > mid:
            side = "long"
        elif trend == "down" and c.high >= mid and c.is_bear and c.close < mid:
            side = "short"
        self._last[c.symbol] = {"ts": c.close_time, "trend": trend, "ema_fast": fast, "ema_mid": mid,
                                "ema_slow": slow, "touch": side is not None}
        if side is None:
            return []
        price = ctx.last_price(c.symbol) or c.close
        stop = slow - p.stop_buffer_atr * atr if side == "long" else slow + p.stop_buffer_atr * atr
        self._counters[side] += 1
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="5m", price=price, stop=stop,
            tps_r=[(p.tp1_r, p.tp1_fraction), (p.runner_r, 1.0 - p.tp1_fraction)],
            trail=TrailSpec("atr", "5m", p.trail_atr_mult, p.atr_period, activate_after_tp1=True),
            be_at_r=p.be_at_r, valid_bars=2, reason=f"EMA{p.ema_mid} pullback {side} in stacked {trend}trend",
            meta={"ema_fast": fast, "ema_mid": mid, "ema_slow": slow, "atr": atr})]

    def state(self) -> dict[str, Any]:
        return {"last": self._last, "counters": dict(self._counters)}

    def reset(self) -> None:
        self._last.clear()
        self._counters = {"long": 0, "short": 0}
