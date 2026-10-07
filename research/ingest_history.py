"""Older Bybit 1m history for V8's six coins, for independent (out-of-window) tests: data/research/history_v8.duckdb.

    python research/ingest_history.py [--start 2025-01-01] [--end 2026-06-26]

The research store (research/ingest.py) covers 2026-06-26 .. now; hypotheses found there are confirmed or rejected on
THIS earlier, untouched window. Public Bybit market data only; resumable (each coin continues from its stored end).
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import bybit_archive as bb  # noqa: E402

DB = PROJECT / "data" / "research" / "history_v8.duckdb"
COINS = ("ARBUSDT", "DOGEUSDT", "ENAUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
DAY, MIN = 86_400_000, 60_000


def ms(day: str) -> int:
    return int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def fetch(symbol: str, start: int, end: int) -> pd.DataFrame:
    """Month by month (bb.klines pages backward within each slice)."""
    parts, a = [], start
    while a < end:
        b = min(a + 30 * DAY, end)
        rows = bb.klines(symbol, "1", a, b)
        if rows:
            parts.append(pd.DataFrame([(symbol, int(r[0]), *map(float, r[1:7])) for r in rows],
                                      columns=["symbol", "ts", "open", "high", "low", "close", "volume", "turnover"]))
        a = b
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-06-26")
    args = ap.parse_args()
    start, end = ms(args.start), ms(args.end)
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB))
    con.execute("CREATE TABLE IF NOT EXISTS candles_1m(symbol VARCHAR, ts BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, "
                "close DOUBLE, volume DOUBLE, turnover DOUBLE, PRIMARY KEY(symbol, ts))")
    have = {s: con.execute("SELECT max(ts) FROM candles_1m WHERE symbol = ?", [s]).fetchone()[0] for s in COINS}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {s: ex.submit(fetch, s, (int(have[s]) + MIN) if have[s] else start, end) for s in COINS}
        for s, f in futs.items():
            frame = f.result()
            if len(frame):
                con.register("incoming", frame)
                con.execute("INSERT OR REPLACE INTO candles_1m SELECT * FROM incoming")
                con.unregister("incoming")
            print(f"{s:9s} +{len(frame):,} bars ({time.time() - t0:.0f}s)", flush=True)
    for s, n, lo, hi in con.execute("SELECT symbol, count(*), min(ts), max(ts) FROM candles_1m GROUP BY 1 ORDER BY 1").fetchall():
        print(f"{s:9s} {n:>9,} bars  {time.strftime('%Y-%m-%d', time.gmtime(lo / 1000))} .. "
              f"{time.strftime('%Y-%m-%d', time.gmtime(hi / 1000))}")
    con.close()


if __name__ == "__main__":
    main()
