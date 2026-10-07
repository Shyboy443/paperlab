"""Research store ingest: one DuckDB file with everything the studies need (local only, never on the trading server).

    python research/ingest.py            # build / top up data/research/market.duckdb

Tables (all timestamps are epoch ms, UTC; `ts` is when the value became KNOWN, see `available_ms` notes):
    candles_1m(symbol, ts, open, high, low, close, volume, turnover)   Bybit linear perps, ts = bar open
    oi_5m(symbol, ts, oi)                 Bybit open interest, 5-minute snapshots
    ls_5m(symbol, ts, buy_ratio)          Bybit long/short ACCOUNT ratio (share of accounts net long), 5-minute
    premium_5m(symbol, ts, premium)       Bybit premium index (perp vs index), 5m bar CLOSE, ts = bar open
    funding(symbol, ts, rate)             Bybit settled funding, ts = settlement time
    taker_5m(symbol, ts, volume, taker_buy)  Binance USD-M 5m klines: total and aggressive-buy base volume, ts = bar open
    paper_trades(...)                     the live paper bots' closed trades (research/pull_trades.py)

Sources are public, unauthenticated market-data endpoints. Liquidations and order-book history are NOT available
historically from either venue: they can only be collected forward (see docs/SYSTEM_REVIEW.md).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import bybit_archive as bb  # noqa: E402

DB = PROJECT / "data" / "research" / "market.duckdb"
SCAN = PROJECT / "data" / "v11_scan"
MIN, FIVE = 60_000, 300_000
BINANCE_ALIAS = {"PUMPFUNUSDT": "PUMPUSDT"}


def universe() -> tuple[list[str], int]:
    u = json.loads((SCAN / "universe.json").read_text())
    return [c["symbol"] for c in u["coins"]], int(u["start_ms"])


def schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS candles_1m(symbol VARCHAR, ts BIGINT, open DOUBLE, high DOUBLE, low DOUBLE,
            close DOUBLE, volume DOUBLE, turnover DOUBLE, PRIMARY KEY(symbol, ts));
        CREATE TABLE IF NOT EXISTS oi_5m(symbol VARCHAR, ts BIGINT, oi DOUBLE, PRIMARY KEY(symbol, ts));
        CREATE TABLE IF NOT EXISTS ls_5m(symbol VARCHAR, ts BIGINT, buy_ratio DOUBLE, PRIMARY KEY(symbol, ts));
        CREATE TABLE IF NOT EXISTS premium_5m(symbol VARCHAR, ts BIGINT, premium DOUBLE, PRIMARY KEY(symbol, ts));
        CREATE TABLE IF NOT EXISTS funding(symbol VARCHAR, ts BIGINT, rate DOUBLE, PRIMARY KEY(symbol, ts));
        CREATE TABLE IF NOT EXISTS taker_5m(symbol VARCHAR, ts BIGINT, volume DOUBLE, taker_buy DOUBLE,
            PRIMARY KEY(symbol, ts));
    """)


def last_ts(con, table: str, symbol: str) -> int | None:
    r = con.execute(f"SELECT max(ts) FROM {table} WHERE symbol = ?", [symbol]).fetchone()
    return int(r[0]) if r and r[0] is not None else None


def put(con, table: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    frame = pd.DataFrame(rows, columns=cols)                  # one bulk insert: executemany is row-by-row in DuckDB
    con.register("incoming", frame)
    con.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM incoming")
    con.unregister("incoming")
    return len(rows)


def binance_taker(symbol: str, start: int, end: int) -> list[tuple]:
    """Binance 5m klines: [open_ms, o, h, l, c, volume, close_ms, quote_vol, trades, taker_buy_base, ...]."""
    sym = BINANCE_ALIAS.get(symbol, symbol)
    out, t = {}, start
    while t < end:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=5m&startTime={t}&endTime={end - 1}&limit=1500"
        for i in range(5):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "paperlab-research"}), timeout=30) as r:
                    rows = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 400:                       # not listed on Binance
                    return []
                time.sleep(2 * (i + 1))
            except Exception:
                time.sleep(2 * (i + 1))
        else:
            raise RuntimeError(f"binance klines failed: {sym}")
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = (symbol, int(r[0]), float(r[5]), float(r[9]))
        t = int(rows[-1][0]) + FIVE
        if len(rows) < 1500:
            break
        time.sleep(0.3)
    return [out[k] for k in sorted(out)]


