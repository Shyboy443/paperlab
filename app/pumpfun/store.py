"""SQLite storage for the pump.fun recorder (its own database, pumpfun.db)."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens(
  mint TEXT PRIMARY KEY, t INTEGER NOT NULL, creator TEXT, name TEXT, symbol TEXT, initial_buy REAL, sol_amount REAL,
  v_sol REAL, v_tokens REAL, mcap_sol REAL, uri TEXT, pool TEXT, sig TEXT);
CREATE INDEX IF NOT EXISTS tokens_creator ON tokens(creator, t);
CREATE INDEX IF NOT EXISTS tokens_t ON tokens(t);
CREATE TABLE IF NOT EXISTS migrations(
  mint TEXT PRIMARY KEY, t INTEGER NOT NULL, pool TEXT, sig TEXT,
  creator TEXT, created_t INTEGER, initial_buy_pct REAL, creator_launches_24h INTEGER,
  pair TEXT, dex TEXT, quote_symbol TEXT, last_price_t INTEGER, misses INTEGER DEFAULT 0, done INTEGER DEFAULT 0,
  holders INTEGER, top10_pct REAL, pool_pct REAL, top10_ex_pool_pct REAL, holders_t INTEGER, holders_error TEXT);
CREATE INDEX IF NOT EXISTS migrations_t ON migrations(t);
CREATE TABLE IF NOT EXISTS prices(
  mint TEXT NOT NULL, t INTEGER NOT NULL, price_usd REAL, price_native REAL, liq_usd REAL, liq_base REAL, liq_quote REAL,
  fdv REAL, vol_m5 REAL, buys_m5 INTEGER, sells_m5 INTEGER, PRIMARY KEY(mint, t));
CREATE TABLE IF NOT EXISTS gaps(start_t INTEGER NOT NULL, end_t INTEGER NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT);
"""

TOKEN_COLS = ("mint", "t", "creator", "name", "symbol", "initial_buy", "sol_amount", "v_sol", "v_tokens", "mcap_sol",
              "uri", "pool", "sig")
PRICE_COLS = ("mint", "t", "price_usd", "price_native", "liq_usd", "liq_base", "liq_quote", "fdv", "vol_m5", "buys_m5",
              "sells_m5")


