"""Candidate (frozen v2) multi-year validation tables for Storage (schema 10).

HISTORICAL validation only. Live forward shadow evidence lives in the shadow_* tables and is never
written here: the two are shown side by side and their PnLs are never added together.
"""
from __future__ import annotations

import json
import math
from typing import Any

CANDIDATE_DDL = [
    """CREATE TABLE IF NOT EXISTS candidate_runs(
        run_id TEXT PRIMARY KEY, created_ts INTEGER, finished_ts INTEGER, status TEXT, label TEXT,
        first_month TEXT, last_month TEXT, venue TEXT, source_test_run TEXT,
        protocol_fingerprint TEXT, protocol_json TEXT, progress_json TEXT, summary_json TEXT,
        dataset_json TEXT, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS candidate_results(
        run_id TEXT NOT NULL, key TEXT NOT NULL, manifest_fingerprint TEXT, verdict TEXT,
        result_json TEXT, finished_ts INTEGER, PRIMARY KEY(run_id, key))""",
]


def finite(obj: Any) -> Any:
    """JSON a browser can parse: no Infinity / NaN (profit factor with no losers is 'inf')."""
    if isinstance(obj, float):
        if math.isnan(obj):
            return None
        if math.isinf(obj):
            return 999.0 if obj > 0 else -999.0
        return obj
    if isinstance(obj, dict):
        return {k: finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [finite(v) for v in obj]
    return obj


def _dumps(obj: Any) -> str:
    return json.dumps(finite(obj), separators=(",", ":"), default=str)


class CandidateQueries:
    """Requires the host class to provide `self.conn`."""

    def candidate_run_start(self, row: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO candidate_runs(run_id, created_ts, status, label, first_month, last_month, "
                "venue, source_test_run, protocol_fingerprint, protocol_json, dataset_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (row["run_id"], row["created_ts"], row.get("status", "running"), row.get("label"),
                 row.get("first_month"), row.get("last_month"), row.get("venue"), row.get("source_test_run"),
                 row.get("protocol_fingerprint"), _dumps(row.get("protocol")), _dumps(row.get("dataset"))))

    def candidate_run_update(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(_dumps(v) if k.endswith("_json") and not isinstance(v, (str, type(None))) else v)
        with self.conn:
            self.conn.execute(f"UPDATE candidate_runs SET {', '.join(cols)} WHERE run_id=?", (*vals, run_id))

    def candidate_result_save(self, run_id: str, key: str, result: dict[str, Any], ts: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO candidate_results(run_id, key, manifest_fingerprint, verdict, result_json, "
                "finished_ts) VALUES(?,?,?,?,?,?)",
                (run_id, key, (result.get("manifest") or {}).get("manifest_fingerprint"), result.get("verdict"),
                 _dumps(result), ts))

    def candidate_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM candidate_runs ORDER BY created_ts DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("protocol_json", "progress_json", "summary_json", "dataset_json"):
                d[k[:-5]] = json.loads(d.pop(k) or "null")
            out.append(d)
        return out

    def candidate_results(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT result_json FROM candidate_results WHERE run_id=? ORDER BY key",
                                 (run_id,)).fetchall()
        return [json.loads(r["result_json"] or "{}") for r in rows]
