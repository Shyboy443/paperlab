"""Shared machinery for V5 hourly / daily positioning strategies (docs/V5_PROTOCOL.md).

A V5 family is bound to ONE horizon class per bot by `for_class(horizon, feed, ...)`:

    HOURLY   decides on every closed 1h candle; context 4h + 1D; baseline time stop 24 h
    SWING    decides on every closed 4h candle; context 1D + a 1W regime; baseline time stop 72 h

and to one symbol's PositioningFeed (app/competition/v5_features.py): funding, open interest, basis and the
long/short account ratio, read CAUSALLY at the signal candle's close. Execution happens later, on the 1m tape.

One exit for every family (docs/V5_PROTOCOL.md §7): a structural stop (the setup's own extreme + 0.25 ATR of the
signal timeframe, 0.8%-8% of price, refused beyond) and a time stop; Stage 2 may add a fixed target, chosen on
DEVELOPMENT only and frozen. No break-even move, no trail, no partial -- the RAW study measures the entries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Sequence

from app.core.types import Candle, Signal, tf_ms
from app.strategies.base import MarketContext
from app.strategies.v3.base import V3Strategy, clamp01, close_location, scale  # noqa: F401

V5_CLASSES: dict[str, dict[str, Any]] = {
    "HOURLY": {"signal": "1h", "c1": "4h", "c2": "1d", "time_stop_h": 24, "lookback": 12, "day_bars": 24},
    "SWING": {"signal": "4h", "c1": "1d", "c2": "1w", "time_stop_h": 72, "lookback": 6, "day_bars": 6},
}
MIN_STOP_PCT = 0.008
MAX_STOP_PCT = 0.08
STOP_BUFFER_ATR = 0.25


@dataclass
class Setup:
    side: str
    stop_ref: float
    factors: dict[str, float] = field(default_factory=dict)
    note: str = ""
    expected_move_pct: float | None = None


class V5Strategy(V3Strategy):
    version: ClassVar[str] = "v5"
    horizon: ClassVar[str] = ""
    feed: ClassVar[Any] = None
    time_stop_h: ClassVar[float] = 24.0
    target_r: ClassVar[float | None] = None
    thesis: ClassVar[str] = ""
    fails_when: ClassVar[str] = ""
    expected_hold: ClassVar[str] = ""
    expected_frequency: ClassVar[str] = ""
    why_new: ClassVar[str] = ""
    supported_timeframes: ClassVar[tuple[str, ...]] = ("1h", "4h")
    timeframes: ClassVar[tuple[str, ...]] = ("1h", "4h", "1d", "1w")
    max_hold_bars: ClassVar[int] = 0
    warmup_bars = 60
    default_leverage = 20
    max_positions = 1

    @classmethod
    def for_class(cls, horizon: str, feed: Any = None, time_stop_h: float | None = None,
                  target_r: float | None = None) -> type["V5Strategy"]:
        spec = V5_CLASSES[horizon]
        subs = (spec["signal"], spec["c1"], spec["c2"])
        return type(f"{cls.__name__}_{horizon}", (cls,), {
            "horizon": horizon, "signal_tf": spec["signal"], "ctx_fast": spec["c1"], "ctx_slow": spec["c2"],
            "context_tf": spec["c1"], "native_timeframe": spec["signal"], "supported_timeframes": (spec["signal"],),
            "timeframes": subs, "feed": feed, "time_stop_h": float(time_stop_h or spec["time_stop_h"]),
            "target_r": target_r, "lookback": spec["lookback"], "day_bars": spec["day_bars"], "journal": {},
            "__module__": cls.__module__})

    # -- context -----------------------------------------------------------------------------------------
    def ctx_trend(self, ctx: MarketContext, symbol: str, tf: str) -> str | None:
        """up / down / flat on 4h and 1D (EMA 20/50 + slope); on 1W a regime: close vs EMA10 and its slope."""
        if tf == "1w":
            cs = ctx.candles(symbol, "1w")
            ema = ctx.ind(symbol, "1w", "ema", n=10)
            if len(cs) < 12 or not ema or ema[-1] is None or ema[-3] is None:
                return None
            up, down = cs[-1].close > ema[-1] and ema[-1] > ema[-3], cs[-1].close < ema[-1] and ema[-1] < ema[-3]
            return "up" if up else "down" if down else "flat"
        t = self.trend(ctx, symbol, tf)
        return t["direction"] if t else None

    def positioning(self, t: int) -> dict[str, Any]:
        return self.feed.snapshot(t) if self.feed is not None else {}

    @staticmethod
    def ret(cs: Sequence[Candle], n: int) -> float | None:
        if len(cs) <= n or cs[-1 - n].close <= 0:
            return None
        return cs[-1].close / cs[-1 - n].close - 1.0

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any]) -> Setup | None:
        raise NotImplementedError

    # -- the decision ----------------------------------------------------------------------------------------
    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        cs = self.series(ctx, c.symbol, self.signal_tf)
        if len(cs) < 60 or len(ctx.candles(c.symbol, self.ctx_fast)) < 55:
            return []
        pos = self.positioning(c.close_time)
        st = self.setup(ctx, c, cs, pos)
        if st is None:
            return []
        atr = self.atr(ctx, c.symbol, self.signal_tf)
        price = ctx.last_price(c.symbol) or c.close
        if not atr or price <= 0:
            return []
        dist = (price - st.stop_ref) if st.side == "long" else (st.stop_ref - price)
        if dist <= 0:
            return []
        sp = self.stop_pct(price, dist + STOP_BUFFER_ATR * atr)
        if sp is None:
            return []
        return [self.v5_entry(c=c, ctx=ctx, st=st, price=price, stop_pct=sp, atr=atr, pos=pos)]

    @staticmethod
    def stop_pct(price: float, raw_distance: float) -> float | None:
        if price <= 0 or raw_distance <= 0:
            return None
        pct = raw_distance / price
        if pct > MAX_STOP_PCT:
            return None
        return max(pct, MIN_STOP_PCT)

    def v5_entry(self, *, c: Candle, ctx: MarketContext, st: Setup, price: float, stop_pct: float, atr: float,
                 pos: dict[str, Any]) -> Signal:
        move = st.expected_move_pct or min(0.08, 2.0 * (self.atr(ctx, c.symbol, self.ctx_fast) or atr) / price)
        funding = self.feed.expected_funding(c.close_time, st.side, self.time_stop_h) if self.feed is not None else None
        keep = ("funding_rate", "funding_ann", "funding_pct_90d", "funding_chg_24h", "funding_interval_h", "oi_chg_1h",
                "oi_chg_4h", "oi_chg_24h", "oi_chg_3d", "oi_pct_30d", "basis", "basis_pct_30d", "long_ratio",
                "long_ratio_chg_24h")
        extra = {"thesis": self.thesis, "horizon": self.horizon, "structure_tf": self.ctx_fast, "trend_tf": self.ctx_slow,
                 "positioning": {k: pos.get(k) for k in keep}, "expected_funding_pct": funding,
                 "time_stop_h": self.time_stop_h, "setup": st.note,
                 "trend_c1": self.ctx_trend(ctx, c.symbol, self.ctx_fast), "trend_c2": self.ctx_trend(ctx, c.symbol, self.ctx_slow),
                 "regime": self.regime_label(ctx, c.symbol, st.side), "vol_band": self.vol_band(ctx, c.symbol),
                 "expected_hold": self.expected_hold}
        sig = self.entry(c=c, ctx=ctx, side=st.side, price=price, stop_pct=stop_pct,
                         tps_r=[(self.target_r, 1.0)] if self.target_r else [], trail_atr=None, be_at_r=None,
                         expected_move_pct=move, expected_move_source=f"min(8%, 2 ATR of {self.ctx_fast}) or the setup's own",
                         factors={**st.factors}, reason=f"{self.horizon} {st.note}", extra=extra)
        sig.max_hold_s = int(self.time_stop_h * 3600)
        sig.id = f"{self.id}:{self.horizon}:{c.close_time}:{st.side}"
        journal = getattr(type(self), "journal", None)
        if journal is not None:              # the entry context of every signal, for the trade detail
            journal[sig.id] = {"setup": st.note, "thesis": self.thesis, "stop_pct": round(stop_pct, 5),
                               "expected_move_pct": round(move, 5), "expected_funding_pct": funding,
                               "positioning": extra["positioning"], "trend_c1": extra["trend_c1"],
                               "trend_c2": extra["trend_c2"], "regime": extra["regime"], "vol_band": extra["vol_band"],
                               "factors": {k: round(v, 3) for k, v in st.factors.items()}}
        return sig

    def regime_label(self, ctx: MarketContext, symbol: str, side: str) -> str:
        d = self.ctx_trend(ctx, symbol, self.ctx_slow)
        if d is None:
            return "UNKNOWN"
        if d == "flat":
            return "FLAT"
        return "WITH" if d == ("up" if side == "long" else "down") else "AGAINST"

    def vol_band(self, ctx: MarketContext, symbol: str) -> str:
        atr = ctx.ind(symbol, self.ctx_fast, "atr", n=14)
        cs = list(ctx.candles(symbol, self.ctx_fast))
        vals = [a / x.close for a, x in zip(atr[-100:], cs[-100:]) if a and x.close]
        if len(vals) < 30:
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
