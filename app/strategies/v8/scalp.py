"""Short-hold paper challengers on closed 3m and 5m Bybit candles.

These are new hypotheses, not extensions of the frozen V6/V7 experiments.
The live worker applies a separate freshness and execution-cost gate.
"""
from dataclasses import dataclass
import statistics

from app.strategies.v6.base import Setup, V6Strategy, close_location, scale

HOUR = 3_600_000


@dataclass
class Params:
    target_r: float = 1.5
    min_stop_pct: float = 0.004
    max_stop_pct: float = 0.018
    max_funding_r: float = 0.10


class ScalpV8(V6Strategy):
    version = "v8"
    Params = Params
    signal_tf = ""
    max_hold_min = 0
    expected_hold = "minutes, capped below one hour"
    fails_when = "choppy moves, wide spreads, or signal latency overwhelms the move"

    @classmethod
    def for_class(cls, horizon, feed=None, market=None):
        if horizon != "SCALP":
            raise ValueError("V8 only supports SCALP")
        return type(cls.__name__ + "_SCALP", (cls,), {
            "horizon": horizon, "signal_tf": cls.signal_tf, "ctx_fast": "15m", "ctx_slow": "1h",
            "context_tf": "15m", "native_timeframe": cls.signal_tf,
            "supported_timeframes": (cls.signal_tf,),
            "timeframes": (cls.signal_tf, "15m", "1h"), "feed": feed, "market": market,
            "time_stop_h": cls.max_hold_min / 60, "tag": cls.signal_tf.upper(),
            "day_bars": 1440 // (3 if cls.signal_tf == "3m" else 5),
            "journal": {}, "__module__": cls.__module__,
        })

    def on_candle(self, c, ctx):
        if not self.bound(c):
            return []
        cs = self.series(ctx, c.symbol, self.signal_tf)
        if len(cs) < 60 or len(ctx.candles(c.symbol, "1h")) < 60:
            return []
        t = c.close_time + 1
        hour = t // HOUR * HOUR
        # Positioning and funding must come from the last recorded hourly watermark.
        if self.feed is None or hour not in self.feed.caps:
            return []
        fast = self.ctx_trend(ctx, c.symbol, "15m")
        slow = self.ctx_trend(ctx, c.symbol, "1h")
        if fast not in ("up", "down") or slow == ("down" if fast == "up" else "up"):
            return []
        side = "long" if fast == "up" else "short"
        pos = self.positioning(hour)
        if pos.get("oi_chg_24h") is None or pos["oi_chg_24h"] < -0.10:
            return []
        atr = self.atr(ctx, c.symbol, self.signal_tf)
        if not atr or c.close <= 0:
            return []
        setup = self.scalp_setup(ctx, c, cs, side, atr)
        if setup is None:
            return []
        dist = c.close - setup.stop_ref if side == "long" else setup.stop_ref - c.close
        raw_stop = (dist + 0.25 * atr) / c.close
        if dist <= 0 or raw_stop > self.params.max_stop_pct:
            return []
        stop_pct = max(raw_stop, self.params.min_stop_pct)
        funding = self.feed.expected_funding(hour, side, self.max_hold_min / 60)
        if funding is None or max(0.0, funding) > self.params.max_funding_r * stop_pct:
            return []
        setup.factors.update(trend=1.0 if slow == fast else 0.5)
        target = self.params.target_r
        sig = self.entry(c=c, ctx=ctx, side=side, price=c.close, stop_pct=stop_pct,
                         tps_r=[(target, 1.0)], trail_atr=None, be_at_r=None,
                         expected_move_pct=target * stop_pct,
                         expected_move_source="1.5R structural scalp target",
                         factors=setup.factors, reason=setup.note,
                         extra={"setup": setup.note, "horizon": "SCALP", "target_r": target,
                                "time_stop_h": self.max_hold_min / 60, "positioning_hour": hour,
                                "expected_funding_pct": funding})
        sig.max_hold_s = self.max_hold_min * 60
        sig.id = f"{self.id}:SCALP:{c.close_time}:{side}"
        return [sig]


class BreakoutV8(ScalpV8):
    id = "V8.1"
    name = "Three-minute continuation scalp"
    family = "SCALP_BREAKOUT"
    thesis = "A strong 3m range break in the 15m direction continues for several minutes"
    signal_tf = "3m"
    max_hold_min = 30

    def scalp_setup(self, ctx, c, cs, side, atr):
        prior = list(cs)[-7:-1]
        typical = statistics.median(x.volume for x in prior)
        if typical <= 0 or c.volume < typical or c.high - c.low > 2.5 * atr:
            return None
        long = side == "long"
        broken = c.close > max(x.high for x in prior) if long else c.close < min(x.low for x in prior)
        loc = close_location(c) if long else 1 - close_location(c)
        if not broken or loc < 0.65:
            return None
        recent = list(cs)[-3:]
        extreme = min(x.low for x in recent) if long else max(x.high for x in recent)
        return Setup(side, extreme, {"close": scale(loc, 0.5, 1.0),
                                     "volume": scale(c.volume / typical, 1, 2)},
                     "3m range break with volume")


class PullbackV8(ScalpV8):
    id = "V8.2"
    name = "Five-minute trend resumption scalp"
    family = "SCALP_PULLBACK"
    thesis = "A brief 5m pullback resumes in the 15m trend direction"
    signal_tf = "5m"
    max_hold_min = 45

    def scalp_setup(self, ctx, c, cs, side, atr):
        ema = ctx.ind(c.symbol, "5m", "ema", n=20)
        if not ema or ema[-1] is None:
            return None
        prior = list(cs)[-4:-1]
        long = side == "long"
        touched = any(x.low <= ema[-1] + 0.3 * atr if long else x.high >= ema[-1] - 0.3 * atr for x in prior)
        resumed = c.close > cs[-2].high and c.close > ema[-1] if long else c.close < cs[-2].low and c.close < ema[-1]
        loc = close_location(c) if long else 1 - close_location(c)
        if not touched or not resumed or loc < 0.6 or abs(c.close - ema[-1]) > 2 * atr:
            return None
        extreme = min(x.low for x in prior + [c]) if long else max(x.high for x in prior + [c])
        return Setup(side, extreme, {"close": scale(loc, 0.5, 1.0)}, "5m trend pullback resumed")


def load_v8():
    return {cls.id: cls for cls in (BreakoutV8, PullbackV8)}
