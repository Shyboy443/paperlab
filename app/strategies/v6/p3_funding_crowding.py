"""V6.3 - Funding crowding reversal.

Hypothesis: when one side is crowded -- funding in the extreme decile of its 90-day history AND the futures trading at
an extreme premium (or discount) to the index -- and price is stretched in that side's favour, the first reversal bar
starts a squeeze of the crowded side (longs forced out after a crowded rally, shorts after a crowded selloff).
Expected hold 4-24 h hourly / 12-72 h swing; ~2-8 setups / month hourly, ~1-3 swing; seeks 3R.
Fails when the crowd is right (a persistent trend that keeps paying funding) or the premium is structural.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v6.base import Setup, V6Strategy, close_location, scale


@dataclass
class Params:
    funding_extreme: float = P(0.90, min=0.6, max=0.99, step=0.01, label="funding percentile (90d) crowded >=")
    basis_extreme: float = P(0.80, min=0.5, max=0.99, step=0.01, label="basis percentile (30d) crowded >=")
    stretch_atr: float = P(2.0, min=0.5, max=5.0, step=0.1, label="distance from the signal EMA50 >= (ATR)")
    extreme_bars: int = P(6, min=2, max=24, step=1, label="stop anchor: extreme of the last (bars)")
    cooldown_bars: int = P(4, min=0, max=50, step=1, label="cooldown (signal bars)")


class FundingCrowdingV6(V6Strategy):
    id = "V6.3"
    name = "Funding Crowding Reversal v6"
    family = "FUNDING_CROWDING_REVERSAL"
    hypothesis = "an extreme-funding, extreme-premium crowd stretched in its favour is squeezed after the first reversal bar"
    thesis = "funding >= 90th pct (<= 10th) and basis >= 80th pct (<= 20th); price >= 2 ATR past the EMA50; reversal bar"
    fails_when = "the crowd is right (persistent trend); structural premium"
    expected_hold = "4-24 h hourly, 12-72 h swing"
    expected_frequency = "hourly ~2-8 / month, swing ~1-3 / month"
    source = "V5.2 FUNDING_OI_CROWDING_REVERSAL idea (no V5 raw edge); rebuilt on funding AND basis extremes"
    Params = Params
    doc = StrategyDoc(
        idea="Fade a crowded side (funding and basis both extreme) after a stretched move, on the first reversal bar.",
        timeframe="1h (hourly) or 4h (swing) signal; 1m execution", symbols="one coin per bot",
        entry="longs crowded: funding percentile >= 0.90 and basis percentile >= 0.80, close >= 2 ATR above the signal "
              "EMA50, the bar closes below the previous bar's low -> short (mirror for crowded shorts)",
        stop="beyond the extreme of the last 6 bars + 0.25 ATR, 1%-6%", targets="3R, time stop 24 h / 72 h",
        sizing="AGGRESSIVE_V6", why_aggressive="squeezes are fast and large when they come")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any],
              mkt: dict[str, Any]) -> Setup | None:
        p = self.params
        fp, bp = pos.get("funding_pct_90d"), pos.get("basis_pct_30d")
        if fp is None or bp is None:
            return None
        if fp >= p.funding_extreme and bp >= p.basis_extreme:
            crowd, side = "long", "short"
        elif fp <= 1.0 - p.funding_extreme and bp <= 1.0 - p.basis_extreme:
            crowd, side = "short", "long"
        else:
            return None
        ema50 = ctx.ind(c.symbol, self.signal_tf, "ema", n=50)
        atr = self.atr(ctx, c.symbol, self.signal_tf)
        if not ema50 or ema50[-1] is None or not atr:
            return None
        sign = 1.0 if crowd == "long" else -1.0
        prev = cs[-2]
        cur = sign * (c.close - ema50[-1]) / atr
        before = sign * (prev.close - ema50[-2]) / atr if len(ema50) > 1 and ema50[-2] is not None else cur
        stretch = max(cur, before)                  # the reversal bar itself has already come back toward the mean
        if stretch < p.stretch_atr:
            return None
        recent = list(cs)[-int(p.extreme_bars):]
        if side == "short":
            if not c.close < prev.low:
                return None
            extreme = max(x.high for x in recent)
            cl = 1.0 - close_location(c)
        else:
            if not c.close > prev.high:
                return None
            extreme = min(x.low for x in recent)
            cl = close_location(c)
        f_ext = fp if crowd == "long" else 1.0 - fp
        b_ext = bp if crowd == "long" else 1.0 - bp
        return Setup(side, extreme, {"funding": scale(f_ext, p.funding_extreme, 1.0),
                                     "basis": scale(b_ext, p.basis_extreme, 1.0),
                                     "stretch": scale(stretch, 0.0, 2.0 * p.stretch_atr), "close": scale(cl, 0.5, 1.0)},
                     f"crowded {crowd}s: reversal bar")
