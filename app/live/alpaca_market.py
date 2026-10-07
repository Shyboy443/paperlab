"""Live US-stock market data for the V9 STOCKS arena (docs/V9_PROTOCOL.md): Alpaca's free IEX feed, regular sessions
only. Read-only -- nothing here can place an order -- but Alpaca's market data needs the account's keys, which arrive
from the encrypted key vault (or Railway variables) and are only ever sent in the WebSocket auth message and the REST
headers.

    sessions   Alpaca's trading calendar (holidays and half days included); bars outside the regular session are
               dropped, so the bots only ever see 09:30-16:00 ET
    bars       closed 1m bars from the IEX stream (REST for warm-up and gap repair). IEX prints only when it trades: a
               session minute with no IEX bar 15 s after it ends is filled FLAT at the last close (volume 0), so every
               5m / 15m / 1h candle closes on time. Flat fills are stored like real bars, so a restart re-derives the
               same books
    quotes     market-order costs use IEX half-spread capped at 1 bp (2026-10-02).
               This cap is a model assumption, not observed consolidated NBBO routing.
               It may understate or overstate executable costs. Tight IEX quotes are
               kept; resting stock limit targets use their separate fill model.
    fallback   Alpaca's free plan allows ONE market-data stream per account. While the stream is refused (406: another
               app holds it) or down, the feed polls REST every 10 s for new minute bars and the latest quotes -- the
               same bars, a few seconds later -- and keeps retrying the stream

Subscribers get a queue.Queue of closed 1m Candles per symbol, in order, exactly like the Bybit feed.
"""
from __future__ import annotations

import asyncio
import bisect
import json
import logging
import queue
import random
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.config import ALPACA_DATA_WS_IEX
from app.core.types import Candle

log = logging.getLogger("paperlab.live.alpaca")

ET = ZoneInfo("America/New_York")
MINUTE = 60_000
FILL_GRACE_MS = 15_000
REST_FILL_GRACE_MS = 20_000              # polled bars arrive a little later than streamed ones
POLL_EVERY_S = 10.0
POLL_FRESH_S = 30.0
STALE_S = 120
QUOTE_MAX_AGE_MS = 5 * MINUTE
MAX_HALF_SPREAD_BPS = 1.0
CALENDAR_REFRESH_S = 6 * 3600


def parse_ts(s: str) -> int:
    """Alpaca RFC 3339 (nanoseconds allowed, 'Z') -> epoch ms."""
    s = s.strip().rstrip("Z")
    if "." in s:
        base, frac = s.split(".", 1)
        s = base + "." + frac[:6]
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Sessions:
    """Regular trading sessions [open_ms, close_ms) in UTC ms."""

    def __init__(self, intervals: Sequence[tuple[int, int]] = ()):
        self.iv = sorted((int(a), int(b)) for a, b in intervals if int(b) > int(a))
        self._opens = [a for a, _ in self.iv]

    @classmethod
    def from_calendar(cls, rows: Sequence[Mapping[str, Any]]) -> "Sessions":
        out = []
        for r in rows:
            d = date.fromisoformat(str(r["date"]))
            o = datetime.combine(d, datetime.strptime(str(r["open"]), "%H:%M").time(), ET)
            c = datetime.combine(d, datetime.strptime(str(r["close"]), "%H:%M").time(), ET)
            out.append((int(o.timestamp() * 1000), int(c.timestamp() * 1000)))
        return cls(out)

    def at(self, t: int) -> tuple[int, int] | None:
        i = bisect.bisect_right(self._opens, int(t)) - 1
        if i >= 0 and self.iv[i][0] <= int(t) < self.iv[i][1]:
            return self.iv[i]
        return None

    def is_open(self, t: int) -> bool:
        return self.at(t) is not None

    def next_open(self, t: int) -> int | None:
        i = bisect.bisect_right(self._opens, int(t))
        return self.iv[i][0] if i < len(self.iv) else None

    def last_closed(self, t: int) -> tuple[int, int] | None:
        done = [s for s in self.iv if s[1] <= int(t)]
        return done[-1] if done else None

    def closed_between(self, a: int, b: int) -> int:
        return sum(1 for o, c in self.iv if c > int(a) and c <= int(b) and o >= int(a))


