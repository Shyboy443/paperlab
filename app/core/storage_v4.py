"""V4 INTRADAY SPECIALIST tables for Storage (schema 14).

Separate from every earlier program's tables: V1, V2, V3 and V3.1 runs are frozen evidence and no V4 write
can touch them. A V4 run is one dataset role (DEVELOPMENT or TEST); every bot -- RAW observer, CONTROL, +JEV4,
ALWAYS-TAKE, RANDOM seed, CAPACITY -- is one row keyed by its V4 identity, checkpointed as soon as it finishes.
The heavy parts (trades, decisions, equity, and the RAW observation ledger / CONTROL edge-gate rows in
`extra_json`) are only read when needed.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.storage_candidates import _dumps

V4_DDL = [
    """CREATE TABLE IF NOT EXISTS v4_runs(
        run_id TEXT PRIMARY KEY, created_ts INTEGER, finished_ts INTEGER, status TEXT, stage TEXT,
        label TEXT, dataset_role TEXT, config_fingerprint TEXT, config_json TEXT, field_json TEXT,
        progress_json TEXT, summary_json TEXT, evidence_json TEXT, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS v4_bots(
        run_id TEXT NOT NULL, key TEXT NOT NULL, role TEXT, pair_id TEXT, strategy_id TEXT, family TEXT,
        coin TEXT, timeframe TEXT, seed INTEGER, balance REAL, state TEXT, failure_mode TEXT, score REAL,
        record_json TEXT, trades_json TEXT, decisions_json TEXT, equity_json TEXT, extra_json TEXT,
        analysis_json TEXT, updated_ts INTEGER, PRIMARY KEY(run_id, key))""",
    "CREATE INDEX IF NOT EXISTS v4_bots_role ON v4_bots(run_id, role)",
]

_RUN_JSON = ("config_json", "field_json", "progress_json", "summary_json", "evidence_json")
_HEAVY = ("trades", "decisions", "equity", "observations", "edge_rows")


class V4Queries:
    """Requires the host class to provide `self.conn`."""

    def v4_run_start(self, row: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO v4_runs(run_id, created_ts, status, stage, label, dataset_role, "
                "config_fingerprint, config_json, field_json, progress_json, summary_json, evidence_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["run_id"], row["created_ts"], row.get("status", "running"), row.get("stage", "OBSERVE"),
                 row.get("label", ""), row.get("dataset_role", ""), row.get("config_fingerprint", ""),
                 _dumps(row.get("config") or {}), _dumps(row.get("field") or {}), _dumps(row.get("progress") or {}),
                 _dumps(row.get("summary") or {}), _dumps(row.get("evidence") or {})))

    def v4_run_update(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(_dumps(v) if k in _RUN_JSON and not isinstance(v, str) else v)
        with self.conn:
            self.conn.execute(f"UPDATE v4_runs SET {', '.join(cols)} WHERE run_id=?", (*vals, run_id))

    def v4_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM v4_runs ORDER BY created_ts DESC LIMIT ?", (limit,)).fetchall()
        return [self._v4_run(dict(r)) for r in rows]

    def v4_run(self, run_id: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM v4_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._v4_run(dict(r)) if r else None

    @staticmethod
    def _v4_run(d: dict[str, Any]) -> dict[str, Any]:
        for k in _RUN_JSON:
            d[k.replace("_json", "")] = json.loads(d.pop(k) or "null")
        return d

    def v4_bot_save(self, run_id: str, rec: dict[str, Any], ts: int = 0) -> None:
        idn = rec["identity"]
        light = {k: v for k, v in rec.items() if k not in _HEAVY}
        extra = {k: rec[k] for k in ("observations", "edge_rows") if rec.get(k)}
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO v4_bots(run_id, key, role, pair_id, strategy_id, family, coin, timeframe, "
                "seed, balance, state, failure_mode, score, record_json, trades_json, decisions_json, equity_json, "
                "extra_json, analysis_json, updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, rec["key"], rec["role"], rec["pair_id"], idn["strategy_id"], rec.get("family"), idn["coin"],
                 idn["timeframe"], int(idn.get("seed") or 0), float(rec.get("balance") or 0.0), None, None, None,
                 _dumps(light), _dumps(rec.get("trades") or []), _dumps(rec.get("decisions") or []),
                 _dumps(rec.get("equity") or []), _dumps(extra), None, ts))

    def v4_bot_analysis(self, run_id: str, key: str, analysis: dict[str, Any], state: str, failure_mode: str,
                         score: float | None) -> None:
        with self.conn:
            self.conn.execute("UPDATE v4_bots SET analysis_json=?, state=?, failure_mode=?, score=? "
                              "WHERE run_id=? AND key=?", (_dumps(analysis), state, failure_mode, score, run_id, key))

    def v4_bot_keys(self, run_id: str) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT key FROM v4_bots WHERE run_id=?", (run_id,)).fetchall()}

    def v4_bots(self, run_id: str, role: str | None = None, heavy: bool = False, extra: bool = False,
                 keys: list[str] | None = None) -> list[dict[str, Any]]:
        cols = "record_json, analysis_json, state, failure_mode, score" + (
            ", trades_json, decisions_json, equity_json" if heavy else "") + (", extra_json" if extra else "")
        q, args = f"SELECT {cols} FROM v4_bots WHERE run_id=?", [run_id]
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
            if extra:
                d.update(json.loads(r["extra_json"] or "{}"))
            out.append(d)
        return out
