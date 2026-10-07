"""V4.6 - Trend-aligned range rejection.

Hypothesis: inside a 1h uptrend, a sideways structure range tends to resolve upward; a bar that probes
the range floor and is rejected (long lower wick, strong close) marks trend buyers defending value, and
the range floor is the stop (mirror in a downtrend).

Evidence (DEVELOPMENT only): V3's S34 range rejection faded BOTH edges regardless of trend and lost
gross on every timeframe (-2.7 to -11 bps of turnover). V4 takes only the rejection on the trend's side.
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
    max_width_atr: float = P(5.0, min=2.0, max=10.0, step=0.5, label="range width <= (ATR)")
    min_width_atr: float = P(1.5, min=0.5, max=5.0, step=0.5, label="range width >= (ATR)")
    probe_frac: float = P(0.25, min=0.05, max=0.5, step=0.05, label="probe into the outer (share of range)")
    min_wick: float = P(0.5, min=0.2, max=0.9, step=0.05, label="rejection wick >= (share of bar)")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (trigger bars)")


class TrendRangeRejectionV4(V4Strategy):
    id = "V4.6"
    name = "Trend Range Rejection v4"
    family = "TREND_RANGE_REJECTION"
    hypothesis = "in a 1h trend, a rejected probe of the range edge on the trend's side marks value being defended"
    thesis = "sideways structure range inside a 1h trend, rejection wick at the trend-side edge"
    evidence = "V3 S34 faded both range edges without a trend and lost gross; V4 keeps only the trend side"
    expected_frequency = "~0.2-0.6 setups per coin-day on 15m"
    Params = Params
    doc = StrategyDoc(
        idea="In a 1h uptrend, buy a rejected probe of the 15m range floor (mirror in a downtrend).",
        timeframe="3m / 5m / 15m / 30m trigger; 15m (30m) structure; 1h trend; 1m execution",
        symbols="one coin per bot",
        entry="the 24-bar range is 1.5-5 ATR wide; the structure bar probes its outer 25% on the trend's side, "
              "closes inside with a wick >= 50% of its range and a close in the outer 40%; 3m / 5m: a resuming trigger bar",
        stop="beyond the rejection bar's extreme + 0.25 ATR, 0.6%-2.5%",
        targets="50% at 1.5R, break-even at 1.5R, runner trailed 2 x ATR(structure), 180 / 240 min max",
        sizing="AGGRESSIVE_V4 behind the expected-edge gate; Jev V4 on the +JEV twin",
        why_aggressive="the range edge is a tight, well-defined stop in the trend's favour")

    def setup(self, ctx: MarketContext, symbol: str, cs: Sequence[Candle], atr: float, trend: Any) -> Setup | None:
        p = self.params
        if not trend or trend["direction"] == "flat":
            return None
        up = trend["direction"] == "up"
        bars = list(cs)
        n = int(p.range_bars)
        if len(bars) < n + 2:
            return None
        c = bars[-1]
        hi, lo = self.box(bars, n, skip=1)
        width = hi - lo
        if not (p.min_width_atr * atr <= width <= p.max_width_atr * atr) or c.high <= c.low:
            return None
        rng = c.high - c.low
        if up:
            probe = c.low <= lo + p.probe_frac * width
            wick = (min(c.open, c.close) - c.low) / rng
            cl = close_location(c)
            ok = probe and c.close > lo and wick >= p.min_wick and cl >= 0.6
            extreme = c.low
        else:
            probe = c.high >= hi - p.probe_frac * width
            wick = (c.high - max(c.open, c.close)) / rng
            cl = 1.0 - close_location(c)
            ok = probe and c.close < hi and wick >= p.min_wick and cl >= 0.6
            extreme = c.high
        if not ok:
            return None
        return Setup("long" if up else "short", c.close_time, extreme,
                     {"wick": scale(wick, p.min_wick, 1.0), "close": scale(cl, 0.6, 1.0)}, "range edge rejected")
