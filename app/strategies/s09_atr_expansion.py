"""S09 ATR Expansion Break - "only trade exploding volatility"

Idea:       When the 1m ATR(14) is above its own 30-bar average (ratio > 1.4) volatility is expanding; a close
            through the prior 12-bar high/low in that state is a momentum break worth holding for the fat tail.
Timeframe:  1m (44 bars of warm-up instead of the 64 5m bars the old build needed to leave WARMUP).
Symbols:    all configured symbols.
Entry:      ATR(14) / SMA(ATR(14), 30) > 1.4 and close > prior 12-bar high -> long; close < prior 12-bar low -> short
            (the prior bars exclude the breakout bar itself).
Stop:       1.1 x ATR(14) from the entry price.
Targets:    5R full size; an ATR trail (2.0 x ATR(1m, 14)) is active from the first bar, not only after TP1.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: 1m bars and a 1.4 ratio make the expansion gate fire on thin tape instead of sitting in
            WARMUP, and the trade is held for a 5R tail on an immediate trail.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import last, sma
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")
    atr_sma: int = P(30, min=10, max=200, step=1, label="ATR baseline SMA", help="SMA length over the ATR series")
    ratio_min: float = P(1.4, min=1.0, max=5.0, step=0.1, label="ATR / baseline min",
                         help="ATR(14) divided by its SMA must exceed this")
    breakout_bars: int = P(12, min=3, max=60, step=1, label="breakout lookback (bars)",
                           help="close must clear the high/low of this many prior bars")
    stop_atr_mult: float = P(1.1, min=0.3, max=4.0, step=0.1, label="stop ATR mult")
    tp_r: float = P(5.0, min=1.0, max=10.0, step=0.1, label="TP (R)")
    trail_atr_mult: float = P(2.0, min=0.5, max=5.0, step=0.1, label="runner trail ATR mult",
                              help="ATR trail active from the first bar")


class AtrExpansionBreak(Strategy):
    id = "S09"
    name = "ATR Expansion Break"
    Params = Params
    doc = StrategyDoc(
        idea="Break of the prior 12-bar range while the 1m ATR is expanding (ATR / its 30-bar SMA > 1.4).",
        timeframe="1m",
        symbols="all configured",
        entry="ATR(14)/SMA(ATR,30) > 1.4 and close > prior 12-bar high -> long; close < prior 12-bar low -> short",
        stop="1.1 x ATR(14) from entry",
        targets="5R full size, plus a 2.0 x ATR trail active immediately",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="1m bars and a 1.4 ratio fire on thin tape; held for a 5R tail on an immediate trail",
    )
    timeframes = ("1m",)
    contributes_votes = False
    warmup_bars = 60
    min_rr = 2.5

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, int] = {"signals": 0, "quiet_breaks": 0}

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "1m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "1m")
        if len(candles) < p.atr_period + p.atr_sma + 2:
            return []
        atr_series = ctx.ind(c.symbol, "1m", "atr", n=p.atr_period)
        atr_now = last(atr_series)
        baseline = last(sma(atr_series, p.atr_sma))
        upper, lower = ctx.ind(c.symbol, "1m", "donchian", n=p.breakout_bars)
        hi, lo = last(upper), last(lower)
        if atr_now is None or baseline is None or hi is None or lo is None or atr_now <= 0 or baseline <= 0:
            return []
        ratio = atr_now / baseline
        expanding = ratio > p.ratio_min
        side: str | None = None
        if c.close > hi:
            side = "long"
        elif c.close < lo:
            side = "short"
        self._last[c.symbol] = {"ts": c.close_time, "atr": atr_now, "atr_baseline": baseline, "ratio": ratio,
                                "expanding": expanding, "range_high": hi, "range_low": lo, "break": side}
        if side is None:
            return []
        if not expanding:
            self._counters["quiet_breaks"] += 1
            return []
        price = ctx.last_price(c.symbol) or c.close
        stop = price - p.stop_atr_mult * atr_now if side == "long" else price + p.stop_atr_mult * atr_now
        self._counters["signals"] += 1
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="1m", price=price, stop=stop,
            tps_r=[(p.tp_r, 1.0)],
            trail=TrailSpec("atr", "1m", p.trail_atr_mult, p.atr_period, activate_after_tp1=False),
            valid_bars=2, reason=f"ATR expansion x{ratio:.2f} {side} break of {p.breakout_bars}-bar range",
            meta={"ratio": ratio, "atr": atr_now, "atr_baseline": baseline, "range_high": hi, "range_low": lo})]

    def state(self) -> dict[str, Any]:
        return {"last": self._last, "counters": dict(self._counters)}

    def reset(self) -> None:
        self._last.clear()
        self._counters = {"signals": 0, "quiet_breaks": 0}
