"""Live shadow tables for Storage (schema 9). Mixed into `Storage` like storage_analysis.

Every live shadow boot is a SESSION. Sessions with the same identity form one FORWARD EXPERIMENT
with one continuous book per bot: the first session starts at the starting balance, every later one
re-derives the book from the experiment's forward start and records only what no session recorded
before (app/live/continuity.py). Nothing here is exported to the results seed: live shadow data is
produced on the server.
"""
from __future__ import annotations

import json
from typing import Any

SHADOW_DDL = [
    """CREATE TABLE IF NOT EXISTS shadow_sessions(
        session_id TEXT PRIMARY KEY, created_ts INTEGER, live_ts INTEGER, ended_ts INTEGER,
        status TEXT, source_run_id TEXT, config_json TEXT, summary_json TEXT, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS shadow_bots(
        session_id TEXT NOT NULL, bot_key TEXT NOT NULL, role TEXT, pair_id TEXT, control_key TEXT,
        strategy_id TEXT, symbol TEXT, timeframe TEXT, state_json TEXT, updated_ts INTEGER,
        PRIMARY KEY(session_id, bot_key))""",
    """CREATE TABLE IF NOT EXISTS shadow_trades(
        id TEXT PRIMARY KEY, session_id TEXT, bot_key TEXT, role TEXT, pair_id TEXT, symbol TEXT,
        timeframe TEXT, side TEXT, entry_ts INTEGER, exit_ts INTEGER, qty REAL, entry_price REAL,
        exit_price REAL, gross REAL, fees REAL, net REAL, r REAL, exit_kind TEXT, decision_id TEXT,
        counterfactual INTEGER DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS shadow_trades_bot ON shadow_trades(bot_key, exit_ts)",
    """CREATE TABLE IF NOT EXISTS shadow_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts INTEGER, kind TEXT, bot_key TEXT,
        role TEXT, pair_id TEXT, symbol TEXT, timeframe TEXT, data_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS shadow_events_ts ON shadow_events(ts)",
    """CREATE TABLE IF NOT EXISTS shadow_decisions(
        id TEXT PRIMARY KEY, session_id TEXT, bot_key TEXT, pair_id TEXT, symbol TEXT,
        timeframe TEXT, side TEXT, signal_ts INTEGER, candidate_wall_ms INTEGER,
        request_start_ms INTEGER, response_ms INTEGER, decision_ms INTEGER, submit_ms INTEGER,
        latency_ms INTEGER, mid_at_candidate REAL, mid_at_response REAL,
        latency_slippage_bps REAL, notional REAL, model_requested TEXT, model_resolved TEXT,
        prompt_version TEXT, policy_version TEXT, state_fingerprint TEXT, state_json TEXT,
        take_probability REAL, setup_quality REAL, risk_state TEXT, regime TEXT,
        final_action TEXT, final_level TEXT, risk_multiplier REAL, error_code TEXT,
        timed_out INTEGER, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL,
        outcome_kind TEXT, outcome_net REAL, outcome_r REAL, outcome_exit TEXT, resolved_ts INTEGER)""",
]

DECISION_COLS = ("id", "session_id", "bot_key", "pair_id", "symbol", "timeframe", "side", "signal_ts",
                 "candidate_wall_ms", "request_start_ms", "response_ms", "decision_ms", "submit_ms",
                 "latency_ms", "mid_at_candidate", "mid_at_response", "latency_slippage_bps", "notional",
                 "model_requested", "model_resolved", "prompt_version", "policy_version",
                 "state_fingerprint", "state_json", "take_probability", "setup_quality", "risk_state",
                 "regime", "final_action", "final_level", "risk_multiplier", "error_code", "timed_out",
                 "input_tokens", "output_tokens", "cost_usd")
