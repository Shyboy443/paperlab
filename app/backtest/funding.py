"""Historical funding rates from the public Binance archive, cached beside the klines.

The spec for a perpetual-futures competition is explicit that funding must be real, not a constant:
a bot that holds a long through three days of positive funding in a crowded market pays for it, and
that cost belongs in its net result. This reads the same `data.binance.vision` archive the 1m klines
come from, so funding and price history are always the same dataset version.

Archive layout:
    <ARCHIVE>/monthly/fundingRate/<SYMBOL>/<SYMBOL>-fundingRate-<YYYY-MM>.zip
    -> CSV: calc_time, funding_interval_hours, last_funding_rate

`calc_time` is the settlement instant in epoch ms. A position open at that instant settles against
`last_funding_rate`; the live engine does the same thing from the funding websocket when
`next_funding_ts` rolls over (engine_dispatch.on_funding).
"""
from __future__ import annotations

import io
import logging
import urllib.error
import urllib.request
import zipfile
from bisect import bisect_left
from pathlib import Path
from typing import Sequence

log = logging.getLogger("paperlab.backtest.funding")

ARCHIVE = "https://data.binance.vision/data/futures/um"


class FundingSchedule:
    """Settlement instants and rates per symbol, queried by time window."""

    def __init__(self, by_symbol: dict[str, list[tuple[int, float]]] | None = None):
        # each list is sorted by timestamp
        self.by_symbol: dict[str, list[tuple[int, float]]] = dict(by_symbol or {})

    def __bool__(self) -> bool:
        return any(self.by_symbol.values())

    def events(self, symbol: str, after_ms: int, until_ms: int) -> list[tuple[int, float]]:
        """Settlements in (after_ms, until_ms]. Empty when the symbol has no loaded history."""
        rows = self.by_symbol.get(symbol)
        if not rows:
            return []
        i = bisect_left(rows, (after_ms + 1, float("-inf")))
        out = []
        while i < len(rows) and rows[i][0] <= until_ms:
            out.append(rows[i])
            i += 1
        return out

    def coverage(self) -> dict[str, int]:
        return {s: len(v) for s, v in self.by_symbol.items()}


def _download(url: str, timeout: int = 60) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError) as exc:
        log.warning("funding download failed %s (%s)", url, exc)
        return None


def _rows_from_zip(blob: bytes) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            for line in z.read(name).decode().splitlines():
                parts = line.split(",")
                if len(parts) < 3 or not parts[0].strip().isdigit():
                    continue            # header row
                try:
                    out.append((int(parts[0]), float(parts[2])))
                except ValueError:
                    continue
    return out


def load(symbols: Sequence[str], months: Sequence[str], cache_dir: Path) -> FundingSchedule:
    """Funding history for `symbols` over `months` (YYYY-MM), cached as downloaded zips."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    by_symbol: dict[str, list[tuple[int, float]]] = {}
    for symbol in symbols:
        rows: list[tuple[int, float]] = []
        for month in months:
            cached = cache_dir / f"{symbol}-fundingRate-{month}.zip"
            if not cached.exists():
                blob = _download(f"{ARCHIVE}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip")
                if blob is None:
                    continue
                cached.write_bytes(blob)
            rows.extend(_rows_from_zip(cached.read_bytes()))
        seen: set[int] = set()
        uniq = []
        for ts, rate in sorted(rows):
            if ts not in seen:
                seen.add(ts)
                uniq.append((ts, rate))
        by_symbol[symbol] = uniq
        if not uniq:
            log.warning("no funding history for %s over %s; positions will settle nothing",
                        symbol, ",".join(months))
    return FundingSchedule(by_symbol)
