"""Public Binance spot data only. No credentials or order endpoints."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from app.core.types import Candle
from app.video_breakout.engine import STEP

ARCHIVE = "https://data.binance.vision/data/spot/monthly/klines"
MARKET = "https://data-api.binance.vision/api/v3"


def utc_ms(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def months(first_ms: int, end_ms: int) -> list[str]:
    first = datetime.fromtimestamp(first_ms / 1000, timezone.utc)
    last = datetime.fromtimestamp((end_ms - 1) / 1000, timezone.utc)
    y, m = first.year, first.month
    out = []
    while (y, m) <= (last.year, last.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def bar_from_row(row: list, symbol: str, *, closed: bool = True) -> Candle:
    # Binance spot archives switched to microseconds in January 2025.
    ts = int(row[0])
    ts = ts // 1000 if ts > 100_000_000_000_000 else ts
    if len(row) > 6:
        end = int(row[6])
        end = end // 1000 if end > 100_000_000_000_000 else end
        if end != ts + STEP - 1:
            raise ValueError("Source row is not a full 4h interval")
    return Candle(symbol, "4h", ts, *[float(row[i]) for i in range(1, 6)],
                  ts + STEP, closed=closed, source="backfill")


def fetch_history(symbol: str, start_ms: int, end_ms: int, cache: Path) -> tuple[list[Candle], list[dict]]:
    if not re.fullmatch(r"[A-Z0-9]+USDT", symbol):
        raise ValueError("Invalid symbol")
    cache = cache / symbol
    cache.mkdir(parents=True, exist_ok=True)

    def fetch_month(month):
        name = f"{symbol}-4h-{month}.zip"
        path = cache / name
        sha_path = cache / (name + ".CHECKSUM")
        url = f"{ARCHIVE}/{symbol}/4h/{name}"
        blob = path.read_bytes() if path.exists() else get(url)
        checksum = sha_path.read_bytes() if sha_path.exists() else get(url + ".CHECKSUM")
        actual = hashlib.sha256(blob).hexdigest()
        if actual != checksum.decode().split()[0]:
            raise ValueError(f"Checksum mismatch for {name}; refusing data")
        path.write_bytes(blob)
        sha_path.write_bytes(checksum)
        rows = []
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for member in z.namelist():
                for row in csv.reader(io.StringIO(z.read(member).decode())):
                    if row and row[0].isdigit():
                        rows.append(bar_from_row(row, symbol))
        return rows, {"file": name, "url": url, "sha256": actual, "rows": len(rows)}

    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = list(pool.map(fetch_month, months(start_ms, end_ms)))
    bars = sorted([b for rows, _ in fetched for b in rows if start_ms <= b.open_time and b.close_time <= end_ms],
                  key=lambda b: b.open_time)
    if not bars or bars[0].open_time != start_ms or bars[-1].close_time != end_ms:
        raise ValueError("Archives do not cover the full requested range")
    return bars, [meta for _, meta in fetched]


def recent_bars(symbol: str) -> tuple[int, list[Candle]]:
    before = int(json.loads(get(MARKET + "/time"))["serverTime"])
    query = urllib.parse.urlencode({"symbol": symbol, "interval": "4h", "limit": 100})
    rows = json.loads(get(MARKET + "/klines?" + query))
    bars = [bar_from_row(row, symbol, closed=int(row[0]) + STEP <= before) for row in rows]
    observed = int(json.loads(get(MARKET + "/time"))["serverTime"])
    # Do not mix a request spanning a candle boundary with stale forming-bar data.
    if before // STEP != observed // STEP:
        raise ValueError("Request spanned a 4h boundary; retry on the next poll")
    return observed, bars
