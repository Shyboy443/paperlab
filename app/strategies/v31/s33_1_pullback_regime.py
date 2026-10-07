"""S33.1 - Pullback Continuation with regime alignment and expansion confirmation.

Hypothesis: inside a trend that BOTH context timeframes agree on, a pullback to the signal EMA that
resumes with an expanding, high-volume bar continues for hours.

Why it should overcome the V3 failure: V3's S33 had the only positive 12h drift among pullback
entries (+25 bps on 15m, DEVELOPMENT) but its exits (break-even at 1R, first target at the prior
swing, a trail on the signal timeframe's ATR, <= 60 bars) closed the trades after a median 45-180
minutes and gave back 19% of trades that had reached +1R. V3.1 adds (a) regime alignment on both
context timeframes, (b) a resumption bar that must EXPAND (range >= 1.0 ATR, strong close, volume >= average),
and (c) a long-hold exit: break-even only at 1.5R, 30% at 2R, the runner trailed on 2 x ATR(1h), 12 h max.

Expected holding period: 1-8 hours. Expected move: 2R+ on the runner (1.5 x ATR(1h) cap on the
stated expectation). Expected frequency (raw setups per coin-day, DEVELOPMENT counts): 3m ~7, 5m ~4,
15m ~1, 30m ~0.3; executed trades fewer (one position at a time, the edge gate).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v31.base import V31Strategy, close_location, scale


@dataclass
class Params:
    ema_n: int = P(20, min=5, max=100, step=1, label="signal EMA")
    rsi_reset: float = P(48.0, min=20.0, max=50.0, step=1.0, label="RSI reset (longs)")
    touch_atr: float = P(0.5, min=0.0, max=2.0, step=0.1, label="EMA touch tolerance (ATR)")
    lookback: int = P(8, min=2, max=20, step=1, label="pullback lookback (bars)")
    expand_atr: float = P(1.0, min=0.5, max=3.0, step=0.1, label="resumption bar range (ATR)")
    min_volume_ratio: float = P(1.0, min=0.5, max=4.0, step=0.1, label="resumption volume vs mean")
    min_close: float = P(0.60, min=0.5, max=1.0, step=0.05, label="resumption close location")
    stop_buffer_atr: float = P(0.2, min=0.0, max=2.0, step=0.1, label="stop beyond the pullback (ATR)")
    tp_r: float = P(2.0, min=1.0, max=6.0, step=0.1, label="partial target (R)")
    tp_frac: float = P(0.3, min=0.0, max=1.0, step=0.05, label="partial size")
    be_at_r: float = P(1.5, min=0.0, max=4.0, step=0.1, label="break-even at (R)")
    trail_mult: float = P(2.0, min=0.5, max=6.0, step=0.1, label="runner trail (x ATR 1h)")
    cooldown_bars: int = P(4, min=0, max=50, step=1, label="cooldown (bars)")


class PullbackRegimeV31(V31Strategy):
    id = "S33.1"
    name = "Pullback Regime Continuation v3.1"
    family = "PULLBACK_REGIME_CONTINUATION"
    hypothesis = "a pullback to the EMA inside a two-timeframe trend that resumes on an expanding bar continues for hours"
    thesis = "trend pullback, both contexts aligned, expansion resumption, long hold"
    why_v31 = "V3 S33 had positive 12h drift but exits closed it in 45-180 min; adds regime alignment, expansion and a long-hold runner"
    expected_hold = "1-8 hours"
    expected_frequency = "raw per coin-day: 3m ~7, 5m ~4, 15m ~1, 30m ~0.3"
    Params = Params
    doc = StrategyDoc(
        idea="Buy the expanding resumption after a pullback in a trend both context timeframes agree on.",
        timeframe="3m / 5m / 15m / 30m trigger with 15m+1h or 1h+4h context; 1m execution",
        symbols="one coin per bot",
        entry="both contexts trend with the trade; EMA touch (0.5 ATR) and RSI(14) reset <= 48 in the last 8 bars; "
              "resumption bar closes beyond the previous bar and the EMA with range >= 1.0 ATR, close in the outer "
              "40%, volume at least average",
        stop="beyond the pullback extreme + 0.2 ATR, 0.6%-2.5%",
        targets="break-even at 1.5R, 30% at 2R, runner trailed 2 x ATR(1h), 12 h max",
        sizing="AGGRESSIVE_V31 behind the expected-net-edge gate; Jev V3 on the +JEV twin",
        why_aggressive="participates in every aligned pullback and lets the winners run",
    )

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        p, tf = self.params, self.signal_tf
        cs = self.series(ctx, c.symbol, tf)
        lb = int(p.lookback)
        if len(cs) < 80:
            return []
        slow = self.trend(ctx, c.symbol, self.ctx_slow)
        if slow is None or slow["direction"] == "flat":
            return []
        side = "long" if slow["direction"] == "up" else "short"
        ok, align = self.aligned(ctx, c.symbol, side)
        if not ok:
            return []
        ema_s = ctx.ind(c.symbol, tf, "ema", n=int(p.ema_n))
        rsi_s = ctx.ind(c.symbol, tf, "rsi", n=14)
        atr = self.atr(ctx, c.symbol, tf)
        ema = ema_s[-1] if ema_s else None
        if ema is None or not atr:
            return []
        recent = cs[-lb - 1:-1]
        emas = ema_s[-lb - 1:-1]
        rsis = [r for r in rsi_s[-lb - 1:-1] if r is not None]
        if not rsis or None in emas:
            return []
        prev = cs[-2]
        rng = c.high - c.low
        cl = close_location(c) if side == "long" else 1.0 - close_location(c)
        vr = self.volume_ratio(cs) or 0.0
        if side == "long":
            touched = any(x.low <= e + p.touch_atr * atr for x, e in zip(recent, emas))
            reset = min(rsis) <= p.rsi_reset
            trigger = c.close > prev.high and c.close > ema
            extreme = min(x.low for x in recent + [c])
        else:
            touched = any(x.high >= e - p.touch_atr * atr for x, e in zip(recent, emas))
            reset = max(rsis) >= 100.0 - p.rsi_reset
            trigger = c.close < prev.low and c.close < ema
            extreme = max(x.high for x in recent + [c])
        if not (touched and reset and trigger and rng >= p.expand_atr * atr and cl >= p.min_close and vr >= p.min_volume_ratio):
            return []
        price = ctx.last_price(c.symbol) or c.close
        sp = self.stop_pct(price, abs(price - extreme) + p.stop_buffer_atr * atr)
        if sp is None:
            return []
        reset_depth = (p.rsi_reset - min(rsis)) if side == "long" else (max(rsis) - (100.0 - p.rsi_reset))
        factors = {"trend": scale(abs(slow["slope"]), 0.0, 0.01), "expansion": scale(rng / atr, p.expand_atr, 2.5),
                   "close": scale(cl, p.min_close, 1.0), "volume": scale(vr, 1.0, 3.0),
                   "rsi_reset": scale(reset_depth, 0.0, 15.0)}
        return [self.runner_entry(c=c, ctx=ctx, side=side, price=price, stop_pct=sp, tp_r=p.tp_r, tp_frac=p.tp_frac,
                                  be_at_r=p.be_at_r, trail_mult=p.trail_mult, factors=factors,
                                  reason=f"{tf} pullback resumed on a {rng / atr:.1f} ATR bar with {self.ctx_fast}+{self.ctx_slow} aligned",
                                  extra={"context": align})]
