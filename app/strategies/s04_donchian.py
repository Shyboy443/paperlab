"""S04 Donchian Breakout - "turtle on a 5-minute leash"

Idea:       Classic Donchian channel breakout, but on the 5-minute chart at 15x instead of daily bars.
Timeframe:  5m.
Symbols:    all configured symbols.
Entry:      close above the 12-bar Donchian high (prior 12 bars) -> long; close below the 12-bar low -> short.
Stop:       channel mid = (upper + lower) / 2.
Targets:    TP1 at 2.0R closes 40%; the remaining 60% rides a 1.2 x ATR(5m, 14) runner armed after TP1 with a
            hard cap at 4.0R, and the stop moves to break-even at 0.8R.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: a 12-bar channel on 5m bars breaks many times a day (the 15m/20-bar version barely fired),
            partial profit comes early and the remainder is allowed to run to 4R.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    channel: int = P(12, min=5, max=100, step=1, label="Donchian channel (bars)")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period", help="ATR used by the runner trail")
    tp1_r: float = P(2.0, min=1.0, max=6.0, step=0.1, label="TP1 (R)")
    tp1_fraction: float = P(0.40, min=0.1, max=1.0, step=0.05, label="TP1 fraction",
                            help="fraction closed at TP1; the remainder rides the ATR runner")
    runner_r: float = P(4.0, min=1.5, max=12.0, step=0.1, label="runner cap (R)",
                        help="hard target for the remaining size after TP1")
    trail_atr_mult: float = P(1.2, min=0.5, max=5.0, step=0.1, label="runner trail ATR mult",
                              help="runner trails this x ATR behind the best price after TP1")
    be_at_r: float = P(0.8, min=0.2, max=5.0, step=0.1, label="break-even at (R)",
                       help="move the stop to break-even once the trade is this many R in profit")
    tp_r: float = P(3.0, min=1.0, max=8.0, step=0.1, label="legacy TP cap (R)",
                    help="superseded by runner_r; kept so saved slider values survive")


class DonchianBreakout(Strategy):
    id = "S04"
    name = "Donchian Breakout"
    Params = Params
    doc = StrategyDoc(
        idea="Classic Donchian channel breakout on the 5m chart at 15x instead of daily bars.",
        timeframe="5m",
        symbols="all configured",
        entry="close > 12-bar Donchian high (prior bars) -> long; close < 12-bar low -> short",
        stop="channel mid = (upper + lower) / 2",
        targets="TP1 2.0R closes 40%, remainder trails 1.2 x ATR(14) after TP1 with a hard 4.0R cap, "
                "break-even at 0.8R",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="a 12-bar channel on 5m bars breaks often; early partial, runner allowed to reach 4R",
    )
    timeframes = ("5m",)
    contributes_votes = False
    warmup_bars = 60
    min_rr = 1.5

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last_signal: dict[str, dict[str, Any]] = {}
        self._channel: dict[str, dict[str, float]] = {}
        self._skips: dict[str, int] = {}

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "5m")
        if len(candles) < p.channel + 2:
            return []
        upper, lower = ctx.ind(c.symbol, "5m", "donchian", n=p.channel)
        u, lo = last(upper), last(lower)
        if u is None or lo is None or u <= lo:
            return []
        height = u - lo
        mid = (u + lo) / 2.0
        self._channel[c.symbol] = {"upper": u, "lower": lo, "mid": mid}
        if c.close > u:
            side = "long"
        elif c.close < lo:
            side = "short"
        else:
            return []
        price = ctx.last_price(c.symbol) or c.close
        stop = mid
        dist = price - stop if side == "long" else stop - price
        if dist <= 0:
            self._skips["stop_side"] = self._skips.get("stop_side", 0) + 1
            return []
        band_target = u + height if side == "long" else lo - height  # opposite band mirrored across the break
        runner_r = max(p.runner_r, p.tp1_r)
        runner_fraction = max(0.0, 1.0 - p.tp1_fraction)
        tps = [(p.tp1_r, p.tp1_fraction)]
        if runner_fraction > 0:
            tps.append((runner_r, runner_fraction))
        self._last_signal[c.symbol] = {"ts": c.close_time, "side": side, "height": height,
                                       "tp1_r": p.tp1_r, "runner_r": runner_r}
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="5m", price=price, stop=stop,
            tps_r=tps, be_at_r=p.be_at_r,
            trail=TrailSpec("atr", "5m", p.trail_atr_mult, p.atr_period, activate_after_tp1=True),
            valid_bars=1, reason=f"Donchian{p.channel} break {side} (tp1 {p.tp1_r:.1f}R, runner {runner_r:.1f}R)",
            meta={"upper": u, "lower": lo, "mid": mid, "height": height, "band_target": band_target})]

    def state(self) -> dict[str, Any]:
        return {"last_signal": self._last_signal, "channel": self._channel, "skips": self._skips}

    def reset(self) -> None:
        self._last_signal.clear()
        self._channel.clear()
        self._skips.clear()
