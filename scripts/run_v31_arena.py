"""V3.1 AGGRESSIVE EDGE (docs/V31_PROTOCOL.md). Runs where the OpenRouter key is: the Railway service.

    python scripts/run_v31_arena.py all --workers 20                    # DEVELOPMENT: every phase
    python scripts/run_v31_arena.py control --run-id v31-xxxx           # resume one phase
    python scripts/run_v31_arena.py analyze --run-id v31-xxxx           # re-analyze (no replay, no request)
    python scripts/run_v31_arena.py all --role TEST --archive-dir /data/v31test   # the pre-registered TEST

DEVELOPMENT refuses to start when the V3.1 strategy sources or the Jev V3 prompt / policy differ from
docs/V31_FREEZE.md. TEST refuses to start unless everything -- sources, Jev V3, configuration, edge-model
evidence, field -- matches docs/V31_TEST_PREREGISTRATION.json, which is written BEFORE any TEST month is
downloaded. The frozen V3 run (docs/V3_RESULTS_FREEZE.md) is never touched: V3.1 has its own tables.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ai.jev.models import DEFAULT_MODEL, JevConfig  # noqa: E402
from app.ai.jev.v3 import v3_fingerprints  # noqa: E402
from app.backtest import archive  # noqa: E402
from app.competition import v31_run as vr  # noqa: E402
from app.competition.v3_arena import day_ms  # noqa: E402
from app.competition.v31_config import (DEV_TO, TEST_FROM, TEST_MONTHS, TEST_TO, V31Config)  # noqa: E402
from app.competition.v31_arena import select_field  # noqa: E402
from app.competition.v3_config import SYMBOLS  # noqa: E402
from app.competition.v3_venue import load_or_fetch, to_rules  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.strategies.registry import v31_fingerprints  # noqa: E402
from scripts.backtest import settings_for  # noqa: E402

FREEZE = PROJECT / "docs" / "V31_FREEZE.md"
PREREG = PROJECT / "docs" / "V31_TEST_PREREGISTRATION.json"


def frozen() -> dict[str, str]:
    if not FREEZE.exists():
        return {}
    return dict(re.findall(r"^\|\s*([A-Za-z0-9_.]+)\s*\|\s*`([0-9a-f]{12,16})`", FREEZE.read_text(encoding="utf-8"), re.M))


def frozen_runs() -> set[str]:
    """Runs whose results are frozen evidence (docs/V31_RESULTS_FREEZE.md): no phase but status may run."""
    doc = PROJECT / "docs" / "V31_RESULTS_FREEZE.md"
    if not doc.exists():
        return set()
    return set(re.findall(r"^\|\s*`(v31-[0-9a-f]+)`\s*\|\s*frozen", doc.read_text(encoding="utf-8"), re.M))


def fingerprints() -> dict[str, str]:
    return {**v31_fingerprints(), **{f"jev_{k}": v for k, v in v3_fingerprints().items() if k in ("prompt", "policy")}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["all", "observe", "control", "field", "jev", "take", "random", "capacity",
                                      "analyze", "status", "fingerprints"])
    ap.add_argument("--role", default="DEVELOPMENT", choices=["DEVELOPMENT", "TEST"])
    ap.add_argument("--run-id", default="")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--db", default="")
    ap.add_argument("--archive-dir", default="")
    ap.add_argument("--rules", default="", help="Bybit rules snapshot (default: DATA_DIR/v3/bybit_rules.json)")
    ap.add_argument("--label", default="")
    ap.add_argument("--max-calls", type=int, default=60_000)
    ap.add_argument("--max-cost", type=float, default=10.0)
    ap.add_argument("--coins", default="", help="development smoke runs only (a different config)")
    ap.add_argument("--timeframes", default="", help="development smoke runs only (a different config)")
    ap.add_argument("--allow-unfrozen", action="store_true", help="development smoke runs only")
    ap.add_argument("--replay-only", action="store_true", help="JEV phase: answer only from the frozen ledger")
    args = ap.parse_args(argv)

    if args.phase == "fingerprints":
        print(json.dumps(fingerprints(), indent=1))
        return 0
    data_dir = Path(os.environ.get("DATA_DIR") or PROJECT / "data")
    db = args.db or str(data_dir / "paperlab.db")
    adir = args.archive_dir or str(data_dir / "v3")
    storage = Storage(db)
    settings = settings_for(20.0, DATA_DIR=adir)
    fps = fingerprints()
    want = frozen()
    prereg = json.loads(PREREG.read_text(encoding="utf-8")) if PREREG.exists() else None
    if not args.allow_unfrozen:
        diff = {k: (v, want.get(k)) for k, v in fps.items() if want.get(k) != v}
        if diff:
            print("V3.1 sources differ from docs/V31_FREEZE.md:", json.dumps(diff), flush=True)
            return 2
    meta = archive.load_meta(settings) or {}
    snap = load_or_fetch(Path(args.rules or (data_dir / "v3" / "bybit_rules.json")), SYMBOLS)
    rules = to_rules(snap)
    ticks = {c: rules[f"{c}USDT"].tick for c in V31Config().coins if f"{c}USDT" in rules}
    common = dict(strategy_fingerprints=tuple(sorted(v31_fingerprints().items())),
                  jev_fingerprints=tuple(sorted(v3_fingerprints().items())),
                  rules_source=f"{snap['source']} @ {snap['fetched_ts']}")
    if args.role == "TEST":
        if prereg is None:
            print("TEST needs docs/V31_TEST_PREREGISTRATION.json (written before any TEST month is downloaded)")
            return 2
        bad = {k: (v, prereg["fingerprints"].get(k)) for k, v in fps.items() if prereg["fingerprints"].get(k) != v}
        if bad:
            print("sources differ from the TEST pre-registration:", json.dumps(bad))
            return 2
        cfg = V31Config(dataset_role="TEST", months=TEST_MONTHS, trade_from=TEST_FROM, trade_to=TEST_TO,
                        edge_model_fingerprint=prereg["evidence"]["fingerprint"],
                        dataset_fingerprint=str(meta.get("fingerprint") or ""), **common)
        if cfg.fingerprint(ignore_dataset=True) != prereg["config_fingerprint"]:
            print(f"TEST config {cfg.fingerprint(ignore_dataset=True)} != pre-registered {prereg['config_fingerprint']}")
            return 2
        evidence, ev_stats = vr.evidence_for(storage, prereg["evidence"]["run_id"], day_ms(DEV_TO) + 86_400_000 - 1)
        if ev_stats["fingerprint"] != prereg["evidence"]["fingerprint"]:
            print("edge-model evidence differs from the pre-registration:", ev_stats["fingerprint"], prereg["evidence"]["fingerprint"])
            return 2
    else:
        sub = {}
        if args.coins:
            sub["coins"] = tuple(args.coins.split(","))
        if args.timeframes:
            sub["timeframes"] = tuple(args.timeframes.split(","))
        cfg = V31Config(dataset_fingerprint=str(meta.get("fingerprint") or ""), **common, **sub)
        evidence, ev_stats = [], {}
    run_id = args.run_id
    if args.phase in ("all", "observe", "control") and not run_id:
        label = args.label or (f"V3.1 AGGRESSIVE EDGE - {cfg.dataset_role} {cfg.trade_from}..{cfg.trade_to}")
        run_id = vr.new_run(storage, cfg, label, extra={"bybit_rules": snap, "archive": {k: meta.get(k) for k in (
            "fingerprint", "rows", "first_month", "last_month")}, "prereg": prereg if args.role == "TEST" else None})
        if args.role == "TEST":
            storage.v31_run_update(run_id, field_json=prereg["field"], evidence_json=ev_stats)
    if not run_id:
        print("--run-id is required for this phase")
        return 2
    if run_id in frozen_runs() and args.phase != "status":
        print(f"{run_id} is FROZEN (docs/V31_RESULTS_FREEZE.md): no phase may run on it")
        return 2
    run = storage.v31_run(run_id)
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
          f"dataset {cfg.dataset_fingerprint}", flush=True)
    t0 = time.time()

    def progress(r, i, n):
        if r["ok"]:
            rec = r["record"]
            a = rec["activity"]
            f = rec.get("funnel") or {}
            print(f"  [{i:>3}/{n}] {rec['key']:<34} signals={a['signals']:>5} "
                  f"{'seq=' + str(a.get('sequenced')) if rec['role'] == 'RAW' else 'executed=' + str(f.get('executed'))} "
                  f"{rec['elapsed_s']:>6.1f}s", flush=True)
        else:
            print(f"  [{i:>3}/{n}] ERROR {r['ident']} {r['error']}", flush=True)

    def phase(name: str, jobs: list, ev: list) -> None:
        storage.v31_run_update(run_id, stage=name, progress_json={"phase": name, "jobs": len(jobs), "started_ts": int(time.time() * 1000)})
        print(f"-- {name}: {len(jobs)} jobs", flush=True)
        vr.run_jobs(storage, run_id, jobs, settings, cfg, rules, db, args.workers, ev, progress)

    if args.phase == "status":
        print(json.dumps({"stage": run.get("stage"), "status": run.get("status"), "bots": len(storage.v31_bot_keys(run_id)),
                          "progress": run.get("progress"), "evidence": run.get("evidence")}, indent=1, default=str))
        return 0
    if cfg.dataset_role == "DEVELOPMENT":
        if args.phase in ("all", "observe"):
            phase("OBSERVE", vr.grid_jobs(cfg, run_id, "RAW", "observer"), [])
        if args.phase in ("all", "control", "field", "jev", "take", "random", "capacity"):
            evidence, ev_stats = vr.evidence_for(storage, run_id)
            storage.v31_run_update(run_id, evidence_json=ev_stats)
            print(f"-- EVIDENCE: {ev_stats['observations']} sequenced observations from {ev_stats['raw_bots']} RAW bots "
                  f"(fingerprint {ev_stats['fingerprint']})", flush=True)
        if args.phase in ("all", "control"):
            phase("CONTROL", vr.grid_jobs(cfg, run_id, "CONTROL", "none"), evidence)
        if args.phase in ("all", "field"):
            field = select_field(storage.v31_bots(run_id, role="CONTROL"), cfg)
            storage.v31_run_update(run_id, field_json=field, stage="FIELD")
            print(f"-- FIELD: {field['n']} pairs (minimum {cfg.field_min_pairs}, target {cfg.field_target_pairs}); "
                  f"thin slots {len(field['thin_slots'])}", flush=True)
    else:
        if args.phase in ("all", "control"):
            phase("CONTROL", vr.grid_jobs(cfg, run_id, "CONTROL", "none"), evidence)
    field = (storage.v31_run(run_id) or {}).get("field") or {"pairs": []}
    if args.phase in ("all", "jev"):
        jc = JevConfig.from_env()
        if not args.replay_only and (not os.environ.get("OPENROUTER_API_KEY") or not jc.enabled):
            print("Jev is not configured here (OPENROUTER_API_KEY / JEV_ENABLED): run the JEV phase on the server.")
            return 2
        phase("JEV", vr.twin_jobs(field, run_id, "jev", model=jc.model or DEFAULT_MODEL, replay_only=args.replay_only,
                                  max_calls=args.max_calls, max_cost_usd=args.max_cost), evidence)
    if args.phase in ("all", "take"):
        phase("TAKE", vr.twin_jobs(field, run_id, "take"), evidence)
    if args.phase in ("all", "random"):
        phase("RANDOM", vr.random_jobs(storage, run_id, field, cfg.random_seeds), evidence)
    if args.phase in ("all", "capacity"):
        phase("CAPACITY", vr.capacity_jobs(field, run_id, cfg.capacity_balances), evidence)
    if args.phase in ("all", "analyze"):
        storage.v31_run_update(run_id, stage="ANALYZE")
        dev = None
        if cfg.dataset_role == "TEST":
            dev = (storage.v31_run(prereg["evidence"]["run_id"]) or {}).get("summary") or {}
        summary = vr.analyze_run(storage, run_id, cfg, settings, dev, ticks)
        print(json.dumps({"answers": summary["answers"], "counts": summary["counts"], "states": summary["states"],
                          "failure_modes": {k: v for k, v in summary["failure_modes"].items() if k != "labels"},
                          "advanced_set": summary["advanced_set"]}, indent=1, default=str), flush=True)
    print(f"done in {time.time() - t0:.0f}s", flush=True)
    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
