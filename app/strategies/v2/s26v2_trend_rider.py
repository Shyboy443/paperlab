"""S26 v2 - Trend Rider. Revised S26: the same uncapped-winner shape, with a regime filter.

v1 already had the right exit shape (wide stop, no fixed target, loose trail) but entered every
EMA-stack pullback on 15m. v2 hypothesis: the shape pays only in a sustained trend at a normal
volatility level -- not in a dead market (moves too small for costs) and not in a blow-off (stops
too wide, reversals violent). So v2 requires context-trend agreement and an ATR percentile inside a
middle band, and grades the entry.

Entry:   context trend aligned; signal-timeframe EMA21 > EMA55 (<) with the slow EMA sloping;
         the bar pulled back within `pullback_atr` ATR of EMA21 and closed back in the trend
         direction; ATR percentile (100 bars) between `min_atr_rank` and `max_atr_rank`.
Stop:    max(`stop_atr` x ATR beyond the pullback extreme, `min_stop_pct` of price).
Exits:   25% at 3R, break-even at 1.5R, the rest on a 3 ATR trail; no time stop.
Quality: mean of context-trend strength, signal slope, volatility-band centrality and body.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec, tf_ms
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v2.base import V2Strategy, clamp01, scale


@dataclass
class Params:
    fast_ema: int = P(21, min=5, max=100, step=1, label="fast EMA")
    slow_ema: int = P(55, min=20, max=250, step=5, label="slow EMA")
    slope_bars: int = P(10, min=2, max=60, step=1, label="slope lookback (bars)")
    pullback_atr: float = P(0.75, min=0.1, max=4.0, step=0.05, label="pullback depth (ATR)")
    min_atr_rank: float = P(0.20, min=0.0, max=1.0, step=0.05, label="min ATR percentile")
    max_atr_rank: float = P(0.85, min=0.0, max=1.0, step=0.05, label="max ATR percentile")
    atr_n: int = P(14, min=5, max=50, step=1, label="ATR length")
    stop_atr: float = P(1.6, min=0.5, max=6.0, step=0.1, label="stop beyond the pullback (ATR)")
    min_stop_pct: float = P(0.008, min=0.002, max=0.05, step=0.001, label="min stop (fraction of price)")
    tp1_r: float = P(3.0, min=1.0, max=8.0, step=0.1, label="first target (R)")
    tp1_frac: float = P(0.25, min=0.0, max=1.0, step=0.05, label="first target size")
    trail_atr: float = P(3.0, min=1.0, max=10.0, step=0.1, label="trail (ATR)")
    be_at_r: float = P(1.5, min=0.0, max=6.0, step=0.1, label="break-even at (R)")
    cooldown_bars: int = P(8, min=0, max=100, step=1, label="cooldown (bars)")


class TrendRiderV2(V2Strategy):
    id = "S26"
    name = "Trend Rider v2"
    Params = Params
    doc = StrategyDoc(
        idea="Ride sustained trends at normal volatility with a wide stop, a small early partial and a loose trail.",
        timeframe="5m / 15m / 30m (one per bot) with a 1h or 4h trend context",
        symbols="one coin per bot",
        entry="context trend aligned; EMA21/EMA55 stacked with the slow EMA sloping; pullback to "
              "within 0.75 ATR of EMA21 and a close back with the trend; ATR percentile 20-85%",
        stop="max(1.6 ATR beyond the pullback, 0.8% of price)",
        targets="25% at 3R, break-even at 1.5R, remainder on a 3 ATR trail",
        sizing="risk from the stop; ATTACK only on high signal quality and a large edge-to-cost ratio",
        why_aggressive="uncapped winners in genuine trends; dead and blow-off markets are skipped",
    )
    warmup_bars = 150
    max_positions = 1

    def cooldown_ms(self) -> int:
        return int(self.params.cooldown_bars) * (tf_ms(self.signal_tf) if self.signal_tf else 60_000)

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        p, tf = self.params, self.signal_tf
        candles = list(ctx.candles(c.symbol, tf))       # a deque: list() before slicing
        if len(candles) < max(int(p.slow_ema) + int(p.slope_bars), 100) + 5:
            return []
        tr = self.trend(ctx, c.symbol)
        if tr is None or tr["direction"] == "flat":
            return []
        side = "long" if tr["direction"] == "up" else "short"
        f_s = ctx.ind(c.symbol, tf, "ema", n=int(p.fast_ema))
        s_s = ctx.ind(c.symbol, tf, "ema", n=int(p.slow_ema))
        atr_s = ctx.ind(c.symbol, tf, "atr", n=int(p.atr_n))
        f, s, s_then, atr = last(f_s), last(s_s), last(s_s, int(p.slope_bars)), last(atr_s)
        if None in (f, s, s_then, atr) or atr <= 0 or not s_then:
            return []
        slope = (s - s_then) / s_then
        if side == "long" and not (f > s and slope > 0):
            return []
        if side == "short" and not (f < s and slope < 0):
            return []
        window = [a for a in atr_s[-101:-1] if a is not None]
        rank = sum(1 for a in window if a <= atr) / len(window) if window else 0.5
        if not (p.min_atr_rank <= rank <= p.max_atr_rank):
            return []
        depth = p.pullback_atr * atr
        if side == "long":
            touched, resumed = c.low <= f + depth, c.close > c.open and c.close > f
            extreme = min(c.low, f - depth)
        else:
            touched, resumed = c.high >= f - depth, c.close < c.open and c.close < f
            extreme = max(c.high, f + depth)
        if not (touched and resumed):
            return []
        price = ctx.last_price(c.symbol) or c.close
        dist = max(abs(price - extreme) + p.stop_atr * atr, p.min_stop_pct * price)
        stop = price - dist if side == "long" else price + dist
        body = abs(c.close - c.open) / (c.high - c.low) if c.high > c.low else 0.0
        mid = (p.min_atr_rank + p.max_atr_rank) / 2.0
        half = (p.max_atr_rank - p.min_atr_rank) / 2.0 or 1.0
        factors = {"context_trend": scale(abs(tr["slope"]), 0.0, 0.01), "slope": scale(abs(slope), 0.0, 0.01),
                   "volatility_band": clamp01(1.0 - abs(rank - mid) / half), "body": scale(body, 0.4, 0.9)}
        quality = sum(factors.values()) / len(factors)
        trail = TrailSpec("atr", tf, p.trail_atr, int(p.atr_n), False)
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf=tf, price=price, stop=stop,
            tps_r=[(p.tp1_r, p.tp1_frac)], trail=trail, be_at_r=p.be_at_r,
            reason=f"{tf} trend pullback, ATR percentile {rank:.2f}",
            meta={"signal_quality": round(quality, 4), "quality_factors": {k: round(v, 3) for k, v in factors.items()},
                  "expected_move_pct": self.expected_move(price, stop, p.tp1_r, atr),
                  "expected_move_source": "strategy: min(3R target, 1.5 ATR)"})]
