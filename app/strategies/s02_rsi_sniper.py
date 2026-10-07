"""S02 RSI Extreme Sniper - "fade the extreme the moment it lets go"

Idea:       Fade RSI(5) extremes on the 5-minute chart the instant RSI snaps back out of the extreme zone.
Timeframe:  5m.
Symbols:    all configured symbols.
Entry:      long when RSI(5) printed below 22 within the last 5 bars and now closes at or above 28 (the first
            reclaim after the extreme); short when RSI(5) printed above 78 within the last 5 bars and now closes
            at or below 72.
Stop:       last swing (lowest low of the last 6 bars for longs / highest high for shorts) -/+ 0.8 x ATR(14).
Targets:    a single TP at 4R closing 100%, no partial.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: RSI(5) with wider trigger bands fires far more often on thin tape, a tight 6-bar swing stop
            and a fat 4R target with no partial.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.indicators import last
from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    rsi_period: int = P(5, min=2, max=30, step=1, label="RSI period")
    oversold: float = P(22.0, min=1.0, max=45.0, step=0.5, label="oversold", help="RSI must print below this")
    reclaim: float = P(28.0, min=2.0, max=50.0, step=0.5, label="long reclaim",
                       help="long when RSI closes at or above this after an oversold print")
    overbought: float = P(78.0, min=55.0, max=99.0, step=0.5, label="overbought", help="RSI must print above this")
    lose: float = P(72.0, min=50.0, max=98.0, step=0.5, label="short lose",
                    help="short when RSI closes at or below this after an overbought print")
    lookback_bars: int = P(5, min=1, max=30, step=1, label="extreme lookback (bars)",
                           help="the extreme must have printed within this many prior bars")
    swing_bars: int = P(6, min=2, max=50, step=1, label="swing bars", help="lowest low / highest high window")
    stop_atr_mult: float = P(0.8, min=0.1, max=4.0, step=0.1, label="stop ATR mult",
                             help="stop distance beyond the swing")
    tp_r: float = P(4.0, min=1.0, max=8.0, step=0.1, label="TP (R)")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")


class RsiExtremeSniper(Strategy):
    id = "S02"
    name = "RSI Extreme Sniper"
    Params = Params
    doc = StrategyDoc(
        idea="Fade RSI(5) extremes on the 5m chart the instant RSI snaps back out of the extreme zone.",
        timeframe="5m",
        symbols="all configured",
        entry="RSI(5) < 22 within the last 5 bars and now closes >= 28 -> long; "
              "RSI(5) > 78 within the last 5 bars and now closes <= 72 -> short",
        stop="6-bar swing low/high -/+ 0.8 x ATR(14)",
        targets="single TP at 4R closing 100%",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="RSI(5) and wider bands fire far more often, tight 6-bar swing stop, fat 4R target",
    )
    timeframes = ("5m",)
    contributes_votes = False
    warmup_bars = 60
    min_rr = 2.5

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last_signal: dict[str, dict[str, Any]] = {}
        self._rsi: dict[str, float] = {}
        self._skips: dict[str, int] = {}

    @staticmethod
    def _first_reclaim(rsi: Sequence[float | None], extreme: float, level: float, lookback: int,
                       from_below: bool) -> bool:
        """True when a bar within `lookback` prior bars printed beyond `extreme` and every bar since stayed
        short of `level`, i.e. this bar is the FIRST reclaim of `level` after the extreme."""
        for k in range(1, lookback + 1):
            v = last(rsi, k)
            if v is None:
                return False
            if from_below:
                if v >= level:
                    return False  # already reclaimed earlier -> that bar was the signal
                if v < extreme:
                    return True
            else:
                if v <= level:
                    return False
                if v > extreme:
                    return True
        return False

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "5m")
        if len(candles) < max(p.rsi_period + p.lookback_bars, p.atr_period, p.swing_bars) + 3:
            return []
        rsi = ctx.ind(c.symbol, "5m", "rsi", n=p.rsi_period)
        atr = ctx.ind_last(c.symbol, "5m", "atr", n=p.atr_period)
        now = last(rsi)
        if now is None or atr is None or atr <= 0:
            return []
        self._rsi[c.symbol] = round(now, 2)
        side: str | None = None
        if now >= p.reclaim and self._first_reclaim(rsi, p.oversold, p.reclaim, p.lookback_bars, True):
            side = "long"
        elif now <= p.lose and self._first_reclaim(rsi, p.overbought, p.lose, p.lookback_bars, False):
            side = "short"
        if side is None:
            return []
        price = ctx.last_price(c.symbol) or c.close
        if side == "long":
            swing = ctx.ind_last(c.symbol, "5m", "lowest", n=p.swing_bars)
            stop = None if swing is None else swing - p.stop_atr_mult * atr
        else:
            swing = ctx.ind_last(c.symbol, "5m", "highest", n=p.swing_bars)
            stop = None if swing is None else swing + p.stop_atr_mult * atr
        if stop is None or (side == "long" and stop >= price) or (side == "short" and stop <= price):
            self._skips["stop_side"] = self._skips.get("stop_side", 0) + 1
            return []
        self._last_signal[c.symbol] = {"ts": c.close_time, "side": side, "rsi": round(now, 2), "swing": swing}
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="5m", price=price, stop=stop,
            tps_r=[(p.tp_r, 1.0)], valid_bars=2,
            reason=f"RSI{p.rsi_period} {now:.1f} reclaim {side}",
            meta={"rsi": now, "swing": swing, "atr": atr})]

    def state(self) -> dict[str, Any]:
        return {"last_signal": self._last_signal, "rsi": self._rsi, "skips": self._skips}

    def reset(self) -> None:
        self._last_signal.clear()
        self._rsi.clear()
        self._skips.clear()
