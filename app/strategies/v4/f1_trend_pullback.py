"""V4.1 - Trend continuation after a pullback.

Hypothesis: inside a 1h trend, a pullback to the structure EMA that resumes carries the trend's drift
for hours; the pullback low is a natural, tight structural stop.

Evidence (DEVELOPMENT only, docs/V4_ROOT_CAUSE.md): pullback continuation was the one family with a
positive gross edge that survived costs -- V3.1 S33.1 on 15m, +25 bps of turnover gross over 181 trades --
and V3's S33 was gross-positive on 30m (+5.6 bps). V4 keeps the idea, requires the 1h trend, and lets a
3m / 5m trigger time the resumption instead of entering on the structure bar's close.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v4.base import Setup, V4Strategy, close_location, scale


@dataclass
class Params:
    ema_n: int = P(20, min=5, max=100, step=1, label="structure EMA")
    touch_atr: float = P(0.3, min=0.0, max=2.0, step=0.1, label="EMA touch tolerance (ATR)")
    rsi_reset: float = P(45.0, min=20.0, max=50.0, step=1.0, label="RSI reset (longs)")
    lookback: int = P(6, min=2, max=20, step=1, label="pullback lookback (bars)")
    cooldown_bars: int = P(3, min=0, max=50, step=1, label="cooldown (trigger bars)")


class TrendPullbackV4(V4Strategy):
    id = "V4.1"
    name = "Trend Pullback Continuation v4"
    family = "TREND_PULLBACK"
    hypothesis = "a pullback to value inside a 1h trend that resumes carries the trend's drift for hours"
    thesis = "1h trend, pullback to the structure EMA with an RSI reset, resumption"
    evidence = "the only DEV family x timeframe with positive gross after costs (S33.1 15m, +25 bps of turnover)"
    expected_frequency = "~0.5-1.5 setups per coin-day on 15m"
    Params = Params
    doc = StrategyDoc(
        idea="Buy the resumption after a pullback to the 15m / 30m EMA inside a 1h uptrend (mirror for shorts).",
        timeframe="3m / 5m / 15m / 30m trigger; 15m (30m for 30m bots) structure; 1h trend; 1m execution",
        symbols="one coin per bot",
        entry="1h trend; in the last 6 structure bars the low touched EMA20 (0.3 ATR) and RSI(14) reset <= 45; "
              "the structure bar closes above the EMA and the previous bar's high; 3m / 5m: a resuming trigger bar",
        stop="beyond the pullback extreme + 0.25 ATR, 0.6%-2.5%",
        targets="50% at 1.5R, break-even at 1.5R, runner trailed 2 x ATR(structure), 180 / 240 min max",
        sizing="AGGRESSIVE_V4 behind the expected-edge gate; Jev V4 on the +JEV twin",
        why_aggressive="participates in every trend pullback, one structural stop, lets the runner work")

    def setup(self, ctx: MarketContext, symbol: str, cs: Sequence[Candle], atr: float, trend: Any) -> Setup | None:
        p, tf = self.params, self.structure_tf
        if not trend or trend["direction"] == "flat":
            return None
        side = "long" if trend["direction"] == "up" else "short"
        ema_s = ctx.ind(symbol, tf, "ema", n=int(p.ema_n))
        rsi_s = ctx.ind(symbol, tf, "rsi", n=14)
        lb = int(p.lookback)
        c, prev = cs[-1], cs[-2]
        ema = ema_s[-1]
        recent, emas = list(cs)[-lb - 1:-1], ema_s[-lb - 1:-1]
        rsis = [r for r in rsi_s[-lb - 1:-1] if r is not None]
        if ema is None or not rsis or None in emas:
            return None
        if side == "long":
            touched = any(x.low <= e + p.touch_atr * atr for x, e in zip(recent, emas))
            reset = min(rsis) <= p.rsi_reset
            resumed = c.close > ema and c.close > prev.high
            extreme = min(x.low for x in recent + [c])
        else:
            touched = any(x.high >= e - p.touch_atr * atr for x, e in zip(recent, emas))
            reset = max(rsis) >= 100.0 - p.rsi_reset
            resumed = c.close < ema and c.close < prev.low
            extreme = max(x.high for x in recent + [c])
        if not (touched and reset and resumed):
            return None
        cl = close_location(c) if side == "long" else 1.0 - close_location(c)
        depth = (p.rsi_reset - min(rsis)) if side == "long" else (max(rsis) - (100.0 - p.rsi_reset))
        return Setup(side, c.close_time, extreme, {"close": scale(cl, 0.5, 1.0), "rsi_reset": scale(depth, 0.0, 15.0)},
                     "pullback resumed")
