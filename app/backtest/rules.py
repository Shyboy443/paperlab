"""Exchange filters for offline use, fetched from the venue and cached on disk.

Backtests and competitions must size orders against the same tick/step/minNotional/maintMargin the
live engine gets from `ExchangeClient.load_rules()`. That call needs a ccxt client and an event
loop, which a script does not want, so this fetches the same public `exchangeInfo` document over
plain HTTP and caches it under DATA_DIR.

Hosts come from `app.config` (the only module allowed to name one), so the allowlist stays the
single place that decides which endpoints exist. Only public, unauthenticated metadata is read
here; nothing in this module can carry an order.

PRODUCTION vs TESTNET matters and is recorded per cache entry. They genuinely differ -- testnet
publishes a different BTCUSDT `minQty` (0.0001 vs production 0.001) and a different `liquidationFee`
(0.02 vs 0.0125) -- and a competition that means to model real trading must size against production
constraints even though every order is simulated. `environment="production"` is therefore the
default; pass `environment="venue"` to read the venue actually configured.

Maintenance margin does NOT come from here. `exchangeInfo.maintMarginPercent` is 2.5% for every
symbol because it is the generic 20x-tier default, not the symbol's risk bracket. See
app/backtest/brackets.py; `MarketRules.maint_margin_rate` is left as the venue reports it and is
superseded by the bracket table wherever liquidation is computed.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence

from app.config import METADATA_EXCHANGE_INFO
from app.core.types import MarketRules

log = logging.getLogger("paperlab.backtest.rules")

CACHE_NAME = "exchange_rules.json"
DEFAULT_MMR = 0.025

# symbol -> (tick, step, min_qty, min_notional, mmr, price_precision, qty_precision)
FALLBACK: dict[str, tuple[float, float, float, float, float, int, int]] = {
    "BTCUSDT": (0.1, 0.0001, 0.0001, 50.0, 0.025, 2, 3),
    "ETHUSDT": (0.01, 0.001, 0.001, 20.0, 0.025, 2, 3),
    "SOLUSDT": (0.01, 0.01, 0.01, 5.0, 0.025, 4, 2),
}


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse(info: dict[str, Any], wanted: set[str], kind: str) -> dict[str, MarketRules]:
    """Same filter extraction as ExchangeClient.load_rules, over a raw exchangeInfo document."""
    out: dict[str, MarketRules] = {}
    for s in info.get("symbols", []):
        sym = s.get("symbol")
        if sym not in wanted:
            continue
        f = {x["filterType"]: x for x in s.get("filters", [])}
        lot = f.get("MARKET_LOT_SIZE") or f.get("LOT_SIZE") or {}
        mn = f.get("MIN_NOTIONAL") or f.get("NOTIONAL") or {}
        out[sym] = MarketRules(
            sym,
            _f(f.get("PRICE_FILTER", {}).get("tickSize")),
            _f(lot.get("stepSize")) or _f(f.get("LOT_SIZE", {}).get("stepSize")),
            _f(lot.get("minQty")) or _f(f.get("LOT_SIZE", {}).get("minQty")),
            _f(mn.get("notional") or mn.get("minNotional")),
            _f(s.get("maintMarginPercent"), 2.5) / 100.0 if kind == "futures" else 0.0,
            int(s.get("pricePrecision", 8)), int(s.get("quantityPrecision", 8)))
    return out


def fallback_rules(symbols: Sequence[str]) -> dict[str, MarketRules]:
    out: dict[str, MarketRules] = {}
    for s in symbols:
        spec = FALLBACK.get(s)
        out[s] = MarketRules(s, *spec) if spec else MarketRules(s, 0.0001, 0.001, 0.001, 5.0, DEFAULT_MMR, 6, 3)
    return out


def load_rules(settings: Any, symbols: Sequence[str], refresh: bool = False,
               environment: str = "production") -> dict[str, MarketRules]:
    """Real filters for `symbols`: disk cache, else one public fetch, else FALLBACK.

    `environment` is "production" (real trading constraints, the default) or "venue" (whatever
    MODE points at). The choice is part of the cache key, because the two disagree.
    """
    wanted = set(symbols)
    kind = settings.venue.caps.kind
    cache = Path(settings.data_dir) / CACHE_NAME
    if not refresh and cache.exists():
        try:
            raw = json.loads(cache.read_text())
            if raw.get("environment") == environment and raw.get("kind") == kind:
                cached = {s: MarketRules(**r) for s, r in raw.get("rules", {}).items()}
                if wanted <= set(cached):
                    return {s: cached[s] for s in symbols}
        except (OSError, ValueError, TypeError) as exc:
            log.warning("rules cache unreadable (%s); refetching", exc)

    if environment == "production" and kind == "futures":
        url = METADATA_EXCHANGE_INFO
    else:
        path = "/fapi/v1/exchangeInfo" if kind == "futures" else "/api/v3/exchangeInfo"
        url = settings.venue.rest_base.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            info = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("exchangeInfo fetch failed (%s); using FALLBACK filters", exc)
        return fallback_rules(symbols)

    rules = _parse(info, wanted, kind)
    missing = wanted - set(rules)
    if missing:
        log.warning("venue did not list %s; using FALLBACK for those", sorted(missing))
        rules.update({s: fallback_rules([s])[s] for s in missing})
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(
            {"venue": settings.venue.mode, "environment": environment, "kind": kind,
             "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": url,
             "rules": {s: dataclasses.asdict(r) for s, r in rules.items()}}, indent=1))
    except OSError as exc:
        log.warning("could not write rules cache: %s", exc)
    return {s: rules[s] for s in symbols}
