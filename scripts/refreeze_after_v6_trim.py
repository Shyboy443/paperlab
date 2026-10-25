"""Re-freeze V6 and every program built on it after the 2026-10-12 field trim (operator: remove the retired bots).

    python scripts/refreeze_after_v6_trim.py

V6 keeps its frozen universe, breadth set, instrument rules and funding intervals (no re-ranking of coins): only the
code / params / config / Jev fingerprints and the field are refreshed. Every dependent program (V7, V8, V9, V11, V12,
V13, V14, V16, V17) keeps its own rules and universe and refreshes only its manifest core (which carries the new V6
baseline fingerprint). Each new fingerprint is a NEW forward experiment: those programs start fresh books; the old
databases stay as history. Every freeze is verified after writing; the script fails loudly otherwise.
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ai.jev.v6 import v6_fingerprints as jev_fingerprints  # noqa: E402
from app.competition import v6_config as c6  # noqa: E402

NOTE = "re-frozen 2026-10-12: V6 field trimmed to V6.2/V6.6 (operator removed the families retired from view)"
DEPENDENTS = ("v8", "v9", "v11", "v12", "v13", "v14", "v16", "v17")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write(path: Path, man: dict) -> None:
    path.write_text(json.dumps(man, indent=1), encoding="utf-8")


def main() -> int:
    old = c6.load_freeze()
    traded = list(old["universe"]["traded"])
    man = dict(old)
    man.update(code=c6.code_fingerprints(), params=c6.params_snapshot(),
               config=json.loads(json.dumps(c6.config_snapshot(), default=str)), jev=jev_fingerprints(),
               field=[s.key for s in c6.field_plan(traded)], frozen_at=now(), refrozen_from=old["fingerprint"], refreeze_note=NOTE)
    man["fingerprint"] = c6.manifest_fingerprint(man)
    write(c6.FREEZE_FILE, man)
    problems = {"v6": c6.verify_freeze(c6.load_freeze())}
    print(f"v6  {old['fingerprint']} -> {man['fingerprint']}  traded {traded}  field {len(man['field'])}")

    v7 = importlib.import_module("app.competition.v7_config")
    before = (v7.load_freeze() or {}).get("fingerprint")
    write(v7.FREEZE_FILE, v7.manifest())
    problems["v7"] = v7.verify_freeze(v7.load_freeze())
    print(f"v7  {before} -> {v7.load_freeze()['fingerprint']}")

    for name in DEPENDENTS:
        mod = importlib.import_module(f"app.competition.{name}_config")
        m = dict(mod.load_freeze() or {})
        before = m.get("fingerprint")
        m.update(mod.manifest_core())
        m.update(frozen_at=now(), refrozen_from=before, refreeze_note=NOTE)
        m["fingerprint"] = mod.fingerprint(m)
        write(mod.FREEZE_FILE, m)
        problems[name] = mod.verify_freeze(mod.load_freeze())
        print(f"{name:3s} {before} -> {m['fingerprint']}")

    bad = {k: v for k, v in problems.items() if v}
    if bad:
        print("FREEZE VERIFICATION FAILED:", json.dumps(bad, indent=1))
        return 1
    print("every freeze verifies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
