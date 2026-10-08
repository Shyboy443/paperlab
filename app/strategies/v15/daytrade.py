"""V15 DAY TRADERS: US stocks / ETFs, regular sessions only, flat by the close (operator, 2026-10-08: "add some day
trading bots"). Research candidates for the pre-registered study scripts/v15_daytrade_study.py; a family goes live only
if it passes that study.

Every family trades at most once per symbol per session, decides on closed 5m candles (aggregated from the 1m tape)
and never holds overnight: the stock engine exits at the session close minus one minute (half days included). The
bot's book takes 1% risk per trade (the V9 sizing, fees, execution and 3x cap); the families differ only in their entry
and their stop.

    V15.1  opening-candle trend   the direction of the first 5-minute candle; stop at its far end; 10R target, so in
                                  practice the close (after Zarattini & Aziz 2023, "Can day trading really be
                                  profitable?", 5-minute ORB on QQQ)
    V15.2  stocks in play         the same direction rule, only when the opening 5 minutes trade at least 1.5x their
                                  usual volume (14 sessions); stop 10% of the 14-session ATR (true range of each
                                  regular session against the prior close); held to the close
                                  (after Zarattini, Barbon & Aziz 2024, ORB on stocks in play)
    V15.3  30-minute breakout     a 5m close beyond the first half hour's range, until an hour before the close; stop
                                  at the range's other side; held to the close (the classic opening-range breakout)
    V15.4  noise-band momentum    at each half-hour mark from 10:00, a close outside the stock's usual move from the
                                  open (14-session average at that time of day, around the open and the prior close);
                                  stop at the band or the session VWAP; held to the close (after Zarattini, Aziz &
                                  Barbon 2024, "Beat the market", SPY)
    V15.5  last-half-hour momentum  at 30 minutes before the close, the direction of the first half hour's return from
                                  the prior close; 0.5% stop; out at the close (after Gao, Han, Li & Zhou 2018, market
                                  intraday momentum)

Stops are clamped to at least 0.10% of price (a doji opening candle is skipped instead) and a setup needing more than
2.0% is skipped. No edge is claimed: the study decides.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.strategies.v6.base import V6Strategy

ET = ZoneInfo("America/New_York")
MIN = 60_000
HOLD_S = 7 * 3600                    # longer than any session: the engine's session deadline closes the trade
LOOKBACK = 14                        # sessions of history for volume and noise baselines
MIN_HISTORY = 10                     # baselines need at least this many sessions


@dataclass
class Params:
    min_stop_pct: float = 0.0010
    max_stop_pct: float = 0.0200
    doji_pct: float = 0.0002         # an opening candle moving less than 0.02% has no direction
    target_r: float = 10.0           # V15.1 only
    rvol_min: float = 1.5            # V15.2 only
    atr_stop_frac: float = 0.10      # V15.2 only
    last30_stop_pct: float = 0.005   # V15.5 only
    last30_min_move: float = 0.0010  # V15.5: skip a first half hour that moved less than this


def session_of(t: int, session_at: Callable[[int], Any] | None):
    """(open_ms, close_ms) of the regular session containing instant `t`, or None."""
    if session_at is not None:
        return session_at(t)
    et = datetime.fromtimestamp(t / 1000, ET)
    if et.weekday() >= 5:
        return None
    s = tuple(int(datetime.combine(et.date(), clock, ET).timestamp() * 1000) for clock in (dtime(9, 30), dtime(16, 0)))
    return s if s[0] <= t < s[1] else None


class DayTrader(V6Strategy):
    version = "v15"
    Params = Params
    family = "DAY_TRADE"
    signal_tf = "5m"
    session_at: Callable[[int], Any] | None = None
    expected_hold = "minutes to hours, always flat by the close"
    expected_frequency = "at most one trade per symbol per session"
    fails_when = "the open's direction does not persist, or the spread and fees outweigh the move"

    @classmethod
    def for_class(cls, horizon: str = "DAY", feed: Any = None, market: Any = None, session_at=None):
        return type(cls.__name__ + "_DAY", (cls,), {
            "horizon": "DAY", "signal_tf": "5m", "ctx_fast": "1d", "ctx_slow": "1d", "context_tf": "1d",
            "native_timeframe": "5m", "supported_timeframes": ("5m",), "timeframes": ("5m",),
            "feed": feed, "market": market, "time_stop_h": HOLD_S / 3600, "tag": "DAY", "day_bars": 78,
            "journal": {}, "session_at": staticmethod(session_at) if session_at is not None else None,
            "__module__": cls.__module__})

    # -- per-bot memory (one strategy instance per bot) -----------------------------------------------------------
    def mem(self) -> dict[str, Any]:
        m = self.__dict__.get("_mem")
        if m is None:
            m = self.__dict__["_mem"] = {"done": None, "seen": None, "prev_close": None, "open": None,
                                         "open_vols": deque(maxlen=LOOKBACK), "moves": {}, "m30": None,
                                         "day": None, "trs": deque(maxlen=LOOKBACK)}
        return m

    @staticmethod
    def daily_atr(m) -> float | None:
        """Average true range of the last 14 completed sessions, from this bot's own session highs, lows and closes
        (the engine builds no daily candle from a session tape: a day is never 1,440 contiguous minutes)."""
        trs = list(m["trs"])
        return sum(trs) / len(trs) if len(trs) >= MIN_HISTORY else None

    def todays(self, ctx, c, s) -> list:
        return [b for b in ctx.candles(c.symbol, "5m") if b.closed and s[0] <= b.open_time and b.close_time <= c.close_time]

    def on_candle(self, c, ctx):
        if not self.bound(c) or c.close <= 0:
            return []
        s = session_of(c.open_time, self.session_at)
        if s is None:
            return []
        m = self.mem()
        if m["seen"] != s[0]:                                    # the first candle we see of a new session
            d = m["day"]
            if d is not None:                                    # the session that just ended: its true range
                ref = d["prev_close"] if d["prev_close"] is not None else d["low"]
                m["trs"].append(max(d["high"], ref) - min(d["low"], ref))
            m["seen"] = s[0]
            before = [b for b in ctx.candles(c.symbol, "5m") if b.closed and b.close_time < s[0]]
            m["prev_close"] = before[-1].close if before else None
            m["open"] = c.open if c.open_time == s[0] else None
            m["m30"] = None
            m["day"] = {"high": c.high, "low": c.low, "prev_close": m["prev_close"]}
        m["day"]["high"], m["day"]["low"] = max(m["day"]["high"], c.high), min(m["day"]["low"], c.low)
        if c.open_time == s[0] + 25 * MIN:                       # the candle ending at the 30-minute mark
            m["m30"] = c.close
        self.observe(c, ctx, s, m)
        if m["done"] == s[0] or c.close_time + 1 > s[1] - 2 * MIN:
            return []
        setup = self.decide(c, ctx, s, m)
        if setup is None:
            return []
        side, stop_pct, tps_r, note, extra = setup
        if stop_pct is None or stop_pct > self.params.max_stop_pct:
            return []
        stop_pct = max(stop_pct, self.params.min_stop_pct)
        sig = self.entry(c=c, ctx=ctx, side=side, price=c.close, stop_pct=stop_pct, tps_r=tps_r, trail_atr=None,
                         be_at_r=None, expected_move_pct=(tps_r[0][0] if tps_r else 1.0) * stop_pct,
                         expected_move_source=note, factors={"setup": 0.5}, reason=note,
                         extra={"setup": note, "horizon": "DAY", "market": "US_EQUITY",
                                "stock_session_close_ms": s[1], **extra})
        sig.max_hold_s = HOLD_S
        sig.id = f"{self.id}:DAY:{c.close_time}:{side}"
        m["done"] = s[0]
        return [sig]

    def observe(self, c, ctx, s, m) -> None:
        """Record history a family needs (called on every closed 5m candle, before any decision)."""

    def decide(self, c, ctx, s, m):
        raise NotImplementedError


class OpeningCandleV15(DayTrader):
    id = "V15.1"
    name = "Opening-candle trend"
    thesis = "The direction of the first five minutes tends to carry through the session"

    def decide(self, c, ctx, s, m):
        if c.open_time != s[0]:
            return None
        body = c.close - c.open
        if abs(body) < self.params.doji_pct * c.open:
            return None
        side = "long" if body > 0 else "short"
        dist = c.close - c.low if side == "long" else c.high - c.close
        return side, (dist / c.close if dist > 0 else self.params.min_stop_pct), [(self.params.target_r, 1.0)], \
            "First 5-minute candle direction", {}


class StocksInPlayV15(DayTrader):
    id = "V15.2"
    name = "Stocks in play opening range"
    thesis = "A stock trading unusual opening volume keeps the direction of its first five minutes"

    def observe(self, c, ctx, s, m):
        if c.open_time == s[0]:
            m["rvol_now"] = None
            hist = list(m["open_vols"])
            if len(hist) >= MIN_HISTORY and sum(hist) > 0:
                m["rvol_now"] = c.volume / (sum(hist) / len(hist))
            m["open_vols"].append(c.volume)

    def decide(self, c, ctx, s, m):
        if c.open_time != s[0] or not m.get("rvol_now") or m["rvol_now"] < self.params.rvol_min:
            return None
        body = c.close - c.open
        if abs(body) < self.params.doji_pct * c.open:
            return None
        atr = self.daily_atr(m)
        if not atr:
            return None
        side = "long" if body > 0 else "short"
        return side, self.params.atr_stop_frac * atr / c.close, [], "Opening range on unusual volume", \
            {"rvol": round(m["rvol_now"], 3)}


class RangeBreakoutV15(DayTrader):
    id = "V15.3"
    name = "30-minute opening-range breakout"
    thesis = "A break of the first half hour's range starts the session's trend"

    def decide(self, c, ctx, s, m):
        if c.close_time + 1 < s[0] + 35 * MIN or c.close_time + 1 > s[1] - 60 * MIN:
            return None
        rng = [b for b in self.todays(ctx, c, s) if b.open_time < s[0] + 30 * MIN]
        if len(rng) < 6:
            return None
        hi, lo = max(b.high for b in rng), min(b.low for b in rng)
        if c.close > hi:
            return "long", (c.close - lo) / c.close, [], "Close above the 30-minute range", {"range_pct": (hi - lo) / c.close}
        if c.close < lo:
            return "short", (hi - c.close) / c.close, [], "Close below the 30-minute range", {"range_pct": (hi - lo) / c.close}
        return None


class NoiseBandV15(DayTrader):
    id = "V15.4"
    name = "Noise-band intraday momentum"
    thesis = "A move beyond the stock's usual distance from the open, at that time of day, keeps going"

    def observe(self, c, ctx, s, m):
        mark = (c.close_time + 1 - s[0]) // MIN
        if mark % 30 == 0 and m["open"]:
            m["moves"].setdefault(mark, deque(maxlen=LOOKBACK + 1)).append((s[0], abs(c.close / m["open"] - 1)))

    def decide(self, c, ctx, s, m):
        mark = (c.close_time + 1 - s[0]) // MIN
        if mark % 30 or mark < 30 or not m["open"] or not m["prev_close"]:
            return None
        hist = [v for day, v in m["moves"].get(mark, ()) if day != s[0]]
        if len(hist) < MIN_HISTORY:
            return None
        sigma = sum(hist[-LOOKBACK:]) / len(hist[-LOOKBACK:])
        upper = max(m["open"], m["prev_close"]) * (1 + sigma)
        lower = min(m["open"], m["prev_close"]) * (1 - sigma)
        bars = self.todays(ctx, c, s)
        vol = sum(b.volume for b in bars if b.volume > 0)
        vwap = sum((b.high + b.low + b.close) / 3 * b.volume for b in bars if b.volume > 0) / vol if vol > 0 else None
        if c.close > upper:
            level = max(upper, vwap) if vwap is not None and vwap < c.close else upper
            return "long", (c.close - level) / c.close, [], "Above the noise band", {"sigma": round(sigma, 5)}
        if c.close < lower:
            level = min(lower, vwap) if vwap is not None and vwap > c.close else lower
            return "short", (level - c.close) / c.close, [], "Below the noise band", {"sigma": round(sigma, 5)}
        return None


class LastHalfHourV15(DayTrader):
    id = "V15.5"
    name = "Last-half-hour momentum"
    thesis = "The first half hour's move from the prior close predicts the last half hour"

    def decide(self, c, ctx, s, m):
        if c.close_time + 1 != s[1] - 30 * MIN or not m["m30"] or not m["prev_close"]:
            return None
        r1 = m["m30"] / m["prev_close"] - 1
        if abs(r1) < self.params.last30_min_move:
            return None
        return ("long" if r1 > 0 else "short"), self.params.last30_stop_pct, [], "First half hour sets the last", \
            {"first_half_hour_ret": round(r1, 5)}


def load_v15_day_traders() -> dict[str, type[DayTrader]]:
    return {cls.id: cls for cls in (OpeningCandleV15, StocksInPlayV15, RangeBreakoutV15, NoiseBandV15, LastHalfHourV15)}
