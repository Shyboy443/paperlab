"""Freeze V12 BIZZY before its live paper experiment starts: docs/V12_FREEZE.json.

    python scripts/v12_freeze.py [--force]

The manifest pins the V12 code (Bizzy's strategy, config, worker, Jev question, and the V11 feed / engine / gate files
it imports), parameters and profile (coins, execution window, book, sizing, qualification), the V6 shared-dependency
freeze it runs on, and the Bybit instrument rules and funding intervals of every coin (public Bybit data). The worker
refuses to trade while the running code differs from this file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))

from app.competition import v12_config as v12  # noqa: E402
from v8_freeze import rules_and_intervals  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite an existing freeze (a NEW experiment)")
    args = ap.parse_args()
    if v12.FREEZE_FILE.exists() and not args.force:
        print("docs/V12_FREEZE.json exists; --force starts a NEW experiment")
        return 1
    universe = list(v12.UNIVERSE)
    rules, intervals = rules_and_intervals(sorted(universe))
    man = {**v12.manifest_core(), "frozen_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rules": {s: rules[s] for s in universe}, "funding_interval_min": intervals,
           "field": [s.key for s in v12.field_plan()],
           "studies": ["docs/V12_BIZZY_STUDY.json"],
           "note": "Bizzy Bee ported from github.com/imikerussell/beebots; judged on LIVE forward paper data only. The "
                   "pre-launch study found the rule LOST after Bybit costs (95 days 1m, ~2.5 years 1h): no edge claimed"}
    man["fingerprint"] = v12.fingerprint(man)
    v12.FREEZE_FILE.write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"fingerprint": man["fingerprint"], "bots": man["field"], "coins": len(universe),
                      "verify": v12.verify_freeze(v12.load_freeze())}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