def fetch_symbol(symbol: str, start: int, end: int, have: dict[str, int | None]) -> dict[str, list[tuple]]:
    """Everything missing for one symbol (each source resumes from its own last stored timestamp)."""
    out: dict[str, list[tuple]] = {}
    s = (have["candles_1m"] or start - MIN) + MIN
    if s < end - MIN:
        out["candles_1m"] = [(symbol, int(r[0]), *map(float, r[1:7])) for r in bb.klines(symbol, "1", s, end)]
    s = (have["oi_5m"] or start - FIVE) + FIVE
    if s < end:
        out["oi_5m"] = [(symbol, ts, v) for ts, v in bb.cursor_series(
            "open-interest?category=linear&intervalTime=5min", "openInterest", symbol, s, end, 200)]
    s = (have["ls_5m"] or start - FIVE) + FIVE
    if s < end:
        out["ls_5m"] = [(symbol, ts, v) for ts, v in bb.cursor_series(
            "account-ratio?category=linear&period=5min", "buyRatio", symbol, s, end, 500)]
    s = (have["premium_5m"] or start - FIVE) + FIVE
    if s < end:
        out["premium_5m"] = [(symbol, int(r[0]), float(r[4])) for r in
                             bb.klines(symbol, "5", s, end, "premium-index-price-kline")]
    s = (have["funding"] or start - 1) + 1
    if s < end:
        out["funding"] = [(symbol, ts, v) for ts, v in bb.funding(symbol, s, end)]
    s = (have["taker_5m"] or start - FIVE) + FIVE
    if s < end:
        out["taker_5m"] = binance_taker(symbol, s, end)
    return out


def seed_from_cache(con, symbols: list[str]) -> None:
    """The 1m bars already cached for the V11 studies (data/v11_scan/*.npy) go in first: no re-download."""
    for s in symbols:
        if last_ts(con, "candles_1m", s) is not None:
            continue
        f = SCAN / f"{s}.npy"
        if not f.exists():
            continue
        a = np.load(f)
        con.execute("INSERT OR REPLACE INTO candles_1m SELECT ?, * FROM (SELECT unnest(?) ts, unnest(?) o, unnest(?) h, "
                    "unnest(?) l, unnest(?) c, unnest(?) v, unnest(?) t)",
                    [s, a[:, 0].astype(np.int64).tolist(), *(a[:, i].tolist() for i in range(1, 7))])
        print(f"{s:14s} {len(a):7d} cached 1m bars", flush=True)


def main() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    symbols, start = universe()
    end = (int(time.time() * 1000) // FIVE) * FIVE
    con = duckdb.connect(str(DB))
    schema(con)
    seed_from_cache(con, symbols)
    tables = ("candles_1m", "oi_5m", "ls_5m", "premium_5m", "funding", "taker_5m")
    have = {s: {t: last_ts(con, t, s) for t in tables} for s in symbols}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {s: ex.submit(fetch_symbol, s, start, end, have[s]) for s in symbols}
        for s, f in futs.items():
            got = f.result()
            counts = {t: put(con, t, rows) for t, rows in got.items()}
            print(f"{s:14s} " + " ".join(f"{t}+{n}" for t, n in counts.items()), flush=True)
    for t in tables:
        n, lo, hi = con.execute(f"SELECT count(*), min(ts), max(ts) FROM {t}").fetchone()
        print(f"{t:12s} rows {n:>10,}  {time.strftime('%Y-%m-%d %H:%M', time.gmtime((lo or 0) / 1000))} .. "
              f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime((hi or 0) / 1000))}")
    con.close()


if __name__ == "__main__":
    main()
