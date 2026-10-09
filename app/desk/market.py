"""Trading Desk market data: public candles with a source fallback, and scan().

Sources, tried in order (all public, no key): Binance's public market-data host (data-api.binance.vision -- market data
only, no account or order endpoint exists there), then Bybit spot, then OKX spot. A candle list always comes from ONE
source, which is reported with it, so no series mixes venues.

scan() ranks the coin universe by the 1h relative volume and returns, per coin, the real numbers the agents may quote.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Sequence

from app.desk import indicators as ind

log = logging.getLogger("paperlab.desk.market")

DEFAULT_UNIVERSE = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "TON")
MIN = 60_000
TF_MS = {"1m": MIN, "1h": 60 * MIN, "4h": 240 * MIN, "1d": 1440 * MIN}
UA = {"User-Agent": "PaperLab-TradingDesk/1.0 (paper trading research)"}

Fetch = Callable[[str], Any]


def http_json(url: str, timeout: float = 12.0) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _binance(fetch: Fetch, coin: str, tf: str, limit: int, end_ms: int | None) -> list[dict]:
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={coin}USDT&interval={tf}&limit={min(limit, 1000)}"
    if end_ms:
        url += f"&endTime={end_ms - 1}"
    return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])}
            for r in fetch(url)]


def _bybit(fetch: Fetch, coin: str, tf: str, limit: int, end_ms: int | None) -> list[dict]:
    iv = {"1m": "1", "1h": "60", "4h": "240", "1d": "D"}[tf]
    url = (f"https://api.bybit.com/v5/market/kline?category=spot&symbol={coin}USDT&interval={iv}"
           f"&limit={min(limit, 1000)}")
    if end_ms:
        url += f"&end={end_ms - 1}"
    rows = (fetch(url).get("result") or {}).get("list") or []
    return sorted(({"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
                    "v": float(r[5])} for r in rows), key=lambda x: x["t"])


def _okx(fetch: Fetch, coin: str, tf: str, limit: int, end_ms: int | None) -> list[dict]:
    bar = {"1m": "1m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}[tf]
    path = "history-candles" if end_ms else "candles"
    url = f"https://www.okx.com/api/v5/market/{path}?instId={coin}-USDT&bar={bar}&limit={min(limit, 300)}"
    if end_ms:
        url += f"&after={end_ms}"
    rows = fetch(url).get("data") or []
    return sorted(({"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
                    "v": float(r[5])} for r in rows), key=lambda x: x["t"])


SOURCES = (("binance", _binance), ("bybit", _bybit), ("okx", _okx))


class Market:
    """Candles with a fallback chain and a short cache (a session reads the same series several times)."""

    def __init__(self, fetch: Fetch | None = None, clock: Callable[[], float] = time.time, cache_s: float = 50.0):
        self.fetch = fetch or http_json
        self.clock = clock
        self.cache_s = cache_s
        self._cache: dict[tuple, tuple[float, tuple[str, list[dict]]]] = {}
        self.errors: dict[str, str] = {}

    def candles(self, coin: str, tf: str, limit: int = 200, end_ms: int | None = None,
                closed_only: bool = True) -> tuple[str, list[dict]]:
        """(source, candles) oldest first. With closed_only the forming candle is dropped."""
        key = (coin, tf, limit, end_ms, closed_only)
        hit = self._cache.get(key)
        if hit and self.clock() - hit[0] < self.cache_s:
            return hit[1]
        last_err = None
        for name, fn in SOURCES:
            try:
                rows = fn(self.fetch, coin.upper(), tf, limit + 1, end_ms)
            except Exception as exc:                       # next source
                last_err = f"{name}: {type(exc).__name__}: {str(exc)[:120]}"
                self.errors[name] = last_err
                continue
            if not rows:
                last_err = f"{name}: no candles for {coin}"
                continue
            if closed_only:
                now_ms = int(self.clock() * 1000)
                rows = [r for r in rows if r["t"] + TF_MS[tf] <= now_ms]
            out = (name, rows[-limit:])
            self._cache[key] = (self.clock(), out)
            return out
        raise RuntimeError(f"no market data for {coin} {tf} ({last_err})")

    def price(self, coin: str) -> tuple[str, float]:
        src, rows = self.candles(coin, "1m", limit=2, closed_only=False)
        return src, rows[-1]["c"]

    def minutes(self, coin: str, start_ms: int, end_ms: int) -> tuple[str, list[dict]]:
        """Closed 1m candles with start_ms <= t < end_ms (paged backwards, at most 10 pages)."""
        out: dict[int, dict] = {}
        src = ""
        cursor = end_ms
        for _ in range(10):
            src, rows = self.candles(coin, "1m", limit=1000, end_ms=cursor)
            rows = [r for r in rows if start_ms <= r["t"] < end_ms]
            for r in rows:
                out[r["t"]] = r
            if not rows or rows[0]["t"] <= start_ms:
                break
            cursor = rows[0]["t"]
        return src, [out[k] for k in sorted(out)]

    # -- scan ---------------------------------------------------------------------------------------------------------
    def scan_coin(self, coin: str) -> dict[str, Any]:
        src1, h1 = self.candles(coin, "1h", 200)
        src4, h4 = self.candles(coin, "4h", 200)
        f1, f4 = ind.frame_summary(h1), ind.frame_summary(h4)
        if not (f1.get("ok") and f4.get("ok")):
            return {"coin": coin, "ok": False, "reason": f1.get("reason") or f4.get("reason")}
        _, live = self.price(coin)
        for f in (f1, f4):                             # levels relative to the live price: support is always below it
            f["levels"] = ind.nearest_levels(live, f.pop("swing_highs"), f.pop("swing_lows"), 4)
        day = h1[-24:]
        change_24h = 100 * (live / day[0]["o"] - 1) if day else None
        flags = []
        if (f1["rel_volume"] or 0) >= 2.0:
            flags.append("volume_spike_1h")
        if (f4["rel_volume"] or 0) >= 2.0:
            flags.append("volume_spike_4h")
        for tf, f in (("1h", f1), ("4h", f4)):
            if f["rsi14"] is not None and f["rsi14"] >= 70:
                flags.append(f"overbought_{tf}")
            if f["rsi14"] is not None and f["rsi14"] <= 30:
                flags.append(f"oversold_{tf}")
        if f1["trend"] == f4["trend"] and f1["trend"] != "mixed":
            flags.append(f"trend_aligned_{f1['trend']}")
        return {"coin": coin, "ok": True, "source": src1 if src1 == src4 else f"{src1}/{src4}", "price": live,
                "change_24h_pct": change_24h, "h1": f1, "h4": f4, "round_numbers": ind.round_numbers(live),
                "flags": flags, "as_of_ms": int(self.clock() * 1000)}

    def scan(self, universe: Sequence[str] = DEFAULT_UNIVERSE) -> list[dict[str, Any]]:
        """Every coin's numbers, ranked by 1h relative volume (then 4h). Coins whose data failed come last, marked."""
        def one(coin: str) -> dict[str, Any]:
            try:
                return self.scan_coin(coin)
            except Exception as exc:
                return {"coin": coin, "ok": False, "reason": str(exc)[:200]}
        with ThreadPoolExecutor(min(8, max(1, len(universe)))) as ex:
            rows = list(ex.map(one, universe))
        ok = [r for r in rows if r.get("ok")]
        ok.sort(key=lambda r: (-(r["h1"]["rel_volume"] or 0), -(r["h4"]["rel_volume"] or 0)))
        return ok + [r for r in rows if not r.get("ok")]
