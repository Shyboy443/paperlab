"""V5.3 - Compression breakout with positioning.

Hypothesis: a multi-hour volatility compression on the context timeframe, resolved by a signal-bar close out of its
box on above-average volume WHILE open interest rises, is new money entering, not a stop run; it extends for hours.
Expected hold 8-48 h; ~3-10 trades/month hourly, ~1-4 swing; seeks 3-8%.
Fails in range regimes with false breaks.
New vs V1-V4: V3 S32 / V4.2 traded compressions on 1m-30m bars without positioning and lost gross; V5 decides on
1h / 4h, requires the compression on the CONTEXT timeframe and requires OI to grow with the break.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v5.base import Setup, V5Strategy, scale


@dataclass
class Params:
    width_rank_max: float = P(0.25, min=0.05, max=0.5, step=0.05, label="context BB width rank <=")
    box_bars: int = P(24, min=6, max=96, step=1, label="box (signal bars)")
    min_volume_ratio: float = P(1.5, min=1.0, max=5.0, step=0.1, label="relative volume >=")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (signal bars)")


class CompressionBreakoutV5(V5Strategy):
    id = "V5.3"
    name = "Compression Breakout OI v5"
    family = "COMPRESSION_BREAKOUT_OI"
    hypothesis = "a compression resolved by a volume breakout with rising OI is new money and extends"
    thesis = "context Bollinger width in its lowest quarter, signal close out of the box, volume >= 1.5x, OI rising"
    fails_when = "range regimes with false breaks"
    expected_hold = "8-48 h"
    expected_frequency = "hourly ~3-10 / month, swing ~1-4 / month"
    why_new = "V1-V4 squeezes were 1m-30m and ignored open interest"
    Params = Params
    doc = StrategyDoc(
        idea="Trade the break out of a context-timeframe compression when volume and open interest confirm it.",
        timeframe="1h or 4h signal; 1m execution", symbols="one coin per bot",
        entry="the context bar's Bollinger width ranks in the lowest 25% of its last 60; the signal bar closes beyond the "
              "prior 24-bar (swing 12) box with relative volume >= 1.5 and OI up over the last 4 h (swing 24 h); the "
              "slow context trend is not against the break",
        stop="the box midpoint + 0.25 ATR, 0.8%-8%", targets="time stop; DEVELOPMENT-selected exit after Stage 2",
        sizing="AGGRESSIVE_V5 TAKE 1%", why_aggressive="compressions resolve into the largest multi-hour moves")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any]) -> Setup | None:
        p = self.params
        c1 = list(ctx.candles(c.symbol, self.ctx_fast))
        rank = self.bb_width_rank(c1[:-1]) if len(c1) > 90 else None
        if rank is None or rank > p.width_rank_max:
            return None
        n = int(p.box_bars) if self.horizon == "HOURLY" else max(6, int(p.box_bars) // 2)
        box = list(cs)[-n - 1:-1]
        hi, lo = max(x.high for x in box), min(x.low for x in box)
        vr = self.volume_ratio(list(cs)) or 0.0
        oi = pos.get("oi_chg_4h") if self.horizon == "HOURLY" else pos.get("oi_chg_24h")
        if vr < p.min_volume_ratio or oi is None or oi <= 0:
            return None
        slow = self.ctx_trend(ctx, c.symbol, self.ctx_slow)
        mid = (hi + lo) / 2.0
        if c.close > hi and slow != "down":
            return Setup("long", mid, {"squeeze": scale(p.width_rank_max - rank, 0.0, p.width_rank_max),
                                       "volume": scale(vr, p.min_volume_ratio, 4.0), "oi": scale(oi, 0.0, 0.1)}, "compression broke up, OI rising")
        if c.close < lo and slow != "up":
            return Setup("short", mid, {"squeeze": scale(p.width_rank_max - rank, 0.0, p.width_rank_max),
                                        "volume": scale(vr, p.min_volume_ratio, 4.0), "oi": scale(oi, 0.0, 0.1)}, "compression broke down, OI rising")
        return None
