"""V5.8 - Momentum continuation with open interest.

Hypothesis: a multi-hour impulse in the top decile of its own recent returns, CARRIED BY RISING OPEN INTEREST (new
positions, not covering), followed by a controlled consolidation (3-8 signal bars, less than half the impulse given
back), continues when the consolidation breaks in the impulse's direction.
Expected hold 8-48 h; ~3-10 trades/month hourly, ~1-4 swing; seeks 3-8%.
Fails on exhaustion (an impulse that was the last leg).
New vs V1-V4: V3.1 S37 / V4.4 were 15m-30m impulses without open interest and lost gross; V5.8 decides on 1h / 4h and
requires the impulse to be positioning-driven.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v5.base import Setup, V5Strategy, scale


@dataclass
class Params:
    impulse_bars: int = P(12, min=3, max=48, step=1, label="impulse length (signal bars; swing /2)")
    rank_min: float = P(0.90, min=0.5, max=0.99, step=0.01, label="impulse rank >= (recent returns)")
    min_oi_rise: float = P(0.03, min=0.0, max=0.3, step=0.01, label="OI rise during the impulse >=")
    min_cons: int = P(3, min=1, max=12, step=1, label="consolidation >= (bars)")
    max_cons: int = P(8, min=2, max=24, step=1, label="consolidation <= (bars)")
    max_retrace: float = P(0.5, min=0.2, max=0.8, step=0.05, label="consolidation retrace <= (of the impulse)")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (signal bars)")


class MomentumContinuationV5(V5Strategy):
    id = "V5.8"
    name = "Momentum Continuation OI v5"
    family = "MOMENTUM_CONTINUATION_OI"
    hypothesis = "a top-decile impulse carried by rising OI continues after a controlled consolidation"
    thesis = "top-decile multi-hour impulse, OI up during it, 3-8 bar consolidation under 50% retrace, break"
    fails_when = "exhaustion"
    expected_hold = "8-48 h"
    expected_frequency = "hourly ~3-10 / month, swing ~1-4 / month"
    why_new = "V1-V4 impulses were 15m-30m and ignored open interest"
    Params = Params
    doc = StrategyDoc(
        idea="After a positioning-driven impulse and a tight consolidation, trade the consolidation break.",
        timeframe="1h or 4h signal; 1m execution", symbols="one coin per bot",
        entry="the 12-bar (swing 6) return ending 3-8 bars ago ranks >= 0.90 of recent returns, with the context trend; "
              "OI rose >= 3% during it; the consolidation retraced <= 50%; the signal bar closes beyond it",
        stop="beyond the consolidation + 0.25 ATR, 0.8%-8%", targets="time stop; DEVELOPMENT-selected exit after Stage 2",
        sizing="AGGRESSIVE_V5 TAKE 1%", why_aggressive="joins the strongest positioning-driven moves")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any]) -> Setup | None:
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
        if len(hist) < 100:
            return None
        for cons in range(int(p.min_cons), int(p.max_cons) + 1):
            e = len(bars) - 2 - cons                    # the impulse's last bar; bars e+1 .. -2 consolidate
            if e - k < 0:
                return None
            imp = sgn * (closes[e] / closes[e - k] - 1.0)
            if imp <= 0:
                continue
            rank = sum(1 for r in hist if r <= imp) / len(hist)
            if rank < p.rank_min:
                continue
            oi0 = self.feed.oi.at_or_before(bars[e - k].close_time)
            oi1 = self.feed.oi.at_or_before(bars[e].close_time)
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
                return Setup("long" if up else "short", extreme,
                             {"impulse": scale(rank, p.rank_min, 1.0), "oi": scale(oi1 / oi0 - 1.0, p.min_oi_rise, 0.3),
                              "shallow": scale(p.max_retrace - retr, 0.0, p.max_retrace)}, f"{cons}-bar consolidation broke")
            return None
        return None
