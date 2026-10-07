"""V5.1 - Positioning trend.

Hypothesis: a trend on the context timeframes that NEW positioning is joining -- price and open interest rising
together over the last day (HOURLY) / three days (SWING) -- is carried by fresh capital and persists for hours to
days; a trend on falling OI is mostly short covering and fades.
Expected hold 8-48 h; ~20-40 trades/month hourly, ~5-10 swing; seeks 2-6%.
Fails in choppy regimes and when OI rises on hedged or basis positions rather than directional ones.
New vs V1-V4: no earlier family ever read open interest.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v5.base import Setup, V5Strategy, close_location, scale


@dataclass
class Params:
    min_oi_change: float = P(0.03, min=0.0, max=0.3, step=0.01, label="OI change over the window >=")
    min_price_change: float = P(0.01, min=0.0, max=0.2, step=0.005, label="price change with the trend >=")
    cooldown_bars: int = P(2, min=0, max=50, step=1, label="cooldown (signal bars)")


class PositioningTrendV5(V5Strategy):
    id = "V5.1"
    name = "Positioning Trend v5"
    family = "POSITIONING_TREND"
    hypothesis = "a context trend that new positioning joins (price and OI rising together) persists for hours to days"
    thesis = "4h/1D (1D/1W) trend + price and open interest rising together + a new N-bar extreme"
    fails_when = "choppy regimes; OI rising on hedged positions"
    expected_hold = "8-48 h"
    expected_frequency = "hourly ~20-40 / month, swing ~5-10 / month"
    why_new = "V1-V4 never read open interest"
    Params = Params
    doc = StrategyDoc(
        idea="Join a trend on a new N-bar extreme only when open interest grows with price (new positions, not covering).",
        timeframe="1h signal (4h + 1D context) or 4h signal (1D + 1W); 1m execution",
        symbols="one coin per bot",
        entry="both context trends agree; the signal bar closes at a new 12 (swing 6) bar extreme; over the last 24 h "
              "(swing 3 days) price moved >= 1% with the trend and open interest rose >= 3%",
        stop="beyond the N-bar extreme + 0.25 ATR, 0.8%-8%",
        targets="time stop (24 h hourly / 72 h swing baseline); a DEVELOPMENT-selected exit after Stage 2",
        sizing="AGGRESSIVE_V5 TAKE 1%", why_aggressive="participates in every positioning-confirmed trend leg")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any]) -> Setup | None:
        p = self.params
        t1, t2 = self.ctx_trend(ctx, c.symbol, self.ctx_fast), self.ctx_trend(ctx, c.symbol, self.ctx_slow)
        if t1 is None or t1 != t2 or t1 == "flat":
            return None
        side = "long" if t1 == "up" else "short"
        n = int(self.lookback)
        prior = list(cs)[-n - 1:-1]
        span = int(self.day_bars) * (1 if self.horizon == "HOURLY" else 3)
        oi = pos.get("oi_chg_24h") if self.horizon == "HOURLY" else pos.get("oi_chg_3d")
        move = self.ret(cs, span)
        if oi is None or move is None or oi < p.min_oi_change:
            return None
        sgn = 1.0 if side == "long" else -1.0
        if sgn * move < p.min_price_change:
            return None
        if side == "long" and c.close > max(x.high for x in prior):
            stop_ref = min(x.low for x in prior)
        elif side == "short" and c.close < min(x.low for x in prior):
            stop_ref = max(x.high for x in prior)
        else:
            return None
        cl = close_location(c) if side == "long" else 1.0 - close_location(c)
        return Setup(side, stop_ref, {"oi": scale(oi, p.min_oi_change, 0.2), "move": scale(sgn * move, p.min_price_change, 0.1),
                                      "close": scale(cl, 0.5, 1.0)}, "trend + OI joining, new extreme")
