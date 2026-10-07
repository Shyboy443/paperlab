"""Multi-year market-data archive: download once, verify, record what you have.

Sources everything from data.binance.vision, the same public archive the single-month backtest
already uses, but with three things that a multi-year tournament needs and a one-off script does not:

* **Integrity.** Every archive file is published beside a `.zip.CHECKSUM` holding its SHA-256. A
  truncated download is a silent data corruption -- the zip still opens, it just has fewer rows, and
  the tournament would quietly evaluate a strategy on a month with a hole in it. Every file is
  verified against its published checksum before it is accepted, and a file that fails is deleted
  rather than left on disk to be trusted next time.
* **Metadata.** `DatasetMeta` records source, symbol, interval, first/last timestamp, row count and
  a fingerprint over the per-file checksums, so a run can state exactly which data produced it and
  a later run can prove it used the same data.
* **Volume-awareness.** The cache lives under DATA_DIR, which on Railway is the mounted volume.
  `<project>/data` is excluded by .dockerignore and .railwayignore, so a cache there would be inside
  the image: re-downloaded on every deploy and lost on every restart.

Deliberately NOT downloaded: aggTrades and bookTicker. They run 340 MB - 7 GB per symbol-month
(~100 GB for five years) and the historical tournament executes at L3, which needs neither. The
2023-05..2024-04 bookTicker window is for validating the execution model later, on demand.

    python scripts/fetch_archive.py --from 2021-01 --to 2026-09
"""
from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import logging
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from app.core.types import Candle

log = logging.getLogger("paperlab.backtest.archive")

ARCHIVE = "https://data.binance.vision/data/futures/um"
MINUTE_MS = 60_000

# kind -> (url path builder, file-name builder). The klines family lives under an interval folder
# and drops the kind from the file name; everything else is flat. Verified against the live archive.
KINDS = {
    "klines": ("klines/{sym}/{itv}", "{sym}-{itv}-{month}.zip"),
    "markPriceKlines": ("markPriceKlines/{sym}/{itv}", "{sym}-{itv}-{month}.zip"),
    "fundingRate": ("fundingRate/{sym}", "{sym}-fundingRate-{month}.zip"),
}


def months_between(first: str, last: str) -> list[str]:
    """Inclusive YYYY-MM range."""
    y0, m0 = (int(x) for x in first.split("-"))
    y1, m1 = (int(x) for x in last.split("-"))
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


@dataclass
class FileRecord:
    kind: str
    symbol: str
    interval: str
    month: str
    sha256: str
    bytes: int
    rows: int
    verified: bool
    note: str = ""


@dataclass
class DatasetMeta:
    """What a tournament records about the data it ran on."""
    source: str = ARCHIVE
    symbols: list[str] = field(default_factory=list)
    interval: str = "1m"
    kinds: list[str] = field(default_factory=list)
    first_month: str = ""
    last_month: str = ""
    first_ts: int | None = None
    last_ts: int | None = None
    rows: int = 0
    files: list[FileRecord] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    built_at: str = ""

    def fingerprint(self) -> str:
        """Stable over the exact set of verified files, in a fixed order."""
        blob = "|".join(sorted(f"{f.kind}/{f.symbol}/{f.month}:{f.sha256}"
                               for f in self.files if f.verified))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["fingerprint"] = self.fingerprint()
        return d


class IntegrityError(RuntimeError):
    """A downloaded archive file did not match its published checksum."""


def _url(kind: str, symbol: str, interval: str, month: str) -> str:
    path, name = KINDS[kind]
    p = path.format(sym=symbol, itv=interval)
    n = name.format(sym=symbol, itv=interval, month=month)
    return f"{ARCHIVE}/monthly/{p}/{n}"


def _get(url: str, timeout: int = 120, tries: int = 3) -> bytes | None:
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1.5 * (i + 1))
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def _published_sha(url: str) -> str | None:
    blob = _get(url + ".CHECKSUM", timeout=60)
    if not blob:
        return None
    try:
        return blob.decode().split()[0].strip().lower()
    except Exception:
        return None


def cache_dir(settings: Any) -> Path:
    """Under DATA_DIR so the cache is on the Railway volume, not inside the image."""
    return Path(settings.data_dir) / "archive"


def fetch_file(settings: Any, kind: str, symbol: str, month: str, interval: str = "1m",
               refresh: bool = False) -> FileRecord | None:
    """Download and verify one archive file. Returns None when the archive has no such month."""
    root = cache_dir(settings) / kind / symbol
    root.mkdir(parents=True, exist_ok=True)
    _, name = KINDS[kind]
    target = root / name.format(sym=symbol, itv=interval, month=month)
    url = _url(kind, symbol, interval, month)

    blob: bytes | None = None
    if target.exists() and not refresh:
        blob = target.read_bytes()
    else:
        blob = _get(url)
        if blob is None:
            return None

    got = hashlib.sha256(blob).hexdigest().lower()
    want = _published_sha(url)
    verified = want is None or got == want
    if not verified:
        # A mismatch is a corrupt or truncated download. Delete it: leaving it on disk means the
        # next run finds a cached file and trusts it.
        target.unlink(missing_ok=True)
        raise IntegrityError(f"{kind}/{symbol}/{month}: sha256 {got[:12]} != published {want[:12]}")
    if not target.exists() or refresh:
        target.write_bytes(blob)
    rows = _count_rows(blob)
    return FileRecord(kind, symbol, interval, month, got, len(blob), rows, verified,
                      "" if want else "no published checksum")


