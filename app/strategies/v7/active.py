"""15-minute entries, hourly trend, six-hour maximum hold; independent of frozen V6.

Positioning is read at the last recorded hour watermark, including on quarter-hour
decisions. This keeps late REST arrivals from changing restart re-derivation.
"""
from dataclasses import dataclass
import statistics

from app.strategies.v6.base import V6Strategy, Setup, close_location, scale

HOUR = 3_600_000


@dataclass
class Params:
    cooldown_bars: int = 2
    target_r: float = 1.8
    min_stop_pct: float = 0.006
    max_stop_pct: float = 0.03
    max_hold_h: float = 6.0
    max_funding_r: float = 0.10


class ActiveV7(V6Strategy):
    version = "v7"
    Params = Params
    expected_hold = "15 minutes to 6 hours"
    fails_when = "choppy trends and failed breakouts; no demonstrated edge yet"

    @classmethod
    def for_class(cls, horizon, feed=None, market=None):
        if horizon != "ACTIVE":
            raise ValueError("V7 only supports ACTIVE")
        return type(cls.__name__ + "_ACTIVE", (cls,), {
            "horizon": horizon, "signal_tf": "15m", "ctx_fast": "1h", "ctx_slow": "4h",
            "context_tf": "1h", "native_timeframe": "15m", "supported_timeframes": ("15m",),
            "timeframes": ("15m", "1h", "4h"), "feed": feed, "market": market,
            "time_stop_h": 6.0, "tag": "15M", "day_bars": 96, "journal": {}, "__module__": cls.__module__})

    def on_candle(self, c, ctx):
        if not self.bound(c):
            return []
        cs = self.series(ctx, c.symbol, self.signal_tf)
        if len(cs) < 60 or len(ctx.candles(c.symbol, "4h")) < 60:
            return []
        t = c.close_time + 1
        hour = t // HOUR * HOUR
        # An absent watermark cannot be replaced by subsequently downloaded history.
        if self.feed is None or hour not in self.feed.caps:
            return []
        pos = self.positioning(hour)
        trend = self.ctx_trend(ctx, c.symbol, "1h")
        slow = self.ctx_trend(ctx, c.symbol, "4h")
        if trend not in ("up", "down") or slow is None:
            return []
        side = "long" if trend == "up" else "short"
        if slow == ("down" if side == "long" else "up"):
            return []
        fp, oi = pos.get("funding_pct_90d"), pos.get("oi_chg_24h")
        if fp is None or oi is None or oi < -0.08:
            return []
        crowd = fp if side == "long" else 1 - fp
        atr = self.atr(ctx, c.symbol, self.signal_tf)
        if not atr or c.close <= 0:
            return []
        st = self.active_setup(ctx, c, cs, side, atr)
        if st is None:
            return []
        dist = (c.close - st.stop_ref) if side == "long" else (st.stop_ref - c.close)
        raw = (dist + 0.25 * atr) / c.close
        if dist <= 0 or raw > self.params.max_stop_pct:
            return []
        stop_pct = max(raw, self.params.min_stop_pct)
        funding = self.feed.expected_funding(hour, side, self.params.max_hold_h)
        if funding is None or max(0.0, funding) > self.params.max_funding_r * stop_pct:
            return []
        st.factors.update(uncrowded=scale(0.9 - crowd, 0, 0.9), trend=1.0 if slow == trend else 0.5)
        target = self.params.target_r
        sig = self.entry(c=c, ctx=ctx, side=side, price=c.close, stop_pct=stop_pct,
                         tps_r=[(target, 1.0)], trail_atr=None, be_at_r=None,
                         expected_move_pct=target * stop_pct, expected_move_source="1.8R structural target",
                         factors=st.factors, reason=st.note,
                         extra={"setup": st.note, "horizon": "ACTIVE", "target_r": target,
                                "time_stop_h": self.params.max_hold_h, "positioning_hour": hour,
                                "expected_funding_pct": funding})
        sig.max_hold_s = int(self.params.max_hold_h * 3600)
        sig.id = f"{self.id}:ACTIVE:{c.close_time}:{side}"
        return [sig]


class PullbackV7(ActiveV7):
    id = "V7.1"
    name = "Active trend pullback"
    family = "ACTIVE_PULLBACK"
    thesis = "15m pullback to EMA20 resumes with the hourly trend, without opposing 4h trend"

    def active_setup(self, ctx, c, cs, side, atr):
        ema = ctx.ind(c.symbol, "15m", "ema", n=20)
        if not ema or ema[-1] is None:
            return None
        prior = list(cs)[-5:-1]
        long = side == "long"
        touched = any(x.low <= ema[-1] + 0.3 * atr if long else x.high >= ema[-1] - 0.3 * atr for x in prior)
        resumed = c.close > cs[-2].high and c.close > ema[-1] if long else c.close < cs[-2].low and c.close < ema[-1]
        loc = close_location(c) if long else 1 - close_location(c)
        if not touched or not resumed or loc < 0.6 or abs(c.close - ema[-1]) > 2 * atr:
            return None
        extreme = min(x.low for x in prior + [c]) if long else max(x.high for x in prior + [c])
        return Setup(side, extreme, {"close": scale(loc, 0.5, 1.0)}, "15m trend pullback resumed")


class BreakoutV7(ActiveV7):
    id = "V7.2"
    name = "Active volume breakout"
    family = "ACTIVE_BREAKOUT"
    thesis = "15m break of a three-hour range with volume confirmation and hourly trend"

    def active_setup(self, ctx, c, cs, side, atr):
        prior = list(cs)[-13:-1]
        typical = statistics.median(x.volume for x in prior)
        if typical <= 0 or c.volume < 1.2 * typical or c.high - c.low > 2.5 * atr:
            return None
        long = side == "long"
        broken = c.close > max(x.high for x in prior) if long else c.close < min(x.low for x in prior)
        loc = close_location(c) if long else 1 - close_location(c)
        if not broken or loc < 0.65:
            return None
        recent = list(cs)[-3:]
        extreme = min(x.low for x in recent) if long else max(x.high for x in recent)
        return Setup(side, extreme, {"close": scale(loc, 0.5, 1.0), "volume": scale(c.volume / typical, 1, 2)},
                     "15m volume-confirmed range break")


def load_v7():
    return {cls.id: cls for cls in (PullbackV7, BreakoutV7)}
