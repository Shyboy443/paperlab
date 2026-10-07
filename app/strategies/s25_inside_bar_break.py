"""S25 Inside-Bar Compression Break - trade the release of a multi-bar coil with the trend.

Idea:       An inside bar is a bar whose whole range sits inside the previous bar's range: the market
            has stopped expanding. A run of two or more of them is a coil, and coils resolve. We take
            the break of the mother bar's range in the direction of the higher-timeframe trend, with
            the stop on the opposite side of the coil - which is, by construction, tight.
            Distinct from S03 (Bollinger width squeeze) and S09 (ATR expansion): those measure
            volatility statistically, this is a pure price-structure pattern with an exact invalidation
            level, so the stop does not depend on an indicator at all.
Timeframe:  5m trigger, 15m trend gate.
Symbols:    all configured symbols.
Entry:      >= `min_inside` consecutive inside bars, the coil's range is <= `max_coil_atr` ATRs (a real
            compression, not a wide drifting one), then a bar CLOSES beyond the mother bar's high or low
            in the same direction as the 15m EMA trend.
Stop:       the opposite side of the coil, `stop_buffer_atr` ATRs beyond it.
Targets:    `tp_r` R closes 50%, the rest trails on ATR; time exit after `max_hold_s`.
Sizing:     standard per-trade risk, 10x virtual leverage.
Why aggressive: the stop is the far side of a compressed range, which is the tightest honest stop
            available, so a normal expansion move is worth several R.

Interpretation note: `require_trend=False` turns this into a pure breakout in both directions, which
trades far more often and is the setting worth testing separately rather than assuming.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    min_inside: int = P(2, min=1, max=6, step=1, label="min inside bars",
                        help="consecutive bars contained by the mother bar before the coil counts")
    max_coil_atr: float = P(1.2, min=0.3, max=5.0, step=0.1, label="max coil range (ATR)",
                            help="the mother bar's range must be this small relative to ATR")
    trend_ema: int = P(50, min=10, max=200, step=5, label="15m trend EMA")
    require_trend: bool = P(True, label="require the 15m trend")
    atr_n: int = P(14, min=5, max=50, step=1, label="ATR length")
    stop_buffer_atr: float = P(0.25, min=0.0, max=2.0, step=0.05, label="stop beyond the coil (ATR)")
    tp_r: float = P(2.5, min=0.5, max=10.0, step=0.1, label="first target (R)")
    tp_frac: float = P(0.5, min=0.1, max=1.0, step=0.05, label="fraction closed at the target")
    be_at_r: float = P(1.0, min=0.0, max=5.0, step=0.1, label="break-even at (R)")
    atr_trail_mult: float = P(2.0, min=0.5, max=6.0, step=0.1, label="ATR trail multiple")
    max_hold_s: int = P(10800, min=300, max=28800, step=300, label="max hold (s)")
    cooldown_s: int = P(300, min=0, max=3600, step=30, label="cooldown after exit (s)")


class InsideBarBreak(Strategy):
    id = "S25"
    name = "Inside Bar Compression Break"
    Params = Params
    doc = StrategyDoc(
        idea="Trade the break of a multi-bar inside-bar coil in the direction of the 15m trend.",
        timeframe="5m trigger, 15m trend gate",
        symbols="all configured",
        entry=">= 2 consecutive inside bars with the mother bar's range <= 1.2 ATR, then a close beyond "
              "the mother bar's extreme agreeing with the 15m EMA50",
        stop="the far side of the coil, 0.25 ATR beyond",
        targets="2.5R closes 50%, break-even at 1R, ATR trail on the rest, time exit at 3h",
        sizing="standard risk per trade at 10x virtual leverage",
        why_aggressive="the coil gives the tightest honest stop there is, so an ordinary expansion "
                       "move is worth several R",
    )
    timeframes = ("5m", "15m")
    contributes_votes = False
    warmup_bars = 80
    max_positions = 1
    min_rr = 2.0
    default_leverage = 10

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last: dict[str, dict[str, Any]] = {}
        self._stats = {"coils": 0, "too_wide": 0, "trend_skips": 0, "traded": 0}

    def cooldown_ms(self) -> int:
        return int(self.params.cooldown_s) * 1000

    def _coil(self, candles, n_inside: int) -> tuple[Candle, int] | None:
        """The mother bar and how many inside bars followed it, ending at the bar BEFORE the trigger."""
        if len(candles) < n_inside + 3:
            return None
        inside = 0
        i = len(candles) - 2                      # candles[-1] is the trigger bar
        while i - 1 >= 0:
            bar, prev = candles[i], candles[i - 1]
            if bar.high <= prev.high and bar.low >= prev.low:
                inside += 1
                i -= 1
                continue
            break
        if inside < n_inside:
            return None
        return candles[i], inside                 # candles[i] is the mother bar

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "5m")
        if len(candles) < max(int(p.atr_n), int(p.min_inside) + 4) + 2:
            return []
        atr = last(ctx.ind(c.symbol, "5m", "atr", n=int(p.atr_n)))
        if atr is None or atr <= 0:
            return []
        found = self._coil(candles, int(p.min_inside))
        if found is None:
            return []
        mother, inside = found
        self._stats["coils"] += 1
        self._last[c.symbol] = {"inside_bars": inside, "coil_atr": round(mother.range / atr, 2),
                                "hi": mother.high, "lo": mother.low, "ts": c.close_time}
        if mother.range > p.max_coil_atr * atr:
            self._stats["too_wide"] += 1
            self._last[c.symbol]["skip"] = "coil is too wide to be a compression"
            return []
        if c.close > mother.high:
            side, stop = "long", mother.low - p.stop_buffer_atr * atr
        elif c.close < mother.low:
            side, stop = "short", mother.high + p.stop_buffer_atr * atr
        else:
            self._last[c.symbol]["skip"] = "still coiling"
            return []
        if p.require_trend:
            c15 = ctx.candles(c.symbol, "15m")
            if len(c15) < int(p.trend_ema) + 2:
                return []
            e = last(ctx.ind(c.symbol, "15m", "ema", n=int(p.trend_ema)))
            if e is None:
                return []
            agrees = c15[-1].close > e if side == "long" else c15[-1].close < e
            if not agrees:
                self._stats["trend_skips"] += 1
                self._last[c.symbol]["skip"] = "15m trend disagrees with the break"
                return []
        price = ctx.last_price(c.symbol) or c.close
        if (side == "long" and price <= stop) or (side == "short" and price >= stop):
            return []
        self._stats["traded"] += 1
        self._last[c.symbol]["traded"] = True
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="5m", price=price, stop=stop,
            tps_r=[(p.tp_r, p.tp_frac)], be_at_r=p.be_at_r,
            trail=TrailSpec("atr", "5m", p.atr_trail_mult, int(p.atr_n), True),
            max_hold_s=int(p.max_hold_s), valid_bars=2,
            reason=f"{inside} inside bars, coil {mother.range / atr:.2f} ATR, {side} break",
            meta={"inside_bars": inside, "coil_high": mother.high, "coil_low": mother.low,
                  "coil_atr": round(mother.range / atr, 2)})]

    def state(self) -> dict[str, Any]:
        return {"last_coil": self._last, **self._stats}

    def reset(self) -> None:
        self._last.clear()
        for k in self._stats:
            self._stats[k] = 0
