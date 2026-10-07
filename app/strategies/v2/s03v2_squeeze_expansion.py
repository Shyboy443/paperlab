"""S03 v2 - Squeeze Expansion. Revised S03: trade the release of a long, genuine squeeze.

v1 traded any Bollinger-width low on 5m bars. v2 hypothesis: when Bollinger Bands sit INSIDE the
Keltner Channel for a sustained stretch, volatility is compressed far below normal; the first bar
that closes outside the bands with a strong body and volume starts an expansion large enough to pay
for costs many times over. Trading against a clear higher-timeframe trend is refused.

Entry:   BB(20, 2) inside KC(20, `kc_atr` x ATR) for at least `min_squeeze_bars` of the previous bars;
         the current bar closes outside the upper (lower) band with body >= 50% of its range and
         volume >= `min_volume_ratio` x mean; context trend not against the break.
Stop:    max(the opposite side of the squeeze range, `min_stop_pct` of price).
Exits:   40% at 2R, break-even at 1R, the rest on a 3 ATR trail.
Quality: mean of squeeze duration, volume, trend alignment and close distance beyond the band.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v2.base import V2Strategy, scale


@dataclass
class Params:
    bb_n: int = P(20, min=10, max=60, step=1, label="band length")
    bb_k: float = P(2.0, min=1.0, max=3.5, step=0.1, label="band width (sd)")
    kc_atr: float = P(1.5, min=0.8, max=3.0, step=0.1, label="Keltner width (ATR)")
    min_squeeze_bars: int = P(8, min=3, max=60, step=1, label="min squeeze length (bars)")
    squeeze_window: int = P(12, min=4, max=80, step=1, label="squeeze lookback (bars)")
    min_volume_ratio: float = P(1.3, min=1.0, max=5.0, step=0.1, label="min volume vs 20-bar mean")
    atr_n: int = P(14, min=5, max=50, step=1, label="ATR length")
    min_stop_pct: float = P(0.006, min=0.002, max=0.05, step=0.001, label="min stop (fraction of price)")
    tp1_r: float = P(2.0, min=1.0, max=6.0, step=0.1, label="first target (R)")
    tp1_frac: float = P(0.4, min=0.0, max=1.0, step=0.05, label="first target size")
    trail_atr: float = P(3.0, min=1.0, max=8.0, step=0.1, label="trail (ATR)")
    be_at_r: float = P(1.0, min=0.0, max=4.0, step=0.1, label="break-even at (R)")
    cooldown_bars: int = P(10, min=0, max=100, step=1, label="cooldown (bars)")


class SqueezeExpansionV2(V2Strategy):
    id = "S03"
    name = "Squeeze Expansion v2"
    Params = Params
    doc = StrategyDoc(
        idea="Trade the first strong close out of a sustained Bollinger-inside-Keltner squeeze.",
        timeframe="5m / 15m / 30m (one per bot) with a 1h or 4h trend context",
        symbols="one coin per bot",
        entry="bands inside the Keltner channel for 8 of the last 12 bars; close outside a band "
              "with a strong body and volume; never against a clear context trend",
        stop="max(opposite side of the squeeze range, 0.6% of price)",
        targets="40% at 2R, break-even at 1R, remainder on a 3 ATR trail",
        sizing="risk from the stop; ATTACK only on high signal quality and a large edge-to-cost ratio",
        why_aggressive="volatility expansion after compression: rare, large moves",
    )
    warmup_bars = 120
    max_positions = 1

    def cooldown_ms(self) -> int:
        from app.core.types import tf_ms
        return int(self.params.cooldown_bars) * (tf_ms(self.signal_tf) if self.signal_tf else 60_000)

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        p, tf = self.params, self.signal_tf
        candles = list(ctx.candles(c.symbol, tf))       # a deque: list() before slicing
        w = int(p.squeeze_window)
        if len(candles) < int(p.bb_n) + w + 5:
            return []
        basis, upper, lower, _ = ctx.ind(c.symbol, tf, "bollinger", n=int(p.bb_n), k=float(p.bb_k))
        ema = ctx.ind(c.symbol, tf, "ema", n=int(p.bb_n))
        atr_s = ctx.ind(c.symbol, tf, "atr", n=int(p.atr_n))
        atr = last(atr_s)
        if atr is None or atr <= 0 or last(upper) is None:
            return []
        squeezed = 0
        for i in range(-w - 1, -1):
            u, lo_, m, a = upper[i], lower[i], ema[i], atr_s[i]
            if None in (u, lo_, m, a):
                continue
            if u < m + p.kc_atr * a and lo_ > m - p.kc_atr * a:
                squeezed += 1
        if squeezed < int(p.min_squeeze_bars):
            return []
        side = "long" if c.close > last(upper) else "short" if c.close < last(lower) else None
        if side is None:
            return []
        body = abs(c.close - c.open) / (c.high - c.low) if c.high > c.low else 0.0
        if body < 0.5 or (side == "long") != (c.close > c.open):
            return []
        vr = self.volume_ratio(candles)
        if vr is None or vr < p.min_volume_ratio:
            return []
        tr = self.trend(ctx, c.symbol)
        if tr is None or tr["direction"] == ("down" if side == "long" else "up"):
            return []
        rng = candles[-w - 1:-1]
        price = ctx.last_price(c.symbol) or c.close
        struct = (price - min(b.low for b in rng)) if side == "long" else (max(b.high for b in rng) - price)
        dist = max(struct, p.min_stop_pct * price)
        stop = price - dist if side == "long" else price + dist
        aligned = 1.0 if tr["direction"] == ("up" if side == "long" else "down") else 0.5
        beyond = abs(c.close - (last(upper) if side == "long" else last(lower))) / atr
        factors = {"squeeze": scale(squeezed, p.min_squeeze_bars, w), "volume": scale(vr, 1.0, 2.5),
                   "trend": aligned, "beyond_band": scale(beyond, 0.0, 1.0)}
        quality = sum(factors.values()) / len(factors)
        trail = TrailSpec("atr", tf, p.trail_atr, int(p.atr_n), True)
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf=tf, price=price, stop=stop,
            tps_r=[(p.tp1_r, p.tp1_frac)], trail=trail, be_at_r=p.be_at_r,
            reason=f"{tf} squeeze release after {squeezed} compressed bars",
            meta={"signal_quality": round(quality, 4), "quality_factors": {k: round(v, 3) for k, v in factors.items()},
                  "expected_move_pct": self.expected_move(price, stop, p.tp1_r, atr),
                  "expected_move_source": "strategy: min(2R target, 1.5 ATR)"})]
