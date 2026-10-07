"""Live Bybit linear market data for the V6 FORWARD ARENA (docs/V6_PROTOCOL.md §3).

Public, unauthenticated, read-only endpoints. There is no key, no signed request and no order endpoint anywhere in
this module.

    traded coins     WS  kline.1.<SYM>        closed 1m bars (confirm = true)
                     WS  tickers.<SYM>        best bid / ask (the OBSERVED spread), mark, index, predicted funding,
                                              next funding time, open interest
                     REST /v5/market/kline    warm-up backfill and gap repair
                     REST hourly              open interest 1h, premium index 1h (basis), long/short account ratio 1h,
                                              settled funding -- polled at every hour for the HOUR BARRIER
    market context   REST hourly              1h klines, open interest 1h and settled funding of a fixed breadth set
                     WS  tickers.BTCUSDT / tickers.ETHUSDT  (display only)

Bars reach subscribers strictly in order per symbol and without duplicates. A bar that closes an hour H is held at
the HOUR BARRIER until the poller has fetched H's positioning (or BARRIER_TIMEOUT_MS passed); the market then records
a WATERMARK (the newest stamp of each series, per coin and for the context) and releases the bar, so the hourly
decision sees H's data and a restart can reproduce exactly what it saw. Every input the bots consume -- bars with the
quote observed at their close, positioning points, watermarks and the funding rate charged at each settlement -- is
handed to `store` so the forward experiment can be re-derived from identical inputs.
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
import urllib.request
from typing import Any, Callable, Mapping, Sequence

from app.backtest import bybit_archive as bb
from app.backtest.funding import FundingSchedule
from app.competition.v6_config import BARRIER_TIMEOUT_MS, STALE_POSITIONING_H, STALE_STREAM_S
from app.core.types import Candle

log = logging.getLogger("paperlab.live.bybit")

BYBIT_WS_LINEAR = "wss://stream.bybit.com/v5/public/linear"
MINUTE = 60_000
HOUR = 3_600_000
DAY = 24 * HOUR
LIVE_HORIZON_MS = 10 * MINUTE          # a bar closing an hour this recent is decided live: it waits at the barrier
QUOTE_MAX_AGE_MS = 5 * MINUTE          # an observed quote older than this is not used for a fill


def fast_get(url: str, timeout: float = 8.0) -> dict[str, Any]:
    """One attempt, short timeout: the hour barrier retries on its own clock instead of backing off."""
    req = urllib.request.Request(url, headers={"User-Agent": "paperlab-v6-forward"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    if d.get("retCode") != 0:
        raise RuntimeError(str(d.get("retMsg")))
    return d.get("result") or {}


class LiveFundingV6(FundingSchedule):
    """The funding the engine charges at each settlement instant T.

    Live: Bybit's predicted rate for T (tickers stream), the last value seen before T, FROZEN when the first bar at or
    after T is delivered, and recorded. A re-derivation charges the recorded rate, else Bybit's settled rate -- never
    a later value."""

    def __init__(self, charged: Mapping[str, Sequence[tuple[int, float]]] | None = None):
        super().__init__({})
        self.charged: set[tuple[str, int]] = set()
        self._lock = threading.Lock()
        for sym, rows in (charged or {}).items():
            for ts, rate in rows:
                self._put(sym, int(ts), float(rate))
                self.charged.add((sym, int(ts)))

    def _put(self, sym: str, ts: int, rate: float) -> None:
        rows = self.by_symbol.setdefault(sym, [])
        i = bisect.bisect_left(rows, (ts, float("-inf")))
        if i < len(rows) and rows[i][0] == ts:
            rows[i] = (ts, rate)
        else:
            rows.insert(i, (ts, rate))

    def upsert(self, sym: str, ts: int, rate: float) -> None:
        with self._lock:
            if (sym, int(ts)) not in self.charged:
                self._put(sym, int(ts), float(rate))

    def merge_settled(self, sym: str, rows: Sequence[tuple[int, float]]) -> int:
        n = 0
        with self._lock:
            for ts, rate in rows:
                if (sym, int(ts)) not in self.charged:
                    self._put(sym, int(ts), float(rate))
                    n += 1
        return n

    def freeze(self, sym: str, ts: int) -> float | None:
        """Fix the rate charged at T (called when the first bar at or after T is delivered)."""
        with self._lock:
            rows = self.by_symbol.get(sym) or []
            i = bisect.bisect_left(rows, (int(ts), float("-inf")))
            if i >= len(rows) or rows[i][0] != int(ts):
                return None
            self.charged.add((sym, int(ts)))
            return rows[i][1]


def kline_candle(symbol: str, r: Sequence[Any], source: str) -> Candle:
    """A Bybit REST kline row [start, open, high, low, close, volume, turnover] as a closed 1m Candle (Bybit publishes
    no trade count and no taker volume: those stay 0 and no V6 feature reads them)."""
    o = int(r[0])
    return Candle(symbol, "1m", o, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), o + MINUTE - 1,
                  True, float(r[6]), 0, source, 0.0)


class BybitLiveMarket:
    """Owns one asyncio loop in its own thread. Subscribers get a queue.Queue of Candles per traded coin."""

    def __init__(self, traded: Sequence[str], context: Sequence[str], feeds: Mapping[str, Any], market: Any,
                 funding: LiveFundingV6, store: Callable[[str, dict[str, Any]], None], warmup_from_ms: int,
                 funding_interval_min: Mapping[str, int], preload: Mapping[str, Sequence[Candle]] | None = None,
                 quotes: Mapping[str, Sequence[tuple[int, float]]] | None = None,
                 history_days: Mapping[str, int] | None = None, fetch: Callable[[str], dict[str, Any]] = bb.get,
                 fast_fetch: Callable[[str], dict[str, Any]] = fast_get, connect_fn: Any = None,
                 clock: Callable[[], float] = time.time, ws_url: str = BYBIT_WS_LINEAR):
        self.traded = list(dict.fromkeys(traded))
        self.context = list(dict.fromkeys(context))
        self.feeds = dict(feeds)
        self.market = market
        self.funding = funding
        self.store = store
        self.warmup_from_ms = int(warmup_from_ms)
        self.interval_min = {k: int(v) for k, v in funding_interval_min.items()}
        self.preload = {k: list(v) for k, v in (preload or {}).items()}
        self.history = {"funding": 95, "positioning": 35, "context": 45, "context_oi": 3, **dict(history_days or {})}
        self.fetch = fetch
        self.fast_fetch = fast_fetch
        self.connect_fn = connect_fn
        self.clock = clock
        self.ws_url = ws_url
        self.subs: dict[str, list[queue.Queue]] = {s: [] for s in self.traded}
        self.delivered: dict[str, int] = {s: 0 for s in self.traded}
        self.tickers: dict[str, dict[str, Any]] = {}
        self._q_ts: dict[str, list[int]] = {s: [] for s in self.traded}
        self._q_hs: dict[str, list[float]] = {s: [] for s in self.traded}
        for sym, rows in (quotes or {}).items():
            for ts, hs in sorted(rows):
                self._add_quote(sym, int(ts), float(hs))
        self.warm: dict[str, bool] = {s: False for s in self.traded}
        self.positioning_ready = False
        self.barrier: dict[int, dict[str, Any]] = {}
        self._events: dict[int, asyncio.Event] = {}
        self._wm_ctx_done: set[int] = set()
        self.stats: dict[str, Any] = {"ws": {"connected": False, "reconnects": 0, "last_msg_ts": 0.0, "detail": "not started"},
                                      "last_kline_ts": {}, "last_ticker_ts": {}, "bars_live": 0, "bars_backfill": 0,
                                      "bars_preloaded": 0, "gaps_repaired": 0, "gap_bars": 0, "rest_errors": 0,
                                      "rest_calls": 0, "barriers": 0, "barrier_timeouts": 0, "barrier_last": None,
                                      "positioning_points": 0}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- consumers ------------------------------------------------------------------------------------------
    def subscribe(self, symbol: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        self.subs.setdefault(symbol, []).append(q)
        return q

    def mid(self, symbol: str) -> float | None:
        t = self.tickers.get(symbol) or {}
        b, a = t.get("bid"), t.get("ask")
        return (b + a) / 2.0 if b and a and b > 0 and a > 0 else None

    def live_half_spread_bps(self, symbol: str) -> float | None:
        t = self.tickers.get(symbol) or {}
        b, a = t.get("bid"), t.get("ask")
        if not b or not a or b <= 0 or a <= 0:
            return None
        m = (a + b) / 2.0
        return (a - b) / 2.0 / m * 1e4

    def spread_for_jev(self, symbol: str) -> tuple[float | None, str]:
        hs = self.live_half_spread_bps(symbol)
        return (round(hs, 4), "observed") if hs is not None else (None, "modelled")

    def _add_quote(self, sym: str, ts: int, hs: float) -> None:
        ts_list, hs_list = self._q_ts.setdefault(sym, []), self._q_hs.setdefault(sym, [])
        if ts_list and ts <= ts_list[-1]:
            i = bisect.bisect_left(ts_list, ts)
            if i < len(ts_list) and ts_list[i] == ts:
                hs_list[i] = hs
                return
            ts_list.insert(i, ts)
            hs_list.insert(i, hs)
            return
        ts_list.append(ts)
        hs_list.append(hs)

    def quote_half_spread(self, symbol: str, ts: int) -> float | None:
        """The half-spread observed at or before `ts` (recorded with each live bar), for the engine's fills; None when
        no recent observation exists (warm-up or downtime bars): the engine then models the spread."""
        ts_list = self._q_ts.get(symbol) or []
        i = bisect.bisect_right(ts_list, int(ts))
        if not i:
            return None
        if int(ts) - ts_list[i - 1] > QUOTE_MAX_AGE_MS:
            return None
        return self._q_hs[symbol][i - 1]

    def stream_ok(self, symbol: str) -> tuple[bool, str | None]:
        """Is the live data for `symbol` fresh enough to decide on? (WS up, recent kline and ticker messages)."""
        now = self.clock()
        if not self.stats["ws"]["connected"]:
            return False, "websocket down"
        lk = self.stats["last_kline_ts"].get(symbol)
        if not lk or now - lk > STALE_STREAM_S:
            return False, "no kline message for %ss" % (int(now - lk) if lk else "ever")
        lt = self.stats["last_ticker_ts"].get(symbol)
        if not lt or now - lt > STALE_STREAM_S:
            return False, "no ticker message for %ss" % (int(now - lt) if lt else "ever")
        return True, None

    def positioning_ok(self, symbol: str, t: int) -> tuple[bool, str | None]:
        snap = self.feeds[symbol].snapshot(t)
        oi_age, f_age = snap.get("oi_age_h"), snap.get("funding_age_h")
        iv = snap.get("funding_interval_h") or (self.interval_min.get(symbol, 480) / 60.0)
        if oi_age is None or oi_age > STALE_POSITIONING_H:
            return False, f"open interest {oi_age} h old"
        if f_age is None or f_age > iv + STALE_POSITIONING_H:
            return False, f"funding {f_age} h old"
        return True, None

    def health(self) -> dict[str, Any]:
        now = self.clock()
        ws = self.stats["ws"]
        lk = [v for v in self.stats["last_kline_ts"].values() if v]
        return {"ws": {"connected": ws["connected"], "reconnects": ws["reconnects"], "detail": ws["detail"],
                       "age_s": round(now - ws["last_msg_ts"], 1) if ws["last_msg_ts"] else None},
                "klines_age_s": round(now - max(lk), 1) if lk else None,
                "warm": sum(1 for v in self.warm.values() if v), "symbols": len(self.traded),
                "context_symbols": len(self.context), "positioning_ready": self.positioning_ready,
                **{k: self.stats[k] for k in ("bars_live", "bars_backfill", "bars_preloaded", "gaps_repaired",
                                              "gap_bars", "rest_errors", "rest_calls", "barriers", "barrier_timeouts",
                                              "barrier_last", "positioning_points")}}

    def ready(self) -> bool:
        return self.positioning_ready and all(self.warm.values()) and bool(self.stats["ws"]["connected"])

    # -- lifecycle ------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="bybit-market", daemon=True)
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
            log.exception("bybit market loop died")
            self.stats["rest_errors"] += 1

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        inbox: asyncio.Queue = asyncio.Queue()
        tasks = [asyncio.create_task(self._ws_loop(inbox)), asyncio.create_task(self._hour_loop())]
        try:
            await self._backfill_positioning()
            self.positioning_ready = True
            for sym in self.traded:
                for bar in self.preload.get(sym, []):
                    if self._deliver(bar, source="stored"):
                        self.stats["bars_preloaded"] += 1
                self.preload[sym] = []
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

    # -- REST -----------------------------------------------------------------------------------------------
    async def _call(self, fn: Callable[..., Any], *args: Any) -> Any:
        self.stats["rest_calls"] += 1
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception as exc:
            self.stats["rest_errors"] += 1
            log.warning("bybit REST %s failed: %s", getattr(fn, "__name__", "call"), str(exc)[:160])
            return None

    def _add_points(self, sym: str, kind: str, rows: Sequence[tuple[int, float]], context: bool = False) -> int:
        if context:
            series = {"close": self.market.close, "oi": self.market.oi, "funding": self.market.funding}[kind][sym]
        else:
            series = self.feeds[sym].series(kind)
        n = 0
        for ts, v in rows:
            if series.add(int(ts), float(v)):
                n += 1
                self.store("pos", {"symbol": sym, "kind": ("ctx_" + kind) if context else kind, "ts": int(ts),
                                   "value": float(v)})
        if kind == "funding" and not context:
            self.funding.merge_settled(sym, rows)
        self.stats["positioning_points"] += n
        return n

    def _closed(self, rows: Sequence[Sequence[Any]], span_ms: int, now_ms: int) -> list[tuple[int, float]]:
        return [(int(r[0]), float(r[4])) for r in rows if int(r[0]) + span_ms <= now_ms]

    async def _backfill_positioning(self) -> None:
        now = int(self.clock() * 1000)
        for sym in self.traded:
            feed = self.feeds[sym]
            for kind, days in (("funding", self.history["funding"]), ("oi", self.history["positioning"]),
                               ("premium", self.history["positioning"]), ("ratio", self.history["positioning"])):
                last = feed.series(kind).latest_ts()
                start = max(now - days * DAY, (last + 1) if last else 0)
                if kind == "funding":
                    rows = await self._call(bb.funding, sym, start, now, self.fetch)
                elif kind == "oi":
                    rows = await self._call(bb.open_interest, sym, start, now, self.fetch)
                elif kind == "ratio":
                    rows = await self._call(bb.account_ratio, sym, start, now, self.fetch)
                else:
                    raw = await self._call(bb.klines, sym, "60", start, now, "premium-index-price-kline", self.fetch)
                    rows = self._closed(raw or [], HOUR, now)
                self._add_points(sym, kind, rows or [])
                await asyncio.sleep(0.05)
        for sym in self.context:
            for kind, days in (("close", self.history["context"]), ("oi", self.history["context_oi"]),
                               ("funding", self.history["context_oi"])):
                s = {"close": self.market.close, "oi": self.market.oi, "funding": self.market.funding}[kind][sym]
                last = s.latest_ts()
                start = max(now - days * DAY, (last + 1) if last else 0)
                if kind == "close":
                    raw = await self._call(bb.klines, sym, "60", start, now, "kline", self.fetch)
                    rows = self._closed(raw or [], HOUR, now)
                elif kind == "oi":
                    rows = await self._call(bb.open_interest, sym, start, now, self.fetch)
                else:
                    rows = await self._call(bb.funding, sym, start, now, self.fetch)
                self._add_points(sym, kind, rows or [], context=True)
                await asyncio.sleep(0.05)

    async def _backfill_1m(self, sym: str, start_ms: int, end_ms: int) -> int:
        now = int(self.clock() * 1000)
        raw = await self._call(bb.klines, sym, "1", start_ms, end_ms, "kline", self.fetch)
        n = 0
        for r in raw or []:
            if int(r[0]) + MINUTE <= now and await self._deliver_checked(kline_candle(sym, r, "backfill"), "backfill"):
                n += 1
        return n

    async def _backfill_all(self) -> None:
        for sym in self.traded:
            start = max(self.warmup_from_ms, self.delivered.get(sym, 0) + MINUTE)
            for attempt in range(5):
                now = int(self.clock() * 1000)
                end = now // MINUTE * MINUTE
                if start >= end:
                    break
                n = await self._backfill_1m(sym, start, end)
                self.stats["bars_backfill"] += n
                if self.delivered.get(sym, 0) >= end - 2 * MINUTE:
                    break
                start = max(start, self.delivered.get(sym, 0) + MINUTE)
                await asyncio.sleep(1.0 + attempt)
            self.warm[sym] = True

    # -- delivery --------------------------------------------------------------------------------------------
    def _deliver(self, bar: Candle, source: str, quote: tuple[float, float] | None = None) -> bool:
        with self._lock:
            if bar.open_time <= self.delivered.get(bar.symbol, 0):
                return False
            self.delivered[bar.symbol] = bar.open_time
        hs = None
        if quote is not None and quote[0] > 0 and quote[1] > 0:
            m = (quote[0] + quote[1]) / 2.0
            hs = (quote[1] - quote[0]) / 2.0 / m * 1e4
            self._add_quote(bar.symbol, bar.close_time, hs)
        if source != "stored":
            self.store("bar", {"symbol": bar.symbol, "open_time": bar.open_time, "open": bar.open, "high": bar.high,
                               "low": bar.low, "close": bar.close, "volume": bar.volume, "turnover": bar.quote_volume,
                               "bid": quote[0] if quote else None, "ask": quote[1] if quote else None,
                               "half_spread_bps": hs, "source": source})
        # funding: the first bar at or after a settlement instant fixes the rate the engine charges for it
        iv = self.interval_min.get(bar.symbol, 480) * MINUTE
        t_set = (bar.open_time + iv - 1) // iv * iv
        if bar.open_time <= t_set <= bar.close_time:
            rate = self.funding.freeze(bar.symbol, t_set)
            if rate is not None:
                self.store("funding_charged", {"symbol": bar.symbol, "ts": t_set, "rate": rate})
        for q in self.subs.get(bar.symbol, []):
            q.put(bar)
        return True

    async def _deliver_checked(self, bar: Candle, source: str, quote: tuple[float, float] | None = None) -> bool:
        """Every delivery path (live, gap repair, backfill) goes through here: a bar that closes a RECENT hour waits at
        the hour barrier and records its watermark before any bot sees it."""
        if bar.open_time <= self.delivered.get(bar.symbol, 0):
            return False
        H = bar.close_time + 1
        if H % HOUR == 0 and self.clock() * 1000 - H < LIVE_HORIZON_MS:
            await self._barrier(H)
            self._watermark(bar.symbol, H)
        return self._deliver(bar, source=source, quote=quote)

    async def _deliver_live(self, item: tuple[Candle, tuple[float, float] | None]) -> None:
        bar, quote = item
        last = self.delivered.get(bar.symbol, 0)
        if last and bar.open_time > last + MINUTE:
            n = await self._backfill_1m(bar.symbol, last + MINUTE, bar.open_time)
            self.stats["gaps_repaired"] += 1
            self.stats["gap_bars"] += n
        if await self._deliver_checked(bar, "live", quote):
            self.stats["bars_live"] += 1

    async def _barrier(self, H: int) -> None:
        ev = self._events.setdefault(H, asyncio.Event())
        if ev.is_set():
            return
        remaining = (H + BARRIER_TIMEOUT_MS + 2000) / 1000.0 - self.clock()
        if remaining > 0:
            try:
                await asyncio.wait_for(ev.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass

    def _watermark(self, sym: str, H: int) -> None:
        feed = self.feeds[sym]
        if H not in feed.caps:
            wm = feed.watermark()
            feed.caps[H] = {k: v for k, v in wm.items() if v is not None}
            self.store("watermark", {"symbol": sym, "t": H, "caps": feed.caps[H],
                                     "barrier": self.barrier.get(H)})
        if H not in self._wm_ctx_done and self.market is not None:
            self._wm_ctx_done.add(H)
            wm = self.market.watermark()
            self.market.caps[H] = {k: v for k, v in wm.items() if v is not None}
            self.store("watermark", {"symbol": "*", "t": H, "caps": self.market.caps[H]})

    # -- the hourly poller (HOUR BARRIER) -------------------------------------------------------------------------
    def _due_funding(self, sym: str, H: int) -> bool:
        iv = self.interval_min.get(sym, 480) * MINUTE
        return iv > 0 and H % iv == 0

    def _needs(self, H: int) -> set[tuple[str, str, bool]]:
        need = set()
        for s in self.traded:
            need |= {(s, "oi", False), (s, "premium", False), (s, "ratio", False)}
            if self._due_funding(s, H):
                need.add((s, "funding", False))
        for s in self.context:
            need |= {(s, "close", True), (s, "oi", True)}
            if self._due_funding(s, H):
                need.add((s, "funding", True))
        return need

    def _have(self, sym: str, kind: str, context: bool, H: int) -> bool:
        if context:
            s = {"close": self.market.close, "oi": self.market.oi, "funding": self.market.funding}[kind][sym]
        else:
            s = self.feeds[sym].series(kind)
        last = s.latest_ts()
        if last is None:
            return False
        return last >= (H - HOUR if kind in ("premium", "close") else H)

    def _poll_one(self, sym: str, kind: str, context: bool, H: int) -> list[tuple[int, float]]:
        """One hour's points for one series (runs in a worker thread, single attempt)."""
        f = self.fast_fetch
        start, end = H - 3 * HOUR, H + MINUTE
        if kind == "oi":
            return bb.open_interest(sym, start, end, f)
        if kind == "ratio":
            return bb.account_ratio(sym, start, end, f)
        if kind == "funding":
            return bb.funding(sym, start, end, f)
        rows = bb.klines(sym, "60", start, end, "premium-index-price-kline" if kind == "premium" else "kline", f)
        return self._closed(rows, HOUR, H + MINUTE)

    async def _hour_loop(self) -> None:
        done = 0
        while not self._stop.is_set():
            now_ms = int(self.clock() * 1000)
            H = (now_ms // HOUR) * HOUR
            if H <= done or now_ms - H > BARRIER_TIMEOUT_MS:
                nxt = (now_ms // HOUR + 1) * HOUR
                await asyncio.sleep(min(30.0, max(0.2, (nxt - now_ms) / 1000.0 + 0.5)))
                continue
            done = H
            if not self.positioning_ready:
                self._events.setdefault(H, asyncio.Event()).set()
                continue
            t0 = self.clock()
            pending = {n for n in self._needs(H) if not self._have(n[0], n[1], n[2], H)}
            while pending and self.clock() * 1000 < H + BARRIER_TIMEOUT_MS and not self._stop.is_set():
                batch = sorted(pending)
                results = await asyncio.gather(*(self._call(self._poll_one, s, k, c, H) for s, k, c in batch))
                for (s, k, c), rows in zip(batch, results):
                    if rows:
                        self._add_points(s, k, rows, context=c)
                pending = {n for n in pending if not self._have(n[0], n[1], n[2], H)}
                if pending:
                    await asyncio.sleep(3.0)
            info = {"H": H, "complete": not pending, "missing": sorted(f"{s}:{k}{'(ctx)' if c else ''}" for s, k, c in pending),
                    "waited_s": round(self.clock() - t0, 1)}
            self.barrier[H] = info
            for old in [h for h in self.barrier if h < H - 6 * HOUR]:
                self.barrier.pop(old, None)
            self.stats["barriers"] += 1
            self.stats["barrier_timeouts"] += int(bool(pending))
            self.stats["barrier_last"] = info
            self._events.setdefault(H, asyncio.Event()).set()
            for old in [h for h in self._events if h < H - 6 * HOUR]:
                self._events.pop(old, None)
            if pending:
                log.warning("hour barrier %s timed out; missing %s", H, info["missing"][:8])

    # -- websocket ---------------------------------------------------------------------------------------------
    def _on_message(self, msg: dict[str, Any], inbox: asyncio.Queue) -> None:
        topic = str(msg.get("topic") or "")
        now = self.clock()
        if topic.startswith("kline.1."):
            sym = topic.split(".", 2)[2]
            self.stats["last_kline_ts"][sym] = now
            for k in msg.get("data") or []:
                if not k.get("confirm"):
                    continue
                o = int(k["start"])
                bar = Candle(sym, "1m", o, float(k["open"]), float(k["high"]), float(k["low"]), float(k["close"]),
                             float(k["volume"]), o + MINUTE - 1, True, float(k.get("turnover") or 0.0), 0, "live", 0.0)
                t = self.tickers.get(sym) or {}
                q = (t.get("bid"), t.get("ask")) if t.get("bid") and t.get("ask") else None
                inbox.put_nowait((bar, q))
        elif topic.startswith("tickers."):
            sym = topic.split(".", 1)[1]
            d = msg.get("data") or {}
            t = self.tickers.setdefault(sym, {})
            for src, dst in (("bid1Price", "bid"), ("ask1Price", "ask"), ("markPrice", "mark"), ("indexPrice", "index"),
                             ("fundingRate", "funding_rate"), ("openInterest", "oi"), ("lastPrice", "last")):
                if d.get(src) not in (None, ""):
                    try:
                        t[dst] = float(d[src])
                    except (TypeError, ValueError):
                        pass
            if d.get("nextFundingTime") not in (None, ""):
                t["next_funding_ts"] = int(d["nextFundingTime"])
            t["ts"] = now
            self.stats["last_ticker_ts"][sym] = now
            if sym in self.traded and t.get("next_funding_ts") and t.get("funding_rate") is not None:
                self.funding.upsert(sym, int(t["next_funding_ts"]), float(t["funding_rate"]))

    async def _ws_loop(self, inbox: asyncio.Queue) -> None:
        connect = self.connect_fn
        if connect is None:
            from websockets.asyncio.client import connect
        block = self.stats["ws"]
        topics = [f"kline.1.{s}" for s in self.traded] + [f"tickers.{s}" for s in self.traded] + \
                 [f"tickers.{s}" for s in ("BTCUSDT", "ETHUSDT") if s not in self.traded]
        backoff = 1.0
        while not self._stop.is_set():
            opened = time.monotonic()
            try:
                async with connect(self.ws_url, ping_interval=None, open_timeout=15, max_size=2 ** 22,
                                   max_queue=4096) as ws:
                    for i in range(0, len(topics), 10):
                        await ws.send(json.dumps({"op": "subscribe", "args": topics[i:i + 10]}))
                    block.update(connected=True, detail="connected")
                    backoff = 1.0
                    last_ping = time.monotonic()
                    while not self._stop.is_set():
                        if time.monotonic() - opened > 23 * 3600:
                            block["detail"] = "proactive reconnect"
                            break
                        if time.monotonic() - last_ping > 20:
                            await ws.send(json.dumps({"op": "ping"}))
                            last_ping = time.monotonic()
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        except asyncio.TimeoutError:
                            if self.clock() - (block["last_msg_ts"] or self.clock()) > 60:
                                block["detail"] = "silence watchdog"
                                break
                            continue
                        block["last_msg_ts"] = self.clock()
                        try:
                            self._on_message(json.loads(raw), inbox)
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
            wait = min(30.0, backoff) * random.uniform(0.8, 1.2)
            log.warning("bybit websocket down (%s); reconnecting in %.1fs", block["detail"], wait)
            await asyncio.sleep(wait)
            backoff = min(30.0, backoff * 2)
