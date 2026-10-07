"""Live Binance USD-M market data for the live shadow: closed 1m klines, best bid/ask, funding.

Public, unauthenticated, read-only endpoints (constants in app/config.py). There is no key, no signed
request and no order endpoint anywhere in this module.

    klines      WS   /market/stream <sym>@kline_1m     closed bars only (k.x == true)
                REST /fapi/v1/klines                   warmup backfill and gap repair
    best quote  WS   /public/stream <sym>@bookTicker   kept in memory: the mid at any instant
    funding     REST /fapi/v1/premiumIndex, every 60 s -> LiveFunding, settled like the archive
                REST /fapi/v1/fundingRate (settled history) when a resumed book re-derives the past

Bars reach subscribers strictly in order per symbol and without duplicates: the warmup backfill,
the websocket and gap repair all go through `_deliver`, which drops anything at or before the last
delivered minute and repairs a hole from REST before handing on the bar after it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import random
import threading
import time
import urllib.request
from typing import Any, Callable

from app.backtest.funding import FundingSchedule
from app.config import (MARKETDATA_WS_BOOK, MARKETDATA_WS_KLINES, METADATA_FUNDING_RATE, METADATA_KLINES,
                        METADATA_PREMIUM_INDEX)
from app.core.types import Candle

log = logging.getLogger("paperlab.live.market")

MINUTE = 60_000
PAGE = 1500


def get_json(url: str, timeout: float = 10.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "paperlab-live-shadow"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class LiveFunding(FundingSchedule):
    """A FundingSchedule that grows while the service runs.

    premiumIndex reports the rate that will settle at `nextFundingTime`; the last value seen before
    that instant is recorded against it, which is what the replay's `_settle_funding` then charges
    exactly as it charges archive rates."""

    def upsert(self, symbol: str, ts: int, rate: float) -> None:
        rows = self.by_symbol.setdefault(symbol, [])
        if rows and rows[-1][0] == ts:
            rows[-1] = (ts, rate)
        elif not rows or rows[-1][0] < ts:
            rows.append((ts, rate))

    def merge_settled(self, symbol: str, settled: list[tuple[int, float]]) -> int:
        """Settled history (REST fundingRate) for a re-derived past: authoritative, merged in order."""
        rows = dict(self.by_symbol.get(symbol, []))
        rows.update({int(ts): float(rate) for ts, rate in settled})
        self.by_symbol[symbol] = sorted(rows.items())
        return len(settled)


def kline_row(symbol: str, r: list[Any], source: str) -> Candle:
    return Candle(symbol, "1m", int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                  float(r[5]), int(r[6]), True, float(r[7]), int(r[8]), source,
                  float(r[9]) if len(r) > 9 else 0.0)


def fetch_klines(symbol: str, start_ms: int, end_ms: int, now_ms: int,
                 fetch: Callable[[str], Any] = get_json, pause_s: float = 0.0) -> list[Candle]:
    """Closed 1m bars with open_time in [start_ms, end_ms], oldest first. `pause_s` spaces the pages
    (a 1500-bar page costs 10 request weight; Railway's egress IP is shared)."""
    out: list[Candle] = []
    cur = start_ms
    while cur <= end_ms:
        if pause_s and out:
            time.sleep(pause_s)
        rows = fetch(f"{METADATA_KLINES}?symbol={symbol}&interval=1m&startTime={cur}"
                     f"&endTime={end_ms}&limit={PAGE}") or []
        if not rows:
            break
        for r in rows:
            if int(r[6]) < now_ms:                 # never hand on the bar that is still forming
                out.append(kline_row(symbol, r, "backfill"))
        cur = int(rows[-1][0]) + MINUTE
        if len(rows) < PAGE:
            break
    return out


class LiveMarket:
    """Owns one asyncio loop in its own thread. Subscribers get a queue.Queue of Candles."""

    def __init__(self, symbols: list[str], warmup_ms: int, fetch: Callable[[str], Any] = get_json,
                 connect_fn: Any = None, clock: Callable[[], float] = time.time,
                 start_ms: int | None = None, funding_from_ms: int | None = None):
        self.symbols = sorted(set(symbols))
        self.warmup_ms = int(warmup_ms)
        self.start_ms = start_ms              # a resumed experiment warms up from ITS backfill start
        self.funding_from_ms = funding_from_ms
        self.fetch = fetch
        self.connect_fn = connect_fn
        self.clock = clock
        self.subs: dict[str, list[queue.Queue]] = {s: [] for s in self.symbols}
        self.book: dict[str, tuple[float, float, int]] = {}
        self.funding = LiveFunding()
        self.funding_info: dict[str, dict[str, Any]] = {}
        self.delivered: dict[str, int] = {s: 0 for s in self.symbols}
        self.warm: dict[str, bool] = {s: False for s in self.symbols}
        self.stats: dict[str, Any] = {"klines": {"connected": False, "reconnects": 0, "last_msg_ts": 0.0,
                                                 "detail": "not started"},
                                      "book": {"connected": False, "reconnects": 0, "last_msg_ts": 0.0,
                                               "detail": "not started"},
                                      "bars_live": 0, "bars_backfill": 0, "gaps_repaired": 0,
                                      "gap_bars": 0, "funding_polls": 0, "funding_history": 0, "errors": 0}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- consumers ------------------------------------------------------------------------------
    def subscribe(self, symbol: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        self.subs.setdefault(symbol, []).append(q)
        return q

    def mid(self, symbol: str) -> float | None:
        b = self.book.get(symbol)
        return (b[0] + b[1]) / 2.0 if b and b[0] > 0 and b[1] > 0 else None

    def half_spread_bps(self, symbol: str) -> float | None:
        b = self.book.get(symbol)
        if not b or b[0] <= 0 or b[1] <= 0:
            return None
        m = (b[0] + b[1]) / 2.0
        return (b[1] - b[0]) / 2.0 / m * 1e4

    def health(self) -> dict[str, Any]:
        now = self.clock()

        def age(block: dict[str, Any]) -> float | None:
            return round(now - block["last_msg_ts"], 1) if block.get("last_msg_ts") else None
        return {"klines": {**self.stats["klines"], "age_s": age(self.stats["klines"])},
                "book": {**self.stats["book"], "age_s": age(self.stats["book"])},
                "warm": sum(1 for v in self.warm.values() if v), "symbols": len(self.symbols),
                **{k: self.stats[k] for k in ("bars_live", "bars_backfill", "gaps_repaired", "gap_bars",
                                              "funding_polls", "funding_history", "errors")}}

    # -- lifecycle ------------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="live-market", daemon=True)
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
            log.exception("live market loop died")
            self.stats["errors"] += 1

    async def _main(self) -> None:
        inbox: asyncio.Queue = asyncio.Queue()
        streams_k = "/".join(f"{s.lower()}@kline_1m" for s in self.symbols)
        streams_b = "/".join(f"{s.lower()}@bookTicker" for s in self.symbols)
        tasks = [asyncio.create_task(self._ws_loop(f"{MARKETDATA_WS_KLINES}?streams={streams_k}", "klines",
                                                   lambda d: self._on_kline(d, inbox))),
                 asyncio.create_task(self._ws_loop(f"{MARKETDATA_WS_BOOK}?streams={streams_b}", "book",
                                                   self._on_book)),
                 asyncio.create_task(self._funding_loop())]
        try:
            if self.funding_from_ms is not None:
                await self._funding_history()
            await self._backfill_all()
            while not self._stop.is_set():
                try:
                    bar = await asyncio.wait_for(inbox.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                await self._deliver_live(bar)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- delivery -------------------------------------------------------------------------------
    def _deliver(self, bar: Candle) -> bool:
        with self._lock:
            if bar.open_time <= self.delivered.get(bar.symbol, 0):
                return False
            self.delivered[bar.symbol] = bar.open_time
        for q in self.subs.get(bar.symbol, []):
            q.put(bar)
        return True

    async def _backfill(self, symbol: str, start_ms: int, end_ms: int, pause_s: float = 0.0) -> int:
        now = int(self.clock() * 1000)
        bars = await asyncio.to_thread(fetch_klines, symbol, start_ms, end_ms, now, self.fetch, pause_s)
        n = 0
        for b in bars:
            if self._deliver(b):
                n += 1
        return n

    async def _funding_history(self) -> None:
        """Settled rates since the experiment began, before any past bar is re-derived."""
        now = int(self.clock() * 1000)
        for sym in self.symbols:
            cur = int(self.funding_from_ms or 0)
            for attempt in range(3):
                try:
                    while cur < now:
                        rows = await asyncio.to_thread(
                            self.fetch, f"{METADATA_FUNDING_RATE}?symbol={sym}&startTime={cur}&endTime={now}&limit=1000")
                        rows = rows or []
                        self.stats["funding_history"] += self.funding.merge_settled(
                            sym, [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows])
                        if len(rows) < 1000:
                            break
                        cur = int(rows[-1]["fundingTime"]) + 1
                    break
                except Exception as exc:
                    self.stats["errors"] += 1
                    log.warning("funding history %s failed (%s)", sym, str(exc)[:120])
                    await asyncio.sleep(2 * (attempt + 1))

    async def _backfill_all(self) -> None:
        now = int(self.clock() * 1000)
        start = self.start_ms if self.start_ms is not None else (now - self.warmup_ms) // MINUTE * MINUTE
        for sym in self.symbols:
            for attempt in range(5):
                try:
                    n = await self._backfill(sym, start, now, pause_s=0.3)
                    self.stats["bars_backfill"] += n
                    break
                except Exception as exc:
                    self.stats["errors"] += 1
                    log.warning("warmup backfill %s failed (%s), retrying", sym, str(exc)[:120])
                    await asyncio.sleep(2 * (attempt + 1))
            self.warm[sym] = True
            await asyncio.sleep(0.2)                # pace the REST weight

    async def _deliver_live(self, bar: Candle) -> None:
        last = self.delivered.get(bar.symbol, 0)
        if last and bar.open_time > last + MINUTE:
            # A hole (reconnect, missed frame): repair it from REST before this bar.
            try:
                n = await self._backfill(bar.symbol, last + MINUTE, bar.open_time - MINUTE)
                self.stats["gaps_repaired"] += 1
                self.stats["gap_bars"] += n
            except Exception as exc:
                self.stats["errors"] += 1
                log.warning("gap repair %s failed: %s", bar.symbol, str(exc)[:120])
        if self._deliver(bar):
            self.stats["bars_live"] += 1

    # -- websocket ------------------------------------------------------------------------------
    def _on_kline(self, d: dict[str, Any], inbox: asyncio.Queue) -> None:
        if d.get("e") != "kline":
            return
        k = d.get("k") or {}
        if not k.get("x"):
            return
        inbox.put_nowait(Candle(str(d.get("s") or k.get("s")), "1m", int(k["t"]), float(k["o"]),
                                float(k["h"]), float(k["l"]), float(k["c"]), float(k["v"]), int(k["T"]),
                                True, float(k.get("q") or 0.0), int(k.get("n") or 0), "live",
                                float(k.get("V") or 0.0)))

    def _on_book(self, d: dict[str, Any]) -> None:
        if d.get("e") != "bookTicker" and "b" not in d:
            return
        try:
            self.book[str(d["s"])] = (float(d["b"]), float(d["a"]), int(d.get("T") or d.get("E") or 0))
        except (KeyError, ValueError, TypeError):
            pass

    async def _ws_loop(self, url: str, name: str, handler: Callable[[dict[str, Any]], None]) -> None:
        connect = self.connect_fn
        if connect is None:
            from websockets.asyncio.client import connect
        block = self.stats[name]
        backoff = 1.0
        while not self._stop.is_set():
            opened = time.monotonic()
            try:
                async with connect(url, ping_interval=20, ping_timeout=20, open_timeout=15,
                                   max_size=2 ** 22, max_queue=4096) as ws:
                    block.update(connected=True, detail="connected")
                    backoff = 1.0
                    while not self._stop.is_set():
                        if time.monotonic() - opened > 23 * 3600:
                            block["detail"] = "proactive reconnect (24h limit)"
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            block["detail"] = "silence watchdog"
                            break
                        block["last_msg_ts"] = self.clock()
                        try:
                            msg = json.loads(raw)
                            handler(msg.get("data") or msg)
                        except Exception as exc:
                            log.debug("%s frame error: %s", name, str(exc)[:120])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                block["detail"] = f"{exc.__class__.__name__}: {str(exc)[:100]}"
            block["connected"] = False
            if self._stop.is_set():
                break
            block["reconnects"] += 1
            wait = min(30.0, backoff) * random.uniform(0.8, 1.2)
            log.warning("live %s stream down (%s); reconnecting in %.1fs", name, block["detail"], wait)
            await asyncio.sleep(wait)
            backoff = min(30.0, backoff * 2)

    # -- funding --------------------------------------------------------------------------------
    async def _funding_loop(self) -> None:
        wanted = set(self.symbols)
        while not self._stop.is_set():
            try:
                rows = await asyncio.to_thread(self.fetch, METADATA_PREMIUM_INDEX)
                for r in rows or []:
                    sym = r.get("symbol")
                    if sym not in wanted:
                        continue
                    nxt, rate = int(r.get("nextFundingTime") or 0), float(r.get("lastFundingRate") or 0.0)
                    if nxt:
                        self.funding.upsert(sym, nxt, rate)
                    self.funding_info[sym] = {"rate": rate, "next_ts": nxt,
                                              "mark": float(r.get("markPrice") or 0.0)}
                self.stats["funding_polls"] += 1
            except Exception as exc:
                self.stats["errors"] += 1
                log.warning("premium index poll failed: %s", str(exc)[:120])
            for _ in range(60):
                if self._stop.is_set():
                    return
                await asyncio.sleep(1.0)