def flat(symbol: str, open_ms: int, px: float) -> Candle:
    return Candle(symbol, "1m", open_ms, px, px, px, px, 0.0, open_ms + MINUTE - 1, True, 0.0, 0, "filled", 0.0)


def bar_candle(symbol: str, b: Mapping[str, Any], source: str) -> Candle:
    o = parse_ts(b["t"])
    return Candle(symbol, "1m", o, float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]), float(b.get("v") or 0.0),
                  o + MINUTE - 1, True, float(b.get("v") or 0.0) * float(b.get("vw") or b["c"]), int(b.get("n") or 0),
                  source, 0.0)


def session_tape(symbol: str, bars: Sequence[Candle], sessions: Sessions, last_close: float | None = None,
                 until_ms: int | None = None, last_open_ms: int | None = None) -> list[Candle]:
    """Regular-session 1m tape: bars outside a session dropped, silent session minutes filled flat at the last close
    (never across a session boundary, never past the last real bar or `until_ms`)."""
    out: list[Candle] = []
    prev_open: int | None = last_open_ms
    for b in sorted(bars, key=lambda x: x.open_time):
        s = sessions.at(b.open_time)
        if s is None or (prev_open is not None and b.open_time <= prev_open):
            continue
        # A prior-session close is not a traded price at today's open. Wait for
        # the first actual bar before filling silent minutes in this session.
        same_session = prev_open is not None and s[0] <= prev_open < s[1]
        start = prev_open + MINUTE if same_session else b.open_time
        if last_close is not None and same_session:
            for m in range(start, b.open_time, MINUTE):
                out.append(flat(symbol, m, last_close))
        out.append(b)
        prev_open, last_close = b.open_time, b.close
    if until_ms is not None and prev_open is not None and last_close is not None:
        s = sessions.at(prev_open)
        if s is not None:
            for m in range(prev_open + MINUTE, min(s[1], int(until_ms)), MINUTE):
                if m + MINUTE <= until_ms:
                    out.append(flat(symbol, m, last_close))
    return out


