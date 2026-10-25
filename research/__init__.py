"""Round history index — populated by the runner as rounds complete.

The historical rounds 1..8 referenced in the user's RESEARCH_HISTORY.md
were executed in a different workspace and are NOT re-derived here.
This file is the index of rounds 9+ that the in-repo runner produced.
"""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

def append_summary(round_id: int, payload: dict) -> None:
    out = LOG_DIR / f"round_{round_id:02d}_summary.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True))

def load_summary(round_id: int) -> dict | None:
    p = LOG_DIR / f"round_{round_id:02d}_summary.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())

def list_rounds() -> list[int]:
    return sorted(int(p.stem.split("_")[1]) for p in LOG_DIR.glob("round_*_summary.json"))
