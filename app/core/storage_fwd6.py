"""V6 FORWARD tables for Storage (schema 16). Mixed into `Storage` like the other program modules.

A V6 FORWARD EXPERIMENT (fwd6_experiments) is created once per frozen identity and never restarted: its
forward_start_ms (V6_FORWARD_START) and warm-up start are immutable. Every boot is a SESSION of it (fwd6_sessions)
that heartbeats while LIVE, so the minutes no process observed are known. The experiment's evidence -- candidates,
gate and Jev decisions, fills, closed trades, the latest bot snapshots -- is keyed by experiment, and the market
inputs the bots consumed (1m bars with the spread observed at their close, positioning points, hour watermarks, the
funding rate charged at each settlement) are stored so a restarted process re-derives the books from identical
inputs. Nothing here is secret: no key, no account, no order id.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

FWD6_DDL = [
    """CREATE TABLE IF NOT EXISTS fwd6_experiments(
        experiment_id TEXT PRIMARY KEY, created_ts INTEGER, forward_start_ms INTEGER, warmup_from_ms INTEGER,
        status TEXT, manifest_fingerprint TEXT, identity_json TEXT, config_json TEXT, note TEXT)""",
    """CREATE TABLE IF NOT EXISTS fwd6_sessions(
        session_id TEXT PRIMARY KEY, experiment_id TEXT, created_ts INTEGER, live_ts INTEGER, heartbeat_ts INTEGER,
        ended_ts INTEGER, status TEXT, config_json TEXT, summary_json TEXT, error TEXT)""",
    "CREATE INDEX IF NOT EXISTS fwd6_sessions_exp ON fwd6_sessions(experiment_id, created_ts)",
    """CREATE TABLE IF NOT EXISTS fwd6_bots(
        experiment_id TEXT NOT NULL, bot_key TEXT NOT NULL, role TEXT, pair_id TEXT, strategy_id TEXT, symbol TEXT,
        horizon TEXT, session_id TEXT, state_json TEXT, updated_ts INTEGER, PRIMARY KEY(experiment_id, bot_key))""",
    """CREATE TABLE IF NOT EXISTS fwd6_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT, session_id TEXT, ts INTEGER, kind TEXT,
        bot_key TEXT, role TEXT, pair_id TEXT, symbol TEXT, horizon TEXT, data_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS fwd6_events_exp ON fwd6_events(experiment_id, id)",
    """CREATE TABLE IF NOT EXISTS fwd6_trades(
        id TEXT PRIMARY KEY, experiment_id TEXT, session_id TEXT, bot_key TEXT, role TEXT, pair_id TEXT, symbol TEXT,
        horizon TEXT, strategy_id TEXT, side TEXT, entry_ts INTEGER, exit_ts INTEGER, qty REAL, entry_price REAL,
        exit_price REAL, gross REAL, fees REAL, slippage REAL, funding REAL, funding_paid REAL, funding_received REAL,
        net REAL, r REAL, exit_kind TEXT, risk_pct REAL, risk_usd REAL, tier TEXT, jev_level TEXT,
        decision_id TEXT, counterfactual INTEGER DEFAULT 0, rederived INTEGER DEFAULT 0, data_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS fwd6_trades_exp ON fwd6_trades(experiment_id, exit_ts)",
    """CREATE TABLE IF NOT EXISTS fwd6_decisions(
        id TEXT PRIMARY KEY, experiment_id TEXT, session_id TEXT, bot_key TEXT, role TEXT, pair_id TEXT, symbol TEXT,
        horizon TEXT, strategy_id TEXT, side TEXT, signal_ts INTEGER, decision_instant_ms INTEGER,
        candidate_wall_ms INTEGER, request_start_ms INTEGER, response_ms INTEGER, decision_ms INTEGER,
        latency_ms INTEGER, mid_at_candidate REAL, mid_at_response REAL, move_bps REAL, notional REAL,
        model_requested TEXT, model_resolved TEXT, prompt_version TEXT, policy_version TEXT, state_fingerprint TEXT,
        state_json TEXT, p_support REAL, p_skip REAL, p_take REAL, p_attack REAL, choice TEXT, final_action TEXT,
        final_level TEXT, risk_multiplier REAL, reason TEXT, error_code TEXT, timed_out INTEGER, tier TEXT,
        legal_min_risk_pct REAL, quality REAL, attack_only INTEGER, health TEXT, source TEXT, rederived INTEGER,
        input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL,
        outcome_kind TEXT, outcome_net REAL, outcome_r REAL, outcome_exit TEXT, resolved_ts INTEGER)""",
    "CREATE INDEX IF NOT EXISTS fwd6_decisions_exp ON fwd6_decisions(experiment_id, signal_ts)",
    """CREATE TABLE IF NOT EXISTS fwd6_bars(
        symbol TEXT NOT NULL, open_time INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL,
        turnover REAL, bid REAL, ask REAL, half_spread_bps REAL, source TEXT, PRIMARY KEY(symbol, open_time))""",
    """CREATE TABLE IF NOT EXISTS fwd6_positioning(
        symbol TEXT NOT NULL, kind TEXT NOT NULL, ts INTEGER NOT NULL, value REAL, PRIMARY KEY(symbol, kind, ts))""",
    """CREATE TABLE IF NOT EXISTS fwd6_watermarks(
        symbol TEXT NOT NULL, t INTEGER NOT NULL, caps_json TEXT, barrier_json TEXT, PRIMARY KEY(symbol, t))""",
    """CREATE TABLE IF NOT EXISTS fwd6_funding_charged(
        symbol TEXT NOT NULL, ts INTEGER NOT NULL, rate REAL, PRIMARY KEY(symbol, ts))""",
]

DECISION_COLS = ("id", "experiment_id", "session_id", "bot_key", "role", "pair_id", "symbol", "horizon", "strategy_id",
                 "side", "signal_ts", "decision_instant_ms", "candidate_wall_ms", "request_start_ms", "response_ms",
                 "decision_ms", "latency_ms", "mid_at_candidate", "mid_at_response", "move_bps", "notional",
                 "model_requested", "model_resolved", "prompt_version", "policy_version", "state_fingerprint",
                 "state_json", "p_support", "p_skip", "p_take", "p_attack", "choice", "final_action", "final_level",
                 "risk_multiplier", "reason", "error_code", "timed_out", "tier", "legal_min_risk_pct", "quality",
                 "attack_only", "health", "source", "rederived", "input_tokens", "output_tokens", "cost_usd")
TRADE_COLS = ("id", "experiment_id", "session_id", "bot_key", "role", "pair_id", "symbol", "horizon", "strategy_id",
              "side", "entry_ts", "exit_ts", "qty", "entry_price", "exit_price", "gross", "fees", "slippage", "funding",
              "funding_paid", "funding_received", "net", "r", "exit_kind", "risk_pct", "risk_usd", "tier", "jev_level",
              "decision_id", "counterfactual", "rederived", "data_json")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)


class Fwd6Queries:
    """Requires the host class to provide `self.conn`."""

    # -- experiments -------------------------------------------------------------------------------------------
    def fwd6_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM fwd6_experiments WHERE experiment_id=?", (experiment_id,)).fetchone()
        return self._fwd6_exp(dict(r)) if r else None

    def fwd6_experiments(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM fwd6_experiments ORDER BY created_ts DESC LIMIT ?", (limit,)).fetchall()
        return [self._fwd6_exp(dict(r)) for r in rows]

    @staticmethod
    def _fwd6_exp(d: dict[str, Any]) -> dict[str, Any]:
        d["identity"] = json.loads(d.pop("identity_json") or "null")
        d["config"] = json.loads(d.pop("config_json") or "null")
        return d

    def fwd6_experiment_create(self, row: dict[str, Any]) -> bool:
        """INSERT OR IGNORE: an experiment's forward start is written once and never changed."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO fwd6_experiments(experiment_id, created_ts, forward_start_ms, warmup_from_ms, "
                "status, manifest_fingerprint, identity_json, config_json, note) VALUES(?,?,?,?,?,?,?,?,?)",
                (row["experiment_id"], row["created_ts"], row["forward_start_ms"], row["warmup_from_ms"],
                 row.get("status", "ACTIVE"), row.get("manifest_fingerprint"), _dumps(row.get("identity") or {}),
                 _dumps(row.get("config") or {}), row.get("note")))
        return cur.rowcount == 1

    def fwd6_experiment_status(self, experiment_id: str, status: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE fwd6_experiments SET status=? WHERE experiment_id=?", (status, experiment_id))

    # -- sessions ----------------------------------------------------------------------------------------------
    def fwd6_session_start(self, session_id: str, experiment_id: str, created_ts: int, config: dict[str, Any],
                           status: str = "WARMING_UP") -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO fwd6_sessions(session_id, experiment_id, created_ts, status, "
                              "config_json) VALUES(?,?,?,?,?)", (session_id, experiment_id, created_ts, status,
                                                                  _dumps(config)))

    def fwd6_session_update(self, session_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(_dumps(v) if k.endswith("_json") and not isinstance(v, (str, type(None))) else v)
        with self.conn:
            self.conn.execute(f"UPDATE fwd6_sessions SET {', '.join(cols)} WHERE session_id=?", (*vals, session_id))

    def fwd6_sessions(self, experiment_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM fwd6_sessions", []
        if experiment_id:
            q += " WHERE experiment_id=?"
            args.append(experiment_id)
        rows = self.conn.execute(q + " ORDER BY created_ts ASC LIMIT ?", (*args, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["config"] = json.loads(d.pop("config_json") or "null")
            d["summary"] = json.loads(d.pop("summary_json") or "null")
            out.append(d)
        return out

    def fwd6_coverage(self, experiment_id: str) -> list[tuple[int, int]]:
        """(go-live, last heartbeat) of every session that went LIVE: the instants the experiment observed."""
        rows = self.conn.execute("SELECT live_ts, heartbeat_ts FROM fwd6_sessions WHERE experiment_id=? AND "
                                 "live_ts IS NOT NULL AND heartbeat_ts IS NOT NULL", (experiment_id,)).fetchall()
        return [(int(a), int(b)) for a, b in rows]

    # -- bots ---------------------------------------------------------------------------------------------------
    def fwd6_bots_save(self, experiment_id: str, session_id: str, rows: list[dict[str, Any]], ts: int) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO fwd6_bots(experiment_id, bot_key, role, pair_id, strategy_id, symbol, horizon, "
                "session_id, state_json, updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(experiment_id, r["key"], r.get("role"), r.get("pair_id"), r.get("strategy_id"), r.get("symbol"),
                  r.get("horizon"), session_id, _dumps(r), ts) for r in rows])

    def fwd6_bots(self, experiment_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT state_json, updated_ts, session_id FROM fwd6_bots WHERE experiment_id=? "
                                 "ORDER BY bot_key", (experiment_id,)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["state_json"] or "{}")
            d["_updated_ts"], d["_session_id"] = r["updated_ts"], r["session_id"]
            out.append(d)
        return out

    # -- events, trades, decisions ----------------------------------------------------------------------------------
    def fwd6_event_add(self, experiment_id: str, session_id: str, ts: int, kind: str, data: dict[str, Any]) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO fwd6_events(experiment_id, session_id, ts, kind, bot_key, role, pair_id, symbol, horizon, "
                "data_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (experiment_id, session_id, ts, kind, data.get("bot_key"), data.get("role"), data.get("pair_id"),
                 data.get("symbol"), data.get("horizon"), _dumps(data)))
        return int(cur.lastrowid or 0)

    def fwd6_events(self, experiment_id: str, limit: int = 100, before_id: int | None = None,
                    kinds: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM fwd6_events WHERE experiment_id=?", [experiment_id]
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

    def fwd6_events_all(self, experiment_id: str, kinds: tuple[str, ...]) -> list[dict[str, Any]]:
        q = (f"SELECT * FROM fwd6_events WHERE experiment_id=? AND kind IN ({','.join('?' for _ in kinds)}) "
             "ORDER BY id ASC")
        out = []
        for r in self.conn.execute(q, (experiment_id, *kinds)).fetchall():
            d = dict(r)
            d["data"] = json.loads(d.pop("data_json") or "{}")
            out.append(d)
        return out

    def fwd6_event_count(self, experiment_id: str, kind: str, since_ts: int = 0) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM fwd6_events WHERE experiment_id=? AND kind=? AND ts>=?",
                                     (experiment_id, kind, since_ts)).fetchone()[0])

    def fwd6_trade_save(self, experiment_id: str, session_id: str, t: dict[str, Any]) -> None:
        row = {**t, "experiment_id": experiment_id, "session_id": session_id,
               "id": f"{experiment_id}:{t['bot_key']}:{t['entry_ts']}:{t['side']}"
                     + (f":{t['symbol']}" if t.get("multi_symbol") else "")      # a multi-coin book (V11)
                     + (":cf" if t.get("counterfactual") else ""),
               "counterfactual": int(bool(t.get("counterfactual"))), "rederived": int(bool(t.get("rederived"))),
               "data_json": _dumps({k: t.get(k) for k in ("setup", "regime", "stop_pct", "target_r", "exit_reason",
                                                           "latency_ms", "spread_cost", "hold_s", "quality", "r_net",
                                                           "jev_multiplier", "position_id")})}
        with self.conn:
            self.conn.execute(f"INSERT OR REPLACE INTO fwd6_trades({','.join(TRADE_COLS)}) "
                              f"VALUES({','.join('?' for _ in TRADE_COLS)})", [row.get(c) for c in TRADE_COLS])
            if row.get("decision_id"):
                self.conn.execute(
                    "UPDATE fwd6_decisions SET outcome_kind=?, outcome_net=?, outcome_r=?, outcome_exit=?, resolved_ts=? "
                    "WHERE id=? AND outcome_kind IS NULL",
                    ("SHADOW" if row["counterfactual"] else "TAKEN", row.get("net"), row.get("r"), row.get("exit_kind"),
                     row.get("exit_ts"), row["decision_id"]))

    def fwd6_trades(self, experiment_id: str, counterfactual: bool | None = False,
                    bot_key: str | None = None, since_ts: int = 0) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM fwd6_trades WHERE experiment_id=? AND exit_ts>=?", [experiment_id, since_ts]
        if counterfactual is not None:
            q += " AND counterfactual=?"
            args.append(int(counterfactual))
        if bot_key:
            q += " AND bot_key=?"
            args.append(bot_key)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY exit_ts ASC", args).fetchall()]

    def fwd6_decision_save(self, d: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(f"INSERT OR REPLACE INTO fwd6_decisions({','.join(DECISION_COLS)}) "
                              f"VALUES({','.join('?' for _ in DECISION_COLS)})",
                              [int(d[c]) if c in ("attack_only", "rederived") and d.get(c) is not None else d.get(c)
                               for c in DECISION_COLS])

    def fwd6_decisions(self, experiment_id: str, with_state: bool = False, role: str | None = None,
                       since_ts: int = 0) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM fwd6_decisions WHERE experiment_id=? AND signal_ts>=?", [experiment_id, since_ts]
        if role:
            q += " AND role=?"
            args.append(role)
        out = []
        for r in self.conn.execute(q + " ORDER BY signal_ts ASC", args).fetchall():
            d = dict(r)
            if not with_state:
                d.pop("state_json", None)
            out.append(d)
        return out

    # -- market inputs --------------------------------------------------------------------------------------------
    def fwd6_bars_save(self, rows: Iterable[dict[str, Any]]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO fwd6_bars(symbol, open_time, open, high, low, close, volume, turnover, bid, ask, "
                "half_spread_bps, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [(r["symbol"], r["open_time"], r["open"], r["high"], r["low"], r["close"], r["volume"], r.get("turnover"),
                  r.get("bid"), r.get("ask"), r.get("half_spread_bps"), r.get("source")) for r in rows])

    def fwd6_bars(self, symbol: str, from_ms: int, to_ms: int | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM fwd6_bars WHERE symbol=? AND open_time>=?", [symbol, from_ms]
        if to_ms is not None:
            q += " AND open_time<?"
            args.append(to_ms)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY open_time ASC", args).fetchall()]

    def fwd6_bar_last(self, symbol: str) -> int | None:
        r = self.conn.execute("SELECT MAX(open_time) FROM fwd6_bars WHERE symbol=?", (symbol,)).fetchone()
        return int(r[0]) if r and r[0] is not None else None

    def fwd6_positioning_save(self, rows: Iterable[dict[str, Any]]) -> None:
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO fwd6_positioning(symbol, kind, ts, value) VALUES(?,?,?,?)",
                                  [(r["symbol"], r["kind"], r["ts"], r["value"]) for r in rows])

    def fwd6_positioning(self, symbol: str, kind: str, from_ms: int) -> list[tuple[int, float]]:
        return [(int(a), float(b)) for a, b in self.conn.execute(
            "SELECT ts, value FROM fwd6_positioning WHERE symbol=? AND kind=? AND ts>=? ORDER BY ts ASC",
            (symbol, kind, from_ms)).fetchall()]

    def fwd6_watermark_save(self, rows: Iterable[dict[str, Any]]) -> None:
        with self.conn:
            self.conn.executemany("INSERT OR IGNORE INTO fwd6_watermarks(symbol, t, caps_json, barrier_json) "
                                  "VALUES(?,?,?,?)",
                                  [(r["symbol"], r["t"], _dumps(r.get("caps") or {}), _dumps(r.get("barrier")))
                                   for r in rows])

    def fwd6_watermarks(self, from_ms: int) -> dict[str, dict[int, dict[str, int]]]:
        out: dict[str, dict[int, dict[str, int]]] = {}
        for sym, t, caps in self.conn.execute("SELECT symbol, t, caps_json FROM fwd6_watermarks WHERE t>=?",
                                              (from_ms,)).fetchall():
            out.setdefault(sym, {})[int(t)] = {k: int(v) for k, v in (json.loads(caps or "{}") or {}).items()}
        return out

    def fwd6_funding_charged_save(self, rows: Iterable[dict[str, Any]]) -> None:
        with self.conn:
            self.conn.executemany("INSERT OR IGNORE INTO fwd6_funding_charged(symbol, ts, rate) VALUES(?,?,?)",
                                  [(r["symbol"], r["ts"], r["rate"]) for r in rows])

    def fwd6_funding_charged(self, from_ms: int) -> dict[str, list[tuple[int, float]]]:
        out: dict[str, list[tuple[int, float]]] = {}
        for sym, ts, rate in self.conn.execute("SELECT symbol, ts, rate FROM fwd6_funding_charged WHERE ts>=? "
                                               "ORDER BY ts ASC", (from_ms,)).fetchall():
            out.setdefault(sym, []).append((int(ts), float(rate)))
        return out