TRADE_COLS = ("id", "session_id", "bot_key", "role", "pair_id", "symbol", "timeframe", "side",
              "entry_ts", "exit_ts", "qty", "entry_price", "exit_price", "gross", "fees", "net", "r",
              "exit_kind", "decision_id", "counterfactual", "rederived")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)


class ShadowQueries:
    """Requires the host class to provide `self.conn`."""

    # -- sessions -------------------------------------------------------------------------------
    def shadow_session_start(self, session_id: str, created_ts: int, source_run_id: str,
                             config: dict[str, Any], status: str = "WARMING_UP",
                             experiment_id: str | None = None, experiment: dict[str, Any] | None = None) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO shadow_sessions(session_id, created_ts, status, "
                              "source_run_id, config_json, experiment_id, experiment_json) VALUES(?,?,?,?,?,?,?)",
                              (session_id, created_ts, status, source_run_id, _dumps(config), experiment_id,
                               _dumps(experiment) if experiment is not None else None))

    def shadow_session_update(self, session_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(_dumps(v) if k.endswith("_json") and not isinstance(v, (str, type(None))) else v)
        with self.conn:
            self.conn.execute(f"UPDATE shadow_sessions SET {', '.join(cols)} WHERE session_id=?",
                              (*vals, session_id))

    def shadow_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM shadow_sessions ORDER BY created_ts DESC LIMIT ?",
                                 (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["config"] = json.loads(d.pop("config_json") or "null")
            d["summary"] = json.loads(d.pop("summary_json") or "null")
            d["experiment"] = json.loads(d.pop("experiment_json", None) or "null")
            out.append(d)
        return out

    def shadow_session_last_seen(self, session_ids: list[str]) -> dict[str, int]:
        """Latest evidence each session left behind (bot snapshots or events): where a session that
        never recorded an end (a killed process) actually stopped."""
        if not session_ids:
            return {}
        marks = ",".join("?" for _ in session_ids)
        out: dict[str, int] = {}
        for q in (f"SELECT session_id, MAX(updated_ts) FROM shadow_bots WHERE session_id IN ({marks}) GROUP BY session_id",
                  f"SELECT session_id, MAX(ts) FROM shadow_events WHERE session_id IN ({marks}) GROUP BY session_id"):
            for sid, ts in self.conn.execute(q, session_ids).fetchall():
                if ts:
                    out[sid] = max(out.get(sid, 0), int(ts))
        return out

    def shadow_event_count(self, kind: str, session_ids: list[str] | None = None) -> int:
        q, args = "SELECT COUNT(*) FROM shadow_events WHERE kind=?", [kind]
        if session_ids is not None:
            if not session_ids:
                return 0
            q += f" AND session_id IN ({','.join('?' for _ in session_ids)})"
            args.extend(session_ids)
        return int(self.conn.execute(q, args).fetchone()[0])

    # -- bots -----------------------------------------------------------------------------------
    def shadow_bots_save(self, session_id: str, rows: list[dict[str, Any]], ts: int) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO shadow_bots(session_id, bot_key, role, pair_id, control_key, "
                "strategy_id, symbol, timeframe, state_json, updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(session_id, r["key"], r.get("role"), r.get("pair_id"), r.get("control_key"),
                  r.get("strategy_id"), r.get("symbol"), r.get("timeframe"), _dumps(r), ts) for r in rows])

    def shadow_bots(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT state_json FROM shadow_bots WHERE session_id=? ORDER BY bot_key",
                                 (session_id,)).fetchall()
        return [json.loads(r["state_json"] or "{}") for r in rows]

    # -- trades ---------------------------------------------------------------------------------
    def shadow_trade_save(self, session_id: str, t: dict[str, Any]) -> None:
        row = {**t, "session_id": session_id, "id": f"{session_id}:{t['bot_key']}:{t['position_id']}"
               + (":cf" if t.get("counterfactual") else ""), "counterfactual": int(bool(t.get("counterfactual"))),
               "rederived": int(bool(t.get("rederived")))}
        with self.conn:
            self.conn.execute(f"INSERT OR REPLACE INTO shadow_trades({','.join(TRADE_COLS)}) "
                              f"VALUES({','.join('?' for _ in TRADE_COLS)})", [row.get(c) for c in TRADE_COLS])
            if row.get("decision_id"):
                self.conn.execute(
                    "UPDATE shadow_decisions SET outcome_kind=?, outcome_net=?, outcome_r=?, outcome_exit=?, "
                    "resolved_ts=? WHERE id=? AND outcome_kind IS NULL",
                    ("SHADOW" if row["counterfactual"] else "TAKEN", row.get("net"), row.get("r"),
                     row.get("exit_kind"), row.get("exit_ts"), row["decision_id"]))

    def shadow_trades(self, bot_key: str | None = None, counterfactual: bool | None = False,
                      limit: int = 100_000, session_ids: list[str] | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM shadow_trades WHERE 1=1", []
        if session_ids is not None:
            if not session_ids:
                return []
            q += f" AND session_id IN ({','.join('?' for _ in session_ids)})"
            args.extend(session_ids)
        if bot_key:
            q += " AND bot_key=?"
            args.append(bot_key)
        if counterfactual is not None:
            q += " AND counterfactual=?"
            args.append(int(counterfactual))
        q += " ORDER BY exit_ts ASC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    # -- decisions ------------------------------------------------------------------------------
    def shadow_decision_save(self, d: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(f"INSERT OR REPLACE INTO shadow_decisions({','.join(DECISION_COLS)}) "
                              f"VALUES({','.join('?' for _ in DECISION_COLS)})", [d.get(c) for c in DECISION_COLS])

    def shadow_decisions(self, limit: int = 100_000, with_state: bool = False,
                         session_ids: list[str] | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM shadow_decisions", []
        if session_ids is not None:
            if not session_ids:
                return []
            q += f" WHERE session_id IN ({','.join('?' for _ in session_ids)})"
            args.extend(session_ids)
        rows = self.conn.execute(q + " ORDER BY candidate_wall_ms ASC LIMIT ?", (*args, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if not with_state:
                d.pop("state_json", None)
            out.append(d)
        return out

    # -- events (the activity log) ----------------------------------------------------------------
    def shadow_event_add(self, session_id: str, ts: int, kind: str, data: dict[str, Any]) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO shadow_events(session_id, ts, kind, bot_key, role, pair_id, symbol, timeframe, "
                "data_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (session_id, ts, kind, data.get("bot_key"), data.get("role"), data.get("pair_id"),
                 data.get("symbol"), data.get("timeframe"), _dumps(data)))
        return int(cur.lastrowid or 0)

    def shadow_events_for(self, session_ids: list[str], kinds: tuple[str, ...]) -> list[dict[str, Any]]:
        """Every event of these kinds the given sessions recorded, oldest first."""
        if not session_ids or not kinds:
            return []
        q = (f"SELECT * FROM shadow_events WHERE session_id IN ({','.join('?' for _ in session_ids)}) "
             f"AND kind IN ({','.join('?' for _ in kinds)}) ORDER BY id ASC")
        out = []
        for r in self.conn.execute(q, (*session_ids, *kinds)).fetchall():
            d = dict(r)
            d["data"] = json.loads(d.pop("data_json") or "{}")
            out.append(d)
        return out

    def shadow_events(self, limit: int = 100, before_id: int | None = None,
                      kinds: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM shadow_events WHERE 1=1", []
        if before_id:
            q += " AND id<?"
            args.append(before_id)
        if kinds:
            q += f" AND kind IN ({','.join('?' for _ in kinds)})"
            args.extend(kinds)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        out = []
        for r in self.conn.execute(q, args).fetchall():
            d = dict(r)
            d["data"] = json.loads(d.pop("data_json") or "{}")
            out.append(d)
        return out
