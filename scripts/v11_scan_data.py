"""V11 SCAN data: the scanner universe and its 1m history (docs/V11_PROTOCOL.md).

    python scripts/v11_scan_data.py [--days 95] [--top 30]

Universe (fixed by rule, not by results): Bybit linear USDT perpetuals that are Trading, listed >= 120 days, crypto
(not a stablecoin, and not one of Bybit's tokenised stocks / ETFs / commodities), ranked by the MEDIAN daily turnover of the last 30 days (not today's, which
favours whatever pumped today), top N. BTC is always loaded as market context. Writes data/v11_scan/universe.json and
one cached 1m file per symbol (data/v11_scan/<SYMBOL>.npy: open_ms, open, high, low, close, volume, turnover).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import bybit_archive as bb  # noqa: E402

OUT = PROJECT / "data" / "v11_scan"
DAY = 86_400_000
MIN = 60_000
EXCLUDE = {"USDCUSDT", "USDEUSDT", "FDUSDUSDT", "DAIUSDT", "TUSDUSDT", "USD1USDT", "PAXGUSDT", "XAUTUSDT", "RLUSDUSDT"}


def crypto_symbols() -> set[str]:
    """Linear USDT perps whose underlying is a crypto asset (Bybit's symbolType '' or 'innovation'); tokenised stocks,
    ETFs, commodities and FX ('stock', 'ETF', 'commodity', ...) are not crypto and are left out."""
    out, cursor = set(), None
    while True:
        res = bb.get(f"{bb.BYBIT}/instruments-info?category=linear&limit=1000" + (f"&cursor={cursor}" if cursor else ""))
        out |= {i["symbol"] for i in res.get("list") or [] if (i.get("symbolType") or "") in ("", "innovation")}
        cursor = res.get("nextPageCursor")
        if not cursor:
            return out


def universe(top: int, now_ms: int) -> list[dict]:
    inst = bb.instruments()
    crypto = crypto_symbols()
    inst = {s: v for s, v in inst.items() if s in crypto}
    tick = bb.get(f"{bb.BYBIT}/tickers?category=linear").get("list") or []
    by_turn = sorted((t for t in tick if t["symbol"] in inst), key=lambda t: -float(t.get("turnover24h") or 0))
    pool = []
    for t in by_turn:
        s = t["symbol"]
        i = inst[s]
        if s in EXCLUDE or not s.endswith("USDT") or s.startswith("1000000"):
            continue
        if i["launch_ts"] and now_ms - i["launch_ts"] < 120 * DAY:
            continue
        pool.append(s)
        if len(pool) >= top * 2:
            break

    def med_turnover(s: str) -> float:
        rows = bb.klines(s, "D", now_ms - 31 * DAY, now_ms // DAY * DAY)
        return statistics.median(float(r[6]) for r in rows[-30:]) if len(rows) >= 25 else 0.0

    with ThreadPoolExecutor(8) as ex:
        med = dict(zip(pool, ex.map(med_turnover, pool)))
    ranked = sorted(pool, key=lambda s: -med[s])[:top]
    return [{"symbol": s, "median_turnover_30d": round(med[s]), "rules": inst[s]} for s in ranked]


def fetch(symbol: str, start: int, end: int) -> int:
    f = OUT / f"{symbol}.npy"
    if f.exists():
        a = np.load(f)
        if len(a) and a[0, 0] <= start and a[-1, 0] >= end - 2 * MIN:
            return len(a)
    rows = bb.klines(symbol, "1", start, end)
    a = np.array([[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6])]
                  for r in rows], dtype=np.float64)
    np.save(f, a)
    return len(a)


def fetch_positioning(symbol: str, start: int, end: int) -> int:
    """Funding (settled), open interest (1h), long/short account ratio (1h) and the premium index (1h closes): the same
    series the live market feeds a V6-style bot."""
    f = OUT / f"{symbol}.pos.json"
    if f.exists():
        return sum(len(v) for v in json.loads(f.read_text()).values())
    prem = bb.klines(symbol, "60", start, end, "premium-index-price-kline")
    out = {"funding": bb.funding(symbol, start, end), "oi": bb.open_interest(symbol, start, end),
           "ratio": bb.account_ratio(symbol, start, end), "premium": [(int(r[0]), float(r[4])) for r in prem]}
    f.write_text(json.dumps(out))
    return sum(len(v) for v in out.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=95)
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    now = int(time.time() * 1000)
    end = now // DAY * DAY
    start = end - args.days * DAY
    uni = universe(args.top, now)
    (OUT / "universe.json").write_text(json.dumps({"built_ms": now, "start_ms": start, "end_ms": end,
                                                    "rule": "top by 30d median daily turnover, listed >= 120d",
                                                    "coins": uni}, indent=1))
    syms = sorted({u["symbol"] for u in uni} | {"BTCUSDT"})
    print("universe:", " ".join(u["symbol"].replace("USDT", "") for u in uni), flush=True)
    t = time.time()
    with ThreadPoolExecutor(6) as ex:
        for s, n in zip(syms, ex.map(lambda s: fetch(s, start, end), syms)):
            print(f"{s:14s} {n:7d} bars", flush=True)
    with ThreadPoolExecutor(4) as ex:
        for s, n in zip(syms, ex.map(lambda s: fetch_positioning(s, start, end), syms)):
            print(f"{s:14s} {n:7d} positioning points", flush=True)
    print(f"done in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
