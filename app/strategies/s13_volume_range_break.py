"""S13 Volume Spike Range Break - "volume is the trigger"

Idea:       A 5m close outside the prior 20-bar high/low range on a volume spike (>= 1.4 x the prior 20-bar volume
            average) is traded immediately, with the stop half-way back inside the range.
Timeframe:  5m.
Symbols:    all configured symbols.
Entry:      close > prior 20-bar high with volume >= 1.4 x SMA(20) of the prior bars' volume -> long;
            close < prior 20-bar low with the same volume spike -> short (the range excludes the current bar).
Stop:       50% back inside the range (long: range high - 0.5 x range height; short: range low + 0.5 x height).
Targets:    TP1 2R closes 40%; the remaining 60% rides a 1.2 x ATR(5m, 14) trail armed after TP1, hard-capped
            at 6R. Break-even stop at 0.8R.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: the 1.4x volume trigger actually prints on the testnet tape (2.2x never did), the range is only
            20 bars so breaks come sooner, and the runner is left alone to 6R.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    range_bars: int = P(20, min=5, max=120, step=1, label="range bars", help="prior bars forming the range")
    volume_mult: float = P(1.4, min=1.0, max=6.0, step=0.1, label="volume spike mult",
                           help="bar volume vs the SMA of the prior range bars' volume")
    stop_inside_fraction: float = P(0.5, min=0.1, max=1.0, step=0.05, label="stop inside range (fraction)",
                                    help="stop this fraction of the range height back inside the range")
    tp1_r: float = P(2.0, min=0.5, max=6.0, step=0.1, label="TP1 (R)")
    tp1_fraction: float = P(0.40, min=0.1, max=0.9, step=0.05, label="TP1 fraction")
    runner_r: float = P(6.0, min=1.0, max=12.0, step=0.1, label="runner cap (R)",
                        help="hard cap on the runner: closes the remainder if the trail has not already")
    trail_atr_mult: float = P(1.2, min=0.5, max=5.0, step=0.1, label="runner trail ATR mult")
    be_at_r: float = P(0.8, min=0.0, max=3.0, step=0.1, label="break-even at (R)",
                       help="move the stop to entry once the trade is this many R in profit")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period", help="ATR used by the runner trail")


class VolumeSpikeRangeBreak(Strategy):
    id = "S13"
    name = "Volume Spike Range Break"
    Params = Params
    doc = StrategyDoc(
        idea="Close outside the prior 20-bar range on a 1.4x volume spike.",
        timeframe="5m",
        symbols="all configured",
        entry="close > prior 20-bar high with volume >= 1.4 x SMA(20) volume -> long; close < the 20-bar low -> short",
        stop="50% back inside the range",
        targets="TP1 2R closes 40%, 1.2 x ATR trail on the rest after TP1, hard cap 6R; break-even at 0.8R",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="1.4x volume trigger fires on this tape, 20-bar range breaks sooner, runner held to 6R",
    )
    timeframes = ("5m",)
    contributes_votes = True
    warmup_bars = 60
    min_rr = 2.5

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, int] = {"signals": 0, "quiet_breaks": 0}

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "5m")
        if len(candles) < max(int(p.range_bars), int(p.atr_period)) + 2:
            return []
        upper, lower = ctx.ind(c.symbol, "5m", "donchian", n=p.range_bars)
        hi, lo = last(upper), last(lower)
        vol_base = last(ctx.ind(c.symbol, "5m", "vol_sma", n=p.range_bars), 1)  # prior bars only
        if hi is None or lo is None or vol_base is None or vol_base <= 0:
            return []
        height = hi - lo
        if height <= 0:
            return []
        vol_ratio = c.volume / vol_base
        side: str | None = None
        if c.close > hi:
            side = "long"
        elif c.close < lo:
            side = "short"
        self._last[c.symbol] = {"ts": c.close_time, "range_high": hi, "range_low": lo, "vol_ratio": vol_ratio,
                                "break": side}
        if side is None:
            return []
        if vol_ratio < p.volume_mult:
            self._counters["quiet_breaks"] += 1
            return []
        price = ctx.last_price(c.symbol) or c.close
        stop = hi - p.stop_inside_fraction * height if side == "long" else lo + p.stop_inside_fraction * height
        self._counters["signals"] += 1
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="5m", price=price, stop=stop,
            tps_r=[(p.tp1_r, p.tp1_fraction), (p.runner_r, 1.0 - p.tp1_fraction)],
            trail=TrailSpec("atr", "5m", p.trail_atr_mult, int(p.atr_period), activate_after_tp1=True),
            be_at_r=p.be_at_r, valid_bars=2,
            reason=f"{p.range_bars}-bar range {side} break on {vol_ratio:.1f}x volume",
            meta={"range_high": hi, "range_low": lo, "range_height": height, "vol_ratio": vol_ratio,
                  "vol_base": vol_base})]

    def state(self) -> dict[str, Any]:
        return {"last": self._last, "counters": dict(self._counters)}

    def reset(self) -> None:
        self._last.clear()
        self._counters = {"signals": 0, "quiet_breaks": 0}
