"""Shared machinery for the V6 forward families (docs/V6_PROTOCOL.md §2-§4).

A V6 family is bound to ONE class per bot by `for_class(horizon, feed, market)`:

    HOURLY   decides on every closed 1h candle; context 4h + 1D; time stop 24 h
    SWING    decides on every closed 4h candle; context 1D;       time stop 72 h

The decision instant is H = the signal candle's close_time + 1 ms (an exact hour). At H the bot reads its own coin's
candles (aggregated from the 1m tape), its coin's Bybit positioning (app/competition/v6_features.PositioningFeedV6)
and the shared market context (BTC, ETH, breadth, aggregate funding and open interest: MarketContextV6). It trades
only its own coin. Nothing is read that was stamped after H.

One exit for every family: a structural stop (the setup's own extreme + 0.25 ATR of the signal timeframe, clamped to
at least 1.0% of price and refused beyond 6.0%), a 3R take-profit on the whole position, and the class time stop. No
trail, no break-even, no partials. `signal_quality` is the mean of named factors in [0, 1]; the sizing policy reads it
(docs/V6_PROTOCOL.md §5: strong >= 0.60, very high conviction >= 0.85).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Sequence

from app.competition.v6_features import regime as market_regime
from app.core.types import Candle, Signal, tf_ms
from app.strategies.base import MarketContext
from app.strategies.v3.base import V3Strategy, clamp01, close_location, scale  # noqa: F401

V6_CLASSES: dict[str, dict[str, Any]] = {
    "HOURLY": {"signal": "1h", "c1": "4h", "c2": "1d", "time_stop_h": 24, "tag": "1H", "day_bars": 24},
    "SWING": {"signal": "4h", "c1": "1d", "c2": "1d", "time_stop_h": 72, "tag": "4H", "day_bars": 6},
}
MIN_STOP_PCT = 0.010
MAX_STOP_PCT = 0.060
STOP_BUFFER_ATR = 0.25
TARGET_R = 3.0
MIN_SIGNAL_BARS = 60
MIN_C1_BARS = 40


@dataclass
class Setup:
    side: str
    stop_ref: float
    factors: dict[str, float] = field(default_factory=dict)
    note: str = ""


class V6Strategy(V3Strategy):
    version: ClassVar[str] = "v6"
    horizon: ClassVar[str] = ""
    feed: ClassVar[Any] = None
    market: ClassVar[Any] = None
    time_stop_h: ClassVar[float] = 24.0
    tag: ClassVar[str] = ""
    day_bars: ClassVar[int] = 24
    thesis: ClassVar[str] = ""
    fails_when: ClassVar[str] = ""
    expected_hold: ClassVar[str] = ""
    expected_frequency: ClassVar[str] = ""
    source: ClassVar[str] = ""
    supported_timeframes: ClassVar[tuple[str, ...]] = ("1h", "4h")
    timeframes: ClassVar[tuple[str, ...]] = ("1h", "4h", "1d")
    max_hold_bars: ClassVar[int] = 0
    warmup_bars = 60
    default_leverage = 20
    max_positions = 1
    min_rr = 0.0

    @classmethod
    def for_class(cls, horizon: str, feed: Any = None, market: Any = None) -> type["V6Strategy"]:
        spec = V6_CLASSES[horizon]
        subs = tuple(dict.fromkeys((spec["signal"], spec["c1"], spec["c2"])))
        return type(f"{cls.__name__}_{horizon}", (cls,), {
            "horizon": horizon, "signal_tf": spec["signal"], "ctx_fast": spec["c1"], "ctx_slow": spec["c2"],
            "context_tf": spec["c1"], "native_timeframe": spec["signal"], "supported_timeframes": (spec["signal"],),
            "timeframes": subs, "feed": feed, "market": market, "time_stop_h": float(spec["time_stop_h"]),
            "tag": spec["tag"], "day_bars": spec["day_bars"], "journal": {}, "__module__": cls.__module__})

    # -- context ----------------------------------------------------------------------------------------
    def ctx_trend(self, ctx: MarketContext, symbol: str, tf: str) -> str | None:
        """up / down / flat: EMA 20/50 and the slow EMA's 5-bar slope; on 1D, EMA 10/30 and a 3-bar slope."""
        t = self.trend(ctx, symbol, tf, 10, 30, 3) if tf == "1d" else self.trend(ctx, symbol, tf)
        return t["direction"] if t else None

    def positioning(self, t: int) -> dict[str, Any]:
        return self.feed.snapshot(t) if self.feed is not None else {}

    def market_ctx(self, t: int) -> dict[str, Any]:
        return self.market.snapshot(t) if self.market is not None else {}

    def oi_at(self, ts: int, t: int) -> float | None:
        """Open interest stamped at or before `ts`, as the decision at t could see it (capped by t's watermark)."""
        if self.feed is None:
            return None
        cap = self.feed.cap(t, "oi")
        return self.feed.oi.at_or_before(min(int(ts), int(t)) if cap is None else min(int(ts), int(t), int(cap)))

    @staticmethod
    def ret(cs: Sequence[Candle], n: int) -> float | None:
        if len(cs) <= n or cs[-1 - n].close <= 0:
            return None
        return cs[-1].close / cs[-1 - n].close - 1.0

    @staticmethod
    def alignment(mkt: dict[str, Any], side: str) -> float:
        """1 when BTC's 1D and 4h trends both agree with the trade, 0 when both oppose it, 0.5 in between."""
        r = market_regime(mkt, side)
        return {"WITH": 1.0, "MIXED": 0.5, "AGAINST": 0.0}.get(r, 0.5)

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any],
              mkt: dict[str, Any]) -> Setup | None:
        raise NotImplementedError

    # -- the decision -------------------------------------------------------------------------------------
    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        cs = self.series(ctx, c.symbol, self.signal_tf)
        if len(cs) < MIN_SIGNAL_BARS or len(ctx.candles(c.symbol, self.ctx_fast)) < MIN_C1_BARS:
            return []
        t = int(c.close_time) + 1                                   # the decision instant H
        pos = self.positioning(t)
        mkt = self.market_ctx(t)
        st = self.setup(ctx, c, cs, pos, mkt)
        if st is None:
            return []
        atr = self.atr(ctx, c.symbol, self.signal_tf)
        price = ctx.last_price(c.symbol) or c.close
        if not atr or price <= 0:
            return []
        dist = (price - st.stop_ref) if st.side == "long" else (st.stop_ref - price)
        if dist <= 0:
            return []
        raw = (dist + STOP_BUFFER_ATR * atr) / price
        if raw > MAX_STOP_PCT:
            return []
        return [self.v6_entry(c=c, ctx=ctx, st=st, price=price, stop_pct=max(raw, MIN_STOP_PCT), atr=atr,
                              pos=pos, mkt=mkt, t=t)]

    def v6_entry(self, *, c: Candle, ctx: MarketContext, st: Setup, price: float, stop_pct: float, atr: float,
                 pos: dict[str, Any], mkt: dict[str, Any], t: int) -> Signal:
        move = TARGET_R * stop_pct
        funding = self.feed.expected_funding(t, st.side, self.time_stop_h) if self.feed is not None else None
        keep = ("funding_rate", "funding_ann", "funding_pct_90d", "funding_chg_24h", "funding_interval_h",
                "funding_age_h", "oi_chg_1h", "oi_chg_4h", "oi_chg_24h", "oi_chg_3d", "oi_pct_30d", "oi_age_h",
                "basis", "basis_pct_30d", "long_ratio", "long_ratio_chg_24h")
        positioning = {k: pos.get(k) for k in keep}
        market = {"btc": mkt.get("btc"), "eth": mkt.get("eth"), "breadth_1d": mkt.get("breadth_1d"),
                  "agg_funding_median": mkt.get("agg_funding_median"), "agg_oi_chg_24h": mkt.get("agg_oi_chg_24h")}
        extra = {"thesis": self.thesis, "horizon": self.horizon, "decision_ms": t, "structure_tf": self.ctx_fast,
                 "trend_tf": self.ctx_slow, "positioning": positioning, "market": market,
                 "expected_funding_pct": funding, "time_stop_h": self.time_stop_h, "target_r": TARGET_R,
                 "setup": st.note, "trend_c1": self.ctx_trend(ctx, c.symbol, self.ctx_fast),
                 "trend_c2": self.ctx_trend(ctx, c.symbol, self.ctx_slow),
                 "regime": market_regime(mkt, st.side), "vol_band": self.vol_band(ctx, c.symbol),
                 "expected_hold": self.expected_hold}
        sig = self.entry(c=c, ctx=ctx, side=st.side, price=price, stop_pct=stop_pct, tps_r=[(TARGET_R, 1.0)],
                         trail_atr=None, be_at_r=None, expected_move_pct=move,
                         expected_move_source=f"{TARGET_R:g}R target on the structural stop",
                         factors={k: clamp01(v) for k, v in st.factors.items()}, reason=f"{self.tag} {st.note}",
                         extra=extra)
        sig.max_hold_s = int(self.time_stop_h * 3600)
        sig.id = f"{self.id}:{self.horizon}:{c.close_time}:{st.side}"
        journal = getattr(type(self), "journal", None)
        if journal is not None:
            journal[sig.id] = {"setup": st.note, "thesis": self.thesis, "stop_pct": round(stop_pct, 5),
                               "expected_move_pct": round(move, 5), "expected_funding_pct": funding,
                               "positioning": positioning, "market": market, "trend_c1": extra["trend_c1"],
                               "trend_c2": extra["trend_c2"], "regime": extra["regime"], "vol_band": extra["vol_band"],
                               "quality": sig.meta.get("signal_quality"),
                               "factors": {k: round(clamp01(v), 3) for k, v in st.factors.items()}}
        return sig

    def vol_band(self, ctx: MarketContext, symbol: str) -> str:
        atr = ctx.ind(symbol, self.ctx_fast, "atr", n=14)
        cs = list(ctx.candles(symbol, self.ctx_fast))
        vals = [a / x.close for a, x in zip(atr[-100:], cs[-100:]) if a and x.close]
        if len(vals) < 20:
            return "UNKNOWN"
        rank = sum(1 for v in vals if v <= vals[-1]) / len(vals)
        return "LOW" if rank < 0.33 else "HIGH" if rank > 0.67 else "MID"

    def cooldown_ms(self) -> int:
        return int(getattr(self.params, "cooldown_bars", 0) or 0) * tf_ms(self.signal_tf or "1h")

    @staticmethod
    def bb_width_rank(cs: Sequence[Candle], n: int = 20, window: int = 60) -> float | None:
        """Rank (0..1) of the LAST CLOSED bar's Bollinger width among the previous `window` widths."""
        closes = [x.close for x in cs[-(window + n):]]
        widths = []
        for i in range(n, len(closes) + 1):
            w = closes[i - n:i]
            m = sum(w) / n
            sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5
            if m > 0:
                widths.append(4.0 * sd / m)
        if len(widths) < window // 2:
            return None
        return sum(1 for w in widths if w <= widths[-1]) / len(widths)
