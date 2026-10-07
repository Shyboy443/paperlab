"""The tradeable coin universe, derived from exchange metadata rather than a hard-coded list.

A competition of single-market specialists needs more than three symbols, but "more symbols" must
not mean "any symbol". A thin, newly listed contract would hand a bot a market it cannot actually
trade with 20 USDT, or one whose history is too short to walk-forward, and it would pad the
competitor count without adding evidence.

So the universe is built by filtering live USD-M metadata:

    status == TRADING            the contract still exists
    contractType == PERPETUAL    no dated futures
    quoteAsset == USDT           one wallet currency across the arena
    24h quote volume             a liquidity floor, configurable
    notional reachable           a 20 USDT book at the allowed leverage must clear minNotional
                                 AND minQty -- otherwise the bot can never place an order

`PREFERRED` is an ordering hint for picking a small, liquid starting set; it is NOT the universe and
nothing is included because it appears there. A symbol that fails a filter is excluded with its
reason recorded, so "why is DOGE not in the arena" always has an answer.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from app.config import METADATA_EXCHANGE_INFO, METADATA_TICKER_24H

log = logging.getLogger("paperlab.competition.universe")

CACHE_NAME = "universe.json"

# Ordering hint for "give me N liquid markets", most established first. Not a whitelist.
PREFERRED: tuple[str, ...] = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT",
)


@dataclass(frozen=True)
class UniverseFilters:
    min_quote_volume_24h: float = 50_000_000.0   # USDT traded in the last 24h
    max_symbols: int = 12
    require_perpetual: bool = True
    quote_asset: str = "USDT"
    # A bot must be able to place its smallest legal order from a 20 USDT book. Binance checks
    # minNotional on the submitted order (reduce-only exits are exempt), so 1x is the exchange rule;
    # anything above is PaperLab caution and must be chosen explicitly.
    wallet: float = 20.0
    max_leverage: int = 20
    min_notional_safety_multiplier: float = 1.0


@dataclass
class SymbolInfo:
    symbol: str
    base: str = ""
    status: str = ""
    contract_type: str = ""
    tick: float = 0.0
    step: float = 0.0
    min_qty: float = 0.0
    min_notional: float = 0.0
    quote_volume_24h: float = 0.0
    last_price: float = 0.0
    eligible: bool = False
    reason: str = ""

    @property
    def required_notional(self) -> float:
        """Smallest order the exchange will accept, in USDT."""
        by_qty = self.min_qty * self.last_price
        return max(by_qty, self.min_notional)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        d = asdict(self)
        d["required_notional"] = self.required_notional
        return d


def _get(url: str, timeout: int = 45) -> Any | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "paperlab"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("universe fetch failed for %s: %s", url.rsplit("/", 1)[-1], exc)
        return None


def fetch(filters: UniverseFilters | None = None) -> list[SymbolInfo]:
    """Every USD-M symbol with its filters and 24h volume, each marked eligible or not."""
    f = filters or UniverseFilters()
    info = _get(METADATA_EXCHANGE_INFO)
    tickers = _get(METADATA_TICKER_24H)
    if not info:
        return []
    vol = {t.get("symbol"): float(t.get("quoteVolume") or 0.0) for t in (tickers or [])}
    last = {t.get("symbol"): float(t.get("lastPrice") or 0.0) for t in (tickers or [])}

    out: list[SymbolInfo] = []
    for s in info.get("symbols", []):
        flt = {x["filterType"]: x for x in s.get("filters", [])}
        lot = flt.get("MARKET_LOT_SIZE") or flt.get("LOT_SIZE") or {}
        mn = flt.get("MIN_NOTIONAL") or flt.get("NOTIONAL") or {}
        sym = s.get("symbol", "")
        si = SymbolInfo(
            symbol=sym, base=s.get("baseAsset", ""), status=s.get("status", ""),
            contract_type=s.get("contractType", ""),
            tick=float(flt.get("PRICE_FILTER", {}).get("tickSize") or 0),
            step=float(lot.get("stepSize") or 0), min_qty=float(lot.get("minQty") or 0),
            min_notional=float(mn.get("notional") or mn.get("minNotional") or 0),
            quote_volume_24h=vol.get(sym, 0.0), last_price=last.get(sym, 0.0))
        si.eligible, si.reason = _judge(si, f)
        out.append(si)
    out.sort(key=lambda x: -x.quote_volume_24h)
    return out


def _judge(si: SymbolInfo, f: UniverseFilters) -> tuple[bool, str]:
    if si.status != "TRADING":
        return False, f"status {si.status or 'unknown'}"
    if f.require_perpetual and si.contract_type != "PERPETUAL":
        return False, f"contract {si.contract_type or 'unknown'}"
    if not si.symbol.endswith(f.quote_asset):
        return False, f"not {f.quote_asset}-margined"
    if si.quote_volume_24h < f.min_quote_volume_24h:
        return False, (f"24h volume {si.quote_volume_24h/1e6:.1f}M "
                       f"< {f.min_quote_volume_24h/1e6:.0f}M")
    need = max(si.min_qty * si.last_price, si.min_notional * f.min_notional_safety_multiplier)
    reach = f.wallet * f.max_leverage
    if need > reach:
        return False, (f"smallest legal order {need:.0f} USDT > {reach:.0f} reachable "
                       f"from {f.wallet:.0f} at {f.max_leverage}x")
    return True, ""


def eligible(symbols: Sequence[SymbolInfo], limit: int | None = None) -> list[str]:
    """Eligible symbols, PREFERRED ones first, then by liquidity."""
    ok = [s for s in symbols if s.eligible]
    rank = {s: i for i, s in enumerate(PREFERRED)}
    ok.sort(key=lambda s: (rank.get(s.symbol, len(PREFERRED)), -s.quote_volume_24h))
    names = [s.symbol for s in ok]
    return names[:limit] if limit else names


def load(settings: Any, filters: UniverseFilters | None = None,
         refresh: bool = False) -> list[SymbolInfo]:
    """Cached universe. Volume moves, so the cache is a convenience, not a source of truth."""
    f = filters or UniverseFilters()
    cache = Path(settings.data_dir) / CACHE_NAME
    if not refresh and cache.exists():
        try:
            raw = json.loads(cache.read_text())
            rows = [SymbolInfo(**{k: v for k, v in r.items() if k != "required_notional"})
                    for r in raw.get("symbols", [])]
            if rows:
                return rows
        except (OSError, ValueError, TypeError) as exc:
            log.warning("universe cache unreadable (%s); refetching", exc)
    rows = fetch(f)
    if rows:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(
                {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "filters": {k: getattr(f, k) for k in ("min_quote_volume_24h", "max_symbols",
                                                        "wallet", "max_leverage")},
                 "symbols": [s.to_dict() for s in rows]}, indent=1))
        except OSError as exc:
            log.warning("could not write universe cache: %s", exc)
    return rows
