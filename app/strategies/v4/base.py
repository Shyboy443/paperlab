"""Shared machinery for V4 intraday specialists (docs/V4_PROTOCOL.md).

V4 answers the root-cause analysis (docs/V4_ROOT_CAUSE.md, DEVELOPMENT data only):

* the V1-V3.1 entries had NO raw edge: gross PnL was negative before any fee for 27 of 30 V3 family x
  timeframe cells and for the V3 program as a whole (-5.4 bps of turnover), so the costs only decided
  how fast the books lost;
* the exits were NOT the cause: stopped trades reached +2R afterwards no more often than random entries
  with the same stop (52.7% vs 51.4%), and price did not keep going after winning exits (-0.09R over
  the next 4h) -- no exit rewrite can create an edge the entries do not have;
* the small timeframes (1m/3m/5m) paid 3-4x the fees per unit of edge, and the only cells with positive
  gross were continuation entries on 15m / 30m structure (pullback continuation, flow momentum).

So every V4 bot is a THREE-LAYER specialist:

    trend      1h EMA trend (20/50 + slope): the direction a trade may take (the failed-breakout family
               is the one explicit exception and says why)
    structure  the family's setup on 15m (3m / 5m / 15m bots) or 30m (30m bots): a pullback, a squeeze,
               a retest, a flag, a failed breakout, a range rejection, a flow breakout
    trigger    the bot's own timeframe: a 3m / 5m bot enters on the first trigger bar that resumes in the
               setup's direction within one structure bar; a 15m / 30m bot enters on the setup bar's close

and one EXIT POLICY (the exit analysis found nothing to fix, so it is plain and uniform): a structural stop
clamped to 0.6%..2.5% of price (the fee gate needs >= 0.44%), 50% at +1.5R, break-even once +1.5R is reached,
the rest trailed at 2 x ATR of the structure timeframe, and a maximum hold of 180 min (3m / 5m) or 240 min
(15m / 30m): a larger structural move, not a scalp.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Sequence

from app.core.types import Candle, Signal, TrailSpec, tf_ms
from app.strategies.base import MarketContext
from app.strategies.v3.base import V3Strategy, clamp01, close_location, scale  # noqa: F401

V4_TIMEFRAMES: tuple[str, ...] = ("3m", "5m", "15m", "30m")
TREND_TF = "1h"
REGIME_TF = "4h"
STRUCTURE: dict[str, str] = {"3m": "15m", "5m": "15m", "15m": "15m", "30m": "30m"}
MAX_HOLD_MIN: dict[str, int] = {"3m": 180, "5m": 180, "15m": 240, "30m": 240}
MIN_STOP_PCT = 0.006
MAX_STOP_PCT = 0.025
TP_R = 1.5
TP_FRAC = 0.5
BE_AT_R = 1.5
TRAIL_ATR = 2.0
STOP_BUFFER_ATR = 0.25


@dataclass
class Setup:
    """A structure-timeframe setup: the side, the bar that completed it, the stop reference (the price
    beyond which the idea is wrong) and the named, bounded quality factors."""
    side: str
    bar_close_ts: int
    stop_ref: float
    factors: dict[str, float] = field(default_factory=dict)
    note: str = ""


class V4Strategy(V3Strategy):
    version: ClassVar[str] = "v4"
    supported_timeframes: ClassVar[tuple[str, ...]] = V4_TIMEFRAMES
    timeframes: ClassVar[tuple[str, ...]] = V4_TIMEFRAMES + ("1h", "4h")
    structure_tf: ClassVar[str] = ""
    trend_aligned: ClassVar[bool] = True           # False only for the failed-breakout reversal
    thesis: ClassVar[str] = ""
    evidence: ClassVar[str] = ""
    expected_hold: ClassVar[str] = "30-180 min (3m/5m), 30-240 min (15m/30m)"
    expected_frequency: ClassVar[str] = ""
    warmup_bars = 60
    max_hold_bars: ClassVar[int] = 0

    @classmethod
    def for_timeframe(cls, tf: str) -> type["V4Strategy"]:
        if tf not in V4_TIMEFRAMES:
            raise ValueError(f"{cls.__name__} does not support {tf}")
        s = STRUCTURE[tf]
        subs = tuple(dict.fromkeys((tf, s, TREND_TF, REGIME_TF, "1m")))
        return type(f"{cls.__name__}_{tf}", (cls,), {
            "signal_tf": tf, "structure_tf": s, "ctx_fast": s if s != tf else TREND_TF, "ctx_slow": TREND_TF,
            "context_tf": TREND_TF, "native_timeframe": tf, "supported_timeframes": (tf,), "timeframes": subs,
            "__module__": cls.__module__})

    # -- the three layers -----------------------------------------------------------------------------
    def trend_side(self, ctx: MarketContext, symbol: str) -> tuple[str | None, dict[str, Any] | None]:
        t = self.trend(ctx, symbol, TREND_TF)
        if t is None or t["direction"] == "flat":
            return None, t
        return ("long" if t["direction"] == "up" else "short"), t

    def setup(self, ctx: MarketContext, symbol: str, cs: Sequence[Candle], atr: float,
              trend: dict[str, Any] | None) -> Setup | None:
        """The family's structure-timeframe pattern, completed on the LAST closed structure bar `cs[-1]`."""
        raise NotImplementedError

    def trigger(self, c: Candle, ctx: MarketContext, st: Setup) -> tuple[bool, dict[str, float]]:
        """3m / 5m: the first trigger bar that resumes in the setup's direction -- it closes beyond the
        previous trigger bar's extreme, in the outer 40% of its range, and has not broken the stop
        reference. 15m / 30m bots enter on the setup bar itself (trigger timeframe = structure)."""
        if self.signal_tf == self.structure_tf:
            return c.close_time == st.bar_close_ts, {}
        cs = self.series(ctx, c.symbol, self.signal_tf)
        if len(cs) < 20:
            return False, {}
        prev = cs[-2]
        cl = close_location(c) if st.side == "long" else 1.0 - close_location(c)
        if st.side == "long":
            ok = c.close > prev.high and c.low > st.stop_ref
        else:
            ok = c.close < prev.low and c.high < st.stop_ref
        return ok and cl >= 0.6, {"trigger_close": scale(cl, 0.6, 1.0)}

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        s_tf = self.structure_tf
        cs = self.series(ctx, c.symbol, s_tf)
        if len(cs) < 60:
            return []
        last = cs[-1]
        if self.signal_tf != s_tf:
            # the setup must be FRESH: completed on the structure bar that closed at most one structure
            # bar ago, and each setup is traded at most once
            if c.close_time - last.close_time > tf_ms(s_tf) or getattr(self, "_last_setup", None) == last.close_time:
                return []
        memo = getattr(self, "_memo", None)
        if memo is not None and memo[0] == last.close_time:        # one evaluation per structure bar
            _, st, side, trend, atr = memo
        else:
            atr = self.atr(ctx, c.symbol, s_tf)
            side, trend = self.trend_side(ctx, c.symbol)
            st = self.setup(ctx, c.symbol, cs, atr, trend) if atr else None
            self._memo = (last.close_time, st, side, trend, atr)
        if st is None or not atr:
            return []
        if self.trend_aligned and st.side != side:
            return []
        ok, tf_factors = self.trigger(c, ctx, st)
        if not ok:
            return []
        price = ctx.last_price(c.symbol) or c.close
        dist = (price - st.stop_ref) if st.side == "long" else (st.stop_ref - price)
        sp = self.stop_pct(price, dist + STOP_BUFFER_ATR * atr) if dist > 0 else None
        if sp is None:
            return []
        self._last_setup = last.close_time
        factors = {**st.factors, **tf_factors,
                   "trend": scale(abs((trend or {}).get("slope") or 0.0), 0.0, 0.01)}
        return [self.v4_entry(c=c, ctx=ctx, side=st.side, price=price, stop_pct=sp, atr_s=atr, factors=factors,
                              reason=f"{s_tf} {st.note} -> {self.signal_tf} trigger; 1h {(trend or {}).get('direction')}")]

    # -- the uniform V4 exit ------------------------------------------------------------------------------
    @staticmethod
    def stop_pct(price: float, raw_distance: float) -> float | None:
        if price <= 0 or raw_distance <= 0:
            return None
        pct = raw_distance / price
        if pct > MAX_STOP_PCT:
            return None
        return max(pct, MIN_STOP_PCT)

    def v4_entry(self, *, c: Candle, ctx: MarketContext, side: str, price: float, stop_pct: float, atr_s: float,
                 factors: dict[str, float], reason: str) -> Signal:
        move = min(TP_R * stop_pct, 2.0 * atr_s / price)
        sig = self.entry(c=c, ctx=ctx, side=side, price=price, stop_pct=stop_pct, tps_r=[(TP_R, TP_FRAC)],
                         trail_atr=None, be_at_r=BE_AT_R, expected_move_pct=move,
                         expected_move_source=f"min({TP_R:g}R partial, 2 ATR of {self.structure_tf})",
                         factors=factors, reason=reason,
                         extra={"thesis": self.thesis, "structure_tf": self.structure_tf, "trend_tf": TREND_TF,
                                "vol_band": self.vol_band(ctx, c.symbol), "regime": self.regime(ctx, c.symbol, side),
                                "runner": f"{TRAIL_ATR:g} x ATR({self.structure_tf}) trail after the {TP_R:g}R partial",
                                "expected_hold": self.expected_hold})
        sig.trail = TrailSpec("atr", self.structure_tf, TRAIL_ATR, 14, True)
        sig.max_hold_s = MAX_HOLD_MIN[self.signal_tf] * 60
        return sig

    # -- context labels (information for the analyzer and Jev; never a gate) ---------------------------
    def vol_band(self, ctx: MarketContext, symbol: str) -> str:
        atr = ctx.ind(symbol, TREND_TF, "atr", n=14)
        cs = ctx.candles(symbol, TREND_TF)
        vals = [a / x.close for a, x in zip(atr[-100:], list(cs)[-100:]) if a and x.close]
        if len(vals) < 30:
            return "UNKNOWN"
        rank = sum(1 for v in vals if v <= vals[-1]) / len(vals)
        return "LOW" if rank < 0.33 else "HIGH" if rank > 0.67 else "MID"

    def regime(self, ctx: MarketContext, symbol: str, side: str) -> str:
        h4 = self.trend(ctx, symbol, REGIME_TF)
        if h4 is None:
            return "UNKNOWN"
        if h4["direction"] == "flat":
            return "FLAT"
        return "WITH" if h4["direction"] == ("up" if side == "long" else "down") else "AGAINST"

    def cooldown_ms(self) -> int:
        return int(getattr(self.params, "cooldown_bars", 0) or 0) * tf_ms(self.signal_tf or "1m")

    # -- small helpers the families share ------------------------------------------------------------------
    @staticmethod
    def box(cs: Sequence[Candle], n: int, skip: int = 0) -> tuple[float, float]:
        """(high, low) of the n bars before the last `skip` bars."""
        part = list(cs)[-(n + skip):len(cs) - skip] if skip else list(cs)[-n:]
        return max(x.high for x in part), min(x.low for x in part)

    @staticmethod
    def taker_share(c: Candle) -> float | None:
        return (c.taker_buy_volume / c.volume) if c.volume > 0 and c.taker_buy_volume > 0 else None
