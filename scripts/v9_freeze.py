"""Freeze V9 STOCKS: write docs/V9_FREEZE.json from the running code, parameters, profile and instrument rules.

    python scripts/v9_freeze.py            (refuses to overwrite; --force starts a NEW experiment)

Offline: the stock rules (whole shares, 1-cent ticks, Reg T maintenance) are fixed in app/competition/v9_config.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.competition import v9_config as v9  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite an existing freeze (a NEW experiment)")
    args = ap.parse_args()
    if v9.FREEZE_FILE.exists() and not args.force:
        print("docs/V9_FREEZE.json exists; --force starts a NEW experiment")
        return 1
    man = {**v9.manifest_core(), "frozen_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "field": [s.key for s in v9.field_plan(v9.SYMBOLS)],
           "note": "V9 is judged on LIVE forward paper data only (Alpaca IEX, regular sessions); no edge claimed"}
    man["fingerprint"] = v9.fingerprint(man)
    v9.FREEZE_FILE.write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"fingerprint": man["fingerprint"], "bots": len(man["field"]), "symbols": v9.SYMBOLS,
                      "verify": v9.verify_freeze(v9.load_freeze())}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