class PumpStore:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -- writes ---------------------------------------------------------------------------------------------------------
    def add_token(self, row: dict[str, Any]) -> None:
        with self.lock, self.db:
            self.db.execute(f"INSERT OR IGNORE INTO tokens({','.join(TOKEN_COLS)}) VALUES({','.join('?' * len(TOKEN_COLS))})",
                            [row.get(c) for c in TOKEN_COLS])

    def add_migration(self, mint: str, t: int, pool: str | None, sig: str | None, day_ms: int = 86_400_000) -> bool:
        """Record a graduation once; links the creation record (creator, first buy, the creator's launches in 24 h)."""
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM migrations WHERE mint=?", (mint,)).fetchone():
                return False
            tok = self.db.execute("SELECT creator, t, initial_buy FROM tokens WHERE mint=?", (mint,)).fetchone()
            creator = tok["creator"] if tok else None
            launches = None
            if creator:
                launches = self.db.execute("SELECT COUNT(*) FROM tokens WHERE creator=? AND t>=? AND t<=?",
                                           (creator, t - day_ms, t)).fetchone()[0]
            pct = (tok["initial_buy"] / 1e9 * 100) if tok and tok["initial_buy"] is not None else None   # supply 1e9
            self.db.execute("INSERT INTO migrations(mint, t, pool, sig, creator, created_t, initial_buy_pct, creator_launches_24h)"
                            " VALUES(?,?,?,?,?,?,?,?)", (mint, t, pool, sig, creator, tok["t"] if tok else None, pct, launches))
            return True

    def add_prices(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        with self.lock, self.db:
            self.db.executemany(f"INSERT OR IGNORE INTO prices({','.join(PRICE_COLS)}) VALUES({','.join('?' * len(PRICE_COLS))})",
                                [[r.get(c) for c in PRICE_COLS] for r in rows])
        return len(rows)

    def mark_polled(self, mint: str, t: int, found: dict[str, Any] | None) -> None:
        with self.lock, self.db:
            if found:
                self.db.execute("UPDATE migrations SET last_price_t=?, misses=0, pair=COALESCE(pair, ?), dex=COALESCE(dex, ?),"
                                " quote_symbol=COALESCE(quote_symbol, ?) WHERE mint=?",
                                (t, found.get("pair"), found.get("dex"), found.get("quote_symbol"), mint))
            else:
                self.db.execute("UPDATE migrations SET last_price_t=?, misses=misses+1 WHERE mint=?", (t, mint))

    def finish(self, mint: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE migrations SET done=1 WHERE mint=?", (mint,))

    def set_holders(self, mint: str, t: int, holders: int | None, top10: float | None, pool_pct: float | None,
                    error: str | None = None) -> None:
        ex = (top10 - pool_pct) if top10 is not None and pool_pct is not None else None
        with self.lock, self.db:
            self.db.execute("UPDATE migrations SET holders=?, top10_pct=?, pool_pct=?, top10_ex_pool_pct=?, holders_t=?,"
                            " holders_error=? WHERE mint=?", (holders, top10, pool_pct, ex, t, error, mint))

    def add_gap(self, start: int, end: int, reason: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO gaps VALUES(?,?,?)", (start, end, reason[:200]))

    def set_state(self, key: str, value: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, value))

    # -- reads ----------------------------------------------------------------------------------------------------------
    def state(self, key: str) -> str | None:
        with self.lock:
            r = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def due_prices(self, now: int, schedule: list[tuple[int, int]], horizon: int) -> list[str]:
        """Graduated tokens whose next price sample is due: schedule = [(age_limit_ms, every_ms), ...] ascending."""
        with self.lock:
            rows = self.db.execute("SELECT mint, t, last_price_t FROM migrations WHERE done=0").fetchall()
        due, finished = [], []
        for r in rows:
            age = now - r["t"]
            if age > horizon:
                finished.append(r["mint"])
                continue
            every = next((e for lim, e in schedule if age <= lim), schedule[-1][1])
            if r["last_price_t"] is None or now - r["last_price_t"] >= every:
                due.append(r["mint"])
        for m in finished:
            self.finish(m)
        return due

    def due_holders(self, now: int, after_ms: int) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT mint FROM migrations WHERE holders_t IS NULL AND t<=? ORDER BY t",
                                                  (now - after_ms,)).fetchall()]

    def liquidity_base(self, mint: str) -> float | None:
        with self.lock:
            r = self.db.execute("SELECT liq_base FROM prices WHERE mint=? AND liq_base IS NOT NULL ORDER BY t LIMIT 1",
                                (mint,)).fetchone()
        return r[0] if r else None

    def counts(self, now: int) -> dict[str, Any]:
        day = now - 86_400_000
        with self.lock:
            q = lambda sql, *a: self.db.execute(sql, a).fetchone()[0]  # noqa: E731
            return {"tokens_total": q("SELECT COUNT(*) FROM tokens"), "tokens_24h": q("SELECT COUNT(*) FROM tokens WHERE t>=?", day),
                    "graduations_total": q("SELECT COUNT(*) FROM migrations"),
                    "graduations_24h": q("SELECT COUNT(*) FROM migrations WHERE t>=?", day),
                    "graduations_with_creation": q("SELECT COUNT(*) FROM migrations WHERE creator IS NOT NULL"),
                    "tracking_now": q("SELECT COUNT(*) FROM migrations WHERE done=0"),
                    "price_points": q("SELECT COUNT(*) FROM prices"),
                    "with_holders": q("SELECT COUNT(*) FROM migrations WHERE top10_pct IS NOT NULL"),
                    "gaps": q("SELECT COUNT(*) FROM gaps"),
                    "gap_minutes": round((q("SELECT COALESCE(SUM(end_t-start_t),0) FROM gaps") or 0) / 60000, 1),
                    "first_t": q("SELECT MIN(t) FROM tokens")}

    def recent(self, n: int = 10) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT m.mint, m.t, m.creator_launches_24h, m.initial_buy_pct, m.top10_ex_pool_pct, m.holders,"
                                   " tk.name, tk.symbol, (SELECT price_usd FROM prices p WHERE p.mint=m.mint ORDER BY t DESC LIMIT 1) AS last_usd,"
                                   " (SELECT liq_usd FROM prices p WHERE p.mint=m.mint ORDER BY t DESC LIMIT 1) AS last_liq"
                                   " FROM migrations m LEFT JOIN tokens tk ON tk.mint=m.mint ORDER BY m.t DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]
