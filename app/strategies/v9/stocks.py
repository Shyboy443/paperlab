"""V9 STOCKS: the three V8 scalp families on US stocks / ETFs, regular sessions only (docs/V9_PROTOCOL.md).

Stock-specific versions of the V8 families. Structural windows and VWAP reset
at the regular-session open. Silent bars cannot create entries. Pullback bars
must intersect their contemporaneous EMA band, and VWAP reversals target the
session average only when at least 0.75R of room remains.

    session   a candidate only from 10 minutes after the open to 50 minutes before the close (the session's own
              close: half days included), so the 45-minute time stop always exits inside the session -- nothing is
              carried overnight
    stop      the structural extreme + 0.2 ATR(5m), at least 0.25% and at most 1.0% of price: stocks trade commission-
              free, so V8's 0.45% floor (set by Bybit's taker fee) is not needed; 0.25% keeps 1% risk at or below the
              3x paper buying-power cap (sizing may lower risk)
    target    1.5R for trend scalps; current-session VWAP for snap-back scalps;
              time stop 45 minutes, capped at the session close; cooldown one 5m bar

    V9.1  micro-pullback   (V8.1's setup)
    V9.2  micro-breakout   (V8.2's setup)
    V9.3  VWAP snap-back   (current-session VWAP, prior-bar stretch)

No edge is claimed; the forward paper results decide.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.strategies.v8.arena import MicroBreakoutV8, MicroPullbackV8, VwapSnapV8
from app.strategies.v8.arena import Params as ParamsV8
from app.strategies.v6.base import Setup, close_location, scale
from app.core.types import TakeProfit

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


def session_for(t: int, session_at: Callable[[int], Any] | None):
    if session_at is not None:
        return session_at(t)
    et = datetime.fromtimestamp(t / 1000, ET)
    if et.weekday() >= 5:
        return None
    return tuple(int(datetime.combine(et.date(), clock, ET).timestamp() * 1000)
                 for clock in (dtime(9, 30), dtime(16, 0)))


def session_bars(ctx, c, session_at):
    session = session_for(c.close_time, session_at)
    if session is None:
        return []
    return [b for b in ctx.candles(c.symbol, '5m')
            if b.closed and session[0] <= b.open_time and b.close_time <= c.close_time]


def session_vwap(bars):
    """Session-only HLC3/volume proxy, identical on archived and forward bars."""
    volume = sum(b.volume for b in bars if b.volume > 0)
    return (sum((b.high + b.low + b.close) / 3 * b.volume for b in bars if b.volume > 0) / volume
            if volume > 0 else None)


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
        if c.volume <= 0 or not entry_allowed(c.close_time + 1, self.session_at):
            return []
        sigs = super().on_candle(c, ctx)
        for s in sigs:
            s.meta["market"] = "US_EQUITY"
            session = session_for(c.close_time, self.session_at)
            if session is not None:
                s.meta['stock_session_close_ms'] = session[1]
        return sigs


class PullbackV9(StockSession, MicroPullbackV8):
    id = "V9.1"
    name = "Stock micro pullback scalp"

    def scalp_setup(self, ctx, c, cs, atr, trend15, trend1h):
        todays = session_bars(ctx, c, self.session_at)
        if len(todays) < 4 or trend15 not in ('up', 'down'):
            return None
        side = 'long' if trend15 == 'up' else 'short'
        if trend1h == ('down' if side == 'long' else 'up'):
            return None
        emas = ctx.ind(c.symbol, '5m', 'ema', n=20)
        if not emas or len(emas) < 4 or any(e is None for e in emas[-4:]):
            return None
        ema = emas[-1]
        # A low anywhere below EMA is not a touch. The prior candle must
        # overlap its OWN contemporaneous EMA band, on today's session.
        touched = any(b.volume > 0 and b.low <= e + 0.35 * atr and b.high >= e - 0.35 * atr
                      for b, e in zip(todays[-4:-1], emas[-4:-1]))
        long = side == 'long'
        resumed = (c.close > todays[-2].high and c.close > ema) if long else (c.close < todays[-2].low and c.close < ema)
        location = close_location(c) if long else 1 - close_location(c)
        if not touched or not resumed or location < 0.5 or abs(c.close - ema) > 1.5 * atr:
            return None
        extreme = min(b.low for b in todays[-4:]) if long else max(b.high for b in todays[-4:])
        return Setup(side, extreme, {'close': scale(location, 0.5, 1),
                                     'trend': 1 if trend1h == trend15 else 0.5}, 'Session pullback reclaimed its EMA band')


class BreakoutV9(StockSession, MicroBreakoutV8):
    id = "V9.2"
    name = "Stock micro breakout scalp"

    def scalp_setup(self, ctx, c, cs, atr, trend15, trend1h):
        todays = session_bars(ctx, c, self.session_at)
        if len(todays) < 7 or sum(b.volume > 0 for b in todays[-7:-1]) < 3:
            return None
        return super().scalp_setup(ctx, c, todays, atr, trend15, trend1h)


class VwapSnapV9(StockSession, VwapSnapV8):
    id = "V9.3"
    name = "Stock VWAP snap-back scalp"

    def scalp_setup(self, ctx, c, cs, atr, trend15, trend1h):
        todays = session_bars(ctx, c, self.session_at)
        if len(todays) < 4 or todays[-2].volume <= 0:
            return None
        vwap = session_vwap(todays[:-1])
        if vwap is None:
            return None
        prev = todays[-2]
        stretch = (prev.close - vwap) / atr
        if -4 <= stretch <= -1.8 and c.close > c.open and c.close > prev.close and close_location(c) >= 0.6:
            side = 'long'
        elif 1.8 <= stretch <= 4 and c.close < c.open and c.close < prev.close and close_location(c) <= 0.4:
            side = 'short'
        else:
            return None
        extreme = min(b.low for b in todays[-3:]) if side == 'long' else max(b.high for b in todays[-3:])
        return Setup(side, extreme, {'stretch': scale(abs(stretch), 1.8, 3.5)}, 'Current-session VWAP reversal')

    def on_candle(self, c, ctx):
        sigs = super().on_candle(c, ctx)
        target = session_vwap(session_bars(ctx, c, self.session_at)) if sigs else None
        kept = []
        for sig in sigs:
            distance = abs(sig.entry_price - sig.stop)
            reward = (target - sig.entry_price) * (1 if sig.side == 'long' else -1) if target is not None else 0
            if distance <= 0 or reward / distance < 0.75:
                continue  # A mean-reversion target must have meaningful room BEFORE entry.
            sig.take_profits = [TakeProfit(target, 1.0)]
            sig.meta.update(target_r=reward / distance, reward_risk=reward / distance,
                            expected_move_pct=reward / sig.entry_price,
                            expected_move_source='Current-session VWAP target', stock_session_vwap=target)
            kept.append(sig)
        return kept


def load_v9_stock_scalpers():
    return {cls.id: cls for cls in (PullbackV9, BreakoutV9, VwapSnapV9)}
