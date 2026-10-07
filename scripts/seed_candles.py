"""Seed the PaperLab SQLite DB with closed candles so the dashboard is not empty on first boot.

    .venv\\Scripts\\python scripts\\seed_candles.py                 # 500 bars per symbol x {1m,5m,15m} from the venue
    .venv\\Scripts\\python scripts\\seed_candles.py --bars 100
    .venv\\Scripts\\python scripts\\seed_candles.py --synthetic     # offline: seeded random walks (source=synthetic)
    .venv\\Scripts\\python scripts\\seed_candles.py --symbols BTCUSDT,ETHUSDT --db C:\\tmp\\seed.db

Real mode uses the venue configured in paperlab/.env (MODE / REST_BASE_OVERRIDE); live hosts are refused by
app.config exactly as they are for the server. Only public endpoints (exchangeInfo, klines) are called, so no
API key is needed. Exit status is non-zero when any series fails.

Synthetic bars are tagged source="synthetic". The engine deletes them for a (symbol, timeframe) as soon as a
real backfill for that series succeeds, and never persists synthetic bars itself, so they only serve offline
GUI work and disappear on the first connected boot.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import ConfigError, Settings, load_settings  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.core.types import Candle, tf_ms  # noqa: E402

DEFAULT_TFS = ("1m", "5m", "15m")
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
ANCHOR_PRICES = {"BTC": 65_000.0, "ETH": 3_000.0, "SOL": 150.0, "BNB": 600.0, "XRP": 0.6, "DOGE": 0.15}


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def series_line(symbol: str, tf: str, candles: list[Candle], suffix: str = "") -> str:
    if not candles:
        return f"{symbol} {tf} +0 bars (no data){suffix}"
    return (f"{symbol} {tf} +{len(candles)} bars (first {fmt_ts(candles[0].open_time)} "
            f"last {fmt_ts(candles[-1].open_time)} UTC){suffix}")


def anchor_price(symbol: str) -> float:
    for prefix, px in ANCHOR_PRICES.items():
        if symbol.startswith(prefix):
            return px
    return 100.0


def synthetic_series(symbol: str, tf: str, bars: int, now_ms: int, seed: int | None = None) -> list[Candle]:
    """A seeded geometric random walk of `bars` closed candles ending at the last fully closed bar."""
    step = tf_ms(tf)
    minutes = step / 60_000
    rng = random.Random(seed if seed is not None else zlib.crc32(f"{symbol}:{tf}".encode()))
    vol = 0.0006 * math.sqrt(minutes)          # per-bar log-return sigma, scaled with the bar length
    last_closed_open = (now_ms // step) * step - step
    first_open = last_closed_open - (bars - 1) * step
    px = anchor_price(symbol) * math.exp(rng.gauss(0, 0.01))
    base_vol = 40.0 / math.sqrt(anchor_price(symbol)) * 100.0 * minutes
    out: list[Candle] = []
    for i in range(bars):
        open_time = first_open + i * step
        o = px
        c = o * math.exp(rng.gauss(0, vol))
        wick_hi = abs(rng.gauss(0, vol * 0.6))
        wick_lo = abs(rng.gauss(0, vol * 0.6))
        h = max(o, c) * (1 + wick_hi)
        low = min(o, c) * (1 - wick_lo)
        v = max(1.0, rng.lognormvariate(math.log(base_vol), 0.5))
        out.append(Candle(symbol, tf, open_time, o, h, low, c, v, open_time + step - 1, True, v * c,
                          int(v), "synthetic"))
        px = c
    return out


async def seed_real(settings: Settings, symbols: list[str], tfs: list[str], bars: int, storage: Storage) -> int:
    from app.exchange.client import ExchangeClient

    print(f"venue {settings.venue.label} ({settings.venue.rest_base}) mode={settings.mode}")
    client = ExchangeClient(settings)
    failures = 0
    total = 0
    try:
        await client.load_rules(symbols)  # also validates that every symbol exists on this venue
        now_ms = await client.fetch_time()
        for symbol in symbols:
            for tf in tfs:
                try:
                    candles = await client.backfill(symbol, tf, None, now_ms, max_bars=bars)
                except Exception as exc:  # one bad series must not hide the others
                    failures += 1
                    print(f"{symbol} {tf} FAILED: {exc.__class__.__name__}: {str(exc)[:160]}")
                    continue
                if not candles:
                    failures += 1
                    print(series_line(symbol, tf, candles, " FAILED: empty response"))
                    continue
                n = storage.insert_candles(candles)
                total += n
                print(series_line(symbol, tf, candles))
    finally:
        await client.close()
    print(f"summary: {total} bars into {storage.path} for {len(symbols)} symbols x {len(tfs)} timeframes, "
          f"{failures} failed series")
    return 1 if failures or total == 0 else 0


def seed_synthetic(symbols: list[str], tfs: list[str], bars: int, storage: Storage, seed: int | None) -> int:
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    total = 0
    for symbol in symbols:
        for tf in tfs:
            candles = synthetic_series(symbol, tf, bars, now_ms, None if seed is None else seed + zlib.crc32(f"{symbol}:{tf}".encode()))
            total += storage.insert_candles(candles)
            print(series_line(symbol, tf, candles, " [synthetic]"))
    print(f"summary: {total} synthetic bars into {storage.path} for {len(symbols)} symbols x {len(tfs)} timeframes")
    print("note: synthetic bars are dropped per series as soon as the engine completes a real backfill")
    return 0 if total else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill closed candles for every configured symbol x timeframe into the PaperLab DB.",
        epilog="--synthetic writes seeded random walks tagged source=synthetic for offline GUI work; the engine "
               "deletes them for a series once a real backfill of that series succeeds.")
    p.add_argument("--bars", type=int, default=500, help="closed bars per series (default 500, max 1500)")
    p.add_argument("--symbols", help="comma-separated override of SYMBOLS from .env")
    p.add_argument("--tfs", default=",".join(DEFAULT_TFS), help="comma-separated timeframes (default 1m,5m,15m)")
    p.add_argument("--db", help="SQLite path override (default: <DATA_DIR>/paperlab.db from .env)")
    p.add_argument("--synthetic", action="store_true",
                   help="generate seeded random-walk bars (source=synthetic) instead of calling the venue; "
                        "the engine drops them once a real backfill succeeds")
    p.add_argument("--seed", type=int, help="base seed for --synthetic (default: derived from symbol/timeframe)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    bars = max(1, min(1500, args.bars))
    tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]
    for tf in tfs:
        try:
            tf_ms(tf)
        except ValueError as exc:
            print(f"error: {exc}")
            return 2
    settings: Settings | None = None
    try:
        settings = load_settings()
    except ConfigError as exc:
        if not args.synthetic or not (args.symbols and args.db):
            print(f"config error: {exc}")
            print("hint: fix paperlab/.env, or use --synthetic with --symbols and --db to stay offline")
            return 2
    symbols = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols
               else list(settings.symbols) if settings else list(DEFAULT_SYMBOLS))
    if not symbols:
        print("error: no symbols")
        return 2
    db_path = Path(args.db) if args.db else (settings.db_path if settings else PROJECT_ROOT / "data" / "paperlab.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    storage = Storage(db_path)
    try:
        if args.synthetic:
            return seed_synthetic(symbols, tfs, bars, storage, args.seed)
        assert settings is not None
        return asyncio.run(seed_real(settings, symbols, tfs, bars, storage))
    except KeyboardInterrupt:
        print("interrupted")
        return 130
    except Exception as exc:
        print(f"failed: {exc.__class__.__name__}: {str(exc)[:300]}")
        return 1
    finally:
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
