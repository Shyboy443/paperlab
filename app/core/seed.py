"""Import shipped tournament results into the volume database on boot.

Tournaments run where the CPU and the market archive are; the dashboard people open is the Railway
service, which holds only what its live feed backfilled. `scripts/export_results.py` writes
`seeds/results.json.gz` into the repo, `railway up` carries it into the image, and this imports it
into DATA_DIR/paperlab.db the first time the service boots with it.

Rules, so a seed can never destroy a real run:

* **Additive, plus completion.** A run id already present is skipped -- EXCEPT when the stored copy
  is still marked `running` and the incoming one has finished or carries more competitors. Without
  that exception a run first shipped mid-flight stays frozen at its partial snapshot forever, which
  is exactly what happened to an 81-competitor validation seeded at 54. A finished stored run is
  never replaced by an older file.
* **Results only.** Runs, competitors, metrics, ledgers and qualification. No candles, no engine
  state, no credentials.
* **Never fatal.** A missing or corrupt seed logs a warning; the service boots regardless. A
  dashboard with no results is a worse outcome than a crash only in the sense that it is quieter,
  so the warning names the file and the reason.
"""
from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("paperlab.seed")

DEFAULT_SEED = Path(__file__).resolve().parents[2] / "seeds" / "results.json.gz"

# table -> (id column used to decide "already present", columns that identify a row)
RUN_TABLES = {"competition_runs": "run_id", "validation_runs": "run_id", "arena_runs": "run_id",
              "jev_runs": "run_id", "candidate_runs": "run_id"}
CHILD_TABLES = {"competition_competitors": "run_id", "validation_competitors": "run_id",
                "arena_bots": "run_id", "jev_bots": "run_id", "jev_decisions": "run_id",
                "candidate_results": "run_id"}


def load_seed(path: Path = DEFAULT_SEED) -> dict[str, list[dict[str, Any]]] | None:
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rb") as fh:
            return json.loads(fh.read())
    except (OSError, ValueError) as exc:
        log.warning("seed file %s unreadable (%s); skipping", path, exc)
        return None


def _existing_runs(conn: Any, table: str) -> dict[str, str]:
    """run_id -> status for what is already stored."""
    try:
        return {r[0]: (r[1] or "") for r in
                conn.execute(f"SELECT run_id, status FROM {table}").fetchall()}
    except Exception:
        return {}


def _child_counts(conn: Any, table: str) -> dict[str, int]:
    try:
        return {r[0]: r[1] for r in conn.execute(
            f"SELECT run_id, COUNT(*) FROM {table} GROUP BY run_id").fetchall()}
    except Exception:
        return {}


def _supersedes(incoming: dict[str, Any], stored_status: str,
                incoming_children: int, stored_children: int) -> bool:
    """Should the seed replace what is already stored?

    Only when the stored copy is unfinished AND the incoming one is better evidence: either it has
    completed, or it simply knows about more competitors. A stored run that already finished is
    left alone -- results produced on the service outrank a file baked into an image.
    """
    if stored_status not in ("running", "", None):
        return False
    return (incoming.get("status") not in ("running", "", None)
            or incoming_children > stored_children)


def _insert(conn: Any, table: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    cols = list(rows[0])
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT OR IGNORE INTO {table}({','.join(cols)}) VALUES({placeholders})"
    n = 0
    for r in rows:
        try:
            conn.execute(sql, [r.get(c) for c in cols])
            n += 1
        except Exception as exc:                       # one bad row must not abort the import
            log.warning("seed row rejected for %s: %s", table, str(exc)[:160])
    return n


def apply_seed(storage: Any, path: Path = DEFAULT_SEED) -> dict[str, int]:
    """Import any runs the database does not already have. Returns what was added."""
    data = load_seed(path)
    if not data:
        return {}
    added: dict[str, int] = {}
    conn = storage.conn
    wanted: set[str] = set()
    replaced: set[str] = set()
    child_of = {"competition_runs": "competition_competitors",
                "validation_runs": "validation_competitors",
                "arena_runs": "arena_bots", "jev_runs": "jev_bots", "candidate_runs": "candidate_results"}
    for table in RUN_TABLES:
        have = _existing_runs(conn, table)
        child_table = child_of[table]
        stored_counts = _child_counts(conn, child_table)
        incoming_counts: dict[str, int] = {}
        for r in data.get(child_table, []):
            rid = r.get("run_id")
            incoming_counts[rid] = incoming_counts.get(rid, 0) + 1
        fresh, refresh = [], []
        for r in data.get(table, []):
            rid = r.get("run_id")
            if rid not in have:
                fresh.append(r)
            elif _supersedes(r, have[rid], incoming_counts.get(rid, 0), stored_counts.get(rid, 0)):
                refresh.append(r)
        wanted |= {r["run_id"] for r in fresh} | {r["run_id"] for r in refresh}
        replaced |= {r["run_id"] for r in refresh}
        if fresh or refresh:
            with conn:
                if refresh:
                    ids = [r["run_id"] for r in refresh]
                    marks = ",".join("?" for _ in ids)
                    conn.execute(f"DELETE FROM {child_table} WHERE run_id IN ({marks})", ids)
                    conn.execute(f"DELETE FROM {table} WHERE run_id IN ({marks})", ids)
                added[table] = _insert(conn, table, fresh + refresh)
    for table in CHILD_TABLES:
        fresh = [r for r in data.get(table, []) if r.get("run_id") in wanted]
        if fresh:
            with conn:
                added[table] = _insert(conn, table, fresh)
    if replaced:
        log.info("seed refreshed unfinished runs: %s", ", ".join(sorted(replaced)))
    if added:
        log.info("seeded tournament results from %s: %s", path.name,
                 ", ".join(f"{k}={v}" for k, v in added.items()))
    else:
        log.info("seed %s already applied; nothing to import", path.name)
    return added
