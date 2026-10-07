"""V5.2 - Funding + OI crowding reversal.

Hypothesis: funding in the extreme decile of its last 90 days + open interest near its 30-day high + price failing
to extend (a close through the last three bars' opposite extreme) means one side is crowded and paying to stay in;
its unwind reverses price for hours to days.
Expected hold 8-48 h; ~2-8 trades/month hourly, ~1-4 swing; seeks 3-8%.
Fails in strong trends where crowding persists and funding stays extreme while price keeps going.
New vs V1-V4: no earlier family read funding or open interest; the V3 fades faded price extremes, not positioning.
The CONTINUATION reading of the same extremes is V5.6 (carry-aware trend): both hypotheses are tested.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v5.base import Setup, V5Strategy, scale


@dataclass
class Params:
    funding_pct: float = P(0.90, min=0.5, max=0.99, step=0.01, label="funding percentile (90d) >=")
    oi_pct: float = P(0.80, min=0.5, max=0.99, step=0.01, label="OI percentile (30d) >=")
    trigger_bars: int = P(3, min=1, max=10, step=1, label="close through the last N bars")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (signal bars)")


class CrowdingReversalV5(V5Strategy):
    id = "V5.2"
    name = "Funding OI Crowding Reversal v5"
    family = "FUNDING_OI_CROWDING_REVERSAL"
    hypothesis = "extreme funding + high OI + a failed extension = a crowded side that unwinds"
    thesis = "funding in its 90-day extreme decile, OI near its 30-day high, close through the last 3 bars against the crowd"
    fails_when = "strong trends where crowding persists"
    expected_hold = "8-48 h"
    expected_frequency = "hourly ~2-8 / month, swing ~1-4 / month"
    why_new = "V1-V4 never read funding or open interest"
    Params = Params
    doc = StrategyDoc(
        idea="Fade the side that is crowded (extreme funding, high OI) once price stops extending in its favour.",
        timeframe="1h or 4h signal; 1m execution", symbols="one coin per bot",
        entry="funding percentile >= 0.90 and positive (shorts) / <= 0.10 and negative (longs); OI percentile >= 0.80; "
              "the signal bar closes through the prior 3 bars' low (shorts) / high (longs)",
        stop="beyond the recent extreme + 0.25 ATR, 0.8%-8%", targets="time stop; DEVELOPMENT-selected exit after Stage 2",
        sizing="AGGRESSIVE_V5 TAKE 1%", why_aggressive="positioning extremes are rare and large")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any]) -> Setup | None:
        p = self.params
        fp, fr, oip = pos.get("funding_pct_90d"), pos.get("funding_rate"), pos.get("oi_pct_30d")
        if fp is None or fr is None or oip is None or (pos.get("funding_n_90d") or 0) < 30 or oip < p.oi_pct:
            return None
        k = int(p.trigger_bars)
        prior = list(cs)[-k - 1:-1]
        recent = list(cs)[-int(self.lookback):]
        if fp >= p.funding_pct and fr > 0 and c.close < min(x.low for x in prior):
            return Setup("short", max(x.high for x in recent), {"funding": scale(fp, p.funding_pct, 1.0), "oi": scale(oip, p.oi_pct, 1.0)},
                         "crowded longs unwinding")
        if fp <= 1.0 - p.funding_pct and fr < 0 and c.close > max(x.high for x in prior):
            return Setup("long", min(x.low for x in recent), {"funding": scale(1.0 - fp, p.funding_pct, 1.0), "oi": scale(oip, p.oi_pct, 1.0)},
                         "crowded shorts unwinding")
        return None
