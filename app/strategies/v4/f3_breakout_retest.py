"""V4.3 - Breakout and retest.

Hypothesis: a level that price broke, came back to and HELD has flipped from resistance to support
(or the reverse); entering on the retest puts the stop just beyond the level -- a better R than chasing
the breakout bar -- and the retest filters out the breakouts that were only a stop run.

Evidence (DEVELOPMENT only): V3's momentum breakout (S31), which entered on the breakout itself, lost
gross on every timeframe (-5 to -8 bps of turnover). The retest requirement is the V4 answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v4.base import Setup, V4Strategy, close_location, scale


@dataclass
class Params:
    level_bars: int = P(24, min=8, max=100, step=1, label="level: extreme of (bars)")
    window: int = P(6, min=2, max=20, step=1, label="breakout -> retest window (bars)")
    retest_atr: float = P(0.3, min=0.0, max=1.0, step=0.05, label="retest tolerance (ATR)")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (trigger bars)")


class BreakoutRetestV4(V4Strategy):
    id = "V4.3"
    name = "Breakout Retest v4"
    family = "BREAKOUT_RETEST"
    hypothesis = "a broken level that is retested and holds has flipped; the retest gives a stop right beyond it"
    thesis = "structure level broken by a close, retested within 6 bars, held, resumed with the 1h trend"
    evidence = "V3 S31 entered breakouts directly and lost gross on every timeframe; V4 waits for the retest"
    expected_frequency = "~0.2-0.6 setups per coin-day on 15m"
    Params = Params
    doc = StrategyDoc(
        idea="After a close through the 24-bar high, buy the retest of that level once it holds (mirror for shorts).",
        timeframe="3m / 5m / 15m / 30m trigger; 15m (30m) structure; 1h trend; 1m execution",
        symbols="one coin per bot",
        entry="a close beyond the prior 24-bar extreme within the last 6 bars; since then a bar within 0.3 ATR of "
              "the level and no close back through it by more than 0.3 ATR; the structure bar closes beyond the "
              "previous bar and the level; 3m / 5m: a resuming trigger bar",
        stop="the retest extreme + 0.25 ATR, 0.6%-2.5%",
        targets="50% at 1.5R, break-even at 1.5R, runner trailed 2 x ATR(structure), 180 / 240 min max",
        sizing="AGGRESSIVE_V4 behind the expected-edge gate; Jev V4 on the +JEV twin",
        why_aggressive="the level is the stop: a small, well-defined risk on a confirmed breakout")

    def setup(self, ctx: MarketContext, symbol: str, cs: Sequence[Candle], atr: float, trend: Any) -> Setup | None:
        p = self.params
        if not trend or trend["direction"] == "flat":
            return None
        side = "long" if trend["direction"] == "up" else "short"
        w, n = int(p.window), int(p.level_bars)
        bars = list(cs)
        if len(bars) < n + w + 2:
            return None
        c, prev = bars[-1], bars[-2]
        base = bars[-(n + w + 1):-(w + 1)]          # the level is set BEFORE the breakout window
        after = bars[-(w + 1):-1]
        up = side == "long"
        level = max(x.high for x in base) if up else min(x.low for x in base)
        tol = p.retest_atr * atr
        brk = next((i for i, x in enumerate(after) if (x.close > level if up else x.close < level)), None)
        if brk is None:
            return None
        since = after[brk + 1:] + [c]
        if up:
            retest = any(x.low <= level + tol for x in since)
            held = all(x.close >= level - tol for x in since)
            go = c.close > prev.high and c.close > level
            extreme = min(x.low for x in since)
        else:
            retest = any(x.high >= level - tol for x in since)
            held = all(x.close <= level + tol for x in since)
            go = c.close < prev.low and c.close < level
            extreme = max(x.high for x in since)
        if not (retest and held and go):
            return None
        cl = close_location(c) if up else 1.0 - close_location(c)
        return Setup(side, c.close_time, extreme, {"close": scale(cl, 0.5, 1.0),
                                                   "hold": scale(abs(c.close - level) / atr, 0.0, 1.0)}, "retest held")
