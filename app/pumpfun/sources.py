"""Parsing PumpPortal stream messages, and the two free REST sources (DexScreener prices, GeckoTerminal holders)."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Callable

from app.config import DEXSCREENER_REST, GECKOTERMINAL_REST

# addresses that arrive as a "mint" but are pump.fun programs, not tokens (seen on the migration stream 2026-10-17)
NOT_TOKENS = frozenset({"pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn", "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"})
_B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def valid_mint(m: Any) -> bool:
    return isinstance(m, str) and bool(_B58.match(m)) and m not in NOT_TOKENS


def parse(msg: dict[str, Any], t: int) -> tuple[str, dict[str, Any]] | None:
    """('token', row) for a creation, ('migration', row) for a graduation, None for anything else."""
    tx = msg.get("txType")
    mint = msg.get("mint")
    if not valid_mint(mint):
        return None
    if tx == "create":
        return "token", {"mint": mint, "t": t, "creator": msg.get("traderPublicKey"), "name": str(msg.get("name") or "")[:80],
                         "symbol": str(msg.get("symbol") or "")[:24], "initial_buy": _f(msg.get("initialBuy")),
                         "sol_amount": _f(msg.get("solAmount")), "v_sol": _f(msg.get("vSolInBondingCurve")),
                         "v_tokens": _f(msg.get("vTokensInBondingCurve")), "mcap_sol": _f(msg.get("marketCapSol")),
                         "uri": str(msg.get("uri") or "")[:200], "pool": msg.get("pool"), "sig": msg.get("signature")}
    if tx == "migrate":
        return "migration", {"mint": mint, "t": t, "pool": msg.get("pool"), "sig": msg.get("signature")}
    return None


def http_json(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "paperlab-research", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def dexscreener_prices(mints: list[str], t: int, getter: Callable[[str], Any] = http_json) -> dict[str, dict[str, Any]]:
    """Up to 30 mints per call. Per mint: the deepest PumpSwap pair (else the deepest pair) as a price row."""
    out: dict[str, dict[str, Any]] = {}
    if not mints:
        return out
    pairs = getter(f"{DEXSCREENER_REST}/tokens/v1/solana/{','.join(urllib.parse.quote(m) for m in mints)}") or []
    best: dict[str, dict] = {}
    for p in pairs:
        mint = ((p.get("baseToken") or {}).get("address"))
        if mint not in mints:
            continue
        liq = _f((p.get("liquidity") or {}).get("usd")) or 0.0
        rank = (p.get("dexId") == "pumpswap", liq)
        cur = best.get(mint)
        if cur is None or rank > cur["_rank"]:
            best[mint] = {**p, "_rank": rank}
    for mint, p in best.items():
        liq, vol, tx = p.get("liquidity") or {}, p.get("volume") or {}, (p.get("txns") or {}).get("m5") or {}
        out[mint] = {"mint": mint, "t": t, "price_usd": _f(p.get("priceUsd")), "price_native": _f(p.get("priceNative")),
                     "liq_usd": _f(liq.get("usd")), "liq_base": _f(liq.get("base")), "liq_quote": _f(liq.get("quote")),
                     "fdv": _f(p.get("fdv")), "vol_m5": _f(vol.get("m5")), "buys_m5": tx.get("buys"), "sells_m5": tx.get("sells"),
                     "pair": p.get("pairAddress"), "dex": p.get("dexId"),
                     "quote_symbol": (p.get("quoteToken") or {}).get("symbol")}
    return out


def geckoterminal_holders(mint: str, getter: Callable[[str], Any] = http_json) -> tuple[int | None, float | None]:
    """(holder count, top-10 share in %) as GeckoTerminal reports it (the pool's own account included)."""
    d = getter(f"{GECKOTERMINAL_REST}/networks/solana/tokens/{urllib.parse.quote(mint)}/info")
    h = (((d or {}).get("data") or {}).get("attributes") or {}).get("holders") or {}
    top10 = _f((h.get("distribution_percentage") or {}).get("top_10"))
    count = h.get("count")
    return (int(count) if isinstance(count, (int, float)) else None), top10
