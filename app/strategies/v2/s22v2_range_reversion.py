"""S22 v2 - Range Extreme Reversion. Revised S22: fade stretched moves only when there is no trend.

v1 faded Keltner touches with a tight stop and a target a few bps away -- a micro edge the round trip
destroyed. v2 hypothesis: in a FLAT higher-timeframe regime, a close far outside a wide Keltner band
with an RSI extreme tends to revert to the mean, and the distance back to the mean is concrete and
large enough to be worth the costs. The expected move here is not a proxy: it is the distance to the
exit the strategy actually targets (the EMA20 mean).

Entry:   context trend flat (|slope| below `max_trend_slope`); the previous bar closed beyond the
         Keltner band (`kc_atr` x ATR around EMA20) with RSI(14) beyond `rsi_extreme`; the current bar
         closes back toward the mean (a reversal bar).
Stop:    `stop_atr` x ATR beyond the extreme, at least `min_stop_pct` of price.
Target:  100% at the EMA20 mean; if the mean is less than `min_target_r` x R away, no trade.
Exit:    time stop after `max_hold_bars` bars.
Quality: mean of stretch (distance in ATR), RSI extremity, regime flatness and reversal-bar body.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.indicators import last
from app.core.types import Candle, Signal, tf_ms
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v2.base import V2Strategy, clamp01, scale


@dataclass
class Params:
    ema_n: int = P(20, min=10, max=100, step=1, label="mean (EMA)")
    kc_atr: float = P(2.5, min=1.5, max=5.0, step=0.1, label="band width (ATR)")
    rsi_extreme: float = P(25.0, min=5.0, max=40.0, step=1.0, label="RSI extreme (longs below)")
    max_trend_slope: float = P(0.004, min=0.0, max=0.05, step=0.001, label="max context slope (flat regime)")
    atr_n: int = P(14, min=5, max=50, step=1, label="ATR length")
    stop_atr: float = P(1.0, min=0.3, max=4.0, step=0.1, label="stop beyond the extreme (ATR)")
    min_stop_pct: float = P(0.006, min=0.002, max=0.05, step=0.001, label="min stop (fraction of price)")
    min_target_r: float = P(1.2, min=0.5, max=5.0, step=0.1, label="min distance to the mean (R)")
    max_hold_bars: int = P(24, min=4, max=200, step=1, label="time stop (bars)")
    cooldown_bars: int = P(12, min=0, max=100, step=1, label="cooldown (bars)")


class RangeReversionV2(V2Strategy):
    id = "S22"
    name = "Range Reversion v2"
    Params = Params
    doc = StrategyDoc(
        idea="Fade a close far outside a wide Keltner band back to the mean, only in a flat regime.",
        timeframe="5m / 15m / 30m (one per bot) with a 1h or 4h regime context",
        symbols="one coin per bot",
        entry="context flat; previous close beyond 2.5 ATR from EMA20 with RSI beyond 25/75; the "
              "current bar reverses toward the mean",
        stop="1 ATR beyond the extreme, at least 0.6% of price",
        targets="all at the EMA20 mean, only if it is at least 1.2R away; time stop after 24 bars",
        sizing="risk from the stop; ATTACK only on high signal quality and a large edge-to-cost ratio",
        why_aggressive="a concrete, measurable reversion target that clears costs by a wide margin",
    )
    warmup_bars = 120
    max_positions = 1

    def cooldown_ms(self) -> int:
        return int(self.params.cooldown_bars) * (tf_ms(self.signal_tf) if self.signal_tf else 60_000)

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        p, tf = self.params, self.signal_tf
        candles = list(ctx.candles(c.symbol, tf))       # a deque: list() before slicing
        if len(candles) < int(p.ema_n) + 30:
            return []
        tr = self.trend(ctx, c.symbol)
        if tr is None or abs(tr["slope"]) > p.max_trend_slope:
            return []
        ema_s = ctx.ind(c.symbol, tf, "ema", n=int(p.ema_n))
        atr_s = ctx.ind(c.symbol, tf, "atr", n=int(p.atr_n))
        rsi_s = ctx.ind(c.symbol, tf, "rsi", n=14)
        mean, atr = last(ema_s), last(atr_s)
        m_prev, a_prev, r_prev = last(ema_s, 1), last(atr_s, 1), last(rsi_s, 1)
        if None in (mean, atr, m_prev, a_prev, r_prev) or atr <= 0:
            return []
        prev = candles[-2]
        if prev.close < m_prev - p.kc_atr * a_prev and r_prev <= p.rsi_extreme:
            side, stretch, rsi_x = "long", (m_prev - prev.close) / a_prev, p.rsi_extreme - r_prev
            reversal = c.close > c.open and c.close > prev.close
            extreme = min(prev.low, c.low)
        elif prev.close > m_prev + p.kc_atr * a_prev and r_prev >= 100.0 - p.rsi_extreme:
            side, stretch, rsi_x = "short", (prev.close - m_prev) / a_prev, r_prev - (100.0 - p.rsi_extreme)
            reversal = c.close < c.open and c.close < prev.close
            extreme = max(prev.high, c.high)
        else:
            return []
        if not reversal:
            return []
        price = ctx.last_price(c.symbol) or c.close
        dist = max(abs(price - extreme) + p.stop_atr * atr, p.min_stop_pct * price)
        stop = price - dist if side == "long" else price + dist
        to_mean = (mean - price) if side == "long" else (price - mean)
        if to_mean < p.min_target_r * dist:
            return []
        body = abs(c.close - c.open) / (c.high - c.low) if c.high > c.low else 0.0
        factors = {"stretch": scale(stretch, p.kc_atr, p.kc_atr + 1.5), "rsi": scale(rsi_x, 0.0, 15.0),
                   "flatness": clamp01(1.0 - abs(tr["slope"]) / p.max_trend_slope) if p.max_trend_slope else 0.0,
                   "body": scale(body, 0.4, 0.9)}
        quality = sum(factors.values()) / len(factors)
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf=tf, price=price, stop=stop,
            tps_price=[(mean, 1.0)], max_hold_s=int(p.max_hold_bars) * tf_ms(tf) // 1000,
            reason=f"{tf} reversion from {stretch:.1f} ATR in a flat {self.context_tf} regime",
            meta={"signal_quality": round(quality, 4), "quality_factors": {k: round(v, 3) for k, v in factors.items()},
                  "expected_move_pct": to_mean / price,
                  "expected_move_source": "strategy: distance to the EMA20 mean it exits at"})]
