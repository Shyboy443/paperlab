"""Run the V5 HOURLY / DAILY FUTURES ARENA (docs/V5_PROTOCOL.md). Detached on the server:

    python scripts/v5_bybit.py fetch --universe docs/V5_UNIVERSE_DEV.json ... --data-dir /data/v5/dev   (the data)
    python scripts/run_v5_arena.py --role DEVELOPMENT --phase all --workers 18
    python scripts/run_v5_arena.py --role DEVELOPMENT --phase jev --run-id v5-...        (needs OPENROUTER_API_KEY)
    python scripts/run_v5_arena.py --role TEST --phase all ...                         (needs the pre-registration)

`all` = STAGE 1 -> ANALYZE 1 -> STAGE 2 -> ANALYZE 2 -> [JEV -> TAKE -> RANDOM, only if a family survived] -> ANALYZE.
TEST refuses to start unless docs/V5_TEST_PREREGISTRATION.json exists and the V5 strategy and Jev fingerprints, the
universe and the TEST configuration all match it. A run listed in docs/V5_RESULTS_FREEZE.md accepts only `status`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ai.jev.models import DEFAULT_MODEL, JevConfig  # noqa: E402
from app.ai.jev.v5 import v5_fingerprints as jev_fingerprints  # noqa: E402
from app.backtest import archive  # noqa: E402
from app.competition import v5_run as vr  # noqa: E402
from app.competition.v3_venue import to_rules  # noqa: E402
from app.competition.v5_config import WINDOWS, config_for, load_universe  # noqa: E402
from app.competition.v5_universe import UniverseRuleV5  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.strategies.registry import v5_fingerprints  # noqa: E402
from scripts.backtest import settings_for  # noqa: E402

DOCS = PROJECT / "docs"
PREREG = DOCS / "V5_TEST_PREREGISTRATION.json"
PHASES = ("fingerprints", "all", "stage1", "analyze1", "stage2", "analyze2", "jev", "take", "random", "analyze", "status")


def fingerprints() -> dict[str, str]:
    fp = {f"strategy:{k}": v for k, v in v5_fingerprints().items()}
    fp.update({f"jev:{k}": v for k, v in jev_fingerprints().items()})
    fp["universe_rule"] = UniverseRuleV5().fingerprint()
    for name in ("v5_features", "v5_arena", "v5_analyzer", "v5_run"):
        import hashlib
        src = (PROJECT / "app" / "competition" / f"{name}.py").read_text(encoding="utf-8").replace("\r\n", "\n")
        fp[f"source:{name}"] = hashlib.sha256(src.encode()).hexdigest()[:12]
    return fp


def build_config(window, universe, fps, rules_source: str = "", dataset_fingerprint: str = ""):
    """The run configuration. The TEST pre-registration fingerprints it with rules_source and dataset empty (both are
    only known after the holdout's files exist)."""
    return config_for(window, universe,
                      strategy_fingerprints=tuple(sorted((k, v) for k, v in fps.items() if k.startswith("strategy:"))),
                      jev_fingerprints=tuple(sorted((k, v) for k, v in fps.items() if k.startswith("jev:"))),
                      rules_source=rules_source, dataset_fingerprint=dataset_fingerprint)


def frozen_runs() -> set[str]:
    p = DOCS / "V5_RESULTS_FREEZE.md"
    return set(re.findall(r"`(v5-[0-9a-f]{10})`", p.read_text(encoding="utf-8"))) if p.exists() else set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=("DEVELOPMENT", "TEST"), default="DEVELOPMENT")
    ap.add_argument("--phase", default="all", choices=PHASES)
    ap.add_argument("--run-id")
    ap.add_argument("--label")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--db")
    ap.add_argument("--data-dir", help="the Bybit V5 archive (default DATA_DIR/v5/dev or /test)")
    ap.add_argument("--replay-only", action="store_true")
    ap.add_argument("--max-calls", type=int)
    ap.add_argument("--max-cost", type=float)
    ap.add_argument("--no-jev", action="store_true", help="all: stop before Stage 3")
    args = ap.parse_args()

    fps = fingerprints()
    if args.phase == "fingerprints":
        print(json.dumps(fps, indent=1))
        return 0
    window = WINDOWS[args.role]
    root = Path(os.environ.get("DATA_DIR") or PROJECT / "data")
    db = args.db or str(root / "paperlab.db")
    ddir = Path(args.data_dir or root / "v5" / ("dev" if args.role == "DEVELOPMENT" else "test"))
    settings = settings_for(20.0, DATA_DIR=str(ddir))
    universe = load_universe(window)
    prereg = json.loads(PREREG.read_text(encoding="utf-8")) if PREREG.exists() else None
    meta = archive.load_meta(settings) or {}
    snap = json.loads((ddir / "bybit_rules.json").read_text(encoding="utf-8"))
    rules = to_rules(snap)
    cfg = build_config(window, universe, fps, f"{snap['source']} @ {snap['fetched_ts']}", str(meta.get("fingerprint") or ""))
    missing = [s for s in (f"{c}USDT" for c in cfg.coins) if s not in (meta.get("symbols") or [])]
    if missing:
        print(f"the archive in {ddir} lacks {missing}: fetch it first (scripts/v5_bybit.py fetch)")
        return 2
    if args.role == "TEST":
        if prereg is None:
            print("TEST needs docs/V5_TEST_PREREGISTRATION.json (written before any TEST month is downloaded)")
            return 2
        bad = {k: (v, prereg["fingerprints"].get(k)) for k, v in fps.items()
               if not k.startswith("source:") and prereg["fingerprints"].get(k) != v}
        if bad:
            print("sources differ from the TEST pre-registration:", json.dumps(bad))
            return 2
        if universe["fingerprint"] != prereg.get("universe_fingerprint"):
            print(f"TEST universe {universe['fingerprint']} != pre-registered {prereg.get('universe_fingerprint')}")
            return 2
        registered = build_config(window, universe, fps).fingerprint()
        if registered != prereg.get("config_fingerprint"):
            print(f"TEST config {registered} != pre-registered {prereg.get('config_fingerprint')}")
            return 2
    run_id = args.run_id
    if args.phase in ("all", "stage1") and not run_id:
        label = args.label or f"V5 HOURLY / DAILY FUTURES ARENA - {cfg.dataset_role} {cfg.trade_from}..{cfg.trade_to}"
        storage = Storage(db)
        run_id = vr.new_run(storage, cfg, label,
                            extra={"bybit_rules": snap, "universe": universe["snapshot"],
                                   "archive": {k: meta.get(k) for k in ("fingerprint", "rows", "first_month", "last_month",
                                                                          "missing_minutes", "source")},
                                   "fingerprints": fps, "prereg": prereg if args.role == "TEST" else None})
        storage.close()
    if not run_id:
        print("--run-id is required for this phase")
        return 2
    if run_id in frozen_runs() and args.phase != "status":
        print(f"{run_id} is FROZEN (docs/V5_RESULTS_FREEZE.md): no phase may run on it")
        return 2
    storage = Storage(db)
    run = storage.v5_run(run_id)
    if run is None:
        print("no such run", run_id)
        return 2
    if run.get("config_fingerprint") != cfg.fingerprint() and args.phase != "status":
        print(f"config changed since {run_id} started ({run.get('config_fingerprint')} != {cfg.fingerprint()})")
        return 2
    if run.get("dataset_role") != cfg.dataset_role:
        print(f"{run_id} is a {run.get('dataset_role')} run, not {cfg.dataset_role}")
        return 2
    print(f"run {run_id}  role {cfg.dataset_role}  phase {args.phase}  workers {args.workers}  config {cfg.fingerprint()}  "
          f"dataset {cfg.dataset_fingerprint}  coins {list(cfg.coins)}", flush=True)
    t0 = time.time()

    def progress(r, i, n):
        if r["ok"]:
            rec = r["record"]
            a, m = rec["activity"], rec["metrics"]
            print(f"  [{i:>4}/{n}] {rec['key']:<40} signals={a['signals']:>5} trades={m.get('trades') or 0:>4} "
                  f"refused={a['below_exchange_minimum']:>4} net={m.get('net_profit') or 0:>8.3f} {rec['elapsed_s']:>6.1f}s",
                  flush=True)
        else:
            print(f"  [{i:>4}/{n}] ERROR {r['ident']} {r['error']}", flush=True)

    def phase(name: str, jobs: list) -> None:
        storage.v5_run_update(run_id, stage=name, progress_json={"phase": name, "jobs": len(jobs),
                                                                  "started_ts": int(time.time() * 1000)})
        print(f"-- {name}: {len(jobs)} jobs", flush=True)
        errors = [r for r in vr.run_jobs(storage, run_id, jobs, settings, cfg, rules, db, str(ddir), args.workers, progress)
                  if not r["ok"]]
        if errors:
            print(f"-- {name}: {len(errors)} job(s) failed; re-run the phase to retry them", flush=True)

    if args.phase == "status":
        print(json.dumps({"stage": run.get("stage"), "status": run.get("status"), "bots": len(storage.v5_bot_keys(run_id)),
                          "progress": run.get("progress")}, indent=1, default=str))
        return 0
    frozen = None
    dev_edges = None
    if args.role == "TEST":
        frozen = {"survivors": prereg.get("survivors") or []}
        dev_edges = {f"{s['strategy_id']}|{s['horizon']}": s.get("family_edge") or {} for s in prereg.get("survivors") or []}
    if args.phase in ("all", "stage1"):
        phase("STAGE1", vr.stage1_jobs(cfg, run_id))
    if args.phase in ("all", "analyze1"):
        storage.v5_run_update(run_id, stage="ANALYZE1")
        field = vr.analyze_stage1(storage, run_id, cfg, settings, frozen)
        for f in field["families"]:
            r = f["raw_edge"]
            print(f"   {f['strategy_id']:<5} {f['horizon']:<6} raw {'PASS' if r['passed'] else 'fail'}  n={r['trades']:>5} "
                  f"gross={r['mean_r']} R  p={r['p_mean_le_0']}  subs={r['subperiod_mean_r']}  "
                  f"net20={f['economic_20_baseline']['mean_r']} (n={f['economic_20_baseline']['trades']})  "
                  f"exit={(f.get('exit') or {}).get('plan')}", flush=True)
        print(f"-- ANALYZE1: {len(field['survivors'])} survivor(s): "
              f"{[(s['strategy_id'], s['horizon'], (s.get('exit') or {}).get('plan')) for s in field['survivors']]}", flush=True)
    field = (storage.v5_run(run_id) or {}).get("field") or {}
    if args.phase in ("all", "stage2") and field.get("survivors"):
        phase("STAGE2", vr.stage2_jobs(cfg, run_id, field))
    if args.phase in ("all", "analyze2"):
        field = vr.analyze_stage2(storage, run_id, cfg, dev_edges)
        print(f"-- ANALYZE2: economic {field.get('economic_passed')}; {len(field.get('pairs') or [])} Jev pair(s)", flush=True)
    field = (storage.v5_run(run_id) or {}).get("field") or {}
    jev_ok = bool(field.get("pairs")) and not (args.phase == "all" and args.no_jev)
    if args.phase == "jev" or (args.phase == "all" and jev_ok):
        jc = JevConfig.from_env()
        if not args.replay_only and (not os.environ.get("OPENROUTER_API_KEY") or not jc.enabled):
            print("Jev is not configured here (OPENROUTER_API_KEY / JEV_ENABLED): run the JEV phase on the server.")
            return 2
        phase("JEV", vr.twin_jobs(field, run_id, "jev", model=jc.model or DEFAULT_MODEL, replay_only=args.replay_only,
                                  max_calls=args.max_calls, max_cost_usd=args.max_cost))
    if args.phase == "take" or (args.phase == "all" and jev_ok):
        phase("TAKE", vr.twin_jobs(field, run_id, "take"))
    if args.phase == "random" or (args.phase == "all" and jev_ok):
        phase("RANDOM", vr.random_jobs(storage, run_id, field, cfg.random_seeds))
    if args.phase in ("all", "analyze"):
        storage.v5_run_update(run_id, stage="ANALYZE")
        dev = None
        if cfg.dataset_role == "TEST":
            dev = (storage.v5_run(prereg["dev_run_id"]) or {}).get("summary") or {}
        summary = vr.analyze_run(storage, run_id, cfg, settings, dev)
        print(json.dumps({"answers": summary["answers"], "counts": summary["counts"], "viability": summary["viability"],
                          "costs": summary["costs"]["controls_20"], "labels": summary["labels"],
                          "failure_modes": summary["failure_modes"], "advanced_set": summary["advanced_set"]},
                         indent=1, default=str), flush=True)
    print(f"done in {time.time() - t0:.0f}s", flush=True)
    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
