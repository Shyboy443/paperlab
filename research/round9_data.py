"""Data fetchers for round 9: cross-venue perpetual basis.

Public endpoints only. Results are cached as JSON lines under
`research/data/round9/`.

The 'index' (a fair-price reference) is the OKX `index-tickers` field.
The 'mark' (tradeable perp) is the OKX `tickers` field with `instId`
ending in `-USDT-SWAP`. We additionally fetch Binance USDⓈ-M perp
markPrice and Bybit v5 `mark_price_kline` for cross-venue comparison.
"""
from __future__ import annotations
import json, time, urllib.request, urllib.error, ssl
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "data" / "round9"
CACHE.mkdir(parents=True, exist_ok=True)

UA = "paperlab-research/0.1"
TIMEOUT = 20

def _http_get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context()) as r:
        return json.loads(r.read().decode("utf-8"))

def fetch_okx_1m_marks(inst_id: str, pages: int = 600) -> list[dict]:
    """Fetch historical OKX 1-minute mark-price candles for a perp, paginated
    backwards via the history endpoint (the live endpoint caps at ~24h)."""
    out_path = CACHE / f"okx_{inst_id.replace('-','_')}_mark_1m.jsonl"
    if out_path.exists():
        return [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
    out = []
    after = None
    base = "https://www.okx.com/api/v5/market/history-mark-price-candles"
    for _ in range(pages):
        url = (f"{base}?instId={inst_id}&bar=1m&limit=100"
               + (f"&after={after}" if after else ""))
        try:
            data = _http_get_json(url)
        except urllib.error.HTTPError as e:
            if e.code in (429, 418): time.sleep(2); continue
            raise
        rows = data.get("data", [])
        if not rows: break
        for row in rows:
            out.append({"ts": int(row[0]), "mark": float(row[4])})
        after = int(rows[-1][0])
        if len(rows) < 100: break
        time.sleep(0.1)
    out_path.write_text("\n".join(json.dumps(r) for r in out))
    return out

def fetch_binance_1m_marks(symbol: str, limit: int = 1000) -> list[dict]:
    """Binance USDⓈ-M mark price klines (1m). Single page; the endpoint
    is historical-only up to 1000 most recent bars."""
    out_path = CACHE / f"binance_{symbol}_mark_1m.jsonl"
    if out_path.exists():
        return [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
    url = f"https://fapi.binance.com/fapi/v1/markPriceKlines?symbol={symbol}&interval=1m&limit={limit}"
    data = _http_get_json(url)
    out = [{"ts": int(r[0]), "mark": float(r[4])} for r in data]
    out_path.write_text("\n".join(json.dumps(r) for r in out))
    return out

def fetch_bybit_1m_marks(symbol: str, limit: int = 1000) -> list[dict]:
    """Bybit v5 mark-price kline (linear perp). Returns ~1000 most recent bars."""
    out_path = CACHE / f"bybit_{symbol}_mark_1m.jsonl"
    if out_path.exists():
        return [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
    url = (f"https://api.bybit.com/v5/market/mark-price-kline?category=linear"
           f"&symbol={symbol}&interval=1&limit={limit}")
    data = _http_get_json(url)
    rows = (data.get("result") or {}).get("list", [])
    # Bybit returns newest-first in [ts, o, h, l, c, ...] format.
    out = [{"ts": int(r[0]), "mark": float(r[4])} for r in rows][::-1]
    out_path.write_text("\n".join(json.dumps(r) for r in out))
    return out

def fetch_okx_index_1m(swap_inst_id: str, pages: int = 100) -> list[dict]:
    """OKX 1-minute index candles. Same pagination as mark candles, but for
    the spot index used to mark the perp fair-price."""
    out_path = CACHE / f"okx_{swap_inst_id.replace('-','_')}_index_1m.jsonl"
    if out_path.exists():
        return [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
    base = swap_inst_id.split("-USDT-SWAP")[0]
    inst = f"{base}-USD"
    out = []
    after = None
    for _ in range(pages):
        url = (f"https://www.okx.com/api/v5/market/index-candles?instId={inst}"
               f"&bar=1m&limit=100" + (f"&after={after}" if after else ""))
        try:
            data = _http_get_json(url)
        except urllib.error.HTTPError as e:
            if e.code in (429, 418): time.sleep(2); continue
            raise
        rows = data.get("data", [])
        if not rows: break
        for row in rows:
            out.append({"ts": int(row[0]), "index": float(row[4])})
        after = int(rows[-1][0])
        if len(rows) < 100: break
        time.sleep(0.1)
    out_path.write_text("\n".join(json.dumps(r) for r in out))
    return out
