"""S33 v3 - Pullback Continuation.

Hypothesis: inside an established slow-context trend, a pullback on the signal timeframe into the
EMA zone with an RSI reset, followed by a resumption bar, continues toward the prior swing with a
nearby, well-defined invalidation (the pullback extreme).

Entry:   slow context trending, fast context not opposed; within the last `lookback` bars price
         reached within `touch_atr` ATR of the signal-TF EMA and RSI dipped to <= `rsi_reset`
         (>= 100 - it for shorts); this bar closes beyond the previous bar's high (low) and on the
         trend side of the EMA, with RSI turning.
Stop:    beyond the pullback extreme by `stop_buffer_atr` ATR, clamped to 0.5%..2.0%.
Exits:   50% at the prior swing (if >= 1.2R away, else at 1.5R), break-even at 1R, 2.5 ATR trail,
         60 bars max.
Expected move: min(first objective, 1 ATR of the fast context) -- a continuation leg to the prior
         swing, bounded by what the context typically moves in one bar.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v3.base import V3Strategy, close_location, scale


@dataclass
class Params:
    ema_n: int = P(20, min=5, max=100, step=1, label="signal EMA")
    rsi_n: int = P(14, min=5, max=30, step=1, label="RSI length")
    rsi_reset: float = P(45.0, min=20.0, max=50.0, step=1.0, label="RSI reset level (longs)")
    touch_atr: float = P(0.3, min=0.0, max=2.0, step=0.1, label="EMA touch tolerance (ATR)")
    lookback: int = P(5, min=2, max=20, step=1, label="pullback lookback (bars)")
    swing_n: int = P(30, min=10, max=120, step=1, label="prior swing lookback (bars)")
    min_tp_r: float = P(1.2, min=0.8, max=4.0, step=0.1, label="swing target must be >= (R)")
    tp1_r: float = P(1.5, min=1.0, max=5.0, step=0.1, label="first target when no swing (R)")
    tp1_frac: float = P(0.5, min=0.0, max=1.0, step=0.05, label="first target size")
    stop_buffer_atr: float = P(0.2, min=0.0, max=2.0, step=0.1, label="stop beyond the pullback (ATR)")
    trail_atr: float = P(2.5, min=1.0, max=6.0, step=0.1, label="trail (ATR)")
    be_at_r: float = P(1.0, min=0.0, max=3.0, step=0.1, label="break-even at (R)")
    cooldown_bars: int = P(4, min=0, max=50, step=1, label="cooldown (bars)")


class PullbackContinuationV3(V3Strategy):
    id = "S33"
    name = "Pullback Continuation v3"
    family = "PULLBACK_CONTINUATION"
    hypothesis = "a trend pullback to the EMA with an RSI reset and a resumption bar continues to the prior swing"
    Params = Params
    max_hold_bars = 60
    doc = StrategyDoc(
        idea="Buy the resumption after a pullback to the EMA in a context uptrend (mirror for shorts).",
        timeframe="3m / 5m / 15m (30m benchmark) with 15m+1h or 1h+4h context; 1m execution",
        symbols="one coin per bot",
        entry="context trend; EMA-zone touch and RSI <= 45 in the last 5 bars; close beyond the previous "
              "bar's extreme on the trend side of the EMA with RSI turning",
        stop="beyond the pullback extreme + 0.2 ATR, 0.5%-2.0%",
        targets="50% at the prior swing (>= 1.2R) or 1.5R, break-even at 1R, 2.5 ATR trail, 60 bars max",
        sizing="AGGRESSIVE_V3 tiers on signal quality and edge-to-cost; Jev V2 on the +JEV twin",
        why_aggressive="re-enters every healthy pullback of a running trend",
    )

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        p, tf = self.params, self.signal_tf
        cs = self.series(ctx, c.symbol, tf)
        lb, sw = int(p.lookback), int(p.swing_n)
        if len(cs) < max(sw + lb, 60) + 5:
            return []
        slow = self.trend(ctx, c.symbol, self.ctx_slow)
        fast = self.trend(ctx, c.symbol, self.ctx_fast)
        if slow is None or fast is None or slow["direction"] == "flat":
            return []
        side = "long" if slow["direction"] == "up" else "short"
        want = slow["direction"]
        if fast["direction"] not in (want, "flat"):
            return []
        ema_s = ctx.ind(c.symbol, tf, "ema", n=int(p.ema_n))
        rsi_s = ctx.ind(c.symbol, tf, "rsi", n=int(p.rsi_n))
        atr = self.atr(ctx, c.symbol, tf)
        ema, rsi, rsi_prev = ema_s[-1], rsi_s[-1], rsi_s[-2] if len(rsi_s) > 1 else None
        if None in (ema, rsi, rsi_prev) or not atr:
            return []
        recent = cs[-lb - 1:-1]
        emas = ema_s[-lb - 1:-1]
        rsis = [r for r in rsi_s[-lb - 1:-1] if r is not None]
        prev = cs[-2]
        if not rsis or None in emas:
            return []
        if side == "long":
            touched = any(x.low <= e + p.touch_atr * atr for x, e in zip(recent, emas))
            reset = min(rsis) <= p.rsi_reset
            trigger = c.close > prev.high and c.close > ema and rsi > rsi_prev
            extreme = min(x.low for x in recent + [c])
            swing = max(x.high for x in cs[-sw - lb - 1:-lb - 1])
        else:
            touched = any(x.high >= e - p.touch_atr * atr for x, e in zip(recent, emas))
            reset = max(rsis) >= 100.0 - p.rsi_reset
            trigger = c.close < prev.low and c.close < ema and rsi < rsi_prev
            extreme = max(x.high for x in recent + [c])
            swing = min(x.low for x in cs[-sw - lb - 1:-lb - 1])
        if not (touched and reset and trigger):
            return []
        price = ctx.last_price(c.symbol) or c.close
        raw = abs(price - extreme) + p.stop_buffer_atr * atr
        sp = self.stop_pct(price, raw)
        if sp is None:
            return []
        dist = sp * price
        swing_gap = (swing - price) if side == "long" else (price - swing)
        if swing_gap >= p.min_tp_r * dist:
            tps_price, tps_r, first = [(swing, p.tp1_frac)], [], swing_gap / price
            objective = "prior swing"
        else:
            tps_price, tps_r, first = [], [(p.tp1_r, p.tp1_frac)], p.tp1_r * sp
            objective = f"{p.tp1_r:g}R"
        ctx_atr = self.atr(ctx, c.symbol, self.ctx_fast)
        move = min(first, ctx_atr / price) if ctx_atr else first
        depth = abs((max(x.high for x in recent) if side == "long" else min(x.low for x in recent)) - extreme) / atr
        reset_depth = (p.rsi_reset - min(rsis)) if side == "long" else (max(rsis) - (100.0 - p.rsi_reset))
        cl = close_location(c) if side == "long" else 1.0 - close_location(c)
        vr = self.volume_ratio(cs) or 0.0
        factors = {"trend": scale(abs(slow["slope"]), 0.0, 0.01), "depth": 1.0 - min(1.0, abs(depth - 1.5) / 1.5),
                   "rsi_reset": scale(reset_depth, 0.0, 15.0), "resumption": scale(cl, 0.6, 1.0),
                   "volume": scale(vr, 1.0, 2.5)}
        return [self.entry(c=c, ctx=ctx, side=side, price=price, stop_pct=sp, tps_r=tps_r, tps_price=tps_price,
                           trail_atr=p.trail_atr, be_at_r=p.be_at_r, expected_move_pct=move,
                           expected_move_source=f"strategy: min(first objective ({objective}), 1 ATR of {self.ctx_fast})",
                           factors=factors,
                           reason=f"{tf} pullback to EMA{int(p.ema_n)} resumed with the {self.ctx_slow} trend",
                           extra={"context": {"slow": slow["direction"], "fast": fast["direction"]},
                                  "pullback_depth_atr": round(depth, 3), "objective": objective})]
