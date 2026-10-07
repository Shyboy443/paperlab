"""S05 VWAP Reclaim Scalp - "wick through VWAP, close back, go"

Idea:       Scalp bars that wick through the session VWAP and close back on the other side with rising volume.
Timeframe:  1m (session VWAP resets at 00:00 UTC), optional UTC session-hours filter (OFF by default).
Symbols:    all configured symbols.
Entry:      long when the bar's low is below VWAP but it closes above VWAP, closes up (close > open) and its
            volume exceeds the previous bar's; short on the mirror image (high above VWAP, close below, close
            down, volume up).
Stop:       0.5 x ATR(14) from the entry price.
Targets:    single TP at 3R closing 100%.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Cooldown:   90s per symbol after any exit, so the book cannot re-enter the same 1m fade tick after tick.
Why aggressive: 1-minute bars, no session filter (runs 24h), very tight 0.5 ATR stop and a 3R target;
            the only brake is the 90s per-symbol cooldown.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    stop_atr_mult: float = P(0.5, min=0.1, max=3.0, step=0.1, label="stop ATR mult")
    tp_r: float = P(3.0, min=1.0, max=6.0, step=0.1, label="TP (R)")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")
    session_filter: bool = P(False, label="session filter", help="only trade inside [start, end) UTC hours")
    cooldown_after_loss_s: int = P(90, min=0, max=3600, step=5, label="cooldown after exit (s)",
                                   help="the engine refuses a re-entry on that symbol for this long after an exit")
    session_start_utc: int = P(0, min=0, max=24, step=1, label="session start (UTC hour)")
    session_end_utc: int = P(24, min=0, max=24, step=1, label="session end (UTC hour)")


class VwapReclaimScalp(Strategy):
    id = "S05"
    name = "VWAP Reclaim Scalp"
    Params = Params
    doc = StrategyDoc(
        idea="Scalp bars that wick through the session VWAP and close back on the other side with rising volume.",
        timeframe="1m (session VWAP resets 00:00 UTC), optional UTC session filter",
        symbols="all configured",
        entry="low < VWAP < close with close > open and volume > previous bar -> long; "
              "high > VWAP > close with close < open and volume > previous bar -> short",
        stop="0.5 x ATR(14) from the entry",
        targets="single TP at 3R closing 100%",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="1m bars, no session filter, very tight 0.5 ATR stop; braked only by a 90s cooldown",
    )
    timeframes = ("1m",)
    contributes_votes = False
    warmup_bars = 200
    min_rr = 2.5

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last_signal: dict[str, dict[str, Any]] = {}
        self._vwap: dict[str, float] = {}
        self._skips: dict[str, int] = {}

    def cooldown_ms(self) -> int:
        """Block a re-entry on the same symbol for this long after an exit (stops the tick-by-tick re-fade)."""
        return max(0, int(self.params.cooldown_after_loss_s)) * 1000

    def _in_session(self, open_time: int) -> bool:
        p = self.params
        hour = (open_time // 3_600_000) % 24
        start, end = int(p.session_start_utc), int(p.session_end_utc)
        if start < end:
            return start <= hour < end
        if start > end:  # window wraps midnight, e.g. 22 -> 4
            return hour >= start or hour < end
        return False

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "1m":
            return []
        p = self.params
        candles = ctx.candles(c.symbol, "1m")
        if len(candles) < p.atr_period + 3:
            return []
        if p.session_filter and not self._in_session(c.open_time):
            self._skips["session"] = self._skips.get("session", 0) + 1
            return []
        vwap = ctx.ind_last(c.symbol, "1m", "vwap")
        atr = ctx.ind_last(c.symbol, "1m", "atr", n=p.atr_period)
        if vwap is None or atr is None or atr <= 0:
            return []
        self._vwap[c.symbol] = vwap
        prev = candles[-2] if candles[-1].open_time == c.open_time else candles[-1]
        vol_up = c.volume > prev.volume
        if c.low < vwap < c.close and c.close > c.open and vol_up:
            side = "long"
        elif c.high > vwap > c.close and c.close < c.open and vol_up:
            side = "short"
        else:
            return []
        price = ctx.last_price(c.symbol) or c.close
        stop = price - p.stop_atr_mult * atr if side == "long" else price + p.stop_atr_mult * atr
        if stop <= 0:
            return []
        self._last_signal[c.symbol] = {"ts": c.close_time, "side": side, "vwap": vwap, "atr": atr}
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="1m", price=price, stop=stop,
            tps_r=[(p.tp_r, 1.0)], valid_bars=2,
            reason=f"VWAP reclaim {side} vol {c.volume:.0f}>{prev.volume:.0f}",
            meta={"vwap": vwap, "atr": atr, "prev_volume": prev.volume})]

    def state(self) -> dict[str, Any]:
        return {"last_signal": self._last_signal, "vwap": self._vwap, "skips": self._skips}

    def reset(self) -> None:
        self._last_signal.clear()
        self._vwap.clear()
        self._skips.clear()
