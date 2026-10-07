"""MarketFeed: one combined websocket for klines / mark price / depth10 / aggTrade, plus the user stream.

Reconnects with jittered backoff (1..30 s), proactively before Binance's 24 h limit, and after 30 s of
silence. Gap filling after a reconnect is done by the engine (it owns the candle stores) when it sees
`FeedStatus(connected=True)`. The host is asserted against the venue allowlist BEFORE connecting.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.config import Venue, host_of
from app.core.types import (BookEvent, BookSnapshot, Candle, CandleClosed, CandleForming, FeedStatus, MarkEvent,
                            MarkPrice, Tick, TradeEvent, UserEvent)

log = logging.getLogger("paperlab.feed")


class FeedHostBlocked(RuntimeError):
    pass


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class MarketFeed:
    def __init__(self, venue: Venue, symbols: list[str], tfs: tuple[str, ...], queue: asyncio.Queue,
                 client: Any | None = None, user_stream: bool = False, silence_timeout_s: float = 30.0,
                 max_connection_s: float = 23 * 3600):
        self.venue = venue
        self.symbols = list(symbols)
        self.tfs = tuple(tfs)
        self.queue = queue
        self.client = client
        # Bybit's private socket authenticates with the API key itself (no listenKey); that path is not
        # implemented yet, so the user stream stays off there and the 10 s position poll covers it.
        self.user_stream = (user_stream and client is not None and venue.caps.user_stream
                            and venue.exchange != "bybit")
        self.silence_timeout_s = silence_timeout_s
        self.max_connection_s = max_connection_s
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        self.market = {"connected": False, "last_msg_ts": 0, "reconnects": 0, "detail": "not started", "messages": 0}
        self.user = {"connected": False, "last_msg_ts": 0, "reconnects": 0, "detail": "disabled", "messages": 0}
        self._forming: dict[tuple[str, str], Candle] = {}
        self.bybit = venue.exchange == "bybit"
        # Bybit orderbook.N is snapshot-then-delta, so the book has to be kept per symbol and patched.
        self._books: dict[str, dict[str, dict[float, float]]] = {}

    # -- lifecycle -------------------------------------------------------------------------
    def stream_names(self) -> list[str]:
        names: list[str] = []
        for sym in self.symbols:
            s = sym.lower()
            names += [f"{s}@kline_{tf}" for tf in self.tfs]
            if self.venue.caps.mark_price:
                names.append(f"{s}@markPrice@1s")
            names.append(f"{s}@depth10@100ms")
            names.append(f"{s}@aggTrade")
        return names

    # Bybit kline topics take bare minutes, not "1m".
    BYBIT_TF = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D"}

    def bybit_topics(self) -> list[str]:
        topics: list[str] = []
        for sym in self.symbols:
            topics += [f"kline.{self.BYBIT_TF[tf]}.{sym}" for tf in self.tfs if tf in self.BYBIT_TF]
            topics += [f"tickers.{sym}", f"orderbook.50.{sym}", f"publicTrade.{sym}"]
        return topics

    def market_url(self) -> str:
        if self.bybit:
            return self.venue.ws_base            # bybit subscribes after connecting, no query string
        return f"{self.venue.ws_base.rstrip('/')}/stream?streams={'/'.join(self.stream_names())}"

    def _assert_host(self, url: str) -> None:
        host = host_of(url)
        if host not in self.venue.allowed_hosts:
            raise FeedHostBlocked(f"refusing websocket to {host!r}; allowed {sorted(self.venue.allowed_hosts)}")

    async def start(self) -> None:
        self._stopping = False
        self._tasks.append(asyncio.create_task(self._market_loop(), name="feed-market"))
        if self.user_stream:
            self.user["detail"] = "starting"
            self._tasks.append(asyncio.create_task(self._user_loop(), name="feed-user"))
        else:
            self.user["detail"] = ("bybit: no private stream yet (position poll covers it)" if self.bybit
                                   else "dry_run" if self.client is None else "disabled")

    async def stop(self) -> None:
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        self.market["connected"] = False

    def status(self) -> dict[str, Any]:
        now = time.time()

        def block(d: dict[str, Any]) -> dict[str, Any]:
            age = (now - d["last_msg_ts"]) if d["last_msg_ts"] else None
            return {"connected": d["connected"], "last_msg_age_s": None if age is None else round(age, 1),
                    "reconnects": d["reconnects"], "detail": d["detail"], "messages": d["messages"]}
        return {"market": block(self.market), "user": block(self.user)}

    async def _emit(self, ev: Any) -> None:
        await self.queue.put(ev)

    # -- market stream --------------------------------------------------------------
    async def _market_loop(self) -> None:
        url = self.market_url()
        self._assert_host(url)
        backoff = 1.0
        while not self._stopping:
            opened = time.monotonic()
            try:
                async with connect(url, ping_interval=20, ping_timeout=20, open_timeout=15, max_size=2 ** 22,
                                   max_queue=4096) as ws:
                    if self.bybit:
                        self._books.clear()      # a reconnect invalidates every delta-built book
                        await ws.send(json.dumps({"op": "subscribe", "args": self.bybit_topics()}))
                    self.market.update(connected=True, detail="connected", last_msg_ts=time.time())
                    await self._emit(FeedStatus("market", True, f"connected ({self.market['reconnects']} reconnects)",
                                                int(time.time() * 1000)))
                    backoff = 1.0
                    while not self._stopping:
                        if time.monotonic() - opened > self.max_connection_s:
                            self.market["detail"] = "proactive reconnect (24h limit)"
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=self.silence_timeout_s)
                        except asyncio.TimeoutError:
                            self.market["detail"] = "silence watchdog"
                            break
                        self.market["last_msg_ts"] = time.time()
                        self.market["messages"] += 1
                        try:
                            await self._handle_market(json.loads(raw))
                        except Exception as exc:  # never let a bad frame kill the feed
                            log.warning("market frame error: %s", str(exc)[:160])
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
                self.market["detail"] = f"{exc.__class__.__name__}: {str(exc)[:120]}"
            except Exception as exc:
                self.market["detail"] = f"{exc.__class__.__name__}: {str(exc)[:120]}"
                log.exception("market feed error")
            if self._stopping:
                break
            self.market["connected"] = False
            self.market["reconnects"] += 1
            await self._emit(FeedStatus("market", False, self.market["detail"], int(time.time() * 1000)))
            wait = min(30.0, backoff) * random.uniform(0.8, 1.2)
            log.warning("market feed disconnected (%s); reconnecting in %.1fs", self.market["detail"], wait)
            await asyncio.sleep(wait)
            backoff = min(30.0, backoff * 2)

    async def _handle_market(self, msg: dict[str, Any]) -> None:
        if self.bybit or "topic" in msg:
            await self._handle_bybit(msg)
            return
        stream = str(msg.get("stream", ""))
        data = msg.get("data") or msg
        etype = data.get("e")
        if etype == "kline":
            await self._handle_kline(data)
        elif etype == "aggTrade":
            await self._emit(TradeEvent(Tick(str(data["s"]), _f(data["p"]), _f(data["q"]), int(data.get("T") or data.get("E") or 0),
                                             bool(data.get("m")))))
        elif etype == "markPriceUpdate":
            await self._emit(MarkEvent(MarkPrice(str(data["s"]), _f(data["p"]), _f(data.get("i")), _f(data.get("r")),
                                                 int(_f(data.get("T"))), int(_f(data.get("E"))))))
        elif etype == "depthUpdate" or "bids" in data:
            sym = str(data.get("s") or stream.split("@")[0].upper())
            bids = data.get("b") if "b" in data else data.get("bids", [])
            asks = data.get("a") if "a" in data else data.get("asks", [])
            book = BookSnapshot(sym, [(_f(p), _f(q)) for p, q in bids[:10]], [(_f(p), _f(q)) for p, q in asks[:10]],
                                int(_f(data.get("E")) or time.time() * 1000))
            await self._emit(BookEvent(book))

    # -- bybit v5 frames ---------------------------------------------------------------
    async def _handle_bybit(self, msg: dict[str, Any]) -> None:
        topic = str(msg.get("topic", ""))
        if not topic:
            if msg.get("op") == "subscribe" and not msg.get("success", True):
                log.warning("bybit subscribe rejected: %s", str(msg)[:200])
            return                                    # pong / subscribe ack
        data = msg.get("data")
        head = topic.split(".")
        kind, sym = head[0], head[-1]
        if kind == "kline":
            inv = {v: k for k, v in self.BYBIT_TF.items()}
            tf = inv.get(head[1], head[1])
            for k in (data or []):
                await self._push_candle(Candle(
                    sym, tf, int(_f(k.get("start"))), _f(k.get("open")), _f(k.get("high")), _f(k.get("low")),
                    _f(k.get("close")), _f(k.get("volume")), int(_f(k.get("end"))) + 1,
                    bool(k.get("confirm")), _f(k.get("turnover")), 0, "live"))
        elif kind == "publicTrade":
            for t in (data or []):
                # Bybit's S is the AGGRESSOR side; Binance's `m` is "buyer is maker", i.e. a sell-side hit.
                await self._emit(TradeEvent(Tick(str(t.get("s", sym)), _f(t.get("p")), _f(t.get("v")),
                                                 int(_f(t.get("T"))), str(t.get("S")) == "Sell")))
        elif kind == "tickers" and isinstance(data, dict):
            mark = _f(data.get("markPrice"))
            if mark:
                await self._emit(MarkEvent(MarkPrice(
                    str(data.get("symbol", sym)), mark, _f(data.get("indexPrice")), _f(data.get("fundingRate")),
                    int(_f(data.get("nextFundingTime"))), int(_f(msg.get("ts"))))))
        elif kind == "orderbook" and isinstance(data, dict):
            await self._emit_bybit_book(msg, data, sym)

    async def _emit_bybit_book(self, msg: dict[str, Any], data: dict[str, Any], sym: str) -> None:
        """Rebuild the book from snapshot + deltas. A zero size deletes the level."""
        side = self._books.setdefault(sym, {"b": {}, "a": {}})
        if str(msg.get("type")) == "snapshot":
            side["b"].clear()
            side["a"].clear()
        for key in ("b", "a"):
            for level in data.get(key, []) or []:
                price, size = _f(level[0]), _f(level[1])
                if size <= 0:
                    side[key].pop(price, None)
                else:
                    side[key][price] = size
        if not side["b"] or not side["a"]:
            return
        bids = sorted(side["b"].items(), key=lambda x: -x[0])[:10]
        asks = sorted(side["a"].items(), key=lambda x: x[0])[:10]
        await self._emit(BookEvent(BookSnapshot(sym, bids, asks,
                                                int(_f(msg.get("ts")) or time.time() * 1000))))

    async def _push_candle(self, candle: Candle) -> None:
        """Shared forming/closed bookkeeping (a skipped close is synthesised from the last update)."""
        key = (candle.symbol, candle.tf)
        prev = self._forming.get(key)
        if prev is not None and prev.open_time < candle.open_time and not prev.closed:
            prev.closed = True
            await self._emit(CandleClosed(prev))
        if candle.closed:
            self._forming.pop(key, None)
            await self._emit(CandleClosed(candle))
        else:
            self._forming[key] = candle
            await self._emit(CandleForming(candle))

    async def _handle_kline(self, data: dict[str, Any]) -> None:
        k = data["k"]
        sym, tf = str(k["s"]), str(k["i"])
        candle = Candle(sym, tf, int(k["t"]), _f(k["o"]), _f(k["h"]), _f(k["l"]), _f(k["c"]), _f(k["v"]), int(k["T"]),
                        bool(k.get("x")), _f(k.get("q")), int(_f(k.get("n"))), "live")
        key = (sym, tf)
        prev = self._forming.get(key)
        if prev is not None and prev.open_time < candle.open_time and not prev.closed:
            prev.closed = True  # the close frame was skipped: synthesise it from the last update we saw
            await self._emit(CandleClosed(prev))
        if candle.closed:
            self._forming.pop(key, None)
            await self._emit(CandleClosed(candle))
        else:
            self._forming[key] = candle
            await self._emit(CandleForming(candle))

    # -- user stream ------------------------------------------------------------------
    async def _user_loop(self) -> None:
        backoff = 1.0
        while not self._stopping:
            key: str | None = None
            try:
                key = await self.client.create_listen_key()
                url = f"{self.venue.ws_base.rstrip('/')}/ws/{key}"
                self._assert_host(url)
                keepalive_task = asyncio.create_task(self._keepalive(key))
                try:
                    async with connect(url, ping_interval=20, ping_timeout=20, open_timeout=15, max_size=2 ** 20) as ws:
                        self.user.update(connected=True, detail="connected", last_msg_ts=time.time())
                        await self._emit(FeedStatus("user", True, "connected", int(time.time() * 1000)))
                        backoff = 1.0
                        opened = time.monotonic()
                        while not self._stopping:
                            if time.monotonic() - opened > self.max_connection_s:
                                break
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=self.silence_timeout_s * 10)
                            except asyncio.TimeoutError:
                                continue  # user streams are quiet; websockets pings keep it alive
                            self.user["last_msg_ts"] = time.time()
                            self.user["messages"] += 1
                            data = json.loads(raw)
                            kind = str(data.get("e", "unknown"))
                            if kind == "listenKeyExpired":
                                self.user["detail"] = "listenKey expired; re-keying"
                                break
                            await self._emit(UserEvent(kind, data))
                finally:
                    keepalive_task.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.user["detail"] = f"{exc.__class__.__name__}: {str(exc)[:120]}"
                log.warning("user stream error: %s", self.user["detail"])
            if self._stopping:
                break
            self.user["connected"] = False
            self.user["reconnects"] += 1
            await self._emit(FeedStatus("user", False, self.user["detail"], int(time.time() * 1000)))
            await asyncio.sleep(min(30.0, backoff) * random.uniform(0.8, 1.2))
            backoff = min(30.0, backoff * 2)

    async def _keepalive(self, key: str) -> None:
        while not self._stopping:
            await asyncio.sleep(30 * 60)
            try:
                await self.client.keepalive_listen_key(key)
            except Exception as exc:
                log.warning("listenKey keepalive failed: %s", str(exc)[:120])
