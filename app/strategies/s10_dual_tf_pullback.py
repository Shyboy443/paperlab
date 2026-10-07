"""S10 Dual-TF Momentum Pullback - "1m rejection candles in the 15m trend"

Idea:       Take 1m pullbacks to the EMA9 in the direction of the 15m EMA50 trend, entering on a rejection
            candle (pin bar or engulfing) so every trade is a high-frequency continuation of the higher trend.
Timeframe:  1m entries, 15m bias.
Symbols:    all configured symbols.
Entry:      15m close > EMA50 -> longs only: a 1m bar with low <= EMA9 and close > EMA9 that is a pin (lower wick
            >= 1.5 x body, close in the upper half of the range) or a bullish engulfing bar (bull bar, open <=
            previous close, close >= previous open, body > previous body). Short mirror below the 15m EMA50.
Stop:       beyond the pullback wick: bar low - 0.1 x ATR(14) for longs (bar high + 0.1 x ATR for shorts).
Targets:    TP1 at 2.0R closes 40%; the remaining 60% rides a 1.2 x ATR(1m, 14) runner armed after TP1, with
            the stop at break-even from 0.8R.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: 1m entries in the 15m trend, a 1.5 pin ratio accepts far more rejection candles, profit is
            banked at 2R and the rest runs on an ATR trail instead of a single all-or-nothing 3R exit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc

HTF_MIN_BARS = 55  # closed 15m bars required before the bias is trusted


@dataclass
class Params:
    htf_ema: int = P(50, min=10, max=200, step=1, label="15m bias EMA")
    ema_pullback: int = P(9, min=3, max=50, step=1, label="1m pullback EMA")
    pin_wick_ratio: float = P(1.5, min=1.0, max=5.0, step=0.1, label="pin wick / body",
                              help="rejection wick must be at least this many bodies long")
    stop_buffer_atr: float = P(0.1, min=0.0, max=1.0, step=0.05, label="stop buffer (ATR)",
                               help="stop distance beyond the pullback wick")
    tp1_r: float = P(2.0, min=1.0, max=6.0, step=0.1, label="TP1 (R)")
    tp1_fraction: float = P(0.40, min=0.1, max=1.0, step=0.05, label="TP1 fraction",
                            help="fraction closed at TP1; the remainder rides the ATR runner")
    trail_atr_mult: float = P(1.2, min=0.5, max=5.0, step=0.1, label="runner trail ATR mult",
                              help="runner trails this x ATR behind the best price after TP1")
    be_at_r: float = P(0.8, min=0.2, max=5.0, step=0.1, label="break-even at (R)",
                       help="move the stop to break-even once the trade is this many R in profit")
    tp_r: float = P(3.0, min=1.0, max=8.0, step=0.1, label="legacy TP (R)",
                    help="superseded by tp1_r; kept so saved slider values survive")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")


class DualTfMomentumPullback(Strategy):
    id = "S10"
    name = "Dual-TF Momentum Pullback"
    Params = Params
    doc = StrategyDoc(
        idea="1m pullback to EMA9 with a rejection candle, only in the 15m EMA50 trend direction.",
        timeframe="1m entries, 15m bias",
        symbols="all configured",
        entry="15m close > EMA50: 1m bar with low <= EMA9, close > EMA9 and a 1.5-ratio pin or bullish "
              "engulfing -> long; mirror below the 15m EMA50 -> short",
        stop="pullback wick -/+ 0.1 x ATR(14)",
        targets="TP1 2.0R closes 40%, runner trails 1.2 x ATR(14) after TP1, break-even at 0.8R",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="1m entries in the 15m trend, looser 1.5 pin ratio, early partial and a real runner",
    )
    timeframes = ("1m", "15m")
    contributes_votes = True
    warmup_bars = 60
    # TP1 is the only hard target (the remainder trails), so Signal.rr() reports the TP1 multiple: the floor has
    # to sit at or below tp1_r or every signal would be rejected as rr_below_min.
    min_rr = 2.0

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._bias: dict[str, dict[str, Any]] = {}
        self._last: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, int] = {"pin": 0, "engulfing": 0}

    # -- candle patterns ------------------------------------------------------------------
    def _rejection(self, c: Candle, prev: Candle, side: str) -> str | None:
        ratio = self.params.pin_wick_ratio
        if c.range <= 0:
            return None
        mid = c.low + 0.5 * c.range
        if side == "long":
            if c.lower_wick >= ratio * c.body and c.close >= mid:
                return "pin"
            if c.is_bull and c.open <= prev.close and c.close >= prev.open and c.body > prev.body:
                return "engulfing"
            return None
        if c.upper_wick >= ratio * c.body and c.close <= mid:
            return "pin"
        if c.is_bear and c.open >= prev.close and c.close <= prev.open and c.body > prev.body:
            return "engulfing"
        return None

    def _htf_bias(self, symbol: str, ctx: MarketContext) -> str | None:
        p = self.params
        htf = ctx.candles(symbol, "15m")
        need = max(HTF_MIN_BARS, p.htf_ema + 5)
        if len(htf) < need:
            self._bias[symbol] = {"bias": None, "bars": len(htf), "need": need}
            return None
        ema = ctx.ind_last(symbol, "15m", "ema", n=p.htf_ema)
        close = htf[-1].close
        bias = None if ema is None else "long" if close > ema else "short" if close < ema else None
        self._bias[symbol] = {"bias": bias, "close": close, "ema": ema, "ts": htf[-1].close_time}
        return bias

    # -- hook -------------------------------------------------------------------------------
    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "1m":
            return []
        p = self.params
        bias = self._htf_bias(c.symbol, ctx)
        if bias is None:
            return []
        candles = ctx.candles(c.symbol, "1m")
        if len(candles) < max(p.ema_pullback, p.atr_period) + 3:
            return []
        ema = ctx.ind_last(c.symbol, "1m", "ema", n=p.ema_pullback)
        atr = ctx.ind_last(c.symbol, "1m", "atr", n=p.atr_period)
        if ema is None or atr is None or atr <= 0:
            return []
        prev = candles[-2]
        pulled_back = (c.low <= ema < c.close) if bias == "long" else (c.high >= ema > c.close)
        pattern = self._rejection(c, prev, bias) if pulled_back else None
        self._last[c.symbol] = {"ts": c.close_time, "bias": bias, "ema": ema, "pulled_back": pulled_back,
                                "pattern": pattern}
        if pattern is None:
            return []
        price = ctx.last_price(c.symbol) or c.close
        stop = c.low - p.stop_buffer_atr * atr if bias == "long" else c.high + p.stop_buffer_atr * atr
        self._counters[pattern] += 1
        return [self.make_entry(
            symbol=c.symbol, side=bias, ts=c.close_time, tf="1m", price=price, stop=stop,
            tps_r=[(p.tp1_r, p.tp1_fraction)], be_at_r=p.be_at_r,
            trail=TrailSpec("atr", "1m", p.trail_atr_mult, p.atr_period, activate_after_tp1=True),
            valid_bars=2,
            reason=f"15m trend {bias}, 1m EMA{p.ema_pullback} pullback {pattern}",
            meta={"pattern": pattern, "ema_pullback": ema, "atr": atr, "htf": self._bias.get(c.symbol, {})})]

    def state(self) -> dict[str, Any]:
        return {"bias": self._bias, "last": self._last, "counters": dict(self._counters)}

    def reset(self) -> None:
        self._bias.clear()
        self._last.clear()
        self._counters = {"pin": 0, "engulfing": 0}
