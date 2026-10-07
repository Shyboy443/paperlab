"""Read-only Bybit ticker fanout for the UI, isolated from execution and storage.

One public socket per server, coalesced to four complete snapshots per second.
Fresh greetings recover clients after dropped packets without replaying old quotes.
Reference: https://bybit-exchange.github.io/docs/v5/websocket/public/ticker
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import random
import time
import uuid
from typing import Any

import websockets

log = logging.getLogger("paperlab.display_prices")
URL = "wss://stream.bybit.com/v5/public/linear"
STALE_MS = 5000
PUBLISH_S = 0.25
FIELDS = {"bid1Price": "bid", "ask1Price": "ask", "lastPrice": "last", "markPrice": "mark",
          "indexPrice": "index", "fundingRate": "funding_rate", "openInterest": "oi",
          "price24hPcnt": "change_24h", "nextFundingTime": "next_funding_ts"}


class DisplayPrices:
    def __init__(self, bus: Any, symbols: list[str], clock=time.time):
        self.bus, self.clock = bus, clock
        self.symbols = sorted(set(symbols))
        self.quotes: dict[str, dict] = {}
        self.stream_id = uuid.uuid4().hex[:12]
        self.seq = 0
        self.connected = False
        self.reconnects = 0
        self.tasks: list[asyncio.Task] = []
        self._ready: set[str] = set()
        self._last_data = 0.0

    def ingest(self, message: dict) -> bool:
        topic = message.get("topic", "")
        symbol = topic.removeprefix("tickers.")
        data = message.get("data")
        if not topic.startswith("tickers.") or symbol not in self.symbols or not isinstance(data, dict):
            return False
        if data.get("symbol", symbol) != symbol:
            return False
        kind = message.get("type")
        if kind not in ("snapshot", "delta") or (kind == "delta" and symbol not in self._ready):
            return False
        try:
            stamp, sequence = int(message["ts"]), int(message["cs"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        now_ms = int(self.clock() * 1000)
        if stamp <= 0 or stamp > now_ms + STALE_MS:
            return False
        old = self.quotes.get(symbol, {}) if symbol in self._ready else {}
        if old and (sequence <= old["exchange_seq"] or stamp < old["exchange_ts"]):
            return False
        quote = {} if kind == "snapshot" else dict(old)
        for source, target in FIELDS.items():
            if source not in data:
                continue  # Bybit deltas omit unchanged fields.
            try:
                value = float(data[source])
            except (ValueError, TypeError):
                return False
            if not math.isfinite(value) or (target in ("bid", "ask", "last", "mark", "index") and value <= 0):
                return False
            quote[target] = value
        bid, ask = quote.get("bid"), quote.get("ask")
        if not bid or not ask or bid > ask:
            return False
        quote.update(mid=(bid + ask) / 2, exchange_ts=stamp, exchange_seq=sequence, received_ts=now_ms)
        quote["half_spread_bps"] = (ask - bid) / (bid + ask) * 10000
        self.quotes[symbol] = quote
        self._ready.add(symbol)
        self._last_data = self.clock()
        return True

    def snapshot(self) -> dict:
        now = int(self.clock() * 1000)
        quotes = {}
        for symbol, row in self.quotes.items():
            age = max(0, now - row["received_ts"], now - row["exchange_ts"])
            quotes[symbol] = {**row, "age_ms": age,
                              "stale": not self.connected or symbol not in self._ready or age > STALE_MS}
        return {"stream_id": self.stream_id, "seq": self.seq, "ts": now, "source": "Bybit public ticker",
                "connected": self.connected, "reconnects": self.reconnects, "stale_after_ms": STALE_MS,
                "symbols": self.symbols, "quotes": quotes, "display_only": True}

    def start(self):
        if not self.tasks:
            self.tasks = [asyncio.create_task(self._listen(), name="display-quotes"),
                          asyncio.create_task(self._publish(), name="display-price-push")]

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.connected = False

    async def _publish(self):
        while True:
            await asyncio.sleep(PUBLISH_S)
            self.seq += 1
            if self.bus.clients:
                self.bus.publish("prices", self.snapshot(), public=True, replay=False)

    async def _heartbeat(self, ws):
        while True:
            await asyncio.sleep(20)
            await ws.send(json.dumps({"op": "ping"}))
            if self.clock() - self._last_data > 30:
                await ws.close()
                return

    async def _listen(self):
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(URL, open_timeout=10, close_timeout=3, ping_interval=20,
                                               ping_timeout=10, max_queue=32) as ws:
                    self._ready.clear()  # Fresh snapshots are required after reconnect.
                    self.connected = True
                    self._last_data = self.clock()
                    await ws.send(json.dumps({"op": "subscribe", "args": [f"tickers.{s}" for s in self.symbols]}))
                    heartbeat = asyncio.create_task(self._heartbeat(ws))
                    try:
                        async for raw in ws:
                            message = json.loads(raw)
                            if message.get("op") == "subscribe" and message.get("success") is False:
                                raise ValueError("ticker subscription refused")
                            if self.ingest(message):
                                backoff = 1.0
                    finally:
                        heartbeat.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await heartbeat
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("display ticker reconnect: %s", type(exc).__name__)
            finally:
                self.connected = False
                self._ready.clear()
            self.reconnects += 1
            await asyncio.sleep(backoff + random.uniform(0, 0.5))
            backoff = min(30.0, backoff * 2)
