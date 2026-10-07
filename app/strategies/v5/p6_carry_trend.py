"""V5.6 - Carry-aware trend (the CONTINUATION reading of funding).

Hypothesis: a trend premium exists at multi-day horizons, net of carry: a Donchian break in the direction both
context trends agree on continues, provided the funding the position would pay over its planned hold does not eat a
material part of the move it seeks (<= 20% of it); trades that RECEIVE funding are allowed freely.
Expected hold 24-72 h; ~4-12 trades/month hourly, ~2-6 swing; seeks 4-10%.
Fails in whipsaw ranges.
New vs V1-V4: no earlier family held for days, and none treated funding as a cost of the idea.
V5.2 tests the opposite (reversal) reading of extreme funding.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v5.base import Setup, V5Strategy, scale


@dataclass
class Params:
    donchian_days: float = P(2.0, min=0.5, max=20.0, step=0.5, label="Donchian window (days; swing x2.5)")
    max_funding_share: float = P(0.20, min=0.0, max=1.0, step=0.05, label="expected funding cost <= share of the move")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (signal bars)")


class CarryTrendV5(V5Strategy):
    id = "V5.6"
    name = "Carry-aware Trend v5"
    family = "CARRY_AWARE_TREND"
    hypothesis = "a multi-day trend premium exists net of carry; skip breaks whose funding would eat the move"
    thesis = "Donchian break with both context trends, expected funding over the hold <= 20% of the move sought"
    fails_when = "whipsaw ranges"
    expected_hold = "24-72 h"
    expected_frequency = "hourly ~4-12 / month, swing ~2-6 / month"
    why_new = "V1-V4 never held for days and never costed funding into the idea"
    Params = Params
    doc = StrategyDoc(
        idea="Trade a multi-day Donchian break with the context trends, unless the funding over the hold eats the move.",
        timeframe="1h or 4h signal; 1m execution", symbols="one coin per bot",
        entry="both context trends agree; the signal bar closes beyond the prior 2-day (swing 5-day) extreme; expected "
              "funding over the planned hold <= 20% of the expected move (funding received always allowed)",
        stop="beyond the extreme of the last quarter of the window + 0.25 ATR, 0.8%-8%",
        targets="time stop (baseline 24 h / 72 h); DEVELOPMENT-selected exit after Stage 2",
        sizing="AGGRESSIVE_V5 TAKE 1%", why_aggressive="rides multi-day trends, the moves that dwarf costs")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any]) -> Setup | None:
        p = self.params
        t1, t2 = self.ctx_trend(ctx, c.symbol, self.ctx_fast), self.ctx_trend(ctx, c.symbol, self.ctx_slow)
        if t1 is None or t1 != t2 or t1 == "flat":
            return None
        side = "long" if t1 == "up" else "short"
        days = p.donchian_days * (1.0 if self.horizon == "HOURLY" else 2.5)
        n = max(6, min(len(cs) - 2, int(round(days * self.day_bars))))
        prior = list(cs)[-n - 1:-1]
        hi, lo = max(x.high for x in prior), min(x.low for x in prior)
        q = list(cs)[-max(2, n // 4) - 1:]
        if side == "long" and c.close > hi:
            stop_ref = min(x.low for x in q)
        elif side == "short" and c.close < lo:
            stop_ref = max(x.high for x in q)
        else:
            return None
        atr1 = self.atr(ctx, c.symbol, self.ctx_fast)
        move = min(0.10, 2.0 * atr1 / c.close) if atr1 else None
        cost = self.feed.expected_funding(c.close_time, side, self.time_stop_h) if self.feed is not None else None
        if move is None or cost is None:
            return None
        if cost > 0 and cost > p.max_funding_share * move:
            return None
        return Setup(side, stop_ref, {"carry": scale(-cost, -p.max_funding_share * move, move),
                                      "break": scale(abs(c.close - (hi if side == 'long' else lo)) / c.close, 0.0, 0.02)},
                     "Donchian trend break, carry acceptable", expected_move_pct=move)