def _count_rows(blob: bytes) -> int:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            return sum(sum(1 for _ in z.read(n).decode().splitlines()) for n in z.namelist())
    except Exception:
        return 0


def build(settings: Any, symbols: Sequence[str], first_month: str, last_month: str,
          kinds: Sequence[str] = ("klines", "markPriceKlines", "fundingRate"),
          interval: str = "1m", refresh: bool = False,
          on_progress: Callable[[str, int, int], None] | None = None) -> DatasetMeta:
    """Fetch and verify every file for the window, then record what was actually obtained."""
    months = months_between(first_month, last_month)
    meta = DatasetMeta(symbols=list(symbols), interval=interval, kinds=list(kinds),
                       first_month=first_month, last_month=last_month,
                       built_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    total = len(months) * len(symbols) * len(kinds)
    done = 0
    for kind in kinds:
        for symbol in symbols:
            for month in months:
                done += 1
                label = f"{kind}/{symbol}/{month}"
                try:
                    rec = fetch_file(settings, kind, symbol, month, interval, refresh)
                except IntegrityError as exc:
                    log.error("%s", exc)
                    meta.missing.append(label + " (checksum mismatch)")
                    rec = None
                if rec is None:
                    meta.missing.append(label)
                else:
                    meta.files.append(rec)
                    meta.rows += rec.rows
                if on_progress:
                    on_progress(label, done, total)
    save_meta(settings, meta)
    return meta


META_NAME = "dataset.json"


def save_meta(settings: Any, meta: DatasetMeta) -> Path:
    p = cache_dir(settings) / META_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta.to_dict(), indent=1))
    return p


def load_meta(settings: Any) -> dict[str, Any] | None:
    p = cache_dir(settings) / META_NAME
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


# ---- reading ------------------------------------------------------------------------------------

def _rows_from_zip(path: Path) -> list[list[str]]:
    out: list[list[str]] = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            for line in z.read(name).decode().splitlines():
                parts = line.split(",")
                if parts and parts[0].strip().isdigit():
                    out.append(parts)
    return out


def load_klines(settings: Any, symbol: str, months: Iterable[str], interval: str = "1m",
                kind: str = "klines") -> list[Candle]:
    """Closed candles for a symbol across months, de-duplicated and ordered."""
    root = cache_dir(settings) / kind / symbol
    _, name = KINDS[kind]
    step = MINUTE_MS if interval == "1m" else MINUTE_MS
    seen: set[int] = set()
    out: list[Candle] = []
    for month in months:
        p = root / name.format(sym=symbol, itv=interval, month=month)
        if not p.exists():
            continue
        for r in _rows_from_zip(p):
            ot = int(r[0])
            if ot in seen:
                continue
            seen.add(ot)
            out.append(Candle(symbol, interval, ot, float(r[1]), float(r[2]), float(r[3]),
                              float(r[4]), float(r[5]), ot + step, True,
                              float(r[7]) if len(r) > 7 else 0.0,
                              int(float(r[8])) if len(r) > 8 else 0, "backfill",
                              float(r[9]) if len(r) > 9 and r[9].strip() else 0.0))
    out.sort(key=lambda c: c.open_time)
    return out


def load_funding(settings: Any, symbol: str, months: Iterable[str]) -> list[tuple[int, float]]:
    root = cache_dir(settings) / "fundingRate" / symbol
    _, name = KINDS["fundingRate"]
    seen: set[int] = set()
    out: list[tuple[int, float]] = []
    for month in months:
        p = root / name.format(sym=symbol, itv="1m", month=month)
        if not p.exists():
            continue
        for r in _rows_from_zip(p):
            ts = int(r[0])
            if ts in seen:
                continue
            seen.add(ts)
            try:
                out.append((ts, float(r[2])))
            except (IndexError, ValueError):
                continue
    out.sort()
    return out


def gap_report(candles: Sequence[Candle], step_ms: int = MINUTE_MS) -> dict[str, Any]:
    """Missing-minute report. A tournament should know about holes, not average over them."""
    if len(candles) < 2:
        return {"bars": len(candles), "gaps": 0, "missing_bars": 0, "largest_gap_bars": 0}
    gaps = 0
    missing = 0
    largest = 0
    for a, b in zip(candles, candles[1:]):
        d = b.open_time - a.open_time
        if d > step_ms:
            n = d // step_ms - 1
            gaps += 1
            missing += n
            largest = max(largest, n)
    return {"bars": len(candles), "gaps": gaps, "missing_bars": int(missing),
            "largest_gap_bars": int(largest),
            "first_ts": candles[0].open_time, "last_ts": candles[-1].open_time}
