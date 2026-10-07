"""Multi-year validation of the three frozen v2 TEST survivors (docs/V2_MULTIYEAR_PROTOCOL.md).

    python scripts/run_candidate_validation.py --workers 12
    python scripts/run_candidate_validation.py --keys S26-XRPUSDT-30m@20x-v2 --scenarios NORMAL

Refuses to start if any candidate's source fingerprint or configuration differs from the v2 freeze.
Writes candidate_runs / candidate_results (schema 10) to the results database; export them to the
seed with scripts/export_results.py. Historical only: nothing here touches the live shadow.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.competition import candidate_validation as cv  # noqa: E402
from app.core.storage import Storage  # noqa: E402


def fmt(v, d=2, pct=False):
    if v is None:
        return "-"
    return f"{v * 100:+.{d}f}%" if pct else f"{v:+.{d}f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(PROJECT / "data" / "paperlab.db"))
    ap.add_argument("--keys", default=",".join(cv.CANDIDATES))
    ap.add_argument("--scenarios", default=",".join(cv.SCENARIOS))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--label", default="v2 candidates multi-year 2021-01..2025-10")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--raw-dir", default=str(PROJECT / "data" / "candidate_raw"),
                    help="every scenario replay is saved here (re-assemble with --from-raw)")
    ap.add_argument("--from-raw", default="", help="assemble a finished run's saved replays; no replaying")
    ap.add_argument("--run-id", default="", help="with --from-raw: re-assemble INTO this existing run id")
    args = ap.parse_args(argv)

    proto = cv.Protocol()
    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    if "NORMAL" not in scenarios:
        scenarios.insert(0, "NORMAL")
    storage = Storage(args.db)
    test_run = storage.arena_run(cv.SOURCE_TEST_RUN, heavy=True)
    if test_run is None:
        print(f"TEST run {cv.SOURCE_TEST_RUN} not in {args.db}")
        return 2
    test_run["bots"] = storage.arena_bots(cv.SOURCE_TEST_RUN)

    # ---- freeze check: nothing runs unless every candidate is exactly the frozen v2 bot ----
    manifests, problems = {}, []
    for k in keys:
        m = cv.manifest(test_run, k)
        manifests[k] = m
        problems += [f"{k}: {p}" for p in cv.verify(m, test_run)]
    print("=" * 100)
    print("FROZEN V2 MANIFESTS")
    for k, m in manifests.items():
        print(f"  {k:26s} manifest {m['manifest_fingerprint']}  source {m['source_fingerprint']}  "
              f"{m['symbol']} {m['timeframe']} (context {m['context_timeframe']})  venue {m['venue']}")
    if problems:
        print("REFUSED - not the frozen v2 candidates:")
        for p in problems:
            print("  ", p)
        return 3

    months = cv.months_between(proto.first_month, proto.last_month)
    run_id = (args.run_id if args.from_raw and args.run_id else uuid.uuid4().hex[:12])
    arena_cfg = test_run["config"]
    rules = arena_cfg.get("rules") or {}
    dataset = {}
    try:
        dataset = json.loads((PROJECT / "data" / "archive" / "dataset.json").read_text())
        dataset = {k: dataset.get(k) for k in ("fingerprint", "first_month", "last_month", "symbols", "rows", "source")}
    except (OSError, ValueError):
        pass
    if not (args.from_raw and args.run_id):
        storage.candidate_run_start({"run_id": run_id, "created_ts": int(time.time() * 1000), "label": args.label,
                                     "first_month": months[0], "last_month": months[-1], "venue": cv.VENUE,
                                     "source_test_run": cv.SOURCE_TEST_RUN,
                                     "protocol_fingerprint": proto.fingerprint(),
                                     "protocol": {**proto.to_dict(), "scenarios": scenarios,
                                                  "scenario_labels": {s: cv.SCENARIO_LABELS[s] for s in scenarios},
                                                  "manifests": manifests},
                                     "dataset": dataset})
    print(f"run {run_id}  protocol {proto.version} {proto.fingerprint()}  window {months[0]} -> {months[-1]} "
          f"(+ warmup {','.join(proto.warmup_months)})  dataset {dataset.get('fingerprint')}")

    # daily closes for regime labels (public REST, from listing), recorded with the result
    daily = {}
    for sym in sorted({manifests[k]["symbol"] for k in keys}):
        daily[sym] = cv.fetch_daily(sym, cv.month_start_ms("2020-01"), cv.month_end_ms(months[-1]))
        print(f"  daily {sym}: {len(daily[sym])} days")

    jobs = []
    for k in keys:
        m = manifests[k]
        spec = {"strategy_id": m["strategy_id"], "symbol": m["symbol"], "timeframe": m["timeframe"],
                "max_leverage": m["leverage_ceiling"], "profile": arena_cfg.get("profile") or "AGGRESSIVE",
                "params_version": m["params_version"], "name": m["strategy_name"]}
        for s in scenarios:
            jobs.append({"key": k, "scenario": s, "spec": spec, "arena_config": arena_cfg,
                         "rules": {m["symbol"]: rules[m["symbol"]]}, "months": months,
                         "warmup_months": list(proto.warmup_months)})
    # longest first: the 15m bot and NORMAL runs
    jobs.sort(key=lambda j: (j["spec"]["timeframe"] != "15m", j["scenario"] != "NORMAL"))
    runs: dict[str, dict[str, dict]] = {k: {} for k in keys}
    raw_dir = Path(args.from_raw or args.raw_dir) / (run_id if not args.from_raw else "")
    if args.from_raw:
        for f in sorted(Path(args.from_raw).glob("*.json")):
            r = json.loads(f.read_text())
            if r["key"] in runs:
                runs[r["key"]][r["scenario"]] = r
        jobs = []
    else:
        raw_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    done = 0
    if jobs:
        storage.candidate_run_update(run_id, progress_json={"done": 0, "total": len(jobs)})
    else:
        n = sum(len(v) for v in runs.values())
        print(f"re-assembling {n} saved replays into run {run_id} (no replaying)")
    with ProcessPoolExecutor(max_workers=max(1, args.workers), mp_context=get_context("spawn")) as pool:
        futs = {pool.submit(cv.run_path, j): j for j in jobs}
        for fut in as_completed(futs):
            j = futs[fut]
            done += 1
            try:
                r = fut.result()
            except Exception as exc:
                print(f"  [{done:>2}/{len(jobs)}] {j['key']:26s} {j['scenario']:28s} FAILED {type(exc).__name__}: {exc}")
                storage.candidate_run_update(run_id, status="error", error=f"{j['key']} {j['scenario']}: {exc}"[:300])
                raise
            runs[r["key"]][r["scenario"]] = r
            (raw_dir / f"{r['key'].replace('@', '_')}__{r['scenario'].replace('+', '_')}.json").write_text(
                json.dumps(cv_finite(r), default=str))
            m = r["metrics"]
            print(f"  [{done:>2}/{len(jobs)}] {r['key']:26s} {r['scenario']:28s} trades={m['trades']:>5} "
                  f"net={m['net_profit']:>+9.2f} expR={m['expectancy_r']:>+6.3f} PF={min(m['profit_factor'], 999):>6.2f} "
                  f"DD={m['max_drawdown_pct'] * 100:>5.1f}% {'HALTED ' if r['halted'] else ''}{r['elapsed_s']:>6.1f}s",
                  flush=True)
            storage.candidate_run_update(run_id, progress_json={"done": done, "total": len(jobs),
                                                                "last": f"{r['key']} {r['scenario']}",
                                                                "elapsed_s": round(time.time() - t0, 1)})

    results = {}
    for k in keys:
        res = cv.assemble(k, manifests[k], runs[k], daily[manifests[k]["symbol"]], proto)
        res["daily_source"] = {"symbol": manifests[k]["symbol"], "days": len(daily[manifests[k]["symbol"]]),
                               "fingerprint": cv._fp(daily[manifests[k]["symbol"]])}
        results[k] = res
        storage.candidate_result_save(run_id, k, res, int(time.time() * 1000))
    summary = {k: {"verdict": r["verdict"], "stages": r["stages"], "net": r["metrics"]["net_profit"],
                   "trades": r["metrics"]["trades"], "ending_equity": r["metrics"]["ending_equity"]}
               for k, r in results.items()}
    summary["passed"] = [k for k, r in results.items() if r["verdict"] == "PASS"]
    storage.candidate_run_update(run_id, status="complete", finished_ts=int(time.time() * 1000),
                                 summary_json=summary)
    report(results, proto)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(cv_finite(results), default=str))
    print(f"\nrun {run_id} complete in {time.time() - t0:.0f}s; passed: {summary['passed'] or 'none'}")
    storage.close()
    return 0


def cv_finite(obj):
    from app.core.storage_candidates import finite
    return finite(obj)


def report(results: dict, proto) -> None:
    for k, r in results.items():
        m, h, mc = r["metrics"], r["headroom"], r["monte_carlo"]
        print("\n" + "=" * 100)
        print(f"{k}   VERDICT {r['verdict']}   stages {r['stages']}")
        print(f"  equity {m['starting_equity']:.2f} -> {m['ending_equity']:.2f} ({fmt(m['net_return_pct'], 1, True)})  "
              f"trades {m['trades']}  win {fmt(m['win_rate'], 1, True)}  expR {fmt(m['expectancy_r'], 3)}  "
              f"PF {min(m['profit_factor'], 999):.2f}  maxDD {m['max_drawdown_pct'] * 100:.1f}%  "
              f"halted {r['halted']}")
        print(f"  gross {fmt(m['gross_pnl'])}  fees {fmt(-m['fees_paid'])}  slippage {fmt(-m['slippage_cost'])}  "
              f"funding {fmt(m['funding_paid'])}  net {fmt(m['net_profit'])}")
        w = r["windows"]
        print(f"  quarters: {w['active']} active of {w['total']}, profitable {w['profitable']}, "
              f"median return {fmt(w['median_return'], 1, True)}")
        for y in r["years"]:
            print(f"    {y['year']}: trades {y['trades']:>4}  return {fmt(y['return'], 1, True):>8}  "
                  f"expR {fmt(y['expectancy_r'], 3):>7}  PF {min(y['profit_factor'] or 0, 999):>5.2f}  DD {(y['max_dd'] or 0) * 100:>5.1f}%")
        for dim in ("trend", "vol"):
            print(f"  regimes ({dim}): " + "  ".join(f"{x['regime']} n={x['trades']} net={x['net']:+.2f} "
                                                   f"expR={fmt(x['expectancy_r'], 2)}" for x in r["regimes"][dim]["rows"]))
        rb = r["robustness"]
        print(f"  robustness: net w/o best {fmt(rb['net_without_best'])}, w/o best 3 {fmt(rb['net_without_best3'])}, "
              f"w/o best year ({rb['best_year']}) {fmt(rb['net_without_best_year'])}")
        print(f"  headroom: break-even {h.get('breakeven_cost_bps', 0):.1f} bps vs Binance {h.get('binance_cost_bps', 0):.1f} "
              f"/ Bybit {h.get('bybit_cost_bps', 0):.1f} bps")
        if mc.get("ran"):
            print(f"  MC: median end {mc['median_ending_equity']:.2f}  p5 {mc['p5_ending_equity']:.2f}  "
                  f"medDD {mc['median_max_dd'] * 100:.1f}%  p95DD {mc['p95_max_dd'] * 100:.1f}%  "
                  f"p99DD {mc['p99_max_dd'] * 100:.1f}%  P(ruin) {mc['ruin_probability'] * 100:.1f}%")
        for s in r["stress"]:
            print(f"    stress {s['label']:<60s} net {fmt(s['net']):>8}  expR {fmt(s['expectancy_r'], 3):>7}  "
                  f"{'survives' if s['survives'] else 'FAILS'}")
        for g in r["gates"]:
            print(f"    [{'PASS' if g['ok'] else 'FAIL'}] {g['name']:<32s} {g['actual']}  ({g['threshold']})")


if __name__ == "__main__":
    raise SystemExit(main())
