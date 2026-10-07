"""V4.2 - Volatility expansion after compression.

Hypothesis: a structure range that has compressed to its tightest 20% of the last 100 bars stores
energy; the first expansion bar out of it, in the direction the 1h trend allows, starts a move that is
large relative to the tight stop the box provides.

Evidence (DEVELOPMENT only): V3's S32 volatility expansion was gross-negative on every timeframe
(-2.4 bps of turnover on 15m) -- it traded expansions in both directions with no trend filter and no
compression requirement. V4 trades only a genuine squeeze and only with the 1h trend.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v4.base import Setup, V4Strategy, close_location, scale


@dataclass
class Params:
    width_rank_max: float = P(0.20, min=0.05, max=0.5, step=0.05, label="compression: BB width rank <=")
    box_bars: int = P(12, min=4, max=40, step=1, label="compression box (bars)")
    expand_atr: float = P(1.2, min=0.5, max=3.0, step=0.1, label="breakout bar range (ATR)")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (trigger bars)")


class SqueezeExpansionV4(V4Strategy):
    id = "V4.2"
    name = "Squeeze Expansion v4"
    family = "SQUEEZE_EXPANSION"
    hypothesis = "the first expansion out of a genuinely compressed range, with the 1h trend, runs"
    thesis = "compression (BB width in its lowest 20%), expansion bar out of the box with the 1h trend"
    evidence = "V3 S32 traded expansions both ways without compression and lost gross; V4 adds both filters"
    expected_frequency = "~0.2-0.5 setups per coin-day on 15m"
    Params = Params
    doc = StrategyDoc(
        idea="Trade the first 1.2 ATR bar that closes out of a tight structure box, with the 1h trend.",
        timeframe="3m / 5m / 15m / 30m trigger; 15m (30m) structure; 1h trend; 1m execution",
        symbols="one coin per bot",
        entry="BB(20,2) width rank <= 0.20 on the bar before; the structure bar closes beyond the 12-bar box with "
              "range >= 1.2 ATR; 3m / 5m: a resuming trigger bar",
        stop="the breakout bar's opposite extreme + 0.25 ATR, 0.6%-2.5%",
        targets="50% at 1.5R, break-even at 1.5R, runner trailed 2 x ATR(structure), 180 / 240 min max",
        sizing="AGGRESSIVE_V4 behind the expected-edge gate; Jev V4 on the +JEV twin",
        why_aggressive="tight box = tight stop = large position per unit of risk on the expansion")

    def setup(self, ctx: MarketContext, symbol: str, cs: Sequence[Candle], atr: float, trend: Any) -> Setup | None:
        p, tf = self.params, self.structure_tf
        if not trend or trend["direction"] == "flat":
            return None
        side = "long" if trend["direction"] == "up" else "short"
        hist = bb_widths(list(cs)[-121:-1])                     # the 100 bars ending with the one before
        if len(hist) < 100:
            return None
        before = sum(1 for w in hist if w <= hist[-1]) / len(hist)   # its width's rank, 0..1
        if before > p.width_rank_max:
            return None
        c = cs[-1]
        hi, lo = self.box(cs, int(p.box_bars), skip=1)
        rng = c.high - c.low
        if rng < p.expand_atr * atr:
            return None
        if side == "long" and c.close > hi:
            stop_ref = c.low
        elif side == "short" and c.close < lo:
            stop_ref = c.high
        else:
            return None
        cl = close_location(c) if side == "long" else 1.0 - close_location(c)
        return Setup(side, c.close_time, stop_ref, {"squeeze": scale(p.width_rank_max - before, 0.0, p.width_rank_max),
                                                    "expansion": scale(rng / atr, p.expand_atr, 3.0),
                                                    "close": scale(cl, 0.5, 1.0)}, "squeeze broke")


def bb_widths(bars: Sequence[Candle], n: int = 20, k: float = 2.0) -> list[float]:
    """Bollinger band width (upper - lower) / basis for every bar with n bars of history -- only over the
    bars given (the full 600-bar indicator series is recomputed on every call, this is ~100 x cheaper)."""
    closes = [x.close for x in bars]
    out = []
    for i in range(n, len(closes) + 1):
        w = closes[i - n:i]
        mean = sum(w) / n
        sd = (sum((x - mean) ** 2 for x in w) / n) ** 0.5
        if mean > 0:
            out.append(2.0 * k * sd / mean)
    return out
