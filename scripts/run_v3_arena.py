"""V3 AGGRESSIVE DISCOVERY (docs/V3_PROTOCOL.md). Runs where the OpenRouter key is: the Railway service.

    python scripts/run_v3_arena.py all --workers 18                 # SCAN, FIELD, JEV, TAKE, RANDOM, ANALYZE
    python scripts/run_v3_arena.py scan --run-id v3-xxxx            # resume one phase
    python scripts/run_v3_arena.py analyze --run-id v3-xxxx         # re-analyze (no replay, no request)
    python scripts/run_v3_arena.py scan --allow-unfrozen --coins SUI --timeframes 5m   # development only

The price tape comes from the V3 archive (DATA_DIR/v3/archive, fetched with scripts/fetch_archive.py);
results go to DATA_DIR/paperlab.db. The run refuses to start when the V3.0 strategy sources or the Jev
V2 prompt/policy differ from docs/V3_FREEZE.md.
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
from app.ai.jev.v2 import v2_fingerprints  # noqa: E402
from app.backtest import archive  # noqa: E402
from app.competition import v3_run as vr  # noqa: E402
from app.competition.v3_config import SYMBOLS, V3Config  # noqa: E402
from app.competition.v3_venue import load_or_fetch, to_rules  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.strategies.registry import v3_fingerprints  # noqa: E402
from scripts.backtest import settings_for  # noqa: E402

FREEZE = PROJECT / "docs" / "V3_FREEZE.md"


def frozen_runs() -> set[str]:
    """Runs whose results are frozen evidence (docs/V3_RESULTS_FREEZE.md): never re-analyzed."""
    doc = PROJECT / "docs" / "V3_RESULTS_FREEZE.md"
    if not doc.exists():
        return set()
    return set(re.findall(r"^\|\s*`(v3-[0-9a-f]+)`\s*\|\s*frozen", doc.read_text(encoding="utf-8"), re.M))


def frozen() -> dict[str, str]:
    if not FREEZE.exists():
        return {}
    return dict(re.findall(r"^\|\s*([A-Za-z0-9_]+)\s*\|\s*`([0-9a-f]{12,16})`", FREEZE.read_text(encoding="utf-8"), re.M))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["all", "scan", "field", "jev", "take", "random", "analyze", "status"])
    ap.add_argument("--run-id", default="")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--db", default="")
    ap.add_argument("--archive-dir", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--coins", default="")
    ap.add_argument("--timeframes", default="")
    ap.add_argument("--max-calls", type=int, default=60_000)
    ap.add_argument("--max-cost", type=float, default=10.0)
    ap.add_argument("--allow-unfrozen", action="store_true")
    ap.add_argument("--replay-only", action="store_true", help="JEV phase: answer only from the frozen ledger")
    args = ap.parse_args(argv)

    data_dir = Path(os.environ.get("DATA_DIR") or PROJECT / "data")
    db = args.db or str(data_dir / "paperlab.db")
    adir = args.archive_dir or str(data_dir / "v3")
    storage = Storage(db)
    settings = settings_for(20.0, DATA_DIR=adir)
    fps = {**v3_fingerprints(), **{k: v for k, v in v2_fingerprints().items() if k in ("prompt", "policy")}}
    want = frozen()
    if not args.allow_unfrozen:
        diff = {k: (v, want.get(k)) for k, v in fps.items() if want.get(k) != v}
        if diff:
            print("V3 sources differ from docs/V3_FREEZE.md:", json.dumps(diff), flush=True)
            return 2
    meta = archive.load_meta(settings) or {}
    snap = load_or_fetch(Path(adir) / "bybit_rules.json", SYMBOLS)
    rules = to_rules(snap)
    base = V3Config()
    cfg = V3Config(coins=tuple(args.coins.split(",")) if args.coins else base.coins,
                   scan_timeframes=tuple(args.timeframes.split(",")) if args.timeframes else base.scan_timeframes,
                   strategy_fingerprints=tuple(sorted(v3_fingerprints().items())),
                   jev_fingerprints=tuple(sorted(v2_fingerprints().items())),
                   rules_source=f"{snap['source']} @ {snap['fetched_ts']}",
                   dataset_fingerprint=str(meta.get("fingerprint") or ""))
    run_id = args.run_id
    if args.phase in ("all", "scan") and not run_id:
        run_id = vr.new_run(storage, cfg, args.label or "V3 AGGRESSIVE DISCOVERY - DEVELOPMENT 2025-11..2026-04",
                            extra={"bybit_rules": snap, "archive": {k: meta.get(k) for k in ("fingerprint", "rows", "first_month", "last_month")}})
    if not run_id:
        print("--run-id is required for this phase")
        return 2
    run = storage.v3_run(run_id)
    if run_id in frozen_runs() and args.phase != "status":
        print(f"{run_id} is FROZEN (docs/V3_RESULTS_FREEZE.md): no phase may run on it")
        return 2
    if run is None:
        print("no such run", run_id)
        return 2
    if run.get("config_fingerprint") != cfg.fingerprint() and args.phase != "status":
        print(f"config changed since {run_id} started ({run.get('config_fingerprint')} != {cfg.fingerprint()})")
        return 2
    print(f"run {run_id}  phase {args.phase}  workers {args.workers}  config {cfg.fingerprint()}  "
          f"dataset {cfg.dataset_fingerprint}  coins {len(cfg.coins)}", flush=True)
    t0 = time.time()

    def progress(r, i, n):
        if r["ok"]:
            rec = r["record"]
            a = rec["activity"]
            print(f"  [{i:>3}/{n}] {rec['key']:<30} signals={a['signals']:>5} entries={a['entries']:>5} "
                  f"decisions={a['gate_decisions']:>5} {rec['elapsed_s']:>6.1f}s", flush=True)
        else:
            print(f"  [{i:>3}/{n}] ERROR {r['ident']} {r['error']}", flush=True)

    def phase(name: str, jobs: list) -> None:
        storage.v3_run_update(run_id, stage=name, progress_json={"phase": name, "jobs": len(jobs), "started_ts": int(time.time() * 1000)})
        print(f"-- {name}: {len(jobs)} jobs", flush=True)
        vr.run_jobs(storage, run_id, jobs, settings, cfg, rules, db, args.workers, progress)

    if args.phase == "status":
        print(json.dumps({"stage": run.get("stage"), "status": run.get("status"), "bots": len(storage.v3_bot_keys(run_id)),
                          "progress": run.get("progress")}, indent=1))
        return 0
    if args.phase in ("all", "scan"):
        phase("SCAN", vr.scan_jobs(cfg, run_id))
    if args.phase in ("all", "field"):
        field = vr.select_field(storage.v3_bots(run_id, role="CONTROL"), cfg)
        storage.v3_run_update(run_id, field_json=field, stage="FIELD")
        print(f"-- FIELD: {len(field['pairs'])} pairs, {len(field['empty_slots'])} empty slots", flush=True)
        if len(field["pairs"]) < cfg.min_active_jev_bots:
            storage.v3_run_update(run_id, status="insufficient_competitors")
            print("INSUFFICIENT_COMPETITORS: fewer than", cfg.min_active_jev_bots, "Jev pairs qualify")
    field = (storage.v3_run(run_id) or {}).get("field") or {"pairs": []}
    if args.phase in ("all", "jev"):
        jc = JevConfig.from_env()
        if not args.replay_only and (not os.environ.get("OPENROUTER_API_KEY") or not jc.enabled):
            print("Jev is not configured here (OPENROUTER_API_KEY / JEV_ENABLED): run the JEV phase on the server.")
            return 2
        phase("JEV", vr.twin_jobs(field, run_id, "jev", model=jc.model or DEFAULT_MODEL, replay_only=args.replay_only,
                                  max_calls=args.max_calls, max_cost_usd=args.max_cost))
    if args.phase in ("all", "take"):
        phase("TAKE", vr.twin_jobs(field, run_id, "take"))
    if args.phase in ("all", "random"):
        phase("RANDOM", vr.random_jobs(storage, run_id, field, cfg.random_seeds))
    if args.phase in ("all", "analyze"):
        storage.v3_run_update(run_id, stage="ANALYZE")
        tables = vr.regime_tables(cfg)
        summary = vr.analyze_run(storage, run_id, cfg, tables)
        print(json.dumps({"answers": summary["answers"], "counts": summary["counts"],
                          "failure_modes": summary["failure_modes"]["jev_bots"],
                          "states": summary["states"]}, indent=1, default=str), flush=True)
    print(f"done in {time.time() - t0:.0f}s", flush=True)
    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
