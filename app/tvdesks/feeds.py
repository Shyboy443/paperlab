"""Public hourly spot candles and order filters for the TradingView research desks (GET only, no keys, no orders).

Venues follow the research snapshot (data-coverage.json): Binance spot for 78 coins, Gate for KAS / ONDO / AKT / CRO /
GT, Bitget for BGB. Only COMPLETED hourly candles are returned (open time + 1h <= now).
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from app.config import BITGET_SPOT_REST, GATE_SPOT_REST

H1 = 3_600_000


def _get(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "paperlab-tvdesks"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class Feeds:
    def __init__(self, binance_client: Any = None, clock=time.time, getter=_get, stop: Any = None):
        if binance_client is None:
            import threading

            from app.autoresearch.client import Client
            binance_client = Client(stop or threading.Event())          # the stop event interrupts rate-limit waits
        self.binance = binance_client
        self.clock = clock
        self._get = getter

    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    # -- hourly candles -----------------------------------------------------------------------------------------------
    def hours(self, venue: str, pair: str, start_ms: int, end_ms: int | None = None) -> list[dict]:
        end_ms = min(end_ms or self.now_ms(), self.now_ms())
        fn = {"Binance": self._binance, "Gate": self._gate, "Bitget": self._bitget}[venue]
        out: dict[int, dict] = {}
        cursor = start_ms // H1 * H1
        while cursor < end_ms:
            part = fn(pair, cursor, end_ms)
            if not part:
                break
            for b in part:
                if start_ms <= b["t"] and b["t"] + H1 <= end_ms:
                    out[b["t"]] = b
            nxt = max(b["t"] for b in part) + H1
            if nxt <= cursor:
                break
            cursor = nxt
        return [out[t] for t in sorted(out)]

    def _binance(self, pair: str, start: int, end: int) -> list[dict]:
        rows = self.binance.get("spot", "klines", {"symbol": pair, "interval": "1h", "startTime": start, "endTime": end - 1,
                                                   "limit": 1000}, weight=2)
        return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in rows]

    def _gate(self, pair: str, start: int, end: int) -> list[dict]:
        to = min(end, start + 999 * H1)
        q = urllib.parse.urlencode({"currency_pair": pair, "interval": "1h", "from": start // 1000, "to": to // 1000})
        rows = self._get(f"{GATE_SPOT_REST}/spot/candlesticks?{q}")
        # [time s, quote volume, close, high, low, open, base volume, window closed]
        return [{"t": int(r[0]) * 1000, "o": float(r[5]), "h": float(r[3]), "l": float(r[4]), "c": float(r[2]), "v": float(r[6])}
                for r in rows]

    def _bitget(self, pair: str, start: int, end: int) -> list[dict]:
        # market/candles only serves the most recent ~1000 hours; history-candles pages back from endTime (200 a call)
        to = min(end, start + 200 * H1)
        q = urllib.parse.urlencode({"symbol": pair, "granularity": "1h", "endTime": to, "limit": 200})
        rows = (self._get(f"{BITGET_SPOT_REST}/api/v2/spot/market/history-candles?{q}") or {}).get("data") or []
        # [time ms, open, high, low, close, base volume, usdt volume, quote volume]
        return sorted(({"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])}
                       for r in rows), key=lambda b: b["t"])

    # -- order filters (execution simulation) -------------------------------------------------------------------------
    def filters(self, venue: str, pair: str) -> dict[str, Any]:
        if venue == "Binance":
            info = self.binance.get("spot", "exchangeInfo", {"symbol": pair}, weight=2)
            s = info["symbols"][0]
            f = {x["filterType"]: x for x in s["filters"]}
            lot = f.get("LOT_SIZE") or {}
            notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
            return {"step": float(lot.get("stepSize") or 0), "min_qty": float(lot.get("minQty") or 0),
                    "min_notional": float(notional.get("minNotional") or 0), "source": f"Binance spot exchangeInfo {pair}"}
        if venue == "Gate":
            s = self._get(f"{GATE_SPOT_REST}/spot/currency_pairs/{urllib.parse.quote(pair)}")
            return {"step": 10 ** -int(s.get("amount_precision") or 0), "min_qty": float(s.get("min_base_amount") or 0),
                    "min_notional": float(s.get("min_quote_amount") or 0), "source": f"Gate currency_pairs {pair}"}
        rows = (self._get(f"{BITGET_SPOT_REST}/api/v2/spot/public/symbols?symbol={urllib.parse.quote(pair)}") or {}).get("data") or []
        s = rows[0]
        return {"step": 10 ** -int(s.get("quantityPrecision") or 0), "min_qty": float(s.get("minTradeAmount") or 0),
                "min_notional": float(s.get("minTradeUSDT") or 0), "source": f"Bitget public symbols {pair}"}
