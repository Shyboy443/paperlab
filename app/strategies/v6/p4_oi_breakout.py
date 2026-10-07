"""V6.4 - Open-interest breakout.

Hypothesis: a volatility compression during which open interest BUILDS (positions accumulating while price goes
nowhere) resolves into a directional move; the breakout out of the compression range, when it is not against the
daily trend, is the side that the accumulated positioning will be forced to chase.
Expected hold 4-24 h hourly / 12-72 h swing; ~3-10 setups / month hourly, ~1-3 swing; seeks 3R.
Fails on false breakouts (the range holds) and when the OI build was hedging.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v6.base import Setup, V6Strategy, scale


@dataclass
class Params:
    max_width_rank: float = P(0.20, min=0.05, max=0.6, step=0.05, label="Bollinger width rank (60 bars) <=")
    range_bars: int = P(20, min=6, max=60, step=1, label="breakout range (signal bars)")
    oi_bars: int = P(12, min=3, max=48, step=1, label="OI build window (signal bars)")
    min_oi_build: float = P(0.02, min=0.0, max=0.2, step=0.005, label="OI rise over the window >=")
    cooldown_bars: int = P(4, min=0, max=50, step=1, label="cooldown (signal bars)")


class OIBreakoutV6(V6Strategy):
    id = "V6.4"
    name = "OI Breakout v6"
    family = "OI_BREAKOUT"
    hypothesis = "a compression during which open interest builds resolves in the breakout direction"
    thesis = "Bollinger width in its bottom 20%; OI up >= 2% over 12 bars; close beyond the 20-bar range; not against 1D"
    fails_when = "false breakouts; hedging open interest"
    expected_hold = "4-24 h hourly, 12-72 h swing"
    expected_frequency = "hourly ~3-10 / month, swing ~1-3 / month"
    source = "V5.3 COMPRESSION_BREAKOUT_OI idea (no V5 raw edge); rebuilt around the OI build, not the squeeze alone"
    Params = Params
    doc = StrategyDoc(
        idea="Trade the break of a tight range in which open interest accumulated.",
        timeframe="1h (hourly) or 4h (swing) signal; 1m execution", symbols="one coin per bot",
        entry="the previous bar's Bollinger width ranks <= 0.20 of the last 60; OI rose >= 2% over the last 12 bars; "
              "the signal bar closes beyond the prior 20-bar high (low); the 1D trend is not the opposite",
        stop="beyond the breakout bar's opposite extreme + 0.25 ATR, 1%-6%", targets="3R, time stop 24 h / 72 h",
        sizing="AGGRESSIVE_V6", why_aggressive="expansions after positioning build-ups are fast")

    def setup(self, ctx: MarketContext, c: Candle, cs: Sequence[Candle], pos: dict[str, Any],
              mkt: dict[str, Any]) -> Setup | None:
        p = self.params
        if self.feed is None:
            return None
        bars = list(cs)
        rank = self.bb_width_rank(bars[:-1])
        if rank is None or rank > p.max_width_rank:
            return None
        n = int(p.range_bars)
        rng = bars[-n - 1:-1]
        if len(rng) < n:
            return None
        hi, lo = max(x.high for x in rng), min(x.low for x in rng)
        if c.close > hi:
            side = "long"
        elif c.close < lo:
            side = "short"
        else:
            return None
        daily = self.ctx_trend(ctx, c.symbol, "1d")
        if (side == "long" and daily == "down") or (side == "short" and daily == "up"):
            return None
        t = int(c.close_time) + 1
        oi_now = self.oi_at(t, t)
        oi_then = self.oi_at(bars[-int(p.oi_bars) - 1].close_time + 1, t)
        if not oi_now or not oi_then:
            return None
        build = oi_now / oi_then - 1.0
        if build < p.min_oi_build:
            return None
        atr = self.atr(ctx, c.symbol, self.signal_tf)
        if not atr:
            return None
        beyond = (c.close - hi) / atr if side == "long" else (lo - c.close) / atr
        extreme = c.low if side == "long" else c.high
        return Setup(side, extreme, {"compression": scale(p.max_width_rank - rank, 0.0, p.max_width_rank),
                                     "oi_build": scale(build, p.min_oi_build, 0.10),
                                     "breakout": scale(beyond, 0.0, 1.0), "market": self.alignment(mkt, side)},
                     "OI-built compression broke")
