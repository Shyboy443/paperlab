"""S27 Breakout Runner - buy the N-bar high, stop at the opposite channel, ride it out.

The second half of the wide-target experiment (see S26). This is the classic Donchian/turtle structure,
kept deliberately plain so the test is about the SHAPE of the trade rather than a clever filter:

  * enter on a close beyond the `channel` -bar extreme;
  * stop at the opposite side of a shorter channel - wide, so the fee is a small share of R;
  * no fixed target; a channel trail exits when the trend structure breaks.

S04 (Donchian Breakout) already exists and is a different trade: it targets 3R or the opposite band and
runs on 5m. This one is 15m, has NO target at all, and trails on the channel rather than on ATR - the
exit is price structure, not volatility. Both can be right; they are testing different claims.

Idea:       an expansion out of a multi-hour range keeps going more often than it pays to fade.
Timeframe:  15m.
Symbols:    all configured symbols.
Entry:      close above the highest high of the prior `channel` bars (or below the lowest low), with the
            breakout bar's range at least `min_range_atr` ATRs so it is a real expansion, not a drift.
Stop:       the opposite side of the `exit_channel` -bar channel, `stop_buffer_atr` ATRs beyond.
Targets:    none. Break-even at `be_at_r`, then the channel trail carries it.
Sizing:     standard per-trade risk, 5x virtual leverage.
Why aggressive: no cap on the winner and no clock; the loss is bounded by a wide but definite stop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    channel: int = P(48, min=8, max=200, step=4, label="breakout channel (bars)",
                     help="48 x 15m = 12 hours")
    exit_channel: int = P(16, min=4, max=100, step=2, label="exit channel (bars)")
    min_range_atr: float = P(1.0, min=0.2, max=5.0, step=0.1, label="min breakout bar range (ATR)")
    atr_n: int = P(14, min=5, max=50, step=1, label="ATR length")
    stop_buffer_atr: float = P(0.5, min=0.0, max=3.0, step=0.1, label="stop beyond the exit channel (ATR)")
    be_at_r: float = P(1.5, min=0.0, max=6.0, step=0.1, label="break-even at (R)")
    trail_mult: float = P(3.0, min=1.0, max=10.0, step=0.1, label="ATR trail multiple")
    max_hold_s: int = P(0, min=0, max=604800, step=3600, label="max hold (s)", help="0 = no clock")
    cooldown_s: int = P(1800, min=0, max=14400, step=60, label="cooldown after exit (s)")


class BreakoutRunner(Strategy):
    id = "S27"
    name = "Breakout Runner"
    Params = Params
    doc = StrategyDoc(
        idea="Take a 12-hour range breakout with a wide channel stop, no target and a loose trail.",
        timeframe="15m",
        symbols="all configured",
        entry="close beyond the prior 48-bar extreme with a breakout bar range >= 1 ATR",
        stop="the opposite side of the 16-bar channel, 0.5 ATR beyond",
        targets="none - break-even at 1.5R then a 3x ATR trail; no time exit",
        sizing="standard risk per trade at 5x virtual leverage",
        why_aggressive="uncapped winner with a bounded loss; the wide stop makes the round-trip fee a "
                       "small fraction of R, which is where the other strategies died",
    )
    timeframes = ("15m",)
    contributes_votes = False
    warmup_bars = 200
    max_positions = 1
    min_rr = 0.0
    default_leverage = 5

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last: dict[str, dict[str, Any]] = {}
        self._stats = {"breaks": 0, "weak_range": 0, "traded": 0}

    def cooldown_ms(self) -> int:
        return int(self.params.cooldown_s) * 1000

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "15m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "15m")
        if len(candles) < int(p.channel) + int(p.atr_n) + 3:
            return []
        # offset 1 so the channel EXCLUDES the breakout bar itself
        hi = last(ctx.ind(c.symbol, "15m", "highest", n=int(p.channel)), 1)
        lo = last(ctx.ind(c.symbol, "15m", "lowest", n=int(p.channel)), 1)
        ex_hi = last(ctx.ind(c.symbol, "15m", "highest", n=int(p.exit_channel)), 1)
        ex_lo = last(ctx.ind(c.symbol, "15m", "lowest", n=int(p.exit_channel)), 1)
        atr = last(ctx.ind(c.symbol, "15m", "atr", n=int(p.atr_n)))
        if None in (hi, lo, ex_hi, ex_lo, atr) or atr <= 0:
            return []
        if c.close > hi:
            side, stop = "long", ex_lo - p.stop_buffer_atr * atr
        elif c.close < lo:
            side, stop = "short", ex_hi + p.stop_buffer_atr * atr
        else:
            return []
        self._stats["breaks"] += 1
        self._last[c.symbol] = {"side": side, "range_atr": round(c.range / atr, 2),
                                "channel_hi": hi, "channel_lo": lo, "ts": c.close_time}
        if c.range < p.min_range_atr * atr:
            self._stats["weak_range"] += 1
            self._last[c.symbol]["skip"] = "breakout bar is too small to be an expansion"
            return []
        price = ctx.last_price(c.symbol) or c.close
        if (side == "long" and price <= stop) or (side == "short" and price >= stop):
            return []
        self._stats["traded"] += 1
        self._last[c.symbol]["traded"] = True
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="15m", price=price, stop=stop,
            tps_r=(), be_at_r=p.be_at_r,
            trail=TrailSpec("atr", "15m", p.trail_mult, int(p.atr_n), False),
            max_hold_s=int(p.max_hold_s) or None, valid_bars=2,
            reason=f"{side} break of the {int(p.channel)}-bar channel on a {c.range / atr:.1f} ATR bar",
            meta={"channel_high": hi, "channel_low": lo, "range_atr": round(c.range / atr, 2),
                  "stop_pct": round(abs(price - stop) / price * 100, 3)})]

    def state(self) -> dict[str, Any]:
        return {"last_break": self._last, **self._stats}

    def reset(self) -> None:
        self._last.clear()
        for k in self._stats:
            self._stats[k] = 0
