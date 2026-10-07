"""Export finished competition/validation results to a seed file that ships with the deploy.

    python scripts/export_results.py --out seeds/results.json.gz
    python scripts/export_results.py --db <path> --db <path> --out seeds/results.json.gz

Why this exists: tournaments are run where the CPU and the market archive are (a workstation), but
the dashboard people actually open is the Railway service. Railway holds ~600 bars per symbol from
the live feed -- nowhere near a season, let alone five years -- so it can display results but cannot
produce them. This carries the finished rows over with the ordinary `railway up` deploy, and
`app.core.seed` imports them into the volume database on boot.

It exports RESULTS only: runs, competitors, metrics, ledgers, qualification. No candles, no keys,
no engine state. Importing is additive and never overwrites a run id that already exists.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

TABLES = ("competition_runs", "competition_competitors",
          "validation_runs", "validation_competitors",
          "arena_runs", "arena_bots", "jev_runs", "jev_bots", "jev_decisions",
          "candidate_runs", "candidate_results")


def dump(paths: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {t: [] for t in TABLES}
    seen: dict[str, set] = {t: set() for t in TABLES}
    for p in paths:
        if not Path(p).exists():
            print(f"  skip (missing): {p}")
            continue
        conn = sqlite3.connect(p)
        conn.row_factory = sqlite3.Row
        for t in TABLES:
            try:
                rows = conn.execute(f"SELECT * FROM {t}").fetchall()
            except sqlite3.OperationalError:
                continue                      # older database without that table
            for r in rows:
                d = dict(r)
                # de-duplicate across source databases by primary key
                key = (d.get("run_id"), d.get("strategy_id"), d.get("key"), d.get("version"), d.get("id"))
                if key in seen[t]:
                    continue
                seen[t].add(key)
                out[t].append(d)
        conn.close()
        print(f"  read {p}")
    return out


def merge_existing(data: dict[str, list[dict]], seed: Path) -> int:
    """Keep every row of the current seed file that the source databases do not have.

    The seed is the only surviving copy of some runs (the validation run bdddc67f6315 among them):
    the workstation databases that produced them are gone. Re-exporting from a database that lacks
    them must not silently delete them from the deploy, so their rows are carried over verbatim.
    """
    if not seed.exists():
        return 0
    try:
        with gzip.open(seed, "rb") as fh:
            old = json.loads(fh.read())
    except (OSError, ValueError):
        return 0
    kept = 0
    for t in TABLES:
        have = {(d.get("run_id"), d.get("strategy_id"), d.get("key"), d.get("version"), d.get("id"))
                for d in data.get(t, [])}
        for d in old.get(t, []):
            k = (d.get("run_id"), d.get("strategy_id"), d.get("key"), d.get("version"), d.get("id"))
            if k not in have:
                data.setdefault(t, []).append(d)
                have.add(k)
                kept += 1
    return kept


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", action="append", default=[], help="source SQLite path (repeatable)")
    ap.add_argument("--out", default=str(PROJECT / "seeds" / "results.json.gz"))
    ap.add_argument("--only-finished", action="store_true",
                    help="skip runs still marked running")
    ap.add_argument("--no-merge", action="store_true",
                    help="do NOT carry over runs that exist only in the current seed file")
    args = ap.parse_args(argv)

    sources = args.db or [str(PROJECT / "data" / "paperlab.db")]
    print("exporting from:")
    data = dump(sources)
    if not args.no_merge:
        kept = merge_existing(data, Path(args.out))
        print(f"  carried over {kept} rows from the existing {Path(args.out).name}")

    if args.only_finished:
        bad = {r["run_id"] for r in data["validation_runs"] if r.get("status") == "running"}
        bad |= {r["run_id"] for r in data["competition_runs"] if r.get("status") == "running"}
        bad |= {r["run_id"] for r in data["arena_runs"] if r.get("status") == "running"}
        bad |= {r["run_id"] for r in data["jev_runs"] if r.get("status") == "running"}
        bad |= {r["run_id"] for r in data["candidate_runs"] if r.get("status") == "running"}
        for t in TABLES:
            data[t] = [r for r in data[t] if r.get("run_id") not in bad]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(data, separators=(",", ":")).encode()
    with gzip.open(out, "wb", compresslevel=9) as fh:
        fh.write(blob)
    print(f"\n{out}  {out.stat().st_size / 1e6:.2f} MB gz  ({len(blob) / 1e6:.1f} MB raw)")
    for t in TABLES:
        print(f"  {t:<26} {len(data[t])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
