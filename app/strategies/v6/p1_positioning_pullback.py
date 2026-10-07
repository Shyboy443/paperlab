"""V6.1 - Positioning pullback.

Hypothesis (the one lead V5 produced: V5.4 trend pullback + positioning, replicated only on V5's pseudo-holdout):
inside an established trend, a pullback to value RESUMES when positioning confirms it -- funding is not crowded in the
trend's direction, open interest is not being unwound, and positions are building (not leaving) as price resumes.
Written fresh for V6 with a-priori parameters; nothing here was fitted to V5's development or pseudo-holdout results.
Expected hold 6-24 h (hourly) / 12-72 h (swing); ~10-25 setups / month hourly, ~3-8 swing; seeks 3R.
Fails at trend exhaustion and in chop, where "pullbacks" are the start of reversals.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v6.base import Setup, V6Strategy, close_location, scale


@dataclass
class Params:
    touch_atr: float = P(0.6, min=0.0, max=2.0, step=0.1, label="pullback reaches the context EMA20 within (signal ATR)")
    pullback_bars: int = P(8, min=2, max=24, step=1, label="pullback window (signal bars)")
    max_crowding_pct: float = P(0.80, min=0.3, max=1.0, step=0.05, label="funding percentile in the trade's favour <=")
    min_oi_chg_4h: float = P(0.0, min=-0.1, max=0.1, step=0.005, label="OI change 4h >= (positions building)")
    min_oi_chg_24h: float = P(-0.08, min=-0.3, max=0.0, step=0.01, label="OI change 24h >= (no unwind)")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (signal bars)")


class PositioningPullbackV6(V6Strategy):
    id = "V6.1"
    name = "Positioning Pullback v6"
    family = "POSITIONING_PULLBACK"
    hypothesis = "a trend pullback to value resumes when positioning is uncrowded and building, not unwinding"
    thesis = "context trends agree; pullback to the context EMA20; resumption bar; funding uncrowded; OI building"
    fails_when = "trend exhaustion; chop"
    expected_hold = "6-24 h hourly, 12-72 h swing"
    expected_frequency = "hourly ~10-25 / month, swing ~3-8 / month"
    source = "V5.4 TREND_PULLBACK_POSITIONING (the only V5 raw-edge replication, pseudo-holdout SWING); fresh parameters"
    Params = Params
    doc = StrategyDoc(
        idea="Buy the resumption of a pulled-back uptrend when funding is not crowded and open interest builds (mirror).",
        timeframe="1h (hourly) or 4h (swing) signal; 1m execution", symbols="one coin per bot",
        entry="context trends agree (hourly: 4h and 1D; swing: 4h and 1D); within the last 8 signal bars price came "
              "within 0.6 ATR of the context EMA20; the signal bar closes beyond the previous bar's extreme and its own "
              "EMA20; funding percentile in the trade's favour <= 0.80; OI 4h change >= 0; OI 24h change >= -8%",
        stop="beyond the pullback extreme + 0.25 ATR, 1%-6%", targets="3R, time stop 24 h / 72 h",
        sizing="AGGRESSIVE_V6: 1% base, legal-minimum tiers, ATTACK only via Jev",
        why_aggressive="joins every positioning-confirmed trend resumption")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any],
              mkt: dict[str, Any]) -> Setup | None:
        p = self.params
        trend_fast = self.ctx_trend(ctx, c.symbol, self.ctx_fast)
        trend_slow = self.ctx_trend(ctx, c.symbol, "1d" if self.horizon == "HOURLY" else self.signal_tf)
        if trend_fast not in ("up", "down") or trend_fast != trend_slow:
            return None
        side = "long" if trend_fast == "up" else "short"
        fp, oi4, oi24 = pos.get("funding_pct_90d"), pos.get("oi_chg_4h"), pos.get("oi_chg_24h")
        if fp is None or oi4 is None or oi24 is None:
            return None
        crowd = fp if side == "long" else 1.0 - fp
        if crowd > p.max_crowding_pct or oi4 < p.min_oi_chg_4h or oi24 < p.min_oi_chg_24h:
            return None
        ema_c1 = ctx.ind(c.symbol, self.ctx_fast, "ema", n=20)
        ema_s = ctx.ind(c.symbol, self.signal_tf, "ema", n=20)
        atr = self.atr(ctx, c.symbol, self.signal_tf)
        if not ema_c1 or ema_c1[-1] is None or not ema_s or ema_s[-1] is None or not atr:
            return None
        k = int(p.pullback_bars)
        recent = list(cs)[-k - 1:-1]
        prev = cs[-2]
        anchor = ema_c1[-1]
        if side == "long":
            touched = any(x.low <= anchor + p.touch_atr * atr for x in recent)
            go = c.close > prev.high and c.close > ema_s[-1]
            extreme = min(x.low for x in recent + [c])
        else:
            touched = any(x.high >= anchor - p.touch_atr * atr for x in recent)
            go = c.close < prev.low and c.close < ema_s[-1]
            extreme = max(x.high for x in recent + [c])
        if not (touched and go):
            return None
        cl = close_location(c) if side == "long" else 1.0 - close_location(c)
        return Setup(side, extreme, {"uncrowded": scale(p.max_crowding_pct - crowd, 0.0, p.max_crowding_pct),
                                     "oi_building": scale(oi4, p.min_oi_chg_4h, 0.03),
                                     "close": scale(cl, 0.5, 1.0), "market": self.alignment(mkt, side)},
                     "positioning-confirmed pullback resumed")
