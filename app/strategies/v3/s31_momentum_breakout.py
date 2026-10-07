"""S31 v3 - Momentum Breakout (multi-timeframe).

Hypothesis: a break of the recent signal-timeframe range that happens out of compressed volatility,
in the direction of the slow context trend (and not against the fast one), on a volume surge,
extends far enough to pay a Bybit round trip several times over.

Entry:   close beyond the prior `channel`-bar high/low; slow context trend aligned; fast context not
         opposed; ATR before the break in its lower `compress_rank` of the last 100 bars; volume >=
         `min_volume_ratio` x its 20-bar mean; the bar closes in the breakout's top/bottom 40%.
Stop:    max(`stop_atr` x ATR, back inside the broken level by 0.5 ATR), clamped to 0.5%..2.0%.
Exits:   50% at 1.5R, break-even at 1R, the rest on a 2.5 ATR trail, 48 bars max.
Expected move: min(first target, height of the broken range) -- a breakout's measured move.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v3.base import V3Strategy, clamp01, close_location, scale


@dataclass
class Params:
    channel: int = P(20, min=8, max=100, step=1, label="breakout range (bars)")
    compress_rank: float = P(0.60, min=0.1, max=1.0, step=0.05, label="max ATR percentile before the break")
    min_volume_ratio: float = P(1.5, min=1.0, max=5.0, step=0.1, label="min volume vs 20-bar mean")
    stop_atr: float = P(1.2, min=0.5, max=4.0, step=0.1, label="stop (ATR)")
    tp1_r: float = P(1.5, min=1.0, max=5.0, step=0.1, label="first target (R)")
    tp1_frac: float = P(0.5, min=0.0, max=1.0, step=0.05, label="first target size")
    trail_atr: float = P(2.5, min=1.0, max=6.0, step=0.1, label="trail (ATR)")
    be_at_r: float = P(1.0, min=0.0, max=3.0, step=0.1, label="break-even at (R)")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (bars)")


class MomentumBreakoutV3(V3Strategy):
    id = "S31"
    name = "Momentum Breakout v3"
    family = "MOMENTUM_BREAKOUT"
    hypothesis = ("a compressed-range breakout with the context trend and a volume surge extends far "
                  "enough to beat costs")
    Params = Params
    max_hold_bars = 48
    doc = StrategyDoc(
        idea="Break of a compressed signal-TF range, with the slow context trend, on volume.",
        timeframe="3m / 5m / 15m (30m benchmark) with 15m+1h or 1h+4h context; 1m execution",
        symbols="one coin per bot",
        entry="close beyond the prior 20-bar range; slow trend aligned; ATR in its lower 60% before "
              "the break; volume >= 1.5x; close in the breakout's outer 40%",
        stop="max(1.2 ATR, 0.5 ATR back inside the broken level), 0.5%-2.0%",
        targets="50% at 1.5R, break-even at 1R, 2.5 ATR trail, 48 bars max",
        sizing="AGGRESSIVE_V3 tiers on signal quality and edge-to-cost; Jev V2 on the +JEV twin",
        why_aggressive="fires on every qualifying breakout on a fast timeframe",
    )

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        p, tf = self.params, self.signal_tf
        n = int(p.channel)
        cs = self.series(ctx, c.symbol, tf)
        if len(cs) < max(n, 100) + 5:
            return []
        prior = cs[-n - 1:-1]
        hi, lo = max(x.high for x in prior), min(x.low for x in prior)
        side = "long" if c.close > hi else "short" if c.close < lo else None
        if side is None:
            return []
        want = "up" if side == "long" else "down"
        slow = self.trend(ctx, c.symbol, self.ctx_slow)
        fast = self.trend(ctx, c.symbol, self.ctx_fast)
        if slow is None or fast is None or slow["direction"] != want:
            return []
        if fast["direction"] not in (want, "flat"):
            return []
        atr_s = ctx.ind(c.symbol, tf, "atr", n=14)
        atr, atr_prev = atr_s[-1] if atr_s else None, atr_s[-2] if len(atr_s) > 1 else None
        if not atr or not atr_prev:
            return []
        window = [a for a in atr_s[-101:-1] if a is not None]
        rank = sum(1 for a in window if a <= atr_prev) / len(window) if window else 1.0
        if rank > p.compress_rank:
            return []
        vr = self.volume_ratio(cs)
        if vr is None or vr < p.min_volume_ratio:
            return []
        cl = close_location(c) if side == "long" else 1.0 - close_location(c)
        if cl < 0.6:
            return []
        price = ctx.last_price(c.symbol) or c.close
        level = hi if side == "long" else lo
        raw = max(p.stop_atr * atr, abs(price - level) + 0.5 * atr)
        sp = self.stop_pct(price, raw)
        if sp is None:
            return []
        height = (hi - lo) / price
        factors = {"trend": scale(abs(slow["slope"]), 0.0, 0.01), "compression": clamp01(1.0 - rank),
                   "volume": scale(vr, p.min_volume_ratio, 3.5), "close": scale(cl, 0.6, 1.0),
                   "fast_context": 1.0 if fast["direction"] == want else 0.5}
        return [self.entry(c=c, ctx=ctx, side=side, price=price, stop_pct=sp,
                           tps_r=[(p.tp1_r, p.tp1_frac)], trail_atr=p.trail_atr, be_at_r=p.be_at_r,
                           expected_move_pct=min(p.tp1_r * sp, height),
                           expected_move_source="strategy: min(1.5R first target, height of the broken range)",
                           factors=factors,
                           reason=f"{tf} {n}-bar breakout out of compression with the {self.ctx_slow} trend",
                           extra={"context": {"slow": slow["direction"], "fast": fast["direction"]},
                                  "range_height_pct": round(height, 6), "volume_ratio": round(vr, 3)})]
