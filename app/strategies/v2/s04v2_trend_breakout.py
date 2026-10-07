"""S04 v2 - Trend Breakout. Revised S04 Donchian: fewer, larger breakouts, with the trend.

v1 broke a 12-bar channel on 5m bars in either direction and paid a round trip on every wiggle.
v2 hypothesis: a breakout is worth its costs only when (a) the higher timeframe already trends the
same way, (b) volatility was compressed before it (energy stored, not already spent), and (c) volume
confirms. Everything else is skipped.

Entry:   close beyond the prior `channel`-bar high/low, context trend in the same direction, ATR
         percentile over the last 100 bars below `max_atr_rank` before the break, volume >=
         `min_volume_ratio` x its 20-bar average.
Stop:    max(`stop_atr` x ATR, `min_stop_pct` of price) beyond entry.
Exits:   40% at 2R, break-even at 1R, the rest on a 3 ATR trail after the first target.
Quality: mean of trend strength, volume confirmation, prior compression and breakout-bar body.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v2.base import V2Strategy, clamp01, scale


@dataclass
class Params:
    channel: int = P(24, min=10, max=100, step=1, label="breakout channel (bars)")
    atr_n: int = P(14, min=5, max=50, step=1, label="ATR length")
    max_atr_rank: float = P(0.60, min=0.1, max=1.0, step=0.05, label="max ATR percentile before the break")
    min_volume_ratio: float = P(1.3, min=1.0, max=5.0, step=0.1, label="min volume vs 20-bar mean")
    stop_atr: float = P(1.5, min=0.5, max=5.0, step=0.1, label="stop (ATR)")
    min_stop_pct: float = P(0.006, min=0.002, max=0.05, step=0.001, label="min stop (fraction of price)")
    tp1_r: float = P(2.0, min=1.0, max=6.0, step=0.1, label="first target (R)")
    tp1_frac: float = P(0.4, min=0.0, max=1.0, step=0.05, label="first target size")
    trail_atr: float = P(3.0, min=1.0, max=8.0, step=0.1, label="trail (ATR)")
    be_at_r: float = P(1.0, min=0.0, max=4.0, step=0.1, label="break-even at (R)")
    cooldown_bars: int = P(6, min=0, max=100, step=1, label="cooldown (bars)")


class TrendBreakoutV2(V2Strategy):
    id = "S04"
    name = "Trend Breakout v2"
    Params = Params
    doc = StrategyDoc(
        idea="Breakouts only with the higher-timeframe trend, out of compression, on volume.",
        timeframe="5m / 15m / 30m (one per bot) with a 1h or 4h trend context",
        symbols="one coin per bot",
        entry="close beyond the prior 24-bar channel; context trend aligned; ATR in its lower 60% "
              "before the break; volume >= 1.3x its mean",
        stop="max(1.5 ATR, 0.6% of price)",
        targets="40% at 2R, break-even at 1R, remainder on a 3 ATR trail",
        sizing="risk from the stop; ATTACK only on high signal quality and a large edge-to-cost ratio",
        why_aggressive="few, large, trend-aligned moves; costs are a small fraction of each R",
    )
    warmup_bars = 150
    max_positions = 1

    def cooldown_ms(self) -> int:
        from app.core.types import tf_ms
        return int(self.params.cooldown_bars) * (tf_ms(self.signal_tf) if self.signal_tf else 60_000)

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        p, tf = self.params, self.signal_tf
        candles = list(ctx.candles(c.symbol, tf))       # a deque: list() before slicing
        if len(candles) < max(int(p.channel), 100) + 5:
            return []
        upper, lower = ctx.ind(c.symbol, tf, "donchian", n=int(p.channel))
        up, lo = last(upper), last(lower)
        atr_s = ctx.ind(c.symbol, tf, "atr", n=int(p.atr_n))
        atr, atr_prev = last(atr_s), last(atr_s, 1)
        if None in (up, lo, atr, atr_prev) or atr <= 0:
            return []
        side = "long" if c.close > up else "short" if c.close < lo else None
        if side is None:
            return []
        tr = self.trend(ctx, c.symbol)
        if tr is None or tr["direction"] != ("up" if side == "long" else "down"):
            return []
        window = [a for a in atr_s[-101:-1] if a is not None]
        rank = sum(1 for a in window if a <= atr_prev) / len(window) if window else 1.0
        if rank > p.max_atr_rank:
            return []
        vr = self.volume_ratio(candles)
        if vr is None or vr < p.min_volume_ratio:
            return []
        price = ctx.last_price(c.symbol) or c.close
        dist = max(p.stop_atr * atr, p.min_stop_pct * price)
        stop = price - dist if side == "long" else price + dist
        body = abs(c.close - c.open) / (c.high - c.low) if c.high > c.low else 0.0
        factors = {"trend": scale(abs(tr["slope"]), 0.0, 0.01), "volume": scale(vr, 1.0, 2.5),
                   "compression": clamp01(1.0 - rank), "body": scale(body, 0.4, 0.9)}
        quality = sum(factors.values()) / len(factors)
        trail = TrailSpec("atr", tf, p.trail_atr, int(p.atr_n), True)
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf=tf, price=price, stop=stop,
            tps_r=[(p.tp1_r, p.tp1_frac)], trail=trail, be_at_r=p.be_at_r,
            reason=f"{tf} breakout of {int(p.channel)}-bar channel with the {self.context_tf} trend",
            meta={"signal_quality": round(quality, 4), "quality_factors": {k: round(v, 3) for k, v in factors.items()},
                  "expected_move_pct": self.expected_move(price, stop, p.tp1_r, atr),
                  "expected_move_source": "strategy: min(2R target, 1.5 ATR)"})]
