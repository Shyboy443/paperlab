"""Freeze V13 SNAPBACK before its live paper experiment starts: docs/V13_FREEZE.json.

    python scripts/v13_freeze.py [--force]

The manifest pins the V13 code (strategy, limit-entry engine, config, worker, and the V11 feed / engine / gate objects it
imports), the parameters chosen by the pre-registered study (docs/V13_SNAPBACK_STUDY.json) and the profile (universe,
execution window, book, sizing, qualification), the V6 shared-dependency freeze it runs on, and the Bybit instrument
rules and funding intervals of every coin (public Bybit data). The worker refuses to trade while the running code
differs from this file.
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

from app.competition import v13_config as v13  # noqa: E402
from v8_freeze import rules_and_intervals  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite an existing freeze (a NEW experiment)")
    args = ap.parse_args()
    if v13.FREEZE_FILE.exists() and not args.force:
        print("docs/V13_FREEZE.json exists; --force starts a NEW experiment")
        return 1
    study = json.loads((PROJECT / "docs" / "V13_SNAPBACK_STUDY.json").read_text(encoding="utf-8"))
    universe = list(v13.UNIVERSE)
    rules, intervals = rules_and_intervals(sorted(universe))
    man = {**v13.manifest_core(), "frozen_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rules": {s: rules[s] for s in universe}, "funding_interval_min": intervals,
           "field": [s.key for s in v13.field_plan()],
           "studies": ["docs/V13_SNAPBACK_STUDY.json", "docs/ALTDATA_STUDY.json"],
           "study_verdict": study.get("verdict"), "study_selected": study.get("selected"),
           "note": "parameters = the study's DEV-selected variant; judged on its TEST verdict and LIVE forward paper data"}
    man["fingerprint"] = v13.fingerprint(man)
    v13.FREEZE_FILE.write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"fingerprint": man["fingerprint"], "bots": man["field"], "coins": len(universe),
                      "verdict": man["study_verdict"], "verify": v13.verify_freeze(v13.load_freeze())}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
