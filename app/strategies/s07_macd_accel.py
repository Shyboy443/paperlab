"""S07 MACD Acceleration - "zero-line cross with the histogram already accelerating"

Idea:       Trade the fast MACD histogram zero-line cross on the 5-minute chart, but only when the histogram is
            already accelerating into the cross and price is on the right side of EMA21.
Timeframe:  5m.
Symbols:    all configured symbols.
Entry:      MACD(6, 13, 4) histogram crosses above 0 on this bar with the histogram accelerating over the last
            `accel_bars` (1 by default: hist[-1] > hist[-2]) and close > EMA21 -> long; crosses below 0 with the
            histogram falling over the same window and close < EMA21 -> short.
Stop:       1 x ATR(14) from the entry price.
Targets:    single TP at 3.5R closing 100%.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: 6/13/4 MACD reacts a bar or two earlier than 8/21/5, only one bar of acceleration is
            required instead of two, and the target is a fat 3.5R with no partial.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import last
from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    fast: int = P(6, min=2, max=50, step=1, label="MACD fast")
    slow: int = P(13, min=5, max=120, step=1, label="MACD slow")
    signal: int = P(4, min=2, max=50, step=1, label="MACD signal")
    accel_bars: int = P(1, min=1, max=5, step=1, label="acceleration bars",
                        help="the histogram must have risen (longs) over this many prior bars before the cross")
    ema_filter: int = P(21, min=5, max=200, step=1, label="EMA filter", help="close must be on the trade's side")
    stop_atr_mult: float = P(1.0, min=0.3, max=4.0, step=0.1, label="stop ATR mult")
    tp_r: float = P(3.5, min=1.0, max=8.0, step=0.1, label="TP (R)")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")


class MacdAcceleration(Strategy):
    id = "S07"
    name = "MACD Acceleration"
    Params = Params
    doc = StrategyDoc(
        idea="Fast MACD histogram zero-line cross on 5m, only when the histogram is already accelerating.",
        timeframe="5m",
        symbols="all configured",
        entry="MACD(6,13,4) hist crosses above 0 with hist rising over 1 bar and close > EMA21 -> long; "
              "crosses below 0 with hist falling over 1 bar and close < EMA21 -> short",
        stop="1 x ATR(14) from the entry",
        targets="single TP at 3.5R closing 100%",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="6/13/4 MACD, only one bar of acceleration required, fat 3.5R target",
    )
    timeframes = ("5m",)
    contributes_votes = False
    warmup_bars = 60
    min_rr = 2.5

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last_signal: dict[str, dict[str, Any]] = {}
        self._hist: dict[str, float] = {}
        self._skips: dict[str, int] = {}

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "5m")
        if len(candles) < max(p.slow + p.signal, p.ema_filter, p.atr_period) + int(p.accel_bars) + 2:
            return []
        _macd, _sig, hist = ctx.ind(c.symbol, "5m", "macd", fast=p.fast, slow=p.slow, signal=p.signal)
        ema = ctx.ind_last(c.symbol, "5m", "ema", n=p.ema_filter)
        atr = ctx.ind_last(c.symbol, "5m", "atr", n=p.atr_period)
        n_accel = max(1, int(p.accel_bars))
        window = [last(hist, k) for k in range(n_accel + 1)]  # newest first: h0, h1, ... h_naccel
        if any(v is None for v in window) or ema is None or atr is None or atr <= 0:
            return []
        h0, h1 = window[0], window[1]
        self._hist[c.symbol] = h0
        rising = all(window[i] > window[i + 1] for i in range(n_accel))
        falling = all(window[i] < window[i + 1] for i in range(n_accel))
        if h1 <= 0 < h0 and rising and c.close > ema:
            side = "long"
        elif h1 >= 0 > h0 and falling and c.close < ema:
            side = "short"
        else:
            if (h1 <= 0 < h0) or (h1 >= 0 > h0):
                self._skips["cross_unconfirmed"] = self._skips.get("cross_unconfirmed", 0) + 1
            return []
        price = ctx.last_price(c.symbol) or c.close
        stop = price - p.stop_atr_mult * atr if side == "long" else price + p.stop_atr_mult * atr
        if stop <= 0:
            return []
        self._last_signal[c.symbol] = {"ts": c.close_time, "side": side, "hist": h0, "ema": ema}
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="5m", price=price, stop=stop,
            tps_r=[(p.tp_r, 1.0)], valid_bars=2,
            reason=f"MACD({p.fast},{p.slow},{p.signal}) hist zero-cross {side}, accelerating",
            meta={"hist": list(reversed(window)), "ema": ema, "atr": atr, "accel_bars": n_accel})]

    def state(self) -> dict[str, Any]:
        return {"last_signal": self._last_signal, "hist": self._hist, "skips": self._skips}

    def reset(self) -> None:
        self._last_signal.clear()
        self._hist.clear()
        self._skips.clear()
