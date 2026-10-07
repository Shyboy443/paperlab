"""S06 Supertrend Flip - "flip with the 15m wind at your back"

Idea:       Enter on every 5-minute Supertrend flip that agrees with the 15-minute Supertrend direction and ride
            the line as a trailing stop.
Timeframe:  5m (entries and trailing), 15m (direction filter).
Symbols:    all configured symbols.
Entry:      5m Supertrend(ATR 8, x1.8) direction flips -1 -> +1 while the 15m Supertrend(ATR 10, x2.2) is +1 ->
            long; flips +1 -> -1 while the 15m is -1 -> short.
Stop:       the 5m Supertrend line; moved to break-even at 1.0R; manage() keeps ratcheting the stop onto the
            5m line as it advances (only favourable moves are applied).
Targets:    TP1 at 2.0R closes 40%; the remaining 60% rides a 1.2 x ATR(5m, 8) runner armed after TP1.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage (first boot: size_mult 0.5).
Why aggressive: a faster ATR 8 / x1.8 supertrend flips much more often, entry is on the very first flip bar,
            profit is banked at 2R and the rest runs on an ATR trail.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import last
from app.core.types import Candle, ExitUpdate, Signal, TrailSpec, VirtualPosition
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    # `atr_period` and `multiplier` are read by the engine to compute the trailing supertrend line: keep the names.
    atr_period: int = P(8, min=3, max=50, step=1, label="5m supertrend ATR period")
    multiplier: float = P(1.8, min=0.5, max=6.0, step=0.1, label="5m supertrend multiplier")
    htf_atr_period: int = P(10, min=3, max=50, step=1, label="15m supertrend ATR period")
    htf_multiplier: float = P(2.2, min=0.5, max=6.0, step=0.1, label="15m supertrend multiplier")
    tp1_r: float = P(2.0, min=1.0, max=6.0, step=0.1, label="TP1 (R)")
    tp1_fraction: float = P(0.40, min=0.1, max=1.0, step=0.05, label="TP1 fraction",
                            help="fraction closed at TP1; the remainder rides the ATR runner")
    trail_atr_mult: float = P(1.2, min=0.5, max=5.0, step=0.1, label="runner trail ATR mult",
                              help="runner trails this x ATR behind the best price after TP1")
    be_at_r: float = P(1.0, min=0.3, max=5.0, step=0.1, label="break-even at (R)")
    tp_r: float = P(3.0, min=1.0, max=8.0, step=0.1, label="legacy TP (R)",
                    help="superseded by tp1_r; kept so saved slider values survive")


class SupertrendFlip(Strategy):
    id = "S06"
    name = "Supertrend Flip"
    Params = Params
    doc = StrategyDoc(
        idea="Enter on every 5m Supertrend flip that agrees with the 15m Supertrend and ride an ATR runner.",
        timeframe="5m entries, 15m direction filter",
        symbols="all configured",
        entry="5m Supertrend(8, 1.8) flips -1 -> +1 with the 15m Supertrend(10, 2.2) at +1 -> long; "
              "flips +1 -> -1 with the 15m at -1 -> short",
        stop="the 5m Supertrend line, break-even at 1.0R, manage() ratchets it onto the line",
        targets="TP1 2.0R closes 40%, runner trails 1.2 x ATR(8) after TP1",
        sizing="2% wallet risk per trade at 15x virtual leverage; first boot size_mult 0.5",
        why_aggressive="ATR 8 / x1.8 flips far more often; first flip bar, early partial, ATR runner",
    )
    timeframes = ("5m", "15m")
    contributes_votes = True
    warmup_bars = 60
    # TP1 is the only hard target (the remainder trails), so Signal.rr() reports the TP1 multiple: the floor has
    # to sit at or below tp1_r or every signal would be rejected as rr_below_min.
    min_rr = 2.0

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last_signal: dict[str, dict[str, Any]] = {}
        self._direction: dict[str, dict[str, int | None]] = {}
        self._skips: dict[str, int] = {}

    def _line_and_direction(self, symbol: str, ctx: MarketContext) -> tuple[list, list]:
        p = self.params
        return ctx.ind(symbol, "5m", "supertrend", n=p.atr_period, mult=p.multiplier)

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "5m")
        if len(candles) < p.atr_period + 3:
            return []
        line, direction = self._line_and_direction(c.symbol, ctx)
        d_now, d_prev, st_line = last(direction), last(direction, 1), last(line)
        htf_dir = last(ctx.ind(c.symbol, "15m", "supertrend", n=p.htf_atr_period, mult=p.htf_multiplier)[1])
        self._direction[c.symbol] = {"5m": d_now, "15m": htf_dir}
        if d_now is None or d_prev is None or st_line is None:
            return []
        if d_prev == -1 and d_now == 1:
            side = "long"
        elif d_prev == 1 and d_now == -1:
            side = "short"
        else:
            return []
        if htf_dir != d_now:
            self._skips["htf_disagrees"] = self._skips.get("htf_disagrees", 0) + 1
            self._last_signal[c.symbol] = {"ts": c.close_time, "side": side, "filtered": True, "htf": htf_dir}
            return []
        price = ctx.last_price(c.symbol) or c.close
        stop = st_line
        if stop <= 0 or (side == "long" and stop >= price) or (side == "short" and stop <= price):
            self._skips["stop_side"] = self._skips.get("stop_side", 0) + 1
            return []
        self._last_signal[c.symbol] = {"ts": c.close_time, "side": side, "filtered": False, "line": stop}
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="5m", price=price, stop=stop,
            tps_r=[(p.tp1_r, p.tp1_fraction)], be_at_r=p.be_at_r,
            trail=TrailSpec("atr", "5m", p.trail_atr_mult, p.atr_period, activate_after_tp1=True),
            valid_bars=2, reason=f"5m supertrend flip {side}, 15m agrees",
            meta={"line": stop, "htf_direction": htf_dir})]

    def manage(self, pos: VirtualPosition, c: Candle, ctx: MarketContext) -> ExitUpdate | None:
        """Ratchet the stop to the current 5m supertrend line; the engine applies favourable moves only."""
        if c.tf != "5m" or c.symbol != pos.symbol:
            return None
        line, _direction = self._line_and_direction(pos.symbol, ctx)
        st_line = last(line)
        if st_line is None or st_line <= 0:
            return None
        return ExitUpdate(stop=st_line, reason="trail")

    def state(self) -> dict[str, Any]:
        return {"last_signal": self._last_signal, "direction": self._direction, "skips": self._skips}

    def reset(self) -> None:
        self._last_signal.clear()
        self._direction.clear()
        self._skips.clear()
