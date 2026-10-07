"""V4.7 - Volume / flow confirmed breakout.

Hypothesis: a structure breakout carried by abnormal volume AND aggressive taker flow in its direction
is real participation, not a stop run; with the 1h trend behind it, it continues.

Evidence (DEVELOPMENT only): V3's S35 flow momentum was the only family with a positive gross edge on
BOTH 15m (+1.2 bps of turnover) and 30m (+4.9 bps); its losses were costs, not direction. V4 keeps the
flow requirement, adds the 1h trend and a genuine breakout of the structure range.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v4.base import Setup, V4Strategy, close_location, scale


@dataclass
class Params:
    box_bars: int = P(20, min=5, max=100, step=1, label="breakout of the (bars) range")
    min_volume_ratio: float = P(2.0, min=1.0, max=5.0, step=0.1, label="volume vs its 20-bar mean >=")
    min_taker_share: float = P(0.58, min=0.5, max=0.8, step=0.01, label="taker share in the direction >=")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (trigger bars)")


class FlowBreakoutV4(V4Strategy):
    id = "V4.7"
    name = "Flow Confirmed Breakout v4"
    family = "FLOW_BREAKOUT"
    hypothesis = "a breakout on abnormal volume with taker flow in its direction is real participation and continues"
    thesis = "20-bar range breakout, volume >= 2x mean, taker share >= 58% in the direction, with the 1h trend"
    evidence = "V3 S35 flow momentum was the only family gross-positive on 15m AND 30m in DEV"
    expected_frequency = "~0.2-0.5 setups per coin-day on 15m"
    Params = Params
    doc = StrategyDoc(
        idea="Buy a 15m range breakout made on 2x volume with >= 58% taker buying, in a 1h uptrend (mirror for shorts).",
        timeframe="3m / 5m / 15m / 30m trigger; 15m (30m) structure; 1h trend; 1m execution",
        symbols="one coin per bot",
        entry="the structure bar closes beyond the prior 20-bar range, volume >= 2x its 20-bar mean, taker-buy share "
              ">= 0.58 (<= 0.42 for shorts), close in the outer 40%; 3m / 5m: a resuming trigger bar",
        stop="the breakout bar's opposite extreme + 0.25 ATR, 0.6%-2.5%",
        targets="50% at 1.5R, break-even at 1.5R, runner trailed 2 x ATR(structure), 180 / 240 min max",
        sizing="AGGRESSIVE_V4 behind the expected-edge gate; Jev V4 on the +JEV twin",
        why_aggressive="participation-confirmed breakouts are the ones that run")

    def setup(self, ctx: MarketContext, symbol: str, cs: Sequence[Candle], atr: float, trend: Any) -> Setup | None:
        p = self.params
        if not trend or trend["direction"] == "flat":
            return None
        up = trend["direction"] == "up"
        bars = list(cs)
        n = int(p.box_bars)
        if len(bars) < n + 2:
            return None
        c = bars[-1]
        hi, lo = self.box(bars, n, skip=1)
        vr = self.volume_ratio(bars) or 0.0
        share = self.taker_share(c)
        if share is None or vr < p.min_volume_ratio:
            return None
        flow = share if up else 1.0 - share
        cl = close_location(c) if up else 1.0 - close_location(c)
        brk = c.close > hi if up else c.close < lo
        if not (brk and flow >= p.min_taker_share and cl >= 0.6):
            return None
        return Setup("long" if up else "short", c.close_time, c.low if up else c.high,
                     {"volume": scale(vr, p.min_volume_ratio, 5.0), "flow": scale(flow, p.min_taker_share, 0.8),
                      "close": scale(cl, 0.6, 1.0)}, "flow breakout")
