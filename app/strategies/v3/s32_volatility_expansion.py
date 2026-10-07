"""S32 v3 - Volatility Expansion (squeeze release).

Hypothesis: after a Bollinger-inside-Keltner squeeze (stored energy), the FIRST wide, decisive bar
out of it starts a directional move large enough to pay costs, unless the slow context opposes it.

Entry:   >= `squeeze_min` of the previous 10 bars in a squeeze (Bollinger 20/2 width < Keltner
         `kc_mult` x ATR width); this bar's range >= `expansion_atr` x the ATR before it; close in
         the outer `close_loc` of the bar; slow context not opposite.
Stop:    max(0.6 x the expansion bar's range, 1 ATR), clamped to 0.5%..2.0%.
Exits:   50% at 1.5R, break-even at 1R, 2 ATR trail, 36 bars max.
Expected move: min(first target, the expansion bar's own range) -- a release typically extends by
         about one expansion bar.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v3.base import V3Strategy, close_location, scale


@dataclass
class Params:
    bb_n: int = P(20, min=10, max=50, step=1, label="Bollinger length")
    bb_k: float = P(2.0, min=1.0, max=3.0, step=0.1, label="Bollinger width (sd)")
    kc_mult: float = P(1.5, min=0.5, max=3.0, step=0.1, label="Keltner width (ATR)")
    squeeze_min: int = P(6, min=1, max=10, step=1, label="squeeze bars of the last 10")
    expansion_atr: float = P(1.8, min=1.0, max=4.0, step=0.1, label="expansion bar range (ATR)")
    close_loc: float = P(0.75, min=0.5, max=1.0, step=0.05, label="close in the outer part of the bar")
    tp1_r: float = P(1.5, min=1.0, max=5.0, step=0.1, label="first target (R)")
    tp1_frac: float = P(0.5, min=0.0, max=1.0, step=0.05, label="first target size")
    trail_atr: float = P(2.0, min=1.0, max=6.0, step=0.1, label="trail (ATR)")
    be_at_r: float = P(1.0, min=0.0, max=3.0, step=0.1, label="break-even at (R)")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (bars)")


class VolatilityExpansionV3(V3Strategy):
    id = "S32"
    name = "Volatility Expansion v3"
    family = "VOLATILITY_EXPANSION"
    hypothesis = "the first decisive bar out of a volatility squeeze starts a move that beats costs"
    Params = Params
    max_hold_bars = 36
    doc = StrategyDoc(
        idea="Trade the first wide, decisive bar out of a Bollinger-inside-Keltner squeeze.",
        timeframe="3m / 5m / 15m (30m benchmark) with 15m+1h or 1h+4h context; 1m execution",
        symbols="one coin per bot",
        entry=">= 6 of the last 10 bars squeezed; bar range >= 1.8 ATR; close in its outer 25%; "
              "slow context not opposite",
        stop="max(0.6 x bar range, 1 ATR), 0.5%-2.0%",
        targets="50% at 1.5R, break-even at 1R, 2 ATR trail, 36 bars max",
        sizing="AGGRESSIVE_V3 tiers on signal quality and edge-to-cost; Jev V2 on the +JEV twin",
        why_aggressive="every squeeze release is a candidate; squeezes are frequent on fast timeframes",
    )

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        p, tf = self.params, self.signal_tf
        cs = self.series(ctx, c.symbol, tf)
        if len(cs) < 60:
            return []
        atr_s = ctx.ind(c.symbol, tf, "atr", n=14)
        _, upper, lower, _ = ctx.ind(c.symbol, tf, "bollinger", n=int(p.bb_n), k=float(p.bb_k))
        atr_prev = atr_s[-2] if len(atr_s) > 1 else None
        if not atr_prev:
            return []
        squeezed = 0
        for i in range(len(cs) - 11, len(cs) - 1):
            u, lw, a = upper[i], lower[i], atr_s[i]
            if None not in (u, lw, a) and (u - lw) < 2.0 * p.kc_mult * a:
                squeezed += 1
        if squeezed < p.squeeze_min:
            return []
        rng = c.high - c.low
        if rng < p.expansion_atr * atr_prev:
            return []
        cl = close_location(c)
        side = "long" if cl >= p.close_loc else "short" if cl <= 1.0 - p.close_loc else None
        if side is None:
            return []
        slow = self.trend(ctx, c.symbol, self.ctx_slow)
        if slow is None or slow["direction"] == ("down" if side == "long" else "up"):
            return []
        price = ctx.last_price(c.symbol) or c.close
        sp = self.stop_pct(price, max(0.6 * rng, atr_prev))
        if sp is None:
            return []
        vr = self.volume_ratio(cs) or 0.0
        want = "up" if side == "long" else "down"
        outer = cl if side == "long" else 1.0 - cl
        factors = {"squeeze": scale(squeezed, p.squeeze_min, 10), "expansion": scale(rng / atr_prev, p.expansion_atr, 3.5),
                   "close": scale(outer, p.close_loc, 1.0), "volume": scale(vr, 1.2, 3.0),
                   "context": 1.0 if slow["direction"] == want else 0.5}
        return [self.entry(c=c, ctx=ctx, side=side, price=price, stop_pct=sp,
                           tps_r=[(p.tp1_r, p.tp1_frac)], trail_atr=p.trail_atr, be_at_r=p.be_at_r,
                           expected_move_pct=min(p.tp1_r * sp, rng / price),
                           expected_move_source="strategy: min(1.5R first target, expansion bar range)",
                           factors=factors,
                           reason=f"{tf} squeeze release ({squeezed}/10 squeezed, {rng / atr_prev:.1f} ATR bar)",
                           extra={"context": {"slow": slow["direction"]}, "squeeze_bars": squeezed,
                                  "expansion_atr": round(rng / atr_prev, 3), "volume_ratio": round(vr, 3)})]
