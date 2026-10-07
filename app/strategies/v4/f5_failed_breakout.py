"""V4.5 - Failed-breakout reversal.

Hypothesis: a structure bar that pierces the range extreme but closes back inside has trapped the
breakout traders; when the next bar confirms (closes below the failed bar's midpoint), their exits push
price back into the range. Only taken when the 1h trend does NOT support the breakout -- a failed
breakout against a trend that wants it is often just a pause.

This is the one V4 family that may trade against the 1h trend (it needs the trend to be flat or
opposed to the breakout), so it is NOT trend-aligned by construction.

Evidence (DEVELOPMENT only): every V3 fade (S34 range rejection, S36 fast mean reversion) was
gross-negative -- fading generic extremes was wrong-way. This family carries the burden of proof: it
fades only a specific, confirmed trap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v4.base import Setup, V4Strategy, close_location, scale


@dataclass
class Params:
    range_bars: int = P(24, min=8, max=100, step=1, label="range (bars)")
    pierce_atr: float = P(0.15, min=0.0, max=1.0, step=0.05, label="pierce beyond the extreme (ATR)")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (trigger bars)")


class FailedBreakoutV4(V4Strategy):
    id = "V4.5"
    name = "Failed Breakout Reversal v4"
    family = "FAILED_BREAKOUT_REVERSAL"
    trend_aligned = False
    hypothesis = "a pierce of the range extreme that closes back inside traps breakout traders; their exits reverse it"
    thesis = "failed breakout (pierce + close back inside), confirmation bar, 1h trend not supporting the breakout"
    evidence = "generic fades were wrong-way in DEV; V4 fades only a confirmed trap against a non-supporting trend"
    expected_frequency = "~0.3-0.8 setups per coin-day on 15m"
    Params = Params
    doc = StrategyDoc(
        idea="Sell a failed upside breakout once the next bar confirms it (mirror for downside failures).",
        timeframe="3m / 5m / 15m / 30m trigger; 15m (30m) structure; 1h trend as a filter; 1m execution",
        symbols="one coin per bot",
        entry="the previous structure bar's high pierced the 24-bar high by >= 0.15 ATR and closed back below it; the "
              "structure bar closes below that bar's midpoint; the 1h trend is not up; 3m / 5m: a resuming trigger bar",
        stop="beyond the failed bar's extreme + 0.25 ATR, 0.6%-2.5%",
        targets="50% at 1.5R, break-even at 1.5R, runner trailed 2 x ATR(structure), 180 / 240 min max",
        sizing="AGGRESSIVE_V4 behind the expected-edge gate; Jev V4 on the +JEV twin",
        why_aggressive="the trap's extreme is a precise stop; the unwind is fast")

    def setup(self, ctx: MarketContext, symbol: str, cs: Sequence[Candle], atr: float, trend: Any) -> Setup | None:
        p = self.params
        if not trend:
            return None
        bars = list(cs)
        n = int(p.range_bars)
        if len(bars) < n + 3:
            return None
        c, failed = bars[-1], bars[-2]
        hi, lo = self.box(bars, n, skip=2)
        mid = (failed.high + failed.low) / 2.0
        d = trend["direction"]
        if d != "up" and failed.high >= hi + p.pierce_atr * atr and failed.close < hi and c.close < mid and c.close < hi:
            cl = 1.0 - close_location(c)
            return Setup("short", c.close_time, max(failed.high, c.high),
                         {"pierce": scale((failed.high - hi) / atr, p.pierce_atr, 1.0), "close": scale(cl, 0.5, 1.0)},
                         "upside breakout failed")
        if d != "down" and failed.low <= lo - p.pierce_atr * atr and failed.close > lo and c.close > mid and c.close > lo:
            cl = close_location(c)
            return Setup("long", c.close_time, min(failed.low, c.low),
                         {"pierce": scale((lo - failed.low) / atr, p.pierce_atr, 1.0), "close": scale(cl, 0.5, 1.0)},
                         "downside breakout failed")
        return None
