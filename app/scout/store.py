"""scout.db: what the scout read (items), what it measured per symbol and 5-minute bucket (features), its runs and
the Jev headline tones. Features are bucketed by FETCH time -- what a live bot would have known at that moment -- and
an item first seen more than 6 hours after it was written (a first-run backfill) is stored but not counted.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Iterable, Sequence

BUCKET_MS = 300_000
STALE_ON_ARRIVAL_MS = 6 * 3_600_000

DDL = (
    """CREATE TABLE IF NOT EXISTS scout_items (id TEXT PRIMARY KEY, source TEXT, channel TEXT, kind TEXT,
       created_ms INTEGER, fetched_ms INTEGER, symbols TEXT, text TEXT, url TEXT, score INTEGER, comments INTEGER,
       tone REAL)""",
    "CREATE INDEX IF NOT EXISTS scout_items_fetched ON scout_items(fetched_ms)",
    "CREATE INDEX IF NOT EXISTS scout_items_created ON scout_items(created_ms)",
    """CREATE TABLE IF NOT EXISTS scout_tone (item_id TEXT, symbol TEXT, tone REAL, choice TEXT, p_bull REAL,
       p_bear REAL, scored_ms INTEGER, PRIMARY KEY (item_id, symbol))""",
    """CREATE TABLE IF NOT EXISTS scout_features (bucket_ms INTEGER, symbol TEXT, posts INTEGER, comments INTEGER,
       news INTEGER, tone_reddit REAL, tone_news REAL, PRIMARY KEY (bucket_ms, symbol))""",
    """CREATE TABLE IF NOT EXISTS scout_runs (ts INTEGER, source TEXT, ok INTEGER, fetched INTEGER, new INTEGER,
       detail TEXT, cost REAL)""",
    """CREATE TABLE IF NOT EXISTS scout_trending (ts INTEGER, rank INTEGER, symbol TEXT, name TEXT, cg_id TEXT,
       mcap_rank INTEGER, PRIMARY KEY (ts, rank))""",
    "CREATE INDEX IF NOT EXISTS scout_trending_sym ON scout_trending(symbol, ts)",
    "CREATE INDEX IF NOT EXISTS scout_runs_ts ON scout_runs(ts)",
)


class ScoutStore:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self.conn:
            self.conn.execute("PRAGMA journal_mode=WAL")
            for d in DDL:
                self.conn.execute(d)

    def close(self) -> None:
        self.conn.close()

    # -- items --------------------------------------------------------------------------------------------------
    def known(self, ids: Iterable[str]) -> set[str]:
        ids = list(ids)
        out: set[str] = set()
        with self._lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = f"SELECT id FROM scout_items WHERE id IN ({','.join('?' for _ in chunk)})"
                out |= {r[0] for r in self.conn.execute(q, chunk)}
        return out

    def add_items(self, items: Sequence[dict[str, Any]], fetched_ms: int) -> list[dict[str, Any]]:
        """Store the items not seen before; returns those new ones."""
        seen = self.known(i["id"] for i in items)
        new = [i for i in items if i["id"] not in seen]
        with self._lock, self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO scout_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(i["id"], i["source"], i["channel"], i["kind"], int(i["created_ms"]), fetched_ms, ",".join(i.get("symbols") or []),
                  i["text"], i.get("url") or "", int(i.get("score") or 0), int(i.get("comments") or 0), i.get("tone"))
                 for i in new])
        return new

    def items(self, symbol: str | None = None, limit: int = 50, since_ms: int = 0) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM scout_items WHERE created_ms >= ?", [since_ms]
        if symbol:
            q += " AND (',' || symbols || ',') LIKE ?"
            args.append(f"%,{symbol},%")
        q += " ORDER BY created_ms DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            return [dict(r) for r in self.conn.execute(q, args)]

    def last_created(self, source: str) -> int | None:
        with self._lock:
            r = self.conn.execute("SELECT MAX(created_ms) FROM scout_items WHERE source=?", (source,)).fetchone()
        return int(r[0]) if r and r[0] else None

    # -- tone ---------------------------------------------------------------------------------------------------
    def save_tones(self, rows: Sequence[tuple[str, str, dict[str, Any]]], ts: int) -> None:
        with self._lock, self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO scout_tone VALUES (?,?,?,?,?,?,?)",
                                  [(i, s, t["tone"], t["choice"], t["p_bull"], t["p_bear"], ts) for i, s, t in rows])

    def tones(self, item_ids: Sequence[str]) -> dict[tuple[str, str], float]:
        out: dict[tuple[str, str], float] = {}
        ids = list(item_ids)
        with self._lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = f"SELECT item_id, symbol, tone FROM scout_tone WHERE item_id IN ({','.join('?' for _ in chunk)})"
                out.update({(r[0], r[1]): r[2] for r in self.conn.execute(q, chunk)})
        return out

    # -- features -----------------------------------------------------------------------------------------------
    def add_features(self, bucket_ms: int, per_symbol: dict[str, dict[str, Any]]) -> None:
        """Add this cycle's counts into the bucket (a bucket can receive several cycles' items)."""
        with self._lock, self.conn:
            for sym, f in per_symbol.items():
                old = self.conn.execute("SELECT * FROM scout_features WHERE bucket_ms=? AND symbol=?", (bucket_ms, sym)).fetchone()
                o = dict(old) if old else {"posts": 0, "comments": 0, "news": 0, "tone_reddit": None, "tone_news": None}

                def mean(a: Any, na: int, b: Any, nb: int) -> Any:
                    if b is None or not nb:
                        return a
                    if a is None or not na:
                        return b
                    return round((a * na + b * nb) / (na + nb), 4)
                self.conn.execute("INSERT OR REPLACE INTO scout_features VALUES (?,?,?,?,?,?,?)", (
                    bucket_ms, sym, o["posts"] + f["posts"], o["comments"] + f["comments"], o["news"] + f["news"],
                    mean(o["tone_reddit"], o["posts"] + o["comments"], f.get("tone_reddit"), f.get("n_tone_reddit") or 0),
                    mean(o["tone_news"], o["news"], f.get("tone_news"), f.get("n_tone_news") or 0)))

    def features(self, since_ms: int, symbol: str | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM scout_features WHERE bucket_ms >= ?", [since_ms]
        if symbol:
            q += " AND symbol=?"
            args.append(symbol)
        with self._lock:
            return [dict(r) for r in self.conn.execute(q + " ORDER BY bucket_ms", args)]

    # -- CoinGecko trending snapshots ---------------------------------------------------------------------------
    def add_trending(self, ts: int, rows: Sequence[dict[str, Any]]) -> None:
        with self._lock, self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO scout_trending VALUES (?,?,?,?,?,?)",
                                  [(ts, r["rank"], r["symbol"], r["name"], r["cg_id"], r.get("mcap_rank")) for r in rows])

    def trending(self, since_ms: int) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM scout_trending WHERE ts >= ? ORDER BY ts, rank", (since_ms,))]

    # -- runs ---------------------------------------------------------------------------------------------------
    def add_run(self, ts: int, source: str, ok: bool, fetched: int, new: int, detail: str = "", cost: float = 0.0) -> None:
        with self._lock, self.conn:
            self.conn.execute("INSERT INTO scout_runs VALUES (?,?,?,?,?,?,?)", (ts, source, int(ok), fetched, new, detail[:200], cost))

    def runs(self, since_ms: int) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM scout_runs WHERE ts >= ? ORDER BY ts", (since_ms,))]

    def prune(self, before_ms: int) -> int:
        """Drop item TEXT older than the cutoff (features, tones and runs stay: they are the research record)."""
        with self._lock, self.conn:
            return self.conn.execute("DELETE FROM scout_items WHERE fetched_ms < ?", (before_ms,)).rowcount


def dumps(x: Any) -> str:
    return json.dumps(x, separators=(",", ":"))
