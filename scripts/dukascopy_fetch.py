"""Download 1-minute BID candles from Dukascopy's public historical datafeed (no account, no key) into data/dukascopy/.

    python scripts/dukascopy_fetch.py XAUUSD 2024-10-01 2026-10-20

Format: one LZMA-compressed .bi5 file per UTC day, 24-byte big-endian records (seconds from the day's start, open,
close, low, high as integers / DIVISOR, volume as float32). Polite: one request at a time, a pause between requests,
backing off on HTTP 429. Days already downloaded are skipped. Output: data/dukascopy/<SYM>_m1.npy (t_ms, o, h, l, c, v).
"""
from __future__ import annotations

import datetime as dt
import lzma
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
DIVISOR = {"XAUUSD": 1000.0, "XAGUSD": 1000.0}
URL = "https://datafeed.dukascopy.com/datafeed/{sym}/{y}/{m:02d}/{d:02d}/BID_candles_min_1.bi5"   # month is 0-based
PAUSE_S, WORKERS = 0.8, 4


def fetch_day(sym: str, day: dt.date) -> bytes | None:
    url = URL.format(sym=sym, y=day.year, m=day.month - 1, d=day.day)
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (PaperLab research)"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 503):
                time.sleep(15 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"giving up on {url}")


def parse(raw: bytes, day: dt.date, div: float) -> np.ndarray:
    if not raw:
        return np.empty((0, 6))
    data = lzma.decompress(raw)
    t0 = int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp() * 1000)
    rows = []
    for off, o, c, lo, hi, v in struct.iter_unpack(">iiiiif", data):
        rows.append((t0 + off * 1000, o / div, hi / div, lo / div, c / div, v))
    return np.array(rows, dtype=float)


def main() -> int:
    sym, a, b = sys.argv[1], dt.date.fromisoformat(sys.argv[2]), dt.date.fromisoformat(sys.argv[3])
    div = DIVISOR.get(sym, 100000.0)
    day_dir = PROJECT / "data" / "dukascopy" / sym
    day_dir.mkdir(parents=True, exist_ok=True)
    days = [a + dt.timedelta(days=k) for k in range((b - a).days)]
    todo = [d for d in days if d.weekday() != 5 and not (day_dir / f"{d.isoformat()}.npy").exists()]   # no Saturdays

    def one(day: dt.date) -> None:
        arr = parse(fetch_day(sym, day) or b"", day, div)
        arr = arr[arr[:, 5] > 0] if len(arr) else arr                    # Dukascopy pads closed minutes with zero volume
        np.save(day_dir / f"{day.isoformat()}.npy", arr)
        time.sleep(PAUSE_S)
        if day.day == 1:
            print("fetched", day, len(arr), flush=True)

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(WORKERS) as pool:                            # the feed answers slowly (~20 s): 4 at a time
        list(pool.map(one, todo))
    parts = [np.load(p) for p in sorted(day_dir.glob("*.npy"))]
    allb = np.concatenate([p for p in parts if len(p)])
    np.save(PROJECT / "data" / "dukascopy" / f"{sym}_m1.npy", allb)
    print("total", len(allb), "candles", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
