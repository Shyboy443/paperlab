"""S03 Bollinger Squeeze Break - "first candle out of the squeeze"

Idea:       Trade the first expansion candle out of a Bollinger squeeze on the 5-minute chart.
Timeframe:  5m.
Symbols:    all configured symbols.
Entry:      the PRIOR bar's bandwidth ((upper - lower) / middle of BB 20/2) is the lowest of the last 12
            bandwidths, and this bar closes outside the squeeze band (close > upper -> long, close < lower ->
            short) on volume > 1.2 x the 20-bar volume SMA of the prior bars.
Stop:       the opposite squeeze band (long stop = lower band, short stop = upper band).
Targets:    TP1 at 2.0R closes 40%; the remainder rides a 1.2 x ATR(5m, 14) runner armed after TP1, with the
            stop at break-even from 0.8R. The squeeze height is kept in the signal meta for the journal.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: a 12-bar squeeze lookback and a 1.2x volume gate actually fire on thin testnet tape; the
            very first expansion candle is traded without confirmation at high leverage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    bb_period: int = P(20, min=5, max=100, step=1, label="BB period", help="also the volume SMA length")
    bb_k: float = P(2.0, min=0.5, max=4.0, step=0.1, label="BB std mult")
    squeeze_bars: int = P(12, min=5, max=200, step=1, label="squeeze lookback (bars)",
                          help="the prior bar's bandwidth must be the lowest of this many bandwidths")
    volume_mult: float = P(1.2, min=0.5, max=5.0, step=0.1, label="volume mult",
                           help="bar volume must exceed this x the volume SMA of the prior bars")
    tp1_r: float = P(2.0, min=1.0, max=6.0, step=0.1, label="TP1 (R)")
    tp1_fraction: float = P(0.40, min=0.1, max=1.0, step=0.05, label="TP1 fraction",
                            help="fraction closed at TP1; the remainder rides the ATR runner")
    trail_atr_mult: float = P(1.2, min=0.5, max=5.0, step=0.1, label="runner trail ATR mult",
                              help="runner trails this x ATR behind the best price after TP1")
    be_at_r: float = P(0.8, min=0.2, max=5.0, step=0.1, label="break-even at (R)",
                       help="move the stop to break-even once the trade is this many R in profit")
    tp_height_mult: float = P(1.6, min=0.5, max=5.0, step=0.1, label="squeeze-height note (x)",
                              help="legacy squeeze-height target, reported in the journal meta only")
    tp_fraction: float = P(0.5, min=0.1, max=1.0, step=0.05, label="legacy TP fraction",
                           help="superseded by tp1_fraction; kept so saved slider values survive")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")


class BollingerSqueezeBreak(Strategy):
    id = "S03"
    name = "Bollinger Squeeze Break"
    Params = Params
    doc = StrategyDoc(
        idea="Trade the first expansion candle out of a Bollinger squeeze on the 5m chart.",
        timeframe="5m",
        symbols="all configured",
        entry="prior bar's BB(20,2) bandwidth is a 12-bar low and this bar closes outside the band "
              "(close > upper -> long, close < lower -> short) on volume > 1.2 x vol SMA(20)",
        stop="opposite squeeze band (long: lower band, short: upper band)",
        targets="TP1 2.0R closes 40%, runner trails 1.2 x ATR(14) after TP1, break-even at 0.8R",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="looser squeeze/volume filters fire on thin tape; first expansion candle, high leverage",
    )
    timeframes = ("5m",)
    contributes_votes = True
    warmup_bars = 80
    # TP1 is now a fixed 2.0R (the squeeze height only survives in the signal meta), so Signal.rr() reports
    # 2.0 and a 1.5 floor is comfortably cleared while still rejecting degenerate geometry.
    min_rr = 1.5

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last_signal: dict[str, dict[str, Any]] = {}
        self._squeeze: dict[str, bool] = {}
        self._skips: dict[str, int] = {}

    def _skip(self, why: str) -> None:
        self._skips[why] = self._skips.get(why, 0) + 1

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "5m")
        if len(candles) < p.bb_period + p.squeeze_bars + 2:
            return []
        _basis, upper, lower, width = ctx.ind(c.symbol, "5m", "bollinger", n=p.bb_period, k=p.bb_k)
        u_prev, l_prev, w_prev = last(upper, 1), last(lower, 1), last(width, 1)
        if u_prev is None or l_prev is None or w_prev is None:
            return []
        window = list(width[-(p.squeeze_bars + 1):-1])  # the squeeze_bars bandwidths ending at the prior bar
        if len(window) < p.squeeze_bars or any(w is None for w in window):
            return []
        squeezed = w_prev <= min(window)
        self._squeeze[c.symbol] = squeezed
        if not squeezed:
            return []
        if c.close > u_prev:
            side, stop = "long", l_prev
        elif c.close < l_prev:
            side, stop = "short", u_prev
        else:
            return []
        vsma = last(ctx.ind(c.symbol, "5m", "vol_sma", n=p.bb_period), 1)
        if vsma is None or vsma <= 0 or c.volume <= p.volume_mult * vsma:
            self._skip("volume")
            return []
        height = u_prev - l_prev
        price = ctx.last_price(c.symbol) or c.close
        if height <= 0 or (side == "long" and stop >= price) or (side == "short" and stop <= price):
            self._skip("stop_side")
            return []
        height_target = price + p.tp_height_mult * height if side == "long" else price - p.tp_height_mult * height
        self._last_signal[c.symbol] = {"ts": c.close_time, "side": side, "bandwidth": w_prev, "height": height,
                                       "vol_x": round(c.volume / vsma, 2)}
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="5m", price=price, stop=stop,
            tps_r=[(p.tp1_r, p.tp1_fraction)], be_at_r=p.be_at_r,
            trail=TrailSpec("atr", "5m", p.trail_atr_mult, p.atr_period, activate_after_tp1=True),
            valid_bars=2, reason=f"BB squeeze break {side} vol {c.volume / vsma:.1f}x",
            meta={"upper": u_prev, "lower": l_prev, "height": height, "bandwidth": w_prev, "vol_sma": vsma,
                  "squeeze_height": height, "squeeze_height_target": height_target})]

    def state(self) -> dict[str, Any]:
        return {"last_signal": self._last_signal, "squeeze": self._squeeze, "skips": self._skips}

    def reset(self) -> None:
        self._last_signal.clear()
        self._squeeze.clear()
        self._skips.clear()
