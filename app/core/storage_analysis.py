"""Analysis-side queries for Storage: the lab journal, the daily rollup table and per-strategy reads.

Split out of storage.py to keep each module under the project's 500-line budget; mixed into `Storage`,
so every method is still reached as `storage.<name>(...)`.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.types import Fill


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


class AnalysisQueries:
    """Requires the host class to provide `self.conn` and `_row_to_fill`."""

    # -- analysis notes ----------------------------------------------------------------------
    def insert_note(self, epoch: int, kind: str, text: str, strategy_id: str | None = None,
                    symbol: str | None = None, author: str = "system", ts: int | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO analysis_notes(ts,epoch,strategy_id,symbol,author,kind,text) VALUES(?,?,?,?,?,?,?)",
            (ts if ts is not None else _now_ms(), epoch, strategy_id, symbol, author, kind, text[:500]))
        note_id = int(cur.lastrowid or 0)
        emit = getattr(self, "_emit", None)
        if emit is not None:
            emit("note", {"id": note_id, "ts": ts if ts is not None else _now_ms(), "kind": kind,
                          "strategy_id": strategy_id, "symbol": symbol, "author": author, "text": text[:500]})
        return note_id

    def notes(self, epoch: int | None = None, strategy_id: str | None = None, limit: int = 20,
              author: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM analysis_notes WHERE 1=1"
        args: list[Any] = []
        if epoch is not None:
            q += " AND epoch=?"
            args.append(epoch)
        if strategy_id:
            q += " AND strategy_id=?"
            args.append(strategy_id)
        if author:
            q += " AND author=?"
            args.append(author)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    # -- daily performance ---------------------------------------------------------------------
    def upsert_strategy_daily(self, row: dict[str, Any]) -> None:
        cols = ["day_utc", "epoch", "strategy_id", "start_equity", "end_equity", "realized", "fees", "funding",
                "upnl_eod", "trades", "wins", "losses", "win_rate", "profit_factor", "avg_r", "max_dd_pct",
                "expectancy", "signals_total", "signals_approved", "signals_rejected", "signals_warmup",
                "signals_stale", "time_in_market_s", "gross_notional_max", "reject_reasons_json", "life", "updated_ts"]
        values = [row.get(c) for c in cols[:-1]] + [_now_ms()]
        self.conn.execute(
            f"INSERT OR REPLACE INTO strategy_daily({','.join(cols)}) VALUES({','.join('?' * len(cols))})", values)

    def strategy_daily(self, epoch: int | None = None, strategy_id: str | None = None,
                       day_from: str | None = None, day_to: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM strategy_daily WHERE 1=1"
        args: list[Any] = []
        for clause, val in (("epoch=?", epoch), ("strategy_id=?", strategy_id),
                            ("day_utc>=?", day_from), ("day_utc<=?", day_to)):
            if val is not None:
                q += f" AND {clause}"
                args.append(val)
        q += " ORDER BY day_utc DESC, strategy_id ASC"
        out = []
        for r in self.conn.execute(q, args).fetchall():
            d = dict(r)
            d["reject_reasons"] = json.loads(d.pop("reject_reasons_json") or "{}")
            out.append(d)
        return out

    def fills_for(self, epoch: int, strategy_id: str | None = None, since_ts: int | None = None,
                  until_ts: int | None = None, life: int | None = None) -> list[Fill]:
        q = "SELECT * FROM fills WHERE epoch=?"
        args: list[Any] = [epoch]
        if strategy_id:
            q += " AND strategy_id=?"
            args.append(strategy_id)
        if life is not None:
            q += " AND life=?"
            args.append(life)
        if since_ts is not None:
            q += " AND ts>=?"
            args.append(since_ts)
        if until_ts is not None:
            q += " AND ts<?"
            args.append(until_ts)
        q += " ORDER BY ts ASC, rowid ASC"
        return [self._row_to_fill(r) for r in self.conn.execute(q, args).fetchall()]

    def exit_kind_counts(self, epoch: int, since_ts: int = 0) -> dict[str, dict[str, float]]:
        """How closed-side fills ended this epoch: {kind: {n, pnl}}. The Overview reads this so the exit
        mix does not have to be scraped out of /api/tape."""
        rows = self.conn.execute(
            "SELECT kind, COUNT(*) AS n, SUM(realized_pnl) AS pnl FROM fills "
            "WHERE epoch=? AND ts>=? AND is_open=0 AND kind<>'funding' GROUP BY kind ORDER BY n DESC",
            (epoch, since_ts)).fetchall()
        return {r["kind"]: {"n": int(r["n"]), "pnl": float(r["pnl"] or 0.0)} for r in rows}

    def signal_status_counts(self, epoch: int, strategy_id: str, since_ts: int, until_ts: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM signals WHERE epoch=? AND strategy_id=? AND ts>=? AND ts<? "
            "GROUP BY status", (epoch, strategy_id, since_ts, until_ts)).fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    def reject_reason_counts(self, epoch: int, strategy_id: str | None = None, since_ts: int = 0,
                             until_ts: int | None = None) -> dict[str, int]:
        q = ("SELECT reason, COUNT(*) AS n FROM signals WHERE epoch=? AND status='rejected' AND ts>=?")
        args: list[Any] = [epoch, since_ts]
        if strategy_id:
            q += " AND strategy_id=?"
            args.append(strategy_id)
        if until_ts is not None:
            q += " AND ts<?"
            args.append(until_ts)
        q += " GROUP BY reason ORDER BY n DESC"
        # collapse to the SHORT reject code so the GUI, the row pill and this breakdown share one vocabulary
        from app.core.risk import reject_code
        out: dict[str, int] = {}
        for r in self.conn.execute(q, args).fetchall():
            key = reject_code(str(r["reason"] or "unknown"))
            out[key] = out.get(key, 0) + int(r["n"])
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

