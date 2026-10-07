"""The V11 SCAN market feed (docs/V11_PROTOCOL.md): Bybit linear public REST, polled every minute for the whole
30-coin universe and delivered as ONE ordered tape. Read-only: no key, no order path.

A multi-coin book needs every coin's bar for an instant before it decides, and a restart must re-derive the same
decisions. So the feed hands its subscribers a single queue of closed 1m bars, minute by minute, every coin of the
minute in a fixed order with the ANCHOR (BTCUSDT) last -- the scanners decide on the anchor's candle, when the whole
minute is in:

    history    bars stored by earlier sessions (the experiment's warm-up and forward tape), read from the database a
               day at a time, re-emitted in the same order (a bounded pace: never more than ~50k bars queued)
    catch-up   REST 1m klines from the last delivered minute to now, a day at a time, stored and emitted
    live       at every minute close + 2 s: one tickers call (bid / ask / funding for every coin) and the new klines
               of every coin (parallel). A minute is released when all coins are in, or 15 s after it closed with
               whatever arrived (a coin's late bar is then delivered before its next one: per-coin order always holds)

The observed half-spread (bid / ask at delivery) is recorded with every live bar and priced into fills, exactly like
the V6-V8 feed; funding settlements freeze the last predicted rate on the first bar at or after the settlement.
"""
from __future__ import annotations

import bisect
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.backtest import bybit_archive as bb
from app.core.types import Candle

log = logging.getLogger("paperlab.live.scan")

MINUTE = 60_000
DAY = 86_400_000
POLL_DELAY_S = 2.0
RETRY_EVERY_S = 2.0
RELEASE_AFTER_MS = 15_000
STALE_S = 150
QUOTE_MAX_AGE_MS = 5 * MINUTE
MAX_QUEUED = 50_000


def candle(symbol: str, r: Sequence[Any], source: str) -> Candle:
    o = int(r[0])
    return Candle(symbol, "1m", o, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), o + MINUTE - 1,
                  True, float(r[6] if len(r) > 6 else 0.0), 0, source, 0.0)


def row_candle(r: Mapping[str, Any]) -> Candle:
    o = int(r["open_time"])
    return Candle(r["symbol"], "1m", o, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                  float(r["volume"] or 0.0), o + MINUTE - 1, True, float(r.get("turnover") or 0.0), 0,
                  str(r.get("source") or "stored"), 0.0)


