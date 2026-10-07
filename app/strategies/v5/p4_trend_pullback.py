"""V5.4 - Trend pullback with supportive positioning.

Hypothesis: a pullback to value inside a context trend resumes when positioning is NOT crowded in the trend's
direction (funding not in its top 30% for longs / bottom 30% for shorts) and open interest is not collapsing -- the
trend still has fuel and nobody is over-extended.
Expected hold 8-48 h; ~10-30 trades/month hourly, ~3-8 swing; seeks 2-5%.
Fails at trend exhaustion.
New vs V1-V4: V3 S33 / V3.1 S33.1 / V4.1 were 1m-30m pullbacks with no positioning condition; S33.1 15m was the only
V1-V4 cell with a replicated positive gross, which V5.4 re-asks at 1h / 4h with positioning.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v5.base import Setup, V5Strategy, close_location, scale


@dataclass
class Params:
    touch_atr: float = P(0.5, min=0.0, max=2.0, step=0.1, label="touch of the context EMA20 (signal ATR)")
    pullback_bars: int = P(6, min=2, max=24, step=1, label="pullback window (signal bars)")
    max_crowding_pct: float = P(0.70, min=0.3, max=1.0, step=0.05, label="funding percentile with the trade <=")
    min_oi_change: float = P(-0.05, min=-0.3, max=0.0, step=0.01, label="OI change 24h >=")
    cooldown_bars: int = P(2, min=0, max=50, step=1, label="cooldown (signal bars)")


class TrendPullbackV5(V5Strategy):
    id = "V5.4"
    name = "Trend Pullback Positioning v5"
    family = "TREND_PULLBACK_POSITIONING"
    hypothesis = "a context-trend pullback to value resumes while positioning is not crowded and OI is not collapsing"
    thesis = "both context trends agree, a pullback to the context EMA20, funding not crowded, OI not collapsing, resumption"
    fails_when = "trend exhaustion"
    expected_hold = "8-48 h"
    expected_frequency = "hourly ~10-30 / month, swing ~3-8 / month"
    why_new = "V1-V4 pullbacks were 1m-30m and ignored funding and open interest"
    Params = Params
    doc = StrategyDoc(
        idea="Buy a pullback to the context EMA inside an uptrend when funding is not crowded and OI holds (mirror).",
        timeframe="1h or 4h signal; 1m execution", symbols="one coin per bot",
        entry="both context trends up; within the last 6 signal bars a low within 0.5 ATR of the context EMA20; funding "
              "percentile <= 0.70; OI change 24h >= -5%; the signal bar closes above the previous high and its own EMA20",
        stop="beyond the pullback extreme + 0.25 ATR, 0.8%-8%", targets="time stop; DEVELOPMENT-selected exit after Stage 2",
        sizing="AGGRESSIVE_V5 TAKE 1%", why_aggressive="participates in every uncrowded trend pullback")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any]) -> Setup | None:
        p = self.params
        t1, t2 = self.ctx_trend(ctx, c.symbol, self.ctx_fast), self.ctx_trend(ctx, c.symbol, self.ctx_slow)
        if t1 is None or t1 != t2 or t1 == "flat":
            return None
        side = "long" if t1 == "up" else "short"
        fp, oi = pos.get("funding_pct_90d"), pos.get("oi_chg_24h")
        if fp is None or oi is None or oi < p.min_oi_change:
            return None
        crowd = fp if side == "long" else 1.0 - fp
        if crowd > p.max_crowding_pct:
            return None
        ema_c1 = ctx.ind(c.symbol, self.ctx_fast, "ema", n=20)
        ema_s = ctx.ind(c.symbol, self.signal_tf, "ema", n=20)
        atr = self.atr(ctx, c.symbol, self.signal_tf)
        if not ema_c1 or ema_c1[-1] is None or not ema_s or ema_s[-1] is None or not atr:
            return None
        k = int(p.pullback_bars)
        recent = list(cs)[-k - 1:-1]
        prev = cs[-2]
        e1 = ema_c1[-1]
        if side == "long":
            touched = any(x.low <= e1 + p.touch_atr * atr for x in recent)
            go = c.close > prev.high and c.close > ema_s[-1]
            extreme = min(x.low for x in recent + [c])
        else:
            touched = any(x.high >= e1 - p.touch_atr * atr for x in recent)
            go = c.close < prev.low and c.close < ema_s[-1]
            extreme = max(x.high for x in recent + [c])
        if not (touched and go):
            return None
        cl = close_location(c) if side == "long" else 1.0 - close_location(c)
        return Setup(side, extreme, {"uncrowded": scale(p.max_crowding_pct - crowd, 0.0, p.max_crowding_pct),
                                     "close": scale(cl, 0.5, 1.0)}, "uncrowded pullback resumed")
