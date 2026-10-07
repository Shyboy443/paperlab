"""V6.2 - Momentum continuation carried by open interest.

Hypothesis: an unusually strong multi-hour move in the direction of the context trend that NEW POSITIONS carried
(open interest rose during it, i.e. it was not short covering or long liquidation) continues after a short, shallow
consolidation, when the consolidation breaks in the move's direction.
Expected hold 4-24 h hourly / 12-72 h swing; ~4-12 setups / month hourly, ~1-4 swing; seeks 3R.
Fails when the impulse was the last leg (exhaustion) or the open interest came from hedges.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v6.base import Setup, V6Strategy, scale


@dataclass
class Params:
    impulse_bars: int = P(12, min=3, max=48, step=1, label="impulse length (signal bars; swing: half)")
    rank_min: float = P(0.85, min=0.5, max=0.99, step=0.01, label="impulse rank among recent same-length moves >=")
    min_oi_rise: float = P(0.02, min=0.0, max=0.3, step=0.005, label="OI rise during the impulse >=")
    min_cons: int = P(2, min=1, max=12, step=1, label="consolidation >= (bars)")
    max_cons: int = P(6, min=2, max=24, step=1, label="consolidation <= (bars)")
    max_retrace: float = P(0.5, min=0.2, max=0.8, step=0.05, label="consolidation retrace <= (of the impulse)")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (signal bars)")


class MomentumOIV6(V6Strategy):
    id = "V6.2"
    name = "Momentum Continuation OI v6"
    family = "MOMENTUM_OI"
    hypothesis = "a top-15% impulse carried by rising open interest continues after a shallow consolidation"
    thesis = "context trend; top-15% multi-hour impulse; OI up >= 2% during it; 2-6 bar consolidation <= 50%; break"
    fails_when = "exhaustion; hedging open interest"
    expected_hold = "4-24 h hourly, 12-72 h swing"
    expected_frequency = "hourly ~4-12 / month, swing ~1-4 / month"
    source = "V5.8 MOMENTUM_CONTINUATION_OI hypothesis (DEVELOPMENT and pseudo-holdout gross > 0, not significant); fresh parameters"
    Params = Params
    doc = StrategyDoc(
        idea="After a positioning-driven impulse and a tight consolidation, trade the consolidation break.",
        timeframe="1h (hourly) or 4h (swing) signal; 1m execution", symbols="one coin per bot",
        entry="the impulse return ending 2-6 bars ago ranks >= 0.85 of recent same-length returns in the context "
              "trend's direction; OI rose >= 2% across it; the consolidation retraced <= 50%; the bar closes beyond it",
        stop="beyond the consolidation + 0.25 ATR, 1%-6%", targets="3R, time stop 24 h / 72 h",
        sizing="AGGRESSIVE_V6", why_aggressive="joins the strongest positioning-driven moves")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any],
              mkt: dict[str, Any]) -> Setup | None:
        p = self.params
        trend = self.ctx_trend(ctx, c.symbol, self.ctx_fast)
        if trend not in ("up", "down") or self.feed is None:
            return None
        up = trend == "up"
        sgn = 1.0 if up else -1.0
        bars = list(cs)
        k = int(p.impulse_bars) if self.horizon == "HOURLY" else max(3, int(p.impulse_bars) // 2)
        closes = [x.close for x in bars]
        hist = [sgn * (closes[j] / closes[j - k] - 1.0) for j in range(k, len(closes) - 1) if closes[j - k] > 0]
        if len(hist) < 60:
            return None
        for cons in range(int(p.min_cons), int(p.max_cons) + 1):
            e = len(bars) - 2 - cons                 # the impulse's last bar; bars e+1 .. -2 consolidate
            if e - k < 0:
                return None
            imp = sgn * (closes[e] / closes[e - k] - 1.0)
            if imp <= 0:
                continue
            rank = sum(1 for r in hist if r <= imp) / len(hist)
            if rank < p.rank_min:
                continue
            t = int(c.close_time) + 1
            oi0 = self.oi_at(bars[e - k].close_time + 1, t)
            oi1 = self.oi_at(bars[e].close_time + 1, t)
            if not oi0 or not oi1 or oi1 / oi0 - 1.0 < p.min_oi_rise:
                return None
            flag = bars[e + 1:-1]
            seg = bars[e - k + 1:e + 1]
            start = closes[e - k]
            top = max(x.high for x in seg) if up else min(x.low for x in seg)
            size = abs(top - start)
            if size <= 0:
                return None
            if up:
                retr = (top - min(x.low for x in flag)) / size
                brk = c.close > max(x.high for x in flag)
                extreme = min(x.low for x in flag + [c])
            else:
                retr = (max(x.high for x in flag) - top) / size
                brk = c.close < min(x.low for x in flag)
                extreme = max(x.high for x in flag + [c])
            if 0.0 <= retr <= p.max_retrace and brk:
                side = "long" if up else "short"
                return Setup(side, extreme,
                             {"impulse": scale(rank, p.rank_min, 1.0), "oi": scale(oi1 / oi0 - 1.0, p.min_oi_rise, 0.10),
                              "shallow": scale(p.max_retrace - retr, 0.0, p.max_retrace),
                              "market": self.alignment(mkt, side)}, f"{cons}-bar consolidation broke")
            return None
        return None
