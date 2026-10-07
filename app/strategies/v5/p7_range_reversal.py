"""V5.7 - Structural range reversal with positioning.

Hypothesis: when the slow context is flat (a range regime), a test of the multi-day range boundary that is rejected
(the signal bar closes back inside with a wick) WHILE positioning is crowded into the boundary (funding in its extreme
quintile toward the break, or OI up >= 5% in a day) is a failed breakout attempt by a crowded side; price reverts
toward the range middle.
Expected hold 8-48 h; ~2-8 trades/month hourly, ~1-4 swing; seeks 3-6%.
Fails on real breakouts.
New vs V1-V4: V3 S34 / V4.6 used 1m-30m oscillator-style ranges without positioning and lost gross; V5.7 uses a 7-day
(SWING 30-day) structure, a flat slow regime and a positioning condition.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v5.base import Setup, V5Strategy, close_location, scale


@dataclass
class Params:
    range_days: float = P(7.0, min=2.0, max=60.0, step=1.0, label="range window (days; swing x4)")
    edge_frac: float = P(0.10, min=0.02, max=0.3, step=0.01, label="boundary zone (share of range)")
    crowd_pct: float = P(0.80, min=0.5, max=0.99, step=0.01, label="funding percentile toward the break >=")
    oi_rise: float = P(0.05, min=0.0, max=0.3, step=0.01, label="or OI change 24h >=")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (signal bars)")


class RangeReversalV5(V5Strategy):
    id = "V5.7"
    name = "Structural Range Reversal v5"
    family = "STRUCTURAL_RANGE_REVERSAL"
    hypothesis = "in a flat regime, a rejected test of the range boundary by a crowded side reverts toward the middle"
    thesis = "flat slow context, multi-day range boundary rejected, positioning crowded into the boundary"
    fails_when = "real breakouts"
    expected_hold = "8-48 h"
    expected_frequency = "hourly ~2-8 / month, swing ~1-4 / month"
    why_new = "V1-V4 range fades were 1m-30m and ignored positioning"
    Params = Params
    doc = StrategyDoc(
        idea="In a range regime, fade a rejected boundary test when the side pushing the boundary is crowded.",
        timeframe="1h or 4h signal; 1m execution", symbols="one coin per bot",
        entry="slow context flat; the signal bar reaches the outer 10% of the 7-day (swing 28-day) range and closes back "
              "inside in its opposite half; funding percentile >= 0.80 toward the break (<= 0.20 at the floor) or OI +5% 24 h",
        stop="beyond the bar's extreme + 0.25 ATR, 0.8%-8%", targets="time stop; DEVELOPMENT-selected exit after Stage 2",
        sizing="AGGRESSIVE_V5 TAKE 1%", why_aggressive="range edges give precise, structural stops")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any]) -> Setup | None:
        p = self.params
        if self.ctx_trend(ctx, c.symbol, self.ctx_slow) != "flat":
            return None
        c1 = list(ctx.candles(c.symbol, self.ctx_fast))
        per_day = 6 if self.horizon == "HOURLY" else 1
        n = int(round(p.range_days * (1.0 if self.horizon == "HOURLY" else 4.0) * per_day))
        if len(c1) < n + 1:
            return None
        rng = c1[-n - 1:-1]
        hi, lo = max(x.high for x in rng), min(x.low for x in rng)
        width = hi - lo
        if width <= 0 or c.high <= c.low:
            return None
        fp, oi = pos.get("funding_pct_90d"), pos.get("oi_chg_24h")
        oi_crowd = oi is not None and oi >= p.oi_rise
        cl = close_location(c)
        if c.high >= hi - p.edge_frac * width and c.close < hi and cl <= 0.5:
            if oi_crowd or (fp is not None and fp >= p.crowd_pct):
                return Setup("short", max(c.high, hi), {"rejection": scale(1.0 - cl, 0.5, 1.0),
                                                        "crowd": scale(fp or 0.0, p.crowd_pct, 1.0)}, "range top rejected, longs crowded",
                             expected_move_pct=(c.close - (hi + lo) / 2.0) / c.close)
        if c.low <= lo + p.edge_frac * width and c.close > lo and cl >= 0.5:
            if oi_crowd or (fp is not None and fp <= 1.0 - p.crowd_pct):
                return Setup("long", min(c.low, lo), {"rejection": scale(cl, 0.5, 1.0),
                                                      "crowd": scale(1.0 - (fp if fp is not None else 1.0), p.crowd_pct, 1.0)},
                             "range floor rejected, shorts crowded", expected_move_pct=((hi + lo) / 2.0 - c.close) / c.close)
        return None
