"""BYBIT-NATIVE historical data for V5 (docs/V5_DATA_AUDIT.md): public REST v5, no key.

    1m klines           -> archive/klines/<SYM>/<SYM>-1m-<YYYY-MM>.zip        (the replay's tape; Binance column
                           layout so app/backtest/archive.load_klines reads it unchanged: open_time, open, high,
                           low, close, volume, close_time, turnover, 0, 0, 0, 0 -- Bybit klines carry no trade
                           count and no taker volume, so those stay 0 and nothing may read them)
    funding settlements -> archive/fundingRate/<SYM>/<SYM>-fundingRate-<YYYY-MM>.zip   (settle_ts, interval_h, rate)
    positioning (1h)    -> positioning/<SYM>/{oi,premium,mark,index,ratio}_1h.csv
    instrument filters  -> bybit_rules.json (today's snapshot)
    manifest            -> archive/dataset.json (every file's rows and sha256, and the dataset fingerprint)

Every endpoint returns the NEWEST records of [start, end] first, so every reader pages backwards from `end`.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

BYBIT = "https://api.bybit.com/v5/market"
MIN = 60_000
HOUR = 3_600_000


def get(url: str, tries: int = 5) -> dict[str, Any]:
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "paperlab-research"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
            if d.get("retCode") == 0:
                return d.get("result") or {}
            last = str(d.get("retMsg"))
        except Exception as exc:                     # network / rate limit: back off and retry
            last = type(exc).__name__
        time.sleep(0.5 * (i + 1) ** 2)
    raise RuntimeError(f"bybit request failed after {tries} tries: {last} ({url.split('?')[0]})")


def instruments(fetch: Callable[[str], dict[str, Any]] = get) -> dict[str, dict[str, Any]]:
    out, cursor = {}, None
    while True:
        res = fetch(f"{BYBIT}/instruments-info?category=linear&limit=1000" + (f"&cursor={cursor}" if cursor else ""))
        for i in res.get("list") or []:
            if i.get("contractType") == "LinearPerpetual" and i.get("quoteCoin") == "USDT" and i.get("status") == "Trading":
                out[i["symbol"]] = {"tick": i["priceFilter"]["tickSize"], "step": i["lotSizeFilter"]["qtyStep"],
                                    "min_qty": i["lotSizeFilter"]["minOrderQty"],
                                    "min_notional": i["lotSizeFilter"].get("minNotionalValue") or "5",
                                    "max_leverage": (i.get("leverageFilter") or {}).get("maxLeverage") or "20",
                                    "funding_interval_min": int(i.get("fundingInterval") or 480),
                                    "launch_ts": int(i.get("launchTime") or 0), "status": i.get("status")}
        cursor = res.get("nextPageCursor")
        if not cursor:
            return out


def klines(symbol: str, interval: str, start_ms: int, end_ms: int, kind: str = "kline",
           fetch: Callable[[str], dict[str, Any]] = get) -> list[list[Any]]:
    """Bars with start_ms <= open_time < end_ms, ascending. `kind`: kline | mark-price-kline | index-price-kline |
    premium-index-price-kline."""
    out: dict[int, list[Any]] = {}
    end = end_ms - 1
    while end >= start_ms:
        res = fetch(f"{BYBIT}/{kind}?category=linear&symbol={symbol}&interval={interval}&start={start_ms}&end={end}&limit=1000")
        rows = res.get("list") or []
        if not rows:
            break
        for r in rows:
            ts = int(r[0])
            if start_ms <= ts < end_ms:
                out[ts] = r
        oldest = min(int(r[0]) for r in rows)
        if oldest <= start_ms or len(rows) < 1000:
            break
        end = oldest - 1
    return [out[k] for k in sorted(out)]


def funding(symbol: str, start_ms: int, end_ms: int, fetch: Callable[[str], dict[str, Any]] = get) -> list[tuple[int, float]]:
    out: dict[int, float] = {}
    end = end_ms - 1
    while end >= start_ms:
        res = fetch(f"{BYBIT}/funding/history?category=linear&symbol={symbol}&startTime={start_ms}&endTime={end}&limit=200")
        rows = res.get("list") or []
        if not rows:
            break
        for r in rows:
            ts = int(r["fundingRateTimestamp"])
            if start_ms <= ts < end_ms:
                out[ts] = float(r["fundingRate"])
        oldest = min(int(r["fundingRateTimestamp"]) for r in rows)
        if oldest <= start_ms or len(rows) < 200:
            break
        end = oldest - 1
    return sorted(out.items())


def cursor_series(path: str, value_key: str, symbol: str, start_ms: int, end_ms: int, limit: int,
                  fetch: Callable[[str], dict[str, Any]] = get) -> list[tuple[int, float]]:
    """open-interest / account-ratio: newest first, paged with nextPageCursor."""
    out: dict[int, float] = {}
    cursor = None
    while True:
        url = f"{BYBIT}/{path}&symbol={symbol}&startTime={start_ms}&endTime={end_ms - 1}&limit={limit}"
        res = fetch(url + (f"&cursor={cursor}" if cursor else ""))
        for r in res.get("list") or []:
            ts = int(r["timestamp"])
            if start_ms <= ts < end_ms:
                out[ts] = float(r[value_key])
        cursor = res.get("nextPageCursor")
        if not cursor or not res.get("list"):
            return sorted(out.items())


def open_interest(symbol: str, start_ms: int, end_ms: int, fetch: Callable[[str], dict[str, Any]] = get) -> list[tuple[int, float]]:
    return cursor_series("open-interest?category=linear&intervalTime=1h", "openInterest", symbol, start_ms, end_ms, 200, fetch)


def account_ratio(symbol: str, start_ms: int, end_ms: int, fetch: Callable[[str], dict[str, Any]] = get) -> list[tuple[int, float]]:
    return cursor_series("account-ratio?category=linear&period=1h", "buyRatio", symbol, start_ms, end_ms, 500, fetch)


# ---- writers --------------------------------------------------------------------------------------------------

def _zip_csv(path: Path, name: str, rows: Iterable[Sequence[Any]]) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    n = 0
    for r in rows:
        w.writerow(r)
        n += 1
    data = buf.getvalue().encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, data)
    return n, hashlib.sha256(data).hexdigest()


def write_month_klines(root: Path, symbol: str, month: str, bars: Sequence[Sequence[Any]]) -> tuple[int, str]:
    rows = ([int(b[0]), b[1], b[2], b[3], b[4], b[5], int(b[0]) + MIN - 1, b[6], 0, 0, 0, 0] for b in bars)
    return _zip_csv(root / "klines" / symbol / f"{symbol}-1m-{month}.zip", f"{symbol}-1m-{month}.csv", rows)


def write_month_funding(root: Path, symbol: str, month: str, rows: Sequence[tuple[int, float]],
                        interval_h: float) -> tuple[int, str]:
    return _zip_csv(root / "fundingRate" / symbol / f"{symbol}-fundingRate-{month}.zip",
                    f"{symbol}-fundingRate-{month}.csv", ([ts, interval_h, rate] for ts, rate in rows))


def write_series(root: Path, symbol: str, name: str, rows: Iterable[Sequence[Any]]) -> tuple[int, str]:
    p = root / symbol / f"{name}_1h.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    n = 0
    for r in rows:
        w.writerow(r)
        n += 1
    data = buf.getvalue().encode()
    p.write_bytes(data)
    return n, hashlib.sha256(data).hexdigest()


def read_series(root: Path, symbol: str, name: str) -> list[tuple[int, float]]:
    p = root / symbol / f"{name}_1h.csv"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        parts = line.split(",")
        if len(parts) >= 2:
            try:
                out.append((int(parts[0]), float(parts[-1])))
            except ValueError:
                continue
    return out


def month_bounds(month: str) -> tuple[int, int]:
    import datetime as dt
    y, m = (int(x) for x in month.split("-"))
    a = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
    b = dt.datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=dt.timezone.utc)
    return int(a.timestamp() * 1000), int(b.timestamp() * 1000)


def fingerprint(files: Sequence[dict[str, Any]]) -> str:
    blob = json.dumps(sorted((f["path"], f["sha256"]) for f in files))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
