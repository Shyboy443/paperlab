"""Trading Desk paper ledger (SQLite). Paper money only: nothing here talks to an exchange account.

Rules:
- the book starts at `start_balance` (default 1,000 USD); equity = start + realized P&L (+ unrealized for reports);
- open() validates the sides (long: stop < entry < target; short: target < entry < stop), allows one trade per coin and
  at most `max_open` (3) open or pending trades, and sizes the trade so a stop-out loses at most `risk_pct` of equity
  INCLUDING the fee on both sides (fee_pct per side, default 0.1%); no leverage: each trade's notional is capped at
  equity / max_open so all of them fit in the book at once;
- a "market" entry fills at the price passed in (the live price when the Chief decides); a limit entry waits until a
  1m candle touches it and expires after `limit_ttl_h`;
- update() replays the 1m candles since each trade's last check: a candle that reaches both the stop and the target is
  booked as a STOP (the conservative assumption); a candle that opens beyond the stop fills at its open (a gap).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

DEFAULTS = {"start_balance": 1000.0, "risk_pct": 2.0, "fee_pct": 0.1, "max_open": 3, "limit_ttl_h": 24,
            "max_total_risk_pct": 6.0, "max_same_side": 3}

SCHEMA = """
CREATE TABLE IF NOT EXISTS desk_kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS desk_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, channel TEXT, author TEXT,
    kind TEXT, text TEXT, data TEXT, session_id TEXT);
CREATE INDEX IF NOT EXISTS desk_messages_channel ON desk_messages(channel, id);
CREATE TABLE IF NOT EXISTS desk_sessions (id TEXT PRIMARY KEY, started_ts INTEGER, ended_ts INTEGER, mode TEXT,
    status TEXT, coin TEXT, verdict TEXT, decision TEXT, trade_id INTEGER, log_md TEXT, cost_usd REAL);
CREATE TABLE IF NOT EXISTS desk_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT, side TEXT, status TEXT,
    order_type TEXT, entry_req REAL, entry REAL, stop REAL, target REAL, qty REAL, risk_usd REAL, fees REAL,
    created_ts INTEGER, opened_ts INTEGER, closed_ts INTEGER, exit REAL, exit_reason TEXT, pnl REAL, r REAL,
    session_id TEXT, reason TEXT, chart TEXT, checked_ts INTEGER);
