"""S12 v2 - Trend Pullback Resume. Revised S12: enter established trends on a real reset, not noise.

v1 bought any touch of a fast EMA on 5m bars. v2 hypothesis: in a trend confirmed on the higher
timeframe, a pullback that RESETS momentum (RSI back toward neutral) into the EMA20-EMA50 zone and
then resumes offers a large move for a stop placed under the pullback -- and such pullbacks are rare
enough that costs stay small relative to the edge.

Entry:   context trend up (down); on the signal timeframe EMA20 > EMA50 (<); the last `lookback`
         bars dipped into the EMA20-EMA50 zone with RSI(14) below `rsi_reset` (above 100-...); the
         current bar closes back above EMA20 (below) with a body in the trend direction.
Stop:    max(`stop_atr` x ATR below the pullback low, `min_stop_pct` of price).
Exits:   50% at 2R, break-even at 1R, the rest on a 2.5 ATR trail.
Quality: mean of trend strength, pullback depth (best near 1 ATR), momentum reset and the
         resumption bar's body.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v2.base import V2Strategy, clamp01, scale


@dataclass
class Params:
    ema_fast: int = P(20, min=5, max=100, step=1, label="fast EMA")
    ema_slow: int = P(50, min=10, max=200, step=1, label="slow EMA")
    rsi_reset: float = P(45.0, min=20.0, max=60.0, step=1.0, label="RSI reset level (longs)")
    lookback: int = P(6, min=2, max=30, step=1, label="pullback lookback (bars)")
    atr_n: int = P(14, min=5, max=50, step=1, label="ATR length")
    stop_atr: float = P(1.2, min=0.3, max=5.0, step=0.1, label="stop below the pullback (ATR)")
    min_stop_pct: float = P(0.006, min=0.002, max=0.05, step=0.001, label="min stop (fraction of price)")
    tp1_r: float = P(2.0, min=1.0, max=6.0, step=0.1, label="first target (R)")
    tp1_frac: float = P(0.5, min=0.0, max=1.0, step=0.05, label="first target size")
    trail_atr: float = P(2.5, min=1.0, max=8.0, step=0.1, label="trail (ATR)")
    be_at_r: float = P(1.0, min=0.0, max=4.0, step=0.1, label="break-even at (R)")
    cooldown_bars: int = P(8, min=0, max=100, step=1, label="cooldown (bars)")


class TrendPullbackV2(V2Strategy):
    id = "S12"
    name = "Trend Pullback v2"
    Params = Params
    doc = StrategyDoc(
        idea="Join a confirmed trend after a genuine momentum reset into the EMA20-EMA50 zone.",
        timeframe="5m / 15m / 30m (one per bot) with a 1h or 4h trend context",
        symbols="one coin per bot",
        entry="context trend aligned; pullback into the EMA20-EMA50 zone with RSI reset; close back "
              "through EMA20 with a trend-direction body",
        stop="max(1.2 ATR beyond the pullback extreme, 0.6% of price)",
        targets="50% at 2R, break-even at 1R, remainder on a 2.5 ATR trail",
        sizing="risk from the stop; ATTACK only on high signal quality and a large edge-to-cost ratio",
        why_aggressive="high-probability trend entries sized properly, few of them",
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
        if len(candles) < int(p.ema_slow) + int(p.lookback) + 5:
            return []
        tr = self.trend(ctx, c.symbol)
        if tr is None or tr["direction"] == "flat":
            return []
        side = "long" if tr["direction"] == "up" else "short"
        f_s = ctx.ind(c.symbol, tf, "ema", n=int(p.ema_fast))
        s_s = ctx.ind(c.symbol, tf, "ema", n=int(p.ema_slow))
        r_s = ctx.ind(c.symbol, tf, "rsi", n=14)
        atr = last(ctx.ind(c.symbol, tf, "atr", n=int(p.atr_n)))
        f, s = last(f_s), last(s_s)
        if None in (f, s, atr) or atr <= 0:
            return []
        n = int(p.lookback)
        recent = candles[-n - 1:-1]
        rsis = [x for x in r_s[-n - 1:-1] if x is not None]
        if len(recent) < n or not rsis:
            return []
        if side == "long":
            if not f > s:
                return []
            zone_hit = any(b.low <= f_s[-n - 1 + i] for i, b in enumerate(recent) if f_s[-n - 1 + i] is not None)
            reset = min(rsis) <= p.rsi_reset
            resumed = c.close > f and c.close > c.open
            extreme = min(b.low for b in recent + [c])
            depth = (f - extreme) / atr
            reset_amt = scale(p.rsi_reset + 5 - min(rsis), 0.0, 20.0)
        else:
            if not f < s:
                return []
            zone_hit = any(b.high >= f_s[-n - 1 + i] for i, b in enumerate(recent) if f_s[-n - 1 + i] is not None)
            reset = max(rsis) >= 100.0 - p.rsi_reset
            resumed = c.close < f and c.close < c.open
            extreme = max(b.high for b in recent + [c])
            depth = (extreme - f) / atr
            reset_amt = scale(max(rsis) - (100.0 - p.rsi_reset - 5), 0.0, 20.0)
        if not (zone_hit and reset and resumed):
            return []
        if (side == "long" and extreme < s - atr) or (side == "short" and extreme > s + atr):
            return []                      # broke well through the slow EMA: not a pullback any more
        price = ctx.last_price(c.symbol) or c.close
        struct = abs(price - extreme) + p.stop_atr * atr
        dist = max(struct, p.min_stop_pct * price)
        stop = price - dist if side == "long" else price + dist
        body = abs(c.close - c.open) / (c.high - c.low) if c.high > c.low else 0.0
        factors = {"trend": scale(abs(tr["slope"]), 0.0, 0.01),
                   "depth": clamp01(1.0 - abs(depth - 1.0)), "reset": reset_amt,
                   "body": scale(body, 0.4, 0.9)}
        quality = sum(factors.values()) / len(factors)
        trail = TrailSpec("atr", tf, p.trail_atr, int(p.atr_n), True)
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf=tf, price=price, stop=stop,
            tps_r=[(p.tp1_r, p.tp1_frac)], trail=trail, be_at_r=p.be_at_r,
            reason=f"{tf} pullback resume with the {self.context_tf} trend",
            meta={"signal_quality": round(quality, 4), "quality_factors": {k: round(v, 3) for k, v in factors.items()},
                  "expected_move_pct": self.expected_move(price, stop, p.tp1_r, atr),
                  "expected_move_source": "strategy: min(2R target, 1.5 ATR)"})]
