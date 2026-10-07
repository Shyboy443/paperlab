"""S22 Keltner Band Reversion - fade a stretch outside the Keltner channel while the regime is a range.

Idea:       Keltner bands are EMA +/- ATR, so "outside the band" means the move is large relative to the
            symbol's OWN recent volatility rather than a fixed percentage. In a range that stretch tends
            to snap back to the middle band. This is distinct from S03 (Bollinger squeeze BREAKOUT) and
            from S15 (a one/two-bar liquidation flush): here the trigger is a sustained close outside a
            volatility envelope, and we need the range regime to hold.
Timeframe:  5m trigger, 15m regime gate.
Symbols:    all configured symbols.
Entry:      the latest closed 5m bar closes outside the Keltner band by at least `stretch_atr` ATRs AND
            RSI confirms the stretch (<= `rsi_low` for longs, >= `rsi_high` for shorts) AND the 15m ADX
            proxy says range: the 15m close sits inside the prior `range_bars_15m` bar high/low.
Stop:       `stop_atr` ATRs beyond the extreme of the trigger bar.
Targets:    the middle band (EMA) closes 70%, the rest trails; time exit after `max_hold_s`.
Sizing:     standard per-trade risk, 10x virtual leverage.
Why aggressive: it catches the snap-back at the point of maximum stretch instead of waiting for a
            confirmation bar, and the stop is a tight multiple of ATR just past the wick.

Interpretation note: "regime = range" is the same 15m high/low containment test S15 uses, so the two
strategies agree about when the market is trending and neither fades a genuine breakout.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    ema_n: int = P(20, min=5, max=100, step=1, label="channel EMA")
    atr_n: int = P(14, min=5, max=50, step=1, label="channel ATR")
    band_atr: float = P(2.0, min=0.5, max=5.0, step=0.1, label="band width (ATR)")
    stretch_atr: float = P(0.25, min=0.0, max=3.0, step=0.05, label="extra stretch beyond the band (ATR)")
    rsi_n: int = P(14, min=5, max=50, step=1, label="RSI length")
    rsi_low: float = P(30.0, min=5.0, max=50.0, step=1.0, label="RSI oversold")
    rsi_high: float = P(70.0, min=50.0, max=95.0, step=1.0, label="RSI overbought")
    range_bars_15m: int = P(16, min=4, max=64, step=1, label="15m range bars",
                            help="prior closed 15m bars whose high/low define the range regime (16 = 4h)")
    stop_atr: float = P(0.8, min=0.2, max=4.0, step=0.1, label="stop beyond the wick (ATR)")
    mid_frac: float = P(0.7, min=0.1, max=1.0, step=0.05, label="fraction closed at the middle band")
    atr_trail_mult: float = P(1.5, min=0.5, max=6.0, step=0.1, label="ATR trail multiple")
    max_hold_s: int = P(5400, min=300, max=28800, step=300, label="max hold (s)")
    cooldown_s: int = P(300, min=0, max=3600, step=30, label="cooldown after exit (s)")


class KeltnerBandReversion(Strategy):
    id = "S22"
    name = "Keltner Band Reversion"
    Params = Params
    doc = StrategyDoc(
        idea="Fade a close outside the Keltner channel back to the middle band, in a range regime only.",
        timeframe="5m trigger, 15m regime gate",
        symbols="all configured",
        entry="close beyond EMA20 +/- 2 ATR by a further 0.25 ATR, with RSI confirming, while the 15m "
              "close sits inside the prior 16-bar range",
        stop="0.8 ATR beyond the trigger bar's extreme",
        targets="middle band (EMA20) closes 70%, ATR trail on the rest, time exit at 90 min",
        sizing="standard risk per trade at 10x virtual leverage",
        why_aggressive="enters at maximum stretch with no confirmation bar and a tight ATR stop, so R is "
                       "small relative to the distance back to the mean",
    )
    timeframes = ("5m", "15m")
    contributes_votes = False
    warmup_bars = 80
    max_positions = 1
    min_rr = 1.0
    default_leverage = 10

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last: dict[str, dict[str, Any]] = {}
        self._stats = {"stretched": 0, "regime_skips": 0, "rsi_skips": 0, "traded": 0}

    def cooldown_ms(self) -> int:
        return int(self.params.cooldown_s) * 1000

    def _in_range_regime(self, symbol: str, ctx: MarketContext, n: int) -> bool | None:
        c15 = ctx.candles(symbol, "15m")
        if len(c15) < n + 1:
            return None
        lo = last(ctx.ind(symbol, "15m", "lowest", n=n), 1)   # offset 1 excludes the current bar
        hi = last(ctx.ind(symbol, "15m", "highest", n=n), 1)
        if lo is None or hi is None:
            return None
        return lo <= c15[-1].close <= hi

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "5m")
        if len(candles) < max(int(p.ema_n), int(p.atr_n), int(p.rsi_n)) + 5:
            return []
        mid = last(ctx.ind(c.symbol, "5m", "ema", n=int(p.ema_n)))
        atr = last(ctx.ind(c.symbol, "5m", "atr", n=int(p.atr_n)))
        rsi = last(ctx.ind(c.symbol, "5m", "rsi", n=int(p.rsi_n)))
        if mid is None or atr is None or rsi is None or atr <= 0:
            return []
        upper, lower = mid + p.band_atr * atr, mid - p.band_atr * atr
        extra = p.stretch_atr * atr
        if c.close < lower - extra:
            side, target, stop = "long", mid, c.low - p.stop_atr * atr
            rsi_ok = rsi <= p.rsi_low
        elif c.close > upper + extra:
            side, target, stop = "short", mid, c.high + p.stop_atr * atr
            rsi_ok = rsi >= p.rsi_high
        else:
            return []
        self._stats["stretched"] += 1
        stretch_atr = abs(c.close - (lower if side == "long" else upper)) / atr
        self._last[c.symbol] = {"side": side, "stretch_atr": round(stretch_atr, 2), "rsi": round(rsi, 1),
                                "ts": c.close_time}
        if not rsi_ok:
            self._stats["rsi_skips"] += 1
            self._last[c.symbol]["skip"] = "RSI does not confirm the stretch"
            return []
        regime = self._in_range_regime(c.symbol, ctx, int(p.range_bars_15m))
        if regime is None:
            self._last[c.symbol]["skip"] = "15m warm-up"
            return []
        if not regime:
            self._stats["regime_skips"] += 1
            self._last[c.symbol]["skip"] = "15m is trending, not ranging"
            return []
        price = ctx.last_price(c.symbol) or c.close
        # the snap-back must still be ahead of us, and the stop still behind
        if side == "long" and not (stop < price < target):
            return []
        if side == "short" and not (target < price < stop):
            return []
        self._stats["traded"] += 1
        self._last[c.symbol]["traded"] = True
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="5m", price=price, stop=stop,
            tps_price=[(target, p.mid_frac)],
            trail=TrailSpec("atr", "5m", p.atr_trail_mult, int(p.atr_n), True),
            max_hold_s=int(p.max_hold_s), valid_bars=2,
            reason=f"{stretch_atr:.2f} ATR outside the band, RSI {rsi:.0f}",
            meta={"stretch_atr": round(stretch_atr, 2), "rsi": round(rsi, 1), "mid": mid})]

    def state(self) -> dict[str, Any]:
        return {"last_stretch": self._last, **self._stats}

    def reset(self) -> None:
        self._last.clear()
        for k in self._stats:
            self._stats[k] = 0
