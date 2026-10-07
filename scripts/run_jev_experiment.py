"""CONTROL vs +JEV paired discovery. Runs where the OpenRouter key is: the Railway service.

    railway ssh -- python scripts/run_jev_experiment.py --dry-run          # count calls, no requests
    railway ssh -- python scripts/run_jev_experiment.py --pairs 30         # the bounded experiment
    railway ssh -- python scripts/run_jev_experiment.py --replay <run_id>  # reproduce: zero calls

Pairs come from a finished arena run (default: the latest) using the arena's own deterministic
rotation, never by results. Every Jev answer is written to the run's decision ledger before the
replay moves on; replaying the run reads that ledger and makes no network request at all.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ai.jev.models import DEFAULT_MODEL, JevConfig  # noqa: E402
from app.ai.jev.policy import POLICY_V1  # noqa: E402
from app.competition.jev_experiment import JevExperimentConfig, run_experiment  # noqa: E402
from app.core.storage import Storage  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arena-run", default="")
    ap.add_argument("--pairs", type=int, default=30)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-calls", type=int, default=20_000)
    ap.add_argument("--max-cost", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--replay", default="", help="reproduce a finished run from its frozen ledger")
    ap.add_argument("--db", default="")
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv)

    db = args.db or str(Path(os.environ.get("DATA_DIR") or PROJECT / "data") / "paperlab.db")
    storage = Storage(db)
    arena_run = args.arena_run
    if not arena_run:
        runs = storage.arena_runs(limit=1)
        if not runs:
            print("no arena run in", db)
            return 2
        arena_run = runs[0]["run_id"]
    jc = JevConfig.from_env()
    if not args.dry_run and not args.replay:
        if not os.environ.get("OPENROUTER_API_KEY"):
            print("OPENROUTER_API_KEY is not set here: Jev is NOT_CONFIGURED. Run this on the server.")
            return 2
        if not jc.enabled:
            print("JEV_ENABLED is false: no Jev request will be made.")
            return 2
    cfg = JevExperimentConfig(arena_run_id=arena_run, pairs=args.pairs, model=jc.model or DEFAULT_MODEL,
                              policy=POLICY_V1, max_calls=args.max_calls, max_cost_usd=args.max_cost,
                              workers=args.workers, replay_of=args.replay)
    print(f"arena {arena_run}  pairs {args.pairs}  model {cfg.model}  policy {cfg.policy.version}  "
          f"workers {args.workers}  {'DRY RUN' if args.dry_run else 'REPLAY ' + args.replay if args.replay else 'LIVE CALLS'}",
          flush=True)
    t0 = time.time()

    def progress(r, i, n):
        print(f"  [{i:>2}/{n}] {r['key']:<40}{r['role']:<8}{r['state']:<20} signals={r['signals']:>5} "
              f"decisions={r['decisions']:>4} trades={r['trades']:>4} net={r['net']:>+7.2f} "
              f"{r['elapsed_s']:>6.1f}s", flush=True)

    out = run_experiment(storage, db, cfg, label=args.label, dry_run=args.dry_run, on_done=progress)
    print(f"\nfinished in {time.time() - t0:.0f}s")
    if args.dry_run:
        jev = [r for r in out["results"] if r["role"] == "JEV"]
        ctl = [r for r in out["results"] if r["role"] == "CONTROL"]
        print(json.dumps({"pairs": out["pairs"], "months": out["months"],
                          "candidates_if_jev_always_skips (upper bound on calls)": sum(r["decisions"] for r in jev),
                          "control_trades (about the calls if Jev always takes)": sum(r["trades"] for r in ctl),
                          "per_bot": {r["key"]: r["decisions"] for r in jev}}, indent=1))
    else:
        light = {k: v for k, v in out.items() if k not in ("pairs_detail",)}
        print(json.dumps(light, indent=1, default=str))
    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