class ScanMarket:
    def __init__(self, symbols: Sequence[str], anchor: str, store: Callable[[str, dict[str, Any]], None],
                 funding: Any, warmup_from_ms: int, funding_interval_min: Mapping[str, int],
                 history: Callable[[int, int], Mapping[str, Sequence[Mapping[str, Any]]]] | None = None,
                 history_until_ms: int | None = None, quotes_from_ms: int | None = None,
                 fetch: Callable[[str], dict[str, Any]] = bb.get, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] | None = None):
        if anchor not in symbols:
            raise ValueError("the anchor must be in the universe")
        self.symbols = list(dict.fromkeys(symbols))
        self.anchor = anchor
        self.order = sorted(s for s in self.symbols if s != anchor) + [anchor]
        self.store = store
        self.funding = funding
        self.warmup_from_ms = int(warmup_from_ms)
        self.interval_min = {k: int(v) for k, v in funding_interval_min.items()}
        self.history = history
        self.history_until_ms = history_until_ms
        self.quotes_from_ms = int(quotes_from_ms) if quotes_from_ms is not None else None
        self.fetch = fetch
        self.clock = clock
        self._stop = threading.Event()
        self._sleep = sleep or (lambda s: self._stop.wait(s))
        self.subs: list[queue.Queue] = []
        self.delivered: dict[str, int] = {s: 0 for s in self.symbols}
        self.tickers: dict[str, dict[str, Any]] = {}
        self._q_ts: dict[str, list[int]] = {s: [] for s in self.symbols}
        self._q_hs: dict[str, list[float]] = {s: [] for s in self.symbols}
        self._pending: dict[str, dict[int, Candle]] = {s: {} for s in self.symbols}
        self.phase = "NEW"
        self.live_batches = 0
        self.last_poll_ok = 0.0
        self.stats: dict[str, Any] = {"bars_history": 0, "bars_backfill": 0, "bars_live": 0, "late_bars": 0,
                                      "incomplete_minutes": 0, "rest_calls": 0, "rest_errors": 0, "last_error": None,
                                      "last_batch": None}
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- consumers --------------------------------------------------------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        self.subs.append(q)
        return q

    def mid(self, symbol: str) -> float | None:
        t = self.tickers.get(symbol) or {}
        b, a = t.get("bid"), t.get("ask")
        return (a + b) / 2.0 if b and a and a > 0 and b > 0 else None

    def live_half_spread_bps(self, symbol: str) -> float | None:
        t = self.tickers.get(symbol) or {}
        b, a = t.get("bid"), t.get("ask")
        if not b or not a or a <= 0 or b <= 0:
            return None
        return (a - b) / (a + b) * 1e4

    def quote_half_spread(self, symbol: str, ts: int) -> float | None:
        ts_list = self._q_ts.get(symbol) or []
        i = bisect.bisect_right(ts_list, int(ts))
        if not i or int(ts) - ts_list[i - 1] > QUOTE_MAX_AGE_MS:
            return None
        return self._q_hs[symbol][i - 1]

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

    def stream_ok(self, symbol: str) -> tuple[bool, str | None]:
        now = self.clock()
        if self.phase != "LIVE":
            return False, f"feed {self.phase.lower()}"
        if now - self.last_poll_ok > STALE_S:
            return False, "no successful poll for %ss" % int(now - self.last_poll_ok)
        last = self.delivered.get(symbol) or 0
        if now * 1000 - (last + MINUTE) > STALE_S * 1000:
            return False, "no bar for %ss" % int(now - (last + MINUTE) / 1000)
        return True, None

    def ready(self) -> bool:
        return self.phase == "LIVE" and self.live_batches > 0

    def data_ok(self) -> bool:
        return self.phase == "LIVE" and self.clock() - self.last_poll_ok <= STALE_S

    def health(self) -> dict[str, Any]:
        now = self.clock()
        newest = max(self.delivered.values() or [0])
        return {"ws": {"connected": self.data_ok(), "reconnects": 0, "detail": f"REST poll ({self.phase.lower()})",
                       "age_s": round(now - self.last_poll_ok, 1) if self.last_poll_ok else None},
                "mode": "REST_POLL", "phase": self.phase,
                "klines_age_s": round(now - (newest + MINUTE) / 1000.0, 1) if newest else None,
                "symbols": len(self.symbols), "anchor": self.anchor, "live_batches": self.live_batches,
                **{k: self.stats[k] for k in ("bars_history", "bars_backfill", "bars_live", "late_bars",
                                              "incomplete_minutes", "rest_calls", "rest_errors", "last_batch")}}

    # -- lifecycle ----------------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="scan-market", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        try:
            self.phase = "HISTORY"
            self._replay_history()
            self.phase = "CATCH_UP"
            self._catch_up()
            self.phase = "LIVE"
            self._live()
        except Exception as exc:
            log.exception("scan market loop died")
            self.stats["last_error"] = f"{exc.__class__.__name__}: {str(exc)[:160]}"
            self.phase = "ERROR"

    # -- delivery ------------------------------------------------------------------------------------------------------
    def _emit(self, bars: Iterable[Candle], source: str) -> int:
        """Deliver bars in the given order (callers pass canonical order), each coin strictly forward in time."""
        n = 0
        for bar in bars:
            if bar.open_time <= self.delivered.get(bar.symbol, 0):
                continue
            self.delivered[bar.symbol] = bar.open_time
            hs = None
            t = (self.tickers.get(bar.symbol) or {}) if source == "live" else {}
            if t.get("bid") and t.get("ask") and t["ask"] > 0 and t["bid"] > 0:
                hs = (t["ask"] - t["bid"]) / (t["ask"] + t["bid"]) * 1e4
                self._add_quote(bar.symbol, bar.close_time, hs)
            if source != "stored":
                self.store("bar", {"symbol": bar.symbol, "open_time": bar.open_time, "open": bar.open, "high": bar.high,
                                   "low": bar.low, "close": bar.close, "volume": bar.volume, "turnover": bar.quote_volume,
                                   "bid": t.get("bid"), "ask": t.get("ask"), "half_spread_bps": hs, "source": source})
            iv = self.interval_min.get(bar.symbol, 480) * MINUTE
            t_set = (bar.open_time + iv - 1) // iv * iv
            if iv > 0 and bar.open_time <= t_set <= bar.close_time and self.funding is not None:
                rate = self.funding.freeze(bar.symbol, t_set)
                if rate is not None and source != "stored":
                    self.store("funding_charged", {"symbol": bar.symbol, "ts": t_set, "rate": rate})
            for q in self.subs:
                q.put(bar)
            n += 1
        return n

    def _emit_minutes(self, by_symbol: Mapping[str, Sequence[Candle]], source: str) -> int:
        """Merge per-coin bars into minute batches (anchor last) and deliver them."""
        minutes: dict[int, dict[str, Candle]] = {}
        for sym, bars in by_symbol.items():
            for b in bars:
                minutes.setdefault(b.open_time, {})[sym] = b
        n = 0
        for m in sorted(minutes):
            batch = minutes[m]
            n += self._emit((batch[s] for s in self.order if s in batch), source)
            self._pace()
        return n

    def _pace(self) -> None:
        while self.subs and max(q.qsize() for q in self.subs) > MAX_QUEUED and not self._stop.is_set():
            self._sleep(0.05)

    # -- history -------------------------------------------------------------------------------------------------------
    def _replay_history(self) -> None:
        if self.history is None or self.history_until_ms is None or self.history_until_ms < self.warmup_from_ms:
            return
        a = self.warmup_from_ms
        while a <= self.history_until_ms and not self._stop.is_set():
            b = min(a + DAY, self.history_until_ms + MINUTE)
            rows = self.history(a, b)
            by_sym = {}
            for sym, rs in rows.items():
                bars = []
                for r in rs:
                    bars.append(row_candle(r))
                    if (self.quotes_from_ms is not None and int(r["open_time"]) + MINUTE >= self.quotes_from_ms
                            and r.get("half_spread_bps") is not None):
                        self._add_quote(sym, int(r["open_time"]) + MINUTE - 1, float(r["half_spread_bps"]))
                by_sym[sym] = bars
            self.stats["bars_history"] += self._emit_minutes(by_sym, "stored")
            a = b

    # -- REST ------------------------------------------------------------------------------------------------------------
    def _get(self, url: str) -> dict[str, Any] | None:
        self.stats["rest_calls"] += 1
        try:
            return self.fetch(url)
        except Exception as exc:
            self.stats["rest_errors"] += 1
            self.stats["last_error"] = f"{exc.__class__.__name__}: {str(exc)[:120]}"
            return None

    def _klines(self, sym: str, start: int, end: int, source: str = "backfill") -> list[Candle] | None:
        """Closed 1m bars with start <= open_time < end (None: the request failed)."""
        out: dict[int, Candle] = {}
        stop = end - 1
        while stop >= start:
            res = self._get(f"{bb.BYBIT}/kline?category=linear&symbol={sym}&interval=1&start={start}&end={stop}&limit=1000")
            if res is None:
                return None
            rows = res.get("list") or []
            if not rows:
                break
            for r in rows:
                o = int(r[0])
                if start <= o < end:
                    out[o] = candle(sym, r, source)
            oldest = min(int(r[0]) for r in rows)
            if oldest <= start or len(rows) < 1000:
                break
            stop = oldest - 1
        return [out[k] for k in sorted(out)]

    def _tickers(self) -> bool:
        res = self._get(f"{bb.BYBIT}/tickers?category=linear")
        if res is None:
            return False
        now = self.clock()
        wanted = set(self.symbols)
        for d in res.get("list") or []:
            sym = d.get("symbol")
            if sym not in wanted:
                continue
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
            if self.funding is not None and t.get("next_funding_ts") and t.get("funding_rate") is not None:
                self.funding.upsert(sym, int(t["next_funding_ts"]), float(t["funding_rate"]))
        return True

    def _fetch_all(self, start_of: Mapping[str, int], end: int, source: str) -> dict[str, list[Candle]]:
        out: dict[str, list[Candle]] = {}
        with ThreadPoolExecutor(8) as ex:
            futs = {s: ex.submit(self._klines, s, a, end, source) for s, a in start_of.items() if a < end}
            for s, f in futs.items():
                got = f.result()
                if got:
                    out[s] = got
        return out

    # -- catch-up ----------------------------------------------------------------------------------------------------------
    def _catch_up(self) -> None:
        """REST from the last delivered minute to the last closed one, a day at a time, all coins merged."""
        for _ in range(3):
            now_min = int(self.clock() * 1000) // MINUTE * MINUTE
            lo = min((self.delivered[s] + MINUTE) if self.delivered[s] else self.warmup_from_ms for s in self.symbols)
            if lo >= now_min - MINUTE:
                break
            a = lo
            while a < now_min and not self._stop.is_set():
                b = min(a + DAY, now_min)
                starts = {s: max(a, (self.delivered[s] + MINUTE) if self.delivered[s] else self.warmup_from_ms)
                          for s in self.symbols}
                self._tickers()
                got = self._fetch_all(starts, b, "backfill")
                self.stats["bars_backfill"] += self._emit_minutes(got, "backfill")
                a = b

    # -- live ----------------------------------------------------------------------------------------------------------------
    def _live(self) -> None:
        while not self._stop.is_set():
            now = self.clock()
            nxt = (int(now * 1000) // MINUTE + 1) * MINUTE / 1000.0 + POLL_DELAY_S
            self._sleep(max(0.05, nxt - now))
            if self._stop.is_set():
                return
            self.poll_once()

    def poll_once(self) -> None:
        """Collect every coin's closed bars up to the last closed minute and release complete (or overdue) minutes."""
        deadline = self.clock() + RELEASE_AFTER_MS / 1000.0 + 1.0
        while not self._stop.is_set():
            now_ms = int(self.clock() * 1000)
            last_closed = now_ms // MINUTE * MINUTE - MINUTE          # open time of the newest closed minute
            ok = self._tickers()
            need = {s: max((self.delivered[s] + MINUTE) if self.delivered[s] else self.warmup_from_ms,
                           max(self._pending[s]) + MINUTE if self._pending[s] else 0)
                    for s in self.symbols}
            need = {s: a for s, a in need.items() if a <= last_closed}
            got = self._fetch_all(need, last_closed + MINUTE, "live") if need else {}
            if ok or got:
                self.last_poll_ok = self.clock()
            for s, bars in got.items():
                for b in bars:
                    if b.open_time > self.delivered[s]:
                        self._pending[s][b.open_time] = b
            self._release(int(self.clock() * 1000))
            waiting = any(not self._pending[s] or max(self._pending[s]) < last_closed for s in self.symbols
                          if self.delivered[s] < last_closed)
            if not waiting or self.clock() >= deadline:
                return
            self._sleep(RETRY_EVERY_S)

    def _release(self, now_ms: int) -> None:
        """Deliver every minute that is complete, or overdue (RELEASE_AFTER_MS past its close), oldest first. A coin's
        bar for a minute already released without it is delivered on its own, before that coin's newer bars."""
        while True:
            heads = [min(p) for p in self._pending.values() if p]
            if not heads:
                return
            m = min(heads)
            complete = all(m in self._pending[s] or self.delivered[s] >= m for s in self.symbols)
            if not complete and now_ms - (m + MINUTE) < RELEASE_AFTER_MS:
                return
            batch = [self._pending[s].pop(m) for s in self.order if m in self._pending[s]]
            if m <= self.delivered.get(self.anchor, 0):
                self.stats["late_bars"] += len(batch)
            elif not complete:
                self.stats["incomplete_minutes"] += 1
            self.stats["bars_live"] += self._emit(batch, "live")
            self.live_batches += 1
            self.stats["last_batch"] = {"open_ms": m, "coins": len(batch), "complete": complete}
