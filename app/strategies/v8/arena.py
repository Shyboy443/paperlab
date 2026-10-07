"""V8 SCALP: aggressive 5-minute scalpers on live Bybit data (docs/V8_PROTOCOL.md). Independent of frozen V6 and V7,
and of the rejected 2026-09-27 feasibility prototype in app/strategies/v8/scalp.py (docs/V8_SCALP_ASSESSMENT.md).

Every family decides on each closed 5m candle, reads its own coin's 5m / 15m / 1h candles only (no positioning
feed: nothing that can arrive late, so a restart re-derives identical decisions), and exits with one rule:

    stop     the setup's structural extreme + 0.2 ATR(5m), at least the family's minimum and at most 1.2% of price
             (minimums since 2026-10-02: V8.1 1.0%, V8.2 / V8.3 1.2% -- were 0.45%, which made fees 0.21-0.24 R a trade)
    target   1.5R on the whole position, resting as a LIMIT order (app/live/v8_engine.py)
    time     V8.3: 45 minutes, then out at market. V8.1 / V8.2: no time stop -- held to the stop, the target or the
             UTC day close (docs/V8_COST_STUDY.json: their net R per trade improved in both halves)
    cooldown one 5m bar after an order

    V8.1  micro-pullback   5m touch of EMA20 (+-0.35 ATR) in the 15m trend, then a close through the previous bar
    V8.2  micro-breakout   close outside the prior 6-bar range on >= 1.1x median volume, not against the 1h trend
    V8.3  VWAP snap-back   a stretch >= 1.8 ATR from the rolling 4h VWAP, then a reversal bar

Aggressive by design (target: 10+ trades per bot per day). No edge is claimed: taker fees and spread cost roughly a
third of R per round trip, which the forward results must overcome.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from app.strategies.v6.base import Setup, V6Strategy, close_location, scale

MIN_SIGNAL_BARS = 60
MIN_HOURLY_BARS = 57                     # EMA 20/50 + a 5-bar slope on 1h


@dataclass
class Params:
    target_r: float = 1.5
    min_stop_pct: float = 0.0045
    max_stop_pct: float = 0.012
    stop_buffer_atr: float = 0.2
    max_hold_min: float = 45.0
    cooldown_bars: int = 1
    hold_to_day_close: bool = False       # True: no time stop, held at most to 1 minute before the UTC midnight


# per-family settings from the 60-day cost study (scripts/v8_cost_study.py, docs/V8_COST_STUDY.json; CONTROL bots, net R
# per trade before -> after, adopted only when better in both halves): V8.1 -0.283 -> -0.107, V8.2 -0.250 -> -0.087,
# V8.3 -0.308 -> -0.105 (with resting take-profits and level fills). Still no family is profitable.
@dataclass
class PullbackParams(Params):
    min_stop_pct: float = 0.010
    hold_to_day_close: bool = True


@dataclass
class BreakoutParams(Params):
    min_stop_pct: float = 0.012
    hold_to_day_close: bool = True


@dataclass
class SnapParams(Params):
    min_stop_pct: float = 0.012
    # 3 hours, not 45 minutes (operator, 2026-10-03: "fix the V8.3 time limit issue"; scripts/v8_snap_hold_study.py,
    # docs/V8_SNAP_HOLD_STUDY.json, 99 days x 6 coins): with a 1.2% stop and a 1.8% target, 87% of trades ended on the
    # clock. 3 h beat 45 min in BOTH halves on net R a trade (-0.098 / -0.101 vs -0.103 / -0.102; 90 min, 6 h and the day
    # close did not) and, with half the trades, nearly halved the total loss (-81 vs -148 USDT, fees 66 vs 130). Still
    # not profitable.
    max_hold_min: float = 180.0


class ScalpV8(V6Strategy):
    version = "v8"
    Params = Params
    expected_hold = "5 to 45 minutes"
    expected_frequency = "target 10+ per bot per day"
    fails_when = "fees and spread outrun small targets; no demonstrated edge"

    @classmethod
    def for_class(cls, horizon: str = "SCALP", feed=None, market=None):
        if horizon != "SCALP":
            raise ValueError("V8 only supports SCALP")
        return type(cls.__name__ + "_SCALP", (cls,), {
            "horizon": horizon, "signal_tf": "5m", "ctx_fast": "15m", "ctx_slow": "1h", "context_tf": "15m",
            "native_timeframe": "5m", "supported_timeframes": ("5m",), "timeframes": ("5m", "15m", "1h"),
            "feed": feed, "market": market, "time_stop_h": Params.max_hold_min / 60.0, "tag": "5M",
            "day_bars": 288, "journal": {}, "__module__": cls.__module__})

    def scalp_setup(self, ctx, c, cs, atr, trend15, trend1h):
        raise NotImplementedError

    def on_candle(self, c, ctx):
        if not self.bound(c):
            return []
        cs = self.series(ctx, c.symbol, "5m")
        if len(cs) < MIN_SIGNAL_BARS or len(ctx.candles(c.symbol, "1h")) < MIN_HOURLY_BARS:
            return []
        atr = self.atr(ctx, c.symbol, "5m")
        if not atr or c.close <= 0:
            return []
        st = self.scalp_setup(ctx, c, cs, atr, self.ctx_trend(ctx, c.symbol, "15m"), self.ctx_trend(ctx, c.symbol, "1h"))
        if st is None:
            return []
        p = self.params
        dist = (c.close - st.stop_ref) if st.side == "long" else (st.stop_ref - c.close)
        raw = (dist + p.stop_buffer_atr * atr) / c.close
        if dist <= 0 or raw > p.max_stop_pct:
            return []
        stop_pct = max(raw, p.min_stop_pct)
        sig = self.entry(c=c, ctx=ctx, side=st.side, price=c.close, stop_pct=stop_pct, tps_r=[(p.target_r, 1.0)],
                         trail_atr=None, be_at_r=None, expected_move_pct=p.target_r * stop_pct,
                         expected_move_source=f"{p.target_r:g}R scalp target", factors=st.factors, reason=st.note,
                         extra={"setup": st.note, "horizon": "SCALP", "target_r": p.target_r,
                                "time_stop_h": p.max_hold_min / 60.0, "stop_pct": round(stop_pct, 5)})
        sig.max_hold_s = int(p.max_hold_min * 60)
        if p.hold_to_day_close:              # no time stop: out at the stop, the target, or 1 minute before midnight
            close_at = (c.close_time // 86_400_000 + 1) * 86_400_000 - 60_000
            sig.max_hold_s = max(60, int((close_at - (c.close_time + 60_000)) / 1000))
        sig.id = f"{self.id}:SCALP:{c.close_time}:{st.side}"
        return [sig]


class MicroPullbackV8(ScalpV8):
    id = "V8.1"
    name = "Micro pullback scalp"
    family = "SCALP_PULLBACK"
    thesis = "a 5m dip to EMA20 inside the 15m trend resumes with a close through the previous bar"
    Params = PullbackParams

    def scalp_setup(self, ctx, c, cs, atr, trend15, trend1h):
        if trend15 not in ("up", "down"):
            return None
        side = "long" if trend15 == "up" else "short"
        if trend1h == ("down" if side == "long" else "up"):
            return None
        ema = ctx.ind(c.symbol, "5m", "ema", n=20)
        if not ema or ema[-1] is None:
            return None
        e = ema[-1]
        prior = cs[-4:-1]
        long = side == "long"
        touched = any(x.low <= e + 0.35 * atr if long else x.high >= e - 0.35 * atr for x in prior)
        resumed = (c.close > cs[-2].high and c.close > e) if long else (c.close < cs[-2].low and c.close < e)
        loc = close_location(c) if long else 1 - close_location(c)
        if not touched or not resumed or loc < 0.50 or abs(c.close - e) > 1.5 * atr:
            return None
        extreme = min(x.low for x in cs[-4:]) if long else max(x.high for x in cs[-4:])
        return Setup(side, extreme, {"close": scale(loc, 0.5, 1.0), "trend": 1.0 if trend1h == trend15 else 0.5},
                     "5m pullback resumed in the 15m trend")


class MicroBreakoutV8(ScalpV8):
    id = "V8.2"
    name = "Micro breakout scalp"
    family = "SCALP_BREAKOUT"
    thesis = "a 5m close outside the prior 30-minute range on above-median volume"
    Params = BreakoutParams

    def scalp_setup(self, ctx, c, cs, atr, trend15, trend1h):
        prior = cs[-7:-1]
        typical = statistics.median(x.volume for x in prior)
        if typical <= 0 or c.volume < 1.1 * typical or c.high - c.low > 2.5 * atr:
            return None
        if c.close > max(x.high for x in prior):
            side = "long"
        elif c.close < min(x.low for x in prior):
            side = "short"
        else:
            return None
        if trend1h == ("down" if side == "long" else "up"):
            return None
        loc = close_location(c) if side == "long" else 1 - close_location(c)
        if loc < 0.55:
            return None
        extreme = min(x.low for x in cs[-3:]) if side == "long" else max(x.high for x in cs[-3:])
        return Setup(side, extreme, {"close": scale(loc, 0.5, 1.0), "volume": scale(c.volume / typical, 1.0, 2.5)},
                     "5m volume breakout of the 30-minute range")


class VwapSnapV8(ScalpV8):
    id = "V8.3"
    name = "VWAP snap-back scalp"
    family = "SCALP_VWAP_REVERSION"
    thesis = "a 5m stretch of 1.8+ ATR from the rolling 4h VWAP snaps back after a reversal bar"
    expected_hold = "5 minutes to 3 hours"
    Params = SnapParams

    def scalp_setup(self, ctx, c, cs, atr, trend15, trend1h):
        window = cs[-48:]
        vol = sum(x.volume for x in window)
        if vol <= 0:
            return None
        vwap = sum((x.high + x.low + x.close) / 3.0 * x.volume for x in window) / vol
        stretch = (cs[-2].close - vwap) / atr
        prev = cs[-2]
        if -4.0 <= stretch <= -1.8 and c.close > c.open and c.close > prev.close and close_location(c) >= 0.6:
            side = "long"
        elif 1.8 <= stretch <= 4.0 and c.close < c.open and c.close < prev.close and close_location(c) <= 0.4:
            side = "short"
        else:
            return None
        extreme = min(x.low for x in cs[-3:]) if side == "long" else max(x.high for x in cs[-3:])
        return Setup(side, extreme, {"stretch": scale(abs(stretch), 1.8, 3.5)}, "5m VWAP snap-back")


def load_v8_scalpers():
    return {cls.id: cls for cls in (MicroPullbackV8, MicroBreakoutV8, VwapSnapV8)}
