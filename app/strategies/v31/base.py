"""Shared machinery for V3.1 strategies (docs/V31_PROTOCOL.md).

V3.1 answers the V3 root-cause analysis (app/competition/v31_diagnostics.py, DEVELOPMENT data only):

* 3m/5m entries had NO directional edge at any horizon (signed drift after entry negative at 15m..4h),
  so a small timeframe may only TIME an entry into a structure defined on 15m/1h/4h -- it never
  defines the trade on its own;
* the only positive drift was continuation (flow momentum, pullback-in-trend, and stretched moves
  that V3 wrongly faded) at 15m/30m, building over 4-12 hours -- while V3 exits closed those trades
  after 30-120 minutes (break-even at 1R, 1.2-1.5R targets, trails on the signal timeframe's ATR);
* fades (range rejection, fast mean reversion) were strongly wrong-way: V3.1 has none.

So every V3.1 entry is a continuation entry aligned with the higher-timeframe trend, and every exit
lets the winner run: break-even only after 1.5R, a partial at 2R+, the runner trailed on the 1h ATR,
a 12 hour maximum hold. Stops stay structural, clamped to 0.6%..2.5% of price.
"""
from __future__ import annotations

from typing import Any, ClassVar, Sequence

from app.core.types import Candle, Signal, TrailSpec, tf_ms
from app.strategies.base import MarketContext
from app.strategies.v3.base import CONTEXT, V3Strategy, clamp01, close_location, scale  # noqa: F401

V31_TIMEFRAMES: tuple[str, ...] = ("3m", "5m", "15m", "30m")
MIN_STOP_PCT = 0.006
MAX_STOP_PCT = 0.025
RUNNER_TF = "1h"


class V31Strategy(V3Strategy):
    version: ClassVar[str] = "v3.1"
    supported_timeframes: ClassVar[tuple[str, ...]] = V31_TIMEFRAMES
    timeframes: ClassVar[tuple[str, ...]] = V31_TIMEFRAMES + ("1h", "4h")
    max_hold_bars: ClassVar[int] = 0              # V3.1 holds by clock, not by bar count
    max_hold_hours: ClassVar[float] = 12.0
    thesis: ClassVar[str] = ""
    why_v31: ClassVar[str] = ""
    expected_hold: ClassVar[str] = ""
    expected_frequency: ClassVar[str] = ""

    @classmethod
    def for_timeframe(cls, tf: str) -> type["V31Strategy"]:
        if tf not in V31_TIMEFRAMES:
            raise ValueError(f"{cls.__name__} does not support {tf}")
        fast, slow = CONTEXT[tf]
        subs = tuple(dict.fromkeys((tf, fast, slow, RUNNER_TF, "4h", "1m")))
        return type(f"{cls.__name__}_{tf}", (cls,), {
            "signal_tf": tf, "ctx_fast": fast, "ctx_slow": slow, "context_tf": slow,
            "native_timeframe": tf, "supported_timeframes": (tf,), "timeframes": subs,
            "__module__": cls.__module__})

    @staticmethod
    def stop_pct(price: float, raw_distance: float) -> float | None:
        if price <= 0 or raw_distance <= 0:
            return None
        pct = raw_distance / price
        if pct > MAX_STOP_PCT:
            return None
        return max(pct, MIN_STOP_PCT)

    def aligned(self, ctx: MarketContext, symbol: str, side: str) -> tuple[bool, dict[str, Any]]:
        """Both context timeframes trend with the trade (the regime alignment V3 lacked)."""
        want = "up" if side == "long" else "down"
        fast = self.trend(ctx, symbol, self.ctx_fast)
        slow = self.trend(ctx, symbol, self.ctx_slow)
        info = {"fast": (fast or {}).get("direction"), "slow": (slow or {}).get("direction"),
                "slow_slope": (slow or {}).get("slope")}
        return bool(fast and slow and fast["direction"] == want and slow["direction"] == want), info

    def vol_band(self, ctx: MarketContext, symbol: str) -> str:
        """1h ATR as a fraction of price, ranked against its own last 100 hours: LOW / MID / HIGH."""
        atr = ctx.ind(symbol, RUNNER_TF, "atr", n=14)
        cs = ctx.candles(symbol, RUNNER_TF)
        vals = [a / c.close for a, c in zip(atr[-100:], list(cs)[-100:]) if a and c.close]
        if len(vals) < 30:
            return "UNKNOWN"
        rank = sum(1 for v in vals if v <= vals[-1]) / len(vals)
        return "LOW" if rank < 0.33 else "HIGH" if rank > 0.67 else "MID"

    def regime(self, ctx: MarketContext, symbol: str, side: str) -> str:
        """The 4h trend relative to the trade: WITH / AGAINST / FLAT (UNKNOWN before warmup)."""
        h4 = self.trend(ctx, symbol, "4h")
        if h4 is None:
            return "UNKNOWN"
        if h4["direction"] == "flat":
            return "FLAT"
        return "WITH" if h4["direction"] == ("up" if side == "long" else "down") else "AGAINST"

    def runner_entry(self, *, c: Candle, ctx: MarketContext, side: str, price: float, stop_pct: float,
                     tp_r: float, tp_frac: float, be_at_r: float, trail_mult: float,
                     factors: dict[str, float], reason: str, extra: dict[str, Any] | None = None) -> Signal:
        """A long-hold continuation entry: partial at `tp_r`, runner trailed on the 1h ATR."""
        runner_atr = self.atr(ctx, c.symbol, RUNNER_TF)
        target = tp_r * stop_pct
        move = min(target, 1.5 * runner_atr / price) if runner_atr else target
        sig = self.entry(c=c, ctx=ctx, side=side, price=price, stop_pct=stop_pct,
                         tps_r=[(tp_r, tp_frac)], trail_atr=None, be_at_r=be_at_r,
                         expected_move_pct=move,
                         expected_move_source=f"strategy: min({tp_r:g}R partial, 1.5 ATR of {RUNNER_TF})",
                         factors=factors, reason=reason,
                         extra={"thesis": self.thesis, "vol_band": self.vol_band(ctx, c.symbol),
                                "regime": self.regime(ctx, c.symbol, side),
                                "runner": f"{trail_mult:g} x ATR({RUNNER_TF}) trail after the {tp_r:g}R partial",
                                "expected_hold": self.expected_hold, **(extra or {})})
        sig.trail = TrailSpec("atr", RUNNER_TF, trail_mult, 14, True)
        sig.max_hold_s = int(self.max_hold_hours * 3600)
        return sig

    @staticmethod
    def window(cs: Sequence[Candle], n: int) -> list[Candle]:
        return list(cs)[-n:]

    def cooldown_ms(self) -> int:
        return int(getattr(self.params, "cooldown_bars", 0) or 0) * tf_ms(self.signal_tf or "1m")
