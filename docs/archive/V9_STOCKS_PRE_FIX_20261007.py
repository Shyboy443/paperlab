"""V9 STOCKS: the three V8 scalp families on US stocks / ETFs, regular sessions only (docs/V9_PROTOCOL.md).

Same setups as V8 (app/strategies/v8/arena.py, unchanged and inherited), with what a stock session needs:

    session   a candidate only from 10 minutes after the open to 50 minutes before the close (the session's own
              close: half days included), so the 45-minute time stop always exits inside the session -- nothing is
              carried overnight
    stop      the structural extreme + 0.2 ATR(5m), at least 0.25% and at most 1.0% of price: stocks trade commission-
              free, so V8's 0.45% floor (set by Bybit's taker fee) is not needed; 0.25% keeps 1% risk at or below the
              4x intraday buying power
    target    1.5R; time stop 45 minutes; cooldown one 5m bar

    V9.1  micro-pullback   (V8.1's setup)
    V9.2  micro-breakout   (V8.2's setup)
    V9.3  VWAP snap-back   (V8.3's setup, rolling 4h VWAP)

No edge is claimed; the forward paper results decide.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.strategies.v8.arena import MicroBreakoutV8, MicroPullbackV8, VwapSnapV8
from app.strategies.v8.arena import Params as ParamsV8

ET = ZoneInfo("America/New_York")
OPEN_DELAY_MS = 10 * 60_000
CLOSE_BUFFER_MS = 50 * 60_000
FALLBACK_WINDOW = (dtime(9, 40), dtime(15, 10))        # only if no calendar is attached


@dataclass
class Params(ParamsV8):
    min_stop_pct: float = 0.0025
    max_stop_pct: float = 0.010


def entry_allowed(t: int, session_at: Callable[[int], Any] | None) -> bool:
    """May a candidate decided at `t` (ms) be traded? Inside [open + 10 min, close - 50 min] of its session."""
    if session_at is not None:
        s = session_at(t - 1)
        return s is not None and s[0] + OPEN_DELAY_MS <= t <= s[1] - CLOSE_BUFFER_MS
    et = datetime.fromtimestamp(t / 1000, ET)
    return et.weekday() < 5 and FALLBACK_WINDOW[0] <= et.time() <= FALLBACK_WINDOW[1]


class StockSession:
    """Mixin in front of a V8 family: the stock session gate and the V9 parameters."""
    version = "v9"
    Params = Params
    expected_frequency = "several per bot per session (6.5 h)"
    session_at: Callable[[int], Any] | None = None

    @classmethod
    def for_class(cls, horizon: str = "SCALP", feed=None, market=None, session_at=None):
        k = super().for_class(horizon, feed, market)
        k.day_bars = 78
        k.session_at = staticmethod(session_at) if session_at is not None else None
        return k

    def on_candle(self, c, ctx):
        if not entry_allowed(c.close_time + 1, self.session_at):
            return []
        sigs = super().on_candle(c, ctx)
        for s in sigs:
            s.meta["market"] = "US_EQUITY"
        return sigs


class PullbackV9(StockSession, MicroPullbackV8):
    id = "V9.1"
    name = "Stock micro pullback scalp"


class BreakoutV9(StockSession, MicroBreakoutV8):
    id = "V9.2"
    name = "Stock micro breakout scalp"


class VwapSnapV9(StockSession, VwapSnapV8):
    id = "V9.3"
    name = "Stock VWAP snap-back scalp"


def load_v9_stock_scalpers():
    return {cls.id: cls for cls in (PullbackV9, BreakoutV9, VwapSnapV9)}