class AlpacaLiveMarket:
    """Owns one asyncio loop in its own thread. Subscribers get a queue.Queue of Candles per symbol."""

    def __init__(self, symbols: Sequence[str], client: Any, store: Callable[[str, dict[str, Any]], None],
                 warmup_from_ms: int, preload: Mapping[str, Sequence[Candle]] | None = None,
                 quotes: Mapping[str, Sequence[tuple[int, float]]] | None = None, sessions: Sessions | None = None,
                 connect_fn: Any = None, clock: Callable[[], float] = time.time, ws_url: str = ALPACA_DATA_WS_IEX):
        self.traded = list(dict.fromkeys(symbols))
        self.client = client                       # AlpacaClient: REST data + calendar (carries the keys)
        self.store = store
        self.warmup_from_ms = int(warmup_from_ms)
        self.preload = {k: list(v) for k, v in (preload or {}).items()}
        self.sessions = sessions
        self.connect_fn = connect_fn
        self.clock = clock
        self.ws_url = ws_url
        self.subs: dict[str, list[queue.Queue]] = {s: [] for s in self.traded}
        self.delivered: dict[str, int] = {s: 0 for s in self.traded}
        self.last_close: dict[str, float | None] = {s: None for s in self.traded}
        self.tickers: dict[str, dict[str, Any]] = {}
        self._q_ts: dict[str, list[int]] = {s: [] for s in self.traded}
        self._q_hs: dict[str, list[float]] = {s: [] for s in self.traded}
        for sym, rows in (quotes or {}).items():
            for ts, hs in sorted(rows):
                self._add_quote(sym, int(ts), float(hs))
        self.warm = False
        self.stats: dict[str, Any] = {"ws": {"connected": False, "reconnects": 0, "last_msg_ts": 0.0, "detail": "not started"},
                                      "last_bar_ts": {}, "last_quote_ts": {}, "bars_live": 0, "bars_backfill": 0,
                                      "bars_preloaded": 0, "bars_filled": 0, "bars_late": 0, "gaps_repaired": 0,
                                      "rest_calls": 0, "rest_errors": 0, "calendar_ts": 0.0,
                                      "rest_mode": False, "last_poll_ok": 0.0, "last_poll": 0.0}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._inbox: asyncio.Queue | None = None
        self._fetch_ok = False

    # -- consumers ------------------------------------------------------------------------------------------
    def subscribe(self, symbol: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        self.subs.setdefault(symbol, []).append(q)
        return q

    def mid(self, symbol: str) -> float | None:
        t = self.tickers.get(symbol) or {}
        b, a = t.get("bid"), t.get("ask")
        if b and a and 0 < b <= a:
            return (b + a) / 2.0
        return self.last_close.get(symbol)

    def live_half_spread_bps(self, symbol: str) -> float | None:
        t = self.tickers.get(symbol) or {}
        b, a = t.get("bid"), t.get("ask")
        if not b or not a or b <= 0 or a < b:
            return None
        return min(MAX_HALF_SPREAD_BPS, (a - b) / 2.0 / ((a + b) / 2.0) * 1e4)

    def spread_for_jev(self, symbol: str) -> tuple[float | None, str]:
        hs = self.live_half_spread_bps(symbol)
        return (round(hs, 4), "observed") if hs is not None else (None, "modelled")

    def _add_quote(self, sym: str, ts: int, hs: float) -> None:
        ts_list, hs_list = self._q_ts.setdefault(sym, []), self._q_hs.setdefault(sym, [])
        i = bisect.bisect_left(ts_list, ts)
        if i < len(ts_list) and ts_list[i] == ts:
            hs_list[i] = hs
            return
        ts_list.insert(i, ts)
        hs_list.insert(i, hs)

    def quote_half_spread(self, symbol: str, ts: int) -> float | None:
        ts_list = self._q_ts.get(symbol) or []
        i = bisect.bisect_right(ts_list, int(ts))
        if not i or int(ts) - ts_list[i - 1] > QUOTE_MAX_AGE_MS:
            return None
        return self._q_hs[symbol][i - 1]

    def market_state(self) -> dict[str, Any]:
        now = int(self.clock() * 1000)
        s = self.sessions.at(now) if self.sessions else None
        return {"open": s is not None, "close_ms": s[1] if s else None,
                "next_open_ms": (self.sessions.next_open(now) if self.sessions else None)}

    def data_ok(self) -> bool:
        """Is live data flowing: the stream is up, or the REST fallback polled successfully just now?"""
        return bool(self.stats["ws"]["connected"]) or self.clock() - self.stats["last_poll_ok"] <= POLL_FRESH_S

    def stream_ok(self, symbol: str) -> tuple[bool, str | None]:
        """Fresh enough to decide on: data is flowing and the symbol printed a bar or a quote recently."""
        now = self.clock()
        if not self.data_ok():
            return False, "no market data (stream refused or down, REST polling failing)"
        last = max(self.stats["last_bar_ts"].get(symbol) or 0, self.stats["last_quote_ts"].get(symbol) or 0)
        if not last or now - last > STALE_S:
            return False, "no IEX bar or quote for %ss" % (int(now - last) if last else "ever")
        return True, None

    def health(self) -> dict[str, Any]:
        now = self.clock()
        ws = self.stats["ws"]
        lb = [v for v in self.stats["last_bar_ts"].values() if v]
        return {"ws": {"connected": ws["connected"], "reconnects": ws["reconnects"], "detail": ws["detail"],
                       "age_s": round(now - ws["last_msg_ts"], 1) if ws["last_msg_ts"] else None},
                "source": "stream" if ws["connected"] else ("rest polling" if self.data_ok() else "none"),
                "bars_age_s": round(now - max(lb), 1) if lb else None, "warm": int(self.warm) * len(self.traded),
                "symbols": len(self.traded), "session": self.market_state(),
                **{k: self.stats[k] for k in ("bars_live", "bars_backfill", "bars_preloaded", "bars_filled", "bars_late",
                                              "gaps_repaired", "rest_calls", "rest_errors")}}

    def ready(self) -> bool:
        return self.warm and self.data_ok()

    # -- lifecycle ------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="alpaca-market", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception:
            log.exception("alpaca market loop died")
            self.stats["rest_errors"] += 1

    async def _main(self) -> None:
        self._inbox = asyncio.Queue()
        tasks = [asyncio.create_task(self._ws_loop(self._inbox))]
        try:
            while self.sessions is None and not self._stop.is_set():
                await self._refresh_calendar()
                if self.sessions is None:
                    await asyncio.sleep(10)
            for sym in self.traded:
                for bar in self.preload.get(sym, []):
                    if self._deliver(bar, "stored"):
                        self.stats["bars_preloaded"] += 1
                self.preload[sym] = []
            await self._catch_up(self.traded, "backfill")
            self.warm = True
            last_tick = 0.0
            while not self._stop.is_set():
                try:
                    item = await asyncio.wait_for(self._inbox.get(), timeout=1.0)
                    await self._deliver_live(*item)
                except asyncio.TimeoutError:
                    pass
                if time.monotonic() - last_tick >= 5.0:
                    last_tick = time.monotonic()
                    await self._tick()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- REST -----------------------------------------------------------------------------------------------
    async def _get(self, path: str, data: bool) -> Any:
        self.stats["rest_calls"] += 1
        try:
            return await self.client.request("GET", path, data=data)
        except Exception as exc:
            self.stats["rest_errors"] += 1
            log.warning("alpaca REST failed: %s", str(exc)[:160])
            return None

    async def _refresh_calendar(self) -> None:
        now = int(self.clock() * 1000)
        day = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()  # noqa: E731
        a = day(min(self.warmup_from_ms, now)) - timedelta(days=3)
        b = day(now) + timedelta(days=21)
        rows = await self._get(f"/v2/calendar?start={a.isoformat()}&end={b.isoformat()}", data=False)
        if isinstance(rows, list) and rows:
            self.sessions = Sessions.from_calendar(rows)
            self.stats["calendar_ts"] = self.clock()

    async def fetch_bars(self, symbols: Sequence[str], start_ms: int, end_ms: int) -> dict[str, list[Candle]]:
        """Every 1m IEX bar in [start, end) for the symbols; `self._fetch_ok` says whether every page arrived."""
        out: dict[str, list[Candle]] = {s: [] for s in symbols}
        token = None
        self._fetch_ok = False
        for _ in range(200):
            q = {"symbols": ",".join(symbols), "timeframe": "1Min", "start": iso(start_ms), "end": iso(end_ms),
                 "limit": 10000, "feed": "iex", "adjustment": "raw", "sort": "asc"}
            if token:
                q["page_token"] = token
            page = await self._get("/v2/stocks/bars?" + urllib.parse.urlencode(q), data=True)
            if not isinstance(page, dict):
                break
            for sym, rows in (page.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bar_candle(sym, r, "backfill") for r in rows or [])
            token = page.get("next_page_token")
            if not token:
                self._fetch_ok = True
                break
        return out

    async def _catch_up(self, symbols: Sequence[str], source: str) -> bool:
        """REST bars from each symbol's last delivered minute to now, as a regular-session tape. True when every page
        arrived."""
        now = int(self.clock() * 1000)
        start = min((self.delivered.get(s) or self.warmup_from_ms - MINUTE) + MINUTE for s in symbols)
        fetched = await self.fetch_bars(symbols, start, now)
        for sym in symbols:
            done = [b for b in fetched.get(sym, []) if b.open_time + MINUTE <= now]      # never a minute still printing
            tape = session_tape(sym, done, self.sessions, self.last_close.get(sym),
                                last_open_ms=self.delivered.get(sym) or None)
            for bar in tape:
                if self._deliver(bar, "filled" if bar.source == "filled" else source):
                    self.stats["bars_filled" if bar.source == "filled" else
                               ("bars_live" if source == "live" else "bars_backfill")] += 1
                    if source == "live" and bar.source != "filled":
                        self.stats["last_bar_ts"][sym] = self.clock()
        return self._fetch_ok

    async def _poll(self) -> None:
        """The REST fallback: new minute bars and the latest IEX quotes for every symbol (two calls)."""
        self.stats["last_poll"] = self.clock()
        self.stats["rest_mode"] = True
        ok = await self._catch_up(self.traded, "live")
        page = await self._get("/v2/stocks/quotes/latest?" + urllib.parse.urlencode(
            {"symbols": ",".join(self.traded), "feed": "iex"}), data=True)
        if isinstance(page, dict):
            for sym, q in (page.get("quotes") or {}).items():
                self._on_message({"T": "q", "S": sym, "bp": q.get("bp"), "ap": q.get("ap")}, None)
                try:
                    self.stats["last_quote_ts"][sym] = parse_ts(q["t"]) / 1000.0
                except (KeyError, ValueError):
                    pass
        else:
            ok = False
        if ok:
            self.stats["last_poll_ok"] = self.clock()

    # -- delivery -------------------------------------------------------------------------------------------
    def _deliver(self, bar: Candle, source: str) -> bool:
        with self._lock:
            if bar.open_time <= self.delivered.get(bar.symbol, 0):
                return False
            self.delivered[bar.symbol] = bar.open_time
            self.last_close[bar.symbol] = bar.close
        t = self.tickers.get(bar.symbol) or {}
        hs = self.live_half_spread_bps(bar.symbol) if source == "live" else None
        if hs is not None:
            self._add_quote(bar.symbol, bar.close_time, hs)
        if source != "stored":
            self.store("bar", {"symbol": bar.symbol, "open_time": bar.open_time, "open": bar.open, "high": bar.high,
                               "low": bar.low, "close": bar.close, "volume": bar.volume, "turnover": bar.quote_volume,
                               "bid": t.get("bid") if hs is not None else None, "ask": t.get("ask") if hs is not None else None,
                               "half_spread_bps": hs, "source": source})
        for q in self.subs.get(bar.symbol, []):
            q.put(bar)
        return True

    async def _deliver_live(self, bar: Candle, _: Any = None) -> None:
        if self.sessions is None or not self.sessions.is_open(bar.open_time):
            return
        last = self.delivered.get(bar.symbol, 0)
        if bar.open_time <= last:
            self.stats["bars_late"] += 1
            return
        if last and bar.open_time > last + MINUTE:
            s = self.sessions.at(bar.open_time)
            if s is not None and last + MINUTE < s[0]:
                last = s[0] - MINUTE                   # a new session: nothing to repair before its open
            if bar.open_time > last + MINUTE:
                await self._catch_up([bar.symbol], "backfill")
                self.stats["gaps_repaired"] += 1
                px = self.last_close.get(bar.symbol)
                if px is not None and s is not None and s[0] <= self.delivered[bar.symbol] < s[1]:
                    for m in range(max(self.delivered[bar.symbol] + MINUTE, s[0] if s else 0), bar.open_time, MINUTE):
                        if self._deliver(flat(bar.symbol, m, px), "filled"):
                            self.stats["bars_filled"] += 1
        if self._deliver(bar, "live"):
            self.stats["bars_live"] += 1

    async def _tick(self) -> None:
        """Poll REST while the stream is unavailable, fill silent session minutes while data is flowing, refresh the
        calendar now and then."""
        if self.clock() - self.stats["calendar_ts"] > CALENDAR_REFRESH_S:
            await self._refresh_calendar()
        if self.sessions is None:
            return
        ws = self.stats["ws"]
        now = int(self.clock() * 1000)
        streaming = bool(ws["connected"]) and (self.clock() - (ws["last_msg_ts"] or 0) <= 30 or not self.sessions.is_open(now))
        if streaming:
            self.stats["rest_mode"] = False
        elif self.clock() - self.stats["last_poll"] >= POLL_EVERY_S:
            await self._poll()
        polled = self.clock() - self.stats["last_poll_ok"] <= POLL_EVERY_S + 5
        if not (streaming and ws["connected"]) and not polled:
            return
        grace = FILL_GRACE_MS if streaming else REST_FILL_GRACE_MS
        for sym in self.traded:
            px, last = self.last_close.get(sym), self.delivered.get(sym, 0)
            if px is None:
                continue
            m = last + MINUTE
            while m + MINUTE + grace <= now:
                s = self.sessions.at(m)
                if s is None:
                    break  # Never seed a new session from yesterday's price.
                if not (s[0] <= last < s[1]):
                    break
                if self._deliver(flat(sym, m, px), "filled"):
                    self.stats["bars_filled"] += 1
                m += MINUTE

    # -- stream ---------------------------------------------------------------------------------------------
    def _on_message(self, m: Mapping[str, Any], inbox: asyncio.Queue | None) -> None:
        kind, sym = m.get("T"), m.get("S")
        now = self.clock()
        if kind == "b" and sym in self.subs and inbox is not None:
            self.stats["last_bar_ts"][sym] = now
            inbox.put_nowait((bar_candle(sym, m, "live"), None))
        elif kind == "q" and sym:
            t = self.tickers.setdefault(sym, {})
            for src, dst in (("bp", "bid"), ("ap", "ask")):
                if m.get(src) not in (None, ""):
                    t[dst] = float(m[src])
            t["ts"] = now
            self.stats["last_quote_ts"][sym] = now
        elif kind == "error":
            self.stats["ws"]["detail"] = f"error {m.get('code')}: {str(m.get('msg'))[:80]}"

    async def _ws_loop(self, inbox: asyncio.Queue) -> None:
        connect = self.connect_fn
        if connect is None:
            from websockets.asyncio.client import connect
        block = self.stats["ws"]
        backoff = 2.0
        while not self._stop.is_set():
            try:
                async with connect(self.ws_url, open_timeout=15, max_size=2 ** 22, max_queue=4096) as ws:
                    await ws.recv()                                          # [{"T":"success","msg":"connected"}]
                    await ws.send(json.dumps(self.client.stream_auth()))
                    for m in json.loads(await ws.recv()):
                        if m.get("T") == "error":
                            raise RuntimeError(f"auth refused ({m.get('code')}: {m.get('msg')})")
                    await ws.send(json.dumps({"action": "subscribe", "bars": self.traded, "quotes": self.traded}))
                    block.update(connected=True, detail="connected")
                    backoff = 2.0
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        except asyncio.TimeoutError:
                            now_ms = int(self.clock() * 1000)
                            if self.sessions is not None and self.sessions.is_open(now_ms) and \
                                    self.clock() - (block["last_msg_ts"] or self.clock()) > 90:
                                block["detail"] = "silence watchdog"
                                break
                            continue
                        block["last_msg_ts"] = self.clock()
                        try:
                            for m in json.loads(raw):
                                self._on_message(m, inbox)
                        except Exception as exc:
                            log.debug("ws frame error: %s", str(exc)[:120])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                block["detail"] = f"{exc.__class__.__name__}: {str(exc)[:100]}"
            block["connected"] = False
            if self._stop.is_set():
                break
            block["reconnects"] += 1
            # Alpaca's free plan allows ONE stream connection: during a redeploy the old container may still hold it
            wait = min(60.0, backoff) * random.uniform(0.8, 1.2)
            log.warning("alpaca stream down (%s); reconnecting in %.1fs", block["detail"], wait)
            await asyncio.sleep(wait)
            backoff = min(60.0, backoff * 2)
