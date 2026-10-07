"""V3 AGGRESSIVE INTRADAY JEV ARENA tables for Storage (schema 12).

A run is one discovery on one dataset role (DEVELOPMENT). Every bot -- CONTROL, +JEV2, ALWAYS-TAKE,
RANDOM seed -- is one row, keyed by its V3 identity, checkpointed as soon as it finishes so a run can
resume. The heavy parts (trade ledger, decisions, equity) sit in their own columns and are only read
for a bot's detail view. Jev V2 answers go to the shared jev_decisions ledger (by run id), exactly
like every other Jev experiment, so a replay reproduces them without a request.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.storage_candidates import _dumps

V3_DDL = [
    """CREATE TABLE IF NOT EXISTS v3_runs(
        run_id TEXT PRIMARY KEY, created_ts INTEGER, finished_ts INTEGER, status TEXT, stage TEXT,
        label TEXT, dataset_role TEXT, config_fingerprint TEXT, config_json TEXT, field_json TEXT,
        progress_json TEXT, summary_json TEXT, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS v3_bots(
        run_id TEXT NOT NULL, key TEXT NOT NULL, role TEXT, pair_id TEXT, strategy_id TEXT, family TEXT,
        coin TEXT, timeframe TEXT, seed INTEGER, experimental INTEGER, state TEXT, failure_mode TEXT,
        score REAL, record_json TEXT, trades_json TEXT, decisions_json TEXT, equity_json TEXT,
        analysis_json TEXT, updated_ts INTEGER, PRIMARY KEY(run_id, key))""",
    "CREATE INDEX IF NOT EXISTS v3_bots_role ON v3_bots(run_id, role)",
]

_RUN_JSON = ("config_json", "field_json", "progress_json", "summary_json")


class V3Queries:
    """Requires the host class to provide `self.conn`."""

    def v3_run_start(self, row: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO v3_runs(run_id, created_ts, status, stage, label, dataset_role, "
                "config_fingerprint, config_json, field_json, progress_json, summary_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (row["run_id"], row["created_ts"], row.get("status", "running"), row.get("stage", "SCAN"),
                 row.get("label", ""), row.get("dataset_role", ""), row.get("config_fingerprint", ""),
                 _dumps(row.get("config") or {}), _dumps(row.get("field") or {}), _dumps(row.get("progress") or {}),
                 _dumps(row.get("summary") or {})))

    def v3_run_update(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(_dumps(v) if k in _RUN_JSON and not isinstance(v, str) else v)
        with self.conn:
            self.conn.execute(f"UPDATE v3_runs SET {', '.join(cols)} WHERE run_id=?", (*vals, run_id))

    def v3_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM v3_runs ORDER BY created_ts DESC LIMIT ?", (limit,)).fetchall()
        return [self._v3_run(dict(r)) for r in rows]

    def v3_run(self, run_id: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM v3_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._v3_run(dict(r)) if r else None

    @staticmethod
    def _v3_run(d: dict[str, Any]) -> dict[str, Any]:
        for k in _RUN_JSON:
            d[k.replace("_json", "")] = json.loads(d.pop(k) or "null")
        return d

    def v3_bot_save(self, run_id: str, rec: dict[str, Any], analysis: dict[str, Any] | None = None,
                    state: str | None = None, failure_mode: str | None = None, score: float | None = None,
                    ts: int = 0) -> None:
        idn = rec["identity"]
        light = {k: v for k, v in rec.items() if k not in ("trades", "decisions", "equity")}
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO v3_bots(run_id, key, role, pair_id, strategy_id, family, coin, timeframe, seed, "
                "experimental, state, failure_mode, score, record_json, trades_json, decisions_json, equity_json, "
                "analysis_json, updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, rec["key"], rec["role"], rec["pair_id"], idn["strategy_id"], rec.get("family"), idn["coin"],
                 idn["timeframe"], int(idn.get("seed") or 0), int(bool(rec.get("experimental"))), state, failure_mode,
                 score, _dumps(light), _dumps(rec.get("trades") or []), _dumps(rec.get("decisions") or []),
                 _dumps(rec.get("equity") or []), _dumps(analysis) if analysis is not None else None, ts))

    def v3_bot_analysis(self, run_id: str, key: str, analysis: dict[str, Any], state: str, failure_mode: str,
                        score: float | None) -> None:
        with self.conn:
            self.conn.execute("UPDATE v3_bots SET analysis_json=?, state=?, failure_mode=?, score=? "
                              "WHERE run_id=? AND key=?", (_dumps(analysis), state, failure_mode, score, run_id, key))

    def v3_bot_keys(self, run_id: str) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT key FROM v3_bots WHERE run_id=?", (run_id,)).fetchall()}

    def v3_bots(self, run_id: str, role: str | None = None, heavy: bool = False,
                keys: list[str] | None = None) -> list[dict[str, Any]]:
        cols = "record_json, analysis_json, state, failure_mode, score" + (
            ", trades_json, decisions_json, equity_json" if heavy else "")
        q, args = f"SELECT {cols} FROM v3_bots WHERE run_id=?", [run_id]
        if role:
            q += " AND role=?"
            args.append(role)
        if keys is not None:
            if not keys:
                return []
            q += f" AND key IN ({','.join('?' for _ in keys)})"
            args.extend(keys)
        out = []
        for r in self.conn.execute(q + " ORDER BY key", args).fetchall():
            d = json.loads(r["record_json"] or "{}")
            d["analysis"] = json.loads(r["analysis_json"] or "null")
            d["state"], d["failure_mode"], d["score"] = r["state"], r["failure_mode"], r["score"]
            if heavy:
                d["trades"] = json.loads(r["trades_json"] or "[]")
                d["decisions"] = json.loads(r["decisions_json"] or "[]")
                d["equity"] = json.loads(r["equity_json"] or "[]")
            out.append(d)
        return out
