"""Freeze V11 SCAN before its live paper experiment starts: docs/V11_FREEZE.json.

    python scripts/v11_freeze.py [--force]

The manifest pins the V11 code (scanner strategies, config, feed, worker), parameters and profile (the 30-coin
universe, execution window, books, elimination, qualification), the V6 shared-dependency freeze it runs on, and the
Bybit instrument rules and funding intervals of every coin (public Bybit data). The worker refuses to trade while the
running code differs from this file.
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

from app.competition import v11_config as v11  # noqa: E402
from v8_freeze import rules_and_intervals  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite an existing freeze (a NEW experiment)")
    args = ap.parse_args()
    if v11.FREEZE_FILE.exists() and not args.force:
        print("docs/V11_FREEZE.json exists; --force starts a NEW experiment")
        return 1
    universe = list(v11.UNIVERSE)
    rules, intervals = rules_and_intervals(sorted(universe))
    man = {**v11.manifest_core(), "frozen_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rules": {s: rules[s] for s in universe}, "funding_interval_min": intervals,
           "field": [s.key for s in v11.field_plan()],
           "studies": ["docs/V11_SCAN_STUDY.json", "docs/V11_FEATURE_STUDY.json", "docs/V11_REVERSAL_STUDY.json",
                       "docs/V11_ACTIVITY_CHECK.json"],
           "note": "V11 is judged on LIVE forward paper data only; the pre-launch study found no scanner rule that beats "
                   "costs, so no edge is claimed"}
    man["fingerprint"] = v11.fingerprint(man)
    v11.FREEZE_FILE.write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"fingerprint": man["fingerprint"], "bots": man["field"], "coins": len(universe),
                      "verify": v11.verify_freeze(v11.load_freeze())}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
