"""Live mirror tables (schema 17): an operator-armed mirror of one qualified paper bot on a real exchange account.

    live_mirrors          one row per mirror: bot, exchange, network, the operator's amount and limits, status,
                          the open live position (if any) and realized PnL
    live_mirror_log       every live action and error (orders, stop placement, closes, limit stops, reconciliation)
    live_provider_checks  testnet round trips per exchange (mainnet stays locked until one passed)

No key, secret or signature is ever written here.
"""
from __future__ import annotations

import json
from typing import Any

MIRROR_DDL = [
    """CREATE TABLE IF NOT EXISTS live_mirrors(
        id TEXT PRIMARY KEY, program TEXT, bot_key TEXT, symbol TEXT, exchange TEXT, network TEXT, amount_usdt REAL,
        risk_pct REAL, max_daily_loss REAL, max_total_loss REAL, status TEXT, created_ts INTEGER, stopped_ts INTEGER,
        reason TEXT, position_json TEXT, realized REAL DEFAULT 0, daily_json TEXT, trades INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS live_mirror_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, mirror_id TEXT, ts INTEGER, kind TEXT, detail_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS live_mirror_log_m ON live_mirror_log(mirror_id, id)",
    """CREATE TABLE IF NOT EXISTS live_provider_checks(
        id INTEGER PRIMARY KEY AUTOINCREMENT, exchange TEXT, network TEXT, ts INTEGER, ok INTEGER, detail_json TEXT)""",
]

_COLS = ("id", "program", "bot_key", "symbol", "exchange", "network", "amount_usdt", "risk_pct", "max_daily_loss",
         "max_total_loss", "status", "created_ts", "stopped_ts", "reason", "position_json", "realized", "daily_json",
         "trades")


class MirrorQueries:
    conn: Any

    def mirror_save(self, m: dict[str, Any]) -> None:
        row = {**m, "position_json": json.dumps(m.get("position")) if m.get("position") is not None else None,
               "daily_json": json.dumps(m.get("daily") or {})}
        with self.conn:
            self.conn.execute(f"INSERT OR REPLACE INTO live_mirrors({','.join(_COLS)}) VALUES({','.join('?' for _ in _COLS)})",
                              [row.get(c) for c in _COLS])

    def mirrors(self, status: str | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM live_mirrors", []
        if status:
            q += " WHERE status=?"
            args.append(status)
        out = []
        for r in self.conn.execute(q + " ORDER BY created_ts DESC", args).fetchall():
            d = dict(r)
            d["position"] = json.loads(d.pop("position_json") or "null")
            d["daily"] = json.loads(d.pop("daily_json") or "{}")
            out.append(d)
        return out

    def mirror_log(self, mirror_id: str, ts: int, kind: str, detail: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO live_mirror_log(mirror_id, ts, kind, detail_json) VALUES(?,?,?,?)",
                              (mirror_id, ts, kind, json.dumps(detail, default=str)))

    def mirror_logs(self, mirror_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM live_mirror_log", []
        if mirror_id:
            q += " WHERE mirror_id=?"
            args.append(mirror_id)
        rows = self.conn.execute(q + " ORDER BY id DESC LIMIT ?", (*args, int(limit))).fetchall()
        return [{**{k: r[k] for k in ("id", "mirror_id", "ts", "kind")}, "detail": json.loads(r["detail_json"] or "{}")}
                for r in rows]

    def provider_check_save(self, exchange: str, network: str, ts: int, ok: bool, detail: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO live_provider_checks(exchange, network, ts, ok, detail_json) VALUES(?,?,?,?,?)",
                              (exchange, network, ts, int(bool(ok)), json.dumps(detail, default=str)))

    def provider_checks(self, exchange: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM live_provider_checks", []
        if exchange:
            q += " WHERE exchange=?"
            args.append(exchange)
        rows = self.conn.execute(q + " ORDER BY id DESC LIMIT ?", (*args, int(limit))).fetchall()
        return [{"exchange": r["exchange"], "network": r["network"], "ts": r["ts"], "ok": bool(r["ok"]),
                 "detail": json.loads(r["detail_json"] or "{}")} for r in rows]
