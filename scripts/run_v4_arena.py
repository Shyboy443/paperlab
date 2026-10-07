"""Run the V4 INTRADAY SPECIALIST arena (docs/V4_PROTOCOL.md). Detached on the server:

    python scripts/run_v4_arena.py --role DEVELOPMENT --phase fetch
    python scripts/run_v4_arena.py --role DEVELOPMENT --phase all --workers 24
    python scripts/run_v4_arena.py --role DEVELOPMENT --phase jev --run-id v4-...      (needs OPENROUTER_API_KEY)
    python scripts/run_v4_arena.py --role TEST --phase all ...                        (needs the pre-registration)

TEST refuses to start unless docs/V4_TEST_PREREGISTRATION.json exists and the V4 strategy, edge-model and Jev
fingerprints, the tradeability rule and the TEST configuration all match it. A run listed in
docs/V4_RESULTS_FREEZE.md accepts no phase but `status`.
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
from app.ai.jev.v4 import v4_fingerprints as jev_fingerprints  # noqa: E402
from app.backtest import archive  # noqa: E402
from app.competition import v4_run as vr  # noqa: E402
from app.competition.tradeability import TradeabilityRule  # noqa: E402
from app.competition.v4_arena import select_field  # noqa: E402
from app.competition.v4_config import WINDOWS, EdgeConfigV4, config_for, load_universe  # noqa: E402,F401
from app.competition.v3_venue import load_or_fetch, to_rules  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.strategies.registry import v4_fingerprints  # noqa: E402
from scripts.backtest import settings_for  # noqa: E402

DOCS = PROJECT / "docs"
PREREG = DOCS / "V4_TEST_PREREGISTRATION.json"


def fingerprints() -> dict[str, str]:
    import hashlib
    fp = {f"strategy:{k}": v for k, v in v4_fingerprints().items()}
    fp.update({f"jev:{k}": v for k, v in jev_fingerprints().items()})
    fp["edge_model"] = hashlib.sha256(json.dumps(EdgeConfigV4().to_dict(), sort_keys=True).encode()).hexdigest()[:12]
    src = (PROJECT / "app" / "competition" / "v4_edge.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    fp["edge_model_source"] = hashlib.sha256(src.encode()).hexdigest()[:12]
    fp["tradeability_rule"] = TradeabilityRule().fingerprint()
    return fp


def build_config(window, universe, fps, rules_source: str = "", dataset_fingerprint: str = ""):
    """The run configuration. The TEST pre-registration fingerprints it with rules_source and dataset empty (both
    are only known after the holdout's files exist)."""
    return config_for(window, universe,
                      strategy_fingerprints=tuple(sorted((k, v) for k, v in fps.items() if k.startswith("strategy:"))),
                      jev_fingerprints=tuple(sorted((k, v) for k, v in fps.items() if k.startswith("jev:"))),
                      rules_source=rules_source, dataset_fingerprint=dataset_fingerprint)


def frozen_runs() -> set[str]:
    p = DOCS / "V4_RESULTS_FREEZE.md"
    return set(re.findall(r"`(v4-[0-9a-f]{10})`", p.read_text(encoding="utf-8"))) if p.exists() else set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=("DEVELOPMENT", "TEST"), default="DEVELOPMENT")
    ap.add_argument("--phase", default="all", choices=("fingerprints", "fetch", "all", "observe", "control", "field", "jev",
                                                       "take", "random", "capacity", "analyze", "status"))
    ap.add_argument("--run-id")
    ap.add_argument("--label")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--db")
    ap.add_argument("--archive-dir")
    ap.add_argument("--replay-only", action="store_true")
    ap.add_argument("--max-calls", type=int)
    ap.add_argument("--max-cost", type=float)
    ap.add_argument("--no-twins", action="store_true", help="all: stop after FIELD (no Jev / take / random / capacity)")
    args = ap.parse_args()

    if args.phase == "fingerprints":
        print(json.dumps(fingerprints(), indent=1))
        return 0
    window = WINDOWS[args.role]
    data_dir = Path(os.environ.get("DATA_DIR") or PROJECT / "data")
    db = args.db or str(data_dir / "paperlab.db")
    adir = args.archive_dir or str(data_dir / "v4" / ("dev" if args.role == "DEVELOPMENT" else "test"))
    settings = settings_for(20.0, DATA_DIR=adir)
    universe = load_universe(window)
    symbols = [f"{c}USDT" for c in universe["coins"]]
    if args.phase == "fetch":
        t0 = time.time()
        meta = archive.build(settings, symbols, window.months[0], window.months[-1], kinds=("klines", "fundingRate"),
                             on_progress=lambda label, i, n: print(f"  [{i}/{n}] {label}", flush=True) if i % 10 == 0 else None)
        print(f"fetched {len(meta.files)} files, {meta.rows} rows, missing {len(meta.missing)}: {meta.missing[:12]} "
              f"({time.time() - t0:.0f}s)")
        snap = load_or_fetch(Path(adir) / "bybit_rules.json", symbols, refresh=True)
        print("bybit rules:", {s: snap["symbols"].get(s) for s in symbols})
        return 0

    fps = fingerprints()
    prereg = json.loads(PREREG.read_text(encoding="utf-8")) if PREREG.exists() else None
    meta = archive.load_meta(settings) or {}
    snap = load_or_fetch(Path(adir) / "bybit_rules.json", symbols)
    rules = to_rules(snap)
    cfg = build_config(window, universe, fps, f"{snap['source']} @ {snap['fetched_ts']}", str(meta.get("fingerprint") or ""))
    if args.role == "TEST":
        if prereg is None:
            print("TEST needs docs/V4_TEST_PREREGISTRATION.json (written before any TEST month is downloaded)")
            return 2
        bad = {k: (v, prereg["fingerprints"].get(k)) for k, v in fps.items() if prereg["fingerprints"].get(k) != v}
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
    if args.phase in ("all", "observe") and not run_id:
        label = args.label or f"V4 INTRADAY SPECIALISTS - {cfg.dataset_role} {cfg.trade_from}..{cfg.trade_to}"
        run_id = vr.new_run(storage := Storage(db), cfg, label,
                            extra={"bybit_rules": snap, "universe": universe["snapshot"],
                                   "archive": {k: meta.get(k) for k in ("fingerprint", "rows", "first_month", "last_month")},
                                   "fingerprints": fps, "prereg": prereg if args.role == "TEST" else None})
        storage.close()
    if not run_id:
        print("--run-id is required for this phase")
        return 2
    if run_id in frozen_runs() and args.phase != "status":
        print(f"{run_id} is FROZEN (docs/V4_RESULTS_FREEZE.md): no phase may run on it")
        return 2
    storage = Storage(db)
    run = storage.v4_run(run_id)
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
          f"dataset {cfg.dataset_fingerprint}  universe {dict(cfg.coins_per_tf)}", flush=True)
    t0 = time.time()

    def progress(r, i, n):
        if r["ok"]:
            rec = r["record"]
            a = rec["activity"]
            f = rec.get("funnel") or {}
            print(f"  [{i:>4}/{n}] {rec['key']:<34} signals={a['signals']:>5} "
                  f"{'seq=' + str(a.get('sequenced')) if rec['role'] == 'RAW' else 'executed=' + str(f.get('executed'))} "
                  f"{rec['elapsed_s']:>6.1f}s", flush=True)
        else:
            print(f"  [{i:>4}/{n}] ERROR {r['ident']} {r['error']}", flush=True)

    def phase(name: str, jobs: list, ev: list) -> None:
        storage.v4_run_update(run_id, stage=name, progress_json={"phase": name, "jobs": len(jobs), "started_ts": int(time.time() * 1000)})
        print(f"-- {name}: {len(jobs)} jobs", flush=True)
        vr.run_jobs(storage, run_id, jobs, settings, cfg, rules, db, args.workers, ev, progress)

    if args.phase == "status":
        print(json.dumps({"stage": run.get("stage"), "status": run.get("status"), "bots": len(storage.v4_bot_keys(run_id)),
                          "progress": run.get("progress"), "evidence": run.get("evidence")}, indent=1, default=str))
        return 0
    if args.phase in ("all", "observe"):
        phase("OBSERVE", vr.grid_jobs(cfg, run_id, "RAW", "observer"), [])
    evidence: list = []
    if args.phase in ("all", "control", "jev", "take", "random", "capacity"):
        evidence, ev_stats = vr.evidence_for(storage, run_id)
        storage.v4_run_update(run_id, evidence_json=ev_stats)
        print(f"-- EVIDENCE: {ev_stats['observations']} sequenced observations from {ev_stats['raw_bots']} RAW bots "
              f"(fingerprint {ev_stats['fingerprint']})", flush=True)
    if args.phase in ("all", "control"):
        phase("CONTROL", vr.grid_jobs(cfg, run_id, "CONTROL", "none"), evidence)
    if args.phase in ("all", "field"):
        field = select_field(storage.v4_bots(run_id, role="CONTROL"), cfg)
        storage.v4_run_update(run_id, field_json=field, stage="FIELD")
        print(f"-- FIELD: {field['n']} pairs; thin slots {len(field['thin_slots'])}", flush=True)
    field = (storage.v4_run(run_id) or {}).get("field") or {"pairs": []}
    twins = not (args.phase == "all" and args.no_twins)
    if args.phase == "jev" or (args.phase == "all" and twins):
        jc = JevConfig.from_env()
        if not args.replay_only and (not os.environ.get("OPENROUTER_API_KEY") or not jc.enabled):
            print("Jev is not configured here (OPENROUTER_API_KEY / JEV_ENABLED): run the JEV phase on the server.")
            return 2
        phase("JEV", vr.twin_jobs(field, run_id, "jev", model=jc.model or DEFAULT_MODEL, replay_only=args.replay_only,
                                  max_calls=args.max_calls, max_cost_usd=args.max_cost), evidence)
    if args.phase == "take" or (args.phase == "all" and twins):
        phase("TAKE", vr.twin_jobs(field, run_id, "take"), evidence)
    if args.phase == "random" or (args.phase == "all" and twins):
        phase("RANDOM", vr.random_jobs(storage, run_id, field, cfg.random_seeds), evidence)
    if args.phase == "capacity" or (args.phase == "all" and twins):
        phase("CAPACITY", vr.capacity_jobs(field, run_id, cfg.capacity_balances), evidence)
    if args.phase in ("all", "analyze"):
        storage.v4_run_update(run_id, stage="ANALYZE")
        dev = None
        if cfg.dataset_role == "TEST":
            dev = (storage.v4_run(prereg["dev_run_id"]) or {}).get("summary") or {}
        summary = vr.analyze_run(storage, run_id, cfg, settings, dev)
        print(json.dumps({"answers": summary["answers"], "counts": summary["counts"], "states": summary["states"],
                          "failure_modes": {k: v for k, v in summary["failure_modes"].items() if k != "labels"},
                          "advanced_set": summary["advanced_set"]}, indent=1, default=str), flush=True)
    print(f"done in {time.time() - t0:.0f}s", flush=True)
    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
