"""V4.4 - Momentum continuation (flag).

Hypothesis: a structure-timeframe impulse in the top 15% of its own recent momentum, in the direction
of the 1h trend, persists for hours (time-series momentum); a shallow 1-4 bar flag after it gives an
entry with a stop under the flag instead of under the whole impulse.

Evidence (DEVELOPMENT only): V3.1's S37 impulse continuation, which entered ON the impulse, was
gross-negative (-27 to -69 bps of turnover): it bought exhaustion. V4 requires the pause (a flag that
retraces under half of the impulse) and a close beyond it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v4.base import Setup, V4Strategy, scale


@dataclass
class Params:
    roc_bars: int = P(8, min=3, max=30, step=1, label="impulse length (bars)")
    roc_rank_min: float = P(0.85, min=0.5, max=0.99, step=0.01, label="impulse rank >= (of last 200)")
    flag_max: int = P(4, min=1, max=10, step=1, label="flag length <= (bars)")
    max_retrace: float = P(0.5, min=0.2, max=0.8, step=0.05, label="flag retrace <= (of the impulse)")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (trigger bars)")


class MomentumContinuationV4(V4Strategy):
    id = "V4.4"
    name = "Momentum Flag Continuation v4"
    family = "MOMENTUM_CONTINUATION"
    hypothesis = "a top-decile structure impulse with the 1h trend persists; the flag break times it"
    thesis = "impulse in the top 15% of recent momentum, 1-4 bar flag under 50% retrace, flag break"
    evidence = "V3.1 S37 entered on the impulse bar and lost gross; V4 waits for the flag"
    expected_frequency = "~0.3-0.8 setups per coin-day on 15m"
    Params = Params
    doc = StrategyDoc(
        idea="After a strong impulse with the 1h trend, buy the break of the first shallow flag (mirror for shorts).",
        timeframe="3m / 5m / 15m / 30m trigger; 15m (30m) structure; 1h trend; 1m execution",
        symbols="one coin per bot",
        entry="the 8-bar return that ended just before a 1-4 bar flag ranks >= 0.85 of the previous 200; the flag "
              "retraced <= 50% of the impulse; the structure bar closes beyond the flag; 3m / 5m: a resuming trigger bar",
        stop="the flag's opposite extreme + 0.25 ATR, 0.6%-2.5%",
        targets="50% at 1.5R, break-even at 1.5R, runner trailed 2 x ATR(structure), 180 / 240 min max",
        sizing="AGGRESSIVE_V4 behind the expected-edge gate; Jev V4 on the +JEV twin",
        why_aggressive="joins the strongest moves with a flag-sized stop")

    def setup(self, ctx: MarketContext, symbol: str, cs: Sequence[Candle], atr: float, trend: Any) -> Setup | None:
        p = self.params
        if not trend or trend["direction"] == "flat":
            return None
        up = trend["direction"] == "up"
        sgn = 1.0 if up else -1.0
        bars, k = list(cs), int(p.roc_bars)
        c = bars[-1]
        closes = [x.close for x in bars]
        for f in range(1, int(p.flag_max) + 1):
            e = len(bars) - 2 - f                          # the impulse's last bar; bars e+1 .. -2 are the flag
            if e - k - 200 < 0:
                return None
            imp = sgn * (closes[e] / closes[e - k] - 1.0)
            if imp <= 0:
                continue
            hist = [sgn * (closes[j] / closes[j - k] - 1.0) for j in range(e - 200, e)]
            rank = sum(1 for r in hist if r <= imp) / len(hist)
            if rank < p.roc_rank_min:
                continue
            flag = bars[e + 1:-1]
            seg = bars[e - k + 1:e + 1]
            start = closes[e - k]
            top = max(x.high for x in seg) if up else min(x.low for x in seg)
            size = abs(top - start)
            if size <= 0:
                continue
            if up:
                retr = (top - min(x.low for x in flag)) / size
                brk = c.close > max(x.high for x in flag)
                extreme = min(x.low for x in flag + [c])
            else:
                retr = (max(x.high for x in flag) - top) / size
                brk = c.close < min(x.low for x in flag)
                extreme = max(x.high for x in flag + [c])
            if 0.0 <= retr <= p.max_retrace and brk:
                return Setup("long" if up else "short", c.close_time, extreme,
                             {"impulse": scale(rank, p.roc_rank_min, 1.0), "shallow": scale(p.max_retrace - retr, 0.0, p.max_retrace)},
                             f"{f}-bar flag broke")
            return None                                   # the most recent qualifying impulse decides
        return None
