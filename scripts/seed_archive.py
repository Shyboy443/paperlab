"""Seed the PaperLab DB with a month of real archive candles, so a competition season can be replayed.

`scripts/seed_candles.py` fills the dashboard from the venue's REST API, which caps at ~1500 bars per
series -- fine for charts, far too short for a season. This loads whole months from the public
data.binance.vision archive (the same source `scripts/backtest.py` uses) and writes them into the
`candles` and `funding` tables, which is where `CompetitionService` reads its tape and its funding
settlements from.

Funding matters: without it a season settles nothing across the 8-hourly instants and the dashboard
reports a funding cost of exactly zero, which understates every bot's true net result.

    python scripts/seed_archive.py --months 2026-08
    python scripts/seed_archive.py --months 2026-07,2026-08 --db /data/paperlab.db

Only public archive files are fetched; no API key is involved. Rows are written with
source='backfill', so the engine treats them exactly like its own backfill and never deletes them
the way it deletes synthetic bars.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import funding as funding_mod  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.core.types import FundingInfo  # noqa: E402
from scripts.backtest_s15 import fetch  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="2026-08", help="comma-separated YYYY-MM")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--db", default="", help="SQLite path (default: <DATA_DIR>/paperlab.db)")
    ap.add_argument("--no-funding", action="store_true", help="skip funding history")
    args = ap.parse_args(argv)

    months = [m.strip() for m in args.months.split(",") if m.strip()]
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    db = args.db or str(load_settings().db_path)
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    storage = Storage(db)
    total = 0
    try:
        for sym in symbols:
            bars = fetch(sym, months, [])
            n = storage.insert_candles(bars)
            lo, hi, count = storage.candle_span(sym, "1m")
            total += n
            print(f"{sym:<10} +{n:>7} candles  stored {count:>7}   {lo} -> {hi}")
        if not args.no_funding:
            sched = funding_mod.load(symbols, months, Path(db).parent / "funding")
            for sym, rows in sched.by_symbol.items():
                for ts, rate in rows:
                    # mark is unknown in the archive; the replay marks funding against the bar
                    # close it settles on, so a zero here costs nothing.
                    storage.insert_funding(FundingInfo(sym, rate, ts, 0.0, 0.0, ts))
                print(f"{sym:<10} +{len(rows):>7} funding settlements")
    finally:
        storage.close()
    print(f"seeded {total} candles into {db}")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
