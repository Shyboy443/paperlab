"""V5 Bybit data: the window's universe (objective rule) and the Bybit-native download.

    python scripts/v5_bybit.py universe --window-from 2025-03-01 --window-to 2026-08-31 --warmup-from 2024-12-01 \
        --out docs/V5_UNIVERSE_DEV.json
    python scripts/v5_bybit.py fetch --universe docs/V5_UNIVERSE_DEV.json --from-month 2024-12 --to-month 2026-08 \
        --data-dir /data/v5/dev

Public data only (no key). The universe reads the 30 days before the window plus data-existence probes at the
warm-up start; it never reads a strategy result.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import bybit_archive as bb  # noqa: E402
from app.backtest.archive import months_between  # noqa: E402
from app.competition.v5_universe import UniverseRuleV5, measure, score  # noqa: E402

DAY = 86_400_000


def ms(d: str) -> int:
    return int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def iso(t: int) -> str:
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).date().isoformat()


def universe(args: argparse.Namespace) -> int:
    rule = UniverseRuleV5()
    w0, warm0 = ms(args.window_from), ms(args.warmup_from)
    s0, s1 = w0 - 30 * DAY, w0
    t0 = time.time()
    inst = bb.instruments()
    listed = {s: f for s, f in inst.items() if f["launch_ts"] and f["launch_ts"] <= w0 - rule.min_listing_days * DAY}
    print(f"instruments {len(inst)}; listed >= {rule.min_listing_days}d before {args.window_from}: {len(listed)}", flush=True)
    with ThreadPoolExecutor(8) as ex:
        daily = dict(zip(listed, ex.map(lambda s: bb.klines(s, "D", s0, s1), listed)))
    turn = {s: statistics.median([float(k[6]) for k in d]) for s, d in daily.items() if d}
    pool = [s for s, _ in sorted(turn.items(), key=lambda kv: (-kv[1], kv[0]))[:rule.pool_size]]
    print(f"pool {len(pool)} ({time.time() - t0:.0f}s)", flush=True)

    def probe(s: str) -> dict:
        return {"tape": bool(bb.klines(s, "1", warm0, warm0 + 60 * bb.MIN)),
                "funding": bool(bb.funding(s, warm0, warm0 + 3 * DAY)),
                "open_interest": bool(bb.open_interest(s, warm0, warm0 + DAY))}
    with ThreadPoolExecutor(8) as ex:
        b4 = dict(zip(pool, ex.map(lambda s: bb.klines(s, "240", s0, s1), pool)))
        data = dict(zip(pool, ex.map(probe, pool)))
    measured = {}
    for s in pool:
        m = measure(daily[s], b4[s], listed[s], rule)
        m.update({"listed_long_enough": True, "data": data[s], "filters": listed[s]})
        measured[s] = m
    res = score(measured, rule)
    snap = {"window": {"from": args.window_from, "to": args.window_to, "warmup_from": args.warmup_from},
            "scoring_period": {"from": iso(s0), "to": iso(s1 - 1)},
            "sources": {"liquidity_volatility": "Bybit v5 kline D and 240", "filters": "Bybit v5 instruments-info",
                        "data_probes": "Bybit v5 kline 1m, funding/history, open-interest 1h at the warm-up start"},
            "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limits": ["candidates are today's Bybit instruments (a contract delisted before today cannot be one)",
                       "today's tick / step / minimum filters are applied to the scoring-period price"], **res}
    Path(args.out).write_text(json.dumps(snap, indent=1, default=str), encoding="utf-8")
    print("universe", res["universe"])
    print("eligible", res["eligible"], "of", res["candidates"], "failures", res["failures"])
    for s in res["ranked"][:14]:
        r = res["coins"][s]
        print(f"  {s:14s} score {r['score']:.3f} turnover {r['turnover_usdt'] / 1e6:8.1f}M  range4h {r['range_4h_bps']:6.1f}bps "
              f"cost_ratio {r['cost_ratio']:.3f} stop {r['typical_stop_pct']:.3f} notional {r['take_notional_usdt']:.2f} step {r['step_share']:.3f}")
    for s in pool:
        if res["coins"][s]["fail"]:
            print(f"  x {s:14s} {','.join(res['coins'][s]['fail'])}")
    return 0


def fetch(args: argparse.Namespace) -> int:
    snap = json.loads(Path(args.universe).read_text(encoding="utf-8"))
    symbols = list(snap["universe"])
    months = months_between(args.from_month, args.to_month)
    root = Path(args.data_dir)
    arch, pos = root / "archive", root / "positioning"
    a, _ = bb.month_bounds(months[0])
    _, b = bb.month_bounds(months[-1])
    files: list[dict] = []
    t0 = time.time()

    def month_job(sym_month):
        sym, month = sym_month
        s, e = bb.month_bounds(month)
        bars = bb.klines(sym, "1", s, e)
        n, sha = bb.write_month_klines(arch, sym, month, bars)
        gaps = (e - s) // bb.MIN - n
        return {"path": f"archive/klines/{sym}/{sym}-1m-{month}.zip", "rows": n, "sha256": sha, "missing_minutes": int(gaps)}

    jobs = [(s, m) for s in symbols for m in months]
    with ThreadPoolExecutor(args.workers) as ex:
        for i, rec in enumerate(ex.map(month_job, jobs), 1):
            files.append(rec)
            if i % 20 == 0 or i == len(jobs):
                print(f"  klines {i}/{len(jobs)} ({time.time() - t0:.0f}s)", flush=True)

    def sym_job(sym):
        out = []
        rates = bb.funding(sym, a, b)
        for month in months:
            s, e = bb.month_bounds(month)
            part = [(t, r) for t, r in rates if s <= t < e]
            gaps = [y - x for (x, _), (y, _) in zip(part, part[1:])]
            interval_h = round(statistics.median(gaps) / bb.HOUR, 2) if gaps else 8.0
            n, sha = bb.write_month_funding(arch, sym, month, part, interval_h)
            out.append({"path": f"archive/fundingRate/{sym}/{sym}-fundingRate-{month}.zip", "rows": n, "sha256": sha})
        series = {"oi": bb.open_interest(sym, a, b), "ratio": bb.account_ratio(sym, a, b)}
        for name, kind in (("premium", "premium-index-price-kline"), ("mark", "mark-price-kline"), ("index", "index-price-kline")):
            series[name] = [(int(r[0]), float(r[4])) for r in bb.klines(sym, "60", a, b, kind=kind)]
        for name, rows in series.items():
            n, sha = bb.write_series(pos, sym, name, rows)
            out.append({"path": f"positioning/{sym}/{name}_1h.csv", "rows": n, "sha256": sha})
        return out
    with ThreadPoolExecutor(min(args.workers, len(symbols))) as ex:
        for recs in ex.map(sym_job, symbols):
            files.extend(recs)
    print(f"  positioning done ({time.time() - t0:.0f}s)", flush=True)
    inst = bb.instruments()
    rules = {"source": "bybit v5 instruments-info (linear)", "fetched_ts": int(time.time() * 1000),
             "symbols": {s: inst[s] for s in symbols if s in inst}}
    (root / "bybit_rules.json").write_text(json.dumps(rules, indent=1), encoding="utf-8")
    meta = {"source": "bybit v5 public REST", "symbols": symbols, "first_month": months[0], "last_month": months[-1],
            "files": files, "rows": sum(f["rows"] for f in files), "fingerprint": bb.fingerprint(files),
            "missing_minutes": sum(f.get("missing_minutes", 0) for f in files),
            "built_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (arch / "dataset.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("symbols", "first_month", "last_month", "rows", "fingerprint", "missing_minutes")}))
    print(f"done in {time.time() - t0:.0f}s")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("universe")
    u.add_argument("--window-from", required=True)
    u.add_argument("--window-to", required=True)
    u.add_argument("--warmup-from", required=True)
    u.add_argument("--out", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--universe", required=True)
    f.add_argument("--from-month", required=True)
    f.add_argument("--to-month", required=True)
    f.add_argument("--data-dir", required=True)
    f.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    return universe(args) if args.cmd == "universe" else fetch(args)


if __name__ == "__main__":
    raise SystemExit(main())
