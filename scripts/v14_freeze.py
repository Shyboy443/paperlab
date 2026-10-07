"""Freeze V14 HTF before its live paper experiment starts: docs/V14_FREEZE.json.

    python scripts/v14_freeze.py [--force]

The manifest pins the V14 code (the HTF wrapper, config, worker) and every copied object it runs (the V8.3, V11.1,
V11.2 and V13 strategy classes and Params, their engines, the V11 ladder, the scan feed and gate helpers), the profile
(field, HTF rule, universe, execution window, book, sizing, qualification), the V6 shared-dependency freeze it runs on,
and the Bybit instrument rules and funding intervals of every coin (public Bybit data). The worker refuses to trade while the running code
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

from app.competition import v14_config as v14  # noqa: E402
from v8_freeze import rules_and_intervals  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite an existing freeze (a NEW experiment)")
    args = ap.parse_args()
    if v14.FREEZE_FILE.exists() and not args.force:
        print("docs/V14_FREEZE.json exists; --force starts a NEW experiment")
        return 1
    study = json.loads((PROJECT / "docs" / "V14_HTF_STUDY.json").read_text(encoding="utf-8"))
    universe = list(v14.UNIVERSE)
    rules, intervals = rules_and_intervals(sorted(universe))
    man = {**v14.manifest_core(), "frozen_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rules": {s: rules[s] for s in universe}, "funding_interval_min": intervals,
           "field": [s.key for s in v14.field_plan()],
           "studies": ["docs/V14_HTF_STUDY.json", "docs/ALTDATA_STUDY.json"],
           "study_verdicts": study.get("verdicts"),
           "note": "each bot = a copied strategy + the HTF rule; judged against its running original on LIVE paper data"}
    man["fingerprint"] = v14.fingerprint(man)
    v14.FREEZE_FILE.write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"fingerprint": man["fingerprint"], "bots": man["field"], "coins": len(universe),
                      "verdicts": man["study_verdicts"], "verify": v14.verify_freeze(v14.load_freeze())}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
