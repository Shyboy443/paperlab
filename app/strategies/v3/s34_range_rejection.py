"""S34 v3 - Range Rejection.

Hypothesis: while the slow context is flat, price rotates inside a validated range; a rejection wick
at a range extreme that has already been tested, with a close back inside, reverts toward the range
midpoint -- a move known in advance, which is what the cost gate needs.

Entry:   slow context flat (|EMA50 slope| <= `flat_slope`); the prior `range_n`-bar range is at least
         `min_height_atr` ATR tall and its extreme was touched >= `min_touches` times; this bar pierces
         the extreme (within `pierce_atr` ATR), closes back inside, with a wick >= `wick_frac` of its
         range, and closes in the inner half of the bar.
Stop:    beyond the wick extreme by `stop_buffer_atr` ATR, clamped to 0.5%..2.0%.
Exits:   70% at the range midpoint, 30% at the opposite quarter, break-even at 0.8R, 48 bars max.
Expected move: the distance to the range midpoint (the objective itself).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v3.base import V3Strategy, close_location, scale


@dataclass
class Params:
    range_n: int = P(48, min=20, max=200, step=1, label="range lookback (bars)")
    flat_slope: float = P(0.003, min=0.0005, max=0.02, step=0.0005, label="max |slow EMA50 slope| (5 bars)")
    min_height_atr: float = P(4.0, min=2.0, max=15.0, step=0.5, label="min range height (ATR)")
    min_touches: int = P(2, min=1, max=6, step=1, label="prior touches of the extreme")
    pierce_atr: float = P(0.1, min=0.0, max=1.0, step=0.05, label="reach of the extreme (ATR)")
    wick_frac: float = P(0.5, min=0.2, max=0.9, step=0.05, label="min rejection wick (of bar range)")
    stop_buffer_atr: float = P(0.3, min=0.0, max=2.0, step=0.1, label="stop beyond the wick (ATR)")
    mid_frac: float = P(0.7, min=0.1, max=1.0, step=0.05, label="size taken at the midpoint")
    be_at_r: float = P(0.8, min=0.0, max=3.0, step=0.1, label="break-even at (R)")
    cooldown_bars: int = P(6, min=0, max=50, step=1, label="cooldown (bars)")


class RangeRejectionV3(V3Strategy):
    id = "S34"
    name = "Range Rejection v3"
    family = "RANGE_REJECTION"
    hypothesis = "in a flat context, a rejection wick at a tested range extreme reverts to the range midpoint"
    Params = Params
    max_hold_bars = 48
    doc = StrategyDoc(
        idea="Fade a rejected test of a validated range extreme while the context is flat.",
        timeframe="3m / 5m / 15m (30m benchmark) with 15m+1h or 1h+4h context; 1m execution",
        symbols="one coin per bot",
        entry="flat slow context; 48-bar range >= 4 ATR tall, extreme touched twice before; wick >= 50% "
              "of the bar, close back inside and in the bar's inner half",
        stop="beyond the wick + 0.3 ATR, 0.5%-2.0%",
        targets="70% at the range midpoint, 30% at the opposite quarter, break-even at 0.8R, 48 bars max",
        sizing="AGGRESSIVE_V3 tiers on signal quality and edge-to-cost; Jev V2 on the +JEV twin",
        why_aggressive="trades both edges of every range the context allows",
    )

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        p, tf = self.params, self.signal_tf
        n = int(p.range_n)
        cs = self.series(ctx, c.symbol, tf)
        if len(cs) < n + 20:
            return []
        slow = self.trend(ctx, c.symbol, self.ctx_slow)
        if slow is None or slow["direction"] != "flat" or abs(slow["slope"]) > p.flat_slope:
            return []
        atr = self.atr(ctx, c.symbol, tf)
        if not atr:
            return []
        prior = cs[-n - 1:-1]
        hi, lo = max(x.high for x in prior), min(x.low for x in prior)
        height = hi - lo
        if height < p.min_height_atr * atr:
            return []
        rng = c.high - c.low
        if rng <= 0:
            return []
        cl = close_location(c)
        side = None
        if c.high >= hi - p.pierce_atr * atr and c.close < hi and cl <= 0.5:
            wick = c.high - max(c.open, c.close)
            touches = sum(1 for x in prior if x.high >= hi - 0.25 * atr)
            if wick >= p.wick_frac * rng and touches >= p.min_touches:
                side, extreme = "short", c.high
        elif c.low <= lo + p.pierce_atr * atr and c.close > lo and cl >= 0.5:
            wick = min(c.open, c.close) - c.low
            touches = sum(1 for x in prior if x.low <= lo + 0.25 * atr)
            if wick >= p.wick_frac * rng and touches >= p.min_touches:
                side, extreme = "long", c.low
        if side is None:
            return []
        price = ctx.last_price(c.symbol) or c.close
        mid = (hi + lo) / 2.0
        gap = (mid - price) if side == "long" else (price - mid)
        if gap <= 0:
            return []
        sp = self.stop_pct(price, abs(extreme - price) + p.stop_buffer_atr * atr)
        if sp is None:
            return []
        quarter = lo + 0.75 * height if side == "long" else lo + 0.25 * height
        vr = self.volume_ratio(cs) or 0.0
        factors = {"flatness": max(0.0, 1.0 - abs(slow["slope"]) / p.flat_slope),
                   "wick": scale(wick / rng, p.wick_frac, 0.8), "touches": scale(touches, p.min_touches, 5),
                   "room": scale(height / atr, p.min_height_atr, 10.0), "volume": scale(vr, 1.0, 2.5)}
        return [self.entry(c=c, ctx=ctx, side=side, price=price, stop_pct=sp,
                           tps_price=[(mid, p.mid_frac), (quarter, round(1.0 - p.mid_frac, 6))],
                           be_at_r=p.be_at_r, expected_move_pct=gap / price,
                           expected_move_source="strategy: distance to the range midpoint",
                           factors=factors,
                           reason=f"{tf} rejection at the {n}-bar range {'low' if side == 'long' else 'high'} in a flat {self.ctx_slow}",
                           extra={"context": {"slow": slow["direction"]}, "range_height_pct": round(height / price, 6),
                                  "touches": touches})]
