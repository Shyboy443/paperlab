"""Freeze V8 SCALP before its live paper experiment starts: docs/V8_FREEZE.json.

    python scripts/v8_freeze.py [--force]

The manifest pins the V8 code (strategies, config, worker), parameters and profile (execution window, elimination,
qualification), the V6 shared-dependency freeze it runs on, and the Bybit instrument rules and funding intervals of
the six coins (public Bybit data). The worker refuses to trade while the running code differs from this file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import bybit_archive as bb  # noqa: E402
from app.competition import v8_config as v8  # noqa: E402


def decimals(x: object) -> int:
    s = str(x)
    return max(0, len(s.rstrip("0").split(".")[1])) if "." in s else 0


def rules_and_intervals(symbols: list[str]) -> tuple[dict[str, dict], dict[str, int]]:
    inst = bb.instruments()
    missing = [s for s in symbols if s not in inst]
    if missing:
        raise SystemExit("not listed on Bybit linear: " + ", ".join(missing))
    rules = {s: {"symbol": s, "tick": float(inst[s]["tick"]), "step": float(inst[s]["step"]),
                 "min_qty": float(inst[s]["min_qty"]), "min_notional": float(inst[s]["min_notional"]),
                 "maint_margin_rate": 0.025, "price_precision": decimals(inst[s]["tick"]) + 2,
                 "qty_precision": decimals(inst[s]["step"]) + 2} for s in symbols}
    return rules, {s: int(inst[s]["funding_interval_min"]) for s in symbols}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite an existing freeze (a NEW experiment)")
    args = ap.parse_args()
    if v8.FREEZE_FILE.exists() and not args.force:
        print("docs/V8_FREEZE.json exists; --force starts a NEW experiment")
        return 1
    traded = [c + "USDT" for c in v8.COINS]
    rules, intervals = rules_and_intervals(sorted(set(traded + list(v8.CONTEXT))))
    man = {**v8.manifest_core(), "frozen_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rules": {s: rules[s] for s in traded}, "funding_interval_min": intervals,
           "field": [s.key for s in v8.field_plan(v8.COINS)],
           "note": "V8 is judged on LIVE forward paper data only; aggressive by design, no edge claimed"}
    man["fingerprint"] = v8.fingerprint(man)
    v8.FREEZE_FILE.write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"fingerprint": man["fingerprint"], "bots": len(man["field"]), "coins": v8.COINS,
                      "verify": v8.verify_freeze(v8.load_freeze())}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
