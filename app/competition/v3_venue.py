"""Bybit linear order filters for V3 (the confirmed real-money venue), as PaperLab MarketRules.

Public, read-only: GET /v5/market/instruments-info?category=linear. No key, no signed request. The
snapshot is written once per run into the run's configuration, so a result always states the exact
tick, quantity step, minimum quantity and minimum order value it was costed and filtered with.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

from app.core.types import MarketRules

BYBIT_INSTRUMENTS = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "paperlab-research"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _decimals(x: str) -> int:
    s = str(x)
    return len(s.split(".", 1)[1].rstrip("0")) if "." in s else 0


def fetch_bybit_rules(symbols: Sequence[str], fetch: Callable[[str], Any] = _get) -> dict[str, Any]:
    """{symbol: raw filters} for the requested symbols, following the instruments cursor."""
    wanted, out = set(symbols), {}
    url, cursor = BYBIT_INSTRUMENTS, None
    while True:
        data = fetch(url + (f"&cursor={cursor}" if cursor else ""))
        res = (data or {}).get("result") or {}
        for i in res.get("list") or []:
            if i.get("symbol") in wanted:
                out[i["symbol"]] = {"tick": i["priceFilter"]["tickSize"], "step": i["lotSizeFilter"]["qtyStep"],
                                    "min_qty": i["lotSizeFilter"]["minOrderQty"],
                                    "min_notional": i["lotSizeFilter"].get("minNotionalValue") or "5",
                                    "status": i.get("status"), "launch_ts": int(i.get("launchTime") or 0)}
        cursor = res.get("nextPageCursor")
        if not cursor:
            break
    return {"source": "bybit v5 instruments-info (linear)", "fetched_ts": int(time.time() * 1000), "symbols": out}


def to_rules(snapshot: dict[str, Any]) -> dict[str, MarketRules]:
    out = {}
    for sym, f in (snapshot.get("symbols") or {}).items():
        out[sym] = MarketRules(sym, float(f["tick"]), float(f["step"]), float(f["min_qty"]),
                               float(f["min_notional"]), 0.025, max(_decimals(f["tick"]), 0) + 2,
                               max(_decimals(f["step"]), 0) + 2)
    return out


def load_or_fetch(path: Path, symbols: Sequence[str], refresh: bool = False,
                  fetch: Callable[[str], Any] = _get) -> dict[str, Any]:
    if path.exists() and not refresh:
        snap = json.loads(path.read_text())
        if set(symbols) <= set(snap.get("symbols") or {}):
            return snap
    snap = fetch_bybit_rules(symbols, fetch)
    missing = sorted(set(symbols) - set(snap["symbols"]))
    if missing:
        raise ValueError(f"no Bybit linear instrument for {missing}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=1))
    return snap
