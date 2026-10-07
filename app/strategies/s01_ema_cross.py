"""S01 EMA Cross Momentum - "fast trend attack"

Idea:       Trade every EMA 5 / EMA 13 cross on the 1-minute chart in the direction of the cross.
Timeframe:  1m (15m EMA 50 direction filter ON by default - stop fading every 1m cross).
Symbols:    all configured symbols.
Entry:      long when EMA5 crosses above EMA13 and the bar closes above EMA13; short on the mirror image.
Stop:       1.0 x ATR(14) beyond the opposite side of the cross candle (below its low for longs).
Targets:    TP1 at 2.0R closes 40%; the runner trails 1.2 x ATR(1m, 14) behind the best price after TP1,
            with the stop moved to break-even at 0.8R.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage (first boot: size_mult 0.5).
Why aggressive: 15x, faster 5/13 EMAs fire far more often, tight 1.0 ATR stop, early break-even and a
            genuine runner instead of a full exit at TP.

This file is the reference shape every other strategy module follows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import crossed_above, crossed_below, last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    fast: int = P(5, min=3, max=50, step=1, label="fast EMA")
    slow: int = P(13, min=5, max=120, step=1, label="slow EMA")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")
    stop_atr_mult: float = P(1.0, min=0.3, max=4.0, step=0.1, label="stop ATR mult",
                             help="stop distance beyond the cross candle")
    tp1_r: float = P(2.0, min=1.0, max=6.0, step=0.1, label="TP1 (R)")
    tp1_fraction: float = P(0.40, min=0.1, max=1.0, step=0.05, label="TP1 fraction",
                            help="fraction closed at TP1; the remainder rides the ATR runner")
    trail_atr_mult: float = P(1.2, min=0.5, max=5.0, step=0.1, label="runner trail ATR mult",
                              help="runner trails this x ATR behind the best price after TP1")
    be_at_r: float = P(0.8, min=0.2, max=5.0, step=0.1, label="break-even at (R)",
                       help="move the stop to break-even once the trade is this many R in profit")
    htf_ema50_filter: bool = P(True, label="15m EMA50 filter", help="only trade in the 15m EMA50 direction")
    htf_ema_period: int = P(50, min=10, max=200, step=1, label="15m EMA period")


class EmaCrossMomentum(Strategy):
    id = "S01"
    name = "EMA Cross Momentum"
    Params = Params
    doc = StrategyDoc(
        idea="Fast trend attack: trade every EMA5/EMA13 cross on the 1m chart with the 15m trend.",
        timeframe="1m (15m EMA50 filter on by default)",
        symbols="all configured",
        entry="EMA5 crosses above EMA13 and close > EMA13 -> long; EMA5 crosses below EMA13 and close < EMA13 -> short",
        stop="1.0 x ATR(14) beyond the opposite side of the cross candle",
        targets="TP1 2.0R closes 40%, runner trails 1.2 x ATR after TP1, break-even at 0.8R",
        sizing="2% wallet risk per trade at 15x virtual leverage; first boot size_mult 0.5",
        why_aggressive="15x, 5/13 EMAs fire often, tight stop, early break-even and a real runner",
    )
    timeframes = ("1m", "15m")
    contributes_votes = True
    warmup_bars = 60
    # TP1 is the only hard target (the remainder trails), so Signal.rr() reports the TP1 multiple: the floor has
    # to sit at or below tp1_r or every signal would be rejected as rr_below_min.
    min_rr = 2.0

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last_cross: dict[str, dict[str, Any]] = {}

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "1m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "1m")
        if len(candles) < max(p.slow, p.atr_period) + 3:
            return []
        fast = ctx.ind(c.symbol, "1m", "ema", n=p.fast)
        slow = ctx.ind(c.symbol, "1m", "ema", n=p.slow)
        atr = ctx.ind_last(c.symbol, "1m", "atr", n=p.atr_period)
        slow_now = last(slow)
        if atr is None or slow_now is None or atr <= 0:
            return []
        side: str | None = None
        if crossed_above(fast, slow) and c.close > slow_now:
            side = "long"
        elif crossed_below(fast, slow) and c.close < slow_now:
            side = "short"
        if side is None:
            return []
        if p.htf_ema50_filter:
            htf = ctx.ind_last(c.symbol, "15m", "ema", n=p.htf_ema_period)
            if htf is None or (side == "long" and c.close <= htf) or (side == "short" and c.close >= htf):
                self._last_cross[c.symbol] = {"ts": c.close_time, "side": side, "filtered": True}
                return []
        price = ctx.last_price(c.symbol) or c.close
        stop = c.low - p.stop_atr_mult * atr if side == "long" else c.high + p.stop_atr_mult * atr
        self._last_cross[c.symbol] = {"ts": c.close_time, "side": side, "filtered": False}
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="1m", price=price, stop=stop,
            tps_r=[(p.tp1_r, p.tp1_fraction)], be_at_r=p.be_at_r,
            trail=TrailSpec("atr", "1m", p.trail_atr_mult, p.atr_period, activate_after_tp1=True),
            valid_bars=2, reason=f"EMA{p.fast}/{p.slow} cross {side}",
            meta={"fast": last(fast), "slow": slow_now, "atr": atr})]

    def state(self) -> dict[str, Any]:
        return {"last_cross": self._last_cross}

    def reset(self) -> None:
        self._last_cross.clear()
