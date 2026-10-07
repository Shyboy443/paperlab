"""S36 v3 - Fast Mean Reversion.

Hypothesis: a short-term stretch far from the signal-timeframe mean (more than `ext_atr` ATR from the
EMA, RSI at an extreme) that the slow context does not support, closing off its extreme, snaps back
toward the mean -- whose distance is the move this trade expects.

Entry:   (close - EMA) / ATR >= `ext_atr` for a short (<= -`ext_atr` for a long); fast RSI >= `rsi_hi`
         (<= `rsi_lo`); the bar closes off its extreme (close location <= `exhaustion_cl` for a short);
         the slow context is not trending with the stretch.
Stop:    beyond the bar's extreme by `stop_buffer_atr` ATR, clamped to 0.5%..2.0%.
Exits:   70% at the EMA (the mean), break-even at 0.8R, 1.5 ATR trail on the rest, 24 bars max.
Expected move: the distance from entry to the EMA.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v3.base import V3Strategy, close_location, scale


@dataclass
class Params:
    ema_n: int = P(20, min=5, max=100, step=1, label="mean (EMA)")
    ext_atr: float = P(2.5, min=1.0, max=6.0, step=0.1, label="min stretch from the EMA (ATR)")
    rsi_n: int = P(7, min=2, max=21, step=1, label="fast RSI length")
    rsi_hi: float = P(85.0, min=60.0, max=99.0, step=1.0, label="RSI extreme (shorts)")
    rsi_lo: float = P(15.0, min=1.0, max=40.0, step=1.0, label="RSI extreme (longs)")
    exhaustion_cl: float = P(0.6, min=0.1, max=1.0, step=0.05, label="close off the extreme")
    stop_buffer_atr: float = P(0.5, min=0.0, max=2.0, step=0.1, label="stop beyond the extreme (ATR)")
    mean_frac: float = P(0.7, min=0.1, max=1.0, step=0.05, label="size taken at the mean")
    trail_atr: float = P(1.5, min=0.5, max=5.0, step=0.1, label="trail (ATR)")
    be_at_r: float = P(0.8, min=0.0, max=3.0, step=0.1, label="break-even at (R)")
    trend_slope_block: float = P(0.004, min=0.0, max=0.05, step=0.0005, label="slow slope that blocks a fade")
    cooldown_bars: int = P(4, min=0, max=50, step=1, label="cooldown (bars)")


class FastMeanReversionV3(V3Strategy):
    id = "S36"
    name = "Fast Mean Reversion v3"
    family = "FAST_MEAN_REVERSION"
    hypothesis = "an unsupported short-term stretch from the mean, closing off its extreme, reverts toward the mean"
    Params = Params
    max_hold_bars = 24
    doc = StrategyDoc(
        idea="Fade a stretch > 2.5 ATR from the EMA with an extreme fast RSI, unless the context trends with it.",
        timeframe="3m / 5m / 15m (30m benchmark) with 15m+1h or 1h+4h context; 1m execution",
        symbols="one coin per bot",
        entry="|close - EMA20| >= 2.5 ATR; RSI(7) >= 85 / <= 15; close off the extreme; slow context not "
              "trending with the stretch",
        stop="beyond the extreme + 0.5 ATR, 0.5%-2.0%",
        targets="70% at the EMA, break-even at 0.8R, 1.5 ATR trail, 24 bars max",
        sizing="AGGRESSIVE_V3 tiers on signal quality and edge-to-cost; Jev V2 on the +JEV twin",
        why_aggressive="stretches are frequent on fast timeframes and the target is known in advance",
    )

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        p, tf = self.params, self.signal_tf
        cs = self.series(ctx, c.symbol, tf)
        if len(cs) < 60:
            return []
        ema = ctx.ind(c.symbol, tf, "ema", n=int(p.ema_n))[-1]
        rsi = ctx.ind(c.symbol, tf, "rsi", n=int(p.rsi_n))[-1]
        atr = self.atr(ctx, c.symbol, tf)
        if ema is None or rsi is None or not atr:
            return []
        z = (c.close - ema) / atr
        cl = close_location(c)
        if z >= p.ext_atr and rsi >= p.rsi_hi and cl <= p.exhaustion_cl:
            side, extreme, off = "short", c.high, 1.0 - cl
        elif z <= -p.ext_atr and rsi <= p.rsi_lo and cl >= 1.0 - p.exhaustion_cl:
            side, extreme, off = "long", c.low, cl
        else:
            return []
        slow = self.trend(ctx, c.symbol, self.ctx_slow)
        if slow is None:
            return []
        with_stretch = "up" if side == "short" else "down"
        if slow["direction"] == with_stretch and abs(slow["slope"]) >= p.trend_slope_block:
            return []
        price = ctx.last_price(c.symbol) or c.close
        gap = (price - ema) if side == "short" else (ema - price)
        if gap <= 0:
            return []
        sp = self.stop_pct(price, abs(extreme - price) + p.stop_buffer_atr * atr)
        if sp is None:
            return []
        vr = self.volume_ratio(cs) or 0.0
        extremity = (rsi - p.rsi_hi) / (100.0 - p.rsi_hi) if side == "short" else (p.rsi_lo - rsi) / p.rsi_lo
        factors = {"stretch": scale(abs(z), p.ext_atr, 4.0), "rsi": scale(extremity, 0.0, 0.8),
                   "exhaustion": scale(off, 1.0 - p.exhaustion_cl, 0.8),
                   "neutral_context": 1.0 if slow["direction"] == "flat" else 0.5,
                   "climax": scale(vr, 1.5, 4.0)}
        return [self.entry(c=c, ctx=ctx, side=side, price=price, stop_pct=sp,
                           tps_price=[(ema, p.mean_frac)], trail_atr=p.trail_atr, be_at_r=p.be_at_r,
                           expected_move_pct=gap / price,
                           expected_move_source=f"strategy: distance to EMA{int(p.ema_n)} (the mean)",
                           factors=factors,
                           reason=f"{tf} {abs(z):.1f} ATR stretch from EMA{int(p.ema_n)}, RSI{int(p.rsi_n)} {rsi:.0f}",
                           extra={"context": {"slow": slow["direction"]}, "stretch_atr": round(z, 3),
                                  "rsi_fast": round(rsi, 2)})]
