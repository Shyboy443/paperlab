"""S26 Trend Rider - one entry, no fixed target, hold until the trend actually breaks.

Why this exists: every strategy tested so far (S15, S21, S22, S24, S25) fades or retests with a tight
stop and a small fixed target. Measured on real futures data they all lose, and the reason is structural
rather than pattern-specific: with a ~0.4% stop and a ~0.2% target, the round-trip cost is 20-50% of the
gross win, so the hit rate needed is 70%+ and nothing delivers that.

This is the opposite construction, and it is the hypothesis being tested:
  * a WIDE stop (ATR-based, ~1.5-2% on 15m), so the fee is a tiny fraction of R;
  * NO fixed take-profit at all - the position is held until the trend structure breaks;
  * a trailing exit that only gives back a fraction of the move.
Expect a LOW win rate (30-40%) and a large average win. That is the intended shape: it loses often and
small, wins rarely and big. Judge it on avg R and total R, never on win rate.

Idea:       ride an established trend from a pullback, on the highest timeframe the lab subscribes to.
Timeframe:  15m, self-contained.
Symbols:    all configured symbols.
Entry:      EMA`fast` > EMA`slow` (or the mirror) AND the slow EMA itself sloping the right way over
            `slope_bars`, price pulls back to within `pullback_atr` ATRs of the fast EMA, then a bar
            closes back in the trend direction.
Stop:       `stop_atr` ATRs beyond the pullback extreme - wide on purpose.
Targets:    none. Break-even at `be_at_r`, then a Supertrend/ATR trail carries the rest.
Sizing:     standard per-trade risk, 5x virtual leverage (the stop is wide, so the notional is small).
Why aggressive: no profit cap at all - a single trend can pay for a long run of losers, which is the
            only structure that survives a 0.10% round trip.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    fast_ema: int = P(21, min=5, max=100, step=1, label="fast EMA")
    slow_ema: int = P(55, min=20, max=250, step=5, label="slow EMA")
    slope_bars: int = P(10, min=2, max=60, step=1, label="slope lookback (bars)",
                        help="the slow EMA must have moved the right way over this many bars")
    min_slope_pct: float = P(0.05, min=0.0, max=2.0, step=0.01, label="min slow-EMA slope %",
                             help="filters out a flat EMA stack that is not really a trend")
    pullback_atr: float = P(0.75, min=0.1, max=4.0, step=0.05, label="pullback depth (ATR from fast EMA)")
    atr_n: int = P(14, min=5, max=50, step=1, label="ATR length")
    stop_atr: float = P(1.6, min=0.5, max=6.0, step=0.1, label="stop beyond the pullback (ATR)",
                        help="deliberately WIDE: it makes the round-trip fee a small fraction of R")
    be_at_r: float = P(1.5, min=0.0, max=6.0, step=0.1, label="break-even at (R)")
    trail_kind: str = P("atr", label="trail", help="atr or supertrend")
    atr_trail_mult: float = P(3.0, min=1.0, max=10.0, step=0.1, label="ATR trail multiple",
                              help="loose on purpose: a tight trail turns a trend trade into a scalp")
    max_hold_s: int = P(0, min=0, max=604800, step=3600, label="max hold (s)",
                        help="0 = NO clock. The whole point is to let a winner run.")
    cooldown_s: int = P(900, min=0, max=7200, step=60, label="cooldown after exit (s)")


class TrendRider(Strategy):
    id = "S26"
    name = "Trend Rider"
    Params = Params
    doc = StrategyDoc(
        idea="Ride an established 15m trend from a pullback with a wide stop, no target and a loose trail.",
        timeframe="15m",
        symbols="all configured",
        entry="EMA21/EMA55 stacked and the slow EMA sloping; pullback to within 0.75 ATR of the fast EMA; "
              "a bar closes back in the trend direction",
        stop="1.6 ATR beyond the pullback extreme (wide by design)",
        targets="none - break-even at 1.5R then a 3x ATR trail; no time exit",
        sizing="standard risk per trade at 5x virtual leverage",
        why_aggressive="uncapped upside: one trend pays for a long run of small losers, which is the only "
                       "shape that survives a 0.10% round-trip cost",
    )
    timeframes = ("15m",)
    contributes_votes = False
    warmup_bars = 120
    max_positions = 1
    min_rr = 0.0          # there is no fixed target to measure R:R against; the trail defines the exit
    default_leverage = 5

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last: dict[str, dict[str, Any]] = {}
        self._stats = {"trend_bars": 0, "pullbacks": 0, "flat_skips": 0, "traded": 0}

    def cooldown_ms(self) -> int:
        return int(self.params.cooldown_s) * 1000

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "15m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "15m")
        need = max(int(p.slow_ema) + int(p.slope_bars) + 2, int(p.atr_n) + 2)
        if len(candles) < need:
            return []
        fast_s = ctx.ind(c.symbol, "15m", "ema", n=int(p.fast_ema))
        slow_s = ctx.ind(c.symbol, "15m", "ema", n=int(p.slow_ema))
        fast, slow = last(fast_s), last(slow_s)
        slow_then = last(slow_s, int(p.slope_bars))
        atr = last(ctx.ind(c.symbol, "15m", "atr", n=int(p.atr_n)))
        if None in (fast, slow, slow_then, atr) or atr <= 0 or slow_then <= 0:
            return []
        slope_pct = (slow - slow_then) / slow_then * 100.0
        up = fast > slow and slope_pct >= p.min_slope_pct
        down = fast < slow and slope_pct <= -p.min_slope_pct
        if not (up or down):
            self._stats["flat_skips"] += 1
            return []
        self._stats["trend_bars"] += 1
        side = "long" if up else "short"

        # pullback: the bar dipped toward the fast EMA and closed back with the trend
        depth = p.pullback_atr * atr
        if side == "long":
            touched = c.low <= fast + depth
            resumed = c.close > c.open and c.close > fast
            stop = min(c.low, fast - depth) - p.stop_atr * atr
        else:
            touched = c.high >= fast - depth
            resumed = c.close < c.open and c.close < fast
            stop = max(c.high, fast + depth) + p.stop_atr * atr
        if not touched:
            return []
        self._stats["pullbacks"] += 1
        self._last[c.symbol] = {"side": side, "slope_pct": round(slope_pct, 3),
                                "atr_pct": round(atr / c.close * 100, 3), "ts": c.close_time}
        if not resumed:
            self._last[c.symbol]["skip"] = "pullback has not resumed yet"
            return []
        price = ctx.last_price(c.symbol) or c.close
        if (side == "long" and price <= stop) or (side == "short" and price >= stop):
            return []
        self._stats["traded"] += 1
        self._last[c.symbol]["traded"] = True
        trail = (TrailSpec("supertrend", "15m", p.atr_trail_mult, int(p.atr_n), False)
                 if str(p.trail_kind) == "supertrend"
                 else TrailSpec("atr", "15m", p.atr_trail_mult, int(p.atr_n), False))
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="15m", price=price, stop=stop,
            tps_r=(),                                   # deliberately none: let the trail decide
            be_at_r=p.be_at_r, trail=trail,
            max_hold_s=int(p.max_hold_s) or None, valid_bars=2,
            reason=f"{side} trend, slope {slope_pct:+.2f}%, pullback to the fast EMA",
            meta={"slope_pct": round(slope_pct, 3), "fast": fast, "slow": slow,
                  "stop_pct": round(abs(price - stop) / price * 100, 3)})]

    def state(self) -> dict[str, Any]:
        return {"last_setup": self._last, **self._stats}

    def reset(self) -> None:
        self._last.clear()
        for k in self._stats:
            self._stats[k] = 0
