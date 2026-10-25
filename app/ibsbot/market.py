"""Alpaca market data for the IBS bot: the session calendar, QQQ's session so far, and the official daily candles.
GET requests only; works with either Alpaca key set (paper or live).

Alpaca's free data plan serves the consolidated SIP feed only with a 15-minute delay (a request whose end lies within
15 minutes of now is refused) and IEX in real time. IEX alone sees a few % of the volume, so its high and low can miss
the session's real extremes. The estimate two minutes before the close therefore takes SIP minute bars up to 16 minutes
ago, IEX minute bars for the rest, and the latest IEX trade as the close; with a paid plan (IBS_BOT_FEED=sip) it is SIP
throughout. The official record waits until the SIP daily candle is available (17 minutes after the close).
"""
from __future__ import annotations

import math
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .rule import Session

ET = ZoneInfo("America/New_York")
SIP_DELAY = timedelta(minutes=16)


class DataError(RuntimeError):
    pass


def stamp(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _px(v: Any) -> float:
    x = float(v)
    if not math.isfinite(x) or x <= 0:
        raise DataError(f"invalid price {v!r}")
    return x


async def calendar(c: Any, today: date) -> dict[str, dict[str, datetime]]:
    """Trading sessions from 15 days back to 10 ahead: date -> {open, close} (aware datetimes; early closes included)."""
    q = urllib.parse.urlencode({"start": str(today - timedelta(days=15)), "end": str(today + timedelta(days=10))})
    out = {}
    for r in await c.request("GET", "/v2/calendar?" + q):
        d = date.fromisoformat(r["date"])
        at = lambda hm: datetime.combine(d, datetime.strptime(hm, "%H:%M").time(), ET)  # noqa: E731
        out[r["date"]] = {"open": at(r["open"]), "close": at(r["close"])}
    if not out:
        raise DataError("Alpaca returned no market calendar")
    return out


async def bars(c: Any, symbols: list[str], timeframe: str, start: datetime, end: datetime, feed: str,
               adjustment: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
    if end <= start:
        return out
    token, seen = None, set()
    while True:
        q = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": iso(start), "end": iso(end), "feed": feed,
             "adjustment": adjustment, "limit": 10000, "sort": "asc"}
        if token:
            q["page_token"] = token
        d = await c.request("GET", "/v2/stocks/bars?" + urllib.parse.urlencode(q), data=True)
        for s, rows in (d.get("bars") or {}).items():
            if s in out:
                out[s] += rows
        token = d.get("next_page_token")
        if not token:
            return out
        if token in seen:
            raise DataError("repeated Alpaca page token")
        seen.add(token)


async def latest(c: Any, symbols: list[str], feed: str, now: datetime, max_age_s: float = 120) -> dict[str, float]:
    """The latest trade per symbol; refuses a stale one (a halted or quiet feed must not set the close)."""
    q = urllib.parse.urlencode({"symbols": ",".join(symbols), "feed": feed})
    trades = (await c.request("GET", "/v2/stocks/trades/latest?" + q, data=True)).get("trades") or {}
    out = {}
    for s in symbols:
        t = trades.get(s) or {}
        if not t.get("t") or abs((now - stamp(t["t"])).total_seconds()) > max_age_s:
            raise DataError(f"no fresh {feed.upper()} trade for {s}")
        out[s] = _px(t.get("p"))
    return out


def _session_day(sessions: dict[str, dict[str, datetime]], day: str, back: int) -> str:
    days = sorted(d for d in sessions if d < day) if back else []
    if len(days) < back:
        raise DataError(f"no session {back} before {day} in the calendar")
    return days[-back]


async def daily(c: Any, symbols: list[str], sessions: dict[str, dict[str, datetime]], day: str,
                now: datetime) -> dict[str, dict[str, dict[str, float]]]:
    """SIP daily candles (split/dividend adjusted as of now) for `day` and the session before it: sym -> date -> ohlc."""
    prev = _session_day(sessions, day, 1)
    start = sessions[prev]["open"] - timedelta(hours=12)
    end = min(now - SIP_DELAY, datetime.fromisoformat(day).replace(tzinfo=ET) + timedelta(days=1))
    raw = await bars(c, symbols, "1Day", start, end, "sip", "all")
    out: dict[str, dict[str, dict[str, float]]] = {}
    for s, rows in raw.items():
        out[s] = {stamp(r["t"]).astimezone(ET).date().isoformat(): {k: _px(r[k]) for k in ("o", "h", "l", "c")} for r in rows}
    return out


async def prev_high(c: Any, sessions: dict[str, dict[str, datetime]], day: str) -> float:
    prev = _session_day(sessions, day, 1)
    raw = await bars(c, ["QQQ"], "1Day", sessions[prev]["open"] - timedelta(hours=12), sessions[day]["open"], "sip", "all")
    rows = {stamp(r["t"]).astimezone(ET).date().isoformat(): r for r in raw["QQQ"]}
    if prev not in rows:
        raise DataError(f"no QQQ daily candle for the previous session {prev}")
    return _px(rows[prev]["h"])


async def estimate(c: Any, sessions: dict[str, dict[str, datetime]], day: str, now: datetime, feed: str) -> tuple[Session, dict[str, Any]]:
    """QQQ's session so far, two minutes before the close, with the latest trade as the close; plus TQQQ's last price."""
    s = sessions[day]
    split = now if feed == "sip" else max(s["open"], now - SIP_DELAY)
    parts = await bars(c, ["QQQ"], "1Min", s["open"], split, "sip", "raw")
    if feed != "sip":
        tail = await bars(c, ["QQQ"], "1Min", max(s["open"], split - timedelta(minutes=1)), now, "iex", "raw")
        parts["QQQ"] += tail["QQQ"]
    rows = parts["QQQ"]
    if len(rows) < 30:
        raise DataError(f"only {len(rows)} QQQ minute bars for {day}")
    last = await latest(c, ["QQQ", "TQQQ"], feed, now)
    high = max([_px(r["h"]) for r in rows] + [last["QQQ"]])
    low = min([_px(r["l"]) for r in rows] + [last["QQQ"]])
    ph = await prev_high(c, sessions, day)
    return Session(high=high, low=low, last=last["QQQ"], prev_high=ph), {"tqqq_last": last["TQQQ"], "minute_bars": len(rows),
                                                                         "feed": "SIP" if feed == "sip" else "SIP (to 16 min ago) + IEX"}
