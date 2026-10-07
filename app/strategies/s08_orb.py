"""S08 Opening Range Break - "first break of every hour's opening range"

Idea:       With `hourly_orb` (the default) EVERY UTC hour builds a fresh range from its first `range_minutes`
            1m bars, and the first 1m close outside that range is tradeable for the rest of that hour. With
            `hourly_orb` off the classic one-range-per-UTC-session behaviour is used instead.
Timeframe:  1m.
Symbols:    all configured symbols (one range per symbol per hour, or per session in daily mode).
Entry:      first 1m close above the range -> long, below -> short, with volume > 1.8 x the mean volume of the
            range bars; one trade per range; only within max_wait_minutes after the range completes (and never
            past the end of that hour in hourly mode). With require_retest the entry waits for a bar that
            touches the broken edge and closes back outside.
Stop:       the other side of the range (range low for longs, range high for shorts).
Targets:    entry +/- 2.0 x range height, full size.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: 24 tradeable ranges a day instead of 1, first break only, full size, no retest wait.

Range handling: in hourly mode the range window is [HH:00, HH:00 + range_minutes) of every UTC hour and the
range stays usable until the end of that hour - a range built at 13:00 UTC is traded at 13:20 UTC with no
dependence on 00:00. In daily mode the session starts at `session_start_hour_utc` each UTC day and a bar before
that hour belongs to the previous day's session. Either way the range is rebuilt from the closed 1m history
while it forms, so a restart inside the window still knows the range, and the first close outside the range is
THE break: if its volume does not qualify the range is consumed (low_volume) and no later break is traded.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
MINUTE_MS = 60_000


@dataclass
class Params:
    hourly_orb: bool = P(True, label="hourly opening range",
                         help="build a fresh range from the first range_minutes 1m bars of EVERY UTC hour "
                              "(off = one range per UTC session starting at session_start_hour_utc)")
    session_start_hour_utc: int = P(0, min=0, max=23, step=1, label="session start (UTC hour)",
                                    help="daily mode only (hourly_orb off)")
    range_minutes: int = P(15, min=3, max=120, step=1, label="range minutes",
                           help="number of 1m bars after the session start that form the range")
    volume_mult: float = P(1.8, min=0.5, max=5.0, step=0.1, label="break volume mult",
                           help="break-bar volume must exceed this multiple of the mean range-bar volume")
    tp_range_mult: float = P(2.0, min=0.5, max=6.0, step=0.1, label="TP (range heights)")
    max_wait_minutes: int = P(240, min=5, max=720, step=5, label="max wait (minutes)",
                              help="a break is only traded this long after the range completes")
    require_retest: bool = P(False, label="require retest",
                             help="after the break wait for a bar that touches the broken edge and closes back outside")


class OpeningRangeBreak(Strategy):
    id = "S08"
    name = "Opening Range Break"
    Params = Params
    doc = StrategyDoc(
        idea="First 1m close outside the hour's opening range (first 15 1m bars of every UTC hour).",
        timeframe="1m",
        symbols="all configured (one range per symbol per UTC hour; per session when hourly_orb is off)",
        entry="first close above the range with volume > 1.8 x mean range-bar volume -> long; below -> short; "
              "one trade per range within max_wait_minutes and inside that hour; optional retest",
        stop="the other side of the range",
        targets="entry +/- 2.0 x range height, full size",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="24 tradeable ranges a day, first break only, full size, no retest wait",
    )
    timeframes = ("1m",)
    contributes_votes = False
    warmup_bars = 300
    min_rr = 1.5

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._ranges: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, int] = {}

    # -- session / range bookkeeping ---------------------------------------------------
    def _session_start(self, open_time: int) -> int:
        """Start of the range period this bar belongs to: the UTC hour in hourly mode, else the UTC session."""
        if self.params.hourly_orb:
            return open_time - open_time % HOUR_MS
        day = open_time - open_time % DAY_MS
        start = day + int(self.params.session_start_hour_utc) * HOUR_MS
        return start if open_time >= start else start - DAY_MS

    def _expiry(self, rec: dict[str, Any]) -> int:
        """First instant at which this range may no longer be traded."""
        end = rec["window_end"] + max(1, int(self.params.max_wait_minutes)) * MINUTE_MS
        if self.params.hourly_orb:
            end = min(end, rec["session"] + HOUR_MS)
        return end

    @staticmethod
    def _new_record(session: int, window_end: int) -> dict[str, Any]:
        stamp = datetime.fromtimestamp(session / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        return {"session": session, "date": stamp, "window_end": window_end, "high": None, "low": None,
                "bars": 0, "vol_avg": 0.0, "complete": False, "partial": False, "traded": False, "done": False,
                "break_side": None, "break_volume_ratio": None, "awaiting_retest": False, "outcome": None}

    @staticmethod
    def _scan(rec: dict[str, Any], candles: Sequence[Candle]) -> None:
        """Rebuild the range from the closed 1m history (bars inside the session's range window)."""
        hi: float | None = None
        lo: float | None = None
        bars = 0
        vol = 0.0
        for x in candles:
            if x.open_time >= rec["window_end"]:
                break
            if x.open_time < rec["session"]:
                continue
            hi = x.high if hi is None else max(hi, x.high)
            lo = x.low if lo is None else min(lo, x.low)
            bars += 1
            vol += x.volume
        rec["high"], rec["low"], rec["bars"] = hi, lo, bars
        rec["vol_avg"] = vol / bars if bars else 0.0

    def _finish(self, rec: dict[str, Any], outcome: str) -> None:
        rec["done"] = True
        rec["outcome"] = outcome
        rec["awaiting_retest"] = False
        self._counters[outcome] = self._counters.get(outcome, 0) + 1

    # -- hook ----------------------------------------------------------------------------
    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "1m":
            return []
        p = self.params
        range_bars = max(1, int(p.range_minutes))
        if p.hourly_orb:
            range_bars = min(range_bars, 59)  # the window must leave room to trade inside the same hour
        session = self._session_start(c.open_time)
        rec = self._ranges.get(c.symbol)
        if rec is None or rec["session"] != session:
            rec = self._new_record(session, session + range_bars * MINUTE_MS)
            self._ranges[c.symbol] = rec
        if not rec["complete"] and not rec["done"]:
            self._scan(rec, ctx.candles(c.symbol, "1m"))
            past_window = c.open_time >= rec["window_end"]
            if rec["bars"] >= range_bars or (past_window and rec["bars"] > 0):
                rec["complete"] = True
                rec["partial"] = rec["bars"] < range_bars
            elif past_window:
                self._finish(rec, "no_range")
            if not rec["complete"]:
                return []
        if rec["done"] or c.open_time < rec["window_end"]:
            return []
        if c.open_time >= self._expiry(rec):
            self._finish(rec, "retest_timeout" if rec["awaiting_retest"] else "no_break")
            return []
        high, low = float(rec["high"]), float(rec["low"])
        height = high - low
        if height <= 0:
            self._finish(rec, "zero_range")
            return []
        side = rec["break_side"]
        if side is None:
            if c.close > high:
                side = "long"
            elif c.close < low:
                side = "short"
            else:
                return []
            rec["break_side"] = side
            rec["break_ts"] = c.close_time
            vol_avg = rec["vol_avg"]
            rec["break_volume_ratio"] = c.volume / vol_avg if vol_avg > 0 else None
            if not c.volume > p.volume_mult * vol_avg:
                self._finish(rec, "low_volume")
                return []
            if p.require_retest:
                rec["awaiting_retest"] = True
                return []
        else:
            retested = (c.low <= high and c.close > high) if side == "long" else (c.high >= low and c.close < low)
            if not retested:
                return []
        price = ctx.last_price(c.symbol) or c.close
        stop = low if side == "long" else high
        if (side == "long" and price <= stop) or (side == "short" and price >= stop):
            self._finish(rec, "price_inside_range")
            return []
        tp = price + p.tp_range_mult * height if side == "long" else price - p.tp_range_mult * height
        self._finish(rec, "traded")
        rec["traded"] = True
        mode = "retest" if p.require_retest else "break"
        return [self.make_entry(
            symbol=c.symbol, side=side, ts=c.close_time, tf="1m", price=price, stop=stop,
            tps_price=[(tp, 1.0)], valid_bars=2, reason=f"ORB {side} {mode} of {rec['date']} range",
            meta={"range_high": high, "range_low": low, "range_height": height, "range_bars": rec["bars"],
                  "range_vol_avg": rec["vol_avg"], "break_volume_ratio": rec["break_volume_ratio"],
                  "retest": p.require_retest, "session": rec["session"], "hourly": bool(p.hourly_orb)})]

    def state(self) -> dict[str, Any]:
        return {"ranges": {s: dict(r) for s, r in self._ranges.items()}, "counters": dict(self._counters)}

    def reset(self) -> None:
        self._ranges.clear()
        self._counters.clear()
