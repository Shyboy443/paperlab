"""Binance USD-M leverage brackets: notional-tiered maintenance margin.

Maintenance margin is NOT one constant per symbol. Binance publishes a ladder of notional brackets,
each with its own maintenance-margin rate and a cumulative maintenance amount that keeps the
liquidation price continuous across tier boundaries:

    maintenance_margin = notional * mmr(notional) - cum_maintenance(notional)

Getting this wrong changes when a book liquidates, which changes every competition result. Two
plausible-looking values are both WRONG and worth naming so nobody reintroduces them:

  * `exchangeInfo.maintMarginPercent` is 2.5% for BTCUSDT, ETHUSDT and SOLUSDT alike. It is the
    generic 20x-tier default, not the symbol's bracket-1 rate. Verified against the bracket table:
    2.5% is BTCUSDT's *bracket 6*, which starts at 12,000,000 USDT of notional.
  * Hand-written per-symbol constants drift and cannot express the tiers at all.

A 20 USDT competition book trades 100-400 USDT of notional, which is bracket 1 for every symbol
here, so bracket 1 is what almost always applies -- but the ladder is implemented properly because
the same code has to be correct if the book ever grows.

FALLBACK below was read from the production bracket endpoint; each entry records when. Prefer a live
fetch, which caches under DATA_DIR with venue, environment, symbol and retrieval timestamp.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from app.config import METADATA_LEVERAGE_BRACKETS

log = logging.getLogger("paperlab.backtest.brackets")

CACHE_NAME = "leverage_brackets.json"
FALLBACK_RETRIEVED = "2026-09-22"

# symbol -> [(notional_cap, mmr, cum_maintenance_amount), ...] ascending by cap.
FALLBACK: dict[str, list[tuple[float, float, float]]] = {
    "BTCUSDT": [(300_000, 0.004, 0.0), (800_000, 0.005, 300.0), (3_000_000, 0.0065, 1_500.0),
                (12_000_000, 0.01, 12_000.0), (70_000_000, 0.02, 132_000.0),
                (100_000_000, 0.025, 482_000.0)],
    "ETHUSDT": [(300_000, 0.004, 0.0), (800_000, 0.005, 300.0), (3_000_000, 0.0065, 1_500.0),
                (12_000_000, 0.01, 12_000.0), (50_000_000, 0.02, 132_000.0),
                (65_000_000, 0.025, 382_000.0)],
    "SOLUSDT": [(50_000, 0.005, 0.0), (400_000, 0.0065, 75.0), (1_000_000, 0.01, 1_475.0),
                (4_000_000, 0.02, 11_475.0), (8_000_000, 0.025, 31_475.0),
                (40_000_000, 0.05, 231_475.0)],
}
# Used only for a symbol with no table at all. Deliberately the harshest bracket-1 rate above, so an
# unknown symbol liquidates sooner rather than later.
UNKNOWN_MMR = 0.005


@dataclass(frozen=True)
class Bracket:
    notional_cap: float
    mmr: float
    cum_maintenance: float


class BracketTable:
    """Notional -> (maintenance margin rate, cumulative maintenance amount)."""

    def __init__(self, by_symbol: dict[str, list[Bracket]] | None = None, source: str = "fallback",
                 retrieved_at: str = FALLBACK_RETRIEVED, environment: str = "production"):
        self.by_symbol = dict(by_symbol or {})
        self.source = source
        self.retrieved_at = retrieved_at
        self.environment = environment

    @classmethod
    def fallback(cls) -> "BracketTable":
        return cls({s: [Bracket(*b) for b in rows] for s, rows in FALLBACK.items()},
                   source="fallback", retrieved_at=FALLBACK_RETRIEVED)

    def bracket_for(self, symbol: str, notional: float) -> Bracket:
        rows = self.by_symbol.get(symbol)
        if not rows:
            return Bracket(float("inf"), UNKNOWN_MMR, 0.0)
        caps = [b.notional_cap for b in rows]
        i = bisect_left(caps, abs(notional))
        return rows[min(i, len(rows) - 1)]

    def mmr(self, symbol: str, notional: float) -> float:
        return self.bracket_for(symbol, notional).mmr

    def maintenance_margin(self, symbol: str, notional: float) -> float:
        b = self.bracket_for(symbol, notional)
        return abs(notional) * b.mmr - b.cum_maintenance

    def first_bracket_mmr(self) -> dict[str, float]:
        """What `ExitEngine.mmr_by_symbol` wants: the rate that applies at competition size."""
        return {s: rows[0].mmr for s, rows in self.by_symbol.items() if rows}

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "retrieved_at": self.retrieved_at,
                "environment": self.environment,
                "brackets": {s: [[b.notional_cap, b.mmr, b.cum_maintenance] for b in rows]
                             for s, rows in self.by_symbol.items()}}


def _parse(payload: dict[str, Any], wanted: set[str]) -> dict[str, list[Bracket]]:
    out: dict[str, list[Bracket]] = {}
    rows = (payload.get("data") or {}).get("brackets") or []
    for row in rows:
        sym = row.get("symbol")
        if sym not in wanted:
            continue
        brackets = []
        for b in row.get("riskBrackets") or []:
            try:
                brackets.append(Bracket(float(b["bracketNotionalCap"]),
                                        float(b["bracketMaintenanceMarginRate"]),
                                        float(b.get("cumFastMaintenanceAmount") or 0.0)))
            except (KeyError, TypeError, ValueError):
                continue
        if brackets:
            out[sym] = sorted(brackets, key=lambda x: x.notional_cap)
    return out


def load(symbols: Sequence[str], data_dir: Path, refresh: bool = False,
         timeout: int = 30) -> BracketTable:
    """Brackets for `symbols`: disk cache, else one public production fetch, else FALLBACK."""
    wanted = set(symbols)
    cache = Path(data_dir) / CACHE_NAME
    if not refresh and cache.exists():
        try:
            raw = json.loads(cache.read_text())
            rows = {s: [Bracket(*b) for b in v] for s, v in raw.get("brackets", {}).items()}
            if wanted <= set(rows):
                return BracketTable(rows, source=raw.get("source", "cache"),
                                    retrieved_at=raw.get("retrieved_at", ""),
                                    environment=raw.get("environment", "production"))
        except (OSError, ValueError, TypeError) as exc:
            log.warning("bracket cache unreadable (%s); refetching", exc)

    try:
        req = urllib.request.Request(METADATA_LEVERAGE_BRACKETS, headers={"User-Agent": "paperlab"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("bracket fetch failed (%s); using FALLBACK read %s", exc, FALLBACK_RETRIEVED)
        return BracketTable.fallback()

    rows = _parse(payload, wanted)
    missing = wanted - set(rows)
    if missing:
        log.warning("no brackets published for %s; using FALLBACK for those", sorted(missing))
        fb = BracketTable.fallback()
        for s in missing:
            if s in fb.by_symbol:
                rows[s] = fb.by_symbol[s]
    table = BracketTable(rows, source="binance_production",
                         retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(table.to_dict(), indent=1))
    except OSError as exc:
        log.warning("could not write bracket cache: %s", exc)
    return table