CREATE TABLE IF NOT EXISTS desk_equity (ts INTEGER PRIMARY KEY, equity REAL);
"""


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class Sizing:
    qty: float
    risk_usd: float
    notional: float
    capped: bool


def size_position(equity: float, entry: float, stop: float, risk_pct: float, fee_pct: float, max_open: int) -> Sizing:
    """Units to buy/sell so a stop-out (price move + both fees) costs at most risk_pct of equity, and the notional
    (plus its entry fee) fits in equity / max_open."""
    fee = fee_pct / 100.0
    risk_usd = equity * risk_pct / 100.0
    per_unit = abs(entry - stop) + fee * entry + fee * stop
    if per_unit <= 0 or entry <= 0:
        raise LedgerError("entry and stop must differ and be positive")
    q_risk = risk_usd / per_unit
    q_cap = (equity / max_open) / (entry * (1 + fee))
    qty = min(q_risk, q_cap)
    return Sizing(qty=qty, risk_usd=qty * per_unit, notional=qty * entry, capped=q_cap < q_risk)


def check_sides(side: str, entry: float, stop: float, target: float) -> None:
    if side not in ("long", "short"):
        raise LedgerError("side must be long or short")
    if min(entry, stop, target) <= 0:
        raise LedgerError("prices must be positive")
    if side == "long" and not (stop < entry < target):
        raise LedgerError("a long needs stop < entry < target")
    if side == "short" and not (target < entry < stop):
        raise LedgerError("a short needs target < entry < stop")


def walk(side: str, entry: float, stop: float, target: float, candles: Iterable[dict]) -> tuple[int, float, str] | None:
    """First exit over 1m candles: (candle time, exit price, 'stop' | 'target'), or None. Stop wins a tie."""
    for c in candles:
        if side == "long":
            if c["o"] <= stop:
                return c["t"], c["o"], "stop"
            if c["l"] <= stop:
                return c["t"], stop, "stop"
            if c["h"] >= target:
                return c["t"], target, "target"
        else:
            if c["o"] >= stop:
                return c["t"], c["o"], "stop"
            if c["h"] >= stop:
                return c["t"], stop, "stop"
            if c["l"] <= target:
                return c["t"], target, "target"
    return None


class Ledger:
    def __init__(self, path: str, clock: Callable[[], float] = time.time):
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    def close(self) -> None:
        with self._lock:
            self.db.close()

    # -- settings / kv --------------------------------------------------------------------------------------------------
    def kv(self, k: str, default: Any = None) -> Any:
        with self._lock:
            row = self.db.execute("SELECT v FROM desk_kv WHERE k=?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default

    def set_kv(self, k: str, v: Any) -> None:
        with self._lock:
            self.db.execute("INSERT INTO desk_kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                            (k, json.dumps(v)))
            self.db.commit()

    def rule(self, name: str) -> Any:
        return (self.kv("settings", {}) or {}).get(name, DEFAULTS[name])

    # -- money ----------------------------------------------------------------------------------------------------------
    def start_balance(self) -> float:
        v = self.kv("start_balance")
        if v is None:
            v = float(self.rule("start_balance"))
            self.set_kv("start_balance", v)
        return float(v)

    def realized(self) -> float:
        with self._lock:
            row = self.db.execute("SELECT COALESCE(SUM(pnl), 0) AS s FROM desk_trades WHERE status='closed'").fetchone()
        return float(row["s"])

    def equity(self) -> float:
        return self.start_balance() + self.realized()

    # -- trades ---------------------------------------------------------------------------------------------------------
    def trades(self, status: Sequence[str] | None = None, limit: int = 200) -> list[dict[str, Any]]:
        q = "SELECT * FROM desk_trades"
        args: tuple = ()
        if status:
            q += f" WHERE status IN ({','.join('?' * len(status))})"
            args = tuple(status)
        q += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self.db.execute(q, args + (limit,)).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["chart"] = json.loads(d["chart"]) if d.get("chart") else None
        return d

    def trade(self, trade_id: int) -> dict[str, Any] | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM desk_trades WHERE id=?", (trade_id,)).fetchone()
        return self._row(r) if r else None

    def open(self, coin: str, side: str, entry: float | str, stop: float, target: float, reason: str = "",
             session_id: str | None = None, price: float | None = None, chart: Any = None) -> dict[str, Any]:
        """Open (market) or place (limit) a paper trade. `entry` is a price or "market"; a market entry needs `price`."""
        coin = coin.upper()
        market = isinstance(entry, str)
        if market and str(entry).lower() != "market":
            raise LedgerError("entry must be a price or 'market'")
        px = float(price) if market else float(entry)
        if market and not price:
            raise LedgerError("a market entry needs the current price")
        check_sides(side, px, float(stop), float(target))
        with self._lock:
            active = self.trades(("open", "pending"))
            if any(t["coin"] == coin for t in active):
                raise LedgerError(f"there is already a {coin} trade")
            if len(active) >= int(self.rule("max_open")):
                raise LedgerError(f"already {len(active)} open trades (max {self.rule('max_open')})")
            eq = self.equity()
            sz = size_position(eq, px, float(stop), float(self.rule("risk_pct")), float(self.rule("fee_pct")),
                               int(self.rule("max_open")))
            if sz.qty <= 0:
                raise LedgerError("position size is zero")
            open_risk = sum(float(t["risk_usd"] or 0) for t in active)
            cap = eq * float(self.rule("max_total_risk_pct")) / 100.0
            if open_risk + sz.risk_usd > cap + 1e-9:
                raise LedgerError(f"total open risk would be {open_risk + sz.risk_usd:.2f} USD, over the "
                                  f"{self.rule('max_total_risk_pct')}% cap ({cap:.2f} USD)")
            same = sum(1 for t in active if t["side"] == side)
            if same >= int(self.rule("max_same_side")):
                raise LedgerError(f"already {same} {side} trades open (max {self.rule('max_same_side')} on one side)")
            used = sum(float(t["qty"]) * float(t["entry"] or t["entry_req"]) for t in active)
            if used + sz.notional > eq * 1.0005:
                raise LedgerError("not enough free equity for this position without leverage")
            now = self.now_ms()
            fee_in = sz.qty * px * float(self.rule("fee_pct")) / 100.0 if market else 0.0
            cur = self.db.execute(
                "INSERT INTO desk_trades(coin, side, status, order_type, entry_req, entry, stop, target, qty, risk_usd,"
                " fees, created_ts, opened_ts, session_id, reason, chart, checked_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (coin, side, "open" if market else "pending", "market" if market else "limit", px, px if market else None,
                 float(stop), float(target), sz.qty, sz.risk_usd, fee_in, now, now if market else None, session_id,
                 reason[:2000], json.dumps(chart) if chart is not None else None, now))
            self.db.commit()
            return self.trade(int(cur.lastrowid))

    def cancel(self, trade_id: int, why: str) -> None:
        with self._lock:
            self.db.execute("UPDATE desk_trades SET status='cancelled', closed_ts=?, exit_reason=? WHERE id=? AND status='pending'",
                            (self.now_ms(), why[:200], trade_id))
            self.db.commit()

    def close_now(self, trade_id: int, price: float, why: str = "manual") -> dict[str, Any]:
        """Close an open trade at `price` now (a market exit), or cancel it if it is still a pending limit."""
        t = self.trade(int(trade_id))
        if t is None:
            raise LedgerError(f"no trade #{trade_id}")
        if t["status"] == "pending":
            self.cancel(t["id"], why)
            return self.trade(t["id"])
        if t["status"] != "open":
            raise LedgerError(f"trade #{trade_id} is {t['status']}")
        if not price or price <= 0:
            raise LedgerError("a close needs the current price")
        out = self._close(t, self.now_ms(), float(price), why)
        self.snapshot()
        return out

    def _close(self, t: dict[str, Any], ts: int, exit_px: float, why: str) -> dict[str, Any]:
        fee = float(self.rule("fee_pct")) / 100.0
        sign = 1 if t["side"] == "long" else -1
        fees = (t["fees"] or 0.0) + t["qty"] * exit_px * fee
        pnl = sign * (exit_px - t["entry"]) * t["qty"] - fees
        r = pnl / t["risk_usd"] if t["risk_usd"] else None
        with self._lock:
            self.db.execute("UPDATE desk_trades SET status='closed', closed_ts=?, exit=?, exit_reason=?, fees=?, pnl=?, r=?,"
                            " checked_ts=? WHERE id=?", (ts, exit_px, why, fees, pnl, r, ts, t["id"]))
            self.db.commit()
        return self.trade(t["id"])

    def update(self, minutes: Callable[[str, int, int], Sequence[dict]]) -> list[dict[str, Any]]:
        """Replay 1m candles for every pending/open trade since its last check. Returns the trades that changed
        (filled, closed or expired). `minutes(coin, start_ms, end_ms)` gives closed 1m candles in that range."""
        changed = []
        now = self.now_ms()
        fee = float(self.rule("fee_pct")) / 100.0
        for t in sorted(self.trades(("open", "pending")), key=lambda x: x["id"]):
            start = int(t["checked_ts"] or t["created_ts"]) // 60_000 * 60_000
            rows = [c for c in minutes(t["coin"], start, now) if c["t"] + 60_000 <= now]
            if t["status"] == "pending":
                fill = None
                for i, c in enumerate(rows):
                    if (t["side"] == "long" and c["l"] <= t["entry_req"]) or (t["side"] == "short" and c["h"] >= t["entry_req"]):
                        fill = i
                        break
                deadline = t["created_ts"] + int(self.rule("limit_ttl_h")) * 3_600_000
                if fill is None:
                    if now >= deadline:
                        self.cancel(t["id"], "limit not reached in time")
                        changed.append({**self.trade(t["id"]), "event": "expired"})
                    else:
                        self._checked(t["id"], now)
                    continue
                c = rows[fill]
                px = min(t["entry_req"], c["o"]) if t["side"] == "long" else max(t["entry_req"], c["o"])
                with self._lock:
                    self.db.execute("UPDATE desk_trades SET status='open', entry=?, opened_ts=?, fees=? WHERE id=?",
                                    (px, c["t"], t["qty"] * px * fee, t["id"]))
                    self.db.commit()
                t = self.trade(t["id"])
                changed.append({**t, "event": "filled"})
                # the fill candle can still stop the trade out (stop only: we cannot know the order inside it)
                same = rows[fill]
                hit_stop = same["l"] <= t["stop"] if t["side"] == "long" else same["h"] >= t["stop"]
                if hit_stop:
                    changed.append({**self._close(t, same["t"] + 60_000, t["stop"], "stop"), "event": "closed"})
                    continue
                rows = rows[fill + 1:]
            hit = walk(t["side"], t["entry"], t["stop"], t["target"], rows)
            if hit:
                ts, px, why = hit
                changed.append({**self._close(t, ts + 60_000, px, why), "event": "closed"})
            else:
                self._checked(t["id"], now)
        if changed:
            self.snapshot()
        return changed

    def _checked(self, trade_id: int, ts: int) -> None:
        with self._lock:
            self.db.execute("UPDATE desk_trades SET checked_ts=? WHERE id=?", (ts, trade_id))
            self.db.commit()

    def snapshot(self, marks: dict[str, float] | None = None) -> float:
        eq = self.report(marks)["equity"]
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO desk_equity(ts, equity) VALUES(?, ?)", (self.now_ms(), eq))
            self.db.commit()
        return eq

    # -- report ---------------------------------------------------------------------------------------------------------
    def report(self, marks: dict[str, float] | None = None) -> dict[str, Any]:
        marks = marks or {}
        fee = float(self.rule("fee_pct")) / 100.0
        start = self.start_balance()
        realized = self.realized()
        opens = []
        unreal = 0.0
        for t in self.trades(("open", "pending")):
            row = dict(t)
            m = marks.get(t["coin"])
            if t["status"] == "open" and m:
                sign = 1 if t["side"] == "long" else -1
                row["mark"] = m
                row["upnl"] = sign * (m - t["entry"]) * t["qty"] - (t["fees"] or 0) - t["qty"] * m * fee
                unreal += row["upnl"]
            opens.append(row)
        closed = self.trades(("closed",), limit=500)
        wins = [t for t in closed if (t["pnl"] or 0) > 0]
        rs = [t["r"] for t in closed if t["r"] is not None]
        with self._lock:
            curve = [dict(r) for r in self.db.execute("SELECT ts, equity FROM desk_equity ORDER BY ts").fetchall()]
        return {"start_balance": start, "realized": realized, "unrealized": unreal, "equity": start + realized + unreal,
                "return_pct": 100 * (realized + unreal) / start if start else None, "open": opens, "closed": closed[:100],
                "closed_count": len(closed), "win_rate": len(wins) / len(closed) if closed else None,
                "avg_r": sum(rs) / len(rs) if rs else None, "equity_curve": curve,
                "rules": {k: self.rule(k) for k in ("risk_pct", "fee_pct", "max_open", "limit_ttl_h")}}

    # -- chat + sessions -----------------------------------------------------------------------------------------------
    def add_message(self, channel: str, author: str, kind: str, text: str, data: Any = None,
                    session_id: str | None = None) -> dict[str, Any]:
        ts = self.now_ms()
        with self._lock:
            cur = self.db.execute("INSERT INTO desk_messages(ts, channel, author, kind, text, data, session_id)"
                                  " VALUES(?,?,?,?,?,?,?)", (ts, channel, author, kind, text,
                                                             json.dumps(data) if data is not None else None, session_id))
            self.db.commit()
            mid = int(cur.lastrowid)
        return {"id": mid, "ts": ts, "channel": channel, "author": author, "kind": kind, "text": text, "data": data,
                "session_id": session_id}

    def messages(self, channel: str = "group", limit: int = 200, before_id: int | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM desk_messages WHERE channel=?"
        args: tuple = (channel,)
        if before_id:
            q += " AND id<?"
            args += (before_id,)
        with self._lock:
            rows = self.db.execute(q + " ORDER BY id DESC LIMIT ?", args + (limit,)).fetchall()
        out = []
        for r in reversed(rows):
            d = dict(r)
            d["data"] = json.loads(d["data"]) if d["data"] else None
            out.append(d)
        return out

    def save_session(self, row: dict[str, Any]) -> None:
        cols = ("id", "started_ts", "ended_ts", "mode", "status", "coin", "verdict", "decision", "trade_id", "log_md",
                "cost_usd")
        with self._lock:
            self.db.execute(f"INSERT OR REPLACE INTO desk_sessions({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                            tuple(row.get(c) for c in cols))
            self.db.commit()

    def sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT id, started_ts, ended_ts, mode, status, coin, verdict, decision, trade_id,"
                                   " cost_usd FROM desk_sessions ORDER BY started_ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def session(self, sid: str) -> dict[str, Any] | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM desk_sessions WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None
