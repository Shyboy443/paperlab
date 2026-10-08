"""V17 SWING: long-hold crypto strategies (operator, 2026-10-08: "build day trading / swing trading strategies that can
hold trades for longer durations, because the scalp strategies all lost to fees"). Research candidates for the
pre-registered study scripts/v17_swing_study.py; a family runs live only if it passes that study.

All decide on closed 4h candles of one Bybit perpetual, with rules on COMPLETED daily bars (aggregated from the 1m
tape; today's forming day is never read). One position per bot. The initial stop is a multiple of the daily ATR(20);
the family's own exit is an exit signal on a 4h close. Sizing: the bot's 1% risk book (V6's sizing, fees, execution
and real funding), so a wide stop means a small position.

    V17.1  Donchian 20/10    long a 4h close above the prior 20-day high, short below the 20-day low; exit on a 4h
                             close beyond the opposite 10-day extreme; stop 2 ATR (the Turtle "system 1" rule)
    V17.2  trend MA          daily EMA20 above EMA50 and the close above EMA20 = up-trend (mirror for down); enter on
                             a 4h close beyond the prior 5-day extreme in the trend; exit when a daily close crosses
                             EMA20 against the trade; stop 3 ATR
    V17.3  7/3 breakout      long only: a 4h close above the prior 7-day high; exit on a 4h close below the prior
                             3-day low; stop 3 ATR (the Bitcoin video's rule, on every coin)
    V17.4  weekly momentum   at each Monday 00:00 UTC, the sign of the 28-day return sets the side; held until a
                             Monday flips the sign (exit, then the opposite entry); stop 4 ATR
    V17.5  day trend         at each 00:00 UTC, if yesterday closed above its 20-day SMA and higher than it opened,
                             long (mirror short); held 24 hours; stop 1.5 ATR

No edge is claimed: the study decides.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.strategies.v6.base import V6Strategy

HOUR = 3_600_000
DAY = 24 * HOUR
WEEK_MONDAY_OFFSET = 4 * DAY          # 1970-01-01 was a Thursday: (t - 4 days) % 7 days == 0 on Monday 00:00 UTC


@dataclass
class Params:
    atr_n: int = 20
    stop_atr: float = 2.0
    max_stop_pct: float = 0.25           # refuse a setup whose stop is more than 25% away (data spike)


def ema(values: list[float], n: int) -> list[float]:
    out, k = [], 2 / (n + 1)
    for v in values:
        out.append(v if not out else out[-1] + k * (v - out[-1]))
    return out


class SwingV17(V6Strategy):
    version = "v17"
    Params = Params
    family = "SWING"
    signal_tf = "4h"
    stop_atr = 2.0
    hold_s = 60 * DAY                   # a long backstop: the family's own exit normally comes first
    expected_hold = "days to weeks"
    expected_frequency = "a few trades per coin per month at most"
    fails_when = "markets chop sideways with false breakouts, or trends reverse faster than the exit"

    @classmethod
    def for_class(cls, horizon: str = "SWING", feed: Any = None, market: Any = None):
        return type(cls.__name__ + "_SWING", (cls,), {
            "horizon": "SWING", "signal_tf": "4h", "ctx_fast": "1d", "ctx_slow": "1d", "context_tf": "1d",
            "native_timeframe": "4h", "supported_timeframes": ("4h",), "timeframes": ("4h", "1d"),
            "feed": feed, "market": market, "time_stop_h": cls.hold_s / 3600, "tag": "4H", "day_bars": 6,
            "journal": {}, "__module__": cls.__module__})

    # -- data ---------------------------------------------------------------------------------------------------------
    def days(self, ctx, c) -> list:
        """Completed daily bars up to the decision instant (never the forming day)."""
        t = c.close_time + 1
        return [d for d in ctx.candles(c.symbol, "1d") if d.closed and d.close_time < t]

    @staticmethod
    def daily_atr(days: list, n: int) -> float | None:
        if len(days) < n + 1:
            return None
        trs = [max(d.high, p.close) - min(d.low, p.close) for p, d in zip(days[-n - 1:-1], days[-n:])]
        return sum(trs) / n

    def position(self, ctx, symbol: str):
        mine = [p for p in ctx.positions_of(self.id) if p.symbol == symbol] if hasattr(ctx, "positions_of") else []
        return mine[0] if mine else None

    # -- signals ------------------------------------------------------------------------------------------------------
    def enter(self, c, ctx, side: str, atr: float, note: str, mult: float | None = None) -> Any:
        stop_pct = (mult or self.stop_atr) * atr / c.close
        if stop_pct <= 0 or stop_pct > self.params.max_stop_pct:
            return None
        sig = self.entry(c=c, ctx=ctx, side=side, price=c.close, stop_pct=stop_pct, tps_r=[], trail_atr=None,
                         be_at_r=None, expected_move_pct=2 * stop_pct, expected_move_source=note,
                         factors={"setup": 0.5}, reason=note, extra={"setup": note, "horizon": "SWING"})
        sig.max_hold_s = int(self.hold_s / 1000)
        sig.id = f"{self.id}:SWING:{c.close_time}:{side}"
        return sig

    def leave(self, c, side: str, why: str) -> Any:
        return self.make_exit(symbol=c.symbol, side=side, ts=c.close_time, tf=self.signal_tf, reason=why[:120])

    def on_candle(self, c, ctx):
        if not self.bound(c) or c.close <= 0:
            return []
        days = self.days(ctx, c)
        if len(days) < 30:
            return []
        atr = self.daily_atr(days, self.params.atr_n)
        if not atr:
            return []
        pos = self.position(ctx, c.symbol)
        out = []
        if pos is not None:
            why = self.exit_reason(c, ctx, days, pos.side)
            if why:
                out.append(self.leave(c, pos.side, why))
                if not self.flip_on_exit:
                    return out
            else:
                return out
        sig = self.decide(c, ctx, days, atr, pos)
        if sig is not None:
            out.append(sig)
        return out

    flip_on_exit = False

    def exit_reason(self, c, ctx, days, side) -> str | None:
        return None

    def decide(self, c, ctx, days, atr, pos) -> Any:
        raise NotImplementedError


class DonchianV17(SwingV17):
    id = "V17.1"
    name = "Donchian 20/10 breakout"
    thesis = "A break of the 20-day range starts a trend that lasts longer than the fees"
    stop_atr = 2.0

    def exit_reason(self, c, ctx, days, side):
        w = days[-10:]
        if side == "long" and c.close < min(d.low for d in w):
            return "4h close below the 10-day low"
        if side == "short" and c.close > max(d.high for d in w):
            return "4h close above the 10-day high"
        return None

    def decide(self, c, ctx, days, atr, pos):
        w = days[-20:]
        if c.close > max(d.high for d in w):
            return self.enter(c, ctx, "long", atr, "4h close above the 20-day high")
        if c.close < min(d.low for d in w):
            return self.enter(c, ctx, "short", atr, "4h close below the 20-day low")
        return None


class TrendMAV17(SwingV17):
    id = "V17.2"
    name = "Daily trend with 5-day breakout"
    thesis = "Inside a daily EMA trend, a 5-day breakout joins the trend; the trend's end is the exit"
    stop_atr = 3.0

    def trend(self, days):
        closes = [d.close for d in days]
        e20, e50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
        if e20 > e50 and closes[-1] > e20:
            return "up", e20
        if e20 < e50 and closes[-1] < e20:
            return "down", e20
        return None, e20

    def exit_reason(self, c, ctx, days, side):
        closes = [d.close for d in days]
        e20 = ema(closes, 20)[-1]
        if side == "long" and closes[-1] < e20:
            return "daily close below EMA20"
        if side == "short" and closes[-1] > e20:
            return "daily close above EMA20"
        return None

    def decide(self, c, ctx, days, atr, pos):
        if len(days) < 60:
            return None
        trend, _ = self.trend(days)
        w = days[-5:]
        if trend == "up" and c.close > max(d.high for d in w):
            return self.enter(c, ctx, "long", atr, "5-day breakout in a daily up-trend")
        if trend == "down" and c.close < min(d.low for d in w):
            return self.enter(c, ctx, "short", atr, "5-day breakdown in a daily down-trend")
        return None


class Breakout73V17(SwingV17):
    id = "V17.3"
    name = "7-day high / 3-day low breakout"
    thesis = "A new 7-day high in crypto tends to keep running until a 3-day low"
    stop_atr = 3.0

    def exit_reason(self, c, ctx, days, side):
        if side == "long" and c.close < min(d.low for d in days[-3:]):
            return "4h close below the 3-day low"
        return None

    def decide(self, c, ctx, days, atr, pos):
        if c.close > max(d.high for d in days[-7:]):
            return self.enter(c, ctx, "long", atr, "4h close above the 7-day high")
        return None


class WeeklyMomentumV17(SwingV17):
    id = "V17.4"
    name = "Weekly 28-day momentum"
    thesis = "The last four weeks' direction persists for the next week (time-series momentum)"
    stop_atr = 4.0
    flip_on_exit = True

    @staticmethod
    def monday(c) -> bool:
        t = c.close_time + 1
        return (t - WEEK_MONDAY_OFFSET) % (7 * DAY) == 0

    def side(self, days) -> str | None:
        if len(days) < 29:
            return None
        r = days[-1].close / days[-29].close - 1
        return "long" if r > 0 else "short" if r < 0 else None

    def exit_reason(self, c, ctx, days, side):
        if self.monday(c):
            want = self.side(days)
            if want is not None and want != side:
                return "the 28-day momentum flipped"
        return None

    def decide(self, c, ctx, days, atr, pos):
        if not self.monday(c):
            return None
        want = self.side(days)
        if want is None or (pos is not None and pos.side == want):
            return None
        return self.enter(c, ctx, want, atr, f"28-day momentum {want}")


class DayTrendV17(SwingV17):
    id = "V17.5"
    name = "24-hour day trend"
    thesis = "A day that closes up above its 20-day average is followed by another up day (and the mirror)"
    stop_atr = 1.5
    hold_s = DAY

    def decide(self, c, ctx, days, atr, pos):
        if (c.close_time + 1) % DAY or pos is not None:
            return None
        y = days[-1]
        sma = sum(d.close for d in days[-20:]) / 20
        if y.close > sma and y.close > y.open:
            return self.enter(c, ctx, "long", atr, "yesterday up and above its 20-day average")
        if y.close < sma and y.close < y.open:
            return self.enter(c, ctx, "short", atr, "yesterday down and below its 20-day average")
        return None


def load_v17() -> dict[str, type[SwingV17]]:
    return {cls.id: cls for cls in (DonchianV17, TrendMAV17, Breakout73V17, WeeklyMomentumV17, DayTrendV17)}
