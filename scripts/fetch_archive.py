"""Download and verify the multi-year market-data archive.

    python scripts/fetch_archive.py --from 2021-01 --to 2026-09
    python scripts/fetch_archive.py --from 2024-01 --to 2024-12 --symbols BTCUSDT
    python scripts/fetch_archive.py --report          # what is already cached

Every file is checked against the SHA-256 the archive publishes beside it, so a truncated download
is rejected instead of being silently treated as a short month. The cache lives under DATA_DIR --
on Railway that is the mounted volume, so it survives redeploys.

Only klines, markPriceKlines and fundingRate are fetched. aggTrades and bookTicker are 340 MB-7 GB
per symbol-month and the historical tournament executes at L3, which needs neither.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import archive  # noqa: E402
from app.config import load_settings  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024.0
    return f"{n:.1f}TB"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="first", default="2021-01")
    ap.add_argument("--to", dest="last", default=time.strftime("%Y-%m"))
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--kinds", default="klines,markPriceKlines,fundingRate")
    ap.add_argument("--refresh", action="store_true", help="re-download even if cached")
    ap.add_argument("--report", action="store_true", help="print the stored dataset metadata")
    args = ap.parse_args(argv)

    settings = load_settings()
    if args.report:
        meta = archive.load_meta(settings)
        if not meta:
            print("no dataset metadata yet")
            return 1
        print(f"source       {meta['source']}")
        print(f"symbols      {', '.join(meta['symbols'])}")
        print(f"window       {meta['first_month']} -> {meta['last_month']}")
        print(f"kinds        {', '.join(meta['kinds'])}")
        print(f"files        {len(meta['files'])} verified, {len(meta['missing'])} missing")
        print(f"rows         {meta['rows']:,}")
        print(f"fingerprint  {meta['fingerprint']}")
        if meta["missing"]:
            print("missing:", ", ".join(meta["missing"][:12]),
                  "..." if len(meta["missing"]) > 12 else "")
        return 0

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    t0 = time.time()
    last_line = [0]

    def progress(label: str, done: int, total: int) -> None:
        if done - last_line[0] >= 10 or done == total:
            last_line[0] = done
            el = time.time() - t0
            rate = done / el if el else 0
            eta = (total - done) / rate if rate else 0
            print(f"  {done:>4}/{total}  {label:<34} {el:>5.0f}s elapsed, ~{eta:>4.0f}s left",
                  flush=True)

    print(f"fetching {args.first} -> {args.last} for {', '.join(symbols)}", flush=True)
    meta = archive.build(settings, symbols, args.first, args.last, kinds=kinds,
                         refresh=args.refresh, on_progress=progress)
    size = sum(f.bytes for f in meta.files)
    print(f"\nverified {len(meta.files)} files ({human(size)}), {meta.rows:,} rows")
    print(f"fingerprint {meta.fingerprint()}")
    if meta.missing:
        print(f"missing {len(meta.missing)}: {', '.join(meta.missing[:10])}"
              + (" ..." if len(meta.missing) > 10 else ""))
    print(f"cache: {archive.cache_dir(settings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
